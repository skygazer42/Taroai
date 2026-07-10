from fastapi.testclient import TestClient

from taroai.app import create_app
from taroai.config import Settings
from taroai.solution_packs import InMemorySolutionPackRegistry, SolutionPackManifest
from tests.api.test_solution_packs import solution_pack_payload


def login_owner(client: TestClient) -> dict[str, str]:
    login = client.post(
        "/api/auth/login",
        json={
            "tenant_id": "tenant_acme",
            "email": "owner@example.com",
            "password": "correct horse battery staple",
        },
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_tenant_bootstrap_installs_requested_solution_packs_into_starter_workspace():
    pack_registry = InMemorySolutionPackRegistry()
    pack_registry.register_for_tenant(
        tenant_id="tenant_acme",
        created_by_user_id="user_system",
        manifest=SolutionPackManifest.model_validate(solution_pack_payload()),
    )
    pack_registry.publish("tenant_acme", "sales.renewal_ops")
    client = TestClient(
        create_app(
            solution_pack_registry=pack_registry,
            settings=Settings(
                tenant_bootstrap_token="bootstrap_secret",
                dev_request_headers_enabled=False,
                _env_file=None,
            ),
        )
    )

    created = client.post(
        "/api/tenants/bootstrap",
        headers={"X-Bootstrap-Token": "bootstrap_secret"},
        json={
            "tenant_id": "tenant_acme",
            "starter_solution_pack_ids": ["sales.renewal_ops"],
            "owner_email": "owner@example.com",
            "owner_display_name": "Owner",
            "owner_password": "correct horse battery staple",
        },
    )

    assert created.status_code == 201
    body = created.json()
    assert body["starter_solution_pack_ids"] == ["sales.renewal_ops"]
    assert body["starter_solution_pack_skill_ids"] == [
        "sales.crm_lookup",
        "sales.renewal_checklist",
    ]
    assert pack_registry.get_installation("tenant_acme", "sales.renewal_ops").workspace_ids == [
        body["starter_workspace_id"]
    ]

    skill_headers = login_owner(client)
    workspace_skills = client.get(
        f"/api/workspaces/{body['starter_workspace_id']}/skills",
        headers=skill_headers,
    )
    audits = client.get("/api/audit-events", headers=skill_headers)

    assert workspace_skills.status_code == 200
    assert {
        skill["skill_id"]
        for skill in workspace_skills.json()
    }.issuperset({"sales.crm_lookup", "sales.renewal_checklist"})
    bootstrap_event = [
        event
        for event in audits.json()
        if event["event_type"] == "tenant.bootstrap.completed"
    ][0]
    assert bootstrap_event["metadata"]["starter_solution_pack_ids"] == [
        "sales.renewal_ops"
    ]
    assert bootstrap_event["metadata"]["starter_solution_pack_skill_count"] == 2


def test_tenant_bootstrap_owner_gets_solution_pack_and_customer_success_permissions():
    client = TestClient(
        create_app(
            settings=Settings(
                tenant_bootstrap_token="bootstrap_secret",
                dev_request_headers_enabled=False,
                _env_file=None,
            )
        )
    )

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
    assert created.status_code == 201
    headers = login_owner(client)

    assert client.get("/api/solution-packs", headers=headers).status_code == 200
    assert client.get("/api/customer-success/summary", headers=headers).status_code == 200
    owner_role = client.app.state.identity_service.get_role(
        "tenant_acme",
        "tenant_owner",
    )
    assert "skills.invoke" in {
        permission.action for permission in owner_role.permissions
    }
