from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from taroai.app import create_app
from taroai.db import DatabaseConfig, MigrationRunner
from taroai.identity import (
    InMemoryIdentityService,
    PasswordHasher,
    Permission,
    Role,
    UserAccountCreate,
)
from taroai.licensing import (
    Entitlement,
    LicenseKey,
    LicenseService,
    LicensedFeature,
)
from taroai.skills import InMemorySkillRegistry
from taroai.solution_packs import (
    InMemorySolutionPackRegistry,
    SolutionPackInstallationStatus,
    SolutionPackManifest,
    SolutionPackService,
    SqlSolutionPackRegistry,
)
from taroai.store import InMemoryControlPlaneStore


def create_solution_pack_admin_identity():
    identity = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="solution-admin@example.com",
            display_name="Solution Admin",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_solution_pack_admin",
            name="Solution Pack Admin",
            permissions=[
                Permission(action="solution_packs.read", resource="tenant:tenant_acme"),
                Permission(action="solution_packs.manage", resource="tenant:tenant_acme"),
                Permission(action="solution_packs.install", resource="tenant:tenant_acme"),
                Permission(action="skills.read", resource="tenant:tenant_acme"),
                Permission(action="audit.read", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_solution_pack_admin")
    return identity, account


def skill_manifest_payload(skill_id: str = "sales.crm_lookup") -> dict:
    return {
        "id": skill_id,
        "version": "1.0.0",
        "name": "CRM Lookup",
        "description": "Look up account context from CRM.",
        "type": "api_skill",
        "owner": "solutions/sales",
        "input_schema": {
            "type": "object",
            "required": ["account_id"],
            "properties": {"account_id": {"type": "string"}},
        },
        "output_schema": {
            "type": "object",
            "required": ["account"],
            "properties": {"account": {"type": "object"}},
        },
        "required_scopes": ["crm.read"],
        "risk_level": "medium",
        "runtime": {"sandbox": "api", "timeout_seconds": 60},
        "billing_meters": ["tool_call_count"],
    }


def solution_pack_payload() -> dict:
    return {
        "id": "sales.renewal_ops",
        "version": "1.0.0",
        "name": "Renewal Operations",
        "description": "Starter pack for enterprise renewal workflows.",
        "industry": "software",
        "use_cases": ["renewal_risk", "account_briefing"],
        "skills": [
            skill_manifest_payload("sales.crm_lookup"),
            {
                **skill_manifest_payload("sales.renewal_checklist"),
                "name": "Renewal Checklist",
                "description": "Apply approved renewal checklist.",
                "type": "workflow_skill",
                "runtime": {"sandbox": "workflow", "timeout_seconds": 120},
            },
        ],
        "success_metrics": ["active_workspaces", "skills_installed"],
        "rollout_checklist": ["assign_owner", "connect_crm"],
    }


def activated_solution_pack_license() -> LicenseService:
    license_service = LicenseService(runtime_enforcement_enabled=True)
    validation = license_service.validate_license(
        LicenseKey(
            id="license_acme_solution_packs",
            tenant_id="tenant_acme",
            customer_name="Acme Inc",
            issued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
            deployment_modes=["private"],
            entitlements=[
                Entitlement(feature=LicensedFeature.SOLUTION_PACKS),
                Entitlement(feature=LicensedFeature.AUDIT_RETENTION_DAYS, limit=365),
            ],
        ),
        deployment_mode="private",
        now=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    license_service.activate_validation(validation)
    return license_service


def test_solution_pack_service_installs_pack_skills_into_workspaces():
    pack_registry = InMemorySolutionPackRegistry()
    skill_registry = InMemorySkillRegistry()
    service = SolutionPackService(
        pack_registry=pack_registry,
        skill_registry=skill_registry,
    )
    manifest = SolutionPackManifest.model_validate(solution_pack_payload())

    pack_registry.register_for_tenant(
        tenant_id="tenant_acme",
        created_by_user_id="user_admin",
        manifest=manifest,
    )
    pack_registry.publish("tenant_acme", "sales.renewal_ops")
    installation = service.install_for_tenant(
        tenant_id="tenant_acme",
        pack_id="sales.renewal_ops",
        workspace_ids=["workspace_sales", "workspace_success"],
        installed_by_user_id="user_admin",
    )

    assert installation.pack_id == "sales.renewal_ops"
    assert installation.version == "1.0.0"
    assert installation.workspace_ids == ["workspace_sales", "workspace_success"]
    assert installation.installed_skill_ids == [
        "sales.crm_lookup",
        "sales.renewal_checklist",
    ]
    assert [
        item.skill_id
        for item in skill_registry.list_for_workspace("tenant_acme", "workspace_sales")
    ] == ["sales.crm_lookup", "sales.renewal_checklist"]
    assert skill_registry.get_for_tenant("tenant_acme", "sales.crm_lookup").status.value == "published"


def test_solution_pack_service_previews_install_without_mutating_registries():
    pack_registry = InMemorySolutionPackRegistry()
    skill_registry = InMemorySkillRegistry()
    service = SolutionPackService(
        pack_registry=pack_registry,
        skill_registry=skill_registry,
    )
    pack_registry.register_for_tenant(
        tenant_id="tenant_acme",
        created_by_user_id="user_admin",
        manifest=SolutionPackManifest.model_validate(solution_pack_payload()),
    )
    pack_registry.publish("tenant_acme", "sales.renewal_ops")

    preview = service.preview_install(
        tenant_id="tenant_acme",
        pack_id="sales.renewal_ops",
        workspace_ids=["workspace_sales"],
        installed_by_user_id="user_admin",
    )

    assert preview.dry_run is True
    assert preview.can_install is True
    assert preview.pack_id == "sales.renewal_ops"
    assert preview.version == "1.0.0"
    assert preview.conflicts == []
    assert preview.missing_dependencies == []
    assert preview.required_approvals == []
    assert [
        (action.resource_type, action.resource_id, action.action, action.workspace_id)
        for action in preview.actions
    ] == [
        ("skill", "sales.crm_lookup", "register", None),
        ("skill", "sales.crm_lookup", "publish", None),
        ("skill", "sales.crm_lookup", "install", "workspace_sales"),
        ("skill", "sales.renewal_checklist", "register", None),
        ("skill", "sales.renewal_checklist", "publish", None),
        ("skill", "sales.renewal_checklist", "install", "workspace_sales"),
    ]
    assert skill_registry.list_for_tenant("tenant_acme") == []
    assert skill_registry.list_for_workspace("tenant_acme", "workspace_sales") == []


def test_solution_pack_service_reports_workspace_install_conflicts():
    pack_registry = InMemorySolutionPackRegistry()
    skill_registry = InMemorySkillRegistry()
    service = SolutionPackService(
        pack_registry=pack_registry,
        skill_registry=skill_registry,
    )
    manifest = SolutionPackManifest.model_validate(solution_pack_payload())
    pack_registry.register_for_tenant(
        tenant_id="tenant_acme",
        created_by_user_id="user_admin",
        manifest=manifest,
    )
    pack_registry.publish("tenant_acme", "sales.renewal_ops")
    skill_registry.register_for_tenant(
        tenant_id="tenant_acme",
        created_by_user_id="user_admin",
        manifest=manifest.skills[0],
    )
    skill_registry.publish("tenant_acme", "sales.crm_lookup")
    skill_registry.install_for_workspace(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        skill_id="sales.crm_lookup",
        installed_by_user_id="user_admin",
    )

    preview = service.preview_install(
        tenant_id="tenant_acme",
        pack_id="sales.renewal_ops",
        workspace_ids=["workspace_sales"],
        installed_by_user_id="user_admin",
    )

    assert preview.can_install is False
    assert [conflict.kind for conflict in preview.conflicts] == [
        "workspace_skill_already_installed"
    ]
    assert preview.conflicts[0].resource_id == "sales.crm_lookup"
    assert preview.conflicts[0].workspace_id == "workspace_sales"
    assert "account_id" not in preview.model_dump_json()


def test_solution_pack_service_installs_high_risk_skills_disabled_by_default():
    pack_registry = InMemorySolutionPackRegistry()
    skill_registry = InMemorySkillRegistry()
    service = SolutionPackService(
        pack_registry=pack_registry,
        skill_registry=skill_registry,
    )
    payload = solution_pack_payload()
    payload["skills"][0]["risk_level"] = "high"
    pack_registry.register_for_tenant(
        tenant_id="tenant_acme",
        created_by_user_id="user_admin",
        manifest=SolutionPackManifest.model_validate(payload),
    )
    pack_registry.publish("tenant_acme", "sales.renewal_ops")

    service.install_for_tenant(
        tenant_id="tenant_acme",
        pack_id="sales.renewal_ops",
        workspace_ids=["workspace_sales"],
        installed_by_user_id="user_admin",
    )

    installations = {
        installation.skill_id: installation
        for installation in skill_registry.list_for_workspace(
            "tenant_acme",
            "workspace_sales",
        )
    }
    assert installations["sales.crm_lookup"].status.value == "disabled"
    assert installations["sales.renewal_checklist"].status.value == "enabled"


def test_solution_pack_service_rolls_back_workspace_skill_installations_and_audits():
    pack_registry = InMemorySolutionPackRegistry()
    skill_registry = InMemorySkillRegistry()
    audit_store = InMemoryControlPlaneStore()
    service = SolutionPackService(
        pack_registry=pack_registry,
        skill_registry=skill_registry,
        audit_store=audit_store,
    )
    pack_registry.register_for_tenant(
        tenant_id="tenant_acme",
        created_by_user_id="user_admin",
        manifest=SolutionPackManifest.model_validate(solution_pack_payload()),
    )
    pack_registry.publish("tenant_acme", "sales.renewal_ops")
    service.install_for_tenant(
        tenant_id="tenant_acme",
        pack_id="sales.renewal_ops",
        workspace_ids=["workspace_sales"],
        installed_by_user_id="user_admin",
    )

    rollback = service.rollback_installation(
        tenant_id="tenant_acme",
        pack_id="sales.renewal_ops",
        rolled_back_by_user_id="user_admin",
        reason_code="pilot_reset",
    )

    assert rollback.pack_id == "sales.renewal_ops"
    assert rollback.status == SolutionPackInstallationStatus.ROLLED_BACK
    assert rollback.disabled_skill_ids == [
        "sales.crm_lookup",
        "sales.renewal_checklist",
    ]
    assert {
        installation.skill_id: installation.status.value
        for installation in skill_registry.list_for_workspace(
            "tenant_acme",
            "workspace_sales",
        )
    } == {
        "sales.crm_lookup": "disabled",
        "sales.renewal_checklist": "disabled",
    }
    audits = audit_store.list_audit_events("tenant_acme")
    assert audits[-1].event_type == "solution_pack.rollback"
    assert audits[-1].metadata == {
        "pack_id": "sales.renewal_ops",
        "version": "1.0.0",
        "workspace_count": 1,
        "disabled_skill_count": 2,
        "rolled_back_by_user_id": "user_admin",
        "reason_code": "pilot_reset",
    }


def test_solution_pack_api_registers_publishes_installs_and_audits_with_license():
    identity, account = create_solution_pack_admin_identity()
    store = InMemoryControlPlaneStore()
    client = TestClient(
        create_app(
            store=store,
            identity_service=identity,
            license_service=activated_solution_pack_license(),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}

    created = client.post("/api/solution-packs", headers=headers, json=solution_pack_payload())
    published = client.post(
        "/api/solution-packs/sales.renewal_ops/publish",
        headers=headers,
    )
    dry_run = client.post(
        "/api/solution-packs/sales.renewal_ops/install",
        headers=headers,
        json={"workspace_ids": ["workspace_sales"], "dry_run": True},
    )
    workspace_skills_before_install = client.get(
        "/api/workspaces/workspace_sales/skills",
        headers=headers,
    )
    installed = client.post(
        "/api/solution-packs/sales.renewal_ops/install",
        headers=headers,
        json={"workspace_ids": ["workspace_sales"]},
    )
    workspace_skills = client.get("/api/workspaces/workspace_sales/skills", headers=headers)
    audits = client.get(
        "/api/audit-events?event_type=solution_pack.installed",
        headers=headers,
    )

    assert created.status_code == 201
    assert published.status_code == 200
    assert dry_run.status_code == 200
    assert dry_run.json()["dry_run"] is True
    assert dry_run.json()["can_install"] is True
    assert workspace_skills_before_install.json() == []
    assert installed.status_code == 201
    assert installed.json()["installed_skill_ids"] == [
        "sales.crm_lookup",
        "sales.renewal_checklist",
    ]
    assert [item["skill_id"] for item in workspace_skills.json()] == [
        "sales.crm_lookup",
        "sales.renewal_checklist",
    ]
    assert audits.status_code == 200
    assert audits.json()[0]["metadata"]["pack_id"] == "sales.renewal_ops"
    assert audits.json()[0]["metadata"]["version"] == "1.0.0"
    assert audits.json()[0]["metadata"]["workspace_count"] == 1
    assert audits.json()[0]["metadata"]["installed_skill_count"] == 2
    assert "account_id" not in str(audits.json())


def test_solution_pack_api_requires_solution_pack_entitlement_when_enabled():
    identity, account = create_solution_pack_admin_identity()
    license_service = LicenseService(runtime_enforcement_enabled=True)
    validation = license_service.validate_license(
        LicenseKey(
            id="license_acme_no_solution_packs",
            tenant_id="tenant_acme",
            customer_name="Acme Inc",
            issued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
            deployment_modes=["private"],
            entitlements=[
                Entitlement(feature=LicensedFeature.AUDIT_RETENTION_DAYS, limit=365)
            ],
        ),
        deployment_mode="private",
        now=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    license_service.activate_validation(validation)
    client = TestClient(
        create_app(
            identity_service=identity,
            license_service=license_service,
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}
    client.post("/api/solution-packs", headers=headers, json=solution_pack_payload())
    client.post("/api/solution-packs/sales.renewal_ops/publish", headers=headers)

    installed = client.post(
        "/api/solution-packs/sales.renewal_ops/install",
        headers=headers,
        json={"workspace_ids": ["workspace_sales"]},
    )

    assert installed.status_code == 403
    assert installed.json()["code"] == "license_entitlement_denied"
    assert "solution_packs" in installed.json()["message"]


def test_sql_solution_pack_registry_persists_pack_and_installation(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'solution-packs.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    registry = SqlSolutionPackRegistry(config=DatabaseConfig(url=database_url))
    manifest = SolutionPackManifest.model_validate(solution_pack_payload())

    registry.register_for_tenant(
        tenant_id="tenant_acme",
        created_by_user_id="user_admin",
        manifest=manifest,
    )
    registry.publish("tenant_acme", "sales.renewal_ops")
    registry.record_installation(
        tenant_id="tenant_acme",
        pack_id="sales.renewal_ops",
        version="1.0.0",
        workspace_ids=["workspace_sales"],
        installed_skill_ids=["sales.crm_lookup"],
        installed_by_user_id="user_admin",
    )
    restarted = SqlSolutionPackRegistry(config=DatabaseConfig(url=database_url))

    entry = restarted.get_for_tenant("tenant_acme", "sales.renewal_ops")
    installation = restarted.get_installation("tenant_acme", "sales.renewal_ops")

    assert entry.manifest.name == "Renewal Operations"
    assert entry.status.value == "published"
    assert installation.workspace_ids == ["workspace_sales"]
    assert installation.installed_skill_ids == ["sales.crm_lookup"]

    restarted.update_installation_status(
        "tenant_acme",
        "sales.renewal_ops",
        SolutionPackInstallationStatus.ROLLED_BACK,
    )
    persisted_status = SqlSolutionPackRegistry(
        config=DatabaseConfig(url=database_url)
    ).get_installation("tenant_acme", "sales.renewal_ops")
    assert persisted_status.status == SolutionPackInstallationStatus.ROLLED_BACK


def test_sql_solution_pack_registry_hydrates_postgresql_values():
    registry = SqlSolutionPackRegistry(
        config=DatabaseConfig(url="postgresql://example")
    )
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)

    entry = registry._entry_from_row(
        {
            "tenant_id": "tenant_acme",
            "manifest": solution_pack_payload(),
            "status": "published",
            "created_by_user_id": "user_admin",
            "created_at": now,
            "updated_at": now,
        }
    )
    installation = registry._installation_from_row(
        {
            "tenant_id": "tenant_acme",
            "pack_id": "sales.renewal_ops",
            "version": "1.0.0",
            "workspace_ids": {"items": ["workspace_sales"]},
            "installed_skill_ids": {"items": ["sales.crm_lookup"]},
            "status": "installed",
            "installed_by_user_id": "user_admin",
            "created_at": now,
            "updated_at": now,
        }
    )

    assert entry.manifest.name == "Renewal Operations"
    assert entry.created_at == now
    assert installation.workspace_ids == ["workspace_sales"]
    assert installation.installed_skill_ids == ["sales.crm_lookup"]
    assert installation.created_at == now
