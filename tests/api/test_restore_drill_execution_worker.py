from datetime import datetime, timezone
from pathlib import Path

from taroai.audit import AuditService
from taroai.deployment import RestoreDrillVerificationConfig
from taroai.deployment_evidence import RestoreDrillVerificationResult
from taroai.lifecycle import (
    InMemoryRestoreDrillScheduleStore,
    RestoreDrillRunRecord,
    RestoreDrillRunStatus,
    RestoreDrillScheduleCreate,
)
from taroai.store import InMemoryControlPlaneStore
from taroai.workers import (
    InMemoryJobQueue,
    JobStatus,
    JobType,
    RestoreDrillEvidenceCollectionJob,
    RestoreDrillExecutionJob,
    RestoreDrillExecutionWorker,
)


def restore_schedule_store():
    store = InMemoryRestoreDrillScheduleStore()
    schedule = store.create_schedule(
        RestoreDrillScheduleCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            name="Monthly restore drill",
            service_account_id="svc_restore_drill",
            interval_days=30,
            runbook_ref="docs/operations/disaster-recovery.md",
        )
    )
    run_record = store.create_run_record(
        RestoreDrillRunRecord(
            tenant_id=schedule.tenant_id,
            workspace_id=schedule.workspace_id,
            schedule_id=schedule.id,
            scheduled_for=datetime(2026, 7, 1, 2, 0, tzinfo=timezone.utc),
            requested_by_user_id="svc_restore_drill",
            runbook_ref=schedule.runbook_ref,
        )
    )
    return store, schedule, run_record


def verification_config() -> RestoreDrillVerificationConfig:
    return RestoreDrillVerificationConfig(
        drill_id="restore_drill_2026_07",
        backup_manifest_path=Path("/restore/evidence/backup-manifest.json"),
        executed_restore_order=["postgres", "object_storage", "redis"],
        migration_plan_path=Path("/restore/evidence/migration-plan.json"),
        object_storage_verification_path=Path(
            "/restore/evidence/object-storage-verification.json"
        ),
        redis_queue_verification_path=Path("/restore/evidence/redis-verification.json"),
        config_restored=True,
        post_restore_checks_passed=True,
        rpo_minutes=5,
        rto_minutes=22,
    )


def successful_verification() -> RestoreDrillVerificationResult:
    return RestoreDrillVerificationResult(
        drill_id="restore_drill_2026_07",
        backup_manifest_generated=True,
        restore_order_executed=True,
        database_restore_verified=True,
        object_storage_restore_verified=True,
        redis_restore_or_rebuild_verified=True,
        config_restore_verified=True,
        post_restore_validation_passed=True,
        rpo_minutes=5,
        rto_minutes=22,
    )


def test_restore_drill_execution_worker_enqueues_evidence_collection_job():
    schedule_store, schedule, run_record = restore_schedule_store()
    queue = InMemoryJobQueue()
    audit_store = InMemoryControlPlaneStore()
    config = verification_config()
    verifier_inputs: list[RestoreDrillVerificationConfig] = []
    queued = queue.enqueue(
        JobType.RESTORE_DRILL_EXECUTION,
        RestoreDrillExecutionJob(
            tenant_id=schedule.tenant_id,
            workspace_id=schedule.workspace_id,
            schedule_id=schedule.id,
            run_record_id=run_record.id,
            requested_by_user_id="svc_restore_drill",
            verification_config=config,
        ),
        now=datetime(2026, 7, 4, 13, 0, tzinfo=timezone.utc),
    )

    def verifier(input_config: RestoreDrillVerificationConfig):
        verifier_inputs.append(input_config)
        return successful_verification()

    worker = RestoreDrillExecutionWorker(
        schedule_store=schedule_store,
        queue=queue,
        verifier=verifier,
        audit_service=AuditService(store=audit_store),
        worker_id="restore_drill_execute_1",
    )

    processed = worker.process_next(now=datetime(2026, 7, 4, 13, 0, tzinfo=timezone.utc))

    assert processed is not None
    assert processed.id == queued.id
    assert processed.status == JobStatus.SUCCEEDED
    assert verifier_inputs == [config]
    assert len(queue.jobs) == 2
    evidence_job = queue.jobs[1]
    assert evidence_job.type == JobType.RESTORE_DRILL_EVIDENCE
    evidence_payload = RestoreDrillEvidenceCollectionJob.model_validate(
        evidence_job.payload
    )
    assert evidence_payload.tenant_id == schedule.tenant_id
    assert evidence_payload.workspace_id == schedule.workspace_id
    assert evidence_payload.schedule_id == schedule.id
    assert evidence_payload.run_record_id == run_record.id
    assert evidence_payload.requested_by_user_id == "svc_restore_drill"
    assert evidence_payload.verification.drill_id == "restore_drill_2026_07"
    assert (
        schedule_store.get_run_record(schedule.tenant_id, run_record.id).status
        == RestoreDrillRunStatus.REQUESTED
    )

    event_types = [
        event.event_type
        for event in audit_store.list_audit_events(schedule.tenant_id)
    ]
    assert event_types == [
        "worker.job.started",
        "restore_drill.execution_completed",
        "worker.job.succeeded",
    ]
    execution_event = next(
        event
        for event in audit_store.list_audit_events(schedule.tenant_id)
        if event.event_type == "restore_drill.execution_completed"
    )
    assert execution_event.metadata["run_record_id"] == run_record.id
    assert execution_event.metadata["evidence_job_id"] == evidence_job.id
    assert execution_event.metadata["verification_ready"] is True
    assert "backup_manifest_path" not in execution_event.metadata
    assert "migration_plan_path" not in execution_event.metadata
    assert "object_storage_verification_path" not in execution_event.metadata
    assert "redis_queue_verification_path" not in execution_event.metadata


def test_restore_drill_execution_worker_rejects_verifier_exception_without_paths():
    schedule_store, schedule, run_record = restore_schedule_store()
    queue = InMemoryJobQueue()
    audit_store = InMemoryControlPlaneStore()
    queued = queue.enqueue(
        JobType.RESTORE_DRILL_EXECUTION,
        RestoreDrillExecutionJob(
            tenant_id=schedule.tenant_id,
            workspace_id=schedule.workspace_id,
            schedule_id=schedule.id,
            run_record_id=run_record.id,
            requested_by_user_id="svc_restore_drill",
            verification_config=verification_config(),
        ),
        now=datetime(2026, 7, 4, 13, 0, tzinfo=timezone.utc),
        max_attempts=1,
    )

    def verifier(input_config: RestoreDrillVerificationConfig):
        raise RuntimeError(f"cannot read {input_config.backup_manifest_path}")

    worker = RestoreDrillExecutionWorker(
        schedule_store=schedule_store,
        queue=queue,
        verifier=verifier,
        audit_service=AuditService(store=audit_store),
        worker_id="restore_drill_execute_1",
    )

    processed = worker.process_next(now=datetime(2026, 7, 4, 13, 0, tzinfo=timezone.utc))

    assert processed is not None
    assert processed.id == queued.id
    assert processed.status == JobStatus.DEAD_LETTER
    assert len(queue.jobs) == 1
    assert (
        schedule_store.get_run_record(schedule.tenant_id, run_record.id).status
        == RestoreDrillRunStatus.REQUESTED
    )
    failed_event = next(
        event
        for event in audit_store.list_audit_events(schedule.tenant_id)
        if event.event_type == "worker.job.failed"
    )
    assert failed_event.metadata["run_record_id"] == run_record.id
    assert failed_event.metadata["error_type"] == "RuntimeError"
    assert failed_event.metadata["error"] == "restore drill verifier failed"
    assert failed_event.metadata["final_job_status"] == "dead_letter"
    assert "backup_manifest_path" not in failed_event.metadata
    assert "/restore/evidence" not in str(failed_event.metadata)
