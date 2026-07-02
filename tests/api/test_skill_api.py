from fastapi.testclient import TestClient

from taroai.app import create_app
from taroai.config import Settings
from taroai.identity import (
    InMemoryIdentityService,
    PasswordHasher,
    Permission,
    Role,
    UserAccountCreate,
)
from taroai.skills import InMemorySkillRegistry, SkillManifest


def create_skill_admin_identity():
    identity = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="admin@example.com",
            display_name="Admin",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_skill_admin",
            name="Skill Admin",
            permissions=[
                Permission(action="skills.read", resource="tenant:tenant_acme"),
                Permission(action="skills.publish", resource="tenant:tenant_acme"),
                Permission(action="skills.install", resource="tenant:tenant_acme"),
                Permission(action="audit.read", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_skill_admin")
    return identity, account


def skill_manifest_payload() -> dict:
    return {
        "id": "sales.crm_lookup",
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
        "runtime": {"sandbox": "api", "timeout_seconds": 60},
        "billing_meters": ["tool_call_count"],
    }


def create_skill_admin_with_user(
    identity: InMemoryIdentityService,
    email: str,
    role_id: str,
):
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email=email,
            display_name=email.split("@")[0],
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id=role_id,
            name=role_id,
            permissions=[
                Permission(action="skills.read", resource="tenant:tenant_acme"),
                Permission(action="skills.publish", resource="tenant:tenant_acme"),
                Permission(action="skills.install", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, role_id)
    return account


def test_in_memory_skill_registry_filters_workspace_and_private_visibility():
    registry = InMemorySkillRegistry()
    tenant_manifest = SkillManifest.model_validate(skill_manifest_payload())
    workspace_manifest = SkillManifest.model_validate(
        {
            **skill_manifest_payload(),
            "id": "sales.workspace_only",
            "name": "Workspace CRM Lookup",
            "visibility": "workspace",
            "visible_to_workspace_ids": ["workspace_sales"],
        }
    )
    department_manifest = SkillManifest.model_validate(
        {
            **skill_manifest_payload(),
            "id": "sales.department_only",
            "name": "Department CRM Lookup",
            "visibility": "department",
            "visible_to_department_ids": ["dept_sales"],
        }
    )
    private_manifest = SkillManifest.model_validate(
        {
            **skill_manifest_payload(),
            "id": "sales.private_lookup",
            "name": "Private CRM Lookup",
            "visibility": "private",
            "visible_to_user_ids": ["user_owner"],
        }
    )
    registry.register_for_tenant("tenant_acme", "user_owner", tenant_manifest)
    registry.register_for_tenant("tenant_acme", "user_owner", workspace_manifest)
    registry.register_for_tenant("tenant_acme", "user_owner", department_manifest)
    registry.register_for_tenant("tenant_acme", "user_owner", private_manifest)

    owner_sales = registry.list_visible_for_tenant(
        "tenant_acme",
        user_id="user_owner",
        workspace_id="workspace_sales",
        department_id="dept_sales",
    )
    other_sales = registry.list_visible_for_tenant(
        "tenant_acme",
        user_id="user_other",
        workspace_id="workspace_sales",
        department_id="dept_sales",
    )
    owner_support = registry.list_visible_for_tenant(
        "tenant_acme",
        user_id="user_owner",
        workspace_id="workspace_support",
        department_id="dept_support",
    )

    assert [entry.manifest.id for entry in owner_sales] == [
        "sales.crm_lookup",
        "sales.workspace_only",
        "sales.department_only",
        "sales.private_lookup",
    ]
    assert [entry.manifest.id for entry in other_sales] == [
        "sales.crm_lookup",
        "sales.workspace_only",
        "sales.department_only",
    ]
    assert [entry.manifest.id for entry in owner_support] == [
        "sales.crm_lookup",
        "sales.private_lookup",
    ]


def test_in_memory_skill_registry_reports_marketplace_analytics():
    registry = InMemorySkillRegistry()
    tenant_manifest = SkillManifest.model_validate(skill_manifest_payload())
    workspace_manifest = SkillManifest.model_validate(
        {
            **skill_manifest_payload(),
            "id": "sales.workspace_only",
            "name": "Workspace CRM Lookup",
            "visibility": "workspace",
            "visible_to_workspace_ids": ["workspace_sales"],
        }
    )
    private_manifest = SkillManifest.model_validate(
        {
            **skill_manifest_payload(),
            "id": "sales.private_lookup",
            "name": "Private CRM Lookup",
            "visibility": "private",
            "visible_to_user_ids": ["user_owner"],
        }
    )
    registry.register_for_tenant("tenant_acme", "user_owner", tenant_manifest)
    registry.register_for_tenant("tenant_acme", "user_owner", workspace_manifest)
    registry.register_for_tenant("tenant_acme", "user_owner", private_manifest)
    registry.publish("tenant_acme", "sales.workspace_only")
    registry.install_for_workspace(
        tenant_id="tenant_acme",
        workspace_id="workspace_sales",
        skill_id="sales.workspace_only",
        installed_by_user_id="user_owner",
    )
    registry.publish("tenant_acme", "sales.private_lookup")
    registry.disable("tenant_acme", "sales.private_lookup")

    analytics = registry.get_marketplace_analytics("tenant_acme")

    assert analytics.tenant_id == "tenant_acme"
    assert analytics.total_skills == 3
    assert analytics.total_versions == 3
    assert analytics.total_installations == 1
    assert analytics.status_counts == {"disabled": 1, "draft": 1, "published": 1}
    assert analytics.visibility_counts == {"private": 1, "tenant": 1, "workspace": 1}
    assert analytics.installations_by_workspace == {"workspace_sales": 1}


def test_in_memory_skill_registry_lists_skill_version_history():
    registry = InMemorySkillRegistry()
    first_manifest = skill_manifest_payload()
    second_manifest = {
        **skill_manifest_payload(),
        "version": "1.1.0",
        "description": "Look up account context and renewal risk from CRM.",
    }

    registry.register_for_tenant(
        tenant_id="tenant_acme",
        created_by_user_id="user_1",
        manifest=SkillManifest.model_validate(first_manifest),
    )
    registry.register_for_tenant(
        tenant_id="tenant_acme",
        created_by_user_id="user_2",
        manifest=SkillManifest.model_validate(second_manifest),
    )

    history = registry.list_versions("tenant_acme", "sales.crm_lookup")
    current = registry.get_for_tenant("tenant_acme", "sales.crm_lookup")

    assert [entry.manifest.version for entry in history] == ["1.0.0", "1.1.0"]
    assert [entry.created_by_user_id for entry in history] == ["user_1", "user_2"]
    assert history[1].manifest.description == "Look up account context and renewal risk from CRM."
    assert current.manifest.version == "1.1.0"
    assert registry.list_versions("tenant_other", "sales.crm_lookup") == []


def test_skill_api_registers_lists_publishes_and_disables_tenant_skill():
    identity, account = create_skill_admin_identity()
    client = TestClient(
        create_app(
            identity_service=identity,
            skill_registry=InMemorySkillRegistry(),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}

    created = client.post("/api/skills", headers=headers, json=skill_manifest_payload())
    listed = client.get("/api/skills", headers=headers)
    fetched = client.get("/api/skills/sales.crm_lookup", headers=headers)
    published = client.post("/api/skills/sales.crm_lookup/publish", headers=headers)
    disabled = client.post("/api/skills/sales.crm_lookup/disable", headers=headers)
    audit_events = client.get(
        "/api/audit-events?event_type=skill.published",
        headers=headers,
    )

    assert created.status_code == 201
    assert created.json()["tenant_id"] == "tenant_acme"
    assert created.json()["status"] == "draft"
    assert created.json()["manifest"]["id"] == "sales.crm_lookup"
    assert [item["manifest"]["id"] for item in listed.json()] == ["sales.crm_lookup"]
    assert fetched.json()["manifest"]["version"] == "1.0.0"
    assert published.json()["status"] == "published"
    assert disabled.json()["status"] == "disabled"
    assert audit_events.status_code == 200
    assert [event["event_type"] for event in audit_events.json()] == ["skill.published"]
    assert audit_events.json()[0]["user_id"] == account.id
    assert audit_events.json()[0]["metadata"]["skill_id"] == "sales.crm_lookup"
    assert audit_events.json()[0]["metadata"]["version"] == "1.0.0"
    assert audit_events.json()[0]["metadata"]["status"] == "published"
    assert audit_events.json()[0]["metadata"]["actor"]["user_id"] == account.id
    assert audit_events.json()[0]["metadata"]["actor"]["actor_type"] == "user"


def test_skill_api_returns_version_history_for_tenant_skill():
    identity, account = create_skill_admin_identity()
    client = TestClient(
        create_app(
            identity_service=identity,
            skill_registry=InMemorySkillRegistry(),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}
    second_manifest = {
        **skill_manifest_payload(),
        "version": "1.1.0",
        "description": "Look up account context and renewal risk from CRM.",
    }

    client.post("/api/skills", headers=headers, json=skill_manifest_payload())
    client.post("/api/skills", headers=headers, json=second_manifest)
    history = client.get("/api/skills/sales.crm_lookup/versions", headers=headers)
    current = client.get("/api/skills/sales.crm_lookup", headers=headers)

    assert history.status_code == 200
    assert [entry["manifest"]["version"] for entry in history.json()] == ["1.0.0", "1.1.0"]
    assert [entry["created_by_user_id"] for entry in history.json()] == [account.id, account.id]
    assert history.json()[1]["manifest"]["description"] == (
        "Look up account context and renewal risk from CRM."
    )
    assert current.json()["manifest"]["version"] == "1.1.0"


def test_skill_api_filters_workspace_and_private_visibility():
    identity = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    owner = create_skill_admin_with_user(identity, "owner@example.com", "role_skill_owner")
    viewer = create_skill_admin_with_user(identity, "viewer@example.com", "role_skill_viewer")
    client = TestClient(
        create_app(
            identity_service=identity,
            skill_registry=InMemorySkillRegistry(),
        )
    )
    owner_headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": owner.id}
    viewer_headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": viewer.id}
    client.post("/api/skills", headers=owner_headers, json=skill_manifest_payload())
    client.post(
        "/api/skills",
        headers=owner_headers,
        json={
            **skill_manifest_payload(),
            "id": "sales.workspace_only",
            "name": "Workspace CRM Lookup",
            "visibility": "workspace",
            "visible_to_workspace_ids": ["workspace_sales"],
        },
    )
    client.post(
        "/api/skills",
        headers=owner_headers,
        json={
            **skill_manifest_payload(),
            "id": "sales.department_only",
            "name": "Department CRM Lookup",
            "visibility": "department",
            "visible_to_department_ids": ["dept_sales"],
        },
    )
    client.post(
        "/api/skills",
        headers=owner_headers,
        json={
            **skill_manifest_payload(),
            "id": "sales.private_lookup",
            "name": "Private CRM Lookup",
            "visibility": "private",
            "visible_to_user_ids": [owner.id],
        },
    )

    owner_sales = client.get(
        "/api/skills",
        headers=owner_headers,
        params={"workspace_id": "workspace_sales", "department_id": "dept_sales"},
    )
    viewer_sales = client.get(
        "/api/skills",
        headers=viewer_headers,
        params={"workspace_id": "workspace_sales", "department_id": "dept_sales"},
    )
    owner_support = client.get(
        "/api/skills",
        headers=owner_headers,
        params={"workspace_id": "workspace_support", "department_id": "dept_support"},
    )
    viewer_private = client.get(
        "/api/skills/sales.private_lookup",
        headers=viewer_headers,
        params={"workspace_id": "workspace_sales", "department_id": "dept_sales"},
    )

    assert [entry["manifest"]["id"] for entry in owner_sales.json()] == [
        "sales.crm_lookup",
        "sales.workspace_only",
        "sales.department_only",
        "sales.private_lookup",
    ]
    assert [entry["manifest"]["id"] for entry in viewer_sales.json()] == [
        "sales.crm_lookup",
        "sales.workspace_only",
        "sales.department_only",
    ]
    assert [entry["manifest"]["id"] for entry in owner_support.json()] == [
        "sales.crm_lookup",
        "sales.private_lookup",
    ]
    assert viewer_private.status_code == 404


def test_skill_api_returns_marketplace_analytics():
    identity = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    owner = create_skill_admin_with_user(identity, "owner@example.com", "role_skill_owner")
    client = TestClient(
        create_app(
            identity_service=identity,
            skill_registry=InMemorySkillRegistry(),
        )
    )
    owner_headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": owner.id}
    client.post("/api/skills", headers=owner_headers, json=skill_manifest_payload())
    client.post(
        "/api/skills",
        headers=owner_headers,
        json={
            **skill_manifest_payload(),
            "id": "sales.workspace_only",
            "name": "Workspace CRM Lookup",
            "visibility": "workspace",
            "visible_to_workspace_ids": ["workspace_sales"],
        },
    )
    client.post("/api/skills/sales.workspace_only/publish", headers=owner_headers)
    client.post(
        "/api/workspaces/workspace_sales/skills/sales.workspace_only/install",
        headers=owner_headers,
    )
    client.post(
        "/api/skills",
        headers=owner_headers,
        json={
            **skill_manifest_payload(),
            "id": "sales.private_lookup",
            "name": "Private CRM Lookup",
            "visibility": "private",
            "visible_to_user_ids": [owner.id],
        },
    )
    client.post("/api/skills/sales.private_lookup/publish", headers=owner_headers)
    client.post("/api/skills/sales.private_lookup/disable", headers=owner_headers)

    response = client.get("/api/skills/analytics", headers=owner_headers)

    assert response.status_code == 200
    assert response.json()["tenant_id"] == "tenant_acme"
    assert response.json()["total_skills"] == 3
    assert response.json()["total_versions"] == 3
    assert response.json()["total_installations"] == 1
    assert response.json()["status_counts"] == {
        "disabled": 1,
        "draft": 1,
        "published": 1,
    }
    assert response.json()["visibility_counts"] == {
        "private": 1,
        "tenant": 1,
        "workspace": 1,
    }
    assert response.json()["installations_by_workspace"] == {"workspace_sales": 1}


def test_skill_api_rejects_user_without_skill_publish_permission():
    identity = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="viewer@example.com",
            display_name="Viewer",
            password="correct horse battery staple",
        )
    )
    client = TestClient(
        create_app(
            identity_service=identity,
            skill_registry=InMemorySkillRegistry(),
        )
    )

    response = client.post(
        "/api/skills",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id},
        json=skill_manifest_payload(),
    )

    assert response.status_code == 403
    assert response.json()["code"] == "tenant_access_denied"


def test_skill_api_can_use_sql_registry_from_settings(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'taroai.sqlite3'}"
    identity, account = create_skill_admin_identity()
    settings = Settings(
        database_url=database_url,
        skill_registry_backend="sql",
        _env_file=None,
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}
    first_client = TestClient(create_app(settings=settings, identity_service=identity))

    created = first_client.post("/api/skills", headers=headers, json=skill_manifest_payload())
    published = first_client.post("/api/skills/sales.crm_lookup/publish", headers=headers)

    second_client = TestClient(create_app(settings=settings, identity_service=identity))
    listed = second_client.get("/api/skills", headers=headers)

    assert created.status_code == 201
    assert published.json()["status"] == "published"
    assert [item["manifest"]["id"] for item in listed.json()] == ["sales.crm_lookup"]
    assert listed.json()[0]["status"] == "published"


def test_skill_api_installs_and_toggles_workspace_skill():
    identity, account = create_skill_admin_identity()
    client = TestClient(
        create_app(
            identity_service=identity,
            skill_registry=InMemorySkillRegistry(),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}
    client.post("/api/skills", headers=headers, json=skill_manifest_payload())
    client.post("/api/skills/sales.crm_lookup/publish", headers=headers)

    installed = client.post(
        "/api/workspaces/workspace_sales/skills/sales.crm_lookup/install",
        headers=headers,
    )
    disabled = client.post(
        "/api/workspaces/workspace_sales/skills/sales.crm_lookup/disable",
        headers=headers,
    )
    enabled = client.post(
        "/api/workspaces/workspace_sales/skills/sales.crm_lookup/enable",
        headers=headers,
    )
    listed = client.get(
        "/api/workspaces/workspace_sales/skills",
        headers=headers,
    )

    assert installed.status_code == 201
    assert installed.json()["status"] == "enabled"
    assert disabled.json()["status"] == "disabled"
    assert enabled.json()["status"] == "enabled"
    assert [item["skill_id"] for item in listed.json()] == ["sales.crm_lookup"]
    assert listed.json()[0]["workspace_id"] == "workspace_sales"
