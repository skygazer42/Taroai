from pathlib import Path

from fastapi.testclient import TestClient

from taroai.app import create_app
from taroai.config import Settings
from taroai.db import DatabaseConfig, MigrationRunner, SqlControlPlaneRepository
from taroai.identity import PasswordHasher, Role, SqlIdentityService, UserAccountCreate
from taroai.onboarding import ReadinessCheckStatus, TenantReadinessService


def prepare_database(database_url: str) -> None:
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()


def create_ready_identity(database_url: str):
    identity = SqlIdentityService(
        config=DatabaseConfig(url=database_url),
        password_hasher=PasswordHasher(salt="test_salt"),
    )
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="owner@example.com",
            display_name="Owner",
            password="correct horse battery staple",
        )
    )
    identity.create_role(Role(tenant_id="tenant_acme", id="tenant_owner", name="Tenant Owner"))
    identity.assign_role("tenant_acme", account.id, "tenant_owner")
    return identity, account


def test_tenant_readiness_service_reports_ready_for_seeded_tenant(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    prepare_database(database_url)
    identity, account = create_ready_identity(database_url)
    store = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    settings = Settings(
        database_url=database_url,
        identity_service_backend="sql",
        control_plane_store_backend="sql",
        dev_request_headers_enabled=False,
        tenant_quota_profile="poc",
        _env_file=None,
    )
    service = TenantReadinessService(
        identity_service=identity,
        store=store,
        settings=settings,
        job_queue=None,
    )

    report = service.check_tenant_readiness("tenant_acme", account.id)
    checks = {check.name: check for check in report.checks}

    assert report.ready
    assert report.blocking_checks == []
    assert checks["owner_user"].status == ReadinessCheckStatus.PASSED
    assert checks["owner_roles"].metadata["role_ids"] == ["tenant_owner"]
    assert checks["auth_mode"].status == ReadinessCheckStatus.PASSED
    assert checks["quota_profile"].metadata["profile"] == "poc"
    assert checks["starter_skills"].status == ReadinessCheckStatus.WARNING


def test_tenant_readiness_service_reports_missing_owner_and_role(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    prepare_database(database_url)
    identity = SqlIdentityService(
        config=DatabaseConfig(url=database_url),
        password_hasher=PasswordHasher(salt="test_salt"),
    )
    store = SqlControlPlaneRepository(config=DatabaseConfig(url=database_url))
    service = TenantReadinessService(
        identity_service=identity,
        store=store,
        settings=Settings(
            database_url=database_url,
            dev_request_headers_enabled=False,
            _env_file=None,
        ),
        job_queue=None,
    )

    report = service.check_tenant_readiness("tenant_acme", "user_missing")

    assert not report.ready
    assert "owner_user" in report.blocking_checks
    assert "owner_roles" in report.blocking_checks


def test_tenant_readiness_endpoint_uses_authenticated_context(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    prepare_database(database_url)
    create_ready_identity(database_url)
    settings = Settings(
        database_url=database_url,
        identity_service_backend="sql",
        control_plane_store_backend="sql",
        password_hash_salt="test_salt",
        access_token_secret="unit_test_secret",
        dev_request_headers_enabled=False,
        tenant_quota_profile="poc",
        _env_file=None,
    )
    client = TestClient(create_app(settings=settings))
    login = client.post(
        "/api/auth/login",
        json={
            "tenant_id": "tenant_acme",
            "email": "owner@example.com",
            "password": "correct horse battery staple",
        },
    )

    response = client.get(
        "/api/tenants/current/readiness",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["tenant_id"] == "tenant_acme"
    assert body["ready"] is True
    assert body["checks"][0]["name"] == "owner_user"


def test_tenant_bootstrap_endpoint_creates_owner_role_and_safe_audit(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    prepare_database(database_url)
    settings = Settings(
        database_url=database_url,
        identity_service_backend="sql",
        control_plane_store_backend="sql",
        password_hash_salt="test_salt",
        access_token_secret="unit_test_secret",
        dev_request_headers_enabled=False,
        tenant_bootstrap_token="bootstrap_secret",
        tenant_quota_profile="poc",
        _env_file=None,
    )
    client = TestClient(create_app(settings=settings))
    payload = {
        "tenant_id": "tenant_acme",
        "owner_email": "owner@example.com",
        "owner_display_name": "Owner",
        "owner_password": "correct horse battery staple",
    }

    denied = client.post(
        "/api/tenants/bootstrap",
        headers={"X-Bootstrap-Token": "wrong_secret"},
        json=payload,
    )
    created = client.post(
        "/api/tenants/bootstrap",
        headers={"X-Bootstrap-Token": "bootstrap_secret"},
        json=payload,
    )
    login = client.post(
        "/api/auth/login",
        json={
            "tenant_id": "tenant_acme",
            "email": "owner@example.com",
            "password": "correct horse battery staple",
        },
    )
    access_token = login.json()["access_token"]
    readiness = client.get(
        "/api/tenants/current/readiness",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    audits = client.get(
        "/api/audit-events",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert denied.status_code == 403
    assert denied.json()["code"] == "tenant_access_denied"
    assert created.status_code == 201
    created_body = created.json()
    assert created_body["tenant_id"] == "tenant_acme"
    assert created_body["owner_role_id"] == "tenant_owner"
    assert created_body["owner_user_id"].startswith("user_")
    assert created_body["readiness"]["ready"] is True
    assert login.status_code == 200
    assert readiness.status_code == 200
    assert readiness.json()["ready"] is True

    audit_events = audits.json()
    bootstrap_events = [
        event for event in audit_events if event["event_type"] == "tenant.bootstrap.completed"
    ]
    assert len(bootstrap_events) == 1
    metadata = bootstrap_events[0]["metadata"]
    assert metadata["owner_user_id"] == created_body["owner_user_id"]
    assert metadata["owner_role_id"] == "tenant_owner"
    assert metadata["permissions_count"] > 0
    assert "password" not in str(metadata).lower()
    assert "correct horse battery staple" not in str(audit_events)
