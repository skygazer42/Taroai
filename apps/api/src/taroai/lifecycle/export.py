import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from taroai.domain import new_id, utc_now
from taroai.lifecycle.models import DataCategory
from taroai.storage.models import StorageObjectCreate, StoragePurpose
from taroai.store import NotFoundError


class DataExportApiRequest(BaseModel):
    workspace_id: str | None = Field(default=None, min_length=1)
    run_id: str | None = Field(default=None, min_length=1)
    categories: list[DataCategory] = Field(
        default_factory=lambda: [DataCategory.STORAGE_OBJECT]
    )

    @model_validator(mode="after")
    def validate_categories(self):
        if not self.categories:
            raise ValueError("at least one export category is required")
        return self


class DataExportRequest(DataExportApiRequest):
    tenant_id: str = Field(min_length=1)
    requested_by_user_id: str = Field(min_length=1)


class DataExportBundleApiRequest(DataExportApiRequest):
    workspace_id: str | None = Field(default=None, min_length=1)


class DataExportBundleRequest(DataExportBundleApiRequest):
    tenant_id: str = Field(min_length=1)
    requested_by_user_id: str = Field(min_length=1)


class DataExportManifestItem(BaseModel):
    category: DataCategory
    resource_id: str
    workspace_id: str | None = None
    run_id: str | None = None
    uri: str | None = None
    content_type: str | None = None
    size_bytes: int = 0
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class DataExportManifest(BaseModel):
    id: str = Field(default_factory=lambda: new_id("data_export"))
    tenant_id: str
    requested_by_user_id: str
    workspace_id: str | None = None
    run_id: str | None = None
    categories: list[DataCategory]
    item_count: int
    total_size_bytes: int
    items: list[DataExportManifestItem] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class DataExportBundle(BaseModel):
    id: str = Field(default_factory=lambda: new_id("data_export_bundle"))
    tenant_id: str
    requested_by_user_id: str
    workspace_id: str | None = None
    run_id: str | None = None
    filename: str
    content_type: str = "application/json"
    size_bytes: int
    storage_object_id: str
    uri: str
    manifest: DataExportManifest
    created_at: datetime = Field(default_factory=utc_now)


class DataExportService(BaseModel):
    storage_catalog: Any
    object_storage: Any | None = None
    lifecycle_policy_store: Any | None = None

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def create_manifest(self, request: DataExportRequest) -> DataExportManifest:
        items: list[DataExportManifestItem] = []
        if DataCategory.STORAGE_OBJECT in request.categories:
            items.extend(self._storage_object_items(request))
        return DataExportManifest(
            tenant_id=request.tenant_id,
            requested_by_user_id=request.requested_by_user_id,
            workspace_id=request.workspace_id,
            run_id=request.run_id,
            categories=request.categories,
            item_count=len(items),
            total_size_bytes=sum(item.size_bytes for item in items),
            items=items,
        )

    def create_bundle(self, request: DataExportBundleRequest) -> DataExportBundle:
        if self.object_storage is None:
            raise ValueError("object storage adapter is required for export bundles")
        manifest = self.create_manifest(
            DataExportRequest(
                tenant_id=request.tenant_id,
                requested_by_user_id=request.requested_by_user_id,
                workspace_id=request.workspace_id,
                run_id=request.run_id,
                categories=request.categories,
            )
        )
        content = self._bundle_content(manifest)
        filename = f"{manifest.id}.json"
        storage_object = self.storage_catalog.register(
            StorageObjectCreate(
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                run_id=request.run_id,
                purpose=StoragePurpose.DATA_EXPORT,
                filename=filename,
                content_type="application/json",
                size_bytes=len(content),
            )
        )
        self.object_storage.upload(storage_object, content)
        uploaded = self.storage_catalog.mark_uploaded(
            tenant_id=request.tenant_id,
            storage_object_id=storage_object.id,
            size_bytes=len(content),
        )
        return DataExportBundle(
            tenant_id=request.tenant_id,
            requested_by_user_id=request.requested_by_user_id,
            workspace_id=request.workspace_id,
            run_id=request.run_id,
            filename=filename,
            size_bytes=len(content),
            storage_object_id=uploaded.id,
            uri=uploaded.uri,
            manifest=manifest,
        )

    def _storage_object_items(
        self,
        request: DataExportRequest,
    ) -> list[DataExportManifestItem]:
        storage_objects = self.storage_catalog.list_active(
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            run_id=request.run_id,
        )
        return [
            self._storage_object_item(storage_object)
            for storage_object in storage_objects
            if self._is_exportable(
                tenant_id=storage_object.tenant_id,
                workspace_id=storage_object.workspace_id,
                category=DataCategory.STORAGE_OBJECT,
            )
        ]

    def _storage_object_item(self, storage_object) -> DataExportManifestItem:
        metadata = {
            "filename": storage_object.filename,
            "purpose": storage_object.purpose.value,
            "acl_subject_count": len(storage_object.acl_subjects),
            "sensitivity_level": storage_object.sensitivity_level,
            "retention_expires_at": (
                storage_object.retention_expires_at.isoformat()
                if storage_object.retention_expires_at is not None
                else None
            ),
        }
        return DataExportManifestItem(
            category=DataCategory.STORAGE_OBJECT,
            resource_id=storage_object.id,
            workspace_id=storage_object.workspace_id,
            run_id=storage_object.run_id,
            uri=storage_object.uri,
            content_type=storage_object.content_type,
            size_bytes=storage_object.size_bytes,
            created_at=storage_object.created_at,
            metadata=metadata,
        )

    def _is_exportable(
        self,
        tenant_id: str,
        workspace_id: str | None,
        category: DataCategory,
    ) -> bool:
        if self.lifecycle_policy_store is None:
            return True
        try:
            policy = self.lifecycle_policy_store.resolve_policy(
                tenant_id=tenant_id,
                category=category,
                workspace_id=workspace_id,
            )
        except NotFoundError:
            return True
        return policy.exportable

    def _bundle_content(self, manifest: DataExportManifest) -> bytes:
        payload = {
            "manifest": manifest.model_dump(mode="json"),
        }
        return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
