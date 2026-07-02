import pytest

from pydantic import Field

from taroai.domain import ApprovalStatus, RunCreate, RunStatus
from taroai.agent import AgentRuntime, PlanStep
from taroai.guardrails import (
    GuardrailAction,
    GuardrailCondition,
    GuardrailRule,
    GuardrailSeverity,
    GuardrailStage,
    InMemoryGuardrailService,
)
from taroai.model_gateway import (
    ModelBudgetGuard,
    ModelBudgetPolicy,
    ModelPolicy,
    ModelPolicyDeniedError,
    ModelPolicyScope,
    ModelUsage,
    PlannedToolCall,
)
from taroai.model_gateway import ModelGateway, ModelGatewayRequest, ModelGatewayResponse
from taroai.store import InMemoryControlPlaneStore
from taroai.tool_gateway import ToolGateway, ToolPolicy, ToolResult, ToolRiskLevel
from tests.api.adapters import DeterministicModelGateway, DeterministicToolGateway


class RecordingPlanGateway(ModelGateway):
    output_text: str = ""
    plan: list[PlannedToolCall] = Field(default_factory=list)
    requests: list[ModelGatewayRequest] = Field(default_factory=list)

    def create_plan(self, request: ModelGatewayRequest) -> ModelGatewayResponse:
        self.requests.append(request)
        return ModelGatewayResponse(
            id=f"response_{request.run_id}",
            model="recording-test",
            output_text=self.output_text,
            planned_steps=self.plan,
        )


def create_runtime_run(message: str = "Create a prospect brief."):
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_sales_research",
            message=message,
            mode="autonomous",
        ),
    )
    return store, run


def test_agent_runtime_completes_run_and_creates_artifact():
    store, run = create_runtime_run()
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
        tool_gateway=DeterministicToolGateway(),
    )

    state = runtime.execute_run("tenant_acme", run.id)

    assert state.status == RunStatus.SUCCEEDED
    assert store.get_run("tenant_acme", run.id).status == RunStatus.SUCCEEDED
    assert [artifact.name for artifact in store.list_artifacts("tenant_acme", run.id)] == [
        "agent-result.md"
    ]

    event_types = [event.type for event in store.list_run_events("tenant_acme", run.id)]
    assert event_types[-15:] == [
        "run.status_changed",
        "context.loaded",
        "billing.metered",
        "audit.recorded",
        "audit.recorded",
        "plan.created",
        "policy.checked",
        "step.started",
        "tool_call.started",
        "tool_call.completed",
        "billing.metered",
        "audit.recorded",
        "audit.recorded",
        "artifact.created",
        "run.succeeded",
    ]


def test_agent_runtime_blocks_guarded_artifact_publication():
    store, run = create_runtime_run()
    guardrail_service = InMemoryGuardrailService()
    rule = guardrail_service.add_rule(
        GuardrailRule(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            stage=GuardrailStage.ARTIFACT,
            condition=GuardrailCondition(text_contains=["agent-result.md"]),
            action=GuardrailAction.BLOCK,
            severity=GuardrailSeverity.HIGH,
            message="Artifact publication is blocked by policy",
        )
    )
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
        tool_gateway=DeterministicToolGateway(),
        guardrail_service=guardrail_service,
    )

    state = runtime.execute_run("tenant_acme", run.id)

    guardrail_audits = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "guardrail.artifact_blocked"
    ]
    run_events = store.list_run_events("tenant_acme", run.id)

    assert state.status == RunStatus.FAILED
    assert store.list_artifacts("tenant_acme", run.id) == []
    assert [event.metadata["guardrail_rule_ids"] for event in guardrail_audits] == [[rule.id]]
    assert guardrail_audits[0].metadata["guardrail_action"] == "block"
    assert run_events[-1].type == "run.failed"
    assert run_events[-1].payload["reason"] == "artifact_guardrail_blocked"
    assert "agent-result.md" not in str(guardrail_audits[0].metadata)


def test_agent_runtime_redacts_guarded_artifact_metadata_before_publication():
    store, run = create_runtime_run()
    guardrail_service = InMemoryGuardrailService()
    rule = guardrail_service.add_rule(
        GuardrailRule(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            stage=GuardrailStage.ARTIFACT,
            condition=GuardrailCondition(text_contains=["agent-result"]),
            action=GuardrailAction.REDACT,
            severity=GuardrailSeverity.MEDIUM,
            message="Artifact name contains restricted label",
            redaction_replacement="governed-result",
        )
    )
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
        tool_gateway=DeterministicToolGateway(),
        guardrail_service=guardrail_service,
    )

    state = runtime.execute_run("tenant_acme", run.id)

    artifact = store.list_artifacts("tenant_acme", run.id)[0]
    guardrail_audits = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "guardrail.artifact_redacted"
    ]
    run_events = store.list_run_events("tenant_acme", run.id)

    assert state.status == RunStatus.SUCCEEDED
    assert artifact.name == "governed-result.md"
    assert artifact.uri.endswith("/governed-result.md")
    assert [event.metadata["guardrail_rule_ids"] for event in guardrail_audits] == [[rule.id]]
    assert run_events[-1].payload["artifact_name"] == "governed-result.md"
    assert "agent-result" not in str(guardrail_audits[0].metadata)


def test_agent_runtime_resumes_artifact_guardrail_approval_after_worker_restart():
    store, run = create_runtime_run()
    guardrail_service = InMemoryGuardrailService()
    rule = guardrail_service.add_rule(
        GuardrailRule(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            stage=GuardrailStage.ARTIFACT,
            condition=GuardrailCondition(text_contains=["agent-result.md"]),
            action=GuardrailAction.REQUIRE_APPROVAL,
            severity=GuardrailSeverity.HIGH,
            message="Artifact publication requires approval",
        )
    )
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
        tool_gateway=DeterministicToolGateway(),
        guardrail_service=guardrail_service,
    )

    paused_state = runtime.execute_run("tenant_acme", run.id)
    approval = store.list_approval_requests("tenant_acme", run.id)[0]
    snapshot = store.get_runtime_state("tenant_acme", run.id)

    assert paused_state.status == RunStatus.AWAITING_APPROVAL
    assert store.list_artifacts("tenant_acme", run.id) == []
    assert approval.step_id == "guardrail:artifact"
    assert approval.reason == "Artifact publication requires approval"
    assert snapshot.pending_guardrail_approval_stage == "artifact"

    restarted_runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(),
        tool_gateway=DeterministicToolGateway(),
        guardrail_service=guardrail_service,
    )

    resumed_state = restarted_runtime.resume_after_approval(
        tenant_id="tenant_acme",
        run_id=run.id,
        approval_id=approval.id,
        approved_by_user_id="manager_1",
    )

    artifacts = store.list_artifacts("tenant_acme", run.id)

    assert resumed_state.status == RunStatus.SUCCEEDED
    assert [artifact.name for artifact in artifacts] == ["agent-result.md"]
    assert resumed_state.approved_guardrail_keys == [f"artifact:{rule.id}"]
    assert store.list_approval_requests("tenant_acme", run.id)[0].status == ApprovalStatus.APPROVED
    assert store.get_runtime_state("tenant_acme", run.id).approved_guardrail_keys == [
        f"artifact:{rule.id}"
    ]


def test_agent_runtime_records_tool_call_audit_and_billing():
    store, run = create_runtime_run()
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
        tool_gateway=DeterministicToolGateway(),
    )

    runtime.execute_run("tenant_acme", run.id)

    meters = store.list_billing_meters("tenant_acme")
    audits = store.list_audit_events("tenant_acme")

    tool_meters = [meter for meter in meters if meter.meter_type == "tool_call_count"]
    tool_audits = [event for event in audits if event.event_type == "tool.executed"]
    assert len(tool_meters) == 1
    assert tool_meters[0].run_id == run.id
    assert tool_meters[0].metadata["tool_name"] == "research.lookup"
    assert len(tool_audits) == 1
    assert tool_audits[0].metadata["step_id"] == "step_research"
    assert tool_audits[0].metadata["tool_name"] == "research.lookup"


def test_agent_runtime_records_model_usage_audit_and_billing_without_prompt_content():
    store, run = create_runtime_run("Create a prospect brief with private account context.")
    runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(
            model_name="gpt-enterprise-planner",
            usage=ModelUsage(input_tokens=120, output_tokens=45, total_tokens=165),
            plan=[
                PlannedToolCall(
                    id="step_research",
                    title="Research prospect",
                    tool_name="research.lookup",
                    tool_input={"query": "prospect"},
                )
            ],
        ),
        tool_gateway=DeterministicToolGateway(),
    )

    runtime.execute_run("tenant_acme", run.id)

    meters = store.list_billing_meters("tenant_acme")
    audits = store.list_audit_events("tenant_acme")
    model_meters = [
        meter
        for meter in meters
        if meter.meter_type
        in {"model_call_count", "model_tokens_input", "model_tokens_output"}
    ]
    model_audits = [event for event in audits if event.event_type == "model.plan.created"]

    assert [(meter.meter_type, meter.quantity, meter.unit) for meter in model_meters] == [
        ("model_call_count", 1, "call"),
        ("model_tokens_input", 120, "token"),
        ("model_tokens_output", 45, "token"),
    ]
    assert {meter.model for meter in model_meters} == {"gpt-enterprise-planner"}
    assert model_meters[0].metadata["response_id"] == f"response_{run.id}"
    assert model_meters[0].metadata["planned_step_count"] == 1
    assert len(model_audits) == 1
    assert model_audits[0].metadata["response_id"] == f"response_{run.id}"
    assert model_audits[0].metadata["model"] == "gpt-enterprise-planner"
    assert model_audits[0].metadata["usage"] == {
        "input_tokens": 120,
        "output_tokens": 45,
        "total_tokens": 165,
    }
    assert model_audits[0].metadata["planned_step_count"] == 1
    assert "private account context" not in str(model_audits[0].metadata)


def test_agent_runtime_enforces_workspace_scoped_model_policy_before_gateway_call():
    store, run = create_runtime_run()
    gateway = RecordingPlanGateway()
    runtime = AgentRuntime(
        store=store,
        model_gateway=gateway,
        model_policy=ModelPolicy(
            default_model="global-default",
            allowed_models=["global-default", "sales-approved"],
            scoped_policies=[
                ModelPolicyScope(
                    tenant_id="tenant_acme",
                    workspace_id="workspace_sales",
                    default_model="sales-denied",
                    allowed_models=["sales-approved"],
                )
            ],
        ),
    )

    with pytest.raises(ModelPolicyDeniedError):
        runtime.execute_run("tenant_acme", run.id)

    audits = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "model.policy_denied"
    ]
    run_events = store.list_run_events("tenant_acme", run.id)

    assert gateway.requests == []
    assert audits[0].metadata["requested_model"] == "sales-denied"
    assert audits[0].metadata["allowed_models"] == ["sales-approved"]
    assert audits[0].metadata["policy_scope"] == {
        "tenant_id": "tenant_acme",
        "workspace_id": "workspace_sales",
    }
    assert run_events[-1].payload["reason"] == "model_policy_denied"


def test_agent_runtime_blocks_guarded_model_request_before_gateway_call():
    store, run = create_runtime_run("Export raw customer secret values into the account brief.")
    guardrail_service = InMemoryGuardrailService()
    rule = guardrail_service.add_rule(
        GuardrailRule(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            stage=GuardrailStage.MODEL_REQUEST,
            condition=GuardrailCondition(text_contains=["raw customer secret"]),
            action=GuardrailAction.BLOCK,
            severity=GuardrailSeverity.CRITICAL,
            message="Model request contains restricted secret extraction intent",
        )
    )
    model_gateway = DeterministicModelGateway(
        plan=[
            PlannedToolCall(
                id="step_research",
                title="Research prospect",
                tool_name="research.lookup",
                tool_input={"query": "prospect"},
            )
        ],
    )
    runtime = AgentRuntime(
        store=store,
        model_gateway=model_gateway,
        guardrail_service=guardrail_service,
        tool_gateway=DeterministicToolGateway(),
    )

    state = runtime.execute_run("tenant_acme", run.id)

    guardrail_audits = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "guardrail.model_request_blocked"
    ]
    run_events = store.list_run_events("tenant_acme", run.id)

    assert state.status == RunStatus.FAILED
    assert model_gateway.call_count == 0
    assert [event.metadata["guardrail_rule_ids"] for event in guardrail_audits] == [[rule.id]]
    assert guardrail_audits[0].metadata["guardrail_action"] == "block"
    assert run_events[-1].type == "run.failed"
    assert run_events[-1].payload["reason"] == "model_guardrail_blocked"
    assert "raw customer secret" not in str(guardrail_audits[0].metadata)


def test_agent_runtime_redacts_guarded_model_request_before_gateway_call():
    store, run = create_runtime_run("Summarize account token raw-customer-secret for renewal planning.")
    guardrail_service = InMemoryGuardrailService()
    rule = guardrail_service.add_rule(
        GuardrailRule(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            stage=GuardrailStage.MODEL_REQUEST,
            condition=GuardrailCondition(text_contains=["raw-customer-secret"]),
            action=GuardrailAction.REDACT,
            severity=GuardrailSeverity.HIGH,
            message="Model request contains restricted token material",
        )
    )
    model_gateway = RecordingPlanGateway(
        plan=[
            PlannedToolCall(
                id="step_research",
                title="Research prospect",
                tool_name="research.lookup",
                tool_input={"query": "prospect"},
            )
        ],
    )
    runtime = AgentRuntime(
        store=store,
        model_gateway=model_gateway,
        guardrail_service=guardrail_service,
        tool_gateway=DeterministicToolGateway(),
    )

    state = runtime.execute_run("tenant_acme", run.id)

    guardrail_audits = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "guardrail.model_request_redacted"
    ]
    model_messages = "\n".join(message.content for message in model_gateway.requests[0].messages)

    assert state.status == RunStatus.SUCCEEDED
    assert "raw-customer-secret" not in model_messages
    assert "[REDACTED]" in model_messages
    assert [event.metadata["guardrail_rule_ids"] for event in guardrail_audits] == [[rule.id]]
    assert "raw-customer-secret" not in str(guardrail_audits[0].metadata)


def test_agent_runtime_redacts_guarded_model_response_before_plan_execution():
    store, run = create_runtime_run("Draft the renewal email.")
    guardrail_service = InMemoryGuardrailService()
    rule = guardrail_service.add_rule(
        GuardrailRule(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            stage=GuardrailStage.MODEL_RESPONSE,
            condition=GuardrailCondition(text_contains=["raw-customer-secret"]),
            action=GuardrailAction.REDACT,
            severity=GuardrailSeverity.HIGH,
            message="Model response contains restricted token material",
        )
    )
    output_text = (
        '{"steps":[{"id":"step_send","title":"Send summary",'
        '"tool_name":"communication.send_email",'
        '"tool_input":{"body":"Account token is raw-customer-secret"},'
        '"approval_required":false}]}'
    )
    model_gateway = RecordingPlanGateway(
        output_text=output_text,
        plan=[
            PlannedToolCall(
                id="step_send",
                title="Send summary",
                tool_name="communication.send_email",
                tool_input={"body": "Account token is raw-customer-secret"},
            )
        ],
    )
    runtime = AgentRuntime(
        store=store,
        model_gateway=model_gateway,
        guardrail_service=guardrail_service,
        tool_gateway=DeterministicToolGateway(),
    )

    state = runtime.execute_run("tenant_acme", run.id)

    guardrail_audits = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "guardrail.model_response_redacted"
    ]

    assert state.status == RunStatus.SUCCEEDED
    assert state.plan[0].tool_input["body"] == "Account token is [REDACTED]"
    assert [event.metadata["guardrail_rule_ids"] for event in guardrail_audits] == [[rule.id]]
    assert "raw-customer-secret" not in str(state.model_dump(mode="json"))
    assert "raw-customer-secret" not in str(guardrail_audits[0].metadata)


def test_agent_runtime_blocks_model_call_when_run_budget_is_exhausted():
    store, run = create_runtime_run("Create a prospect brief with private account context.")
    store.record_billing_meter(
        tenant_id="tenant_acme",
        run_id=run.id,
        meter_type="model_call_count",
        quantity=1,
        unit="call",
        model="gpt-enterprise-planner",
        metadata={"reason": "previous planning call"},
    )
    model_gateway = DeterministicModelGateway(
        model_name="gpt-enterprise-planner",
        plan=[
            PlannedToolCall(
                id="step_research",
                title="Research prospect",
                tool_name="research.lookup",
                tool_input={"query": "prospect"},
            )
        ],
    )
    runtime = AgentRuntime(
        store=store,
        model_gateway=model_gateway,
        model_budget_guard=ModelBudgetGuard(
            policy=ModelBudgetPolicy(max_model_calls_per_run=1),
        ),
        tool_gateway=DeterministicToolGateway(),
    )

    state = runtime.execute_run("tenant_acme", run.id)

    audits = store.list_audit_events("tenant_acme")
    budget_audits = [event for event in audits if event.event_type == "model.budget_exceeded"]
    assert state.status == RunStatus.FAILED
    assert store.get_run("tenant_acme", run.id).status == RunStatus.FAILED
    assert model_gateway.call_count == 0
    assert len(budget_audits) == 1
    assert budget_audits[0].metadata["limit_type"] == "model_call_count"
    assert budget_audits[0].metadata["current_quantity"] == 1
    assert budget_audits[0].metadata["limit"] == 1
    assert "private account context" not in str(budget_audits[0].metadata)


def test_agent_runtime_blocks_model_call_when_workspace_budget_is_exhausted():
    store, previous_run = create_runtime_run("Previous model use.")
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id="user_1",
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_sales_research",
            message="Create a prospect brief with private account context.",
            mode="autonomous",
        ),
    )
    store.record_billing_meter(
        tenant_id="tenant_acme",
        run_id=previous_run.id,
        meter_type="model_call_count",
        quantity=1,
        unit="call",
        model="gpt-enterprise-planner",
        metadata={"reason": "previous planning call"},
    )
    model_gateway = DeterministicModelGateway(
        model_name="gpt-enterprise-planner",
        plan=[
            PlannedToolCall(
                id="step_research",
                title="Research prospect",
                tool_name="research.lookup",
                tool_input={"query": "prospect"},
            )
        ],
    )
    runtime = AgentRuntime(
        store=store,
        model_gateway=model_gateway,
        model_budget_guard=ModelBudgetGuard(
            policy=ModelBudgetPolicy(max_model_calls_per_workspace=1),
        ),
        tool_gateway=DeterministicToolGateway(),
    )

    state = runtime.execute_run("tenant_acme", run.id)

    audits = store.list_audit_events("tenant_acme")
    budget_audits = [event for event in audits if event.event_type == "model.budget_exceeded"]
    assert state.status == RunStatus.FAILED
    assert model_gateway.call_count == 0
    assert len(budget_audits) == 1
    assert budget_audits[0].metadata["scope_type"] == "workspace"
    assert budget_audits[0].metadata["scope_id"] == "workspace_sales"
    assert budget_audits[0].metadata["limit_type"] == "model_call_count"
    assert "private account context" not in str(budget_audits[0].metadata)


def test_agent_runtime_pauses_for_approval_and_resumes_after_approval():
    store, run = create_runtime_run("Send this brief to an external customer.")
    runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(
            plan=[
                PlannedToolCall(
                    id="step_send",
                    title="Send customer email",
                    tool_name="communication.send_email",
                    tool_input={"to": "customer@example.com"},
                    approval_required=True,
                )
            ]
        ),
        tool_gateway=DeterministicToolGateway(),
    )

    paused_state = runtime.execute_run("tenant_acme", run.id)

    assert paused_state.status == RunStatus.AWAITING_APPROVAL
    assert store.get_run("tenant_acme", run.id).status == RunStatus.AWAITING_APPROVAL
    approval = store.list_approval_requests("tenant_acme", run.id)[0]
    assert approval.status == ApprovalStatus.PENDING
    assert approval.reason == "Step requires approval: Send customer email"

    resumed_state = runtime.resume_after_approval(
        tenant_id="tenant_acme",
        run_id=run.id,
        approval_id=approval.id,
        approved_by_user_id="manager_1",
    )

    assert resumed_state.status == RunStatus.SUCCEEDED
    assert store.list_approval_requests("tenant_acme", run.id)[0].status == ApprovalStatus.APPROVED
    assert store.get_run("tenant_acme", run.id).status == RunStatus.SUCCEEDED

    event_types = [event.type for event in store.list_run_events("tenant_acme", run.id)]
    assert "approval.requested" in event_types
    assert "approval.resolved" in event_types
    assert event_types[-1] == "run.succeeded"


def test_agent_runtime_rejects_paused_approval_without_executing_step():
    store, run = create_runtime_run("Send this brief to an external customer.")
    runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(
            plan=[
                PlannedToolCall(
                    id="step_send",
                    title="Send customer email",
                    tool_name="communication.send_email",
                    tool_input={"to": "customer@example.com"},
                    approval_required=True,
                )
            ]
        ),
        tool_gateway=DeterministicToolGateway(),
    )
    paused_state = runtime.execute_run("tenant_acme", run.id)
    assert paused_state.status == RunStatus.AWAITING_APPROVAL
    approval = store.list_approval_requests("tenant_acme", run.id)[0]

    rejected_state = runtime.reject_approval(
        tenant_id="tenant_acme",
        run_id=run.id,
        approval_id=approval.id,
        rejected_by_user_id="manager_1",
    )

    assert rejected_state.status == RunStatus.FAILED
    assert rejected_state.approval_id is None
    assert rejected_state.tool_results == []
    assert runtime.pending_states == {}
    assert store.get_run("tenant_acme", run.id).status == RunStatus.FAILED
    assert store.list_artifacts("tenant_acme", run.id) == []
    assert store.list_approval_requests("tenant_acme", run.id)[0].status == ApprovalStatus.REJECTED
    event_types = [event.type for event in store.list_run_events("tenant_acme", run.id)]
    assert "approval.rejected" in event_types
    assert event_types[-1] == "run.failed"


def test_agent_runtime_cancels_paused_run_and_pending_approval():
    store, run = create_runtime_run("Send this brief to an external customer.")
    runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(
            plan=[
                PlannedToolCall(
                    id="step_send",
                    title="Send customer email",
                    tool_name="communication.send_email",
                    tool_input={"to": "customer@example.com"},
                    approval_required=True,
                )
            ]
        ),
        tool_gateway=DeterministicToolGateway(),
    )
    paused_state = runtime.execute_run("tenant_acme", run.id)
    approval = store.list_approval_requests("tenant_acme", run.id)[0]

    cancelled_run = runtime.cancel_run(
        tenant_id="tenant_acme",
        run_id=run.id,
        cancelled_by_user_id="manager_1",
        reason_code="user_requested",
    )

    snapshot = store.get_runtime_state("tenant_acme", run.id)
    assert paused_state.status == RunStatus.CANCELLED
    assert cancelled_run.status == RunStatus.CANCELLED
    assert snapshot.status == RunStatus.CANCELLED
    assert snapshot.approval_id is None
    assert runtime.pending_states == {}
    assert store.list_approval_requests("tenant_acme", run.id)[0].status == ApprovalStatus.CANCELLED
    assert store.list_approval_requests("tenant_acme", run.id)[0].id == approval.id
    assert store.list_artifacts("tenant_acme", run.id) == []
    event_types = [event.type for event in store.list_run_events("tenant_acme", run.id)]
    assert "approval.cancelled" in event_types
    assert event_types[-1] == "run.cancelled"


def test_agent_runtime_pauses_when_tool_policy_requires_approval():
    store, run = create_runtime_run("Send this brief to an external customer.")
    tool_gateway = ToolGateway()
    tool_gateway.register_tool(
        policy=ToolPolicy(
            tool_name="communication.send_email",
            risk_level=ToolRiskLevel.HIGH,
            approval_required=True,
        ),
        handler=lambda request: ToolResult(
            tool_name=request.tool_name,
            output={"sent": True},
        ),
    )
    runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(
            plan=[
                PlannedToolCall(
                    id="step_send",
                    title="Send customer email",
                    tool_name="communication.send_email",
                    tool_input={"to": "customer@example.com"},
                    approval_required=False,
                )
            ]
        ),
        tool_gateway=tool_gateway,
    )

    paused_state = runtime.execute_run("tenant_acme", run.id)

    assert paused_state.status == RunStatus.AWAITING_APPROVAL
    approval = store.list_approval_requests("tenant_acme", run.id)[0]
    assert approval.step_id == "step_send"
    assert approval.reason == "Tool approval required: communication.send_email"

    resumed_state = runtime.resume_after_approval(
        tenant_id="tenant_acme",
        run_id=run.id,
        approval_id=approval.id,
        approved_by_user_id="manager_1",
    )

    assert resumed_state.status == RunStatus.SUCCEEDED
    assert store.get_run("tenant_acme", run.id).status == RunStatus.SUCCEEDED


def test_agent_runtime_audits_failed_tool_call_without_raw_sensitive_inputs():
    store, run = create_runtime_run("Look up a prospect.")
    runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(
            plan=[
                PlannedToolCall(
                    id="step_research",
                    title="Research prospect",
                    tool_name="research.lookup",
                    tool_input={"query": "prospect", "api_key": "raw-key-value"},
                )
            ]
        ),
        tool_gateway=ToolGateway(),
    )

    state = runtime.execute_run("tenant_acme", run.id)

    audits = store.list_audit_events("tenant_acme")
    failed_tool_audits = [event for event in audits if event.event_type == "tool.failed"]
    assert state.status == RunStatus.FAILED
    assert len(failed_tool_audits) == 1
    assert failed_tool_audits[0].metadata["step_id"] == "step_research"
    assert failed_tool_audits[0].metadata["tool_name"] == "research.lookup"
    assert failed_tool_audits[0].metadata["tool_input"]["query"] == "prospect"
    assert failed_tool_audits[0].metadata["tool_input"]["api_key"] == "[REDACTED]"
    assert "raw-key-value" not in str(failed_tool_audits[0].metadata)


def test_agent_runtime_resumes_approval_from_persisted_state_after_worker_restart():
    store, run = create_runtime_run("Send this brief to an external customer.")
    runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(
            plan=[
                PlannedToolCall(
                    id="step_send",
                    title="Send customer email",
                    tool_name="communication.send_email",
                    tool_input={"to": "customer@example.com"},
                    approval_required=True,
                )
            ]
        ),
        tool_gateway=DeterministicToolGateway(),
    )

    paused_state = runtime.execute_run("tenant_acme", run.id)
    approval = store.list_approval_requests("tenant_acme", run.id)[0]

    assert paused_state.status == RunStatus.AWAITING_APPROVAL
    assert store.get_runtime_state("tenant_acme", run.id).status == RunStatus.AWAITING_APPROVAL

    restarted_runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(),
        tool_gateway=DeterministicToolGateway(),
    )

    resumed_state = restarted_runtime.resume_after_approval(
        tenant_id="tenant_acme",
        run_id=run.id,
        approval_id=approval.id,
        approved_by_user_id="manager_1",
    )

    assert resumed_state.status == RunStatus.SUCCEEDED
    assert resumed_state.completed_step_ids == ["step_send"]
    assert store.get_runtime_state("tenant_acme", run.id).status == RunStatus.SUCCEEDED
    assert store.get_run("tenant_acme", run.id).status == RunStatus.SUCCEEDED


def test_agent_runtime_resumes_model_request_guardrail_approval_after_worker_restart():
    store, run = create_runtime_run("Research requires-review customer context.")
    guardrail_service = InMemoryGuardrailService()
    rule = guardrail_service.add_rule(
        GuardrailRule(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            stage=GuardrailStage.MODEL_REQUEST,
            condition=GuardrailCondition(text_contains=["requires-review"]),
            action=GuardrailAction.REQUIRE_APPROVAL,
            severity=GuardrailSeverity.HIGH,
            message="Model request requires approval before provider invocation",
        )
    )
    first_gateway = DeterministicModelGateway(
        plan=[
            PlannedToolCall(
                id="step_research",
                title="Research account",
                tool_name="research.lookup",
                tool_input={"query": "account"},
            )
        ]
    )
    runtime = AgentRuntime(
        store=store,
        model_gateway=first_gateway,
        tool_gateway=DeterministicToolGateway(),
        guardrail_service=guardrail_service,
    )

    paused_state = runtime.execute_run("tenant_acme", run.id)
    approval = store.list_approval_requests("tenant_acme", run.id)[0]
    snapshot = store.get_runtime_state("tenant_acme", run.id)

    assert paused_state.status == RunStatus.AWAITING_APPROVAL
    assert first_gateway.call_count == 0
    assert approval.step_id == "guardrail:model_request"
    assert approval.reason == "Model request requires approval before provider invocation"
    assert snapshot.status == RunStatus.AWAITING_APPROVAL
    assert snapshot.pending_guardrail_approval_stage == "model_request"
    assert snapshot.approved_guardrail_keys == []

    resumed_gateway = DeterministicModelGateway(
        plan=[
            PlannedToolCall(
                id="step_research",
                title="Research account",
                tool_name="research.lookup",
                tool_input={"query": "account"},
            )
        ]
    )
    restarted_runtime = AgentRuntime(
        store=store,
        model_gateway=resumed_gateway,
        tool_gateway=DeterministicToolGateway(),
        guardrail_service=guardrail_service,
    )

    resumed_state = restarted_runtime.resume_after_approval(
        tenant_id="tenant_acme",
        run_id=run.id,
        approval_id=approval.id,
        approved_by_user_id="manager_1",
    )

    assert resumed_state.status == RunStatus.SUCCEEDED
    assert resumed_gateway.call_count == 1
    assert store.get_run("tenant_acme", run.id).status == RunStatus.SUCCEEDED
    assert store.list_approval_requests("tenant_acme", run.id)[0].status == ApprovalStatus.APPROVED
    assert resumed_state.approved_guardrail_keys == [f"model_request:{rule.id}"]
    assert store.get_runtime_state("tenant_acme", run.id).approved_guardrail_keys == [
        f"model_request:{rule.id}"
    ]


def test_agent_runtime_retries_transient_tool_failure():
    store, run = create_runtime_run()
    runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(
            plan=[
                PlannedToolCall(
                    id="step_data",
                    title="Analyze source data",
                    tool_name="data.analyze",
                    tool_input={"file_id": "file_123"},
                )
            ]
        ),
        tool_gateway=DeterministicToolGateway(fail_once_for=["data.analyze"]),
        max_step_retries=1,
    )

    state = runtime.execute_run("tenant_acme", run.id)

    assert state.status == RunStatus.SUCCEEDED
    assert runtime.tool_gateway.call_counts["data.analyze"] == 2
    event_types = [event.type for event in store.list_run_events("tenant_acme", run.id)]
    assert "step.retrying" in event_types
    assert "tool_call.failed" in event_types


def test_agent_runtime_builds_langgraph_graph():
    runtime = AgentRuntime(store=InMemoryControlPlaneStore())

    graph = runtime.build_graph()
    compiled = graph.compile()

    assert compiled is not None
