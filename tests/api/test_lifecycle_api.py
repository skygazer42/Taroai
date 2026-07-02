import json
from datetime import timedelta

from fastapi.testclient import TestClient

from taroai.app import create_app
from taroai.config import Settings
from taroai.domain import utc_now
from taroai.identity import (
    InMemoryIdentityService,
    PasswordHasher,
    Permission,
    Role,
    UserAccountCreate,
)
from taroai.lifecycle import InMemoryLifecyclePolicyStore
from taroai.storage import (
    InMemoryStorageCatalog,
    S3CompatibleObjectStorage,
    StorageObjectCreate,
    StoragePurpose,
)
from taroai.store import InMemoryControlPlaneStore


class RecordingObjectStorageClient:
    def __init__(self):
        self.deleted_objects: list[dict] = []
        self.put_objects: list[dict] = []

    def put_object(self, **kwargs):
        self.put_objects.append(kwargs)
        return {"ETag": '"export-etag"'}

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
