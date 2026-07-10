from fastapi.testclient import TestClient

from taroai.app import create_app
from taroai.identity import (
    InMemoryIdentityService,
    PasswordHasher,
    Permission,
    Role,
    UserAccountCreate,
)
from taroai.triggers import InMemoryTriggerStore, TriggerService


def create_trigger_admin_identity():
    identity = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="automation-admin@example.com",
            display_name="Automation Admin",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_trigger_admin",
            name="Trigger Admin",
            permissions=[
                Permission(action="triggers.read", resource="tenant:tenant_acme"),
                Permission(action="triggers.manage", resource="tenant:tenant_acme"),
                Permission(action="triggers.invoke", resource="tenant:tenant_acme"),
                Permission(action="audit.read", resource="tenant:tenant_acme"),
                Permission(action="billing.read", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_trigger_admin")
    return identity, account


def trigger_payload() -> dict:
    return {
        "workspace_id": "workspace_ops",
        "agent_id": "agent_sla",
        "type": "api",
        "name": "SLA sweep",
        "input_template": {"message": "Check open SLA risk."},
        "policy_profile": "business-hours",
        "budget_profile": "automation-low",
    }


def test_trigger_admin_api_creates_lists_disables_and_enables_trigger():
    identity, account = create_trigger_admin_identity()
    trigger_service = TriggerService(store=InMemoryTriggerStore())
    client = TestClient(
        create_app(identity_service=identity, trigger_service=trigger_service)
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}

    created = client.post("/api/triggers", headers=headers, json=trigger_payload())
    listed = client.get("/api/triggers", headers=headers)
    disabled = client.post(
        f"/api/triggers/{created.json()['id']}/disable",
        headers=headers,
    )
    enabled = client.post(
        f"/api/triggers/{created.json()['id']}/enable",
        headers=headers,
    )

    assert created.status_code == 201
    assert created.json()["tenant_id"] == "tenant_acme"
    assert created.json()["created_by_user_id"] == account.id
    assert listed.status_code == 200
    assert [trigger["id"] for trigger in listed.json()] == [created.json()["id"]]
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    assert enabled.status_code == 200
    assert enabled.json()["status"] == "enabled"


def test_trigger_invoke_api_creates_accountable_run_with_audit_and_meter():
    identity, account = create_trigger_admin_identity()
    trigger_service = TriggerService(store=InMemoryTriggerStore())
    client = TestClient(
        create_app(identity_service=identity, trigger_service=trigger_service)
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}

    created = client.post("/api/triggers", headers=headers, json=trigger_payload())
    invoked = client.post(
        f"/api/triggers/{created.json()['id']}/invoke",
        headers=headers,
        json={"payload": {"ticket_id": "ticket_123", "priority": "high"}},
    )
    run = client.get(f"/api/runs/{invoked.json()['run_id']}", headers=headers)
    audits = client.get(
        "/api/audit-events?event_type=trigger.invoked",
        headers=headers,
    )
    meters = client.get("/api/billing/meters", headers=headers)

    assert invoked.status_code == 202
    assert invoked.json()["trigger_id"] == created.json()["id"]
    assert invoked.json()["status"] == "created"
    assert run.status_code == 200
    assert run.json()["workspace_id"] == "workspace_ops"
    assert run.json()["user_id"] == account.id
    assert run.json()["mode"] == "autonomous"
    assert run.json()["message"] == "Check open SLA risk."
    assert audits.status_code == 200
    audit_metadata = audits.json()[0]["metadata"]
    business_metadata = {
        key: audit_metadata[key]
        for key in [
            "trigger_id",
            "trigger_type",
            "run_id",
            "invocation_payload_keys",
        ]
    }
    assert business_metadata == {
        "trigger_id": created.json()["id"],
        "trigger_type": "api",
        "run_id": invoked.json()["run_id"],
        "invocation_payload_keys": ["priority", "ticket_id"],
    }
    assert audit_metadata["audit_retention_days"] == 365
    assert audit_metadata["actor"]["user_id"] == account.id
    trigger_meters = [
        meter
        for meter in meters.json()
        if meter["meter_type"] == "trigger_invocation_count"
    ]
    assert len(trigger_meters) == 1
    assert trigger_meters[0]["metadata"] == {
        "trigger_id": created.json()["id"],
        "trigger_type": "api",
    }


def test_trigger_admin_api_requires_trigger_permission():
    identity = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="employee@example.com",
            display_name="Employee",
            password="correct horse battery staple",
        )
    )
    client = TestClient(create_app(identity_service=identity))
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}

    response = client.post("/api/triggers", headers=headers, json=trigger_payload())

    assert response.status_code == 403
    assert response.json()["code"] == "tenant_access_denied"
