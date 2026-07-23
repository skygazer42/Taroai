from datetime import datetime, timezone

from taroai.audit import AuditService
from taroai.store import InMemoryControlPlaneStore
from taroai.triggers import (
    InMemoryTriggerStore,
    TriggerDefinitionCreate,
    TriggerScheduleConfig,
    TriggerService,
    TriggerType,
)
from taroai.workers import (
    InMemoryJobQueue,
    JobStatus,
    JobType,
    RunExecutionJob,
    TriggerDueJob,
    TriggerDueWorker,
    TriggerSchedulerWorker,
)


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


def test_trigger_scheduler_worker_enqueues_due_jobs_and_persists_next_run_at():
    trigger_service = TriggerService(store=InMemoryTriggerStore())
    trigger = trigger_service.create_trigger(scheduled_trigger_payload())
    queue = InMemoryJobQueue()
    worker = TriggerSchedulerWorker(
        trigger_service=trigger_service,
        queue=queue,
        worker_id="trigger_scheduler_1",
    )

    result = worker.process_due(now=datetime(2026, 7, 2, 9, 5, tzinfo=timezone.utc))
    repeated_result = worker.process_due(
        now=datetime(2026, 7, 2, 9, 5, tzinfo=timezone.utc)
    )

    assert result.scanned_triggers == 1
    assert result.enqueued_jobs == 1
    assert result.updated_triggers == 1
    assert repeated_result.enqueued_jobs == 0
    assert len(queue.jobs) == 1
    queued_job = queue.jobs[0]
    payload = TriggerDueJob.model_validate(queued_job.payload)
    assert queued_job.type == JobType.TRIGGER_DUE
    assert queued_job.status == JobStatus.PENDING
    assert payload.tenant_id == "tenant_acme"
    assert payload.workspace_id == "workspace_ops"
    assert payload.trigger_id == trigger.id
    assert payload.requested_by_user_id == "svc_scheduler"
    assert payload.scheduled_for == datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc)
    assert (
        trigger_service.get_trigger("tenant_acme", trigger.id).next_run_at
        == datetime(2026, 7, 3, 9, 0, tzinfo=timezone.utc)
    )


def test_trigger_scheduler_worker_records_safe_schedule_audit_metadata():
    audit_store = InMemoryControlPlaneStore()
    trigger_service = TriggerService(store=InMemoryTriggerStore())
    trigger = trigger_service.create_trigger(scheduled_trigger_payload())
    worker = TriggerSchedulerWorker(
        trigger_service=trigger_service,
        queue=InMemoryJobQueue(),
        audit_service=AuditService(store=audit_store),
        worker_id="trigger_scheduler_1",
    )

    worker.process_due(now=datetime(2026, 7, 2, 9, 5, tzinfo=timezone.utc))

    events = [
        event
        for event in audit_store.list_audit_events("tenant_acme")
        if event.event_type == "trigger.schedule.evaluated"
    ]
    assert len(events) == 1
    assert events[0].workspace_id == "workspace_ops"
    assert events[0].user_id == "svc_scheduler"
    assert events[0].metadata["trigger_id"] == trigger.id
    assert events[0].metadata["worker_id"] == "trigger_scheduler_1"
    assert events[0].metadata["due_job_count"] == 1
    assert events[0].metadata["next_run_at"] == "2026-07-03T09:00:00Z"
    assert events[0].metadata["actor"]["actor_type"] == "worker"
    assert "input_template" not in events[0].metadata


def test_trigger_due_worker_creates_run_and_queues_execution():
    store = InMemoryControlPlaneStore()
    trigger_service = TriggerService(store=InMemoryTriggerStore())
    trigger = trigger_service.create_trigger(scheduled_trigger_payload())
    queue = InMemoryJobQueue()
    due_job = queue.enqueue(
        JobType.TRIGGER_DUE,
        TriggerDueJob(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            trigger_id=trigger.id,
            trigger_type="schedule",
            scheduled_for=datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc),
            requested_by_user_id="svc_scheduler",
        ),
    )
    worker = TriggerDueWorker(
        store=store,
        trigger_service=trigger_service,
        queue=queue,
        worker_id="trigger_due_1",
    )

    processed = worker.process_next()

    assert processed is not None
    assert processed.id == due_job.id
    assert processed.status == JobStatus.SUCCEEDED
    runs = store.list_runs("tenant_acme")
    assert len(runs) == 1
    run = runs[0]
    assert run.workspace_id == "workspace_ops"
    assert run.user_id == "svc_scheduler"
    assert run.agent_id == "agent_sla"
    assert run.message == "Check open SLA risk."
    assert run.thread_id is not None
    assert run.trigger_message_id is not None
    thread = store.get_chat_thread("tenant_acme", run.thread_id)
    messages = store.list_chat_messages("tenant_acme", thread.id)
    assert thread.title == "Check open SLA risk."
    assert messages[0].id == run.trigger_message_id
    assert messages[0].resource_refs[0].id == "agent_sla"
    assert queue.get(due_job.id).status == JobStatus.SUCCEEDED
    execution_jobs = [job for job in queue.jobs if job.type == JobType.RUN_EXECUTION]
    assert len(execution_jobs) == 1
    execution_payload = RunExecutionJob.model_validate(execution_jobs[0].payload)
    assert execution_payload.run_id == run.id
    assert execution_payload.user_id == "svc_scheduler"
    assert execution_payload.requested_by_user_id == "svc_scheduler"


def test_trigger_due_worker_records_trigger_audit_and_meter():
    store = InMemoryControlPlaneStore()
    trigger_service = TriggerService(store=InMemoryTriggerStore())
    trigger = trigger_service.create_trigger(scheduled_trigger_payload())
    queue = InMemoryJobQueue()
    queue.enqueue(
        JobType.TRIGGER_DUE,
        TriggerDueJob(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            trigger_id=trigger.id,
            trigger_type="schedule",
            scheduled_for=datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc),
            requested_by_user_id="svc_scheduler",
        ),
    )
    worker = TriggerDueWorker(
        store=store,
        trigger_service=trigger_service,
        queue=queue,
        worker_id="trigger_due_1",
    )

    worker.process_next()

    run = store.list_runs("tenant_acme")[0]
    trigger_events = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "trigger.invoked"
    ]
    meters = [
        meter
        for meter in store.list_billing_meters("tenant_acme")
        if meter.meter_type == "trigger_invocation_count"
    ]
    assert len(trigger_events) == 1
    assert trigger_events[0].workspace_id == "workspace_ops"
    assert trigger_events[0].user_id == "svc_scheduler"
    assert trigger_events[0].run_id == run.id
    trigger_metadata = trigger_events[0].metadata
    business_metadata = {
        key: trigger_metadata[key]
        for key in [
            "trigger_id",
            "trigger_type",
            "run_id",
            "scheduled_for",
            "worker_id",
        ]
    }
    assert business_metadata == {
        "trigger_id": trigger.id,
        "trigger_type": "schedule",
        "run_id": run.id,
        "scheduled_for": "2026-07-02T09:00:00Z",
        "worker_id": "trigger_due_1",
    }
    assert trigger_metadata["audit_retention_days"] == 365
    assert trigger_metadata["audit_retention_expires_at"]
    assert trigger_metadata["actor"] == {
        "tenant_id": "tenant_acme",
        "user_id": "svc_scheduler",
        "actor_type": "worker",
    }
    assert len(meters) == 1
    assert meters[0].run_id == run.id
    assert meters[0].metadata == {
        "trigger_id": trigger.id,
        "trigger_type": "schedule",
    }
