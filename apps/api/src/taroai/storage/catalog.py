from datetime import datetime

from pydantic import BaseModel, Field

from taroai.store import NotFoundError, TenantAccessError

from taroai.storage.models import StorageObject, StorageObjectCreate


class InMemoryStorageCatalog(BaseModel):
    bucket: str
    objects: list[StorageObject] = Field(default_factory=list)

    def register(self, request: StorageObjectCreate) -> StorageObject:
        storage_object = StorageObject(
            **request.model_dump(),
            bucket=self.bucket,
            key="pending",
        )
        storage_object = storage_object.model_copy(
            update={"key": self._build_key(request, storage_object.id)}
        )
        self.objects.append(storage_object)
        return storage_object

    def list_for_run(self, tenant_id: str, run_id: str) -> list[StorageObject]:
        return [
            storage_object
            for storage_object in self.objects
            if storage_object.tenant_id == tenant_id and storage_object.run_id == run_id
            and storage_object.deleted_at is None
        ]

    def list_active(
        self,
        tenant_id: str,
        workspace_id: str | None = None,
        run_id: str | None = None,
    ) -> list[StorageObject]:
        active_objects = [
            storage_object
            for storage_object in self.objects
            if storage_object.tenant_id == tenant_id
            and (workspace_id is None or storage_object.workspace_id == workspace_id)
            and (run_id is None or storage_object.run_id == run_id)
            and storage_object.deleted_at is None
        ]
        return sorted(
            active_objects,
            key=lambda storage_object: (
                storage_object.created_at,
                storage_object.id,
            ),
        )

    def list_expired_for_retention(
        self,
        tenant_id: str,
        now: datetime,
        workspace_id: str | None = None,
    ) -> list[StorageObject]:
        expired_objects = [
            storage_object
            for storage_object in self.objects
            if storage_object.tenant_id == tenant_id
            and (workspace_id is None or storage_object.workspace_id == workspace_id)
            and storage_object.deleted_at is None
            and storage_object.retention_expires_at is not None
            and storage_object.retention_expires_at <= now
        ]
        return sorted(
            expired_objects,
            key=lambda storage_object: (
                storage_object.retention_expires_at,
                storage_object.created_at,
                storage_object.id,
            ),
        )

    def get(self, tenant_id: str, storage_object_id: str) -> StorageObject:
        storage_object = self._find(storage_object_id)
        if storage_object is None or storage_object.deleted_at is not None:
            raise NotFoundError(f"Storage object not found: {storage_object_id}")
        if storage_object.tenant_id != tenant_id:
            raise TenantAccessError(
                f"Storage object {storage_object_id} is not in tenant {tenant_id}"
            )
        return storage_object

    def mark_uploaded(
        self,
        tenant_id: str,
        storage_object_id: str,
        size_bytes: int,
    ) -> StorageObject:
        for storage_object in self.objects:
            if storage_object.id != storage_object_id:
                continue
            if storage_object.deleted_at is not None:
                raise NotFoundError(f"Storage object not found: {storage_object_id}")
            if storage_object.tenant_id != tenant_id:
                raise TenantAccessError(
                    f"Storage object {storage_object_id} is not in tenant {tenant_id}"
                )
            uploaded = storage_object.model_copy(update={"size_bytes": size_bytes})
            self.objects = [
                uploaded if existing.id == storage_object_id else existing
                for existing in self.objects
            ]
            return uploaded
        raise NotFoundError(f"Storage object not found: {storage_object_id}")

    def mark_deleted(
        self,
        tenant_id: str,
        storage_object_id: str,
        deleted_at: datetime,
    ) -> StorageObject:
        for storage_object in self.objects:
            if storage_object.id != storage_object_id:
                continue
            if storage_object.deleted_at is not None:
                raise NotFoundError(f"Storage object not found: {storage_object_id}")
            if storage_object.tenant_id != tenant_id:
                raise TenantAccessError(
                    f"Storage object {storage_object_id} is not in tenant {tenant_id}"
                )
            deleted = storage_object.model_copy(update={"deleted_at": deleted_at})
            self.objects = [
                deleted if existing.id == storage_object_id else existing
                for existing in self.objects
            ]
            return deleted
        raise NotFoundError(f"Storage object not found: {storage_object_id}")

    def _find(self, storage_object_id: str) -> StorageObject | None:
        for storage_object in self.objects:
            if storage_object.id == storage_object_id:
                return storage_object
        return None

    def _build_key(self, request: StorageObjectCreate, object_id: str) -> str:
        purpose_path = request.purpose.value
        if request.workspace_id is None:
            return f"{request.tenant_id}/{purpose_path}/{object_id}/{request.filename}"
        if request.run_id is None:
            return f"{request.tenant_id}/{request.workspace_id}/{purpose_path}/{object_id}/{request.filename}"
        return (
            f"{request.tenant_id}/{request.workspace_id}/runs/"
            f"{request.run_id}/{purpose_path}/{object_id}/{request.filename}"
        )
