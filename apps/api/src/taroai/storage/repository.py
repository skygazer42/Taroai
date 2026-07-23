import json
from datetime import datetime

from pydantic import BaseModel

from taroai.db.connection import connect_database
from taroai.db.models import DatabaseConfig
from taroai.store import NotFoundError
from taroai.storage.models import StorageObject, StorageObjectCreate, StoragePurpose


class SqlStorageCatalog(BaseModel):
    config: DatabaseConfig
    bucket: str

    def register(self, request: StorageObjectCreate) -> StorageObject:
        storage_object = StorageObject(
            **request.model_dump(),
            bucket=self.bucket,
            key="pending",
        )
        storage_object = storage_object.model_copy(
            update={"key": self._build_key(request, storage_object.id)}
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO storage_objects (
                    id, tenant_id, workspace_id, run_id, purpose, filename,
                    content_type, size_bytes, acl_subjects, sensitivity_level,
                    bucket, object_key,
                    retention_expires_at, deleted_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    storage_object.id,
                    storage_object.tenant_id,
                    storage_object.workspace_id,
                    storage_object.run_id,
                    storage_object.purpose.value,
                    storage_object.filename,
                    storage_object.content_type,
                    storage_object.size_bytes,
                    self._json(storage_object.acl_subjects),
                    storage_object.sensitivity_level,
                    storage_object.bucket,
                    storage_object.key,
                    self._dt_or_none(storage_object.retention_expires_at),
                    self._dt_or_none(storage_object.deleted_at),
                    self._dt(storage_object.created_at),
                ),
            )
        return storage_object

    def list_for_run(self, tenant_id: str, run_id: str) -> list[StorageObject]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM storage_objects
                WHERE tenant_id = ? AND run_id = ? AND deleted_at IS NULL
                ORDER BY created_at, id
                """,
                (tenant_id, run_id),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def list_active(
        self,
        tenant_id: str,
        workspace_id: str | None = None,
        run_id: str | None = None,
    ) -> list[StorageObject]:
        filters = ["tenant_id = ?", "deleted_at IS NULL"]
        params = [tenant_id]
        if workspace_id is not None:
            filters.append("workspace_id = ?")
            params.append(workspace_id)
        if run_id is not None:
            filters.append("run_id = ?")
            params.append(run_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM storage_objects WHERE "
                + " AND ".join(filters)
                + " ORDER BY created_at, id",
                tuple(params),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def list_expired_for_retention(
        self,
        tenant_id: str,
        now: datetime,
        workspace_id: str | None = None,
    ) -> list[StorageObject]:
        filters = [
            "tenant_id = ?",
            "retention_expires_at IS NOT NULL",
            "retention_expires_at <= ?",
            "deleted_at IS NULL",
        ]
        params = [tenant_id, self._dt(now)]
        if workspace_id is not None:
            filters.insert(1, "workspace_id = ?")
            params.insert(1, workspace_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM storage_objects WHERE "
                + " AND ".join(filters)
                + " ORDER BY retention_expires_at, created_at, id",
                tuple(params),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def get(self, tenant_id: str, storage_object_id: str) -> StorageObject:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM storage_objects WHERE tenant_id = ? AND id = ?",
                (tenant_id, storage_object_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Storage object not found: {storage_object_id}")
        if row["deleted_at"] is not None:
            raise NotFoundError(f"Storage object not found: {storage_object_id}")
        return self._from_row(row)

    def mark_uploaded(
        self,
        tenant_id: str,
        storage_object_id: str,
        size_bytes: int,
    ) -> StorageObject:
        storage_object = self.get(tenant_id, storage_object_id)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE storage_objects
                SET size_bytes = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (size_bytes, tenant_id, storage_object_id),
            )
        return storage_object.model_copy(update={"size_bytes": size_bytes})

    def update_metadata(
        self,
        tenant_id: str,
        storage_object_id: str,
        *,
        filename: str,
    ) -> StorageObject:
        storage_object = self.get(tenant_id, storage_object_id)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE storage_objects
                SET filename = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (filename, tenant_id, storage_object_id),
            )
        return storage_object.model_copy(update={"filename": filename})

    def mark_deleted(
        self,
        tenant_id: str,
        storage_object_id: str,
        deleted_at: datetime,
    ) -> StorageObject:
        storage_object = self.get(tenant_id, storage_object_id)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE storage_objects
                SET deleted_at = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (self._dt(deleted_at), tenant_id, storage_object_id),
            )
        return storage_object.model_copy(update={"deleted_at": deleted_at})

    def _connect(self):
        return connect_database(self.config)

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

    def _from_row(self, row) -> StorageObject:
        return StorageObject(
            id=row["id"],
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            run_id=row["run_id"],
            purpose=StoragePurpose(row["purpose"]),
            filename=row["filename"],
            content_type=row["content_type"],
            size_bytes=row["size_bytes"],
            acl_subjects=self._loads(row["acl_subjects"]),
            sensitivity_level=row["sensitivity_level"],
            bucket=row["bucket"],
            key=row["object_key"],
            retention_expires_at=self._parse_dt_or_none(row["retention_expires_at"]),
            deleted_at=self._parse_dt_or_none(row["deleted_at"]),
            created_at=self._parse_dt(row["created_at"]),
        )

    def _dt(self, value: datetime) -> str:
        return value.isoformat()

    def _json(self, value) -> str:
        return json.dumps(value, separators=(",", ":"))

    def _loads(self, value):
        if value is None:
            return []
        if not isinstance(value, str):
            return value
        return json.loads(value)

    def _dt_or_none(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return self._dt(value)

    def _parse_dt(self, value) -> datetime:
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(value)

    def _parse_dt_or_none(self, value: str | None) -> datetime | None:
        if value is None:
            return None
        return self._parse_dt(value)
