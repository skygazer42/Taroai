from fastapi.testclient import TestClient

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
from taroai.domain import ApprovalStatus, RunCreate
from taroai.identity import (
    InMemoryIdentityService,
    PasswordHasher,
    Permission,
    Role,
    UserAccountCreate,
)
from taroai.store import InMemoryControlPlaneStore


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
                    "allowed_paths": ["/accounts/*"],
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
                        "properties": {"path": {"type": "string"}},
                    },
                )
            ],
        )
    )


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
    assert approvals[0].status == ApprovalStatus.PENDING
    assert approvals[0].reason == (
        f"connector approval required: {connector.id}:search_accounts"
    )
    assert "Acme" not in str(approvals[0].model_dump(mode="json"))
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
