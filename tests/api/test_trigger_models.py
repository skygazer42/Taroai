from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from taroai.domain import RunMode
from taroai.triggers import (
    InMemoryTriggerStore,
    TriggerDefinitionCreate,
    TriggerDisabledError,
    TriggerService,
    TriggerStatus,
    TriggerType,
)


def trigger_payload(**overrides) -> TriggerDefinitionCreate:
    data = {
        "tenant_id": "tenant_acme",
        "workspace_id": "workspace_ops",
        "agent_id": "agent_sla",
        "created_by_user_id": "user_ops",
        "type": TriggerType.API,
        "name": "SLA sweep",
        "input_template": {"message": "Check open SLA risk."},
        "policy_profile": "business-hours",
        "budget_profile": "automation-low",
    }
    data.update(overrides)
    return TriggerDefinitionCreate(**data)


def test_trigger_definition_is_tenant_scoped_and_accountable():
    service = TriggerService(store=InMemoryTriggerStore())

    trigger = service.create_trigger(trigger_payload())

    assert trigger.id.startswith("trigger_")
    assert trigger.tenant_id == "tenant_acme"
    assert trigger.workspace_id == "workspace_ops"
    assert trigger.created_by_user_id == "user_ops"
    assert trigger.status == TriggerStatus.ENABLED
    assert trigger.type == TriggerType.API


def test_trigger_definition_rejects_missing_accountable_identity():
    with pytest.raises(ValidationError, match="created_by_user_id or service_account_id"):
        trigger_payload(created_by_user_id=None, service_account_id=None)


def test_disabled_trigger_cannot_create_run_request():
    service = TriggerService(store=InMemoryTriggerStore())
    trigger = service.create_trigger(trigger_payload(status=TriggerStatus.DISABLED))

    with pytest.raises(TriggerDisabledError, match=trigger.id):
        service.build_run_request(
            tenant_id="tenant_acme",
            trigger_id=trigger.id,
            invoked_by_user_id="user_ops",
            invocation_payload={"ticket_id": "ticket_123"},
        )


def test_trigger_run_request_normalizes_automatic_run_context():
    service = TriggerService(store=InMemoryTriggerStore())
    trigger = service.create_trigger(
        trigger_payload(
            created_by_user_id=None,
            service_account_id="svc_sla",
            next_run_at=datetime(2026, 7, 3, 9, 0, tzinfo=timezone.utc),
        )
    )

    run_request = service.build_run_request(
        tenant_id="tenant_acme",
        trigger_id=trigger.id,
        invocation_payload={"ticket_id": "ticket_123", "priority": "high"},
    )

    assert run_request.tenant_id == "tenant_acme"
    assert run_request.workspace_id == "workspace_ops"
    assert run_request.agent_id == "agent_sla"
    assert run_request.requested_by_user_id == "svc_sla"
    assert run_request.mode == RunMode.AUTONOMOUS
    assert run_request.message == "Check open SLA risk."
    assert run_request.invocation_payload_keys == ["priority", "ticket_id"]
