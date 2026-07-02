import pytest

from taroai.audit import AuditEventCreate
from taroai.agent import AgentRuntime
from taroai.domain import AuditEvent, RunCreate, new_id, utc_now
from taroai.guardrails import (
    GuardrailAction,
    GuardrailCondition,
    GuardrailRule,
    GuardrailSeverity,
    GuardrailStage,
    InMemoryGuardrailService,
)
from taroai.model_gateway import PlannedToolCall
from taroai.secrets import InMemorySecretService, SecretScope
from taroai.store import InMemoryControlPlaneStore
from taroai.tool_gateway import (
    ToolApprovalRequiredError,
    ToolExecutionError,
    ToolGateway,
    ToolGatewayRequest,
    ToolPolicy,
    ToolResult,
    ToolRiskLevel,
    ToolSecretRequirement,
)
from tests.api.adapters import DeterministicModelGateway


class RecordingAuditService:
    def __init__(self):
        self.events: list[AuditEventCreate] = []

    def record(self, event: AuditEventCreate) -> AuditEvent:
        self.events.append(event)
        return AuditEvent(
            id=new_id("audit"),
            tenant_id=event.tenant_id,
            workspace_id=event.workspace_id,
            user_id=event.user_id,
            run_id=event.run_id,
            event_type=event.event_type,
            metadata=event.metadata,
            created_at=utc_now(),
        )


def test_tool_gateway_executes_registered_tool_after_policy_validation():
    gateway = ToolGateway()
    gateway.register_tool(
        policy=ToolPolicy(
            tool_name="artifact.write",
            required_scopes=["storage.write"],
            risk_level=ToolRiskLevel.MEDIUM,
        ),
        handler=lambda request: ToolResult(
            tool_name=request.tool_name,
            output={"artifact_name": request.tool_input["name"]},
        ),
    )

    result = gateway.execute_request(
        ToolGatewayRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            user_id="user_1",
            run_id="run_1",
            step_id="step_write",
            tool_name="artifact.write",
            tool_input={"name": "brief.md"},
            granted_scopes=["storage.write"],
        )
    )

    assert result.tool_name == "artifact.write"
    assert result.output == {"artifact_name": "brief.md"}


def test_tool_gateway_injects_scoped_secret_leases_before_handler_runs():
    secret_service = InMemorySecretService()
    secret = secret_service.create_secret(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        name="crm-api-key",
        value="super-secret-api-key",
        scope=SecretScope(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            allowed_tool_names=["crm.lookup"],
            actions=["read"],
        ),
    )
    captured_requests: list[ToolGatewayRequest] = []

    def handler(request: ToolGatewayRequest) -> ToolResult:
        captured_requests.append(request)
        lease = request.secret_leases[0]
        value = secret_service.resolve_lease_value(
            tenant_id=request.tenant_id,
            lease_token=lease.lease_token,
        )
        return ToolResult(
            tool_name=request.tool_name,
            output={"authenticated": value == "super-secret-api-key"},
        )

    gateway = ToolGateway(secret_service=secret_service)
    gateway.register_tool(
        policy=ToolPolicy(
            tool_name="crm.lookup",
            required_scopes=["crm.read"],
            secret_requirements=[
                ToolSecretRequirement(
                    secret_id=secret.id,
                    actions=["read"],
                    ttl_seconds=60,
                )
            ],
        ),
        handler=handler,
    )

    result = gateway.execute_request(
        ToolGatewayRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            user_id="user_1",
            run_id="run_1",
            step_id="step_crm",
            tool_name="crm.lookup",
            tool_input={"account_id": "acct_1"},
            granted_scopes=["crm.read"],
        )
    )

    request = captured_requests[0]
    lease = request.secret_leases[0]
    audit_metadata = lease.to_audit_metadata()
    assert result.output == {"authenticated": True}
    assert request.tool_input == {"account_id": "acct_1"}
    assert lease.secret_ref_id == secret.id
    assert lease.tool_name == "crm.lookup"
    assert "super-secret-api-key" not in str(request.model_dump(mode="json"))
    assert "super-secret-api-key" not in str(audit_metadata)
    assert lease.lease_token not in str(audit_metadata)


def test_tool_gateway_requires_secret_service_before_secret_backed_tool_runs():
    calls: list[str] = []
    gateway = ToolGateway()
    gateway.register_tool(
        policy=ToolPolicy(
            tool_name="crm.lookup",
            secret_requirements=[
                ToolSecretRequirement(
                    secret_id="secret_crm",
                    actions=["read"],
                    ttl_seconds=60,
                )
            ],
        ),
        handler=lambda request: calls.append(request.tool_name) or ToolResult(tool_name=request.tool_name),
    )

    with pytest.raises(ToolExecutionError, match="secret service is not configured"):
        gateway.execute_request(
            ToolGatewayRequest(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                user_id="user_1",
                run_id="run_1",
                step_id="step_crm",
                tool_name="crm.lookup",
            )
        )

    assert calls == []


def test_tool_gateway_denies_missing_scope_before_handler_runs():
    gateway = ToolGateway()
    calls: list[str] = []
    gateway.register_tool(
        policy=ToolPolicy(
            tool_name="email.draft",
            required_scopes=["email.write"],
            risk_level=ToolRiskLevel.HIGH,
        ),
        handler=lambda request: calls.append(request.tool_name) or ToolResult(tool_name=request.tool_name),
    )

    with pytest.raises(ToolExecutionError, match="missing scopes: email.write"):
        gateway.execute_request(
            ToolGatewayRequest(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                user_id="user_1",
                run_id="run_1",
                step_id="step_email",
                tool_name="email.draft",
                tool_input={"to": "customer@example.com"},
                granted_scopes=[],
            )
        )

    assert calls == []


def test_tool_gateway_records_blocked_call_audit_with_redacted_input():
    audit_records = []
    gateway = ToolGateway(audit_recorder=audit_records.append)
    calls: list[str] = []
    gateway.register_tool(
        policy=ToolPolicy(
            tool_name="email.draft",
            required_scopes=["email.write"],
            risk_level=ToolRiskLevel.HIGH,
        ),
        handler=lambda request: calls.append(request.tool_name) or ToolResult(tool_name=request.tool_name),
    )

    with pytest.raises(ToolExecutionError, match="missing scopes: email.write"):
        gateway.execute_request(
            ToolGatewayRequest(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                user_id="user_1",
                run_id="run_1",
                step_id="step_email",
                tool_name="email.draft",
                tool_input={
                    "to": "customer@example.com",
                    "api_token": "secret-token-value",
                },
                granted_scopes=[],
            )
        )

    assert calls == []
    assert len(audit_records) == 1
    record = audit_records[0]
    assert record.event_type == "tool.blocked"
    assert record.tenant_id == "tenant_acme"
    assert record.workspace_id == "workspace_sales"
    assert record.user_id == "user_1"
    assert record.run_id == "run_1"
    assert record.step_id == "step_email"
    assert record.tool_name == "email.draft"
    assert record.reason == "Tool is not permitted: missing scopes: email.write"
    assert record.missing_scopes == ["email.write"]
    assert record.risk_level == ToolRiskLevel.HIGH
    assert record.tool_input == {
        "to": "customer@example.com",
        "api_token": "[REDACTED]",
    }
    assert "secret-token-value" not in str(record.model_dump(mode="json"))


def test_tool_gateway_records_blocked_call_through_audit_service():
    audit_service = RecordingAuditService()
    gateway = ToolGateway(audit_service=audit_service)
    gateway.register_tool(
        policy=ToolPolicy(
            tool_name="email.draft",
            required_scopes=["email.write"],
            risk_level=ToolRiskLevel.HIGH,
        ),
        handler=lambda request: ToolResult(tool_name=request.tool_name),
    )

    with pytest.raises(ToolExecutionError, match="missing scopes: email.write"):
        gateway.execute_request(
            ToolGatewayRequest(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                user_id="user_1",
                run_id="run_1",
                step_id="step_email",
                tool_name="email.draft",
                tool_input={
                    "to": "customer@example.com",
                    "api_token": "secret-token-value",
                },
                granted_scopes=[],
            )
        )

    assert [event.event_type for event in audit_service.events] == ["tool.blocked"]
    event = audit_service.events[0]
    assert event.tenant_id == "tenant_acme"
    assert event.workspace_id == "workspace_sales"
    assert event.user_id == "user_1"
    assert event.run_id == "run_1"
    assert event.metadata["step_id"] == "step_email"
    assert event.metadata["tool_name"] == "email.draft"
    assert event.metadata["missing_scopes"] == ["email.write"]
    assert event.metadata["risk_level"] == "high"
    assert event.metadata["tool_input"]["api_token"] == "[REDACTED]"
    assert event.actor.user_id == "user_1"
    assert event.actor.actor_type == "user"
    assert event.actor.tenant_id == "tenant_acme"
    assert "secret-token-value" not in str(event.metadata)


def test_tool_gateway_requires_approval_before_high_risk_execution():
    gateway = ToolGateway()
    gateway.register_tool(
        policy=ToolPolicy(
            tool_name="communication.send_email",
            required_scopes=["email.send"],
            risk_level=ToolRiskLevel.HIGH,
            approval_required=True,
        ),
        handler=lambda request: ToolResult(tool_name=request.tool_name, output={"sent": True}),
    )

    with pytest.raises(ToolApprovalRequiredError, match="approval required"):
        gateway.execute_request(
            ToolGatewayRequest(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                user_id="user_1",
                run_id="run_1",
                step_id="step_send",
                tool_name="communication.send_email",
                tool_input={"to": "customer@example.com"},
                granted_scopes=["email.send"],
                approved=False,
            )
        )


def test_tool_gateway_records_approval_required_through_audit_service():
    audit_service = RecordingAuditService()
    gateway = ToolGateway(audit_service=audit_service)
    gateway.register_tool(
        policy=ToolPolicy(
            tool_name="communication.send_email",
            required_scopes=["email.send"],
            risk_level=ToolRiskLevel.HIGH,
            approval_required=True,
        ),
        handler=lambda request: ToolResult(tool_name=request.tool_name, output={"sent": True}),
    )

    with pytest.raises(ToolApprovalRequiredError, match="approval required"):
        gateway.execute_request(
            ToolGatewayRequest(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                user_id="user_1",
                run_id="run_1",
                step_id="step_send",
                tool_name="communication.send_email",
                tool_input={"to": "customer@example.com"},
                granted_scopes=["email.send"],
                approved=False,
            )
        )

    assert [event.event_type for event in audit_service.events] == ["tool.approval_required"]
    event = audit_service.events[0]
    assert event.metadata["step_id"] == "step_send"
    assert event.metadata["tool_name"] == "communication.send_email"
    assert event.metadata["approved"] is False
    assert event.metadata["risk_level"] == "high"
    assert event.actor.user_id == "user_1"
    assert event.actor.actor_type == "user"
    assert event.actor.tenant_id == "tenant_acme"


def test_tool_gateway_guardrail_blocks_tool_request_before_handler_runs():
    guardrail_service = InMemoryGuardrailService()
    rule = guardrail_service.add_rule(
        GuardrailRule(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            stage=GuardrailStage.TOOL_REQUEST,
            condition=GuardrailCondition(attribute_equals={"external_write": True}),
            action=GuardrailAction.BLOCK,
            severity=GuardrailSeverity.HIGH,
            message="External write is blocked by tenant policy",
            audit_required=True,
        )
    )
    audit_records = []
    calls: list[str] = []
    gateway = ToolGateway(
        guardrail_service=guardrail_service,
        audit_recorder=audit_records.append,
    )
    gateway.register_tool(
        policy=ToolPolicy(tool_name="communication.send_email"),
        handler=lambda request: calls.append(request.tool_name) or ToolResult(tool_name=request.tool_name),
    )

    with pytest.raises(ToolExecutionError, match="External write is blocked by tenant policy"):
        gateway.execute_request(
            ToolGatewayRequest(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                user_id="user_1",
                run_id="run_1",
                step_id="step_send",
                tool_name="communication.send_email",
                tool_input={"external_write": True, "to": "customer@example.com"},
            )
        )

    assert calls == []
    assert [record.event_type for record in audit_records] == ["tool.guardrail_blocked"]
    assert audit_records[0].guardrail_rule_ids == [rule.id]
    assert audit_records[0].guardrail_action == GuardrailAction.BLOCK


def test_tool_gateway_guardrail_requires_approval_before_handler_runs():
    guardrail_service = InMemoryGuardrailService()
    rule = guardrail_service.add_rule(
        GuardrailRule(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            stage=GuardrailStage.TOOL_REQUEST,
            condition=GuardrailCondition(attribute_equals={"external_write": True}),
            action=GuardrailAction.REQUIRE_APPROVAL,
            severity=GuardrailSeverity.CRITICAL,
            message="External write requires policy approval",
        )
    )
    audit_records = []
    calls: list[str] = []
    gateway = ToolGateway(
        guardrail_service=guardrail_service,
        audit_recorder=audit_records.append,
    )
    gateway.register_tool(
        policy=ToolPolicy(tool_name="communication.send_email"),
        handler=lambda request: calls.append(request.tool_name) or ToolResult(tool_name=request.tool_name),
    )

    with pytest.raises(ToolApprovalRequiredError, match="External write requires policy approval"):
        gateway.execute_request(
            ToolGatewayRequest(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                user_id="user_1",
                run_id="run_1",
                step_id="step_send",
                tool_name="communication.send_email",
                tool_input={"external_write": True, "to": "customer@example.com"},
                approved=False,
            )
        )

    assert calls == []
    assert [record.event_type for record in audit_records] == ["tool.guardrail_approval_required"]
    assert audit_records[0].guardrail_rule_ids == [rule.id]
    assert audit_records[0].guardrail_action == GuardrailAction.REQUIRE_APPROVAL


def test_tool_gateway_guardrail_redacts_tool_input_before_handler_runs():
    guardrail_service = InMemoryGuardrailService()
    guardrail_service.add_rule(
        GuardrailRule(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            stage=GuardrailStage.TOOL_REQUEST,
            condition=GuardrailCondition(text_contains=["internal-only-token"]),
            action=GuardrailAction.REDACT,
            severity=GuardrailSeverity.MEDIUM,
            message="Sensitive text is redacted",
        )
    )
    captured_requests: list[ToolGatewayRequest] = []

    def handler(request: ToolGatewayRequest) -> ToolResult:
        captured_requests.append(request)
        return ToolResult(tool_name=request.tool_name, output={"body": request.tool_input["body"]})

    gateway = ToolGateway(guardrail_service=guardrail_service)
    gateway.register_tool(
        policy=ToolPolicy(tool_name="artifact.write"),
        handler=handler,
    )

    result = gateway.execute_request(
        ToolGatewayRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            user_id="user_1",
            run_id="run_1",
            step_id="step_write",
            tool_name="artifact.write",
            tool_input={"body": "Share internal-only-token with the report."},
        )
    )

    assert result.output == {"body": "Share [REDACTED] with the report."}
    assert captured_requests[0].tool_input == {"body": "Share [REDACTED] with the report."}


def test_tool_gateway_validates_input_schema_before_handler_runs():
    gateway = ToolGateway()
    calls: list[str] = []
    gateway.register_tool(
        policy=ToolPolicy(
            tool_name="research.lookup",
            input_schema={
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                },
                "additionalProperties": False,
            },
        ),
        handler=lambda request: calls.append(request.tool_name) or ToolResult(tool_name=request.tool_name),
    )

    with pytest.raises(ToolExecutionError, match="tool input is invalid"):
        gateway.execute_request(
            ToolGatewayRequest(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                user_id="user_1",
                run_id="run_1",
                step_id="step_research",
                tool_name="research.lookup",
                tool_input={"limit": 10},
            )
        )

    assert calls == []


def test_tool_gateway_validates_output_schema_after_handler_returns():
    gateway = ToolGateway()
    gateway.register_tool(
        policy=ToolPolicy(
            tool_name="research.lookup",
            input_schema={
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string"},
                },
            },
            output_schema={
                "type": "object",
                "required": ["items"],
                "properties": {
                    "items": {"type": "array"},
                },
            },
        ),
        handler=lambda request: ToolResult(
            tool_name=request.tool_name,
            output={"items": "not-a-list"},
        ),
    )

    with pytest.raises(ToolExecutionError, match="tool output is invalid"):
        gateway.execute_request(
            ToolGatewayRequest(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                user_id="user_1",
                run_id="run_1",
                step_id="step_research",
                tool_name="research.lookup",
                tool_input={"query": "prospect"},
            )
        )


def test_agent_runtime_calls_tool_gateway_with_run_context():
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_sales_research",
            message="Create a prospect brief.",
            mode="autonomous",
        ),
    )
    tool_gateway = RecordingToolGateway()
    runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(
            plan=[
                PlannedToolCall(
                    id="step_research",
                    title="Research prospect",
                    tool_name="research.lookup",
                    tool_input={"query": "prospect"},
                )
            ]
        ),
        tool_gateway=tool_gateway,
    )

    runtime.execute_run("tenant_acme", run.id)

    assert tool_gateway.last_request is not None
    assert tool_gateway.last_request.tenant_id == "tenant_acme"
    assert tool_gateway.last_request.workspace_id == "workspace_sales"
    assert tool_gateway.last_request.user_id == "user_1"
    assert tool_gateway.last_request.run_id == run.id
    assert tool_gateway.last_request.step_id == "step_research"
    assert tool_gateway.last_request.tool_input == {"query": "prospect"}


class RecordingToolGateway(ToolGateway):
    last_request: ToolGatewayRequest | None = None

    def execute_request(self, request: ToolGatewayRequest) -> ToolResult:
        self.last_request = request
        return ToolResult(
            tool_name=request.tool_name,
            output={"ok": True},
        )
