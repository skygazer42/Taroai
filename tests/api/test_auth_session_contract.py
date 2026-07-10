import pytest
from datetime import datetime, timezone
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
    assert claims.role_ids == ["role_admin"]

    tampered_token = f"{login.access_token[:-1]}x"
    with pytest.raises(AuthRequiredError):
        auth_service.validate_token(tampered_token)


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
