import json
from datetime import timedelta

from taroai.domain import utc_now
from taroai.lifecycle import (
    DataExportBundleRequest,
    DataCategory,
    DataExportRequest,
    DataExportService,
    DeletionBehavior,
    InMemoryLifecyclePolicyStore,
    LifecyclePolicyCreate,
)
from taroai.storage import (
    InMemoryStorageCatalog,
    S3CompatibleObjectStorage,
    StorageObjectCreate,
    StoragePurpose,
)


class RecordingUploadClient:
    def __init__(self):
        self.put_objects: list[dict] = []

    def put_object(self, **kwargs):
        self.put_objects.append(kwargs)
        return {"ETag": '"export-etag"'}


def test_data_export_manifest_lists_active_storage_objects_for_workspace_scope():
    catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    now = utc_now()
    exported = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            purpose=StoragePurpose.ARTIFACT,
            filename="agent-result.md",
            content_type="text/markdown",
            size_bytes=128,
            retention_expires_at=now + timedelta(days=30),
        )
    )
    catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_support",
            run_id="run_456",
            purpose=StoragePurpose.UPLOAD,
            filename="support.csv",
            content_type="text/csv",
            size_bytes=512,
        )
    )
    deleted = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_deleted",
            purpose=StoragePurpose.UPLOAD,
            filename="deleted.csv",
            content_type="text/csv",
            size_bytes=1024,
        )
    )
    catalog.mark_deleted("tenant_acme", deleted.id, now)
    service = DataExportService(storage_catalog=catalog)

    manifest = service.create_manifest(
        DataExportRequest(
            tenant_id="tenant_acme",
            requested_by_user_id="user_admin",
            workspace_id="workspace_sales",
            categories=[DataCategory.STORAGE_OBJECT],
        )
    )

    assert manifest.tenant_id == "tenant_acme"
    assert manifest.workspace_id == "workspace_sales"
    assert manifest.item_count == 1
    assert manifest.total_size_bytes == 128
    assert manifest.items[0].resource_id == exported.id
    assert manifest.items[0].uri == exported.uri
    assert manifest.items[0].metadata["filename"] == "agent-result.md"
    assert manifest.items[0].metadata["acl_subject_count"] == 0


def test_data_export_manifest_skips_storage_objects_when_effective_policy_is_not_exportable():
    catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    catalog.register(
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
    support_object = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_support",
            run_id="run_456",
            purpose=StoragePurpose.UPLOAD,
            filename="support.csv",
            content_type="text/csv",
            size_bytes=512,
        )
    )
    lifecycle_store = InMemoryLifecyclePolicyStore()
    lifecycle_store.upsert_policy(
        LifecyclePolicyCreate(
            tenant_id="tenant_acme",
            category=DataCategory.STORAGE_OBJECT,
            retention_days=365,
            deletion_behavior=DeletionBehavior.TOMBSTONE,
            exportable=True,
            residency_region="us-east-1",
            backup_class="standard",
            legal_hold_supported=True,
        )
    )
    lifecycle_store.upsert_policy(
        LifecyclePolicyCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            category=DataCategory.STORAGE_OBJECT,
            retention_days=365,
            deletion_behavior=DeletionBehavior.TOMBSTONE,
            exportable=False,
            residency_region="us-east-1",
            backup_class="standard",
            legal_hold_supported=True,
        )
    )
    service = DataExportService(
        storage_catalog=catalog,
        lifecycle_policy_store=lifecycle_store,
    )

    manifest = service.create_manifest(
        DataExportRequest(
            tenant_id="tenant_acme",
            requested_by_user_id="user_admin",
            categories=[DataCategory.STORAGE_OBJECT],
        )
    )

    assert manifest.item_count == 1
    assert [item.resource_id for item in manifest.items] == [support_object.id]


def test_data_export_bundle_uploads_manifest_json_to_object_storage():
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
    storage_client = RecordingUploadClient()
    service = DataExportService(
        storage_catalog=catalog,
        object_storage=S3CompatibleObjectStorage(
            endpoint_url="http://object-storage.local",
            region="us-east-1",
            client=storage_client,
        ),
    )

    bundle = service.create_bundle(
        DataExportBundleRequest(
            tenant_id="tenant_acme",
            requested_by_user_id="user_admin",
            workspace_id="workspace_sales",
            categories=[DataCategory.STORAGE_OBJECT],
        )
    )

    assert bundle.manifest.item_count == 1
    assert bundle.manifest.items[0].resource_id == exported.id
    assert bundle.content_type == "application/json"
    assert bundle.storage_object_id
    bundle_object = catalog.get("tenant_acme", bundle.storage_object_id)
    assert bundle_object.purpose == StoragePurpose.DATA_EXPORT
    assert bundle_object.content_type == "application/json"
    assert bundle_object.size_bytes == bundle.size_bytes
    assert len(storage_client.put_objects) == 1
    uploaded = storage_client.put_objects[0]
    assert uploaded["Bucket"] == "taroai-artifacts"
    assert uploaded["Key"].endswith(f"data-exports/{bundle.filename}")
    content = json.loads(uploaded["Body"].decode("utf-8"))
    assert content["manifest"]["item_count"] == 1
    assert content["manifest"]["items"][0]["resource_id"] == exported.id
