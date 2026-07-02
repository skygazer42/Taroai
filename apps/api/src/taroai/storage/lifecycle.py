from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from taroai.audit import AuditActor, AuditEventCreate, AuditService
from taroai.domain import utc_now
from taroai.lifecycle.models import DataCategory, LegalHoldScopeType
from taroai.storage.adapter import ObjectStorageAdapter
from taroai.storage.audit import storage_object_audit_metadata


class StorageLifecycleCleanupRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    workspace_id: str | None = None
    now: datetime = Field(default_factory=utc_now)
    dry_run: bool = False


class StorageLifecycleCleanupPreviewRequest(BaseModel):
    workspace_id: str | None = Field(default=None, min_length=1)
    now: datetime | None = None


class StorageLifecycleCleanupResult(BaseModel):
    tenant_id: str
    workspace_id: str | None = None
    deleted_count: int = 0
    storage_object_ids: list[str] = Field(default_factory=list)
    skipped_count: int = 0
    skipped_storage_object_ids: list[str] = Field(default_factory=list)
    would_delete_count: int = 0
    would_delete_storage_object_ids: list[str] = Field(default_factory=list)


class StorageLifecycleService(BaseModel):
    storage_catalog: Any
    object_storage: ObjectStorageAdapter
    audit_service: AuditService | None = None
    lifecycle_policy_store: Any | None = None

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def cleanup_expired_objects(
        self,
        request: StorageLifecycleCleanupRequest,
    ) -> StorageLifecycleCleanupResult:
        expired_objects = self.storage_catalog.list_expired_for_retention(
            tenant_id=request.tenant_id,
            now=request.now,
            workspace_id=request.workspace_id,
        )
        deleted_ids: list[str] = []
        skipped_ids: list[str] = []
        would_delete_ids: list[str] = []
        for storage_object in expired_objects:
            active_holds = self._active_legal_holds_for_storage_object(
                storage_object,
                request.now,
            )
            if active_holds:
                skipped_ids.append(storage_object.id)
                self._record_skipped_event(storage_object, active_holds)
                continue
            if request.dry_run:
                would_delete_ids.append(storage_object.id)
                continue
            self.object_storage.delete(storage_object)
            deleted = self.storage_catalog.mark_deleted(
                tenant_id=request.tenant_id,
                storage_object_id=storage_object.id,
                deleted_at=request.now,
            )
            deleted_ids.append(deleted.id)
            self._record_deleted_event(deleted)
        return StorageLifecycleCleanupResult(
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            deleted_count=len(deleted_ids),
            storage_object_ids=deleted_ids,
            skipped_count=len(skipped_ids),
            skipped_storage_object_ids=skipped_ids,
            would_delete_count=len(would_delete_ids),
            would_delete_storage_object_ids=would_delete_ids,
        )

    def _active_legal_holds_for_storage_object(self, storage_object, now: datetime):
        if self.lifecycle_policy_store is None:
            return []
        scopes = [
            (LegalHoldScopeType.STORAGE_OBJECT, storage_object.id),
            (LegalHoldScopeType.WORKSPACE, storage_object.workspace_id),
            (LegalHoldScopeType.TENANT, storage_object.tenant_id),
        ]
        if storage_object.run_id is not None:
            scopes.append((LegalHoldScopeType.RUN, storage_object.run_id))
        holds = []
        for scope_type, scope_id in scopes:
            holds.extend(
                self.lifecycle_policy_store.list_active_legal_holds(
                    tenant_id=storage_object.tenant_id,
                    category=DataCategory.STORAGE_OBJECT,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    now=now,
                )
            )
        return holds

    def _record_deleted_event(self, storage_object) -> None:
        if self.audit_service is None:
            return
        self.audit_service.record(
            AuditEventCreate(
                tenant_id=storage_object.tenant_id,
                workspace_id=storage_object.workspace_id,
                user_id=None,
                run_id=None,
                event_type="storage.deleted",
                metadata={
                    **storage_object_audit_metadata(storage_object),
                    "retention_cleanup": True,
                },
                actor=AuditActor(
                    tenant_id=storage_object.tenant_id,
                    user_id=None,
                    actor_type="system",
                ),
            )
        )

    def _record_skipped_event(self, storage_object, active_holds) -> None:
        if self.audit_service is None:
            return
        self.audit_service.record(
            AuditEventCreate(
                tenant_id=storage_object.tenant_id,
                workspace_id=storage_object.workspace_id,
                user_id=None,
                run_id=None,
                event_type="storage.retention_skipped",
                metadata={
                    **storage_object_audit_metadata(storage_object),
                    "retention_cleanup": True,
                    "legal_hold_count": len(active_holds),
                    "legal_hold_ids": [hold.id for hold in active_holds],
                },
                actor=AuditActor(
                    tenant_id=storage_object.tenant_id,
                    user_id=None,
                    actor_type="system",
                ),
            )
        )
