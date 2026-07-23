import base64
from io import BytesIO
from zipfile import ZipFile

from fastapi.testclient import TestClient

from taroai.agent import AgentRuntime
from taroai.app import create_app
from taroai.config import Settings
from taroai.model_gateway import PlannedToolCall
from taroai.store import InMemoryControlPlaneStore
from taroai.identity import (
    InMemoryIdentityService,
    PasswordHasher,
    Permission,
    Role,
    UserAccountCreate,
)
from taroai.skills import InMemorySkillRegistry, SkillManifest, parse_skill_frontmatter
from taroai.tool_gateway import ToolPolicy, ToolResult
from tests.api.adapters import DeterministicModelGateway, DeterministicToolGateway


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
                Permission(action="skills.invoke", resource="tenant:tenant_acme"),
                Permission(action="crm.read", resource="tenant:tenant_acme"),
                Permission(action="audit.read", resource="tenant:tenant_acme"),
                Permission(action="billing.read", resource="tenant:tenant_acme"),
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


def workflow_skill_manifest_payload() -> dict:
    return {
        "id": "sales.renewal_brief",
        "version": "1.0.0",
        "name": "Renewal Brief",
        "description": "Prepare a renewal brief from account context.",
        "type": "workflow_skill",
        "owner": "solutions/sales",
        "input_schema": {
            "type": "object",
            "required": ["account_id"],
            "properties": {"account_id": {"type": "string"}},
        },
        "output_schema": {
            "type": "object",
            "required": ["run_id", "status"],
            "properties": {
                "run_id": {"type": "string"},
                "status": {"type": "string"},
            },
        },
        "required_scopes": ["crm.read"],
        "runtime": {"sandbox": "workflow", "timeout_seconds": 120},
        "billing_meters": ["skill_call_count"],
    }


def test_skill_frontmatter_accepts_portable_folded_description():
    frontmatter, body = parse_skill_frontmatter(
        """---
name: ponytail
description: >
  Prefer the simplest solution that works.
  Avoid speculative abstractions.
argument-hint: "[lite|full|ultra]"
license: MIT
---
# Ponytail
"""
    )

    assert frontmatter.description == (
        "Prefer the simplest solution that works. Avoid speculative abstractions."
    )
    assert body == "# Ponytail\n"


def test_portable_skill_zip_import_evaluate_publish_and_install():
    identity, account = create_skill_admin_identity()
    app = create_app(
        identity_service=identity,
        skill_registry=InMemorySkillRegistry(),
        settings=Settings(_env_file=None),
    )
    client = TestClient(app)
    archive = BytesIO()
    with ZipFile(archive, "w") as package:
        package.writestr(
            "SKILL.md",
            """---
name: portable-skill
description: A portable test skill.
---
# Portable skill
""",
        )

    response = client.post(
        "/api/skills/import/zip",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id},
        json={
            "archive_base64": base64.b64encode(archive.getvalue()).decode("ascii"),
            "workspace_id": "workspace_sales",
        },
    )

    assert response.status_code == 201
    meter = app.state.store.list_billing_meters("tenant_acme")[-1]
    assert meter.meter_type == "storage_bytes"
    assert meter.metadata["resource_type"] == "skill_package"
    assert meter.metadata["skill_id"] == "portable-skill"

    evaluation = client.post(
        "/api/skills/portable-skill/packages/0.0.0/evaluate",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id},
        json={"workspace_id": "workspace_sales"},
    )
    assert evaluation.status_code == 200
    assert evaluation.json()["evaluator_version"] == "package-validation.v1"
    assert evaluation.json()["passed"] is True

    published = client.post(
        "/api/skills/portable-skill/packages/0.0.0/publish",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id},
        json={"evaluation_run_id": evaluation.json()["id"]},
    )
    assert published.status_code == 200
    assert published.json()["status"] == "published"

    installed = client.post(
        "/api/workspaces/workspace_sales/skills/portable-skill/install",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id},
        json={
            "version": "0.0.0",
            "package_digest": response.json()["package_digest"],
        },
    )
    assert installed.status_code == 201
    assert installed.json()["status"] == "enabled"


def login_skill_admin(client: TestClient) -> dict[str, str]:
    login = client.post(
        "/api/auth/login",
        json={
            "tenant_id": "tenant_acme",
            "email": "admin@example.com",
            "password": "correct horse battery staple",
        },
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


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


def test_workspace_skill_invoke_executes_installed_enabled_skill_through_tool_gateway():
    identity, account = create_skill_admin_identity()
    app = create_app(
        identity_service=identity,
        skill_registry=InMemorySkillRegistry(),
        settings=Settings(_env_file=None),
    )
    app.state.runtime.tool_gateway.register_tool(
        ToolPolicy(
            tool_name="sales.crm_lookup",
            required_scopes=[],
            input_schema=skill_manifest_payload()["input_schema"],
            output_schema=skill_manifest_payload()["output_schema"],
        ),
        handler=lambda request: ToolResult(
            tool_name=request.tool_name,
            output={
                "account": {
                    "id": request.tool_input["account_id"],
                    "name": "Acme",
                }
            },
        ),
    )
    client = TestClient(app)
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}
    client.post("/api/skills", headers=headers, json=skill_manifest_payload())
    client.post("/api/skills/sales.crm_lookup/publish", headers=headers)
    client.post(
        "/api/workspaces/workspace_sales/skills/sales.crm_lookup/install",
        headers=headers,
    )

    response = client.post(
        "/api/workspaces/workspace_sales/skills/sales.crm_lookup/invoke",
        headers=headers,
        json={"input": {"account_id": "acct_123"}},
    )
    audit_events = client.get(
        "/api/audit-events?event_type=skill.invoked",
        headers=headers,
    )
    meters = client.get("/api/billing/meters", headers=headers)

    assert response.status_code == 200
    assert response.json() == {
        "skill_id": "sales.crm_lookup",
        "tool_name": "sales.crm_lookup",
        "output": {"account": {"id": "acct_123", "name": "Acme"}},
    }
    assert [event["event_type"] for event in audit_events.json()] == ["skill.invoked"]
    audit_metadata = audit_events.json()[0]["metadata"]
    assert audit_metadata["skill_id"] == "sales.crm_lookup"
    assert audit_metadata["tool_name"] == "sales.crm_lookup"
    assert audit_metadata["workspace_id"] == "workspace_sales"
    assert audit_metadata["input_keys"] == ["account_id"]
    assert audit_metadata["output_keys"] == ["account"]
    assert audit_metadata["actor"]["actor_type"] == "user"
    assert audit_metadata["actor"]["user_id"] == account.id
    skill_meters = [
        meter for meter in meters.json() if meter["meter_type"] == "skill_call_count"
    ]
    assert len(skill_meters) == 1
    assert skill_meters[0]["skill_id"] == "sales.crm_lookup"
    assert skill_meters[0]["metadata"]["tool_name"] == "sales.crm_lookup"


def test_skill_invoke_accepts_bearer_auth_when_dev_headers_are_disabled():
    identity, _account = create_skill_admin_identity()
    app = create_app(
        identity_service=identity,
        skill_registry=InMemorySkillRegistry(),
        settings=Settings(
            dev_request_headers_enabled=False,
            access_token_secret="unit_test_secret",
            _env_file=None,
        ),
    )
    app.state.runtime.tool_gateway.register_tool(
        ToolPolicy(
            tool_name="sales.crm_lookup",
            required_scopes=[],
            input_schema=skill_manifest_payload()["input_schema"],
            output_schema=skill_manifest_payload()["output_schema"],
        ),
        handler=lambda request: ToolResult(
            tool_name=request.tool_name,
            output={
                "account": {
                    "id": request.tool_input["account_id"],
                    "name": "Acme",
                }
            },
        ),
    )
    client = TestClient(app)
    headers = login_skill_admin(client)
    client.post("/api/skills", headers=headers, json=skill_manifest_payload())
    client.post("/api/skills/sales.crm_lookup/publish", headers=headers)
    client.post(
        "/api/workspaces/workspace_sales/skills/sales.crm_lookup/install",
        headers=headers,
    )

    response = client.post(
        "/api/workspaces/workspace_sales/skills/sales.crm_lookup/invoke",
        headers=headers,
        json={"input": {"account_id": "acct_123"}},
    )

    assert response.status_code == 200
    assert response.json()["output"] == {
        "account": {
            "id": "acct_123",
            "name": "Acme",
        }
    }


def test_workflow_skill_invoke_starts_agent_run_without_registered_tool_handler():
    identity, _account = create_skill_admin_identity()
    store = InMemoryControlPlaneStore()
    runtime = AgentRuntime(
        store=store,
        model_gateway=DeterministicModelGateway(
            plan=[
                PlannedToolCall(
                    id="step_prepare_brief",
                    title="Prepare renewal brief",
                    tool_name="workspace.note",
                    tool_input={"account_id": "acct_123"},
                )
            ]
        ),
        tool_gateway=DeterministicToolGateway(),
    )
    client = TestClient(
        create_app(
            store=store,
            runtime=runtime,
            identity_service=identity,
            skill_registry=InMemorySkillRegistry(),
            settings=Settings(
                dev_request_headers_enabled=False,
                access_token_secret="unit_test_secret",
                _env_file=None,
            ),
        )
    )
    headers = login_skill_admin(client)
    client.post("/api/skills", headers=headers, json=workflow_skill_manifest_payload())
    client.post("/api/skills/sales.renewal_brief/publish", headers=headers)
    client.post(
        "/api/workspaces/workspace_sales/skills/sales.renewal_brief/install",
        headers=headers,
    )
    listed = client.get(
        "/api/workspaces/workspace_sales/skills",
        headers=headers,
    )

    response = client.post(
        "/api/workspaces/workspace_sales/skills/sales.renewal_brief/invoke",
        headers=headers,
        json={"input": {"account_id": "acct_123"}},
    )

    skill_installation = listed.json()[0]
    assert skill_installation["invocation_mode"] == "agent_workflow"
    assert skill_installation["invocation_ready"] is True
    assert skill_installation["missing_required_scopes"] == []
    assert response.status_code == 200
    body = response.json()
    assert body["skill_id"] == "sales.renewal_brief"
    assert body["tool_name"] == "agent.workflow"
    assert body["output"]["run_id"].startswith("run_")
    assert body["output"]["status"] == "awaiting_approval"
    assert body["run_id"] == body["output"]["run_id"]
    approvals = store.list_approval_requests("tenant_acme", body["run_id"])
    assert len(approvals) == 1
    assert approvals[0].status.value == "pending"
    events = store.list_run_events("tenant_acme", body["run_id"])
    assert "skill.workflow_invoked" in [event.type for event in events]


def test_workspace_skill_invoke_rejects_disabled_or_missing_workspace_installation():
    identity, account = create_skill_admin_identity()
    app = create_app(
        identity_service=identity,
        skill_registry=InMemorySkillRegistry(),
        settings=Settings(_env_file=None),
    )
    app.state.runtime.tool_gateway.register_tool(
        ToolPolicy(
            tool_name="sales.crm_lookup",
            required_scopes=[],
            input_schema=skill_manifest_payload()["input_schema"],
            output_schema=skill_manifest_payload()["output_schema"],
        ),
        handler=lambda request: ToolResult(
            tool_name=request.tool_name,
            output={"account": {"id": request.tool_input["account_id"]}},
        ),
    )
    client = TestClient(app)
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}
    client.post("/api/skills", headers=headers, json=skill_manifest_payload())
    client.post("/api/skills/sales.crm_lookup/publish", headers=headers)

    missing_installation = client.post(
        "/api/workspaces/workspace_sales/skills/sales.crm_lookup/invoke",
        headers=headers,
        json={"input": {"account_id": "acct_123"}},
    )
    client.post(
        "/api/workspaces/workspace_sales/skills/sales.crm_lookup/install",
        headers=headers,
    )
    client.post(
        "/api/workspaces/workspace_sales/skills/sales.crm_lookup/disable",
        headers=headers,
    )
    disabled_installation = client.post(
        "/api/workspaces/workspace_sales/skills/sales.crm_lookup/invoke",
        headers=headers,
        json={"input": {"account_id": "acct_123"}},
    )

    assert missing_installation.status_code == 404
    assert missing_installation.json()["code"] == "not_found"
    assert disabled_installation.status_code == 403
    assert disabled_installation.json()["code"] == "tenant_access_denied"


def test_workspace_skill_invoke_enforces_manifest_required_scopes():
    identity, account = create_skill_admin_identity()
    identity.roles["tenant_acme:role_skill_admin"] = identity.roles[
        "tenant_acme:role_skill_admin"
    ].model_copy(
        update={
            "permissions": [
                permission
                for permission in identity.roles[
                    "tenant_acme:role_skill_admin"
                ].permissions
                if permission.action != "crm.read"
            ]
        }
    )
    app = create_app(
        identity_service=identity,
        skill_registry=InMemorySkillRegistry(),
        settings=Settings(_env_file=None),
    )
    app.state.runtime.tool_gateway.register_tool(
        ToolPolicy(
            tool_name="sales.crm_lookup",
            required_scopes=[],
            input_schema=skill_manifest_payload()["input_schema"],
            output_schema=skill_manifest_payload()["output_schema"],
        ),
        handler=lambda request: ToolResult(
            tool_name=request.tool_name,
            output={"account": {"id": request.tool_input["account_id"]}},
        ),
    )
    client = TestClient(app)
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}
    client.post("/api/skills", headers=headers, json=skill_manifest_payload())
    client.post("/api/skills/sales.crm_lookup/publish", headers=headers)
    client.post(
        "/api/workspaces/workspace_sales/skills/sales.crm_lookup/install",
        headers=headers,
    )

    response = client.post(
        "/api/workspaces/workspace_sales/skills/sales.crm_lookup/invoke",
        headers=headers,
        json={"input": {"account_id": "acct_123"}},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "tenant_access_denied"


def test_workspace_skill_invoke_requires_auth_when_dev_request_headers_are_disabled():
    identity, account = create_skill_admin_identity()
    client = TestClient(
        create_app(
            identity_service=identity,
            skill_registry=InMemorySkillRegistry(),
            settings=Settings(
                dev_request_headers_enabled=False,
                _env_file=None,
            ),
        )
    )

    response = client.post(
        "/api/workspaces/workspace_sales/skills/sales.crm_lookup/invoke",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id},
        json={"input": {"account_id": "acct_123"}},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "auth_required"
