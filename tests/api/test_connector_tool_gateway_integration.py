from fastapi.testclient import TestClient

from taroai.agent import AgentRuntime, PlanStep
from taroai.agent.loop import AgentExecutionServices
from taroai.agent.models import AgentDecision
from taroai.app import create_app
from taroai.config import Settings
from taroai.connectors import (
    ConnectorAuthMode,
    ConnectorCapability,
    ConnectorDefinitionCreate,
    ConnectorDispatchService,
    ConnectorHttpResponse,
    ConnectorInvocationRequest,
    ConnectorInvocationService,
    ConnectorInvocationStatus,
    ConnectorStatus,
    ConnectorType,
    InMemoryConnectorRegistry,
)
from taroai.domain import (
    ApprovalStatus,
    ChatMessageCreate,
    ChatThreadCreate,
    RunCreate,
    RunMode,
    RunStatus,
)
from taroai.identity import (
    InMemoryIdentityService,
    PasswordHasher,
    Permission,
    Role,
    UserAccountCreate,
)
from taroai.store import InMemoryControlPlaneStore
from taroai.model_gateway import PlannedToolCall
from tests.api.adapters import DeterministicModelGateway


def create_connector_operator_identity(
    can_manage: bool = True,
    can_read: bool = True,
    can_invoke: bool = True,
):
    identity = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="connector-operator@example.com",
            display_name="Connector Operator",
            password="correct horse battery staple",
        )
    )
    permissions = []
    if can_manage:
        permissions.append(Permission(action="connectors.manage", resource="tenant:tenant_acme"))
    if can_read:
        permissions.append(Permission(action="connectors.read", resource="tenant:tenant_acme"))
    if can_invoke:
        permissions.append(Permission(action="connectors.invoke", resource="tenant:tenant_acme"))
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_connector_operator",
            name="Connector Operator",
            permissions=permissions,
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_connector_operator")
    return identity, account


def create_run(store: InMemoryControlPlaneStore, user_id: str):
    return store.create_run(
        tenant_id="tenant_acme",
        user_id=user_id,
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_sales",
            message="Look up account context",
        ),
    )


def registered_connector(
    registry: InMemoryConnectorRegistry,
    *,
    status: ConnectorStatus = ConnectorStatus.ENABLED,
    approval_required: bool = False,
):
    return registry.register_connector(
        ConnectorDefinitionCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            type=ConnectorType.SAAS,
            display_name="Sales CRM",
            owner_user_id="user_admin",
            auth_mode=ConnectorAuthMode.NONE,
            status=status,
            capabilities=[
                ConnectorCapability(
                    name="search_accounts",
                    required_scopes=["crm.accounts.read"],
                    risk_level="medium",
                    approval_required=approval_required,
                    input_schema={
                        "type": "object",
                        "required": ["account_name"],
                        "properties": {"account_name": {"type": "string"}},
                    },
                )
            ],
        )
    )


def registered_internal_api_connector(
    registry: InMemoryConnectorRegistry,
    *,
    approval_required: bool = False,
    allowed_paths: list[str] | None = None,
):
    return registry.register_connector(
        ConnectorDefinitionCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            type=ConnectorType.INTERNAL_API,
            display_name="Sales CRM API",
            owner_user_id="user_admin",
            auth_mode=ConnectorAuthMode.NONE,
            status=ConnectorStatus.ENABLED,
            metadata={
                "internal_api": {
                    "base_url": "https://internal.example.com",
                    "allowed_methods": ["GET"],
                    "allowed_paths": allowed_paths or ["/accounts/*"],
                    "timeout_seconds": 4,
                }
            },
            capabilities=[
                ConnectorCapability(
                    name="search_accounts",
                    required_scopes=["crm.accounts.read"],
                    risk_level="medium",
                    approval_required=approval_required,
                    input_schema={
                        "type": "object",
                        "required": ["path"],
                        "properties": {
                            "path": {"type": "string"},
                            "method": {"type": "string"},
                        },
                    },
                )
            ],
        )
    )


def test_agent_connector_schema_exposes_exact_internal_api_allowlist():
    registry = InMemoryConnectorRegistry()
    registered_internal_api_connector(registry, allowed_paths=["/healthz"])
    store = InMemoryControlPlaneStore()
    run = create_run(store, "user_1")
    runtime = AgentRuntime(store=store, connector_registry=registry)

    tool = AgentExecutionServices(runtime)._discover_connector_tools(run)[0]

    properties = tool["input_schema"]["properties"]
    assert properties["method"]["enum"] == ["GET"]
    assert properties["path"]["enum"] == ["/healthz"]


class LocalConnectorHttpClient:
    def __init__(self, response: ConnectorHttpResponse):
        self.response = response
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        return self.response


def test_connector_invocation_service_reuses_tool_gateway_policy_decision():
    registry = InMemoryConnectorRegistry()
    connector = registered_connector(registry)

    decision = ConnectorInvocationService().evaluate(
        connector=connector,
        request=ConnectorInvocationRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            user_id="user_1",
            run_id="run_1",
            step_id="step_crm",
            connector_id=connector.id,
            capability_name="search_accounts",
            tool_input={"account_name": "Acme"},
            granted_scopes=["crm.accounts.read"],
        ),
    )

    assert decision.status == ConnectorInvocationStatus.READY
    assert decision.tool_name == f"connector.{connector.id}.search_accounts"
    assert decision.required_scopes == ["crm.accounts.read"]
    assert decision.missing_scopes == []
    assert decision.approval_required is False
    assert decision.billing_meter_type == "connector_invocation_count"
    assert decision.input_keys == ["account_name"]
    assert "Acme" not in str(decision.model_dump(mode="json"))


def test_connector_invocation_service_denies_missing_scope_before_dispatch():
    registry = InMemoryConnectorRegistry()
    connector = registered_connector(registry)

    decision = ConnectorInvocationService().evaluate(
        connector=connector,
        request=ConnectorInvocationRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            user_id="user_1",
            run_id="run_1",
            step_id="step_crm",
            connector_id=connector.id,
            capability_name="search_accounts",
            tool_input={"account_name": "Acme"},
            granted_scopes=[],
        ),
    )

    assert decision.status == ConnectorInvocationStatus.DENIED
    assert decision.missing_scopes == ["crm.accounts.read"]
    assert decision.billing_meter_type is None


def test_connector_invocation_service_requires_approval_for_guarded_capability():
    registry = InMemoryConnectorRegistry()
    connector = registered_connector(registry, approval_required=True)
    service = ConnectorInvocationService()

    pending = service.evaluate(
        connector=connector,
        request=ConnectorInvocationRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            user_id="user_1",
            run_id="run_1",
            step_id="step_crm",
            connector_id=connector.id,
            capability_name="search_accounts",
            tool_input={"account_name": "Acme"},
            granted_scopes=["crm.accounts.read"],
        ),
    )
    approved = service.evaluate(
        connector=connector,
        request=ConnectorInvocationRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            user_id="user_1",
            run_id="run_1",
            step_id="step_crm",
            connector_id=connector.id,
            capability_name="search_accounts",
            tool_input={"account_name": "Acme"},
            granted_scopes=["crm.accounts.read"],
            approved=True,
        ),
    )

    assert pending.status == ConnectorInvocationStatus.APPROVAL_REQUIRED
    assert pending.billing_meter_type is None
    assert approved.status == ConnectorInvocationStatus.READY
    assert approved.billing_meter_type == "connector_invocation_count"


def test_full_auto_agent_executes_guarded_connector_without_manual_approval():
    store = InMemoryControlPlaneStore()
    run = create_run(store, "user_1")
    registry = InMemoryConnectorRegistry()
    connector = registered_internal_api_connector(registry, approval_required=True)
    http_client = LocalConnectorHttpClient(
        ConnectorHttpResponse(status_code=200, body=b'{"ok":true}')
    )
    runtime = AgentRuntime(
        store=store,
        connector_registry=registry,
        connector_dispatcher=ConnectorDispatchService(http_client=http_client),
        connector_invocation_service=ConnectorInvocationService(),
    )
    state = runtime._initial_state(run)
    state.runtime_metadata["agent_context"] = {"write_autonomy": "full_auto"}
    step = PlanStep(
        id="step_crm",
        title="Look up account",
        tool_name=f"connector.{connector.id}.search_accounts",
        tool_input={"method": "GET", "path": "/accounts/42"},
    )
    decision = AgentDecision(
        kind="action",
        tool_name=step.tool_name,
        tool_input=step.tool_input,
    )
    execution = AgentExecutionServices(runtime)

    assert execution._requires_approval(state, run, decision, step) is False
    assert execution._requires_approval(
        state,
        run,
        decision.model_copy(update={"approval_required": True}),
        step,
    ) is True
    execution._execute_connector_action(state, run, step)
    assert len(http_client.requests) == 1


def test_connector_invoke_api_records_safe_audit_and_meter_for_authorized_call():
    identity, account = create_connector_operator_identity()
    store = InMemoryControlPlaneStore()
    registry = InMemoryConnectorRegistry()
    connector = registered_connector(registry)
    run = create_run(store, account.id)
    client = TestClient(
        create_app(
            identity_service=identity,
            store=store,
            connector_registry=registry,
            settings=Settings(_env_file=None),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}

    response = client.post(
        f"/api/connectors/{connector.id}/invoke",
        headers=headers,
        json={
            "run_id": run.id,
            "step_id": "step_crm",
            "capability_name": "search_accounts",
            "tool_input": {"account_name": "Acme", "api_key": "secret-value"},
            "granted_scopes": ["crm.accounts.read"],
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "ready"
    assert body["connector_id"] == connector.id
    assert body["input_keys"] == ["account_name", "api_key"]
    assert "Acme" not in str(body)
    assert "secret-value" not in str(body)

    events = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "connector.invoked"
    ]
    assert len(events) == 1
    assert events[0].metadata["connector_id"] == connector.id
    assert events[0].metadata["input_keys"] == ["account_name", "api_key"]
    assert "Acme" not in str(events[0].metadata)
    assert "secret-value" not in str(events[0].metadata)

    meters = [
        meter
        for meter in store.list_billing_meters("tenant_acme")
        if meter.meter_type == "connector_invocation_count"
    ]
    assert len(meters) == 1
    assert meters[0].run_id == run.id
    assert meters[0].quantity == 1
    assert meters[0].unit == "invocation"
    assert meters[0].metadata["capability_name"] == "search_accounts"


def test_connector_invoke_api_requires_invoke_permission():
    identity, account = create_connector_operator_identity(can_invoke=False)
    registry = InMemoryConnectorRegistry()
    connector = registered_connector(registry)
    client = TestClient(create_app(identity_service=identity, connector_registry=registry))

    response = client.post(
        f"/api/connectors/{connector.id}/invoke",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id},
        json={
            "run_id": "run_1",
            "step_id": "step_crm",
            "capability_name": "search_accounts",
            "tool_input": {"account_name": "Acme"},
            "granted_scopes": ["crm.accounts.read"],
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "tenant_access_denied"


def test_connector_invoke_api_returns_approval_required_without_billing():
    identity, account = create_connector_operator_identity()
    store = InMemoryControlPlaneStore()
    registry = InMemoryConnectorRegistry()
    connector = registered_connector(registry, approval_required=True)
    run = create_run(store, account.id)
    client = TestClient(
        create_app(
            identity_service=identity,
            store=store,
            connector_registry=registry,
            settings=Settings(_env_file=None),
        )
    )

    payload = {
        "run_id": run.id,
        "step_id": "step_crm",
        "capability_name": "search_accounts",
        "tool_input": {"account_name": "Acme"},
        "granted_scopes": ["crm.accounts.read"],
    }
    response = client.post(
        f"/api/connectors/{connector.id}/invoke",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id},
        json=payload,
    )
    replay = client.post(
        f"/api/connectors/{connector.id}/invoke",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id},
        json=payload,
    )

    assert response.status_code == 202
    assert response.json()["status"] == "approval_required"
    assert replay.status_code == 202
    assert replay.json()["approval_id"] == response.json()["approval_id"]
    approvals = store.list_approval_requests("tenant_acme", run.id)
    assert len(approvals) == 1
    assert approvals[0].id == response.json()["approval_id"]
    assert approvals[0].step_id == "step_crm"
    assert approvals[0].kind == "connector_action"
    assert approvals[0].status == ApprovalStatus.PENDING
    assert approvals[0].reason == (
        f"connector approval required: {connector.id}:search_accounts"
    )
    assert approvals[0].preview_payload["input"] == {"account_name": "Acme"}
    assert [
        meter
        for meter in store.list_billing_meters("tenant_acme")
        if meter.meter_type == "connector_invocation_count"
    ] == []
    approval_events = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "connector.approval_required"
    ]
    assert len(approval_events) == 2
    assert approval_events[0].metadata["approval_id"] == response.json()["approval_id"]
    assert approval_events[1].metadata["approval_id"] == response.json()["approval_id"]
    assert "Acme" not in str(approval_events)


def test_connector_approval_endpoint_resolves_connector_request_without_runtime_state():
    identity, account = create_connector_operator_identity()
    store = InMemoryControlPlaneStore()
    registry = InMemoryConnectorRegistry()
    connector = registered_connector(registry, approval_required=True)
    run = create_run(store, account.id)
    client = TestClient(
        create_app(
            identity_service=identity,
            store=store,
            connector_registry=registry,
            settings=Settings(_env_file=None),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}
    pending = client.post(
        f"/api/connectors/{connector.id}/invoke",
        headers=headers,
        json={
            "run_id": run.id,
            "step_id": "step_crm",
            "capability_name": "search_accounts",
            "tool_input": {"account_name": "Acme"},
            "granted_scopes": ["crm.accounts.read"],
        },
    )

    resolved = client.post(
        f"/api/runs/{run.id}/approvals",
        headers=headers,
        json={"approval_id": pending.json()["approval_id"]},
    )

    assert resolved.status_code == 200
    assert resolved.json() == {
        "run_id": run.id,
        "approval_id": pending.json()["approval_id"],
        "status": "approved",
    }
    approvals = store.list_approval_requests("tenant_acme", run.id)
    assert approvals[0].status == ApprovalStatus.APPROVED


def test_connector_invoke_api_requires_resolved_approval_before_approved_dispatch():
    identity, account = create_connector_operator_identity()
    store = InMemoryControlPlaneStore()
    registry = InMemoryConnectorRegistry()
    connector = registered_internal_api_connector(registry, approval_required=True)
    run = create_run(store, account.id)
    http_client = LocalConnectorHttpClient(
        ConnectorHttpResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=b'{"id":"acct_42"}',
        )
    )
    client = TestClient(
        create_app(
            identity_service=identity,
            store=store,
            connector_registry=registry,
            connector_dispatcher=ConnectorDispatchService(http_client=http_client),
            settings=Settings(_env_file=None),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}
    payload = {
        "run_id": run.id,
        "step_id": "step_crm",
        "capability_name": "search_accounts",
        "tool_input": {"method": "GET", "path": "/accounts/42"},
        "granted_scopes": ["crm.accounts.read"],
    }
    pending = client.post(
        f"/api/connectors/{connector.id}/invoke",
        headers=headers,
        json=payload,
    )
    unbound = client.post(
        f"/api/connectors/{connector.id}/invoke",
        headers=headers,
        json=payload | {"approved": True},
    )
    unresolved = client.post(
        f"/api/connectors/{connector.id}/invoke",
        headers=headers,
        json=payload | {"approved": True, "approval_id": pending.json()["approval_id"]},
    )

    assert pending.status_code == 202
    assert pending.json()["status"] == "approval_required"
    assert unbound.status_code == 403
    assert unresolved.status_code == 403
    assert http_client.requests == []

    resolved = client.post(
        f"/api/runs/{run.id}/approvals",
        headers=headers,
        json={"approval_id": pending.json()["approval_id"]},
    )
    approved = client.post(
        f"/api/connectors/{connector.id}/invoke",
        headers=headers,
        json=payload | {"approved": True, "approval_id": pending.json()["approval_id"]},
    )

    assert resolved.status_code == 200
    assert approved.status_code == 202
    assert approved.json()["status"] == "ready"
    assert approved.json()["approval_id"] == pending.json()["approval_id"]
    assert approved.json()["output"]["body"] == {"id": "acct_42"}
    assert len(http_client.requests) == 1
    assert (
        store.list_approval_requests("tenant_acme", run.id)[0].execution_status
        == "applied"
    )

    invoked_events = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "connector.invoked"
    ]
    assert len(invoked_events) == 1
    assert invoked_events[0].metadata["approval_id"] == pending.json()["approval_id"]
    assert "acct_42" not in str(invoked_events[0].metadata)
    meters = [
        meter
        for meter in store.list_billing_meters("tenant_acme")
        if meter.meter_type == "connector_invocation_count"
    ]
    assert len(meters) == 1


def test_connector_rejection_endpoint_rejects_connector_request_without_runtime_state():
    identity, account = create_connector_operator_identity()
    store = InMemoryControlPlaneStore()
    registry = InMemoryConnectorRegistry()
    connector = registered_internal_api_connector(registry, approval_required=True)
    run = create_run(store, account.id)
    http_client = LocalConnectorHttpClient(
        ConnectorHttpResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=b'{"id":"acct_42"}',
        )
    )
    client = TestClient(
        create_app(
            identity_service=identity,
            store=store,
            connector_registry=registry,
            connector_dispatcher=ConnectorDispatchService(http_client=http_client),
            settings=Settings(_env_file=None),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}
    payload = {
        "run_id": run.id,
        "step_id": "step_crm",
        "capability_name": "search_accounts",
        "tool_input": {"method": "GET", "path": "/accounts/42"},
        "granted_scopes": ["crm.accounts.read"],
    }
    pending = client.post(
        f"/api/connectors/{connector.id}/invoke",
        headers=headers,
        json=payload,
    )

    rejected = client.post(
        f"/api/runs/{run.id}/approvals/reject",
        headers=headers,
        json={"approval_id": pending.json()["approval_id"]},
    )
    approved = client.post(
        f"/api/connectors/{connector.id}/invoke",
        headers=headers,
        json=payload | {"approved": True, "approval_id": pending.json()["approval_id"]},
    )

    assert rejected.status_code == 200
    assert rejected.json() == {
        "run_id": run.id,
        "approval_id": pending.json()["approval_id"],
        "status": "rejected",
    }
    approvals = store.list_approval_requests("tenant_acme", run.id)
    assert approvals[0].status == ApprovalStatus.REJECTED
    assert approved.status_code == 403
    assert http_client.requests == []
    rejection_events = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "approval.rejected"
    ]
    assert len(rejection_events) == 1
    assert rejection_events[0].metadata["approval_id"] == pending.json()["approval_id"]


def test_connector_invoke_api_denies_missing_scope_without_raw_input_in_audit():
    identity, account = create_connector_operator_identity()
    store = InMemoryControlPlaneStore()
    registry = InMemoryConnectorRegistry()
    connector = registered_connector(registry)
    run = create_run(store, account.id)
    client = TestClient(
        create_app(
            identity_service=identity,
            store=store,
            connector_registry=registry,
            settings=Settings(_env_file=None),
        )
    )

    response = client.post(
        f"/api/connectors/{connector.id}/invoke",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id},
        json={
            "run_id": run.id,
            "step_id": "step_crm",
            "capability_name": "search_accounts",
            "tool_input": {"account_name": "Acme"},
            "granted_scopes": [],
        },
    )

    assert response.status_code == 403
    events = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "connector.invocation_denied"
    ]
    assert len(events) == 1
    assert events[0].metadata["missing_scopes"] == ["crm.accounts.read"]
    assert events[0].metadata["input_keys"] == ["account_name"]
    assert "Acme" not in str(events[0].metadata)


def test_thread_and_agent_action_manifest_approve_apply_is_idempotent():
    identity, account = create_connector_operator_identity()
    store = InMemoryControlPlaneStore()
    registry = InMemoryConnectorRegistry()
    connector = registered_internal_api_connector(registry, approval_required=True)
    http_client = LocalConnectorHttpClient(
        ConnectorHttpResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=b'{"id":"acct_42"}',
        )
    )
    client = TestClient(
        create_app(
            identity_service=identity,
            store=store,
            connector_registry=registry,
            connector_dispatcher=ConnectorDispatchService(http_client=http_client),
            settings=Settings(_env_file=None),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}
    thread = store.create_chat_thread(
        "tenant_acme",
        account.id,
        ChatThreadCreate(
            workspace_id="workspace_sales",
            title="Review CRM action",
        ),
    )
    agent = client.post(
        "/api/agents",
        headers=headers,
        json={
            "workspace_id": "workspace_sales",
            "name": "CRM agent",
            "version": {"instructions": "Review and run the requested CRM action."},
        },
    ).json()["agent"]
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id=account.id,
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id=agent["id"],
            thread_id=thread.id,
            message="Look up account context",
        ),
    )
    invoke = client.post(
        f"/api/connectors/{connector.id}/invoke",
        headers=headers,
        json={
            "run_id": run.id,
            "step_id": "step_crm",
            "capability_name": "search_accounts",
            "tool_input": {"method": "GET", "path": "/accounts/42"},
            "granted_scopes": ["crm.accounts.read"],
        },
    )

    assert invoke.status_code == 202
    manifest_id = invoke.json()["approval_id"]
    thread_items = client.get(
        f"/api/threads/{thread.id}/action-manifests",
        headers=headers,
    ).json()
    agent_items = client.get(
        f"/api/agentapps/{agent['id']}/action-manifests",
        headers=headers,
    ).json()
    assert len(thread_items) == 1
    assert agent_items == {"items": thread_items, "nextCursor": None}
    assert {"createdAt", "resolvedAt", "error"} <= set(thread_items[0])
    assert thread_items[0]["manifestId"] == manifest_id
    assert thread_items[0]["provider"] == "Sales CRM API"
    assert thread_items[0]["status"] == "approval_required"
    assert thread_items[0]["approvalStatus"] == "approval_required"
    assert thread_items[0]["preview"]["input"] == {
        "method": "GET",
        "path": "/accounts/42",
    }
    assert http_client.requests == []

    approved = client.post(
        f"/api/threads/{thread.id}/action-manifests/{manifest_id}/approve",
        headers=headers,
    )
    applied = client.post(
        f"/api/threads/{thread.id}/action-manifests/{manifest_id}/apply",
        headers=headers,
    )
    replay = client.post(
        f"/api/threads/{thread.id}/action-manifests/{manifest_id}/apply",
        headers=headers,
    )

    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert applied.status_code == 200
    assert applied.json()["status"] == "applied"
    assert applied.json()["result"]["body"] == {"id": "acct_42"}
    assert replay.status_code == 200
    assert replay.json()["status"] == "applied"
    assert "result" not in replay.json()
    assert len(http_client.requests) == 1
    assert [
        event.payload["status"]
        for event in store.list_run_events("tenant_acme", run.id)
        if event.type == "action_approval"
    ] == ["approval_required", "approved", "applying", "applied"]


def test_runtime_connector_manifest_apply_resumes_agent_once():
    identity, account = create_connector_operator_identity()
    store = InMemoryControlPlaneStore()
    registry = InMemoryConnectorRegistry()
    connector = registered_internal_api_connector(registry, approval_required=True)
    http_client = LocalConnectorHttpClient(
        ConnectorHttpResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=b'{"id":"acct_42"}',
        )
    )
    dispatcher = ConnectorDispatchService(http_client=http_client)
    thread = store.create_chat_thread(
        "tenant_acme",
        account.id,
        ChatThreadCreate(workspace_id="workspace_sales", title="Run CRM action"),
    )
    trigger = store.append_chat_message(
        "tenant_acme",
        thread.id,
        account.id,
        ChatMessageCreate(content="Look up account 42"),
    )
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id=account.id,
        payload=RunCreate(
            workspace_id="workspace_sales",
            thread_id=thread.id,
            trigger_message_id=trigger.id,
            message=trigger.content,
            mode=RunMode.AUTONOMOUS,
        ),
    )
    runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(
            plan=[
                PlannedToolCall(
                    id="step_crm",
                    title="Look up account",
                    tool_name=f"connector.{connector.id}.search_accounts",
                    tool_input={"method": "GET", "path": "/accounts/42"},
                ),
                PlannedToolCall(
                    id="step_crm_2",
                    title="Look up another account",
                    tool_name=f"connector.{connector.id}.search_accounts",
                    tool_input={"method": "GET", "path": "/accounts/43"},
                ),
            ]
        ),
        connector_registry=registry,
        connector_dispatcher=dispatcher,
        connector_invocation_service=ConnectorInvocationService(),
        full_auto_requires_isolation=False,
    )
    client = TestClient(
        create_app(
            identity_service=identity,
            store=store,
            runtime=runtime,
            connector_registry=registry,
            connector_dispatcher=dispatcher,
            settings=Settings(
                _env_file=None,
                agent_loop_full_auto_requires_isolation=False,
            ),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}

    paused = runtime.execute_run("tenant_acme", run.id)
    manifest = client.get(
        f"/api/threads/{thread.id}/action-manifests",
        headers=headers,
    ).json()[0]

    assert paused.status == RunStatus.AWAITING_APPROVAL
    assert manifest["provider"] == "Sales CRM API"
    assert manifest["preview"] == {
        "connectorId": connector.id,
        "capability": "search_accounts",
        "riskLevel": "medium",
        "inputKeys": ["method", "path"],
        "input": {"method": "GET", "path": "/accounts/42"},
    }

    base = f"/api/threads/{thread.id}/action-manifests/{manifest['manifestId']}"
    approved = client.post(f"{base}/approve", headers=headers)
    applied = client.post(f"{base}/apply", headers=headers)
    replay = client.post(f"{base}/apply", headers=headers)

    assert approved.status_code == 200
    assert applied.status_code == 200
    assert applied.json()["status"] == "applied"
    assert replay.status_code == 200
    assert len(http_client.requests) == 1

    second = client.get(
        f"/api/threads/{thread.id}/action-manifests",
        headers=headers,
    ).json()[0]
    assert second["manifestId"] != manifest["manifestId"]
    assert second["status"] == "approval_required"
    assert second["preview"]["input"]["path"] == "/accounts/43"

    second_base = (
        f"/api/threads/{thread.id}/action-manifests/{second['manifestId']}"
    )
    assert client.post(f"{second_base}/approve", headers=headers).status_code == 200
    assert client.post(f"{second_base}/apply", headers=headers).status_code == 200
    assert store.get_run("tenant_acme", run.id).status == RunStatus.SUCCEEDED
    assert len(http_client.requests) == 2


def test_runtime_connector_manifest_reject_terminates_agent():
    identity, account = create_connector_operator_identity()
    store = InMemoryControlPlaneStore()
    registry = InMemoryConnectorRegistry()
    connector = registered_internal_api_connector(registry, approval_required=True)
    http_client = LocalConnectorHttpClient(
        ConnectorHttpResponse(status_code=200, body=b'{"ok":true}')
    )
    dispatcher = ConnectorDispatchService(http_client=http_client)
    thread = store.create_chat_thread(
        "tenant_acme",
        account.id,
        ChatThreadCreate(workspace_id="workspace_sales", title="Reject CRM action"),
    )
    trigger = store.append_chat_message(
        "tenant_acme",
        thread.id,
        account.id,
        ChatMessageCreate(content="Do not run this account lookup"),
    )
    run = store.create_run(
        tenant_id="tenant_acme",
        user_id=account.id,
        payload=RunCreate(
            workspace_id="workspace_sales",
            thread_id=thread.id,
            trigger_message_id=trigger.id,
            message=trigger.content,
            mode=RunMode.AUTONOMOUS,
        ),
    )
    runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(
            plan=[
                PlannedToolCall(
                    id="step_crm",
                    title="Look up account",
                    tool_name=f"connector.{connector.id}.search_accounts",
                    tool_input={"method": "GET", "path": "/accounts/42"},
                )
            ]
        ),
        connector_registry=registry,
        connector_dispatcher=dispatcher,
        connector_invocation_service=ConnectorInvocationService(),
    )
    client = TestClient(
        create_app(
            identity_service=identity,
            store=store,
            runtime=runtime,
            connector_registry=registry,
            connector_dispatcher=dispatcher,
            settings=Settings(
                _env_file=None,
                agent_loop_full_auto_requires_isolation=False,
            ),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}

    assert runtime.execute_run("tenant_acme", run.id).status == RunStatus.AWAITING_APPROVAL
    manifest = client.get(
        f"/api/threads/{thread.id}/action-manifests",
        headers=headers,
    ).json()[0]
    rejected = client.post(
        f"/api/threads/{thread.id}/action-manifests/{manifest['manifestId']}/reject",
        headers=headers,
    )

    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert store.get_run("tenant_acme", run.id).status == RunStatus.CANCELLED
    assert http_client.requests == []
    assert [
        event.type for event in store.list_run_events("tenant_acme", run.id)
    ][-2:] == ["agent.loop.completed", "action_approval"]


def test_connector_preflight_fails_before_action_manifest_creation():
    identity, account = create_connector_operator_identity()
    store = InMemoryControlPlaneStore()
    registry = InMemoryConnectorRegistry()
    connector = registry.register_connector(
        ConnectorDefinitionCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            type=ConnectorType.INTERNAL_API,
            display_name="Disconnected CRM",
            owner_user_id=account.id,
            auth_mode=ConnectorAuthMode.NONE,
            status=ConnectorStatus.ENABLED,
            capabilities=[
                ConnectorCapability(
                    name="create_note",
                    approval_required=True,
                    input_schema={"type": "object"},
                )
            ],
        )
    )
    run = create_run(store, account.id)
    client = TestClient(
        create_app(
            identity_service=identity,
            store=store,
            connector_registry=registry,
            settings=Settings(_env_file=None),
        )
    )
    response = client.post(
        f"/api/connectors/{connector.id}/invoke",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id},
        json={
            "run_id": run.id,
            "step_id": "step_note",
            "capability_name": "create_note",
            "tool_input": {"path": "/notes", "json": {"text": "hello"}},
            "granted_scopes": [],
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "connector_dispatch_failed"
    assert store.list_approval_requests("tenant_acme", run.id) == []
    assert all(
        event.type != "action_approval"
        for event in store.list_run_events("tenant_acme", run.id)
    )
