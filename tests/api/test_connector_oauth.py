from fastapi.testclient import TestClient

from taroai.app import create_app
from taroai.config import Settings
from taroai.connectors import (
    ConnectorAuthMode,
    ConnectorCapability,
    ConnectorCredentialRef,
    ConnectorDefinitionCreate,
    ConnectorStatus,
    ConnectorType,
    InMemoryConnectorRegistry,
)
from taroai.identity import (
    InMemoryIdentityService,
    PasswordHasher,
    Permission,
    Role,
    UserAccountCreate,
)
from taroai.secrets import InMemorySecretService, SecretScope


class LocalOAuthTokenClient:
    def __init__(self):
        self.calls = []

    def exchange_code(self, request):
        self.calls.append(("exchange_code", request))
        return {
            "access_token": "new-access-token-value",
            "refresh_token": "new-refresh-token-value",
            "expires_in": 3600,
            "token_type": "Bearer",
        }

    def refresh(self, request):
        self.calls.append(("refresh", request))
        return {
            "access_token": "rotated-access-token-value",
            "refresh_token": "rotated-refresh-token-value",
            "expires_in": 3600,
            "token_type": "Bearer",
        }


def create_connector_oauth_identity():
    identity = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="oauth-admin@example.com",
            display_name="OAuth Admin",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_oauth_admin",
            name="OAuth Admin",
            permissions=[
                Permission(action="connectors.manage", resource="tenant:tenant_acme"),
                Permission(action="connectors.read", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_oauth_admin")
    return identity, account


def create_oauth_secrets():
    secret_service = InMemorySecretService()
    client_id = secret_service.create_secret(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        name="crm-client-id",
        value="client-id-value",
        scope=SecretScope(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            actions=["connector.oauth2.client_id"],
        ),
    )
    client_secret = secret_service.create_secret(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        name="crm-client-secret",
        value="client-secret-value",
        scope=SecretScope(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            actions=["connector.oauth2.client_secret"],
        ),
    )
    access_token = secret_service.create_secret(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        name="crm-access-token",
        value="old-access-token-value",
        scope=SecretScope(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            actions=["connector.oauth2.access"],
        ),
    )
    refresh_token = secret_service.create_secret(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        name="crm-refresh-token",
        value="old-refresh-token-value",
        scope=SecretScope(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            actions=["connector.oauth2.refresh"],
        ),
    )
    return secret_service, client_id, client_secret, access_token, refresh_token


def register_oauth_connector(registry, client_id, client_secret, access_token, refresh_token):
    return registry.register_connector(
        ConnectorDefinitionCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            type=ConnectorType.SAAS,
            display_name="CRM OAuth",
            owner_user_id="user_admin",
            auth_mode=ConnectorAuthMode.OAUTH2,
            credential_ref=ConnectorCredentialRef(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                secret_ref_id=access_token.id,
                required_actions=["connector.oauth2.access"],
            ),
            status=ConnectorStatus.ENABLED,
            metadata={
                "oauth2": {
                    "authorize_url": "https://crm.example.com/oauth/authorize",
                    "token_url": "https://crm.example.com/oauth/token",
                    "callback_url": "https://agent.example.com/api/connectors/oauth/callback",
                    "scopes": ["crm.accounts.read", "crm.contacts.read"],
                    "client_id_secret_ref_id": client_id.id,
                    "client_secret_secret_ref_id": client_secret.id,
                    "access_token_secret_ref_id": access_token.id,
                    "refresh_token_secret_ref_id": refresh_token.id,
                }
            },
            capabilities=[ConnectorCapability(name="search_accounts")],
        )
    )


def create_oauth_client():
    identity, account = create_connector_oauth_identity()
    registry = InMemoryConnectorRegistry()
    secret_service, client_id, client_secret, access_token, refresh_token = create_oauth_secrets()
    connector = register_oauth_connector(
        registry,
        client_id,
        client_secret,
        access_token,
        refresh_token,
    )
    token_client = LocalOAuthTokenClient()
    app = create_app(
        identity_service=identity,
        connector_registry=registry,
        secret_service=secret_service,
        settings=Settings(_env_file=None),
    )
    app.state.connector_oauth_service = app.state.connector_oauth_service.model_copy(
        update={"token_client": token_client}
    )
    client = TestClient(app)
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}
    return client, headers, connector, secret_service, token_client


def resolve_secret(secret_service, secret_id, action):
    lease = secret_service.create_lease(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        secret_id=secret_id,
        tool_name="connector.oauth2.verify",
        actions=[action],
        ttl_seconds=60,
    )
    return secret_service.resolve_lease_value(
        tenant_id="tenant_acme",
        lease_token=lease.lease_token,
    )


def test_connector_oauth_authorize_returns_stateful_url_without_secret_values():
    client, headers, connector, secret_service, _token_client = create_oauth_client()

    response = client.post(
        f"/api/connectors/{connector.id}/oauth/authorize",
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["connector_id"] == connector.id
    assert body["state"]
    assert body["authorization_url"].startswith("https://crm.example.com/oauth/authorize?")
    assert "client_id=client-id-value" in body["authorization_url"]
    assert "scope=crm.accounts.read+crm.contacts.read" in body["authorization_url"]
    assert "client-secret-value" not in str(body)
    assert "old-access-token-value" not in str(body)
    assert "old-refresh-token-value" not in str(body)

    events = [
        event
        for event in client.app.state.store.list_audit_events("tenant_acme")
        if event.event_type == "connector.oauth_authorization_started"
    ]
    assert len(events) == 1
    assert events[0].metadata["connector_id"] == connector.id
    assert "client-id-value" not in str(events[0].metadata)
    assert "client-secret-value" not in str(events[0].metadata)
    assert "old-access-token-value" not in str(secret_service.model_dump(mode="json"))


def test_connector_oauth_callback_rotates_token_secret_values_without_audit_leak():
    client, headers, connector, secret_service, token_client = create_oauth_client()
    authorize = client.post(
        f"/api/connectors/{connector.id}/oauth/authorize",
        headers=headers,
    ).json()

    response = client.post(
        f"/api/connectors/{connector.id}/oauth/callback",
        headers=headers,
        json={
            "code": "provider-code-value",
            "state": authorize["state"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["access_token_secret_ref_id"] == connector.credential_ref.secret_ref_id
    assert "new-access-token-value" not in str(body)
    assert "new-refresh-token-value" not in str(body)
    assert token_client.calls[0][0] == "exchange_code"
    assert token_client.calls[0][1].code == "provider-code-value"
    assert token_client.calls[0][1].client_secret == "client-secret-value"
    assert resolve_secret(
        secret_service,
        connector.credential_ref.secret_ref_id,
        "connector.oauth2.access",
    ) == "new-access-token-value"

    events = [
        event
        for event in client.app.state.store.list_audit_events("tenant_acme")
        if event.event_type == "connector.oauth_completed"
    ]
    assert len(events) == 1
    assert events[0].metadata["connector_id"] == connector.id
    assert "provider-code-value" not in str(events[0].metadata)
    assert "new-access-token-value" not in str(events[0].metadata)
    assert "new-refresh-token-value" not in str(events[0].metadata)
    assert "client-secret-value" not in str(events[0].metadata)


def test_connector_oauth_refresh_rotates_tokens_without_returning_values():
    client, headers, connector, secret_service, token_client = create_oauth_client()

    response = client.post(
        f"/api/connectors/{connector.id}/oauth/refresh",
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "refreshed"
    assert "rotated-access-token-value" not in str(body)
    assert "rotated-refresh-token-value" not in str(body)
    assert token_client.calls[0][0] == "refresh"
    assert token_client.calls[0][1].refresh_token == "old-refresh-token-value"
    assert resolve_secret(
        secret_service,
        connector.credential_ref.secret_ref_id,
        "connector.oauth2.access",
    ) == "rotated-access-token-value"

    events = [
        event
        for event in client.app.state.store.list_audit_events("tenant_acme")
        if event.event_type == "connector.oauth_refreshed"
    ]
    assert len(events) == 1
    assert events[0].metadata["connector_id"] == connector.id
    assert "old-refresh-token-value" not in str(events[0].metadata)
    assert "rotated-access-token-value" not in str(events[0].metadata)
