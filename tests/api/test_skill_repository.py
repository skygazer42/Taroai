from pathlib import Path
from datetime import datetime, timezone

from taroai.db import DatabaseConfig, MigrationRunner
from taroai.skills import SkillManifest, SkillRuntime, SkillType


def skill_manifest() -> SkillManifest:
    return SkillManifest(
        id="support.ticket_triage",
        version="1.0.0",
        name="Ticket Triage",
        description="Classify and route support tickets.",
        type=SkillType.WORKFLOW,
        owner="solutions/support",
        input_schema={
            "type": "object",
            "required": ["ticket_id"],
            "properties": {"ticket_id": {"type": "string"}},
        },
        output_schema={
            "type": "object",
            "required": ["priority"],
            "properties": {"priority": {"type": "string"}},
        },
        required_scopes=["support.read", "support.route"],
        runtime=SkillRuntime(sandbox="workflow", timeout_seconds=120),
        billing_meters=["tool_call_count"],
    )


def test_sql_skill_registry_persists_tenant_skill_lifecycle(tmp_path: Path):
    from taroai.skills.repository import SqlSkillRegistry

    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    registry = SqlSkillRegistry(config=DatabaseConfig(url=database_url))

    draft = registry.register_for_tenant(
        tenant_id="tenant_acme",
        created_by_user_id="user_1",
        manifest=skill_manifest(),
    )
    published = registry.publish("tenant_acme", "support.ticket_triage")

    restarted = SqlSkillRegistry(config=DatabaseConfig(url=database_url))
    entries = restarted.list_for_tenant("tenant_acme")

    assert draft.status == "draft"
    assert published.status == "published"
    assert len(entries) == 1
    assert entries[0].status == "published"
    assert entries[0].manifest.id == "support.ticket_triage"
    assert entries[0].manifest.required_scopes == ["support.read", "support.route"]
    assert restarted.list_for_tenant("tenant_other") == []


def test_sql_skill_registry_hydrates_postgresql_native_json_and_datetime_values():
    from taroai.skills.repository import SqlSkillRegistry

    registry = SqlSkillRegistry(config=DatabaseConfig(url="postgresql://example"))
    now = datetime(2026, 7, 3, 13, 30, tzinfo=timezone.utc)
    manifest = skill_manifest().model_dump(mode="json")

    entry = registry._entry_from_row(
        {
            "tenant_id": "tenant_acme",
            "manifest": manifest,
            "status": "published",
            "created_by_user_id": "user_owner",
            "created_at": now,
            "updated_at": now,
        }
    )

    assert entry.manifest.id == "support.ticket_triage"
    assert entry.status == "published"
    assert entry.created_at == now


def test_sql_skill_registry_persists_skill_version_history(tmp_path: Path):
    from taroai.skills.repository import SqlSkillRegistry

    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    registry = SqlSkillRegistry(config=DatabaseConfig(url=database_url))
    first_manifest = skill_manifest()
    second_manifest = first_manifest.model_copy(
        update={
            "version": "1.1.0",
            "description": "Classify, route, and summarize support tickets.",
        }
    )

    registry.register_for_tenant(
        tenant_id="tenant_acme",
        created_by_user_id="user_1",
        manifest=first_manifest,
    )
    registry.register_for_tenant(
        tenant_id="tenant_acme",
        created_by_user_id="user_2",
        manifest=second_manifest,
    )

    restarted = SqlSkillRegistry(config=DatabaseConfig(url=database_url))
    history = restarted.list_versions("tenant_acme", "support.ticket_triage")
    current = restarted.get_for_tenant("tenant_acme", "support.ticket_triage")

    assert [entry.manifest.version for entry in history] == ["1.0.0", "1.1.0"]
    assert [entry.created_by_user_id for entry in history] == ["user_1", "user_2"]
    assert history[1].manifest.description == "Classify, route, and summarize support tickets."
    assert current.manifest.version == "1.1.0"
    assert restarted.list_versions("tenant_other", "support.ticket_triage") == []


def test_sql_skill_registry_filters_workspace_and_private_visibility(tmp_path: Path):
    from taroai.skills.repository import SqlSkillRegistry

    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    registry = SqlSkillRegistry(config=DatabaseConfig(url=database_url))
    tenant_manifest = skill_manifest()
    workspace_manifest = SkillManifest.model_validate(
        {
            **tenant_manifest.model_dump(mode="json"),
            "id": "support.workspace_triage",
            "name": "Workspace Ticket Triage",
            "visibility": "workspace",
            "visible_to_workspace_ids": ["workspace_support"],
        }
    )
    department_manifest = SkillManifest.model_validate(
        {
            **tenant_manifest.model_dump(mode="json"),
            "id": "support.department_triage",
            "name": "Department Ticket Triage",
            "visibility": "department",
            "visible_to_department_ids": ["dept_support"],
        }
    )
    private_manifest = SkillManifest.model_validate(
        {
            **tenant_manifest.model_dump(mode="json"),
            "id": "support.private_triage",
            "name": "Private Ticket Triage",
            "visibility": "private",
            "visible_to_user_ids": ["user_owner"],
        }
    )
    registry.register_for_tenant("tenant_acme", "user_owner", tenant_manifest)
    registry.register_for_tenant("tenant_acme", "user_owner", workspace_manifest)
    registry.register_for_tenant("tenant_acme", "user_owner", department_manifest)
    registry.register_for_tenant("tenant_acme", "user_owner", private_manifest)

    restarted = SqlSkillRegistry(config=DatabaseConfig(url=database_url))
    owner_support = restarted.list_visible_for_tenant(
        "tenant_acme",
        user_id="user_owner",
        workspace_id="workspace_support",
        department_id="dept_support",
    )
    other_support = restarted.list_visible_for_tenant(
        "tenant_acme",
        user_id="user_other",
        workspace_id="workspace_support",
        department_id="dept_support",
    )
    owner_sales = restarted.list_visible_for_tenant(
        "tenant_acme",
        user_id="user_owner",
        workspace_id="workspace_sales",
        department_id="dept_sales",
    )

    assert [entry.manifest.id for entry in owner_support] == [
        "support.ticket_triage",
        "support.workspace_triage",
        "support.department_triage",
        "support.private_triage",
    ]
    assert [entry.manifest.id for entry in other_support] == [
        "support.ticket_triage",
        "support.workspace_triage",
        "support.department_triage",
    ]
    assert [entry.manifest.id for entry in owner_sales] == [
        "support.ticket_triage",
        "support.private_triage",
    ]


def test_sql_skill_registry_reports_marketplace_analytics(tmp_path: Path):
    from taroai.skills.repository import SqlSkillRegistry

    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    registry = SqlSkillRegistry(config=DatabaseConfig(url=database_url))
    tenant_manifest = skill_manifest()
    workspace_manifest = SkillManifest.model_validate(
        {
            **tenant_manifest.model_dump(mode="json"),
            "id": "support.workspace_triage",
            "name": "Workspace Ticket Triage",
            "visibility": "workspace",
            "visible_to_workspace_ids": ["workspace_support"],
        }
    )
    private_manifest = SkillManifest.model_validate(
        {
            **tenant_manifest.model_dump(mode="json"),
            "id": "support.private_triage",
            "name": "Private Ticket Triage",
            "visibility": "private",
            "visible_to_user_ids": ["user_owner"],
        }
    )
    registry.register_for_tenant("tenant_acme", "user_owner", tenant_manifest)
    registry.register_for_tenant("tenant_acme", "user_owner", workspace_manifest)
    registry.publish("tenant_acme", "support.workspace_triage")
    registry.install_for_workspace(
        tenant_id="tenant_acme",
        workspace_id="workspace_support",
        skill_id="support.workspace_triage",
        installed_by_user_id="user_owner",
    )
    registry.register_for_tenant("tenant_acme", "user_owner", private_manifest)
    registry.publish("tenant_acme", "support.private_triage")
    registry.disable("tenant_acme", "support.private_triage")

    restarted = SqlSkillRegistry(config=DatabaseConfig(url=database_url))
    analytics = restarted.get_marketplace_analytics("tenant_acme")

    assert analytics.tenant_id == "tenant_acme"
    assert analytics.total_skills == 3
    assert analytics.total_versions == 3
    assert analytics.total_installations == 1
    assert analytics.status_counts == {"disabled": 1, "draft": 1, "published": 1}
    assert analytics.visibility_counts == {"private": 1, "tenant": 1, "workspace": 1}
    assert analytics.installations_by_workspace == {"workspace_support": 1}


def test_sql_skill_registry_persists_workspace_installations(tmp_path: Path):
    from taroai.skills.repository import SqlSkillRegistry

    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    registry = SqlSkillRegistry(config=DatabaseConfig(url=database_url))
    registry.register_for_tenant(
        tenant_id="tenant_acme",
        created_by_user_id="user_1",
        manifest=skill_manifest(),
    )
    registry.publish("tenant_acme", "support.ticket_triage")
    installed = registry.install_for_workspace(
        tenant_id="tenant_acme",
        workspace_id="workspace_support",
        skill_id="support.ticket_triage",
        installed_by_user_id="user_1",
    )
    registry.disable_for_workspace(
        tenant_id="tenant_acme",
        workspace_id="workspace_support",
        skill_id="support.ticket_triage",
    )

    restarted = SqlSkillRegistry(config=DatabaseConfig(url=database_url))
    disabled = restarted.list_for_workspace("tenant_acme", "workspace_support")[0]
    enabled = restarted.enable_for_workspace(
        tenant_id="tenant_acme",
        workspace_id="workspace_support",
        skill_id="support.ticket_triage",
    )

    assert installed.status == "enabled"
    assert disabled.status == "disabled"
    assert disabled.skill_id == "support.ticket_triage"
    assert enabled.status == "enabled"
    assert restarted.list_for_workspace("tenant_other", "workspace_support") == []
