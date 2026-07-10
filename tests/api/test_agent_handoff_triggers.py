import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from taroai.app import create_app
from taroai.identity import (
    InMemoryIdentityService,
    PasswordHasher,
    Permission,
    Role,
    UserAccountCreate,
)
from taroai.triggers import (
    AgentHandoffRequest,
    InMemoryTriggerStore,
    TriggerAgentHandoffConfig,
    TriggerDefinitionCreate,
    TriggerService,
    TriggerType,
)


def create_handoff_identity(include_target_permission: bool = True):
    identity = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="automation-admin@example.com",
            display_name="Automation Admin",
            password="correct horse battery staple",
        )
    )
    permissions = [
        Permission(action="triggers.read", resource="tenant:tenant_acme"),
        Permission(action="triggers.manage", resource="tenant:tenant_acme"),
        Permission(action="triggers.invoke", resource="tenant:tenant_acme"),
        Permission(action="audit.read", resource="tenant:tenant_acme"),
        Permission(action="billing.read", resource="tenant:tenant_acme"),
    ]
    if include_target_permission:
        permissions.append(
            Permission(action="agents.handoff.escalate", resource="tenant:tenant_acme")
        )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_handoff_admin",
            name="Handoff Admin",
            permissions=permissions,
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_handoff_admin")
    return identity, account


def handoff_trigger_payload(**overrides) -> TriggerDefinitionCreate:
    data = {
        "tenant_id": "tenant_acme",
        "workspace_id": "workspace_ops",
        "created_by_user_id": None,
        "service_account_id": "svc_handoff",
        "type": TriggerType.AGENT_HANDOFF,
        "name": "Escalate renewal risk",
        "input_template": {"message": "Review delegated renewal risk."},
        "agent_handoff": TriggerAgentHandoffConfig(
            target_agent_id="agent_specialist",
            max_depth=2,
            required_permissions=["agents.handoff.escalate"],
        ),
    }
    data.update(overrides)
    return TriggerDefinitionCreate(**data)


def create_source_run(client: TestClient, headers: dict) -> dict:
    response = client.post(
        "/api/runs",
        headers=headers,
        json={
            "workspace_id": "workspace_ops",
            "agent_id": "agent_primary",
            "message": "Review the renewal account.",
            "mode": "workflow",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_agent_handoff_trigger_requires_handoff_config():
    with pytest.raises(ValidationError, match="agent handoff trigger requires agent_handoff config"):
        handoff_trigger_payload(agent_handoff=None)


def test_agent_handoff_api_creates_target_agent_run_and_records_events():
    identity, account = create_handoff_identity()
    trigger_service = TriggerService(store=InMemoryTriggerStore())
    client = TestClient(
        create_app(identity_service=identity, trigger_service=trigger_service)
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}
    source = create_source_run(client, headers)
    created = client.post(
        "/api/triggers",
        headers=headers,
        json={
            "workspace_id": "workspace_ops",
            "service_account_id": "svc_handoff",
            "type": "agent_handoff",
            "name": "Escalate renewal risk",
            "input_template": {"message": "Review delegated renewal risk."},
            "agent_handoff": {
                "target_agent_id": "agent_specialist",
                "max_depth": 2,
                "required_permissions": ["agents.handoff.escalate"],
            },
        },
    )

    response = client.post(
        f"/api/triggers/{created.json()['id']}/agent-handoff",
        headers=headers,
        json=AgentHandoffRequest(
            source_run_id=source["run_id"],
            source_agent_id="agent_primary",
            reason_code="specialist_review",
            handoff_depth=0,
            handoff_input={"account_id": "acct_123", "risk": "renewal"},
        ).model_dump(mode="json"),
    )
    target_run = client.get(f"/api/runs/{response.json()['run_id']}", headers=headers)
    source_events = client.get(
        f"/api/runs/{source['run_id']}/events",
        headers=headers,
    ).text
    target_events = client.get(
        f"/api/runs/{response.json()['run_id']}/events",
        headers=headers,
    ).text
    audits = client.get(
        "/api/audit-events?event_type=trigger.invoked",
        headers=headers,
    )
    meters = client.get("/api/billing/meters", headers=headers)

    assert created.status_code == 201
    assert response.status_code == 202
    assert response.json()["trigger_id"] == created.json()["id"]
    assert target_run.status_code == 200
    assert target_run.json()["workspace_id"] == "workspace_ops"
    assert target_run.json()["agent_id"] == "agent_specialist"
    assert target_run.json()["user_id"] == "svc_handoff"
    assert target_run.json()["mode"] == "autonomous"
    assert target_run.json()["message"] == "Review delegated renewal risk."
    assert "agent.handoff.requested" in source_events
    assert "agent.handoff.received" in target_events
    assert "acct_123" not in source_events
    assert "acct_123" not in target_events
    assert audits.status_code == 200
    audit_metadata = audits.json()[0]["metadata"]
    business_metadata = {
        key: audit_metadata[key]
        for key in [
            "trigger_id",
            "trigger_type",
            "run_id",
            "source_run_id",
            "source_agent_id",
            "target_agent_id",
            "handoff_depth",
            "max_depth",
            "reason_code",
            "invocation_payload_keys",
        ]
    }
    assert business_metadata == {
        "trigger_id": created.json()["id"],
        "trigger_type": "agent_handoff",
        "run_id": response.json()["run_id"],
        "source_run_id": source["run_id"],
        "source_agent_id": "agent_primary",
        "target_agent_id": "agent_specialist",
        "handoff_depth": 1,
        "max_depth": 2,
        "reason_code": "specialist_review",
        "invocation_payload_keys": ["account_id", "risk"],
    }
    assert audit_metadata["audit_retention_days"] == 365
    assert audit_metadata["actor"]["user_id"] == "svc_handoff"
    handoff_meters = [
        meter
        for meter in meters.json()
        if meter["meter_type"] == "trigger_invocation_count"
        and meter["metadata"]["trigger_type"] == "agent_handoff"
    ]
    assert len(handoff_meters) == 1


def test_agent_handoff_api_rejects_max_depth_loop():
    identity, account = create_handoff_identity()
    trigger_service = TriggerService(store=InMemoryTriggerStore())
    client = TestClient(
        create_app(identity_service=identity, trigger_service=trigger_service)
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}
    source = create_source_run(client, headers)
    created = client.post(
        "/api/triggers",
        headers=headers,
        json={
            "workspace_id": "workspace_ops",
            "service_account_id": "svc_handoff",
            "type": "agent_handoff",
            "name": "Escalate renewal risk",
            "input_template": {"message": "Review delegated renewal risk."},
            "agent_handoff": {
                "target_agent_id": "agent_specialist",
                "max_depth": 1,
                "required_permissions": ["agents.handoff.escalate"],
            },
        },
    )

    response = client.post(
        f"/api/triggers/{created.json()['id']}/agent-handoff",
        headers=headers,
        json={
            "source_run_id": source["run_id"],
            "source_agent_id": "agent_primary",
            "reason_code": "loop",
            "handoff_depth": 1,
            "handoff_input": {"account_id": "acct_123"},
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "agent_handoff_denied"


def test_agent_handoff_api_requires_configured_permission():
    identity, account = create_handoff_identity(include_target_permission=False)
    trigger_service = TriggerService(store=InMemoryTriggerStore())
    client = TestClient(
        create_app(identity_service=identity, trigger_service=trigger_service)
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}
    source = create_source_run(client, headers)
    created = client.post(
        "/api/triggers",
        headers=headers,
        json={
            "workspace_id": "workspace_ops",
            "service_account_id": "svc_handoff",
            "type": "agent_handoff",
            "name": "Escalate renewal risk",
            "input_template": {"message": "Review delegated renewal risk."},
            "agent_handoff": {
                "target_agent_id": "agent_specialist",
                "max_depth": 2,
                "required_permissions": ["agents.handoff.escalate"],
            },
        },
    )

    response = client.post(
        f"/api/triggers/{created.json()['id']}/agent-handoff",
        headers=headers,
        json={
            "source_run_id": source["run_id"],
            "source_agent_id": "agent_primary",
            "reason_code": "specialist_review",
            "handoff_input": {"account_id": "acct_123"},
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "tenant_access_denied"
