from datetime import datetime, timezone

from fastapi.testclient import TestClient

from taroai.app import create_app
from taroai.config import Settings
from taroai.connectors import ConnectorDispatchService
from taroai.identity import (
    InMemoryIdentityService,
    PasswordHasher,
    Permission,
    Role,
    UserAccountCreate,
)
from taroai.licensing import Entitlement, LicenseKey, LicenseService, LicensedFeature
from taroai.store import InMemoryControlPlaneStore


def create_connector_operator_identity(can_manage: bool = True, can_read: bool = True):
    identity = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="connector-admin@example.com",
            display_name="Connector Admin",
            password="correct horse battery staple",
        )
    )
    permissions = []
    if can_manage:
        permissions.append(Permission(action="connectors.manage", resource="tenant:tenant_acme"))
    if can_read:
        permissions.append(Permission(action="connectors.read", resource="tenant:tenant_acme"))
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_connector_admin",
            name="Connector Admin",
            permissions=permissions,
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_connector_admin")
    return identity, account


class FakeMcpClient:
    def __init__(self):
        self.calls = []

    def list_tools(self, config, headers):
        assert config.url == "https://mcp.example.test/mcp"
        assert headers == {}
        return [
            {
                "name": "get_weather",
                "description": "Get current weather",
                "inputSchema": {
                    "type": "object",
                    "required": ["city"],
                    "properties": {"city": {"type": "string"}},
                },
                "annotations": {"readOnlyHint": True},
            },
            {
                "name": "delete_station",
                "inputSchema": {"type": "object"},
                "annotations": {"destructiveHint": True},
            },
        ]

    def call_tool(self, config, name, arguments, headers):
        self.calls.append((config.url, name, arguments, headers))
        return {
            "content": [{"type": "text", "text": "Sunny"}],
            "structuredContent": {"temperature_c": 26},
            "isError": False,
        }


def test_mcp_connector_discovers_tools_on_enable_and_dispatches_calls():
    identity, account = create_connector_operator_identity()
    fake_mcp = FakeMcpClient()
    client = TestClient(
        create_app(
            identity_service=identity,
            connector_dispatcher=ConnectorDispatchService(mcp_client=fake_mcp),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}
    created = client.post(
        "/api/connectors",
        headers=headers,
        json={
            "workspace_id": "workspace_sales",
            "type": "mcp_server",
            "display_name": "Weather MCP",
            "auth_mode": "none",
            "metadata": {"mcp": {"url": "https://mcp.example.test/mcp"}},
        },
    )

    enabled = client.post(
        f"/api/connectors/{created.json()['id']}/enable",
        headers=headers,
    )

    assert enabled.status_code == 200
    capabilities = {item["name"]: item for item in enabled.json()["capabilities"]}
    assert capabilities["get_weather"]["risk_level"] == "low"
    assert capabilities["get_weather"]["approval_required"] is False
    assert capabilities["get_weather"]["description"] == "Get current weather"
    assert capabilities["delete_station"]["risk_level"] == "high"
    assert capabilities["delete_station"]["approval_required"] is True

    connector = client.app.state.connector_registry.get_connector(
        "tenant_acme",
        created.json()["id"],
    )
    result = client.app.state.connector_dispatcher.dispatch(
        connector,
        {"city": "Beijing"},
        f"connector.{connector.id}.get_weather",
    )

    assert result.output["structured_content"] == {"temperature_c": 26}
    assert fake_mcp.calls == [
        (
            "https://mcp.example.test/mcp",
            "get_weather",
            {"city": "Beijing"},
            {},
        )
    ]


def test_connector_api_creates_lists_and_reads_tenant_scoped_connector():
    identity, account = create_connector_operator_identity()
    store = InMemoryControlPlaneStore()
    client = TestClient(create_app(identity_service=identity, store=store))
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}

    created = client.post(
        "/api/connectors",
        headers=headers,
        json={
            "workspace_id": "workspace_sales",
            "type": "saas",
            "display_name": "Sales CRM",
            "auth_mode": "api_key",
            "credential": {
                "secret_ref_id": "secret_crm_api_key",
                "required_actions": ["connector.read"],
                "raw_token": "api-key-value",
            },
            "capabilities": [
                {
                    "name": "search_accounts",
                    "required_scopes": ["crm.accounts.read"],
                    "risk_level": "medium",
                }
            ],
            "sensitivity_level": 2,
        },
    )

    assert created.status_code == 422

    created = client.post(
        "/api/connectors",
        headers=headers,
        json={
            "workspace_id": "workspace_sales",
            "type": "saas",
            "display_name": "Sales CRM",
            "auth_mode": "api_key",
            "credential": {
                "secret_ref_id": "secret_crm_api_key",
                "required_actions": ["connector.read"],
            },
            "capabilities": [
                {
                    "name": "search_accounts",
                    "required_scopes": ["crm.accounts.read"],
                    "risk_level": "medium",
                }
            ],
            "sensitivity_level": 2,
        },
    )

    assert created.status_code == 201
    created_body = created.json()
    assert created_body["tenant_id"] == "tenant_acme"
    assert created_body["workspace_id"] == "workspace_sales"
    assert created_body["owner_user_id"] == account.id
    assert created_body["credential_ref"]["tenant_id"] == "tenant_acme"
    assert created_body["credential_ref"]["workspace_id"] == "workspace_sales"
    assert created_body["credential_ref"]["secret_ref_id"] == "secret_crm_api_key"
    assert "api-key-value" not in str(created_body)

    listed = client.get("/api/connectors", headers=headers)
    fetched = client.get(f"/api/connectors/{created_body['id']}", headers=headers)

    assert listed.status_code == 200
    assert [connector["id"] for connector in listed.json()] == [created_body["id"]]
    assert fetched.status_code == 200
    assert fetched.json()["id"] == created_body["id"]

    connector_events = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "connector.registered"
    ]
    assert len(connector_events) == 1
    assert connector_events[0].metadata["connector_id"] == created_body["id"]
    assert connector_events[0].metadata["credential_ref_id"] == "secret_crm_api_key"
    assert "api-key-value" not in str(connector_events)


def test_connector_api_enforces_private_connector_license_limit():
    identity, account = create_connector_operator_identity()
    store = InMemoryControlPlaneStore()
    license_service = LicenseService(runtime_enforcement_enabled=True)
    validation = license_service.validate_license(
        LicenseKey(
            id="license_acme_private",
            tenant_id="tenant_acme",
            customer_name="Acme Inc",
            issued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
            deployment_modes=["private"],
            entitlements=[
                Entitlement(feature=LicensedFeature.PRIVATE_CONNECTOR_COUNT, limit=1),
                Entitlement(feature=LicensedFeature.AUDIT_RETENTION_DAYS, limit=365),
            ],
        ),
        deployment_mode="private",
        now=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    license_service.activate_validation(validation)
    client = TestClient(
        create_app(
            identity_service=identity,
            store=store,
            license_service=license_service,
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}
    payload = {
        "workspace_id": "workspace_sales",
        "type": "saas",
        "display_name": "Sales CRM",
        "auth_mode": "none",
        "capabilities": [{"name": "search_accounts"}],
    }

    first = client.post("/api/connectors", headers=headers, json=payload)
    second = client.post(
        "/api/connectors",
        headers=headers,
        json=payload | {"display_name": "Support CRM"},
    )

    assert first.status_code == 201
    assert second.status_code == 403
    assert second.json()["code"] == "license_entitlement_denied"
    assert "private_connector_count" in second.json()["message"]
    assert len(client.get("/api/connectors", headers=headers).json()) == 1


def test_connector_api_updates_enables_and_disables_connector_with_safe_audit():
    identity, account = create_connector_operator_identity()
    store = InMemoryControlPlaneStore()
    client = TestClient(create_app(identity_service=identity, store=store))
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}
    created = client.post(
        "/api/connectors",
        headers=headers,
        json={
            "workspace_id": "workspace_sales",
            "type": "saas",
            "display_name": "Sales CRM",
            "auth_mode": "none",
            "status": "draft",
            "capabilities": [{"name": "search_accounts"}],
            "sensitivity_level": 1,
            "metadata": {"provider": "crm"},
        },
    )
    connector_id = created.json()["id"]

    updated = client.patch(
        f"/api/connectors/{connector_id}",
        headers=headers,
        json={
            "display_name": "Enterprise CRM",
            "sensitivity_level": 3,
            "metadata": {"provider": "crm", "tier": "enterprise"},
            "credential": {"secret_ref_id": "secret_should_not_be_allowed"},
        },
    )
    clean_update = client.patch(
        f"/api/connectors/{connector_id}",
        headers=headers,
        json={
            "display_name": "Enterprise CRM",
            "sensitivity_level": 3,
            "metadata": {"provider": "crm", "tier": "enterprise"},
            "capabilities": [
                {
                    "name": "search_accounts",
                    "required_scopes": ["crm.accounts.read"],
                    "risk_level": "medium",
                }
            ],
        },
    )
    enabled = client.post(f"/api/connectors/{connector_id}/enable", headers=headers)
    disabled = client.post(f"/api/connectors/{connector_id}/disable", headers=headers)

    assert updated.status_code == 422
    assert clean_update.status_code == 200
    assert clean_update.json()["display_name"] == "Enterprise CRM"
    assert clean_update.json()["sensitivity_level"] == 3
    assert clean_update.json()["metadata"] == {"provider": "crm", "tier": "enterprise"}
    assert clean_update.json()["capabilities"][0]["required_scopes"] == [
        "crm.accounts.read"
    ]
    assert enabled.status_code == 200
    assert enabled.json()["status"] == "enabled"
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"

    events = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type
        in {"connector.updated", "connector.enabled", "connector.disabled"}
    ]
    assert [event.event_type for event in events] == [
        "connector.updated",
        "connector.enabled",
        "connector.disabled",
    ]
    assert all(event.metadata["connector_id"] == connector_id for event in events)
    assert "secret_should_not_be_allowed" not in str(events)


def test_connector_api_requires_read_permission():
    identity, account = create_connector_operator_identity(can_manage=False, can_read=False)
    client = TestClient(create_app(identity_service=identity))

    response = client.get(
        "/api/connectors",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "tenant_access_denied"


def test_connector_api_requires_manage_permission_for_updates():
    identity, admin = create_connector_operator_identity(can_manage=True, can_read=True)
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="connector-reader@example.com",
            display_name="Connector Reader",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_connector_reader",
            name="Connector Reader",
            permissions=[
                Permission(action="connectors.read", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_connector_reader")
    client = TestClient(create_app(identity_service=identity))
    created = client.post(
        "/api/connectors",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id},
        json={
            "workspace_id": "workspace_sales",
            "type": "saas",
            "display_name": "Sales CRM",
            "auth_mode": "none",
        },
    )
    connector_id = created.json()["id"]
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}

    update_response = client.patch(
        f"/api/connectors/{connector_id}",
        headers=headers,
        json={"display_name": "Denied CRM"},
    )
    enable_response = client.post(f"/api/connectors/{connector_id}/enable", headers=headers)
    disable_response = client.post(f"/api/connectors/{connector_id}/disable", headers=headers)

    assert update_response.status_code == 403
    assert enable_response.status_code == 403
    assert disable_response.status_code == 403


def test_connector_api_uses_sql_registry_from_settings(tmp_path):
    identity, account = create_connector_operator_identity()
    database_url = f"sqlite:///{tmp_path / 'connectors-api.sqlite3'}"
    settings = Settings(
        database_url=database_url,
        connector_registry_backend="sql",
        _env_file=None,
    )
    first_client = TestClient(create_app(identity_service=identity, settings=settings))
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}

    created = first_client.post(
        "/api/connectors",
        headers=headers,
        json={
            "workspace_id": "workspace_sales",
            "type": "saas",
            "display_name": "Sales CRM",
            "auth_mode": "api_key",
            "credential": {"secret_ref_id": "secret_crm_api_key"},
            "capabilities": [{"name": "search_accounts"}],
        },
    )

    assert created.status_code == 201
    second_client = TestClient(create_app(identity_service=identity, settings=settings))
    listed = second_client.get("/api/connectors", headers=headers)

    assert listed.status_code == 200
    assert [connector["id"] for connector in listed.json()] == [created.json()["id"]]

    updated = second_client.patch(
        f"/api/connectors/{created.json()['id']}",
        headers=headers,
        json={"display_name": "Enterprise CRM", "sensitivity_level": 4},
    )
    third_client = TestClient(create_app(identity_service=identity, settings=settings))
    fetched = third_client.get(
        f"/api/connectors/{created.json()['id']}",
        headers=headers,
    )

    assert updated.status_code == 200
    assert fetched.json()["display_name"] == "Enterprise CRM"
    assert fetched.json()["sensitivity_level"] == 4
