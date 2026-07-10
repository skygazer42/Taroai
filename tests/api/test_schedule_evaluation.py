from datetime import datetime, timezone

from taroai.triggers import (
    InMemoryTriggerStore,
    TriggerDefinitionCreate,
    TriggerScheduleConfig,
    TriggerService,
    TriggerStatus,
    TriggerType,
    evaluate_trigger_schedule,
)
from taroai.workers import JobType


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
        "schedule": TriggerScheduleConfig(
            cron_expression="0 9 * * *",
            timezone="UTC",
            max_catch_up_runs=2,
        ),
        "next_run_at": datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc),
    }
    data.update(overrides)
    return TriggerDefinitionCreate(**data)


def test_daily_schedule_emits_trigger_due_job_and_next_run_time():
    service = TriggerService(store=InMemoryTriggerStore())
    trigger = service.create_trigger(scheduled_trigger_payload())

    result = evaluate_trigger_schedule(
        trigger,
        now=datetime(2026, 7, 2, 9, 5, tzinfo=timezone.utc),
    )

    assert len(result.due_jobs) == 1
    assert result.due_jobs[0].tenant_id == "tenant_acme"
    assert result.due_jobs[0].workspace_id == "workspace_ops"
    assert result.due_jobs[0].trigger_id == trigger.id
    assert result.due_jobs[0].requested_by_user_id == "svc_scheduler"
    assert result.due_jobs[0].scheduled_for == datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc)
    assert result.job_type == JobType.TRIGGER_DUE
    assert result.next_run_at == datetime(2026, 7, 3, 9, 0, tzinfo=timezone.utc)


def test_disabled_schedule_trigger_does_not_emit_due_jobs():
    service = TriggerService(store=InMemoryTriggerStore())
    trigger = service.create_trigger(
        scheduled_trigger_payload(status=TriggerStatus.DISABLED)
    )

    result = evaluate_trigger_schedule(
        trigger,
        now=datetime(2026, 7, 2, 9, 5, tzinfo=timezone.utc),
    )

    assert result.due_jobs == []
    assert result.next_run_at == trigger.next_run_at


def test_expired_schedule_trigger_does_not_emit_due_jobs():
    service = TriggerService(store=InMemoryTriggerStore())
    trigger = service.create_trigger(
        scheduled_trigger_payload(
            schedule=TriggerScheduleConfig(
                cron_expression="0 9 * * *",
                timezone="UTC",
                ends_at=datetime(2026, 7, 1, 23, 59, tzinfo=timezone.utc),
                max_catch_up_runs=2,
            )
        )
    )

    result = evaluate_trigger_schedule(
        trigger,
        now=datetime(2026, 7, 2, 9, 5, tzinfo=timezone.utc),
    )

    assert result.due_jobs == []
    assert result.next_run_at is None


def test_schedule_evaluation_caps_catch_up_jobs_after_downtime():
    service = TriggerService(store=InMemoryTriggerStore())
    trigger = service.create_trigger(
        scheduled_trigger_payload(
            schedule=TriggerScheduleConfig(
                cron_expression="0 9 * * *",
                timezone="UTC",
                max_catch_up_runs=2,
            ),
            next_run_at=datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc),
        )
    )

    result = evaluate_trigger_schedule(
        trigger,
        now=datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc),
    )

    assert [job.scheduled_for for job in result.due_jobs] == [
        datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc),
    ]
    assert result.next_run_at == datetime(2026, 7, 3, 9, 0, tzinfo=timezone.utc)
