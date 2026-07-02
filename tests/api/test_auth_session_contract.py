import pytest
from fastapi.testclient import TestClient

from taroai.auth import AuthInvalidCredentialsError, AuthRequiredError, AuthService
from taroai.config import Settings
from taroai.identity import (
    InMemoryIdentityService,
    PasswordHasher,
    Role,
    UserAccountCreate,
)
from taroai.app import create_app


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
