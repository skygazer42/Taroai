from fastapi.testclient import TestClient

from taroai.app import create_app
from taroai.config import Settings
from taroai.identity import InMemoryIdentityService, PasswordHasher, UserAccountCreate
from taroai.store import InMemoryControlPlaneStore


def test_tenant_workspace_membership_and_invitation_api_closes_the_loop():
    store = InMemoryControlPlaneStore()
    client = TestClient(
        create_app(
            store=store,
            identity_service=InMemoryIdentityService(
                password_hasher=PasswordHasher(salt="test_salt")
            ),
            settings=Settings(
                environment="test",
                access_token_secret="unit_test_access_secret",
                tenant_bootstrap_token="unit_test_bootstrap_secret",
                _env_file=None,
            ),
        )
    )
    owner_password = "correct horse battery staple"
    registration = client.post(
        "/api/auth/register",
        json={
            "display_name": "Workspace Owner",
            "email": "owner@example.com",
            "password": owner_password,
        },
    )
    assert registration.status_code == 201
    tenant_id = registration.json()["tenant_id"]
    starter_workspace_id = registration.json()["starter_workspace_id"]
    login = client.post(
        "/api/auth/login",
        json={
            "tenant_id": tenant_id,
            "email": "owner@example.com",
            "password": owner_password,
        },
    ).json()
    owner_headers = {"Authorization": f"Bearer {login['access_token']}"}

    summary = client.get("/api/tenants/current", headers=owner_headers)
    assert summary.status_code == 200
    assert summary.json()["can_manage"] is True
    assert "organization.manage" in summary.json()["permissions"]
    assert summary.json()["workspaces"] == [
        {
            "id": starter_workspace_id,
            "tenant_id": tenant_id,
            "name": "Default Workspace",
        }
    ]

    renamed_tenant = client.patch(
        "/api/tenants/current",
        headers=owner_headers,
        json={"name": "Acme"},
    )
    workspace = client.post(
        "/api/workspaces",
        headers=owner_headers,
        json={"name": "Research"},
    )
    renamed_workspace = client.patch(
        f"/api/workspaces/{workspace.json()['id']}",
        headers=owner_headers,
        json={"name": "Applied Research"},
    )
    assert renamed_tenant.json() == {"id": tenant_id, "name": "Acme"}
    assert workspace.status_code == 201
    assert renamed_workspace.json()["name"] == "Applied Research"

    created_invitation = client.post(
        "/api/tenants/current/invitations",
        headers=owner_headers,
        json={"email": "member@example.com"},
    )
    assert created_invitation.status_code == 201
    invitation = created_invitation.json()["invitation"]
    invitation_token = created_invitation.json()["token"]
    assert invitation["status"] == "pending"
    assert "token" not in invitation and "token_hash" not in invitation
    stored_invitation = store.get_tenant_invitation(tenant_id, invitation["id"])
    assert stored_invitation.token_hash != invitation_token

    accepted = client.post(
        "/api/tenant-invitations/accept",
        json={
            "tenant_id": tenant_id,
            "token": invitation_token,
            "display_name": "Workspace Member",
            "password": "member password is long enough",
        },
    )
    assert accepted.status_code == 200
    assert accepted.json()["tenant_id"] == tenant_id
    assert accepted.json()["email"] == "member@example.com"
    assert accepted.json()["workspace_id"] in {
        starter_workspace_id,
        workspace.json()["id"],
    }
    assert accepted.json()["access_token"]
    member_headers = {
        "Authorization": f"Bearer {accepted.json()['access_token']}"
    }
    member_summary = client.get("/api/tenants/current", headers=member_headers)
    assert member_summary.status_code == 200
    assert member_summary.json()["can_manage"] is False
    assert member_summary.json()["invitations"] == []
    assert client.post(
        "/api/tenants/current/invitations",
        headers=member_headers,
        json={"email": "blocked@example.com"},
    ).status_code == 403

    retry_invitation = client.post(
        "/api/tenants/current/invitations",
        headers=owner_headers,
        json={"email": "retry@example.com"},
    ).json()
    client.app.state.identity_service.create_user(
        UserAccountCreate(
            tenant_id=tenant_id,
            email="retry@example.com",
            display_name="Interrupted Accept",
            password="retry password is long enough",
        )
    )
    retry_payload = {
        "tenant_id": tenant_id,
        "token": retry_invitation["token"],
        "display_name": "Interrupted Accept",
        "password": "wrong password is still long enough",
    }
    assert client.post(
        "/api/tenant-invitations/accept",
        json=retry_payload,
    ).status_code == 409
    retry_payload["password"] = "retry password is long enough"
    assert client.post(
        "/api/tenant-invitations/accept",
        json=retry_payload,
    ).status_code == 200

    removed = client.delete(
        f"/api/tenants/current/members/{accepted.json()['user_id']}",
        headers=owner_headers,
    )
    assert removed.status_code == 200
    assert removed.json()["status"] == "disabled"
    assert client.get("/api/tenants/current", headers=member_headers).status_code == 401
    restored = client.post(
        f"/api/tenants/current/members/{accepted.json()['user_id']}/restore",
        headers=owner_headers,
    )
    assert restored.status_code == 200
    assert restored.json()["status"] == "active"
    assert client.post(
        "/api/auth/login",
        json={
            "tenant_id": tenant_id,
            "email": "member@example.com",
            "password": "member password is long enough",
        },
    ).status_code == 200
    assert client.post(
        "/api/tenant-invitations/accept",
        json={
            "tenant_id": tenant_id,
            "token": invitation_token,
            "display_name": "Replay",
            "password": "another long enough password",
        },
    ).status_code == 409
    assert client.delete(
        f"/api/tenants/current/members/{login['user_id']}",
        headers=owner_headers,
    ).status_code == 409

    revoke_candidate = client.post(
        "/api/tenants/current/invitations",
        headers=owner_headers,
        json={"email": "revoked@example.com"},
    ).json()
    revoked = client.delete(
        "/api/tenants/current/invitations/"
        f"{revoke_candidate['invitation']['id']}",
        headers=owner_headers,
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    assert client.post(
        "/api/tenant-invitations/accept",
        json={
            "tenant_id": tenant_id,
            "token": revoke_candidate["token"],
            "display_name": "Revoked",
            "password": "another long enough password",
        },
    ).status_code == 409
