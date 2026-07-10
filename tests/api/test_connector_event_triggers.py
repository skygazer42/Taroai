from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from taroai.app import create_app
from taroai.identity import (
    InMemoryIdentityService,
    PasswordHasher,
    Permission,
    Role,
    UserAccountCreate,
)
from taroai.triggers import (
    ConnectorEvent,
    ConnectorEventIngestRequest,
    InMemoryTriggerStore,
    TriggerConnectorEventConfig,
    TriggerDefinitionCreate,
    TriggerService,
    TriggerStatus,
    TriggerType,
    match_connector_event_triggers,
)


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


def connector_trigger_payload(**overrides) -> TriggerDefinitionCreate:
    data = {
        "tenant_id": "tenant_acme",
        "workspace_id": "workspace_ops",
        "agent_id": "agent_sla",
        "created_by_user_id": None,
        "service_account_id": "svc_connector",
        "type": TriggerType.CONNECTOR_EVENT,
        "name": "Escalated case review",
        "input_template": {"message": "Review escalated connector event."},
        "connector_event": TriggerConnectorEventConfig(
            connector_id="crm",
            event_type="case.updated",
            payload_equals={
                "status": "escalated",
                "customer.tier": "enterprise",
            },
        ),
    }
    data.update(overrides)
    return TriggerDefinitionCreate(**data)


def connector_event_payload(**overrides) -> ConnectorEvent:
    data = {
        "tenant_id": "tenant_acme",
        "workspace_id": "workspace_ops",
        "connector_id": "crm",
        "event_type": "case.updated",
        "external_event_id": "evt_123",
        "payload": {
            "status": "escalated",
            "case_id": "case_123",
            "customer": {"tier": "enterprise"},
        },
    }
    data.update(overrides)
    return ConnectorEvent(**data)


def test_connector_event_trigger_requires_connector_event_config():
    with pytest.raises(ValidationError, match="connector event trigger requires connector_event config"):
        connector_trigger_payload(connector_event=None)


def test_connector_event_matching_filters_by_scope_type_status_and_payload():
    service = TriggerService(store=InMemoryTriggerStore())
    matching = service.create_trigger(connector_trigger_payload())
    service.create_trigger(
        connector_trigger_payload(
            workspace_id="workspace_sales",
            name="Wrong workspace",
        )
    )
    service.create_trigger(
        connector_trigger_payload(
            name="Disabled trigger",
            status=TriggerStatus.DISABLED,
        )
    )
    service.create_trigger(
        connector_trigger_payload(
            name="Wrong event type",
            connector_event=TriggerConnectorEventConfig(
                connector_id="crm",
                event_type="case.created",
            ),
        )
    )

    matches = match_connector_event_triggers(
        service.list_triggers("tenant_acme"),
        connector_event_payload(),
    )

    assert [trigger.id for trigger in matches] == [matching.id]


def test_connector_event_ingest_api_creates_runs_for_matching_triggers_only():
    identity, account = create_trigger_admin_identity()
    trigger_service = TriggerService(store=InMemoryTriggerStore())
    client = TestClient(
        create_app(identity_service=identity, trigger_service=trigger_service)
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}
    created = client.post(
        "/api/triggers",
        headers=headers,
        json={
            "workspace_id": "workspace_ops",
            "agent_id": "agent_sla",
            "service_account_id": "svc_connector",
            "type": "connector_event",
            "name": "Escalated case review",
            "input_template": {"message": "Review escalated connector event."},
            "connector_event": {
                "connector_id": "crm",
                "event_type": "case.updated",
                "payload_equals": {
                    "status": "escalated",
                    "customer.tier": "enterprise",
                },
            },
        },
    )
    ignored = client.post(
        "/api/triggers",
        headers=headers,
        json={
            "workspace_id": "workspace_sales",
            "agent_id": "agent_sla",
            "service_account_id": "svc_connector",
            "type": "connector_event",
            "name": "Wrong workspace",
            "input_template": {"message": "This should not run."},
            "connector_event": {
                "connector_id": "crm",
                "event_type": "case.updated",
            },
        },
    )

    response = client.post(
        "/api/triggers/connector-events",
        headers=headers,
        json=ConnectorEventIngestRequest(
            workspace_id="workspace_ops",
            connector_id="crm",
            event_type="case.updated",
            external_event_id="evt_123",
            payload={
                "status": "escalated",
                "case_id": "case_123",
                "customer": {"tier": "enterprise"},
            },
        ).model_dump(mode="json"),
    )
    run = client.get(f"/api/runs/{response.json()['runs'][0]['run_id']}", headers=headers)
    audits = client.get(
        "/api/audit-events?event_type=trigger.invoked",
        headers=headers,
    )

    assert created.status_code == 201
    assert ignored.status_code == 201
    assert response.status_code == 202
    assert response.json()["matched_trigger_count"] == 1
    assert response.json()["runs"][0]["trigger_id"] == created.json()["id"]
    assert run.status_code == 200
    assert run.json()["workspace_id"] == "workspace_ops"
    assert run.json()["user_id"] == "svc_connector"
    assert run.json()["mode"] == "autonomous"
    assert run.json()["message"] == "Review escalated connector event."
    assert audits.status_code == 200
    audit_metadata = audits.json()[0]["metadata"]
    business_metadata = {
        key: audit_metadata[key]
        for key in [
            "trigger_id",
            "trigger_type",
            "run_id",
            "invocation_payload_keys",
            "connector_id",
            "connector_event_type",
            "connector_external_event_id",
        ]
    }
    assert business_metadata == {
        "trigger_id": created.json()["id"],
        "trigger_type": "connector_event",
        "run_id": response.json()["runs"][0]["run_id"],
        "invocation_payload_keys": ["case_id", "customer", "status"],
        "connector_id": "crm",
        "connector_event_type": "case.updated",
        "connector_external_event_id": "evt_123",
    }
    assert audit_metadata["audit_retention_days"] == 365
    assert audit_metadata["actor"]["user_id"] == "svc_connector"
    assert "case_123" not in str(audits.json())
