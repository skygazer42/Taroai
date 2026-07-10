from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from taroai.db import DatabaseConfig
from taroai.db.connection import connect_database
from taroai.deployment_evidence import (
    RestoreDrillVerificationConfig,
    RestoreDrillVerificationResult,
)
from taroai.domain import new_id, utc_now
from taroai.errors import NotFoundError, TenantAccessError
from taroai.storage.adapter import ObjectStorageConfigurationError
from taroai.storage.models import StorageDownloadResult, StorageObject, StoragePurpose


class RestoreDrillScheduleStatus(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class RestoreDrillRunStatus(str, Enum):
    REQUESTED = "requested"
    EVIDENCE_READY = "evidence_ready"
    FAILED = "failed"


class RestoreDrillScheduleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    created_by_user_id: str | None = None
    service_account_id: str | None = None
    status: RestoreDrillScheduleStatus = RestoreDrillScheduleStatus.ENABLED
    interval_days: int = Field(ge=1)
    max_catch_up_runs: int = Field(default=1, ge=1)
    runbook_ref: str = Field(min_length=1)
    next_run_at: datetime | None = None

    @model_validator(mode="after")
    def validate_accountable_identity(self) -> "RestoreDrillScheduleCreate":
        if not self.created_by_user_id and not self.service_account_id:
            raise ValueError("restore drill schedule requires an accountable identity")
        return self


class RestoreDrillScheduleApiCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    service_account_id: str | None = None
    status: RestoreDrillScheduleStatus = RestoreDrillScheduleStatus.ENABLED
    interval_days: int = Field(ge=1)
    max_catch_up_runs: int = Field(default=1, ge=1)
    runbook_ref: str = Field(min_length=1)
    next_run_at: datetime | None = None


class RestoreDrillScheduleApiUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: RestoreDrillScheduleStatus


class RestoreDrillSchedule(RestoreDrillScheduleCreate):
    id: str = Field(default_factory=lambda: new_id("restore_drill_schedule"))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class RestoreDrillRunRecord(BaseModel):
    id: str = Field(default_factory=lambda: new_id("restore_drill_run"))
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    schedule_id: str = Field(min_length=1)
    scheduled_for: datetime
    requested_by_user_id: str = Field(min_length=1)
    runbook_ref: str = Field(min_length=1)
    status: RestoreDrillRunStatus = RestoreDrillRunStatus.REQUESTED
    evidence_object_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class RestoreDrillRunRecordApiUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: RestoreDrillRunStatus
    evidence_object_id: str | None = None

    @model_validator(mode="after")
    def validate_update_status(self) -> "RestoreDrillRunRecordApiUpdate":
        if self.status == RestoreDrillRunStatus.REQUESTED:
            raise ValueError(
                "restore drill run record update status must be evidence_ready or failed"
            )
        if (
            self.status == RestoreDrillRunStatus.EVIDENCE_READY
            and self.evidence_object_id is None
        ):
            raise ValueError(
                "restore drill evidence_ready status requires evidence_object_id"
            )
        return self


class RestoreDrillRunEvidenceApiCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verification: RestoreDrillVerificationResult
    retention_expires_at: datetime | None = None


class RestoreDrillRunExecutionApiCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verification_config: RestoreDrillVerificationConfig
    retention_expires_at: datetime | None = None


class RestoreDrillEvidenceValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    evidence_object_id: str | None = None
    now: datetime = Field(default_factory=utc_now)


class RestoreDrillEvidenceStorageCatalog(Protocol):
    def get(self, tenant_id: str, storage_object_id: str) -> StorageObject:
        raise NotImplementedError


class RestoreDrillEvidenceObjectStorage(Protocol):
    def download(self, storage_object: StorageObject) -> StorageDownloadResult:
        raise NotImplementedError


class RestoreDrillScheduleEvaluation(BaseModel):
    due_scheduled_for: list[datetime] = Field(default_factory=list)
    next_run_at: datetime | None = None


class RestoreDrillScheduleStore(BaseModel):
    def create_schedule(
        self,
        request: RestoreDrillScheduleCreate,
    ) -> RestoreDrillSchedule:
        raise NotImplementedError

    def list_schedules(self, tenant_id: str | None = None) -> list[RestoreDrillSchedule]:
        raise NotImplementedError

    def get_schedule(self, tenant_id: str, schedule_id: str) -> RestoreDrillSchedule:
        raise NotImplementedError

    def update_next_run_at(
        self,
        tenant_id: str,
        schedule_id: str,
        next_run_at: datetime | None,
    ) -> RestoreDrillSchedule:
        raise NotImplementedError

    def update_schedule_status(
        self,
        tenant_id: str,
        schedule_id: str,
        status: RestoreDrillScheduleStatus,
    ) -> RestoreDrillSchedule:
        raise NotImplementedError

    def create_run_record(
        self,
        record: RestoreDrillRunRecord,
    ) -> RestoreDrillRunRecord:
        raise NotImplementedError

    def list_run_records(
        self,
        tenant_id: str,
        schedule_id: str | None = None,
    ) -> list[RestoreDrillRunRecord]:
        raise NotImplementedError

    def get_run_record(
        self,
        tenant_id: str,
        run_record_id: str,
    ) -> RestoreDrillRunRecord:
        raise NotImplementedError

    def get_run_record_by_schedule_time(
        self,
        tenant_id: str,
        schedule_id: str,
        scheduled_for: datetime,
    ) -> RestoreDrillRunRecord | None:
        raise NotImplementedError

    def update_run_record_status(
        self,
        tenant_id: str,
        run_record_id: str,
        status: RestoreDrillRunStatus,
        evidence_object_id: str | None = None,
    ) -> RestoreDrillRunRecord:
        raise NotImplementedError


class InMemoryRestoreDrillScheduleStore(RestoreDrillScheduleStore):
    schedules: list[RestoreDrillSchedule] = Field(default_factory=list)
    run_records: list[RestoreDrillRunRecord] = Field(default_factory=list)

    def create_schedule(
        self,
        request: RestoreDrillScheduleCreate,
    ) -> RestoreDrillSchedule:
        schedule = RestoreDrillSchedule(**request.model_dump())
        self.schedules.append(schedule)
        return schedule

    def list_schedules(self, tenant_id: str | None = None) -> list[RestoreDrillSchedule]:
        schedules = [
            schedule
            for schedule in self.schedules
            if tenant_id is None or schedule.tenant_id == tenant_id
        ]
        return sorted(
            schedules,
            key=lambda schedule: (schedule.tenant_id, schedule.next_run_at or schedule.created_at),
        )

    def get_schedule(self, tenant_id: str, schedule_id: str) -> RestoreDrillSchedule:
        for schedule in self.schedules:
            if schedule.tenant_id == tenant_id and schedule.id == schedule_id:
                return schedule
        raise NotFoundError(f"Restore drill schedule not found: {schedule_id}")

    def create_run_record(
        self,
        record: RestoreDrillRunRecord,
    ) -> RestoreDrillRunRecord:
        existing = self.get_run_record_by_schedule_time(
            tenant_id=record.tenant_id,
            schedule_id=record.schedule_id,
            scheduled_for=record.scheduled_for,
        )
        if existing is not None:
            return existing
        self.run_records.append(record)
        return record

    def list_run_records(
        self,
        tenant_id: str,
        schedule_id: str | None = None,
    ) -> list[RestoreDrillRunRecord]:
        return [
            record
            for record in self.run_records
            if record.tenant_id == tenant_id
            and (schedule_id is None or record.schedule_id == schedule_id)
        ]

    def get_run_record(
        self,
        tenant_id: str,
        run_record_id: str,
    ) -> RestoreDrillRunRecord:
        for record in self.run_records:
            if record.tenant_id == tenant_id and record.id == run_record_id:
                return record
        raise NotFoundError(f"Restore drill run record not found: {run_record_id}")

    def get_run_record_by_schedule_time(
        self,
        tenant_id: str,
        schedule_id: str,
        scheduled_for: datetime,
    ) -> RestoreDrillRunRecord | None:
        scheduled_for_utc = ensure_aware_utc(scheduled_for)
        for record in self.run_records:
            if (
                record.tenant_id == tenant_id
                and record.schedule_id == schedule_id
                and ensure_aware_utc(record.scheduled_for) == scheduled_for_utc
            ):
                return record
        return None

    def update_run_record_status(
        self,
        tenant_id: str,
        run_record_id: str,
        status: RestoreDrillRunStatus,
        evidence_object_id: str | None = None,
    ) -> RestoreDrillRunRecord:
        for index, record in enumerate(self.run_records):
            if record.tenant_id != tenant_id or record.id != run_record_id:
                continue
            updated = record.model_copy(
                update={
                    "status": status,
                    "evidence_object_id": evidence_object_id,
                    "updated_at": utc_now(),
                }
            )
            self.run_records[index] = updated
            return updated
        raise NotFoundError(f"Restore drill run record not found: {run_record_id}")

    def update_next_run_at(
        self,
        tenant_id: str,
        schedule_id: str,
        next_run_at: datetime | None,
    ) -> RestoreDrillSchedule:
        for index, schedule in enumerate(self.schedules):
            if schedule.tenant_id != tenant_id or schedule.id != schedule_id:
                continue
            updated = schedule.model_copy(
                update={
                    "next_run_at": next_run_at,
                    "updated_at": utc_now(),
                }
            )
            self.schedules[index] = updated
            return updated
        raise NotFoundError(f"Restore drill schedule not found: {schedule_id}")

    def update_schedule_status(
        self,
        tenant_id: str,
        schedule_id: str,
        status: RestoreDrillScheduleStatus,
    ) -> RestoreDrillSchedule:
        for index, schedule in enumerate(self.schedules):
            if schedule.tenant_id != tenant_id or schedule.id != schedule_id:
                continue
            updated = schedule.model_copy(
                update={
                    "status": status,
                    "updated_at": utc_now(),
                }
            )
            self.schedules[index] = updated
            return updated
        raise NotFoundError(f"Restore drill schedule not found: {schedule_id}")


class SqlRestoreDrillScheduleStore(RestoreDrillScheduleStore):
    config: DatabaseConfig

    def create_schedule(
        self,
        request: RestoreDrillScheduleCreate,
    ) -> RestoreDrillSchedule:
        schedule = RestoreDrillSchedule(**request.model_dump())
        with self._connect() as connection:
            self._ensure_tenant(connection, schedule.tenant_id)
            connection.execute(
                """
                INSERT INTO restore_drill_schedules (
                    id, tenant_id, workspace_id, name, created_by_user_id,
                    service_account_id, status, interval_days, max_catch_up_runs,
                    runbook_ref, next_run_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    schedule.id,
                    schedule.tenant_id,
                    schedule.workspace_id,
                    schedule.name,
                    schedule.created_by_user_id,
                    schedule.service_account_id,
                    schedule.status.value,
                    schedule.interval_days,
                    schedule.max_catch_up_runs,
                    schedule.runbook_ref,
                    self._dt_or_none(schedule.next_run_at),
                    self._dt(schedule.created_at),
                    self._dt(schedule.updated_at),
                ),
            )
        return schedule

    def list_schedules(self, tenant_id: str | None = None) -> list[RestoreDrillSchedule]:
        sql = """
            SELECT * FROM restore_drill_schedules
        """
        params: tuple[str, ...] = ()
        if tenant_id is not None:
            sql += " WHERE tenant_id = ?"
            params = (tenant_id,)
        sql += " ORDER BY tenant_id, COALESCE(next_run_at, created_at), id"
        with self._connect() as connection:
            rows = connection.execute(
                sql,
                params,
            ).fetchall()
        return [self._schedule_from_row(row) for row in rows]

    def get_schedule(self, tenant_id: str, schedule_id: str) -> RestoreDrillSchedule:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM restore_drill_schedules
                WHERE tenant_id = ? AND id = ?
                """,
                (tenant_id, schedule_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Restore drill schedule not found: {schedule_id}")
        return self._schedule_from_row(row)

    def update_next_run_at(
        self,
        tenant_id: str,
        schedule_id: str,
        next_run_at: datetime | None,
    ) -> RestoreDrillSchedule:
        existing = self.get_schedule(tenant_id, schedule_id)
        updated = existing.model_copy(
            update={
                "next_run_at": next_run_at,
                "updated_at": utc_now(),
            }
        )
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE restore_drill_schedules
                SET next_run_at = ?, updated_at = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (
                    self._dt_or_none(updated.next_run_at),
                    self._dt(updated.updated_at),
                    tenant_id,
                    schedule_id,
                ),
            )
        return updated

    def update_schedule_status(
        self,
        tenant_id: str,
        schedule_id: str,
        status: RestoreDrillScheduleStatus,
    ) -> RestoreDrillSchedule:
        existing = self.get_schedule(tenant_id, schedule_id)
        updated = existing.model_copy(
            update={
                "status": status,
                "updated_at": utc_now(),
            }
        )
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE restore_drill_schedules
                SET status = ?, updated_at = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (
                    updated.status.value,
                    self._dt(updated.updated_at),
                    tenant_id,
                    schedule_id,
                ),
            )
        return updated

    def create_run_record(
        self,
        record: RestoreDrillRunRecord,
    ) -> RestoreDrillRunRecord:
        existing = self.get_run_record_by_schedule_time(
            tenant_id=record.tenant_id,
            schedule_id=record.schedule_id,
            scheduled_for=record.scheduled_for,
        )
        if existing is not None:
            return existing
        with self._connect() as connection:
            self._ensure_tenant(connection, record.tenant_id)
            connection.execute(
                """
                INSERT INTO restore_drill_runs (
                    id, tenant_id, workspace_id, schedule_id, scheduled_for,
                    requested_by_user_id, runbook_ref, status, evidence_object_id,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.tenant_id,
                    record.workspace_id,
                    record.schedule_id,
                    self._dt(record.scheduled_for),
                    record.requested_by_user_id,
                    record.runbook_ref,
                    record.status.value,
                    record.evidence_object_id,
                    self._dt(record.created_at),
                    self._dt(record.updated_at),
                ),
            )
        return record

    def list_run_records(
        self,
        tenant_id: str,
        schedule_id: str | None = None,
    ) -> list[RestoreDrillRunRecord]:
        sql = """
            SELECT * FROM restore_drill_runs
            WHERE tenant_id = ?
        """
        params: tuple[str, ...] = (tenant_id,)
        if schedule_id is not None:
            sql += " AND schedule_id = ?"
            params = (tenant_id, schedule_id)
        sql += " ORDER BY created_at, id"
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._run_record_from_row(row) for row in rows]

    def get_run_record(
        self,
        tenant_id: str,
        run_record_id: str,
    ) -> RestoreDrillRunRecord:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM restore_drill_runs
                WHERE tenant_id = ? AND id = ?
                """,
                (tenant_id, run_record_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Restore drill run record not found: {run_record_id}")
        return self._run_record_from_row(row)

    def get_run_record_by_schedule_time(
        self,
        tenant_id: str,
        schedule_id: str,
        scheduled_for: datetime,
    ) -> RestoreDrillRunRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM restore_drill_runs
                WHERE tenant_id = ? AND schedule_id = ? AND scheduled_for = ?
                ORDER BY created_at, id
                LIMIT 1
                """,
                (tenant_id, schedule_id, self._dt(ensure_aware_utc(scheduled_for))),
            ).fetchone()
        if row is None:
            return None
        return self._run_record_from_row(row)

    def update_run_record_status(
        self,
        tenant_id: str,
        run_record_id: str,
        status: RestoreDrillRunStatus,
        evidence_object_id: str | None = None,
    ) -> RestoreDrillRunRecord:
        existing = self.get_run_record(tenant_id, run_record_id)
        updated = existing.model_copy(
            update={
                "status": status,
                "evidence_object_id": evidence_object_id,
                "updated_at": utc_now(),
            }
        )
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE restore_drill_runs
                SET status = ?, evidence_object_id = ?, updated_at = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (
                    updated.status.value,
                    updated.evidence_object_id,
                    self._dt(updated.updated_at),
                    tenant_id,
                    run_record_id,
                ),
            )
        return updated

    def _connect(self):
        return connect_database(self.config)

    def _ensure_tenant(self, connection, tenant_id: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO tenants (id, name, created_at) VALUES (?, ?, ?)",
            (tenant_id, tenant_id, self._dt(utc_now())),
        )

    def _schedule_from_row(self, row) -> RestoreDrillSchedule:
        return RestoreDrillSchedule(
            id=row["id"],
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            name=row["name"],
            created_by_user_id=row["created_by_user_id"],
            service_account_id=row["service_account_id"],
            status=RestoreDrillScheduleStatus(row["status"]),
            interval_days=row["interval_days"],
            max_catch_up_runs=row["max_catch_up_runs"],
            runbook_ref=row["runbook_ref"],
            next_run_at=self._parse_dt_or_none(row["next_run_at"]),
            created_at=self._parse_dt(row["created_at"]),
            updated_at=self._parse_dt(row["updated_at"]),
        )

    def _run_record_from_row(self, row) -> RestoreDrillRunRecord:
        return RestoreDrillRunRecord(
            id=row["id"],
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            schedule_id=row["schedule_id"],
            scheduled_for=self._parse_dt(row["scheduled_for"]),
            requested_by_user_id=row["requested_by_user_id"],
            runbook_ref=row["runbook_ref"],
            status=RestoreDrillRunStatus(row["status"]),
            evidence_object_id=row["evidence_object_id"],
            created_at=self._parse_dt(row["created_at"]),
            updated_at=self._parse_dt(row["updated_at"]),
        )

    def _dt(self, value: datetime) -> str:
        return value.isoformat()

    def _dt_or_none(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return self._dt(value)

    def _parse_dt(self, value) -> datetime:
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value))

    def _parse_dt_or_none(self, value) -> datetime | None:
        if value is None:
            return None
        return self._parse_dt(value)


def evaluate_restore_drill_schedule(
    schedule: RestoreDrillSchedule,
    now: datetime,
) -> RestoreDrillScheduleEvaluation:
    if schedule.status == RestoreDrillScheduleStatus.DISABLED:
        return RestoreDrillScheduleEvaluation(next_run_at=schedule.next_run_at)

    now_utc = ensure_aware_utc(now)
    next_run_at = ensure_aware_utc(schedule.next_run_at or now_utc)
    due_scheduled_for: list[datetime] = []

    while (
        next_run_at <= now_utc
        and len(due_scheduled_for) < schedule.max_catch_up_runs
    ):
        due_scheduled_for.append(next_run_at)
        next_run_at = next_run_at + timedelta(days=schedule.interval_days)

    return RestoreDrillScheduleEvaluation(
        due_scheduled_for=due_scheduled_for,
        next_run_at=next_run_at,
    )


def validate_restore_drill_evidence_object(
    request: RestoreDrillEvidenceValidationRequest,
    storage_catalog: RestoreDrillEvidenceStorageCatalog,
    object_storage: RestoreDrillEvidenceObjectStorage,
) -> str | None:
    if request.evidence_object_id is None:
        return None
    storage_object = storage_catalog.get(
        request.tenant_id,
        request.evidence_object_id,
    )
    if storage_object.workspace_id != request.workspace_id:
        raise TenantAccessError(
            "Restore drill evidence object workspace does not match schedule"
        )
    if storage_object.purpose != StoragePurpose.DATA_EXPORT:
        raise TenantAccessError(
            "Restore drill evidence object must be a data export"
        )
    if not is_restore_drill_evidence_json_content_type(storage_object.content_type):
        raise TenantAccessError(
            "Restore drill evidence object must use application/json content type"
        )
    if storage_object.size_bytes <= 0:
        raise TenantAccessError(
            "Restore drill evidence object must contain exported evidence"
        )
    if (
        storage_object.retention_expires_at is not None
        and storage_object.retention_expires_at <= request.now
    ):
        raise TenantAccessError(
            "Restore drill evidence object retention has expired"
        )
    try:
        evidence_content = object_storage.download(storage_object).content
    except ObjectStorageConfigurationError:
        raise
    except Exception as error:
        raise TenantAccessError(
            "Restore drill evidence object content is unavailable"
        ) from error
    if len(evidence_content) == 0:
        raise TenantAccessError(
            "Restore drill evidence object must contain exported evidence"
        )
    if len(evidence_content) != storage_object.size_bytes:
        raise TenantAccessError(
            "Restore drill evidence object size does not match catalog metadata"
        )
    try:
        verification = RestoreDrillVerificationResult.model_validate_json(
            evidence_content
        )
    except Exception as error:
        raise TenantAccessError(
            "Restore drill evidence object must match restore drill verification schema"
        ) from error
    if not restore_drill_verification_result_ready(verification):
        raise TenantAccessError(
            "Restore drill evidence object does not confirm a successful restore drill"
        )
    return storage_object.id


def restore_drill_evidence_content(
    verification: RestoreDrillVerificationResult,
) -> bytes:
    return verification.model_dump_json().encode("utf-8")


def restore_drill_evidence_filename(run_record_id: str) -> str:
    return f"restore-drill-{run_record_id}-evidence.json"


def require_restore_drill_verification_result_ready(
    result: RestoreDrillVerificationResult,
) -> None:
    if restore_drill_verification_result_ready(result):
        return
    raise TenantAccessError(
        "Restore drill evidence object does not confirm a successful restore drill"
    )


def is_restore_drill_evidence_json_content_type(content_type: str) -> bool:
    media_type = content_type.split(";", 1)[0].strip().lower()
    return media_type == "application/json"


def restore_drill_verification_result_ready(
    result: RestoreDrillVerificationResult,
) -> bool:
    return (
        result.backup_manifest_generated
        and result.restore_order_executed
        and result.database_restore_verified
        and result.object_storage_restore_verified
        and result.redis_restore_or_rebuild_verified
        and result.config_restore_verified
        and result.post_restore_validation_passed
    )


def ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
