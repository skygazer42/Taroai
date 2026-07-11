import json
from datetime import timedelta

from fastapi.testclient import TestClient

from taroai.app import create_app
from taroai.config import Settings
from taroai.deployment.install_evidence import RestoreDrillVerificationResult
from taroai.domain import utc_now
from taroai.identity import (
    InMemoryIdentityService,
    PasswordHasher,
    Permission,
    Role,
    UserAccountCreate,
)
from taroai.lifecycle import InMemoryLifecyclePolicyStore
from taroai.lifecycle.restore_drill import (
    InMemoryRestoreDrillScheduleStore,
    RestoreDrillRunRecord,
    RestoreDrillRunStatus,
    RestoreDrillScheduleCreate,
)
from taroai.storage import (
    InMemoryStorageCatalog,
    S3CompatibleObjectStorage,
    StorageObjectCreate,
    StoragePurpose,
)
from taroai.store import InMemoryControlPlaneStore
from taroai.workers import (
    InMemoryJobQueue,
    JobType,
    RestoreDrillExecutionJob,
)


class RecordingObjectStorageClient:
    def __init__(self):
        self.deleted_objects: list[dict] = []
        self.put_objects: list[dict] = []
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, **kwargs):
        self.put_objects.append(kwargs)
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = kwargs["Body"]
        return {"ETag": '"export-etag"'}

    def get_object(self, **kwargs):
        key = (kwargs["Bucket"], kwargs["Key"])
        if key not in self.objects:
            raise FileNotFoundError(kwargs["Key"])
        return {"Body": self.objects[key]}

    def delete_object(self, **kwargs):
        self.deleted_objects.append(kwargs)
        return {"DeleteMarker": True}


def create_lifecycle_admin_identity():
    identity = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="lifecycle-admin@example.com",
            display_name="Lifecycle Admin",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_lifecycle_admin",
            name="Lifecycle Admin",
            permissions=[
                Permission(action="lifecycle.read", resource="tenant:tenant_acme"),
                Permission(action="lifecycle.manage", resource="tenant:tenant_acme"),
                Permission(action="audit.read", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_lifecycle_admin")
    return identity, account


def create_lifecycle_reader_identity():
    identity = InMemoryIdentityService(password_hasher=PasswordHasher(salt="test_salt"))
    account = identity.create_user(
        UserAccountCreate(
            tenant_id="tenant_acme",
            email="lifecycle-reader@example.com",
            display_name="Lifecycle Reader",
            password="correct horse battery staple",
        )
    )
    identity.create_role(
        Role(
            tenant_id="tenant_acme",
            id="role_lifecycle_reader",
            name="Lifecycle Reader",
            permissions=[
                Permission(action="lifecycle.read", resource="tenant:tenant_acme"),
            ],
        )
    )
    identity.assign_role("tenant_acme", account.id, "role_lifecycle_reader")
    return identity, account


def restore_drill_verification_json(
    **overrides,
) -> bytes:
    values = {
        "drill_id": "restore_drill_2026_07",
        "backup_manifest_generated": True,
        "restore_order_executed": True,
        "database_restore_verified": True,
        "object_storage_restore_verified": True,
        "redis_restore_or_rebuild_verified": True,
        "config_restore_verified": True,
        "post_restore_validation_passed": True,
        "rpo_minutes": 45,
        "rto_minutes": 25,
    }
    values.update(overrides)
    return RestoreDrillVerificationResult(
        **values,
    ).model_dump_json().encode("utf-8")


def restore_drill_execution_payload(**overrides) -> dict:
    values = {
        "verification_config": {
            "drill_id": "restore_drill_2026_07",
            "backup_manifest_path": "/restore/evidence/backup-manifest.json",
            "executed_restore_order": ["database", "object_storage", "redis", "config"],
            "migration_plan_path": "/restore/evidence/migration-plan.json",
            "object_storage_verification_path": (
                "/restore/evidence/object-storage-verification.json"
            ),
            "redis_queue_verification_path": "/restore/evidence/redis-verification.json",
            "config_restored": True,
            "post_restore_checks_passed": True,
            "rpo_minutes": 45,
            "rto_minutes": 25,
        },
        "retention_expires_at": (utc_now() + timedelta(days=30)).isoformat(),
    }
    values.update(overrides)
    return values


def test_lifecycle_policy_api_requires_manage_permission_and_records_audit():
    identity, admin = create_lifecycle_admin_identity()
    store = InMemoryControlPlaneStore()
    lifecycle_store = InMemoryLifecyclePolicyStore()
    client = TestClient(
        create_app(
            store=store,
            identity_service=identity,
            lifecycle_policy_store=lifecycle_store,
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id}

    response = client.put(
        "/api/lifecycle/policies/storage_object",
        json={
            "retention_days": 180,
            "deletion_behavior": "tombstone",
            "exportable": True,
            "residency_region": "us-east-1",
            "backup_class": "standard",
            "legal_hold_supported": True,
        },
        headers=headers,
    )
    fetched = client.get("/api/lifecycle/policies/storage_object", headers=headers)
    audits = client.get("/api/audit-events", headers=headers)

    assert response.status_code == 200
    assert response.json()["tenant_id"] == "tenant_acme"
    assert response.json()["category"] == "storage_object"
    assert fetched.status_code == 200
    assert fetched.json()["retention_days"] == 180
    policy_events = [
        event
        for event in audits.json()
        if event["event_type"] == "lifecycle.policy.upserted"
    ]
    assert len(policy_events) == 1
    assert policy_events[0]["metadata"]["category"] == "storage_object"
    assert policy_events[0]["metadata"]["retention_days"] == 180
    assert policy_events[0]["metadata"]["actor"]["user_id"] == admin.id


def test_lifecycle_policy_api_returns_workspace_effective_policy():
    identity, admin = create_lifecycle_admin_identity()
    lifecycle_store = InMemoryLifecyclePolicyStore()
    client = TestClient(
        create_app(
            identity_service=identity,
            lifecycle_policy_store=lifecycle_store,
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id}

    tenant_policy = client.put(
        "/api/lifecycle/policies/storage_object",
        json={
            "retention_days": 365,
            "deletion_behavior": "tombstone",
            "exportable": True,
            "residency_region": "us-east-1",
            "backup_class": "standard",
            "legal_hold_supported": True,
        },
        headers=headers,
    )
    workspace_policy = client.put(
        "/api/lifecycle/policies/storage_object",
        json={
            "workspace_id": "workspace_sales",
            "retention_days": 30,
            "deletion_behavior": "hard_delete",
            "exportable": False,
            "residency_region": "us-east-1",
            "backup_class": "standard",
            "legal_hold_supported": True,
        },
        headers=headers,
    )
    effective_workspace = client.get(
        "/api/lifecycle/policies/storage_object/effective",
        params={"workspace_id": "workspace_sales"},
        headers=headers,
    )
    effective_fallback = client.get(
        "/api/lifecycle/policies/storage_object/effective",
        params={"workspace_id": "workspace_support"},
        headers=headers,
    )

    assert tenant_policy.status_code == 200
    assert workspace_policy.status_code == 200
    assert effective_workspace.status_code == 200
    assert effective_workspace.json()["id"] == workspace_policy.json()["id"]
    assert effective_workspace.json()["retention_days"] == 30
    assert effective_workspace.json()["workspace_id"] == "workspace_sales"
    assert effective_fallback.status_code == 200
    assert effective_fallback.json()["id"] == tenant_policy.json()["id"]
    assert effective_fallback.json()["workspace_id"] is None


def test_lifecycle_policy_api_rejects_reader_without_manage_permission():
    identity, reader = create_lifecycle_reader_identity()
    client = TestClient(
        create_app(
            identity_service=identity,
            lifecycle_policy_store=InMemoryLifecyclePolicyStore(),
        )
    )

    response = client.put(
        "/api/lifecycle/policies/storage_object",
        json={
            "retention_days": 180,
            "deletion_behavior": "tombstone",
            "exportable": True,
            "residency_region": "us-east-1",
            "backup_class": "standard",
            "legal_hold_supported": True,
        },
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": reader.id},
    )

    assert response.status_code == 403


def test_lifecycle_legal_hold_api_creates_lists_and_releases_holds():
    identity, admin = create_lifecycle_admin_identity()
    store = InMemoryControlPlaneStore()
    lifecycle_store = InMemoryLifecyclePolicyStore()
    client = TestClient(
        create_app(
            store=store,
            identity_service=identity,
            lifecycle_policy_store=lifecycle_store,
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id}
    expires_at = (utc_now() + timedelta(days=30)).isoformat()

    created = client.post(
        "/api/lifecycle/legal-holds",
        json={
            "category": "storage_object",
            "scope_type": "storage_object",
            "scope_id": "storage_123",
            "reason": "customer litigation hold",
            "expires_at": expires_at,
        },
        headers=headers,
    )
    listed_before_release = client.get(
        "/api/lifecycle/legal-holds",
        params={
            "category": "storage_object",
            "scope_type": "storage_object",
            "scope_id": "storage_123",
        },
        headers=headers,
    )
    released = client.post(
        f"/api/lifecycle/legal-holds/{created.json()['id']}/release",
        headers=headers,
    )
    listed_after_release = client.get(
        "/api/lifecycle/legal-holds",
        params={
            "category": "storage_object",
            "scope_type": "storage_object",
            "scope_id": "storage_123",
        },
        headers=headers,
    )
    audits = client.get("/api/audit-events", headers=headers)

    assert created.status_code == 201
    assert created.json()["created_by_user_id"] == admin.id
    assert listed_before_release.status_code == 200
    assert [hold["id"] for hold in listed_before_release.json()] == [created.json()["id"]]
    assert released.status_code == 200
    assert released.json()["released_at"] is not None
    assert listed_after_release.json() == []
    lifecycle_events = [
        event
        for event in audits.json()
        if event["event_type"].startswith("lifecycle.legal_hold.")
    ]
    assert [event["event_type"] for event in lifecycle_events] == [
        "lifecycle.legal_hold.created",
        "lifecycle.legal_hold.released",
    ]
    assert "reason" not in lifecycle_events[0]["metadata"]
    assert lifecycle_events[0]["metadata"]["reason_length"] == len("customer litigation hold")


def test_lifecycle_storage_cleanup_preview_reports_candidates_without_deleting():
    identity, admin = create_lifecycle_admin_identity()
    store = InMemoryControlPlaneStore()
    catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    storage_client = RecordingObjectStorageClient()
    now = utc_now()
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
    client = TestClient(
        create_app(
            store=store,
            identity_service=identity,
            storage_catalog=catalog,
            object_storage=S3CompatibleObjectStorage(
                endpoint_url="http://object-storage.local",
                region="us-east-1",
                client=storage_client,
            ),
            lifecycle_policy_store=InMemoryLifecyclePolicyStore(),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id}

    response = client.post(
        "/api/lifecycle/storage-cleanup/preview",
        json={
            "workspace_id": "workspace_sales",
            "now": now.isoformat(),
        },
        headers=headers,
    )
    audits = client.get("/api/audit-events", headers=headers)

    assert response.status_code == 200
    assert response.json()["deleted_count"] == 0
    assert response.json()["storage_object_ids"] == []
    assert response.json()["would_delete_count"] == 1
    assert response.json()["would_delete_storage_object_ids"] == [expired.id]
    assert storage_client.deleted_objects == []
    assert catalog.get("tenant_acme", expired.id) == expired
    preview_events = [
        event
        for event in audits.json()
        if event["event_type"] == "lifecycle.cleanup.previewed"
    ]
    assert len(preview_events) == 1
    assert preview_events[0]["workspace_id"] == "workspace_sales"
    assert preview_events[0]["metadata"]["would_delete_count"] == 1


def test_lifecycle_data_export_api_returns_manifest_and_records_summary_audit():
    identity, admin = create_lifecycle_admin_identity()
    store = InMemoryControlPlaneStore()
    catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    exported = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            purpose=StoragePurpose.ARTIFACT,
            filename="agent-result.md",
            content_type="text/markdown",
            size_bytes=128,
        )
    )
    client = TestClient(
        create_app(
            store=store,
            identity_service=identity,
            storage_catalog=catalog,
            lifecycle_policy_store=InMemoryLifecyclePolicyStore(),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id}

    response = client.post(
        "/api/lifecycle/exports",
        json={
            "workspace_id": "workspace_sales",
            "categories": ["storage_object"],
        },
        headers=headers,
    )
    audits = client.get("/api/audit-events", headers=headers)

    assert response.status_code == 201
    assert response.json()["item_count"] == 1
    assert response.json()["total_size_bytes"] == 128
    assert response.json()["items"][0]["resource_id"] == exported.id
    export_events = [
        event
        for event in audits.json()
        if event["event_type"] == "lifecycle.export.manifest_created"
    ]
    assert len(export_events) == 1
    assert export_events[0]["workspace_id"] == "workspace_sales"
    assert export_events[0]["metadata"]["item_count"] == 1
    assert export_events[0]["metadata"]["total_size_bytes"] == 128
    assert "items" not in export_events[0]["metadata"]


def test_lifecycle_data_export_bundle_api_uploads_bundle_and_records_summary_audit():
    identity, admin = create_lifecycle_admin_identity()
    store = InMemoryControlPlaneStore()
    catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    storage_client = RecordingObjectStorageClient()
    exported = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            purpose=StoragePurpose.ARTIFACT,
            filename="agent-result.md",
            content_type="text/markdown",
            size_bytes=128,
        )
    )
    client = TestClient(
        create_app(
            store=store,
            identity_service=identity,
            storage_catalog=catalog,
            object_storage=S3CompatibleObjectStorage(
                endpoint_url="http://object-storage.local",
                region="us-east-1",
                client=storage_client,
            ),
            lifecycle_policy_store=InMemoryLifecyclePolicyStore(),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id}

    response = client.post(
        "/api/lifecycle/export-bundles",
        json={
            "workspace_id": "workspace_sales",
            "categories": ["storage_object"],
        },
        headers=headers,
    )
    audits = client.get("/api/audit-events", headers=headers)

    assert response.status_code == 201
    assert response.json()["manifest"]["item_count"] == 1
    assert response.json()["storage_object_id"]
    assert len(storage_client.put_objects) == 1
    uploaded = storage_client.put_objects[0]
    content = json.loads(uploaded["Body"].decode("utf-8"))
    assert content["manifest"]["items"][0]["resource_id"] == exported.id
    bundle_events = [
        event
        for event in audits.json()
        if event["event_type"] == "lifecycle.export.bundle_created"
    ]
    assert len(bundle_events) == 1
    assert bundle_events[0]["workspace_id"] == "workspace_sales"
    assert bundle_events[0]["metadata"]["storage_object_id"] == response.json()["storage_object_id"]
    assert bundle_events[0]["metadata"]["item_count"] == 1
    assert "manifest" not in bundle_events[0]["metadata"]
    assert "items" not in bundle_events[0]["metadata"]


def test_lifecycle_backup_manifest_api_returns_safe_manifest_and_records_summary_audit():
    identity, admin = create_lifecycle_admin_identity()
    store = InMemoryControlPlaneStore()
    settings = Settings(
        environment="poc",
        database_url="postgresql://user:secret@db.internal:5432/taroai",
        object_storage_bucket="tenant-backups",
        object_storage_region="us-west-2",
        short_term_memory_backend="redis",
        job_queue_backend="redis",
        _env_file=None,
    )
    client = TestClient(
        create_app(
            store=store,
            settings=settings,
            identity_service=identity,
            lifecycle_policy_store=InMemoryLifecyclePolicyStore(),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id}

    response = client.post("/api/lifecycle/backup-manifests", headers=headers)
    audits = client.get("/api/audit-events", headers=headers)

    assert response.status_code == 201
    assert response.json()["tenant_id"] == "tenant_acme"
    assert [component["type"] for component in response.json()["components"]] == [
        "database",
        "object_storage",
        "redis",
        "config",
    ]
    assert "secret" not in response.text
    backup_events = [
        event
        for event in audits.json()
        if event["event_type"] == "lifecycle.backup_manifest.created"
    ]
    assert len(backup_events) == 1
    assert backup_events[0]["metadata"]["component_count"] == 4
    assert backup_events[0]["metadata"]["component_types"] == [
        "database",
        "object_storage",
        "redis",
        "config",
    ]
    assert "components" not in backup_events[0]["metadata"]


def test_restore_drill_schedule_api_creates_lists_and_records_safe_audit():
    identity, admin = create_lifecycle_admin_identity()
    store = InMemoryControlPlaneStore()
    restore_drill_store = InMemoryRestoreDrillScheduleStore()
    client = TestClient(
        create_app(
            store=store,
            identity_service=identity,
            restore_drill_schedule_store=restore_drill_store,
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id}

    created = client.post(
        "/api/lifecycle/restore-drill-schedules",
        json={
            "workspace_id": "workspace_ops",
            "name": "Monthly private restore drill",
            "service_account_id": "svc_restore_drill",
            "interval_days": 30,
            "max_catch_up_runs": 2,
            "runbook_ref": "docs/operations/disaster-recovery.md",
            "next_run_at": "2026-07-01T02:00:00Z",
        },
        headers=headers,
    )
    listed = client.get("/api/lifecycle/restore-drill-schedules", headers=headers)
    audits = client.get("/api/audit-events", headers=headers)

    assert created.status_code == 201
    assert created.json()["tenant_id"] == "tenant_acme"
    assert created.json()["workspace_id"] == "workspace_ops"
    assert created.json()["created_by_user_id"] == admin.id
    assert created.json()["service_account_id"] == "svc_restore_drill"
    assert listed.status_code == 200
    assert [schedule["id"] for schedule in listed.json()] == [created.json()["id"]]
    events = [
        event
        for event in audits.json()
        if event["event_type"] == "restore_drill.schedule.created"
    ]
    assert len(events) == 1
    assert events[0]["workspace_id"] == "workspace_ops"
    assert events[0]["metadata"]["schedule_id"] == created.json()["id"]
    assert events[0]["metadata"]["has_service_account"] is True
    assert "backup_manifest_path" not in events[0]["metadata"]
    assert "object_storage_verification_path" not in events[0]["metadata"]


def test_restore_drill_schedule_api_rejects_reader_without_manage_permission():
    identity, reader = create_lifecycle_reader_identity()
    client = TestClient(
        create_app(
            identity_service=identity,
            restore_drill_schedule_store=InMemoryRestoreDrillScheduleStore(),
        )
    )

    response = client.post(
        "/api/lifecycle/restore-drill-schedules",
        json={
            "workspace_id": "workspace_ops",
            "name": "Monthly private restore drill",
            "interval_days": 30,
            "max_catch_up_runs": 2,
            "runbook_ref": "docs/operations/disaster-recovery.md",
        },
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": reader.id},
    )

    assert response.status_code == 403


def test_restore_drill_schedule_api_updates_status_and_records_safe_audit():
    identity, admin = create_lifecycle_admin_identity()
    restore_drill_store = InMemoryRestoreDrillScheduleStore()
    client = TestClient(
        create_app(
            identity_service=identity,
            restore_drill_schedule_store=restore_drill_store,
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id}
    created = client.post(
        "/api/lifecycle/restore-drill-schedules",
        json={
            "workspace_id": "workspace_ops",
            "name": "Monthly private restore drill",
            "interval_days": 30,
            "max_catch_up_runs": 2,
            "runbook_ref": "docs/operations/disaster-recovery.md",
            "next_run_at": "2026-07-01T02:00:00Z",
        },
        headers=headers,
    )

    updated = client.patch(
        f"/api/lifecycle/restore-drill-schedules/{created.json()['id']}",
        json={"status": "disabled"},
        headers=headers,
    )
    listed = client.get("/api/lifecycle/restore-drill-schedules", headers=headers)
    audits = client.get("/api/audit-events", headers=headers)

    assert updated.status_code == 200
    assert updated.json()["id"] == created.json()["id"]
    assert updated.json()["status"] == "disabled"
    assert listed.json()[0]["status"] == "disabled"
    events = [
        event
        for event in audits.json()
        if event["event_type"] == "restore_drill.schedule.updated"
    ]
    assert len(events) == 1
    assert events[0]["metadata"]["schedule_id"] == created.json()["id"]
    assert events[0]["metadata"]["status"] == "disabled"
    assert "backup_manifest_path" not in events[0]["metadata"]
    assert "object_storage_verification_path" not in events[0]["metadata"]


def test_restore_drill_schedule_api_rejects_reader_status_update():
    identity, reader = create_lifecycle_reader_identity()
    restore_drill_store = InMemoryRestoreDrillScheduleStore()
    schedule = restore_drill_store.create_schedule(
        RestoreDrillScheduleCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            name="Monthly private restore drill",
            created_by_user_id="user_owner",
            interval_days=30,
            max_catch_up_runs=2,
            runbook_ref="docs/operations/disaster-recovery.md",
            next_run_at=utc_now(),
        )
    )
    client = TestClient(
        create_app(
            identity_service=identity,
            restore_drill_schedule_store=restore_drill_store,
        )
    )

    response = client.patch(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}",
        json={"status": "disabled"},
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": reader.id},
    )

    assert response.status_code == 403


def test_restore_drill_schedule_api_lists_run_records_for_schedule():
    identity, admin = create_lifecycle_admin_identity()
    restore_drill_store = InMemoryRestoreDrillScheduleStore()
    client = TestClient(
        create_app(
            identity_service=identity,
            restore_drill_schedule_store=restore_drill_store,
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id}
    schedule = restore_drill_store.create_schedule(
        RestoreDrillScheduleCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            name="Monthly private restore drill",
            created_by_user_id=admin.id,
            interval_days=30,
            max_catch_up_runs=2,
            runbook_ref="docs/operations/disaster-recovery.md",
            next_run_at=utc_now(),
        )
    )
    record = restore_drill_store.create_run_record(
        RestoreDrillRunRecord(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            schedule_id=schedule.id,
            scheduled_for=utc_now(),
            requested_by_user_id=admin.id,
            runbook_ref="docs/operations/disaster-recovery.md",
        )
    )

    response = client.get(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs",
        headers=headers,
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [record.id]
    assert response.json()[0]["status"] == "requested"


def test_restore_drill_run_record_api_updates_evidence_status_and_records_safe_audit():
    identity, admin = create_lifecycle_admin_identity()
    restore_drill_store = InMemoryRestoreDrillScheduleStore()
    storage_catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    storage_client = RecordingObjectStorageClient()
    client = TestClient(
        create_app(
            identity_service=identity,
            restore_drill_schedule_store=restore_drill_store,
            storage_catalog=storage_catalog,
            object_storage=S3CompatibleObjectStorage(
                endpoint_url="http://object-storage.local",
                region="us-east-1",
                client=storage_client,
            ),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id}
    schedule = restore_drill_store.create_schedule(
        RestoreDrillScheduleCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            name="Monthly private restore drill",
            created_by_user_id=admin.id,
            interval_days=30,
            max_catch_up_runs=2,
            runbook_ref="docs/operations/disaster-recovery.md",
            next_run_at=utc_now(),
        )
    )
    record = restore_drill_store.create_run_record(
        RestoreDrillRunRecord(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            schedule_id=schedule.id,
            scheduled_for=utc_now(),
            requested_by_user_id=admin.id,
            runbook_ref="docs/operations/disaster-recovery.md",
        )
    )
    evidence_content = restore_drill_verification_json()
    evidence = storage_catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            purpose=StoragePurpose.DATA_EXPORT,
            filename="restore-drill-evidence.json",
            content_type="application/json",
            size_bytes=len(evidence_content),
        )
    )
    storage_client.objects[(evidence.bucket, evidence.key)] = evidence_content

    response = client.patch(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs/{record.id}",
        json={
            "status": "evidence_ready",
            "evidence_object_id": evidence.id,
        },
        headers=headers,
    )
    listed = client.get(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs",
        headers=headers,
    )
    audits = client.get("/api/audit-events", headers=headers)

    assert response.status_code == 200
    assert response.json()["id"] == record.id
    assert response.json()["status"] == "evidence_ready"
    assert response.json()["evidence_object_id"] == evidence.id
    assert listed.json()[0]["status"] == "evidence_ready"
    events = [
        event
        for event in audits.json()
        if event["event_type"] == "restore_drill.run_record.updated"
    ]
    assert len(events) == 1
    assert events[0]["metadata"]["schedule_id"] == schedule.id
    assert events[0]["metadata"]["run_record_id"] == record.id
    assert events[0]["metadata"]["status"] == "evidence_ready"
    assert events[0]["metadata"]["has_evidence_object"] is True
    assert "backup_manifest_path" not in events[0]["metadata"]
    assert "object_storage_verification_path" not in events[0]["metadata"]


def test_restore_drill_run_record_api_uploads_verification_evidence_and_marks_ready():
    identity, admin = create_lifecycle_admin_identity()
    restore_drill_store = InMemoryRestoreDrillScheduleStore()
    storage_catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    storage_client = RecordingObjectStorageClient()
    client = TestClient(
        create_app(
            identity_service=identity,
            restore_drill_schedule_store=restore_drill_store,
            storage_catalog=storage_catalog,
            object_storage=S3CompatibleObjectStorage(
                endpoint_url="http://object-storage.local",
                region="us-east-1",
                client=storage_client,
            ),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id}
    schedule = restore_drill_store.create_schedule(
        RestoreDrillScheduleCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            name="Monthly private restore drill",
            created_by_user_id=admin.id,
            interval_days=30,
            max_catch_up_runs=2,
            runbook_ref="docs/operations/disaster-recovery.md",
            next_run_at=utc_now(),
        )
    )
    record = restore_drill_store.create_run_record(
        RestoreDrillRunRecord(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            schedule_id=schedule.id,
            scheduled_for=utc_now(),
            requested_by_user_id=admin.id,
            runbook_ref="docs/operations/disaster-recovery.md",
        )
    )
    verification_payload = json.loads(restore_drill_verification_json())

    response = client.post(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs/{record.id}/evidence",
        json={
            "verification": verification_payload,
            "retention_expires_at": (utc_now() + timedelta(days=30)).isoformat(),
        },
        headers=headers,
    )
    listed = client.get(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs",
        headers=headers,
    )
    audits = client.get("/api/audit-events", headers=headers)

    assert response.status_code == 201
    assert response.json()["id"] == record.id
    assert response.json()["status"] == "evidence_ready"
    evidence_object_id = response.json()["evidence_object_id"]
    assert evidence_object_id
    evidence_object = storage_catalog.get("tenant_acme", evidence_object_id)
    assert evidence_object.workspace_id == "workspace_ops"
    assert evidence_object.purpose == StoragePurpose.DATA_EXPORT
    assert evidence_object.filename == f"restore-drill-{record.id}-evidence.json"
    assert evidence_object.content_type == "application/json"
    assert evidence_object.size_bytes == len(storage_client.put_objects[0]["Body"])
    assert json.loads(storage_client.put_objects[0]["Body"]) == verification_payload
    assert listed.json()[0]["status"] == "evidence_ready"
    assert listed.json()[0]["evidence_object_id"] == evidence_object_id
    updated_events = [
        event
        for event in audits.json()
        if event["event_type"] == "restore_drill.run_record.updated"
    ]
    assert len(updated_events) == 1
    assert updated_events[0]["metadata"]["has_evidence_object"] is True
    assert "backup_manifest_path" not in updated_events[0]["metadata"]
    assert "object_storage_verification_path" not in updated_events[0]["metadata"]


def test_restore_drill_run_record_api_enqueues_execution_job_and_records_safe_audit():
    identity, admin = create_lifecycle_admin_identity()
    restore_drill_store = InMemoryRestoreDrillScheduleStore()
    queue = InMemoryJobQueue()
    client = TestClient(
        create_app(
            identity_service=identity,
            restore_drill_schedule_store=restore_drill_store,
            job_queue=queue,
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id}
    schedule = restore_drill_store.create_schedule(
        RestoreDrillScheduleCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            name="Monthly private restore drill",
            created_by_user_id=admin.id,
            interval_days=30,
            max_catch_up_runs=2,
            runbook_ref="docs/operations/disaster-recovery.md",
            next_run_at=utc_now(),
        )
    )
    record = restore_drill_store.create_run_record(
        RestoreDrillRunRecord(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            schedule_id=schedule.id,
            scheduled_for=utc_now(),
            requested_by_user_id=admin.id,
            runbook_ref="docs/operations/disaster-recovery.md",
        )
    )

    response = client.post(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs/{record.id}/execute",
        json=restore_drill_execution_payload(),
        headers=headers,
    )
    audits = client.get("/api/audit-events", headers=headers)

    assert response.status_code == 202
    assert response.json()["run_record_id"] == record.id
    assert response.json()["status"] == "queued"
    assert response.json()["queue"] == "restore_drill.execute"
    assert len(queue.jobs) == 1
    queued_job = queue.get(response.json()["job_id"])
    assert queued_job.type == JobType.RESTORE_DRILL_EXECUTION
    payload = RestoreDrillExecutionJob.model_validate(queued_job.payload)
    assert payload.tenant_id == "tenant_acme"
    assert payload.workspace_id == "workspace_ops"
    assert payload.schedule_id == schedule.id
    assert payload.run_record_id == record.id
    assert payload.requested_by_user_id == admin.id
    assert payload.verification_config.drill_id == "restore_drill_2026_07"
    assert payload.verification_config.backup_manifest_path.as_posix() == (
        "/restore/evidence/backup-manifest.json"
    )
    assert restore_drill_store.get_run_record("tenant_acme", record.id).status == "requested"

    queued_events = [
        event
        for event in audits.json()
        if event["event_type"] == "restore_drill.execution_queued"
    ]
    assert len(queued_events) == 1
    assert queued_events[0]["metadata"]["job_id"] == queued_job.id
    assert queued_events[0]["metadata"]["queue"] == "restore_drill.execute"
    assert queued_events[0]["metadata"]["run_record_id"] == record.id
    assert queued_events[0]["metadata"]["drill_id"] == "restore_drill_2026_07"
    assert queued_events[0]["metadata"]["has_redis_queue_verification"] is True
    assert "backup_manifest_path" not in queued_events[0]["metadata"]
    assert "migration_plan_path" not in queued_events[0]["metadata"]
    assert "object_storage_verification_path" not in queued_events[0]["metadata"]
    assert "redis_queue_verification_path" not in queued_events[0]["metadata"]
    assert "/restore/evidence" not in str(queued_events[0]["metadata"])


def test_restore_drill_run_record_execute_replays_idempotency_key_without_duplicate_job():
    identity, admin = create_lifecycle_admin_identity()
    restore_drill_store = InMemoryRestoreDrillScheduleStore()
    queue = InMemoryJobQueue()
    client = TestClient(
        create_app(
            identity_service=identity,
            restore_drill_schedule_store=restore_drill_store,
            job_queue=queue,
        )
    )
    headers = {
        "X-Tenant-ID": "tenant_acme",
        "X-User-ID": admin.id,
        "Idempotency-Key": "restore-drill-execute-001",
    }
    schedule = restore_drill_store.create_schedule(
        RestoreDrillScheduleCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            name="Monthly private restore drill",
            created_by_user_id=admin.id,
            interval_days=30,
            max_catch_up_runs=2,
            runbook_ref="docs/operations/disaster-recovery.md",
            next_run_at=utc_now(),
        )
    )
    record = restore_drill_store.create_run_record(
        RestoreDrillRunRecord(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            schedule_id=schedule.id,
            scheduled_for=utc_now(),
            requested_by_user_id=admin.id,
            runbook_ref="docs/operations/disaster-recovery.md",
        )
    )
    payload = restore_drill_execution_payload()

    first = client.post(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs/{record.id}/execute",
        json=payload,
        headers=headers,
    )
    second = client.post(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs/{record.id}/execute",
        json=payload,
        headers=headers,
    )
    audits = client.get(
        "/api/audit-events",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id},
    )
    queued_events = [
        event
        for event in audits.json()
        if event["event_type"] == "restore_drill.execution_queued"
    ]

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json() == first.json()
    assert len(queue.jobs) == 1
    assert len(queued_events) == 1


def test_restore_drill_run_record_execute_rejects_idempotency_key_reused_with_changed_body():
    identity, admin = create_lifecycle_admin_identity()
    restore_drill_store = InMemoryRestoreDrillScheduleStore()
    queue = InMemoryJobQueue()
    client = TestClient(
        create_app(
            identity_service=identity,
            restore_drill_schedule_store=restore_drill_store,
            job_queue=queue,
        )
    )
    headers = {
        "X-Tenant-ID": "tenant_acme",
        "X-User-ID": admin.id,
        "Idempotency-Key": "restore-drill-execute-002",
    }
    schedule = restore_drill_store.create_schedule(
        RestoreDrillScheduleCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            name="Monthly private restore drill",
            created_by_user_id=admin.id,
            interval_days=30,
            max_catch_up_runs=2,
            runbook_ref="docs/operations/disaster-recovery.md",
            next_run_at=utc_now(),
        )
    )
    record = restore_drill_store.create_run_record(
        RestoreDrillRunRecord(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            schedule_id=schedule.id,
            scheduled_for=utc_now(),
            requested_by_user_id=admin.id,
            runbook_ref="docs/operations/disaster-recovery.md",
        )
    )

    first = client.post(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs/{record.id}/execute",
        json=restore_drill_execution_payload(),
        headers=headers,
    )
    second = client.post(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs/{record.id}/execute",
        json=restore_drill_execution_payload(retention_expires_at=None),
        headers=headers,
    )

    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["code"] == "idempotency_key_conflict"
    assert len(queue.jobs) == 1


def test_restore_drill_run_record_api_rejects_execution_enqueue_without_manage_permission():
    identity, reader = create_lifecycle_reader_identity()
    restore_drill_store = InMemoryRestoreDrillScheduleStore()
    queue = InMemoryJobQueue()
    client = TestClient(
        create_app(
            identity_service=identity,
            restore_drill_schedule_store=restore_drill_store,
            job_queue=queue,
        )
    )
    schedule = restore_drill_store.create_schedule(
        RestoreDrillScheduleCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            name="Monthly private restore drill",
            created_by_user_id=reader.id,
            interval_days=30,
            max_catch_up_runs=2,
            runbook_ref="docs/operations/disaster-recovery.md",
            next_run_at=utc_now(),
        )
    )
    record = restore_drill_store.create_run_record(
        RestoreDrillRunRecord(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            schedule_id=schedule.id,
            scheduled_for=utc_now(),
            requested_by_user_id=reader.id,
            runbook_ref="docs/operations/disaster-recovery.md",
        )
    )

    response = client.post(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs/{record.id}/execute",
        json=restore_drill_execution_payload(),
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": reader.id},
    )

    assert response.status_code == 403
    assert queue.jobs == []


def test_restore_drill_run_record_api_rejects_execution_enqueue_for_terminal_record():
    identity, admin = create_lifecycle_admin_identity()
    restore_drill_store = InMemoryRestoreDrillScheduleStore()
    queue = InMemoryJobQueue()
    client = TestClient(
        create_app(
            identity_service=identity,
            restore_drill_schedule_store=restore_drill_store,
            job_queue=queue,
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id}
    schedule = restore_drill_store.create_schedule(
        RestoreDrillScheduleCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            name="Monthly private restore drill",
            created_by_user_id=admin.id,
            interval_days=30,
            max_catch_up_runs=2,
            runbook_ref="docs/operations/disaster-recovery.md",
            next_run_at=utc_now(),
        )
    )
    record = restore_drill_store.create_run_record(
        RestoreDrillRunRecord(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            schedule_id=schedule.id,
            scheduled_for=utc_now(),
            requested_by_user_id=admin.id,
            runbook_ref="docs/operations/disaster-recovery.md",
        )
    )
    restore_drill_store.update_run_record_status(
        tenant_id="tenant_acme",
        run_record_id=record.id,
        status=RestoreDrillRunStatus.FAILED,
    )

    response = client.post(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs/{record.id}/execute",
        json=restore_drill_execution_payload(),
        headers=headers,
    )

    assert response.status_code == 409
    assert queue.jobs == []


def test_restore_drill_run_record_api_rejects_failed_uploaded_verification_evidence_without_upload():
    identity, admin = create_lifecycle_admin_identity()
    restore_drill_store = InMemoryRestoreDrillScheduleStore()
    storage_catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    storage_client = RecordingObjectStorageClient()
    client = TestClient(
        create_app(
            identity_service=identity,
            restore_drill_schedule_store=restore_drill_store,
            storage_catalog=storage_catalog,
            object_storage=S3CompatibleObjectStorage(
                endpoint_url="http://object-storage.local",
                region="us-east-1",
                client=storage_client,
            ),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id}
    schedule = restore_drill_store.create_schedule(
        RestoreDrillScheduleCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            name="Monthly private restore drill",
            created_by_user_id=admin.id,
            interval_days=30,
            max_catch_up_runs=2,
            runbook_ref="docs/operations/disaster-recovery.md",
            next_run_at=utc_now(),
        )
    )
    record = restore_drill_store.create_run_record(
        RestoreDrillRunRecord(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            schedule_id=schedule.id,
            scheduled_for=utc_now(),
            requested_by_user_id=admin.id,
            runbook_ref="docs/operations/disaster-recovery.md",
        )
    )
    failed_verification = json.loads(
        restore_drill_verification_json(
            object_storage_restore_verified=False,
            post_restore_validation_passed=False,
        )
    )

    response = client.post(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs/{record.id}/evidence",
        json={"verification": failed_verification},
        headers=headers,
    )

    assert response.status_code == 403
    assert response.json()["code"] == "tenant_access_denied"
    assert storage_client.put_objects == []
    assert storage_catalog.list_active("tenant_acme") == []
    assert restore_drill_store.get_run_record("tenant_acme", record.id).status == "requested"


def test_restore_drill_run_record_api_requires_evidence_for_ready_status():
    identity, admin = create_lifecycle_admin_identity()
    restore_drill_store = InMemoryRestoreDrillScheduleStore()
    client = TestClient(
        create_app(
            identity_service=identity,
            restore_drill_schedule_store=restore_drill_store,
        )
    )
    schedule = restore_drill_store.create_schedule(
        RestoreDrillScheduleCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            name="Monthly private restore drill",
            created_by_user_id=admin.id,
            interval_days=30,
            max_catch_up_runs=2,
            runbook_ref="docs/operations/disaster-recovery.md",
            next_run_at=utc_now(),
        )
    )
    record = restore_drill_store.create_run_record(
        RestoreDrillRunRecord(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            schedule_id=schedule.id,
            scheduled_for=utc_now(),
            requested_by_user_id=admin.id,
            runbook_ref="docs/operations/disaster-recovery.md",
        )
    )

    response = client.patch(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs/{record.id}",
        json={"status": "evidence_ready"},
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id},
    )

    assert response.status_code == 422


def test_restore_drill_run_record_api_rejects_requested_status_update():
    identity, admin = create_lifecycle_admin_identity()
    restore_drill_store = InMemoryRestoreDrillScheduleStore()
    client = TestClient(
        create_app(
            identity_service=identity,
            restore_drill_schedule_store=restore_drill_store,
        )
    )
    schedule = restore_drill_store.create_schedule(
        RestoreDrillScheduleCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            name="Monthly private restore drill",
            created_by_user_id=admin.id,
            interval_days=30,
            max_catch_up_runs=2,
            runbook_ref="docs/operations/disaster-recovery.md",
            next_run_at=utc_now(),
        )
    )
    record = restore_drill_store.create_run_record(
        RestoreDrillRunRecord(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            schedule_id=schedule.id,
            scheduled_for=utc_now(),
            requested_by_user_id=admin.id,
            runbook_ref="docs/operations/disaster-recovery.md",
        )
    )

    response = client.patch(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs/{record.id}",
        json={"status": "requested"},
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id},
    )

    assert response.status_code == 422


def test_restore_drill_run_record_api_rejects_terminal_record_update():
    identity, admin = create_lifecycle_admin_identity()
    restore_drill_store = InMemoryRestoreDrillScheduleStore()
    storage_catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    client = TestClient(
        create_app(
            identity_service=identity,
            restore_drill_schedule_store=restore_drill_store,
            storage_catalog=storage_catalog,
        )
    )
    schedule = restore_drill_store.create_schedule(
        RestoreDrillScheduleCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            name="Monthly private restore drill",
            created_by_user_id=admin.id,
            interval_days=30,
            max_catch_up_runs=2,
            runbook_ref="docs/operations/disaster-recovery.md",
            next_run_at=utc_now(),
        )
    )
    evidence = storage_catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            purpose=StoragePurpose.DATA_EXPORT,
            filename="restore-drill-evidence.json",
            content_type="application/json",
            size_bytes=1024,
        )
    )
    record = restore_drill_store.create_run_record(
        RestoreDrillRunRecord(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            schedule_id=schedule.id,
            scheduled_for=utc_now(),
            requested_by_user_id=admin.id,
            runbook_ref="docs/operations/disaster-recovery.md",
            status="evidence_ready",
            evidence_object_id=evidence.id,
        )
    )

    response = client.patch(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs/{record.id}",
        json={"status": "failed"},
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "conflict"


def test_restore_drill_run_record_api_rejects_reader_without_manage_permission():
    identity, reader = create_lifecycle_reader_identity()
    restore_drill_store = InMemoryRestoreDrillScheduleStore()
    client = TestClient(
        create_app(
            identity_service=identity,
            restore_drill_schedule_store=restore_drill_store,
        )
    )
    schedule = restore_drill_store.create_schedule(
        RestoreDrillScheduleCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            name="Monthly private restore drill",
            created_by_user_id="user_owner",
            interval_days=30,
            max_catch_up_runs=2,
            runbook_ref="docs/operations/disaster-recovery.md",
            next_run_at=utc_now(),
        )
    )
    record = restore_drill_store.create_run_record(
        RestoreDrillRunRecord(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            schedule_id=schedule.id,
            scheduled_for=utc_now(),
            requested_by_user_id="user_owner",
            runbook_ref="docs/operations/disaster-recovery.md",
        )
    )

    response = client.patch(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs/{record.id}",
        json={
            "status": "evidence_ready",
            "evidence_object_id": "storage_restore_drill_evidence",
        },
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": reader.id},
    )

    assert response.status_code == 403


def test_restore_drill_run_record_api_rejects_cross_workspace_evidence_object():
    identity, admin = create_lifecycle_admin_identity()
    restore_drill_store = InMemoryRestoreDrillScheduleStore()
    storage_catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    client = TestClient(
        create_app(
            identity_service=identity,
            restore_drill_schedule_store=restore_drill_store,
            storage_catalog=storage_catalog,
        )
    )
    schedule = restore_drill_store.create_schedule(
        RestoreDrillScheduleCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            name="Monthly private restore drill",
            created_by_user_id=admin.id,
            interval_days=30,
            max_catch_up_runs=2,
            runbook_ref="docs/operations/disaster-recovery.md",
            next_run_at=utc_now(),
        )
    )
    record = restore_drill_store.create_run_record(
        RestoreDrillRunRecord(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            schedule_id=schedule.id,
            scheduled_for=utc_now(),
            requested_by_user_id=admin.id,
            runbook_ref="docs/operations/disaster-recovery.md",
        )
    )
    other_workspace_evidence = storage_catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_other",
            purpose=StoragePurpose.DATA_EXPORT,
            filename="restore-drill-evidence.json",
            content_type="application/json",
            size_bytes=1024,
        )
    )

    response = client.patch(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs/{record.id}",
        json={
            "status": "evidence_ready",
            "evidence_object_id": other_workspace_evidence.id,
        },
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id},
    )

    assert response.status_code == 403


def test_restore_drill_run_record_api_rejects_non_data_export_evidence_object():
    identity, admin = create_lifecycle_admin_identity()
    restore_drill_store = InMemoryRestoreDrillScheduleStore()
    storage_catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    client = TestClient(
        create_app(
            identity_service=identity,
            restore_drill_schedule_store=restore_drill_store,
            storage_catalog=storage_catalog,
        )
    )
    schedule = restore_drill_store.create_schedule(
        RestoreDrillScheduleCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            name="Monthly private restore drill",
            created_by_user_id=admin.id,
            interval_days=30,
            max_catch_up_runs=2,
            runbook_ref="docs/operations/disaster-recovery.md",
            next_run_at=utc_now(),
        )
    )
    record = restore_drill_store.create_run_record(
        RestoreDrillRunRecord(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            schedule_id=schedule.id,
            scheduled_for=utc_now(),
            requested_by_user_id=admin.id,
            runbook_ref="docs/operations/disaster-recovery.md",
        )
    )
    upload_object = storage_catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            purpose=StoragePurpose.UPLOAD,
            filename="restore-drill-evidence.json",
            content_type="application/json",
            size_bytes=1024,
        )
    )

    response = client.patch(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs/{record.id}",
        json={
            "status": "evidence_ready",
            "evidence_object_id": upload_object.id,
        },
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id},
    )

    assert response.status_code == 403


def test_restore_drill_run_record_api_rejects_empty_evidence_object():
    identity, admin = create_lifecycle_admin_identity()
    restore_drill_store = InMemoryRestoreDrillScheduleStore()
    storage_catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    client = TestClient(
        create_app(
            identity_service=identity,
            restore_drill_schedule_store=restore_drill_store,
            storage_catalog=storage_catalog,
        )
    )
    schedule = restore_drill_store.create_schedule(
        RestoreDrillScheduleCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            name="Monthly private restore drill",
            created_by_user_id=admin.id,
            interval_days=30,
            max_catch_up_runs=2,
            runbook_ref="docs/operations/disaster-recovery.md",
            next_run_at=utc_now(),
        )
    )
    record = restore_drill_store.create_run_record(
        RestoreDrillRunRecord(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            schedule_id=schedule.id,
            scheduled_for=utc_now(),
            requested_by_user_id=admin.id,
            runbook_ref="docs/operations/disaster-recovery.md",
        )
    )
    empty_evidence = storage_catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            purpose=StoragePurpose.DATA_EXPORT,
            filename="restore-drill-evidence.json",
            content_type="application/json",
            size_bytes=0,
        )
    )

    response = client.patch(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs/{record.id}",
        json={
            "status": "evidence_ready",
            "evidence_object_id": empty_evidence.id,
        },
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id},
    )
    listed = client.get(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "tenant_access_denied"
    assert listed.json()[0]["status"] == "requested"


def test_restore_drill_run_record_api_rejects_missing_evidence_content():
    identity, admin = create_lifecycle_admin_identity()
    restore_drill_store = InMemoryRestoreDrillScheduleStore()
    storage_catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    storage_client = RecordingObjectStorageClient()
    client = TestClient(
        create_app(
            identity_service=identity,
            restore_drill_schedule_store=restore_drill_store,
            storage_catalog=storage_catalog,
            object_storage=S3CompatibleObjectStorage(
                endpoint_url="http://object-storage.local",
                region="us-east-1",
                client=storage_client,
            ),
        )
    )
    schedule = restore_drill_store.create_schedule(
        RestoreDrillScheduleCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            name="Monthly private restore drill",
            created_by_user_id=admin.id,
            interval_days=30,
            max_catch_up_runs=2,
            runbook_ref="docs/operations/disaster-recovery.md",
            next_run_at=utc_now(),
        )
    )
    record = restore_drill_store.create_run_record(
        RestoreDrillRunRecord(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            schedule_id=schedule.id,
            scheduled_for=utc_now(),
            requested_by_user_id=admin.id,
            runbook_ref="docs/operations/disaster-recovery.md",
        )
    )
    evidence = storage_catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            purpose=StoragePurpose.DATA_EXPORT,
            filename="restore-drill-evidence.json",
            content_type="application/json",
            size_bytes=128,
        )
    )

    response = client.patch(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs/{record.id}",
        json={
            "status": "evidence_ready",
            "evidence_object_id": evidence.id,
        },
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id},
    )
    listed = client.get(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "tenant_access_denied"
    assert listed.json()[0]["status"] == "requested"


def test_restore_drill_run_record_api_rejects_evidence_size_mismatch():
    identity, admin = create_lifecycle_admin_identity()
    restore_drill_store = InMemoryRestoreDrillScheduleStore()
    storage_catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    storage_client = RecordingObjectStorageClient()
    client = TestClient(
        create_app(
            identity_service=identity,
            restore_drill_schedule_store=restore_drill_store,
            storage_catalog=storage_catalog,
            object_storage=S3CompatibleObjectStorage(
                endpoint_url="http://object-storage.local",
                region="us-east-1",
                client=storage_client,
            ),
        )
    )
    schedule = restore_drill_store.create_schedule(
        RestoreDrillScheduleCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            name="Monthly private restore drill",
            created_by_user_id=admin.id,
            interval_days=30,
            max_catch_up_runs=2,
            runbook_ref="docs/operations/disaster-recovery.md",
            next_run_at=utc_now(),
        )
    )
    record = restore_drill_store.create_run_record(
        RestoreDrillRunRecord(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            schedule_id=schedule.id,
            scheduled_for=utc_now(),
            requested_by_user_id=admin.id,
            runbook_ref="docs/operations/disaster-recovery.md",
        )
    )
    evidence = storage_catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            purpose=StoragePurpose.DATA_EXPORT,
            filename="restore-drill-evidence.json",
            content_type="application/json",
            size_bytes=128,
        )
    )
    storage_client.objects[(evidence.bucket, evidence.key)] = b'{"partial":true}'

    response = client.patch(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs/{record.id}",
        json={
            "status": "evidence_ready",
            "evidence_object_id": evidence.id,
        },
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id},
    )
    listed = client.get(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "tenant_access_denied"
    assert listed.json()[0]["status"] == "requested"


def test_restore_drill_run_record_api_rejects_invalid_evidence_schema():
    identity, admin = create_lifecycle_admin_identity()
    restore_drill_store = InMemoryRestoreDrillScheduleStore()
    storage_catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    storage_client = RecordingObjectStorageClient()
    client = TestClient(
        create_app(
            identity_service=identity,
            restore_drill_schedule_store=restore_drill_store,
            storage_catalog=storage_catalog,
            object_storage=S3CompatibleObjectStorage(
                endpoint_url="http://object-storage.local",
                region="us-east-1",
                client=storage_client,
            ),
        )
    )
    schedule = restore_drill_store.create_schedule(
        RestoreDrillScheduleCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            name="Monthly private restore drill",
            created_by_user_id=admin.id,
            interval_days=30,
            max_catch_up_runs=2,
            runbook_ref="docs/operations/disaster-recovery.md",
            next_run_at=utc_now(),
        )
    )
    record = restore_drill_store.create_run_record(
        RestoreDrillRunRecord(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            schedule_id=schedule.id,
            scheduled_for=utc_now(),
            requested_by_user_id=admin.id,
            runbook_ref="docs/operations/disaster-recovery.md",
        )
    )
    evidence_content = b'{"restore":"ok"}'
    evidence = storage_catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            purpose=StoragePurpose.DATA_EXPORT,
            filename="restore-drill-evidence.json",
            content_type="application/json",
            size_bytes=len(evidence_content),
        )
    )
    storage_client.objects[(evidence.bucket, evidence.key)] = evidence_content

    response = client.patch(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs/{record.id}",
        json={
            "status": "evidence_ready",
            "evidence_object_id": evidence.id,
        },
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id},
    )
    listed = client.get(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "tenant_access_denied"
    assert listed.json()[0]["status"] == "requested"


def test_restore_drill_run_record_api_rejects_failed_verification_evidence():
    identity, admin = create_lifecycle_admin_identity()
    restore_drill_store = InMemoryRestoreDrillScheduleStore()
    storage_catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    storage_client = RecordingObjectStorageClient()
    client = TestClient(
        create_app(
            identity_service=identity,
            restore_drill_schedule_store=restore_drill_store,
            storage_catalog=storage_catalog,
            object_storage=S3CompatibleObjectStorage(
                endpoint_url="http://object-storage.local",
                region="us-east-1",
                client=storage_client,
            ),
        )
    )
    schedule = restore_drill_store.create_schedule(
        RestoreDrillScheduleCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            name="Monthly private restore drill",
            created_by_user_id=admin.id,
            interval_days=30,
            max_catch_up_runs=2,
            runbook_ref="docs/operations/disaster-recovery.md",
            next_run_at=utc_now(),
        )
    )
    record = restore_drill_store.create_run_record(
        RestoreDrillRunRecord(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            schedule_id=schedule.id,
            scheduled_for=utc_now(),
            requested_by_user_id=admin.id,
            runbook_ref="docs/operations/disaster-recovery.md",
        )
    )
    evidence_content = restore_drill_verification_json(
        database_restore_verified=False,
        post_restore_validation_passed=False,
    )
    evidence = storage_catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            purpose=StoragePurpose.DATA_EXPORT,
            filename="restore-drill-evidence.json",
            content_type="application/json",
            size_bytes=len(evidence_content),
        )
    )
    storage_client.objects[(evidence.bucket, evidence.key)] = evidence_content

    response = client.patch(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs/{record.id}",
        json={
            "status": "evidence_ready",
            "evidence_object_id": evidence.id,
        },
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id},
    )
    listed = client.get(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "tenant_access_denied"
    assert listed.json()[0]["status"] == "requested"


def test_restore_drill_run_record_api_rejects_non_json_evidence_content_type():
    identity, admin = create_lifecycle_admin_identity()
    restore_drill_store = InMemoryRestoreDrillScheduleStore()
    storage_catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    storage_client = RecordingObjectStorageClient()
    client = TestClient(
        create_app(
            identity_service=identity,
            restore_drill_schedule_store=restore_drill_store,
            storage_catalog=storage_catalog,
            object_storage=S3CompatibleObjectStorage(
                endpoint_url="http://object-storage.local",
                region="us-east-1",
                client=storage_client,
            ),
        )
    )
    schedule = restore_drill_store.create_schedule(
        RestoreDrillScheduleCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            name="Monthly private restore drill",
            created_by_user_id=admin.id,
            interval_days=30,
            max_catch_up_runs=2,
            runbook_ref="docs/operations/disaster-recovery.md",
            next_run_at=utc_now(),
        )
    )
    record = restore_drill_store.create_run_record(
        RestoreDrillRunRecord(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            schedule_id=schedule.id,
            scheduled_for=utc_now(),
            requested_by_user_id=admin.id,
            runbook_ref="docs/operations/disaster-recovery.md",
        )
    )
    evidence_content = restore_drill_verification_json()
    evidence = storage_catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            purpose=StoragePurpose.DATA_EXPORT,
            filename="restore-drill-evidence.json",
            content_type="text/plain",
            size_bytes=len(evidence_content),
        )
    )
    storage_client.objects[(evidence.bucket, evidence.key)] = evidence_content

    response = client.patch(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs/{record.id}",
        json={
            "status": "evidence_ready",
            "evidence_object_id": evidence.id,
        },
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id},
    )
    listed = client.get(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "tenant_access_denied"
    assert listed.json()[0]["status"] == "requested"


def test_restore_drill_run_record_api_rejects_retention_expired_evidence_object():
    identity, admin = create_lifecycle_admin_identity()
    restore_drill_store = InMemoryRestoreDrillScheduleStore()
    storage_catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    storage_client = RecordingObjectStorageClient()
    client = TestClient(
        create_app(
            identity_service=identity,
            restore_drill_schedule_store=restore_drill_store,
            storage_catalog=storage_catalog,
            object_storage=S3CompatibleObjectStorage(
                endpoint_url="http://object-storage.local",
                region="us-east-1",
                client=storage_client,
            ),
        )
    )
    schedule = restore_drill_store.create_schedule(
        RestoreDrillScheduleCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            name="Monthly private restore drill",
            created_by_user_id=admin.id,
            interval_days=30,
            max_catch_up_runs=2,
            runbook_ref="docs/operations/disaster-recovery.md",
            next_run_at=utc_now(),
        )
    )
    record = restore_drill_store.create_run_record(
        RestoreDrillRunRecord(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            schedule_id=schedule.id,
            scheduled_for=utc_now(),
            requested_by_user_id=admin.id,
            runbook_ref="docs/operations/disaster-recovery.md",
        )
    )
    evidence_content = restore_drill_verification_json()
    evidence = storage_catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_ops",
            purpose=StoragePurpose.DATA_EXPORT,
            filename="restore-drill-evidence.json",
            content_type="application/json",
            size_bytes=len(evidence_content),
            retention_expires_at=utc_now() - timedelta(days=1),
        )
    )
    storage_client.objects[(evidence.bucket, evidence.key)] = evidence_content

    response = client.patch(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs/{record.id}",
        json={
            "status": "evidence_ready",
            "evidence_object_id": evidence.id,
        },
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id},
    )
    listed = client.get(
        f"/api/lifecycle/restore-drill-schedules/{schedule.id}/runs",
        headers={"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "tenant_access_denied"
    assert listed.json()[0]["status"] == "requested"


def test_lifecycle_data_residency_api_returns_report_and_records_summary_audit():
    identity, admin = create_lifecycle_admin_identity()
    store = InMemoryControlPlaneStore()
    settings = Settings(
        data_residency_primary_region="eu-central-1",
        data_residency_allowed_regions=["eu-central-1"],
        object_storage_region="us-east-1",
        vector_index_region="eu-central-1",
        sandbox_provider="e2b",
        sandbox_provider_region="us-west-2",
        _env_file=None,
    )
    client = TestClient(
        create_app(
            store=store,
            settings=settings,
            identity_service=identity,
            lifecycle_policy_store=InMemoryLifecyclePolicyStore(),
        )
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id}

    response = client.post("/api/lifecycle/data-residency/reports", headers=headers)
    audits = client.get("/api/audit-events", headers=headers)

    assert response.status_code == 201
    assert response.json()["tenant_id"] == "tenant_acme"
    assert response.json()["compliant"] is False
    disallowed_checks = [
        check for check in response.json()["checks"] if check["allowed"] is False
    ]
    assert [check["resource_type"] for check in disallowed_checks] == [
        "object_storage",
        "sandbox_provider",
    ]
    residency_events = [
        event
        for event in audits.json()
        if event["event_type"] == "lifecycle.data_residency.report_created"
    ]
    assert len(residency_events) == 1
    assert residency_events[0]["metadata"]["compliant"] is False
    assert residency_events[0]["metadata"]["check_count"] == 3
    assert residency_events[0]["metadata"]["disallowed_count"] == 2
    assert residency_events[0]["metadata"]["disallowed_resource_types"] == [
        "object_storage",
        "sandbox_provider",
    ]
    assert "checks" not in residency_events[0]["metadata"]


def test_lifecycle_policy_api_can_use_sql_store_from_settings(tmp_path):
    identity, admin = create_lifecycle_admin_identity()
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'lifecycle-api.sqlite3'}",
        lifecycle_policy_backend="sql",
        _env_file=None,
    )
    headers = {"X-Tenant-ID": "tenant_acme", "X-User-ID": admin.id}
    first_client = TestClient(create_app(settings=settings, identity_service=identity))

    created = first_client.put(
        "/api/lifecycle/policies/storage_object",
        json={
            "retention_days": 365,
            "deletion_behavior": "tombstone",
            "exportable": True,
            "residency_region": "us-east-1",
            "backup_class": "standard",
            "legal_hold_supported": True,
        },
        headers=headers,
    )
    second_client = TestClient(create_app(settings=settings, identity_service=identity))
    fetched = second_client.get("/api/lifecycle/policies/storage_object", headers=headers)

    assert created.status_code == 200
    assert fetched.status_code == 200
    assert fetched.json()["id"] == created.json()["id"]
    assert fetched.json()["retention_days"] == 365
