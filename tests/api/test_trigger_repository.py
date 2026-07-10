from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from taroai.app import create_app
from taroai.config import Settings
from taroai.db import DatabaseConfig, MigrationRunner
from taroai.identity import (
    InMemoryIdentityService,
    PasswordHasher,
    Permission,
    Role,
    UserAccountCreate,
)
from taroai.triggers import (
    SqlTriggerStore,
    TriggerAgentHandoffConfig,
    TriggerConnectorEventConfig,
    TriggerDefinitionCreate,
    TriggerScheduleConfig,
    TriggerService,
    TriggerStatus,
    TriggerType,
)


def prepare_database(path: Path) -> DatabaseConfig:
    config = DatabaseConfig(url=f"sqlite:///{path}")
    MigrationRunner(
        config=config,
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    return config


def scheduled_trigger_payload(**overrides) -> TriggerDefinitionCreate:
    data = {
        "tenant_id": "tenant_acme",
        "workspace_id": "workspace_ops",
        "agent_id": "agent_sla",
        "created_by_user_id": None,
        "service_account_id": "svc_scheduler",
        "type": TriggerType.SCHEDULE,
        "name": "Daily SLA sweep",
        "input_template": {"message": "Check open SLA risk."},
        "policy_profile": "business-hours",
        "budget_profile": "automation-low",
        "schedule": TriggerScheduleConfig(
            cron_expression="0 9 * * *",
            timezone="UTC",
            max_catch_up_runs=2,
        ),
        "next_run_at": datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc),
    }
    data.update(overrides)
    return TriggerDefinitionCreate(**data)


def connector_event_trigger_payload(**overrides) -> TriggerDefinitionCreate:
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
            payload_equals={"status": "escalated"},
        ),
    }
    data.update(overrides)
    return TriggerDefinitionCreate(**data)


def agent_handoff_trigger_payload(**overrides) -> TriggerDefinitionCreate:
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


def test_sql_trigger_store_persists_trigger_definition_and_schedule(tmp_path: Path):
    config = prepare_database(tmp_path / "triggers.sqlite3")
    first_service = TriggerService(store=SqlTriggerStore(config=config))
    trigger = first_service.create_trigger(scheduled_trigger_payload())

    second_service = TriggerService(store=SqlTriggerStore(config=config))
    loaded = second_service.get_trigger("tenant_acme", trigger.id)

    assert loaded.id == trigger.id
    assert loaded.tenant_id == "tenant_acme"
    assert loaded.workspace_id == "workspace_ops"
    assert loaded.agent_id == "agent_sla"
    assert loaded.service_account_id == "svc_scheduler"
    assert loaded.type == TriggerType.SCHEDULE
    assert loaded.status == TriggerStatus.ENABLED
    assert loaded.input_template == {"message": "Check open SLA risk."}
    assert loaded.policy_profile == "business-hours"
    assert loaded.budget_profile == "automation-low"
    assert loaded.schedule is not None
    assert loaded.schedule.cron_expression == "0 9 * * *"
    assert loaded.schedule.timezone == "UTC"
    assert loaded.schedule.max_catch_up_runs == 2
    assert loaded.next_run_at == datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc)


def test_sql_trigger_store_persists_connector_event_config(tmp_path: Path):
    config = prepare_database(tmp_path / "connector-events.sqlite3")
    first_service = TriggerService(store=SqlTriggerStore(config=config))
    trigger = first_service.create_trigger(connector_event_trigger_payload())

    second_service = TriggerService(store=SqlTriggerStore(config=config))
    loaded = second_service.get_trigger("tenant_acme", trigger.id)

    assert loaded.type == TriggerType.CONNECTOR_EVENT
    assert loaded.connector_event is not None
    assert loaded.connector_event.connector_id == "crm"
    assert loaded.connector_event.event_type == "case.updated"
    assert loaded.connector_event.payload_equals == {"status": "escalated"}


def test_sql_trigger_store_persists_agent_handoff_config(tmp_path: Path):
    config = prepare_database(tmp_path / "agent-handoffs.sqlite3")
    first_service = TriggerService(store=SqlTriggerStore(config=config))
    trigger = first_service.create_trigger(agent_handoff_trigger_payload())

    second_service = TriggerService(store=SqlTriggerStore(config=config))
    loaded = second_service.get_trigger("tenant_acme", trigger.id)

    assert loaded.type == TriggerType.AGENT_HANDOFF
    assert loaded.agent_handoff is not None
    assert loaded.agent_handoff.target_agent_id == "agent_specialist"
    assert loaded.agent_handoff.max_depth == 2
    assert loaded.agent_handoff.required_permissions == ["agents.handoff.escalate"]


def test_sql_trigger_store_updates_status_and_next_run_at(tmp_path: Path):
    config = prepare_database(tmp_path / "trigger-updates.sqlite3")
    service = TriggerService(store=SqlTriggerStore(config=config))
    trigger = service.create_trigger(scheduled_trigger_payload())

    disabled = service.disable_trigger("tenant_acme", trigger.id)
    updated = service.update_next_run_at(
        "tenant_acme",
        trigger.id,
        datetime(2026, 7, 3, 9, 0, tzinfo=timezone.utc),
    )

    assert disabled.status == TriggerStatus.DISABLED
    assert updated.status == TriggerStatus.DISABLED
    assert updated.next_run_at == datetime(2026, 7, 3, 9, 0, tzinfo=timezone.utc)
    assert service.list_schedule_triggers()[0].id == trigger.id


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
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_trigger_admin")
    return identity, account


def test_create_app_uses_sql_trigger_store_from_settings(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'app-triggers.sqlite3'}"
    settings = Settings(
        database_url=database_url,
        trigger_store_backend="sql",
        _env_file=None,
    )
    identity, account = create_trigger_admin_identity()
    first_client = TestClient(create_app(settings=settings, identity_service=identity))
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": account.id}

    created = first_client.post(
        "/api/triggers",
        headers=headers,
        json={
            "workspace_id": "workspace_ops",
            "agent_id": "agent_sla",
            "type": "schedule",
            "name": "Daily SLA sweep",
            "input_template": {"message": "Check open SLA risk."},
            "schedule": {
                "cron_expression": "0 9 * * *",
                "timezone": "UTC",
                "max_catch_up_runs": 2,
            },
            "next_run_at": "2026-07-02T09:00:00Z",
        },
    )
    second_client = TestClient(create_app(settings=settings, identity_service=identity))
    listed = second_client.get("/api/triggers", headers=headers)

    assert created.status_code == 201
    assert listed.status_code == 200
    assert [trigger["id"] for trigger in listed.json()] == [created.json()["id"]]
