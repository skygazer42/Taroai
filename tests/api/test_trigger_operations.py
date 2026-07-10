from datetime import datetime, timedelta, timezone
from pathlib import Path

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
from taroai.store import InMemoryControlPlaneStore
from taroai.triggers import (
    InMemoryTriggerStore,
    TriggerDefinitionCreate,
    TriggerOperationsService,
    TriggerScheduleConfig,
    TriggerService,
    TriggerStatus,
    TriggerType,
)


def create_trigger_operator_identity(can_read: bool = True):
    identity = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="trigger-operator@example.com",
            display_name="Trigger Operator",
            password="correct horse battery staple",
        )
    )
    permissions = []
    if can_read:
        permissions.append(Permission(action="triggers.read", resource="tenant:tenant_acme"))
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_trigger_operator",
            name="Trigger Operator",
            permissions=permissions,
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_trigger_operator")
    return identity, account


def scheduled_trigger(**overrides) -> TriggerDefinitionCreate:
    data = {
        "tenant_id": "tenant_acme",
        "workspace_id": "workspace_ops",
        "agent_id": "agent_sla",
        "created_by_user_id": None,
        "service_account_id": "svc_scheduler",
        "type": TriggerType.SCHEDULE,
        "name": "Daily SLA sweep",
        "input_template": {"message": "Check SLA."},
        "schedule": TriggerScheduleConfig(
            cron_expression="0 9 * * *",
            timezone="UTC",
        ),
        "next_run_at": datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc),
    }
    data.update(overrides)
    return TriggerDefinitionCreate(**data)


def api_trigger(**overrides) -> TriggerDefinitionCreate:
    data = {
        "tenant_id": "tenant_acme",
        "workspace_id": "workspace_ops",
        "agent_id": "agent_sla",
        "created_by_user_id": "user_ops",
        "type": TriggerType.API,
        "name": "SLA API trigger",
        "input_template": {"message": "Check SLA."},
    }
    data.update(overrides)
    return TriggerDefinitionCreate(**data)


def test_trigger_operations_service_classifies_stuck_disabled_and_failing_triggers():
    store = InMemoryControlPlaneStore()
    trigger_service = TriggerService(store=InMemoryTriggerStore())
    now = datetime(2026, 7, 2, 9, 20, tzinfo=timezone.utc)
    stuck = trigger_service.create_trigger(scheduled_trigger())
    disabled = trigger_service.create_trigger(
        api_trigger(name="Disabled API trigger", status=TriggerStatus.DISABLED)
    )
    failing = trigger_service.create_trigger(api_trigger(name="Failing API trigger"))
    store.record_audit_event(
        tenant_id="tenant_acme",
        workspace_id="workspace_ops",
        user_id="user_ops",
        run_id=None,
        event_type="trigger.failed",
        metadata={
            "trigger_id": failing.id,
            "trigger_type": "api",
            "reason_code": "webhook_signature_invalid",
        },
    )

    response = TriggerOperationsService(stuck_after_seconds=300).summarize(
        triggers=trigger_service.list_triggers("tenant_acme"),
        audit_events=store.list_audit_events("tenant_acme"),
        now=now,
    )

    by_id = {summary.trigger_id: summary for summary in response.summaries}
    assert by_id[stuck.id].status == "stuck"
    assert by_id[stuck.id].status_reason == "schedule_next_run_overdue"
    assert by_id[disabled.id].status == "disabled"
    assert by_id[failing.id].status == "failing"
    assert by_id[failing.id].last_failure_reason_code == "webhook_signature_invalid"
    assert response.counts == {
        "disabled": 1,
        "failing": 1,
        "healthy": 0,
        "stuck": 1,
    }


def test_trigger_operations_api_requires_read_permission_and_returns_summaries():
    identity, account = create_trigger_operator_identity(can_read=True)
    trigger_service = TriggerService(store=InMemoryTriggerStore())
    settings = Settings(trigger_operations_stuck_after_seconds=60, _env_file=None)
    client = TestClient(
        create_app(
            settings=settings,
            identity_service=identity,
            trigger_service=trigger_service,
        )
    )
    trigger = trigger_service.create_trigger(
        scheduled_trigger(
            next_run_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}

    response = client.get("/api/triggers/operations", headers=headers)

    assert response.status_code == 200
    assert response.json()["tenant_id"] == "tenant_acme"
    assert response.json()["counts"]["stuck"] == 1
    assert response.json()["summaries"][0]["trigger_id"] == trigger.id
    assert response.json()["summaries"][0]["status"] == "stuck"
    assert response.json()["summaries"][0]["recommended_action"] == "inspect_trigger_scheduler_worker"


def test_trigger_operations_api_returns_tenant_id_when_no_triggers():
    identity, account = create_trigger_operator_identity(can_read=True)
    client = TestClient(create_app(identity_service=identity))

    response = client.get(
        "/api/triggers/operations",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id},
    )

    assert response.status_code == 200
    assert response.json()["tenant_id"] == "tenant_acme"
    assert response.json()["summaries"] == []


def test_trigger_operations_api_denies_missing_read_permission():
    identity, account = create_trigger_operator_identity(can_read=False)
    client = TestClient(create_app(identity_service=identity))

    response = client.get(
        "/api/triggers/operations",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "tenant_access_denied"


def test_trigger_operations_runbook_documents_endpoint_workers_and_failure_triage():
    runbook = Path("docs/operations/triggers-runbook.md")

    content = runbook.read_text()

    assert "GET /api/triggers/operations" in content
    assert "trigger_scheduler" in content
    assert "trigger_due" in content
    assert "webhook_signature_invalid" in content
    assert "trigger.failed" in content
