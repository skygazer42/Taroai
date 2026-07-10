from pathlib import Path
from datetime import datetime, timezone

import pytest
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


def test_sql_identity_service_rejects_case_insensitive_duplicate_email(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    prepare_database(database_url)
    service = SqlIdentityService(
        config=DatabaseConfig(url=database_url),
        password_hasher=PasswordHasher(salt="test_salt"),
    )
    service.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="  Luke@Example.com  ",
            display_name="Luke",
            password="correct horse battery staple",
        )
    )
    stored = service.get_user_by_email("tenant_acme", "luke@example.com")

    assert stored.email == "luke@example.com"
    with pytest.raises(ValueError, match="User already exists"):
        service.create_user(
            UserAccountCreate(
                tenant_id="tenant_acme",
                email="luke@example.com",
                display_name="Luke Duplicate",
                password="correct horse battery staple",
            )
        )


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

    pending = restarted.mark_user_pending("tenant_acme", account.id)
    assert pending.status == "pending"
    assert not restarted.has_permission(
        "tenant_acme",
        account.id,
        "runs.read",
        "workspace:workspace_sales",
    )

    active = restarted.activate_user("tenant_acme", account.id)
    assert active.status == "active"
    assert restarted.has_permission(
        "tenant_acme",
        account.id,
        "runs.read",
        "workspace:workspace_sales",
    )

    restarted.disable_user("tenant_acme", account.id)
    after_disable = SqlIdentityService(
        config=DatabaseConfig(url=database_url),
        password_hasher=PasswordHasher(salt="test_salt"),
    )
    assert after_disable.get_user("tenant_acme", account.id).status == "disabled"
    assert not after_disable.has_permission(
        "tenant_acme",
        account.id,
        "runs.read",
        "workspace:workspace_sales",
    )

    deleted = after_disable.delete_user("tenant_acme", account.id)
    after_delete = SqlIdentityService(
        config=DatabaseConfig(url=database_url),
        password_hasher=PasswordHasher(salt="test_salt"),
    )
    assert deleted.status == "deleted"
    assert after_delete.get_user("tenant_acme", account.id).status == "deleted"
    assert not after_delete.has_permission(
        "tenant_acme",
        account.id,
        "runs.read",
        "workspace:workspace_sales",
    )


def test_sql_identity_service_hydrates_postgresql_native_json_and_datetime_values():
    service = SqlIdentityService(config=DatabaseConfig(url="postgresql://example"))
    created_at = datetime(2026, 7, 3, 13, 20, tzinfo=timezone.utc)

    account = service._user_from_row(
        {
            "id": "user_owner",
            "tenant_id": "tenant_acme",
            "email": "owner@example.com",
            "display_name": "Owner",
            "password_hash": "hashed_password",
            "status": "active",
            "created_at": created_at,
        }
    )
    role = service._role_from_row(
        {
            "tenant_id": "tenant_acme",
            "id": "tenant_owner",
            "name": "Tenant Owner",
            "permissions": [
                {"action": "runs.create", "resource": "workspace:*"},
                {"action": "sandbox.execute", "resource": "workspace:*"},
            ],
        }
    )

    assert account.created_at == created_at
    assert [permission.action for permission in role.permissions] == [
        "runs.create",
        "sandbox.execute",
    ]


def test_sql_identity_service_get_user_uses_tenant_scoped_lookup(monkeypatch):
    executed_sql: list[str] = []
    created_at = datetime(2026, 7, 3, 13, 20, tzinfo=timezone.utc)

    class Result:
        def fetchone(self):
            return {
                "id": "user_owner",
                "tenant_id": "tenant_acme",
                "email": "owner@example.com",
                "display_name": "Owner",
                "password_hash": "hashed_password",
                "status": "active",
                "created_at": created_at,
            }

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, error_type, error, traceback):
            return None

        def execute(self, sql, params):
            executed_sql.append(" ".join(sql.split()))
            return Result()

    monkeypatch.setattr("taroai.identity.repository.connect_database", lambda _config: Connection())

    service = SqlIdentityService(config=DatabaseConfig(url="postgresql://example"))

    service.get_user("tenant_acme", "user_owner")

    assert executed_sql == ["SELECT * FROM users WHERE tenant_id = ? AND id = ?"]


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
