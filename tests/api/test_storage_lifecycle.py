from datetime import datetime, timedelta, timezone

import pytest

from taroai.audit import AuditService
from taroai.lifecycle import (
    DataCategory,
    InMemoryLifecyclePolicyStore,
    LegalHoldCreate,
    LegalHoldScopeType,
)
from taroai.storage import (
    InMemoryStorageCatalog,
    S3CompatibleObjectStorage,
    StorageLifecycleCleanupRequest,
    StorageLifecycleService,
    StorageObjectCreate,
    StoragePurpose,
)
from taroai.store import InMemoryControlPlaneStore, NotFoundError
from taroai.workers import CleanupJob, CleanupWorker, InMemoryJobQueue, JobStatus, JobType


class RecordingStorageClient:
    def __init__(self):
        self.deleted_objects: list[dict] = []

    def delete_object(self, **kwargs):
        self.deleted_objects.append(kwargs)
        return {"DeleteMarker": True}


def test_storage_lifecycle_cleanup_deletes_only_expired_active_tenant_objects():
    store = InMemoryControlPlaneStore()
    catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    expired = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            purpose=StoragePurpose.UPLOAD,
            filename="expired.csv",
            content_type="text/csv",
            size_bytes=2048,
            retention_expires_at=now - timedelta(seconds=1),
        )
    )
    future = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            purpose=StoragePurpose.UPLOAD,
            filename="future.csv",
            content_type="text/csv",
            size_bytes=2048,
            retention_expires_at=now + timedelta(days=1),
        )
    )
    catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_other",
            workspace_id="workspace_support",
            run_id="run_999",
            purpose=StoragePurpose.UPLOAD,
            filename="other.csv",
            content_type="text/csv",
            size_bytes=2048,
            retention_expires_at=now - timedelta(days=1),
        )
    )
    deleted = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            purpose=StoragePurpose.UPLOAD,
            filename="deleted.csv",
            content_type="text/csv",
            size_bytes=2048,
            retention_expires_at=now - timedelta(days=2),
        )
    )
    catalog.mark_deleted(
        tenant_id="tenant_acme",
        storage_object_id=deleted.id,
        deleted_at=now - timedelta(hours=1),
    )
    storage_client = RecordingStorageClient()
    service = StorageLifecycleService(
        storage_catalog=catalog,
        object_storage=S3CompatibleObjectStorage(
            endpoint_url="http://object-storage.local",
            region="us-east-1",
            client=storage_client,
        ),
        audit_service=AuditService(store=store),
    )

    result = service.cleanup_expired_objects(
        StorageLifecycleCleanupRequest(
            tenant_id="tenant_acme",
            now=now,
        )
    )

    assert result.deleted_count == 1
    assert result.storage_object_ids == [expired.id]
    assert storage_client.deleted_objects == [
        {"Bucket": expired.bucket, "Key": expired.key}
    ]
    with pytest.raises(NotFoundError):
        catalog.get("tenant_acme", expired.id)
    assert catalog.get("tenant_acme", future.id) == future
    storage_events = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "storage.deleted"
    ]
    assert len(storage_events) == 1
    assert storage_events[0].metadata["storage_object_id"] == expired.id
    assert storage_events[0].metadata["retention_cleanup"] is True
    assert storage_events[0].metadata["actor"]["actor_type"] == "system"


def test_cleanup_worker_processes_storage_retention_jobs():
    store = InMemoryControlPlaneStore()
    queue = InMemoryJobQueue()
    catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    expired = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            purpose=StoragePurpose.UPLOAD,
            filename="expired.csv",
            content_type="text/csv",
            size_bytes=2048,
            retention_expires_at=now - timedelta(days=1),
        )
    )
    storage_client = RecordingStorageClient()
    service = StorageLifecycleService(
        storage_catalog=catalog,
        object_storage=S3CompatibleObjectStorage(
            endpoint_url="http://object-storage.local",
            region="us-east-1",
            client=storage_client,
        ),
        audit_service=AuditService(store=store),
    )
    queued_job = queue.enqueue(
        JobType.CLEANUP,
        CleanupJob(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            older_than_days=30,
            resource_types=["storage_objects"],
        ),
        now=now,
    )
    worker = CleanupWorker(
        queue=queue,
        storage_lifecycle_service=service,
        worker_id="cleanup_worker_1",
        audit_service=AuditService(store=store),
    )

    processed = worker.process_next(now=now)

    assert processed is not None
    assert processed.status == JobStatus.SUCCEEDED
    assert queue.get(queued_job.id).status == JobStatus.SUCCEEDED
    assert storage_client.deleted_objects == [
        {"Bucket": expired.bucket, "Key": expired.key}
    ]
    worker_events = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type.startswith("worker.job.")
    ]
    assert [event.event_type for event in worker_events] == [
        "worker.job.started",
        "worker.job.succeeded",
    ]
    assert worker_events[-1].metadata["deleted_count"] == 1
    assert worker_events[-1].metadata["storage_object_ids"] == [expired.id]
    assert worker_events[-1].metadata["actor"]["actor_type"] == "worker"


def test_storage_lifecycle_cleanup_skips_objects_under_active_legal_hold():
    store = InMemoryControlPlaneStore()
    catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    expired = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            purpose=StoragePurpose.UPLOAD,
            filename="expired.csv",
            content_type="text/csv",
            size_bytes=2048,
            retention_expires_at=now - timedelta(days=1),
        )
    )
    lifecycle_store = InMemoryLifecyclePolicyStore()
    lifecycle_store.create_legal_hold(
        LegalHoldCreate(
            tenant_id="tenant_acme",
            category=DataCategory.STORAGE_OBJECT,
            scope_type=LegalHoldScopeType.STORAGE_OBJECT,
            scope_id=expired.id,
            reason="customer litigation hold",
            created_by_user_id="compliance_admin",
            expires_at=now + timedelta(days=30),
        )
    )
    storage_client = RecordingStorageClient()
    service = StorageLifecycleService(
        storage_catalog=catalog,
        object_storage=S3CompatibleObjectStorage(
            endpoint_url="http://object-storage.local",
            region="us-east-1",
            client=storage_client,
        ),
        audit_service=AuditService(store=store),
        lifecycle_policy_store=lifecycle_store,
    )

    result = service.cleanup_expired_objects(
        StorageLifecycleCleanupRequest(
            tenant_id="tenant_acme",
            now=now,
        )
    )

    assert result.deleted_count == 0
    assert result.skipped_count == 1
    assert result.storage_object_ids == []
    assert result.skipped_storage_object_ids == [expired.id]
    assert storage_client.deleted_objects == []
    assert catalog.get("tenant_acme", expired.id) == expired
    skipped_events = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "storage.retention_skipped"
    ]
    assert len(skipped_events) == 1
    assert skipped_events[0].metadata["storage_object_id"] == expired.id
    assert skipped_events[0].metadata["legal_hold_count"] == 1
    assert skipped_events[0].metadata["retention_cleanup"] is True


def test_storage_lifecycle_cleanup_dry_run_reports_candidates_without_deleting():
    store = InMemoryControlPlaneStore()
    catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    expired = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            purpose=StoragePurpose.UPLOAD,
            filename="expired.csv",
            content_type="text/csv",
            size_bytes=2048,
            retention_expires_at=now - timedelta(days=1),
        )
    )
    storage_client = RecordingStorageClient()
    service = StorageLifecycleService(
        storage_catalog=catalog,
        object_storage=S3CompatibleObjectStorage(
            endpoint_url="http://object-storage.local",
            region="us-east-1",
            client=storage_client,
        ),
        audit_service=AuditService(store=store),
    )

    result = service.cleanup_expired_objects(
        StorageLifecycleCleanupRequest(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            now=now,
            dry_run=True,
        )
    )

    assert result.deleted_count == 0
    assert result.storage_object_ids == []
    assert result.would_delete_count == 1
    assert result.would_delete_storage_object_ids == [expired.id]
    assert storage_client.deleted_objects == []
    assert catalog.get("tenant_acme", expired.id) == expired
    deleted_events = [
        event
        for event in store.list_audit_events("tenant_acme")
        if event.event_type == "storage.deleted"
    ]
    assert deleted_events == []
