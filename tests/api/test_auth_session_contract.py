import pytest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient

from taroai.auth import (
    AuthInvalidCredentialsError,
    AuthRequiredError,
    AuthService,
    SqlAuthSessionStore,
)
from taroai.config import Settings
from taroai.identity import (
    InMemoryIdentityService,
    PasswordHasher,
    Role,
    UserAccountCreate,
)
from taroai.app import create_app
from taroai.sso import InMemorySsoProviderRegistry, SsoProviderCreate
from taroai.store import InMemoryControlPlaneStore


def oidc_sso_provider_payload(password_fallback_enabled: bool = False) -> dict:
    return {
        "id": "okta_workforce",
        "display_name": "Okta Workforce",
        "protocol": "oidc",
        "domains": ["acme.com"],
        "password_fallback_enabled": password_fallback_enabled,
        "jit_provisioning_enabled": True,
        "oidc": {
            "issuer_url": "https://idp.acme.com/oauth2/default",
            "client_id": "taroai-client",
            "client_secret_ref_id": "secret_okta_oidc_client",
        },
    }


def build_identity_service() -> InMemoryIdentityService:
    service = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    account = service.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="luke@example.com",
            display_name="Luke",
            password="correct horse battery staple",
        )
    )
    service.create_role(Role(tenant_id="tenant_acme", id="role_admin", name="Admin"))
    service.assign_role("tenant_acme", account.id, "role_admin")
    return service


def build_acme_identity_service() -> InMemoryIdentityService:
    service = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    account = service.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="luke@acme.com",
            display_name="Luke",
            password="correct horse battery staple",
        )
    )
    service.create_role(Role(tenant_id="tenant_acme", id="role_admin", name="Admin"))
    service.assign_role("tenant_acme", account.id, "role_admin")
    return service


def build_enabled_sso_registry(password_fallback_enabled: bool = False) -> InMemorySsoProviderRegistry:
    registry = InMemorySsoProviderRegistry()
    provider = registry.create_or_update(
        tenant_id="tenant_acme",
        created_by_user_id="user_admin",
        request=SsoProviderCreate.model_validate(
            oidc_sso_provider_payload(
                password_fallback_enabled=password_fallback_enabled,
            )
        ),
    )
    registry.enable("tenant_acme", provider.provider.id)
    return registry


def test_auth_service_issues_and_validates_signed_access_tokens():
    identity_service = build_identity_service()
    auth_service = AuthService(
        identity_service=identity_service,
        access_token_secret="unit_test_secret",
        access_token_ttl_seconds=900,
    )

    login = auth_service.login(
        tenant_id="tenant_acme",
        email="luke@example.com",
        password="correct horse battery staple",
    )
    claims = auth_service.validate_token(login.access_token)

    assert login.token_type == "Bearer"
    assert login.tenant_id == "tenant_acme"
    assert claims.tenant_id == "tenant_acme"
    assert claims.user_id == login.user_id
    assert claims.email == "luke@example.com"
    assert login.display_name == claims.display_name == "Luke"
    assert claims.role_ids == ["role_admin"]

    tampered_token = f"{login.access_token[:-1]}x"
    with pytest.raises(AuthRequiredError):
        auth_service.validate_token(tampered_token)


def test_auth_endpoint_extends_only_remembered_sessions():
    client = TestClient(
        create_app(
            identity_service=build_identity_service(),
            settings=Settings(
                access_token_secret="unit_test_secret",
                access_token_ttl_seconds=900,
                remembered_access_token_ttl_seconds=2_592_000,
                _env_file=None,
            ),
        )
    )
    credentials = {
        "tenant_id": "tenant_acme",
        "email": "luke@example.com",
        "password": "correct horse battery staple",
    }

    regular = client.post("/api/auth/login", json=credentials)
    remembered = client.post(
        "/api/auth/login",
        json={**credentials, "remember_me": True},
    )

    assert regular.status_code == remembered.status_code == 200
    regular_expiry = datetime.fromisoformat(
        regular.json()["expires_at"].replace("Z", "+00:00")
    )
    remembered_expiry = datetime.fromisoformat(
        remembered.json()["expires_at"].replace("Z", "+00:00")
    )
    assert remembered_expiry - regular_expiry > timedelta(days=29)


def test_auth_session_endpoint_syncs_valid_session_and_hides_invalid_state():
    client = TestClient(
        create_app(
            identity_service=build_identity_service(),
            store=InMemoryControlPlaneStore(
                workspace_tenants={
                    "workspace_sales": "tenant_acme",
                    "workspace_research": "tenant_acme",
                }
            ),
            settings=Settings(access_token_secret="unit_test_secret", _env_file=None),
        )
    )
    assert client.get("/api/auth/session").json() == {"authenticated": False}

    login = client.post(
        "/api/auth/login",
        json={
            "email": "luke@example.com",
            "password": "correct horse battery staple",
        },
    ).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    session = client.get("/api/auth/session", headers=headers).json()

    assert session["authenticated"] is True
    assert session["tenant_id"] == "tenant_acme"
    assert session["user_id"] == login["user_id"]
    assert session["email"] == "luke@example.com"
    assert session["display_name"] == "Luke"
    assert session["workspace_id"] == "workspace_sales"

    selected_session = client.get(
        "/api/auth/session",
        headers={**headers, "X-Workspace-ID": "workspace_research"},
    ).json()
    assert selected_session["workspace_id"] == "workspace_research"

    client.post("/api/auth/logout", headers=headers)
    assert client.get("/api/auth/session", headers=headers).json() == {
        "authenticated": False
    }


def test_development_registration_creates_a_workspace_and_supports_login():
    client = TestClient(
        create_app(
            identity_service=InMemoryIdentityService(
                password_hasher=PasswordHasher(salt="test_salt")
            ),
            store=InMemoryControlPlaneStore(),
            settings=Settings(
                access_token_secret="unit_test_secret",
                tenant_bootstrap_token="bootstrap_secret",
                dev_request_headers_enabled=True,
                _env_file=None,
            ),
        )
    )
    credentials = {
        "display_name": "New User",
        "email": "new.user@example.com",
        "password": "correct horse battery staple",
    }

    registration = client.post("/api/auth/register", json=credentials)

    assert registration.status_code == 201
    tenant_id = registration.json()["tenant_id"]
    workspace_id = registration.json()["starter_workspace_id"]
    login = client.post(
        "/api/auth/login",
        json={
            "tenant_id": tenant_id,
            "email": credentials["email"],
            "password": credentials["password"],
        },
    )
    assert login.status_code == 200
    assert login.json()["tenant_id"] == tenant_id
    assert login.json()["workspace_id"] == workspace_id
    assert login.json()["display_name"] == credentials["display_name"]


def test_registration_is_disabled_outside_local_environments():
    client = TestClient(
        create_app(
            settings=Settings(
                access_token_secret="unit_test_secret",
                tenant_bootstrap_token="bootstrap_secret",
                environment="staging",
                dev_request_headers_enabled=False,
                _env_file=None,
            )
        )
    )

    response = client.post(
        "/api/auth/register",
        json={
            "display_name": "New User",
            "email": "new.user@example.com",
            "password": "correct horse battery staple",
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "tenant_access_denied"


@pytest.mark.parametrize("tenant_id", [None, "tenant_stale"])
def test_auth_endpoint_resolves_unique_account_without_a_valid_tenant_hint(tenant_id):
    client = TestClient(
        create_app(
            identity_service=build_identity_service(),
            store=InMemoryControlPlaneStore(
                workspace_tenants={"workspace_sales": "tenant_acme"}
            ),
            settings=Settings(access_token_secret="unit_test_secret", _env_file=None),
        )
    )
    payload = {
        "email": "luke@example.com",
        "password": "correct horse battery staple",
    }
    if tenant_id is not None:
        payload["tenant_id"] = tenant_id

    response = client.post("/api/auth/login", json=payload)

    assert response.status_code == 200
    assert response.json()["tenant_id"] == "tenant_acme"
    assert response.json()["workspace_id"] == "workspace_sales"


def test_auth_service_rejects_ambiguous_account_without_tenant_hint():
    identity_service = build_identity_service()
    identity_service.create_user(
        UserAccountCreate(
            tenant_id="tenant_other",
            email="luke@example.com",
            display_name="Other Luke",
            password="correct horse battery staple",
        )
    )
    auth_service = AuthService(
        identity_service=identity_service,
        access_token_secret="unit_test_secret",
        access_token_ttl_seconds=900,
    )

    with pytest.raises(AuthInvalidCredentialsError, match="invalid credentials"):
        auth_service.login(None, "luke@example.com", "correct horse battery staple")

    login = auth_service.login(
        "tenant_acme", "luke@example.com", "correct horse battery staple"
    )
    assert login.tenant_id == "tenant_acme"


def test_auth_service_rejects_bad_password_and_disabled_user():
    identity_service = build_identity_service()
    account = identity_service.get_user_by_email("tenant_acme", "luke@example.com")
    auth_service = AuthService(
        identity_service=identity_service,
        access_token_secret="unit_test_secret",
        access_token_ttl_seconds=900,
    )

    with pytest.raises(AuthInvalidCredentialsError):
        auth_service.login("tenant_acme", "luke@example.com", "wrong password")

    identity_service.disable_user("tenant_acme", account.id)
    with pytest.raises(AuthInvalidCredentialsError):
        auth_service.login("tenant_acme", "luke@example.com", "correct horse battery staple")


def test_auth_service_rejects_existing_token_after_user_is_disabled():
    identity_service = build_identity_service()
    account = identity_service.get_user_by_email("tenant_acme", "luke@example.com")
    auth_service = AuthService(
        identity_service=identity_service,
        access_token_secret="unit_test_secret",
        access_token_ttl_seconds=900,
    )
    login = auth_service.login(
        tenant_id="tenant_acme",
        email="luke@example.com",
        password="correct horse battery staple",
    )

    identity_service.disable_user("tenant_acme", account.id)

    with pytest.raises(AuthRequiredError):
        auth_service.validate_token(login.access_token)


def test_auth_service_rejects_password_login_for_sso_only_domain():
    identity_service = build_acme_identity_service()
    auth_service = AuthService(
        identity_service=identity_service,
        sso_provider_registry=build_enabled_sso_registry(password_fallback_enabled=False),
        access_token_secret="unit_test_secret",
        access_token_ttl_seconds=900,
    )

    with pytest.raises(AuthInvalidCredentialsError):
        auth_service.login("tenant_acme", "luke@acme.com", "correct horse battery staple")


def test_auth_service_allows_password_login_when_sso_fallback_is_enabled():
    identity_service = build_acme_identity_service()
    auth_service = AuthService(
        identity_service=identity_service,
        sso_provider_registry=build_enabled_sso_registry(password_fallback_enabled=True),
        access_token_secret="unit_test_secret",
        access_token_ttl_seconds=900,
    )

    login = auth_service.login(
        tenant_id="tenant_acme",
        email="luke@acme.com",
        password="correct horse battery staple",
    )

    claims = auth_service.validate_token(login.access_token)

    assert claims.email == "luke@acme.com"


def test_auth_endpoint_applies_sso_password_fallback_policy():
    identity_service = build_acme_identity_service()
    client = TestClient(
        create_app(
            identity_service=identity_service,
            sso_provider_registry=build_enabled_sso_registry(password_fallback_enabled=False),
            settings=Settings(access_token_secret="unit_test_secret", _env_file=None),
        )
    )

    response = client.post(
        "/api/auth/login",
        json={
            "tenant_id": "tenant_acme",
            "email": "luke@acme.com",
            "password": "correct horse battery staple",
        },
    )

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


def test_auth_endpoint_token_can_replace_dev_headers_for_run_creation():
    identity_service = build_identity_service()
    store = InMemoryControlPlaneStore(
        workspace_tenants={"workspace_sales": "tenant_acme"}
    )
    client = TestClient(
        create_app(
            identity_service=identity_service,
            store=store,
            settings=Settings(
                dev_request_headers_enabled=False,
                access_token_secret="unit_test_secret",
                _env_file=None,
            ),
        )
    )

    login = client.post(
        "/api/auth/login",
        json={
            "tenant_id": "tenant_acme",
            "email": "luke@example.com",
            "password": "correct horse battery staple",
        },
    )
    access_token = login.json()["access_token"]
    unauthenticated = client.post(
        "/api/runs",
        json={
            "workspace_id": "workspace_sales",
            "message": "Create a prospect brief.",
            "mode": "workflow",
        },
    )
    created = client.post(
        "/api/runs",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "workspace_id": "workspace_sales",
            "message": "Create a prospect brief.",
            "mode": "workflow",
        },
    )

    assert login.status_code == 200
    assert login.json()["workspace_id"] == "workspace_sales"
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["code"] == "auth_required"
    assert created.status_code == 201
    assert created.json()["run_id"].startswith("run_")


def test_auth_logout_revokes_current_bearer_token():
    identity_service = build_identity_service()
    client = TestClient(
        create_app(
            identity_service=identity_service,
            settings=Settings(
                dev_request_headers_enabled=False,
                access_token_secret="unit_test_secret",
                _env_file=None,
            ),
        )
    )
    login = client.post(
        "/api/auth/login",
        json={
            "tenant_id": "tenant_acme",
            "email": "luke@example.com",
            "password": "correct horse battery staple",
        },
    )
    access_token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    before_logout = client.post(
        "/api/runs",
        headers=headers,
        json={
            "workspace_id": "workspace_sales",
            "message": "Create a prospect brief.",
            "mode": "workflow",
        },
    )
    logout = client.post("/api/auth/logout", headers=headers)
    after_logout = client.post(
        "/api/runs",
        headers=headers,
        json={
            "workspace_id": "workspace_sales",
            "message": "Create another prospect brief.",
            "mode": "workflow",
        },
    )

    assert before_logout.status_code == 201
    assert logout.status_code == 200
    assert logout.json() == {"revoked": True}
    assert after_logout.status_code == 401
    assert after_logout.json()["code"] == "auth_required"


def test_sql_auth_session_store_hydrates_postgresql_native_datetime_values():
    issued_at = datetime(2026, 7, 3, 13, 30, tzinfo=timezone.utc)
    expires_at = datetime(2026, 7, 3, 14, 30, tzinfo=timezone.utc)
    store = SqlAuthSessionStore(config=Settings(_env_file=None).database_config())

    session = store._session_from_row(
        {
            "id": "session_owner",
            "tenant_id": "tenant_acme",
            "user_id": "user_owner",
            "issued_at": issued_at,
            "expires_at": expires_at,
            "revoked_at": None,
        }
    )

    assert session.issued_at == issued_at
    assert session.expires_at == expires_at
