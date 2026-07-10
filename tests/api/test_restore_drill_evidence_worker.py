from datetime import datetime, timezone

from taroai.audit import AuditService
from taroai.deployment_evidence import RestoreDrillVerificationResult
from taroai.lifecycle import (
    InMemoryRestoreDrillScheduleStore,
    RestoreDrillRunRecord,
    RestoreDrillRunStatus,
    RestoreDrillScheduleCreate,
)
from taroai.storage import InMemoryStorageCatalog, StorageDownloadResult
from taroai.store import InMemoryControlPlaneStore
from taroai.workers import (
    InMemoryJobQueue,
    JobStatus,
    JobType,
    RestoreDrillEvidenceCollectionJob,
    RestoreDrillEvidenceWorker,
)


class RecordingObjectStorage:
    def __init__(self):
        self.contents: dict[str, bytes] = {}
        self.uploaded_ids: list[str] = []

    def upload(self, storage_object, content: bytes):
        self.contents[storage_object.id] = content
        self.uploaded_ids.append(storage_object.id)
        return {
            "storage_object_id": storage_object.id,
            "uri": storage_object.uri,
            "etag": "etag_restore_drill",
        }

    def download(self, storage_object) -> StorageDownloadResult:
        return StorageDownloadResult(
            storage_object_id=storage_object.id,
            uri=storage_object.uri,
            content=self.contents[storage_object.id],
            content_type=storage_object.content_type,
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


def failed_verification() -> RestoreDrillVerificationResult:
    return successful_verification().model_copy(
        update={
            "drill_id": "restore_drill_2026_07_failed",
            "database_restore_verified": False,
            "post_restore_validation_passed": False,
        }
    )


def test_restore_drill_evidence_worker_collects_successful_evidence():
    schedule_store, schedule, run_record = restore_schedule_store()
    queue = InMemoryJobQueue()
    storage_catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    object_storage = RecordingObjectStorage()
    audit_store = InMemoryControlPlaneStore()
    queued = queue.enqueue(
        JobType.RESTORE_DRILL_EVIDENCE,
        RestoreDrillEvidenceCollectionJob(
            tenant_id=schedule.tenant_id,
            workspace_id=schedule.workspace_id,
            schedule_id=schedule.id,
            run_record_id=run_record.id,
            requested_by_user_id="svc_restore_drill",
            verification=successful_verification(),
        ),
        now=datetime(2026, 7, 4, 13, 0, tzinfo=timezone.utc),
    )
    worker = RestoreDrillEvidenceWorker(
        schedule_store=schedule_store,
        queue=queue,
        storage_catalog=storage_catalog,
        object_storage=object_storage,
        audit_service=AuditService(store=audit_store),
        worker_id="restore_drill_evidence_1",
    )

    processed = worker.process_next(now=datetime(2026, 7, 4, 13, 0, tzinfo=timezone.utc))

    assert processed is not None
    assert processed.id == queued.id
    assert processed.status == JobStatus.SUCCEEDED
    updated = schedule_store.get_run_record(schedule.tenant_id, run_record.id)
    assert updated.status == RestoreDrillRunStatus.EVIDENCE_READY
    assert updated.evidence_object_id is not None
    evidence_object = storage_catalog.get(schedule.tenant_id, updated.evidence_object_id)
    assert evidence_object.workspace_id == schedule.workspace_id
    assert evidence_object.content_type == "application/json"
    assert object_storage.uploaded_ids == [evidence_object.id]
    stored_verification = RestoreDrillVerificationResult.model_validate_json(
        object_storage.contents[evidence_object.id]
    )
    assert stored_verification.drill_id == "restore_drill_2026_07"

    event_types = [
        event.event_type
        for event in audit_store.list_audit_events(schedule.tenant_id)
    ]
    assert event_types == [
        "worker.job.started",
        "restore_drill.evidence_collected",
        "restore_drill.run_record.updated",
        "worker.job.succeeded",
    ]


def test_restore_drill_evidence_worker_marks_failed_verification_terminal():
    schedule_store, schedule, run_record = restore_schedule_store()
    queue = InMemoryJobQueue()
    storage_catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    object_storage = RecordingObjectStorage()
    audit_store = InMemoryControlPlaneStore()
    queue.enqueue(
        JobType.RESTORE_DRILL_EVIDENCE,
        RestoreDrillEvidenceCollectionJob(
            tenant_id=schedule.tenant_id,
            workspace_id=schedule.workspace_id,
            schedule_id=schedule.id,
            run_record_id=run_record.id,
            requested_by_user_id="svc_restore_drill",
            verification=failed_verification(),
        ),
        now=datetime(2026, 7, 4, 13, 0, tzinfo=timezone.utc),
    )
    worker = RestoreDrillEvidenceWorker(
        schedule_store=schedule_store,
        queue=queue,
        storage_catalog=storage_catalog,
        object_storage=object_storage,
        audit_service=AuditService(store=audit_store),
        worker_id="restore_drill_evidence_1",
    )

    processed = worker.process_next(now=datetime(2026, 7, 4, 13, 0, tzinfo=timezone.utc))

    assert processed is not None
    assert processed.status == JobStatus.SUCCEEDED
    updated = schedule_store.get_run_record(schedule.tenant_id, run_record.id)
    assert updated.status == RestoreDrillRunStatus.FAILED
    assert updated.evidence_object_id is None
    assert object_storage.uploaded_ids == []
    failed_event = next(
        event
        for event in audit_store.list_audit_events(schedule.tenant_id)
        if event.event_type == "restore_drill.evidence_failed"
    )
    assert failed_event.metadata["run_record_id"] == run_record.id
    assert failed_event.metadata["verification_ready"] is False
