from pathlib import Path

from fastapi.testclient import TestClient

from taroai.app import create_app
from taroai.config import Settings
from taroai.db import DatabaseConfig, MigrationRunner, SqlControlPlaneRepository
from taroai.identity import (
    PasswordHasher,
    Permission,
    Role,
    SqlIdentityService,
    UserAccountCreate,
)
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
    assert checks["skills_runtime"].status == ReadinessCheckStatus.WARNING


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
    model_providers = client.get(
        "/api/model-providers",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    model_policy_scopes = client.get(
        "/api/model-policies/scopes",
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
    assert model_providers.status_code == 200
    assert model_providers.json() == []
    assert model_policy_scopes.status_code == 200

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


def test_tenant_bootstrap_upgrades_existing_owner_role_permissions(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    prepare_database(database_url)
    identity = SqlIdentityService(
        config=DatabaseConfig(url=database_url),
        password_hasher=PasswordHasher(salt="test_salt"),
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="tenant_owner",
            name="Tenant Owner",
            permissions=[
                Permission(action="audit.read", resource="tenant:tenant_acme"),
            ],
        )
    )
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

    created = client.post(
        "/api/tenants/bootstrap",
        headers={"X-Bootstrap-Token": "bootstrap_secret"},
        json={
            "tenant_id": "tenant_acme",
            "owner_email": "owner@example.com",
            "owner_display_name": "Owner",
            "owner_password": "correct horse battery staple",
        },
    )

    role = identity.get_role("tenant_acme", "tenant_owner")
    permissions = {
        (permission.action, permission.resource)
        for permission in role.permissions
    }

    assert created.status_code == 201
    assert ("audit.read", "tenant:tenant_acme") in permissions
    assert ("model_policy.read", "tenant:tenant_acme") in permissions
    assert ("model_policy.manage", "tenant:tenant_acme") in permissions
    assert ("model_policy.approve", "tenant:tenant_acme") in permissions
    assert ("model_providers.read", "tenant:tenant_acme") in permissions
    assert ("model_providers.manage", "tenant:tenant_acme") in permissions
    assert ("model_providers.approve", "tenant:tenant_acme") in permissions
    assert ("connectors.read", "tenant:tenant_acme") in permissions
    assert ("connectors.manage", "tenant:tenant_acme") in permissions
    assert ("connectors.invoke", "tenant:tenant_acme") in permissions
    assert ("triggers.read", "tenant:tenant_acme") in permissions
    assert ("triggers.manage", "tenant:tenant_acme") in permissions
    assert ("triggers.invoke", "tenant:tenant_acme") in permissions


def test_tenant_bootstrap_endpoint_is_idempotent_and_seeds_starter_resources(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    prepare_database(database_url)
    settings = Settings(
        database_url=database_url,
        identity_service_backend="sql",
        control_plane_store_backend="sql",
        knowledge_service_backend="sql",
        skill_registry_backend="sql",
        password_hash_salt="test_salt",
        access_token_secret="unit_test_secret",
        dev_request_headers_enabled=False,
        tenant_bootstrap_token="bootstrap_secret",
        tenant_quota_profile="poc",
        _env_file=None,
    )
    client = TestClient(create_app(settings=settings))
    payload = {
        "tenant_slug": "acme",
        "owner_email": "owner@example.com",
        "owner_display_name": "Owner",
        "owner_password": "correct horse battery staple",
    }

    created = client.post(
        "/api/tenants/bootstrap",
        headers={"X-Bootstrap-Token": "bootstrap_secret"},
        json=payload,
    )
    repeated = client.post(
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
    workspace_skills = client.get(
        f"/api/workspaces/{created.json()['starter_workspace_id']}/skills",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    skill_analytics = client.get(
        "/api/skills/analytics",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert created.status_code == 201
    assert repeated.status_code == 201
    assert login.status_code == 200
    created_body = created.json()
    repeated_body = repeated.json()
    assert created_body["tenant_id"] == "tenant_acme"
    assert created_body["tenant_slug"] == "acme"
    assert created_body["starter_workspace_id"] == "workspace_acme"
    assert created_body["owner_user_id"] == repeated_body["owner_user_id"]
    assert created_body["starter_knowledge_base_id"] == repeated_body["starter_knowledge_base_id"]
    assert created_body["starter_skill_ids"] == repeated_body["starter_skill_ids"]
    assert created_body["starter_skill_ids"] == []

    readiness_checks = {check["name"]: check for check in readiness.json()["checks"]}
    assert readiness.status_code == 200
    assert readiness_checks["skills_runtime"]["status"] == "passed"
    assert readiness_checks["knowledge_spaces"]["status"] == "passed"
    assert workspace_skills.status_code == 200
    assert [skill["skill_id"] for skill in workspace_skills.json()] == created_body["starter_skill_ids"]
    assert skill_analytics.status_code == 200
    assert skill_analytics.json()["total_skills"] == len(created_body["starter_skill_ids"])
    assert skill_analytics.json()["total_installations"] == len(created_body["starter_skill_ids"])
