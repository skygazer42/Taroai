from fastapi.testclient import TestClient
from botocore.exceptions import NoCredentialsError

from taroai.app import create_app
from taroai.config import Settings
from taroai.connectors import (
    ConnectorAuthMode,
    ConnectorCredentialRef,
    ConnectorDefinitionCreate,
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
from taroai.secrets import (
    AwsSecretsManagerConfig,
    AwsSecretsManagerSecretService,
    InMemorySecretService,
    LocalEncryptedSecretService,
    SecretScope,
)
from taroai.store import InMemoryControlPlaneStore


class SharedSecretsManagerClient:
    def __init__(self):
        self.values = {}

    def create_secret(self, **payload):
        self.values[payload["Name"]] = payload["SecretString"]
        return {}

    def get_secret_value(self, **payload):
        return {"SecretString": self.values[payload["SecretId"]]}


class UnavailableSecretsManagerClient:
    def create_secret(self, **_):
        raise NoCredentialsError()


class RecordingHttpClient:
    request = None

    def send(self, request):
        self.request = request
        return ConnectorHttpResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=b'{"ok":true}',
        )


def create_connector_manager_identity():
    identity = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="secret-manager@example.com",
            display_name="Secret Manager",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_connector_manager",
            name="Connector Manager",
            permissions=[
                Permission(
                    action="connectors.manage",
                    resource="tenant:tenant_acme",
                )
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_connector_manager")
    return identity, account


def test_local_secret_survives_a_fresh_process_without_plaintext_on_disk(tmp_path):
    path = tmp_path / "secrets.db"
    value = "captured-worker-secret"
    service = LocalEncryptedSecretService(path=path)
    secret = service.create_secret(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        name="Private API credential",
        value=value,
        scope=SecretScope(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            allowed_tool_names=["connector.health"],
            actions=["connector.read"],
        ),
    )
    lease = service.create_lease(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        secret_id=secret.id,
        tool_name="connector.health",
        actions=["connector.read"],
        ttl_seconds=60,
    )
    credential_ref = ConnectorCredentialRef(
        tenant_id=secret.tenant_id,
        workspace_id=secret.workspace_id,
        secret_ref_id=secret.id,
        required_actions=["connector.read"],
        secret_backend=secret.backend,
        secret_external_name=secret.external_name,
    )

    fresh_service = LocalEncryptedSecretService(path=path)
    fresh_service.register_secret_ref(secret)

    assert fresh_service.resolve_lease_value(
        tenant_id="tenant_acme",
        lease_token=lease.lease_token,
    ) == value
    assert credential_ref.secret_backend == "local"
    assert value.encode() not in path.read_bytes()


def test_secret_capture_stores_scoped_secret_without_leaking_value():
    store = InMemoryControlPlaneStore()
    secret_service = InMemorySecretService()
    run = store.create_run(
        "tenant_acme",
        "user_1",
        RunCreate(workspace_id="workspace_sales", message="Use the private API"),
    )
    capture = store.create_secret_capture_request(
        run,
        name="Private API credential",
        tool_name="connector.private.search",
        actions=["connector.read"],
    )
    client = TestClient(
        create_app(
            store=store,
            secret_service=secret_service,
            settings=Settings(_env_file=None),
        )
    )
    value = "secret-value-that-must-never-reach-events"

    response = client.post(
        f"/api/secret-captures/{capture.id}",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": "user_1"},
        json={"value": value},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "resolved"
    secret_id = response.json()["secretRefId"]
    assert secret_service._secret_values[secret_id] == value
    assert value not in response.text
    assert value not in str(
        [
            event.model_dump(mode="json")
            for event in store.list_run_events("tenant_acme", run.id)
        ]
    )
    assert value not in str(secret_service.model_dump(mode="json"))


def test_secret_capture_is_tenant_scoped_and_rejects_empty_values():
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        "tenant_acme",
        "user_1",
        RunCreate(workspace_id="workspace_sales", message="Use the private API"),
    )
    capture = store.create_secret_capture_request(run, name="Credential")
    client = TestClient(
        create_app(store=store, settings=Settings(_env_file=None))
    )

    cross_tenant = client.post(
        f"/api/secret-captures/{capture.id}",
        headers={"X-Tenant-ID": "tenant_other", "X-User-ID": "user_2"},
        json={"value": "not-used"},
    )
    wrong_user = client.post(
        f"/api/secret-captures/{capture.id}",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": "user_2"},
        json={"value": "not-used"},
    )
    empty = client.post(
        f"/api/secret-captures/{capture.id}",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": "user_1"},
        json={"value": ""},
    )

    assert cross_tenant.status_code == 404
    assert wrong_user.status_code == 403
    assert empty.status_code == 422
    assert store.get_secret_capture_request("tenant_acme", capture.id).status == "pending"


def test_secret_capture_reports_unavailable_backend_and_remains_pending():
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        "tenant_acme",
        "user_1",
        RunCreate(workspace_id="workspace_sales", message="Use the private API"),
    )
    capture = store.create_secret_capture_request(run, name="Credential")
    client = TestClient(
        create_app(
            store=store,
            secret_service=AwsSecretsManagerSecretService(
                client=UnavailableSecretsManagerClient()
            ),
            settings=Settings(_env_file=None),
        )
    )

    response = client.post(
        f"/api/secret-captures/{capture.id}",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": "user_1"},
        json={"value": "not-stored"},
    )

    assert response.status_code == 503
    assert response.json()["code"] == "secret_backend_unavailable"
    assert store.get_secret_capture_request("tenant_acme", capture.id).status == "pending"


def test_captured_aws_secret_can_be_used_by_a_fresh_worker_process():
    identity, connector_manager = create_connector_manager_identity()
    store = InMemoryControlPlaneStore()
    run = store.create_run(
        "tenant_acme",
        "user_1",
        RunCreate(workspace_id="workspace_sales", message="Check health"),
    )
    registry = InMemoryConnectorRegistry()
    connector = registry.register_connector(
        ConnectorDefinitionCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            type=ConnectorType.INTERNAL_API,
            display_name="Private health API",
            owner_user_id="user_1",
            auth_mode=ConnectorAuthMode.API_KEY,
            credential_ref=ConnectorCredentialRef(
                tenant_id="tenant_acme",
                workspace_id="workspace_sales",
                secret_ref_id="missing",
                required_actions=["connector.read"],
            ),
            status=ConnectorStatus.NEEDS_REAUTH,
            metadata={
                "internal_api": {
                    "base_url": "https://internal.example.test",
                    "allowed_methods": ["GET"],
                    "allowed_paths": ["/healthz"],
                    "auth": {"mode": "api_key_header", "header_name": "x-api-key"},
                }
            },
        )
    )
    tool_name = f"connector.{connector.id}.health_check"
    capture = store.create_secret_capture_request(
        run,
        name="Private health API credential",
        tool_name=tool_name,
        connector_id=connector.id,
        actions=["connector.read"],
    )
    secrets_client = SharedSecretsManagerClient()
    config = AwsSecretsManagerConfig(secret_name_prefix="taroai/test")
    response = TestClient(
        create_app(
            store=store,
            connector_registry=registry,
            identity_service=identity,
            secret_service=AwsSecretsManagerSecretService(
                config=config, client=secrets_client
            ),
            settings=Settings(_env_file=None),
        )
    ).post(
        f"/api/secret-captures/{capture.id}",
        headers={
            "X-Tenant-ID": "tenant_acme",
            "X-User-ID": connector_manager.id,
        },
        json={"value": "captured-worker-secret"},
    )

    updated = registry.get_connector("tenant_acme", connector.id)
    http_client = RecordingHttpClient()
    ConnectorDispatchService(
        secret_service=AwsSecretsManagerSecretService(
            config=config, client=secrets_client
        ),
        http_client=http_client,
    ).dispatch(
        updated,
        {"method": "GET", "path": "/healthz"},
        tool_name,
    )

    assert response.status_code == 200
    assert "captured-worker-secret" not in response.text
    assert updated.credential_ref.secret_external_name not in str(
        updated.model_dump(mode="json")
    )
    assert http_client.request.headers["x-api-key"] == "captured-worker-secret"
