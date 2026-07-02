from pathlib import Path

from fastapi.testclient import TestClient

from taroai.app import create_app
from taroai.config import Settings
from taroai.db import DatabaseConfig, MigrationRunner
from taroai.identity import (
    PasswordHasher,
    Permission,
    Role,
    SqlIdentityService,
    UserAccountCreate,
)


def prepare_database(database_url: str) -> None:
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()


def test_sql_identity_service_persists_users_roles_and_disabled_status(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    prepare_database(database_url)
    service = SqlIdentityService(
        config=DatabaseConfig(url=database_url),
        password_hasher=PasswordHasher(salt="test_salt"),
    )

    account = service.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="luke@example.com",
            display_name="Luke",
            password="correct horse battery staple",
        )
    )
    service.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_admin",
            name="Admin",
            permissions=[
                Permission(action="runs.read", resource="workspace:*"),
                Permission(action="billing.admin", resource="tenant:tenant_acme"),
            ],
        )
    )
    service.assign_role("tenant_acme", account.id, "role_admin")

    restarted = SqlIdentityService(
        config=DatabaseConfig(url=database_url),
        password_hasher=PasswordHasher(salt="test_salt"),
    )
    persisted = restarted.get_user_by_email("tenant_acme", "luke@example.com")

    assert persisted == account
    assert restarted.verify_password(
        "tenant_acme",
        "luke@example.com",
        "correct horse battery staple",
    )
    assert not restarted.verify_password("tenant_acme", "luke@example.com", "wrong password")
    assert restarted.list_role_ids_for_user("tenant_acme", account.id) == ["role_admin"]
    assert restarted.has_permission(
        "tenant_acme",
        account.id,
        "runs.read",
        "workspace:workspace_sales",
    )
    assert not restarted.has_permission(
        "tenant_acme",
        account.id,
        "skills.publish",
        "tenant:tenant_acme",
    )

    restarted.disable_user("tenant_acme", account.id)
    after_disable = SqlIdentityService(
        config=DatabaseConfig(url=database_url),
        password_hasher=PasswordHasher(salt="test_salt"),
    )
    assert after_disable.get_user("tenant_acme", account.id).status == "disabled"


def test_auth_endpoint_can_login_with_sql_identity_service_after_restart(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    prepare_database(database_url)
    identity_service = SqlIdentityService(
        config=DatabaseConfig(url=database_url),
        password_hasher=PasswordHasher(salt="test_salt"),
    )
    account = identity_service.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="luke@example.com",
            display_name="Luke",
            password="correct horse battery staple",
        )
    )
    identity_service.create_role(Role(tenant_id="tenant_acme", id="role_admin", name="Admin"))
    identity_service.assign_role("tenant_acme", account.id, "role_admin")
    settings = Settings(
        database_url=database_url,
        identity_service_backend="sql",
        password_hash_salt="test_salt",
        access_token_secret="unit_test_secret",
        dev_request_headers_enabled=False,
        _env_file=None,
    )

    client = TestClient(create_app(settings=settings))
    login = client.post(
        "/api/auth/login",
        json={
            "tenant_id": "tenant_acme",
            "email": "luke@example.com",
            "password": "correct horse battery staple",
        },
    )
    access_token = login.json()["access_token"]
    restarted_client = TestClient(create_app(settings=settings))
    created = restarted_client.post(
        "/api/runs",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "workspace_id": "workspace_sales",
            "message": "Create a prospect brief.",
            "mode": "workflow",
        },
    )

    assert login.status_code == 200
    assert login.json()["user_id"] == account.id
    assert created.status_code == 201


def test_sql_auth_session_logout_survives_app_restart(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    prepare_database(database_url)
    identity_service = SqlIdentityService(
        config=DatabaseConfig(url=database_url),
        password_hasher=PasswordHasher(salt="test_salt"),
    )
    identity_service.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="luke@example.com",
            display_name="Luke",
            password="correct horse battery staple",
        )
    )
    settings = Settings(
        database_url=database_url,
        identity_service_backend="sql",
        password_hash_salt="test_salt",
        access_token_secret="unit_test_secret",
        dev_request_headers_enabled=False,
        _env_file=None,
    )

    login_client = TestClient(create_app(settings=settings))
    login = login_client.post(
        "/api/auth/login",
        json={
            "tenant_id": "tenant_acme",
            "email": "luke@example.com",
            "password": "correct horse battery staple",
        },
    )
    access_token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}
    logout_client = TestClient(create_app(settings=settings))
    logout = logout_client.post("/api/auth/logout", headers=headers)
    restarted_client = TestClient(create_app(settings=settings))
    created = restarted_client.post(
        "/api/runs",
        headers=headers,
        json={
            "workspace_id": "workspace_sales",
            "message": "Create a prospect brief.",
            "mode": "workflow",
        },
    )

    assert login.status_code == 200
    assert login.json()["session_id"].startswith("session_")
    assert logout.status_code == 200
    assert logout.json() == {"revoked": True}
    assert created.status_code == 401
    assert created.json()["code"] == "auth_required"
