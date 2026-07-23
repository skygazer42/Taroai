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
from taroai.skills import InMemorySkillRegistry
from taroai.store import InMemoryControlPlaneStore


STORE_ITEM_ID = "taroai.data-signal-starter"


def store_client():
    tenant_id = "tenant_store"
    workspace_id = "workspace_store"
    identity = InMemoryIdentityService(
        password_hasher=PasswordHasher(salt="store_test_salt")
    )
    account = identity.create_user(
        UserAccountCreate(
            tenant_id=tenant_id,
            email="store@example.com",
            display_name="Store User",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id=tenant_id,
            id="role_store_user",
            name="Store User",
            permissions=[
                Permission(action="skills.read", resource=f"tenant:{tenant_id}"),
                Permission(action="skills.install", resource=f"tenant:{tenant_id}"),
                Permission(
                    action="solution_packs.read", resource=f"tenant:{tenant_id}"
                ),
            ],
        )
    )
    identity.assign_role(tenant_id, account.id, "role_store_user")
    store = InMemoryControlPlaneStore()
    store.register_workspace(tenant_id, workspace_id, account.id)
    store.register_workspace("tenant_other", "workspace_other", "user_other")
    app = create_app(
        store=store,
        identity_service=identity,
        skill_registry=InMemorySkillRegistry(),
        settings=Settings(_env_file=None),
    )
    headers = {"X-Tenant-ID": tenant_id, "X-User-ID": account.id}
    return app, TestClient(app), headers, tenant_id, workspace_id


def test_builtin_store_lists_details_and_installs_an_exact_package():
    app, client, headers, tenant_id, workspace_id = store_client()

    assert client.get("/api/store/items").status_code == 401
    listed = client.get(
        "/api/store/items?q=csv&kind=solution_pack", headers=headers
    )
    item = next(entry for entry in listed.json()["items"] if entry["id"] == STORE_ITEM_ID)
    detail = client.get(f"/api/store/items/{STORE_ITEM_ID}", headers=headers)
    assert listed.status_code == detail.status_code == 200
    assert detail.json()["digest"] == item["digest"]
    assert item["origin"] == "builtin"
    assert item["license"] == "Apache-2.0"
    assert item["approval_required"] is False
    assert detail.json()["packages"][0]["skill_id"] == "taroai.csv-signal-brief"
    assert client.get("/api/store/items?kind=skill", headers=headers).json() == {
        "items": []
    }

    isolated = client.post(
        f"/api/store/items/{STORE_ITEM_ID}/install",
        headers=headers,
        json={"workspace_id": "workspace_other", "expected_digest": item["digest"]},
    )
    assert isolated.status_code == 404

    installed = client.post(
        f"/api/store/items/{STORE_ITEM_ID}/install",
        headers=headers,
        json={"workspace_id": workspace_id, "expected_digest": item["digest"]},
    )
    assert installed.status_code == 201
    assert installed.json()["skills"] == [
        {
            "skill_id": "taroai.csv-signal-brief",
            "version": "1.0.0",
            "package_digest": detail.json()["packages"][0]["package_digest"],
            "status": "enabled",
            "requires_approval": False,
        }
    ]
    discovered = app.state.skill_service.discover(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=headers["X-User-ID"],
    )
    assert [skill.skill_id for skill in discovered] == ["taroai.csv-signal-brief"]
    packs = client.get("/api/solution-packs", headers=headers)
    pack_installations = client.get(
        "/api/solution-pack-installations", headers=headers
    )
    assert [entry["manifest"]["id"] for entry in packs.json()] == [STORE_ITEM_ID]
    assert pack_installations.json()[0]["workspace_ids"] == [workspace_id]


def test_builtin_store_rejects_a_tenant_package_digest_conflict():
    app, client, headers, tenant_id, workspace_id = store_client()
    package = app.state.store_catalog.get(STORE_ITEM_ID).skills[0].package
    app.state.skill_registry.register_package_for_tenant(
        tenant_id,
        headers["X-User-ID"],
        package.model_copy(update={"package_digest": "0" * 64}),
    )

    response = client.post(
        f"/api/store/items/{STORE_ITEM_ID}/install",
        headers=headers,
        json={"workspace_id": workspace_id},
    )

    assert response.status_code == 409
    assert "digest conflict" in response.json()["detail"]
