import sqlite3

import pytest
from fastapi.testclient import TestClient

from taroai.app import create_app
from taroai.config import Settings
from taroai.connectors import (
    ConnectorAuthMode,
    ConnectorCapability,
    ConnectorCredentialRef,
    ConnectorDefinitionCreate,
    ConnectorDispatchError,
    ConnectorDispatchService,
    ConnectorHttpResponse,
    ConnectorStatus,
    ConnectorType,
    InMemoryConnectorRegistry,
)
from taroai.domain import RunCreate
from taroai.identity import (
    InMemoryIdentityService,
    PasswordHasher,
    Permission,
    Role,
    UserAccountCreate,
)
from taroai.secrets import InMemorySecretService, SecretScope
from taroai.store import InMemoryControlPlaneStore


class LocalConnectorHttpClient:
    def __init__(self, response: ConnectorHttpResponse):
        self.response = response
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        return self.response


def create_connector_operator_identity():
    identity = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="connector-dispatch@example.com",
            display_name="Connector Dispatch",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_connector_dispatch",
            name="Connector Dispatch",
            permissions=[
                Permission(action="connectors.invoke", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_connector_dispatch")
    return identity, account


def create_run(store: InMemoryControlPlaneStore, user_id: str):
    return store.create_run(
        tenant_id="tenant_acme",
        user_id=user_id,
        payload=RunCreate(
            workspace_id="workspace_sales",
            agent_id="agent_sales",
            message="Fetch account details",
        ),
    )


def register_internal_api_connector(registry: InMemoryConnectorRegistry):
    return registry.register_connector(
        ConnectorDefinitionCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            type=ConnectorType.INTERNAL_API,
            display_name="Internal Accounts API",
            owner_user_id="user_admin",
            auth_mode=ConnectorAuthMode.NONE,
            status=ConnectorStatus.ENABLED,
            metadata={
                "internal_api": {
                    "base_url": "https://internal.example.com/api",
                    "allowed_methods": ["GET"],
                    "allowed_paths": ["/accounts/*"],
                    "timeout_seconds": 4,
                }
            },
            capabilities=[
                ConnectorCapability(
                    name="get_account",
                    required_scopes=["accounts.read"],
                    risk_level="low",
                    input_schema={
                        "type": "object",
                        "required": ["path"],
                        "properties": {"path": {"type": "string"}},
                    },
                )
            ],
        )
    )


def register_authenticated_internal_api_connector(
    registry: InMemoryConnectorRegistry,
    secret_id: str,
):
    return registry.register_connector(
        ConnectorDefinitionCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            type=ConnectorType.INTERNAL_API,
            display_name="Internal Accounts API",
            owner_user_id="user_admin",
            auth_mode=ConnectorAuthMode.API_KEY,
            credential_ref=ConnectorCredentialRef(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                secret_ref_id=secret_id,
                required_actions=["connector.read"],
            ),
            status=ConnectorStatus.ENABLED,
            metadata={
                "internal_api": {
                    "base_url": "https://internal.example.com/api",
                    "allowed_methods": ["GET"],
                    "allowed_paths": ["/accounts/*"],
                    "timeout_seconds": 4,
                    "auth": {
                        "mode": "api_key_header",
                        "header_name": "x-api-key",
                    },
                }
            },
            capabilities=[
                ConnectorCapability(
                    name="get_account",
                    required_scopes=["accounts.read"],
                    risk_level="low",
                    input_schema={
                        "type": "object",
                        "required": ["path"],
                        "properties": {"path": {"type": "string"}},
                    },
                )
            ],
        )
    )


def register_oauth_internal_api_connector(
    registry: InMemoryConnectorRegistry,
    secret_id: str,
):
    return registry.register_connector(
        ConnectorDefinitionCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            type=ConnectorType.INTERNAL_API,
            display_name="Internal Accounts API",
            owner_user_id="user_admin",
            auth_mode=ConnectorAuthMode.OAUTH2,
            credential_ref=ConnectorCredentialRef(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                secret_ref_id=secret_id,
                required_actions=["connector.oauth2.access"],
            ),
            status=ConnectorStatus.ENABLED,
            metadata={
                "internal_api": {
                    "base_url": "https://internal.example.com/api",
                    "allowed_methods": ["GET"],
                    "allowed_paths": ["/accounts/*"],
                    "timeout_seconds": 4,
                    "auth": {
                        "mode": "oauth2_bearer",
                    },
                }
            },
            capabilities=[
                ConnectorCapability(
                    name="get_account",
                    required_scopes=["accounts.read"],
                    risk_level="low",
                    input_schema={
                        "type": "object",
                        "required": ["path"],
                        "properties": {"path": {"type": "string"}},
                    },
                )
            ],
        )
    )


def register_database_connector(
    registry: InMemoryConnectorRegistry,
    secret_id: str,
):
    return registry.register_connector(
        ConnectorDefinitionCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            type=ConnectorType.DATABASE,
            display_name="Sales Database",
            owner_user_id="user_admin",
            auth_mode=ConnectorAuthMode.DATABASE_PASSWORD,
            credential_ref=ConnectorCredentialRef(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                secret_ref_id=secret_id,
                required_actions=["connector.database.read"],
            ),
            status=ConnectorStatus.ENABLED,
            metadata={
                "database": {
                    "allowed_tables": ["accounts"],
                    "read_only": True,
                    "max_rows": 10,
                    "timeout_seconds": 3,
                }
            },
            capabilities=[
                ConnectorCapability(
                    name="query_accounts",
                    required_scopes=["database.accounts.read"],
                    risk_level="medium",
                    input_schema={
                        "type": "object",
                        "required": ["sql"],
                        "properties": {
                            "sql": {"type": "string"},
                            "parameters": {"type": "array"},
                        },
                    },
                )
            ],
        )
    )


def create_database_secret_service(database_url: str):
    secret_service = InMemorySecretService()
    secret = secret_service.create_secret(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        name="sales-database-dsn",
        value=database_url,
        scope=SecretScope(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            actions=["connector.database.read"],
        ),
    )
    return secret_service, secret


def create_sales_database(path):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE accounts (id TEXT PRIMARY KEY, name TEXT NOT NULL)")
        connection.execute(
            "INSERT INTO accounts (id, name) VALUES (?, ?)",
            ("acct_42", "Acme"),
        )
        connection.execute(
            "INSERT INTO accounts (id, name) VALUES (?, ?)",
            ("acct_43", "Globex"),
        )


def create_connector_secret_service():
    secret_service = InMemorySecretService()
    secret = secret_service.create_secret(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        name="internal-api-key",
        value="internal-secret-key",
        scope=SecretScope(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            actions=["connector.read"],
        ),
    )
    return secret_service, secret


def create_connector_oauth_secret_service():
    secret_service = InMemorySecretService()
    secret = secret_service.create_secret(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        name="internal-oauth-access-token",
        value="oauth-access-token-value",
        scope=SecretScope(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            actions=["connector.oauth2.access"],
        ),
    )
    return secret_service, secret


def test_internal_api_dispatch_sends_allowed_request_and_decodes_json_response():
    registry = InMemoryConnectorRegistry()
    connector = register_internal_api_connector(registry)
    response_body = b'{"id":"acct_42","name":"Acme"}'
    http_client = LocalConnectorHttpClient(
        ConnectorHttpResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=response_body,
        )
    )
    service = ConnectorDispatchService(http_client=http_client)

    result = service.dispatch(
        connector=connector,
        tool_input={
            "method": "GET",
            "path": "/accounts/42",
            "query": {"include": "contacts"},
        },
    )

    assert len(http_client.requests) == 1
    sent = http_client.requests[0]
    assert sent.method == "GET"
    assert sent.url == "https://internal.example.com/api/accounts/42?include=contacts"
    assert sent.timeout_seconds == 4
    assert sent.body is None
    assert result.output == {
        "status_code": 200,
        "content_type": "application/json",
        "body": {"id": "acct_42", "name": "Acme"},
    }
    assert result.response_size_bytes == len(response_body)


def test_internal_api_dispatch_injects_api_key_from_secret_reference_only():
    registry = InMemoryConnectorRegistry()
    secret_service, secret = create_connector_secret_service()
    connector = register_authenticated_internal_api_connector(registry, secret.id)
    http_client = LocalConnectorHttpClient(
        ConnectorHttpResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=b'{"ok":true}',
        )
    )
    service = ConnectorDispatchService(
        http_client=http_client,
        secret_service=secret_service,
    )

    result = service.dispatch(
        connector=connector,
        tool_name=f"connector.{connector.id}.get_account",
        tool_input={"method": "GET", "path": "/accounts/42"},
    )

    assert len(http_client.requests) == 1
    assert http_client.requests[0].headers == {"x-api-key": "internal-secret-key"}
    assert result is not None
    assert result.output["body"] == {"ok": True}
    assert "internal-secret-key" not in str(result.model_dump(mode="json"))


def test_internal_api_dispatch_injects_oauth2_bearer_token_from_secret_reference():
    registry = InMemoryConnectorRegistry()
    secret_service, secret = create_connector_oauth_secret_service()
    connector = register_oauth_internal_api_connector(registry, secret.id)
    http_client = LocalConnectorHttpClient(
        ConnectorHttpResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=b'{"ok":true}',
        )
    )
    service = ConnectorDispatchService(
        http_client=http_client,
        secret_service=secret_service,
    )

    result = service.dispatch(
        connector=connector,
        tool_name=f"connector.{connector.id}.get_account",
        tool_input={"method": "GET", "path": "/accounts/42"},
    )

    assert len(http_client.requests) == 1
    assert http_client.requests[0].headers == {
        "authorization": "Bearer oauth-access-token-value"
    }
    assert result is not None
    assert result.output["body"] == {"ok": True}
    assert "oauth-access-token-value" not in str(result.model_dump(mode="json"))


def test_database_dispatch_runs_allowlisted_read_query_from_secret_dsn(tmp_path):
    database_path = tmp_path / "sales.sqlite3"
    create_sales_database(database_path)
    registry = InMemoryConnectorRegistry()
    secret_service, secret = create_database_secret_service(f"sqlite:///{database_path}")
    connector = register_database_connector(registry, secret.id)
    service = ConnectorDispatchService(secret_service=secret_service)

    result = service.dispatch(
        connector=connector,
        tool_name=f"connector.{connector.id}.query_accounts",
        tool_input={
            "sql": "SELECT id, name FROM accounts WHERE id = ?",
            "parameters": ["acct_42"],
        },
    )

    assert result is not None
    assert result.status_code == 200
    assert result.output == {
        "columns": ["id", "name"],
        "rows": [{"id": "acct_42", "name": "Acme"}],
        "row_count": 1,
    }
    assert "sqlite:///" not in str(result.model_dump(mode="json"))


def test_database_dispatch_blocks_write_query_before_execution(tmp_path):
    database_path = tmp_path / "sales-blocked.sqlite3"
    create_sales_database(database_path)
    registry = InMemoryConnectorRegistry()
    secret_service, secret = create_database_secret_service(f"sqlite:///{database_path}")
    connector = register_database_connector(registry, secret.id)
    service = ConnectorDispatchService(secret_service=secret_service)

    with pytest.raises(ConnectorDispatchError, match="only SELECT queries are allowed"):
        service.dispatch(
            connector=connector,
            tool_name=f"connector.{connector.id}.query_accounts",
            tool_input={
                "sql": "DELETE FROM accounts WHERE id = ?",
                "parameters": ["acct_42"],
            },
        )

    with sqlite3.connect(database_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    assert count == 2


def test_database_dispatch_blocks_disallowed_table(tmp_path):
    database_path = tmp_path / "sales-disallowed.sqlite3"
    create_sales_database(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE invoices (id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO invoices (id) VALUES (?)", ("invoice_1",))
    registry = InMemoryConnectorRegistry()
    secret_service, secret = create_database_secret_service(f"sqlite:///{database_path}")
    connector = register_database_connector(registry, secret.id)
    service = ConnectorDispatchService(secret_service=secret_service)

    with pytest.raises(ConnectorDispatchError, match="table is not allowed"):
        service.dispatch(
            connector=connector,
            tool_name=f"connector.{connector.id}.query_accounts",
            tool_input={"sql": "SELECT id FROM invoices"},
        )


def test_internal_api_dispatch_requires_secret_service_for_authenticated_connector():
    registry = InMemoryConnectorRegistry()
    secret_service, secret = create_connector_secret_service()
    connector = register_authenticated_internal_api_connector(registry, secret.id)
    http_client = LocalConnectorHttpClient(
        ConnectorHttpResponse(status_code=200, headers={}, body=b"{}")
    )
    service = ConnectorDispatchService(http_client=http_client)

    with pytest.raises(ConnectorDispatchError, match="secret service is not configured"):
        service.dispatch(
            connector=connector,
            tool_name=f"connector.{connector.id}.get_account",
            tool_input={"method": "GET", "path": "/accounts/42"},
        )

    assert http_client.requests == []


def test_internal_api_dispatch_blocks_disallowed_path_before_network_call():
    registry = InMemoryConnectorRegistry()
    connector = register_internal_api_connector(registry)
    http_client = LocalConnectorHttpClient(
        ConnectorHttpResponse(status_code=200, headers={}, body=b"{}")
    )
    service = ConnectorDispatchService(http_client=http_client)

    with pytest.raises(ConnectorDispatchError, match="path is not allowed"):
        service.dispatch(
            connector=connector,
            tool_input={"method": "GET", "path": "/admin/users"},
        )

    assert http_client.requests == []


def test_connector_invoke_api_dispatches_internal_api_and_keeps_audit_safe():
    identity, account = create_connector_operator_identity()
    store = InMemoryControlPlaneStore()
    registry = InMemoryConnectorRegistry()
    connector = register_internal_api_connector(registry)
    run = create_run(store, account.id)
    response_body = b'{"id":"acct_42","name":"Acme"}'
    http_client = LocalConnectorHttpClient(
        ConnectorHttpResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=response_body,
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

    response = client.post(
        f"/api/connectors/{connector.id}/invoke",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id},
        json={
            "run_id": run.id,
            "step_id": "step_internal_api",
            "capability_name": "get_account",
            "tool_input": {
                "method": "GET",
                "path": "/accounts/42",
                "customer_token": "token-value",
            },
            "granted_scopes": ["accounts.read"],
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "ready"
    assert body["output"]["body"] == {"id": "acct_42", "name": "Acme"}
    assert len(http_client.requests) == 1

    events = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "connector.invoked"
    ]
    assert len(events) == 1
    assert events[0].metadata["connector_id"] == connector.id
    assert events[0].metadata["dispatch_status_code"] == 200
    assert events[0].metadata["response_size_bytes"] == len(response_body)
    assert "token-value" not in str(events[0].metadata)
    assert "acct_42" not in str(events[0].metadata)

    meters = [
        meter
        for meter in store.list_billing_meters("tenant_acme")
        if meter.meter_type == "connector_invocation_count"
    ]
    assert len(meters) == 1
    assert meters[0].metadata["dispatch_status_code"] == 200


def test_connector_invoke_api_dispatches_authenticated_internal_api_without_audit_leak():
    identity, account = create_connector_operator_identity()
    store = InMemoryControlPlaneStore()
    registry = InMemoryConnectorRegistry()
    secret_service, secret = create_connector_secret_service()
    connector = register_authenticated_internal_api_connector(registry, secret.id)
    run = create_run(store, account.id)
    response_body = b'{"id":"acct_42"}'
    http_client = LocalConnectorHttpClient(
        ConnectorHttpResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=response_body,
        )
    )
    client = TestClient(
        create_app(
            identity_service=identity,
            store=store,
            connector_registry=registry,
            secret_service=secret_service,
            connector_dispatcher=ConnectorDispatchService(http_client=http_client),
            settings=Settings(_env_file=None),
        )
    )

    response = client.post(
        f"/api/connectors/{connector.id}/invoke",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id},
        json={
            "run_id": run.id,
            "step_id": "step_internal_api",
            "capability_name": "get_account",
            "tool_input": {"method": "GET", "path": "/accounts/42"},
            "granted_scopes": ["accounts.read"],
        },
    )

    assert response.status_code == 202
    assert len(http_client.requests) == 1
    assert http_client.requests[0].headers == {"x-api-key": "internal-secret-key"}
    assert "internal-secret-key" not in str(response.json())

    events = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "connector.invoked"
    ]
    assert len(events) == 1
    assert events[0].metadata["credential_ref_id"] == secret.id
    assert events[0].metadata["credential_actions"] == ["connector.read"]
    assert "internal-secret-key" not in str(events[0].metadata)


def test_connector_invoke_api_dispatches_oauth2_internal_api_without_audit_leak():
    identity, account = create_connector_operator_identity()
    store = InMemoryControlPlaneStore()
    registry = InMemoryConnectorRegistry()
    secret_service, secret = create_connector_oauth_secret_service()
    connector = register_oauth_internal_api_connector(registry, secret.id)
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
            secret_service=secret_service,
            connector_dispatcher=ConnectorDispatchService(http_client=http_client),
            settings=Settings(_env_file=None),
        )
    )

    response = client.post(
        f"/api/connectors/{connector.id}/invoke",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id},
        json={
            "run_id": run.id,
            "step_id": "step_internal_api",
            "capability_name": "get_account",
            "tool_input": {"method": "GET", "path": "/accounts/42"},
            "granted_scopes": ["accounts.read"],
        },
    )

    assert response.status_code == 202
    assert http_client.requests[0].headers == {
        "authorization": "Bearer oauth-access-token-value"
    }
    assert "oauth-access-token-value" not in str(response.json())

    events = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "connector.invoked"
    ]
    assert len(events) == 1
    assert events[0].metadata["credential_ref_id"] == secret.id
    assert events[0].metadata["credential_actions"] == ["connector.oauth2.access"]
    assert "oauth-access-token-value" not in str(events[0].metadata)


def test_connector_invoke_api_dispatches_database_connector_without_audit_leak(tmp_path):
    database_path = tmp_path / "sales-api.sqlite3"
    database_url = f"sqlite:///{database_path}"
    create_sales_database(database_path)
    identity, account = create_connector_operator_identity()
    store = InMemoryControlPlaneStore()
    registry = InMemoryConnectorRegistry()
    secret_service, secret = create_database_secret_service(database_url)
    connector = register_database_connector(registry, secret.id)
    run = create_run(store, account.id)
    client = TestClient(
        create_app(
            identity_service=identity,
            store=store,
            connector_registry=registry,
            secret_service=secret_service,
            connector_dispatcher=ConnectorDispatchService(),
            settings=Settings(_env_file=None),
        )
    )

    response = client.post(
        f"/api/connectors/{connector.id}/invoke",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id},
        json={
            "run_id": run.id,
            "step_id": "step_database",
            "capability_name": "query_accounts",
            "tool_input": {
                "sql": "SELECT id, name FROM accounts WHERE id = ?",
                "parameters": ["acct_42"],
                "customer_token": "token-value",
            },
            "granted_scopes": ["database.accounts.read"],
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "ready"
    assert body["output"]["rows"] == [{"id": "acct_42", "name": "Acme"}]
    assert database_url not in str(body)

    events = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "connector.invoked"
    ]
    assert len(events) == 1
    assert events[0].metadata["connector_id"] == connector.id
    assert events[0].metadata["dispatch_status_code"] == 200
    assert events[0].metadata["credential_ref_id"] == secret.id
    assert "token-value" not in str(events[0].metadata)
    assert "acct_42" not in str(events[0].metadata)
    assert database_url not in str(events[0].metadata)


def test_connector_invoke_api_records_safe_dispatch_failure():
    identity, account = create_connector_operator_identity()
    store = InMemoryControlPlaneStore()
    registry = InMemoryConnectorRegistry()
    connector = register_internal_api_connector(registry)
    run = create_run(store, account.id)
    http_client = LocalConnectorHttpClient(
        ConnectorHttpResponse(status_code=200, headers={}, body=b"{}")
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

    response = client.post(
        f"/api/connectors/{connector.id}/invoke",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id},
        json={
            "run_id": run.id,
            "step_id": "step_internal_api",
            "capability_name": "get_account",
            "tool_input": {
                "method": "GET",
                "path": "/admin/users",
                "customer_token": "token-value",
            },
            "granted_scopes": ["accounts.read"],
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "connector_dispatch_failed"
    assert http_client.requests == []

    events = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "connector.dispatch_failed"
    ]
    assert len(events) == 1
    assert events[0].metadata["connector_id"] == connector.id
    assert events[0].metadata["error_code"] == "connector_dispatch_failed"
    assert "token-value" not in str(events[0].metadata)
