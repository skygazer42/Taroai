import json
from datetime import datetime

from pydantic import BaseModel

from taroai.db import DatabaseConfig
from taroai.db.connection import connect_database
from taroai.domain import utc_now
from taroai.memory.models import (
    MemoryRecord,
    MemoryScopeType,
    MemoryStatus,
    MemoryWriteRequest,
    ShortTermMemoryReview,
    ShortTermMemoryReviewStatus,
)
from taroai.store import NotFoundError, TenantAccessError


class SqlLongTermMemoryService(BaseModel):
    config: DatabaseConfig

    def write(self, request: MemoryWriteRequest) -> MemoryRecord:
        record = MemoryRecord(**request.model_dump())
        with self._connect() as connection:
            self._ensure_tenant(connection, record.tenant_id)
            self._ensure_workspace(connection, record.tenant_id, record.workspace_id)
            connection.execute(
                """
                INSERT INTO memory_records (
                    id, tenant_id, workspace_id, run_id, scope_type, scope_id,
                    source_run_id, content, created_by, metadata, sensitivity_level,
                    confidence, created_at, expires_at, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.tenant_id,
                    record.workspace_id,
                    record.source_run_id,
                    record.scope_type.value,
                    record.scope_id,
                    record.source_run_id,
                    record.content,
                    record.created_by,
                    self._json(record.metadata),
                    record.sensitivity_level,
                    record.confidence,
                    self._dt(record.created_at),
                    self._dt(record.expires_at) if record.expires_at is not None else None,
                    record.status.value,
                ),
            )
        return record

    def propose_candidate(self, request: MemoryWriteRequest) -> MemoryRecord:
        return self.write(request.model_copy(update={"status": MemoryStatus.CANDIDATE}))

    def get(self, tenant_id: str, memory_id: str) -> MemoryRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory_records WHERE id = ?",
                (memory_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Memory not found: {memory_id}")
        if row["tenant_id"] != tenant_id:
            raise TenantAccessError(f"Memory {memory_id} is not in tenant {tenant_id}")
        return self._from_row(row)

    def approve(
        self,
        tenant_id: str,
        memory_id: str,
        reviewed_by_user_id: str,
    ) -> MemoryRecord:
        return self._update_status(tenant_id, memory_id, MemoryStatus.ACTIVE)

    def reject(
        self,
        tenant_id: str,
        memory_id: str,
        reviewed_by_user_id: str,
    ) -> MemoryRecord:
        return self._update_status(tenant_id, memory_id, MemoryStatus.REJECTED)

    def list_by_scope(
        self,
        tenant_id: str,
        scope_type: MemoryScopeType,
        scope_id: str,
    ) -> list[MemoryRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM memory_records
                WHERE tenant_id = ?
                    AND scope_type = ?
                    AND scope_id = ?
                    AND status = 'active'
                    AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY created_at, id
                """,
                (tenant_id, scope_type.value, scope_id, self._dt(utc_now())),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def delete_for_tenant(self, tenant_id: str) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id FROM memory_records
                WHERE tenant_id = ? AND status != ?
                ORDER BY created_at, id
                """,
                (tenant_id, MemoryStatus.EXPIRED.value),
            ).fetchall()
            memory_ids = [row["id"] for row in rows]
            connection.execute(
                """
                UPDATE memory_records
                SET content = '', metadata = ?, status = ?
                WHERE tenant_id = ? AND status != ?
                """,
                (
                    self._json({}),
                    MemoryStatus.EXPIRED.value,
                    tenant_id,
                    MemoryStatus.EXPIRED.value,
                ),
            )
        return memory_ids

    def _connect(self):
        return connect_database(self.config)

    def _ensure_tenant(self, connection, tenant_id: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO tenants (id, name, created_at) VALUES (?, ?, ?)",
            (tenant_id, tenant_id, self._dt(utc_now())),
        )

    def _ensure_workspace(self, connection, tenant_id: str, workspace_id: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO workspaces (id, tenant_id, name, created_at) VALUES (?, ?, ?, ?)",
            (workspace_id, tenant_id, workspace_id, self._dt(utc_now())),
        )

    def _update_status(
        self,
        tenant_id: str,
        memory_id: str,
        status: MemoryStatus,
    ) -> MemoryRecord:
        self.get(tenant_id, memory_id)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE memory_records
                SET status = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (status.value, tenant_id, memory_id),
            )
        return self.get(tenant_id, memory_id)

    def _from_row(self, row) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"],
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            scope_type=MemoryScopeType(row["scope_type"]),
            scope_id=row["scope_id"],
            source_run_id=row["source_run_id"],
            content=row["content"],
            created_by=row["created_by"],
            metadata=self._loads(row["metadata"]),
            sensitivity_level=row["sensitivity_level"],
            confidence=row["confidence"],
            created_at=self._parse_dt(row["created_at"]),
            expires_at=self._parse_dt(row["expires_at"]) if row["expires_at"] else None,
            status=MemoryStatus(row["status"]),
        )

    def _json(self, value: dict) -> str:
        return json.dumps(value, separators=(",", ":"))

    def _loads(self, value: str | None) -> dict:
        if value is None:
            return {}
        return json.loads(value)

    def _dt(self, value: datetime) -> str:
        return value.isoformat()

    def _parse_dt(self, value: str) -> datetime:
        return datetime.fromisoformat(value)


class SqlShortTermMemoryReviewStore(BaseModel):
    config: DatabaseConfig

    def save_review(self, review: ShortTermMemoryReview) -> ShortTermMemoryReview:
        with self._connect() as connection:
            self._ensure_tenant(connection, review.tenant_id)
            self._ensure_workspace(connection, review.tenant_id, review.workspace_id)
            connection.execute(
                """
                INSERT INTO short_term_memory_reviews (
                    id, tenant_id, workspace_id, run_id, memory_key, value,
                    ttl_seconds, created_by, created_at, expires_at, status,
                    approved_by_user_id, approved_at, rejected_by_user_id,
                    rejected_at, activated_entry_expires_at, guardrail_metadata
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    tenant_id = excluded.tenant_id,
                    workspace_id = excluded.workspace_id,
                    run_id = excluded.run_id,
                    memory_key = excluded.memory_key,
                    value = excluded.value,
                    ttl_seconds = excluded.ttl_seconds,
                    created_by = excluded.created_by,
                    created_at = excluded.created_at,
                    expires_at = excluded.expires_at,
                    status = excluded.status,
                    approved_by_user_id = excluded.approved_by_user_id,
                    approved_at = excluded.approved_at,
                    rejected_by_user_id = excluded.rejected_by_user_id,
                    rejected_at = excluded.rejected_at,
                    activated_entry_expires_at = excluded.activated_entry_expires_at,
                    guardrail_metadata = excluded.guardrail_metadata
                """,
                (
                    review.id,
                    review.tenant_id,
                    review.workspace_id,
                    review.run_id,
                    review.key,
                    self._json(review.value),
                    review.ttl_seconds,
                    review.created_by,
                    self._dt(review.created_at),
                    self._dt(review.expires_at),
                    review.status.value,
                    review.approved_by_user_id,
                    self._dt(review.approved_at) if review.approved_at is not None else None,
                    review.rejected_by_user_id,
                    self._dt(review.rejected_at) if review.rejected_at is not None else None,
                    (
                        self._dt(review.activated_entry_expires_at)
                        if review.activated_entry_expires_at is not None
                        else None
                    ),
                    self._json(review.guardrail_metadata),
                ),
            )
        return review

    def get_review(self, tenant_id: str, review_id: str) -> ShortTermMemoryReview:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM short_term_memory_reviews WHERE id = ?",
                (review_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Short-term memory review not found: {review_id}")
        if row["tenant_id"] != tenant_id:
            raise TenantAccessError(
                f"Short-term memory review {review_id} is not in tenant {tenant_id}"
            )
        return self._from_row(row)

    def list_reviews(
        self,
        tenant_id: str | None = None,
        run_id: str | None = None,
        status: ShortTermMemoryReviewStatus | None = None,
    ) -> list[ShortTermMemoryReview]:
        clauses: list[str] = []
        params: list[str] = []
        if tenant_id is not None:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM short_term_memory_reviews
                {where_sql}
                ORDER BY created_at, id
                """,
                tuple(params),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def delete_for_tenant(self, tenant_id: str) -> int:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id FROM short_term_memory_reviews WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchall()
            connection.execute(
                "DELETE FROM short_term_memory_reviews WHERE tenant_id = ?",
                (tenant_id,),
            )
        return len(rows)

    def _connect(self):
        return connect_database(self.config)

    def _ensure_tenant(self, connection, tenant_id: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO tenants (id, name, created_at) VALUES (?, ?, ?)",
            (tenant_id, tenant_id, self._dt(utc_now())),
        )

    def _ensure_workspace(self, connection, tenant_id: str, workspace_id: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO workspaces (id, tenant_id, name, created_at) VALUES (?, ?, ?, ?)",
            (workspace_id, tenant_id, workspace_id, self._dt(utc_now())),
        )

    def _from_row(self, row) -> ShortTermMemoryReview:
        return ShortTermMemoryReview(
            id=row["id"],
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            run_id=row["run_id"],
            key=row["memory_key"],
            value=self._loads(row["value"]),
            ttl_seconds=row["ttl_seconds"],
            created_by=row["created_by"],
            created_at=self._parse_dt(row["created_at"]),
            expires_at=self._parse_dt(row["expires_at"]),
            status=ShortTermMemoryReviewStatus(row["status"]),
            approved_by_user_id=row["approved_by_user_id"],
            approved_at=self._parse_optional_dt(row["approved_at"]),
            rejected_by_user_id=row["rejected_by_user_id"],
            rejected_at=self._parse_optional_dt(row["rejected_at"]),
            activated_entry_expires_at=self._parse_optional_dt(
                row["activated_entry_expires_at"]
            ),
            guardrail_metadata=self._loads(row["guardrail_metadata"]),
        )

    def _json(self, value: dict) -> str:
        return json.dumps(value, separators=(",", ":"))

    def _loads(self, value: str | None) -> dict:
        if value is None:
            return {}
        return json.loads(value)

    def _dt(self, value: datetime) -> str:
        return value.isoformat()

    def _parse_dt(self, value: str) -> datetime:
        return datetime.fromisoformat(value)

    def _parse_optional_dt(self, value: str | None) -> datetime | None:
        if value is None:
            return None
        return self._parse_dt(value)
