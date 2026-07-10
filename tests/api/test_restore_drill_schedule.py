from datetime import datetime, timezone
from pathlib import Path

from taroai.audit import AuditService
from taroai.db import DatabaseConfig, MigrationRunner
from taroai.lifecycle.restore_drill import (
    InMemoryRestoreDrillScheduleStore,
    RestoreDrillRunRecord,
    RestoreDrillScheduleCreate,
    RestoreDrillRunStatus,
    RestoreDrillScheduleStatus,
    SqlRestoreDrillScheduleStore,
)
from taroai.store import InMemoryControlPlaneStore
from taroai.workers import (
    InMemoryJobQueue,
    JobStatus,
    JobType,
    RestoreDrillDueJob,
    RestoreDrillDueWorker,
    RestoreDrillSchedulerWorker,
)


def restore_drill_schedule(**overrides) -> RestoreDrillScheduleCreate:
    data = {
        "tenant_id": "tenant_acme",
        "workspace_id": "workspace_ops",
        "name": "Monthly private restore drill",
        "service_account_id": "svc_restore_drill",
        "interval_days": 30,
        "max_catch_up_runs": 2,
        "runbook_ref": "docs/operations/disaster-recovery.md",
        "next_run_at": datetime(2026, 7, 1, 2, 0, tzinfo=timezone.utc),
    }
    data.update(overrides)
    return RestoreDrillScheduleCreate(**data)


def test_restore_drill_scheduler_worker_enqueues_due_job_and_advances_schedule():
    schedule_store = InMemoryRestoreDrillScheduleStore()
    schedule = schedule_store.create_schedule(restore_drill_schedule())
    queue = InMemoryJobQueue()
    worker = RestoreDrillSchedulerWorker(
        schedule_store=schedule_store,
        queue=queue,
        worker_id="restore_drill_scheduler_1",
    )

    result = worker.process_due(now=datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc))
    repeated = worker.process_due(now=datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc))

    assert result.scanned_schedules == 1
    assert result.enqueued_jobs == 1
    assert result.updated_schedules == 1
    assert repeated.enqueued_jobs == 0
    assert len(queue.jobs) == 1
    queued_job = queue.jobs[0]
    payload = RestoreDrillDueJob.model_validate(queued_job.payload)
    assert queued_job.type == JobType.RESTORE_DRILL_DUE
    assert queued_job.status == JobStatus.PENDING
    assert payload.tenant_id == "tenant_acme"
    assert payload.workspace_id == "workspace_ops"
    assert payload.schedule_id == schedule.id
    assert payload.requested_by_user_id == "svc_restore_drill"
    assert payload.scheduled_for == datetime(2026, 7, 1, 2, 0, tzinfo=timezone.utc)
    assert payload.runbook_ref == "docs/operations/disaster-recovery.md"
    assert (
        schedule_store.get_schedule("tenant_acme", schedule.id).next_run_at
        == datetime(2026, 7, 31, 2, 0, tzinfo=timezone.utc)
    )


def test_restore_drill_scheduler_worker_skips_disabled_schedule():
    schedule_store = InMemoryRestoreDrillScheduleStore()
    schedule_store.create_schedule(
        restore_drill_schedule(status=RestoreDrillScheduleStatus.DISABLED)
    )
    queue = InMemoryJobQueue()
    worker = RestoreDrillSchedulerWorker(
        schedule_store=schedule_store,
        queue=queue,
        worker_id="restore_drill_scheduler_1",
    )

    result = worker.process_due(now=datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc))

    assert result.scanned_schedules == 1
    assert result.enqueued_jobs == 0
    assert queue.jobs == []


def test_restore_drill_scheduler_worker_records_safe_audit_metadata():
    audit_store = InMemoryControlPlaneStore()
    schedule_store = InMemoryRestoreDrillScheduleStore()
    schedule = schedule_store.create_schedule(restore_drill_schedule())
    worker = RestoreDrillSchedulerWorker(
        schedule_store=schedule_store,
        queue=InMemoryJobQueue(),
        audit_service=AuditService(store=audit_store),
        worker_id="restore_drill_scheduler_1",
    )

    worker.process_due(now=datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc))

    events = [
        event
        for event in audit_store.list_audit_events("tenant_acme")
        if event.event_type == "restore_drill.schedule.evaluated"
    ]
    assert len(events) == 1
    assert events[0].workspace_id == "workspace_ops"
    assert events[0].user_id == "svc_restore_drill"
    assert events[0].metadata["schedule_id"] == schedule.id
    assert events[0].metadata["worker_id"] == "restore_drill_scheduler_1"
    assert events[0].metadata["job_type"] == "restore_drill.due"
    assert events[0].metadata["due_job_count"] == 1
    assert events[0].metadata["next_run_at"] == "2026-07-31T02:00:00Z"
    assert events[0].metadata["actor"]["actor_type"] == "worker"
    assert "backup_manifest_path" not in events[0].metadata
    assert "object_storage_verification_path" not in events[0].metadata


def test_restore_drill_due_worker_creates_run_record_and_acknowledges_job():
    audit_store = InMemoryControlPlaneStore()
    schedule_store = InMemoryRestoreDrillScheduleStore()
    schedule = schedule_store.create_schedule(restore_drill_schedule())
    queue = InMemoryJobQueue()
    queued = queue.enqueue(
        JobType.RESTORE_DRILL_DUE,
        RestoreDrillDueJob(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            schedule_id=schedule.id,
            scheduled_for=datetime(2026, 7, 1, 2, 0, tzinfo=timezone.utc),
            requested_by_user_id="svc_restore_drill",
            runbook_ref="docs/operations/disaster-recovery.md",
        ),
        now=datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc),
    )
    worker = RestoreDrillDueWorker(
        schedule_store=schedule_store,
        queue=queue,
        audit_service=AuditService(store=audit_store),
        worker_id="restore_drill_due_1",
    )

    processed = worker.process_next(now=datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc))

    assert processed is not None
    assert processed.id == queued.id
    assert processed.status == JobStatus.SUCCEEDED
    records = schedule_store.list_run_records("tenant_acme", schedule.id)
    assert len(records) == 1
    record = records[0]
    assert record.status == RestoreDrillRunStatus.REQUESTED
    assert record.schedule_id == schedule.id
    assert record.requested_by_user_id == "svc_restore_drill"
    assert record.scheduled_for == datetime(2026, 7, 1, 2, 0, tzinfo=timezone.utc)
    assert record.runbook_ref == "docs/operations/disaster-recovery.md"

    events = audit_store.list_audit_events("tenant_acme")
    event_types = [event.event_type for event in events]
    assert "worker.job.started" in event_types
    assert "restore_drill.requested" in event_types
    assert "worker.job.succeeded" in event_types
    requested_event = next(
        event for event in events if event.event_type == "restore_drill.requested"
    )
    assert requested_event.metadata["schedule_id"] == schedule.id
    assert requested_event.metadata["run_record_id"] == record.id
    assert requested_event.metadata["worker_id"] == "restore_drill_due_1"
    assert "backup_manifest_path" not in requested_event.metadata
    assert "object_storage_verification_path" not in requested_event.metadata


def test_restore_drill_due_worker_skips_queued_job_for_disabled_schedule():
    audit_store = InMemoryControlPlaneStore()
    schedule_store = InMemoryRestoreDrillScheduleStore()
    schedule = schedule_store.create_schedule(restore_drill_schedule())
    schedule_store.update_schedule_status(
        tenant_id=schedule.tenant_id,
        schedule_id=schedule.id,
        status=RestoreDrillScheduleStatus.DISABLED,
    )
    queue = InMemoryJobQueue()
    queued = queue.enqueue(
        JobType.RESTORE_DRILL_DUE,
        RestoreDrillDueJob(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            schedule_id=schedule.id,
            scheduled_for=datetime(2026, 7, 1, 2, 0, tzinfo=timezone.utc),
            requested_by_user_id="svc_restore_drill",
            runbook_ref="docs/operations/disaster-recovery.md",
        ),
        now=datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc),
    )
    worker = RestoreDrillDueWorker(
        schedule_store=schedule_store,
        queue=queue,
        audit_service=AuditService(store=audit_store),
        worker_id="restore_drill_due_1",
    )

    processed = worker.process_next(now=datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc))

    assert processed is not None
    assert processed.id == queued.id
    assert processed.status == JobStatus.SUCCEEDED
    assert schedule_store.list_run_records("tenant_acme", schedule.id) == []
    events = audit_store.list_audit_events("tenant_acme")
    skipped_event = next(
        event for event in events if event.event_type == "restore_drill.skipped"
    )
    assert skipped_event.metadata["schedule_id"] == schedule.id
    assert skipped_event.metadata["skip_reason"] == "schedule_disabled"
    succeeded_event = next(
        event for event in events if event.event_type == "worker.job.succeeded"
    )
    assert succeeded_event.metadata["skipped"] is True
    assert succeeded_event.metadata["skip_reason"] == "schedule_disabled"
    assert "run_record_id" not in succeeded_event.metadata


def test_restore_drill_due_worker_skips_duplicate_queued_job():
    audit_store = InMemoryControlPlaneStore()
    schedule_store = InMemoryRestoreDrillScheduleStore()
    schedule = schedule_store.create_schedule(restore_drill_schedule())
    queue = InMemoryJobQueue()
    payload = RestoreDrillDueJob(
        tenant_id="tenant_acme",
        workspace_id="workspace_ops",
        schedule_id=schedule.id,
        scheduled_for=datetime(2026, 7, 1, 2, 0, tzinfo=timezone.utc),
        requested_by_user_id="svc_restore_drill",
        runbook_ref="docs/operations/disaster-recovery.md",
    )
    first_job = queue.enqueue(
        JobType.RESTORE_DRILL_DUE,
        payload,
        now=datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc),
    )
    duplicate_job = queue.enqueue(
        JobType.RESTORE_DRILL_DUE,
        payload,
        now=datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc),
    )
    worker = RestoreDrillDueWorker(
        schedule_store=schedule_store,
        queue=queue,
        audit_service=AuditService(store=audit_store),
        worker_id="restore_drill_due_1",
    )

    first_processed = worker.process_next(now=datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc))
    duplicate_processed = worker.process_next(
        now=datetime(2026, 7, 4, 12, 1, tzinfo=timezone.utc)
    )

    assert first_processed is not None
    assert first_processed.id == first_job.id
    assert first_processed.status == JobStatus.SUCCEEDED
    assert duplicate_processed is not None
    assert duplicate_processed.id == duplicate_job.id
    assert duplicate_processed.status == JobStatus.SUCCEEDED
    records = schedule_store.list_run_records("tenant_acme", schedule.id)
    assert len(records) == 1
    skipped_event = next(
        event
        for event in audit_store.list_audit_events("tenant_acme")
        if event.event_type == "restore_drill.skipped"
    )
    assert skipped_event.metadata["skip_reason"] == "run_record_exists"
    assert skipped_event.metadata["existing_run_record_id"] == records[0].id
    duplicate_succeeded_event = [
        event
        for event in audit_store.list_audit_events("tenant_acme")
        if event.event_type == "worker.job.succeeded"
        and event.metadata["job_id"] == duplicate_job.id
    ][0]
    assert duplicate_succeeded_event.metadata["skipped"] is True
    assert duplicate_succeeded_event.metadata["skip_reason"] == "run_record_exists"
    assert duplicate_succeeded_event.metadata["existing_run_record_id"] == records[0].id


def test_restore_drill_due_worker_rejects_unknown_schedule_with_safe_audit():
    audit_store = InMemoryControlPlaneStore()
    queue = InMemoryJobQueue()
    queued = queue.enqueue(
        JobType.RESTORE_DRILL_DUE,
        RestoreDrillDueJob(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            schedule_id="restore_drill_schedule_missing",
            scheduled_for=datetime(2026, 7, 1, 2, 0, tzinfo=timezone.utc),
            requested_by_user_id="svc_restore_drill",
            runbook_ref="docs/operations/disaster-recovery.md",
        ),
        now=datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc),
        max_attempts=1,
    )
    worker = RestoreDrillDueWorker(
        schedule_store=InMemoryRestoreDrillScheduleStore(),
        queue=queue,
        audit_service=AuditService(store=audit_store),
        worker_id="restore_drill_due_1",
    )

    processed = worker.process_next(now=datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc))

    assert processed is not None
    assert processed.id == queued.id
    assert processed.status == JobStatus.DEAD_LETTER
    events = audit_store.list_audit_events("tenant_acme")
    event_types = [event.event_type for event in events]
    assert event_types.index("worker.job.started") < event_types.index("worker.job.failed")
    started_event = next(
        event for event in events if event.event_type == "worker.job.started"
    )
    assert started_event.metadata["schedule_id"] == "restore_drill_schedule_missing"
    assert started_event.metadata["job_id"] == queued.id
    failed_event = next(
        event
        for event in events
        if event.event_type == "worker.job.failed"
    )
    assert failed_event.metadata["schedule_id"] == "restore_drill_schedule_missing"
    assert failed_event.metadata["final_job_status"] == "dead_letter"
    assert "backup_manifest_path" not in failed_event.metadata
    assert "object_storage_verification_path" not in failed_event.metadata


def test_restore_drill_due_worker_rejects_payload_that_does_not_match_schedule():
    audit_store = InMemoryControlPlaneStore()
    schedule_store = InMemoryRestoreDrillScheduleStore()
    schedule = schedule_store.create_schedule(restore_drill_schedule())
    queue = InMemoryJobQueue()
    queued = queue.enqueue(
        JobType.RESTORE_DRILL_DUE,
        RestoreDrillDueJob(
            tenant_id="tenant_acme",
            workspace_id="workspace_other",
            schedule_id=schedule.id,
            scheduled_for=datetime(2026, 7, 1, 2, 0, tzinfo=timezone.utc),
            requested_by_user_id="svc_other",
            runbook_ref="docs/operations/other-runbook.md",
        ),
        now=datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc),
        max_attempts=1,
    )
    worker = RestoreDrillDueWorker(
        schedule_store=schedule_store,
        queue=queue,
        audit_service=AuditService(store=audit_store),
        worker_id="restore_drill_due_1",
    )

    processed = worker.process_next(now=datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc))

    assert processed is not None
    assert processed.id == queued.id
    assert processed.status == JobStatus.DEAD_LETTER
    assert schedule_store.list_run_records("tenant_acme", schedule.id) == []
    failed_event = next(
        event
        for event in audit_store.list_audit_events("tenant_acme")
        if event.event_type == "worker.job.failed"
    )
    assert failed_event.workspace_id == "workspace_ops"
    assert failed_event.user_id == "svc_restore_drill"
    assert failed_event.metadata["schedule_id"] == schedule.id
    assert failed_event.metadata["error_type"] == "ValueError"
    assert failed_event.metadata["error"] == "restore drill due job does not match schedule"
    assert failed_event.metadata["final_job_status"] == "dead_letter"
    assert failed_event.metadata["requested_by_user_id"] == "svc_restore_drill"
    assert "workspace_other" not in str(failed_event.metadata)
    assert "svc_other" not in str(failed_event.metadata)
    assert "docs/operations/other-runbook.md" not in str(failed_event.metadata)


def test_sql_restore_drill_schedule_store_persists_schedules_and_run_records(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'restore-drills.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    store = SqlRestoreDrillScheduleStore(config=DatabaseConfig(url=database_url))
    next_run_at = datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc)

    schedule = store.create_schedule(
        restore_drill_schedule(
            next_run_at=next_run_at,
            interval_days=14,
        )
    )
    updated_schedule = store.update_next_run_at(
        tenant_id=schedule.tenant_id,
        schedule_id=schedule.id,
        next_run_at=datetime(2026, 7, 16, 9, 0, tzinfo=timezone.utc),
    )
    run_record = store.create_run_record(
        RestoreDrillRunRecord(
            tenant_id=schedule.tenant_id,
            workspace_id=schedule.workspace_id,
            schedule_id=schedule.id,
            scheduled_for=next_run_at,
            requested_by_user_id="svc_restore_drill",
            runbook_ref=schedule.runbook_ref,
            status=RestoreDrillRunStatus.EVIDENCE_READY,
            evidence_object_id="storage_restore_drill_evidence",
        )
    )

    restarted = SqlRestoreDrillScheduleStore(config=DatabaseConfig(url=database_url))

    assert restarted.get_schedule(schedule.tenant_id, schedule.id) == updated_schedule
    assert restarted.list_schedules() == [updated_schedule]
    assert restarted.list_run_records(schedule.tenant_id, schedule.id) == [run_record]


def test_restore_drill_schedule_stores_update_schedule_status(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'restore-drill-schedule-status.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    sql_store = SqlRestoreDrillScheduleStore(config=DatabaseConfig(url=database_url))

    for store in [InMemoryRestoreDrillScheduleStore(), sql_store]:
        schedule = store.create_schedule(restore_drill_schedule())

        disabled = store.update_schedule_status(
            tenant_id=schedule.tenant_id,
            schedule_id=schedule.id,
            status=RestoreDrillScheduleStatus.DISABLED,
        )

        assert disabled.status == RestoreDrillScheduleStatus.DISABLED
        assert store.get_schedule(schedule.tenant_id, schedule.id) == disabled


def test_restore_drill_schedule_stores_update_run_record_status(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'restore-drill-status.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    sql_store = SqlRestoreDrillScheduleStore(config=DatabaseConfig(url=database_url))

    for store in [InMemoryRestoreDrillScheduleStore(), sql_store]:
        schedule = store.create_schedule(restore_drill_schedule())
        record = store.create_run_record(
            RestoreDrillRunRecord(
                tenant_id=schedule.tenant_id,
                workspace_id=schedule.workspace_id,
                schedule_id=schedule.id,
                scheduled_for=datetime(2026, 7, 1, 2, 0, tzinfo=timezone.utc),
                requested_by_user_id="svc_restore_drill",
                runbook_ref=schedule.runbook_ref,
            )
        )

        updated = store.update_run_record_status(
            tenant_id=schedule.tenant_id,
            run_record_id=record.id,
            status=RestoreDrillRunStatus.EVIDENCE_READY,
            evidence_object_id="storage_restore_drill_evidence",
        )

        assert updated.status == RestoreDrillRunStatus.EVIDENCE_READY
        assert updated.evidence_object_id == "storage_restore_drill_evidence"
        assert store.list_run_records(schedule.tenant_id, schedule.id) == [updated]


def test_restore_drill_schedule_stores_get_run_record_by_schedule_time(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'restore-drill-run-record-lookup.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    sql_store = SqlRestoreDrillScheduleStore(config=DatabaseConfig(url=database_url))
    scheduled_for = datetime(2026, 7, 1, 2, 0, tzinfo=timezone.utc)

    for store in [InMemoryRestoreDrillScheduleStore(), sql_store]:
        schedule = store.create_schedule(restore_drill_schedule())
        record = store.create_run_record(
            RestoreDrillRunRecord(
                tenant_id=schedule.tenant_id,
                workspace_id=schedule.workspace_id,
                schedule_id=schedule.id,
                scheduled_for=scheduled_for,
                requested_by_user_id="svc_restore_drill",
                runbook_ref=schedule.runbook_ref,
            )
        )

        found = store.get_run_record_by_schedule_time(
            tenant_id=schedule.tenant_id,
            schedule_id=schedule.id,
            scheduled_for=scheduled_for,
        )
        missing = store.get_run_record_by_schedule_time(
            tenant_id=schedule.tenant_id,
            schedule_id=schedule.id,
            scheduled_for=datetime(2026, 7, 31, 2, 0, tzinfo=timezone.utc),
        )

        assert found == record
        assert missing is None


def test_restore_drill_schedule_stores_create_run_record_idempotently(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'restore-drill-run-record-idempotency.sqlite3'}"
    MigrationRunner(
        config=DatabaseConfig(url=database_url),
        migrations_path=Path("apps/api/migrations"),
    ).apply()
    sql_store = SqlRestoreDrillScheduleStore(config=DatabaseConfig(url=database_url))
    scheduled_for = datetime(2026, 7, 1, 2, 0, tzinfo=timezone.utc)

    for store in [InMemoryRestoreDrillScheduleStore(), sql_store]:
        schedule = store.create_schedule(restore_drill_schedule())
        first = store.create_run_record(
            RestoreDrillRunRecord(
                tenant_id=schedule.tenant_id,
                workspace_id=schedule.workspace_id,
                schedule_id=schedule.id,
                scheduled_for=scheduled_for,
                requested_by_user_id="svc_restore_drill",
                runbook_ref=schedule.runbook_ref,
            )
        )
        duplicate = store.create_run_record(
            RestoreDrillRunRecord(
                tenant_id=schedule.tenant_id,
                workspace_id=schedule.workspace_id,
                schedule_id=schedule.id,
                scheduled_for=scheduled_for,
                requested_by_user_id="svc_restore_drill",
                runbook_ref=schedule.runbook_ref,
            )
        )

        assert duplicate == first
        assert store.list_run_records(schedule.tenant_id, schedule.id) == [first]
