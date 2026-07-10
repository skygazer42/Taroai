from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from taroai.audit import AuditActor, AuditEventCreate, AuditService
from taroai.domain import utc_now
from taroai.lifecycle.restore_drill import (
    RestoreDrillEvidenceValidationRequest,
    RestoreDrillRunRecord,
    RestoreDrillRunStatus,
    RestoreDrillSchedule,
    RestoreDrillScheduleStore,
    restore_drill_evidence_content,
    restore_drill_evidence_filename,
    restore_drill_verification_result_ready,
    validate_restore_drill_evidence_object,
)
from taroai.storage import StorageObjectCreate, StoragePurpose
from taroai.workers.models import (
    JobEnvelope,
    JobType,
    RestoreDrillEvidenceCollectionJob,
)
from taroai.workers.queue import JobQueue


class RestoreDrillEvidenceWorker(BaseModel):
    schedule_store: RestoreDrillScheduleStore
    queue: JobQueue
    storage_catalog: Any
    object_storage: Any
    audit_service: AuditService | None = None
    worker_id: str = "restore_drill_evidence_worker"
    lease_seconds: int = 300
    retry_delay_seconds: int = 30
    max_attempts: int = Field(default=3, ge=1)

    def process_next(self, now: datetime | None = None) -> JobEnvelope | None:
        resolved_now = now or utc_now()
        job = self.queue.claim(
            JobType.RESTORE_DRILL_EVIDENCE,
            worker_id=self.worker_id,
            now=resolved_now,
            lease_seconds=self.lease_seconds,
        )
        if job is None:
            return None

        payload = RestoreDrillEvidenceCollectionJob.model_validate(job.payload)
        audit_schedule: RestoreDrillSchedule | None = None
        self._record_job_audit("worker.job.started", job, payload)
        try:
            schedule = self.schedule_store.get_schedule(
                payload.tenant_id,
                payload.schedule_id,
            )
            audit_schedule = schedule
            run_record = self.schedule_store.get_run_record(
                payload.tenant_id,
                payload.run_record_id,
            )
            self._validate_payload_matches_record(payload, schedule, run_record)
            if not restore_drill_verification_result_ready(payload.verification):
                updated = self.schedule_store.update_run_record_status(
                    tenant_id=payload.tenant_id,
                    run_record_id=payload.run_record_id,
                    status=RestoreDrillRunStatus.FAILED,
                )
                self._record_restore_drill_evidence_failed(job, payload, updated)
                self._record_run_record_updated(payload, updated)
                completed = self.queue.ack(job.id, now=resolved_now)
                self._record_job_audit(
                    "worker.job.succeeded",
                    completed,
                    payload,
                    {
                        "run_record_id": updated.id,
                        "run_record_status": updated.status.value,
                        "evidence_ready": False,
                    },
                    schedule=audit_schedule,
                )
                return completed

            updated = self._collect_successful_evidence(
                payload=payload,
                schedule=schedule,
                run_record=run_record,
                job=job,
            )
        except Exception as error:
            rejected = self.queue.reject(
                job.id,
                str(error),
                now=resolved_now,
                retry_delay_seconds=self.retry_delay_seconds,
            )
            self._record_job_audit(
                "worker.job.failed",
                rejected,
                payload,
                {
                    "error_type": error.__class__.__name__,
                    "error": str(error),
                    "final_job_status": rejected.status.value,
                },
                schedule=audit_schedule,
            )
            return rejected

        completed = self.queue.ack(job.id, now=resolved_now)
        self._record_job_audit(
            "worker.job.succeeded",
            completed,
            payload,
            {
                "run_record_id": updated.id,
                "run_record_status": updated.status.value,
                "evidence_object_id": updated.evidence_object_id,
                "evidence_ready": True,
            },
            schedule=audit_schedule,
        )
        return completed

    def _collect_successful_evidence(
        self,
        payload: RestoreDrillEvidenceCollectionJob,
        schedule: RestoreDrillSchedule,
        run_record: RestoreDrillRunRecord,
        job: JobEnvelope,
    ) -> RestoreDrillRunRecord:
        evidence_content = restore_drill_evidence_content(payload.verification)
        evidence_object = self.storage_catalog.register(
            StorageObjectCreate(
                tenant_id=payload.tenant_id,
                workspace_id=schedule.workspace_id,
                purpose=StoragePurpose.DATA_EXPORT,
                filename=restore_drill_evidence_filename(run_record.id),
                content_type="application/json",
                size_bytes=len(evidence_content),
                retention_expires_at=payload.retention_expires_at,
            )
        )
        self.object_storage.upload(evidence_object, evidence_content)
        evidence_object_id = validate_restore_drill_evidence_object(
            RestoreDrillEvidenceValidationRequest(
                tenant_id=payload.tenant_id,
                workspace_id=schedule.workspace_id,
                evidence_object_id=evidence_object.id,
            ),
            self.storage_catalog,
            self.object_storage,
        )
        updated = self.schedule_store.update_run_record_status(
            tenant_id=payload.tenant_id,
            run_record_id=run_record.id,
            status=RestoreDrillRunStatus.EVIDENCE_READY,
            evidence_object_id=evidence_object_id,
        )
        self._record_restore_drill_evidence_collected(job, payload, updated)
        self._record_run_record_updated(payload, updated)
        return updated

    def _validate_payload_matches_record(
        self,
        payload: RestoreDrillEvidenceCollectionJob,
        schedule: RestoreDrillSchedule,
        run_record: RestoreDrillRunRecord,
    ) -> None:
        if (
            schedule.tenant_id != payload.tenant_id
            or schedule.workspace_id != payload.workspace_id
            or schedule.id != payload.schedule_id
            or run_record.tenant_id != payload.tenant_id
            or run_record.workspace_id != payload.workspace_id
            or run_record.schedule_id != payload.schedule_id
            or run_record.id != payload.run_record_id
            or run_record.requested_by_user_id != payload.requested_by_user_id
        ):
            raise ValueError("restore drill evidence job does not match run record")
        if run_record.status != RestoreDrillRunStatus.REQUESTED:
            raise ValueError("restore drill run record is already terminal")

    def _record_restore_drill_evidence_collected(
        self,
        job: JobEnvelope,
        payload: RestoreDrillEvidenceCollectionJob,
        record: RestoreDrillRunRecord,
    ) -> None:
        self._record_restore_drill_event(
            "restore_drill.evidence_collected",
            job,
            payload,
            record,
            {"verification_ready": True},
        )

    def _record_restore_drill_evidence_failed(
        self,
        job: JobEnvelope,
        payload: RestoreDrillEvidenceCollectionJob,
        record: RestoreDrillRunRecord,
    ) -> None:
        self._record_restore_drill_event(
            "restore_drill.evidence_failed",
            job,
            payload,
            record,
            {"verification_ready": False},
        )

    def _record_run_record_updated(
        self,
        payload: RestoreDrillEvidenceCollectionJob,
        record: RestoreDrillRunRecord,
    ) -> None:
        if self.audit_service is None:
            return
        self.audit_service.record(
            AuditEventCreate(
                tenant_id=payload.tenant_id,
                workspace_id=record.workspace_id,
                user_id=payload.requested_by_user_id,
                event_type="restore_drill.run_record.updated",
                metadata=restore_drill_run_record_metadata(record),
                actor=AuditActor(
                    tenant_id=payload.tenant_id,
                    user_id=payload.requested_by_user_id,
                    actor_type="worker",
                ),
            )
        )

    def _record_restore_drill_event(
        self,
        event_type: str,
        job: JobEnvelope,
        payload: RestoreDrillEvidenceCollectionJob,
        record: RestoreDrillRunRecord,
        metadata: dict[str, Any],
    ) -> None:
        if self.audit_service is None:
            return
        self.audit_service.record(
            AuditEventCreate(
                tenant_id=payload.tenant_id,
                workspace_id=record.workspace_id,
                user_id=payload.requested_by_user_id,
                event_type=event_type,
                metadata={
                    "job_id": job.id,
                    "job_type": job.type.value,
                    "worker_id": self.worker_id,
                    **restore_drill_run_record_metadata(record),
                    **metadata,
                },
                actor=AuditActor(
                    tenant_id=payload.tenant_id,
                    user_id=payload.requested_by_user_id,
                    actor_type="worker",
                ),
            )
        )

    def _record_job_audit(
        self,
        event_type: str,
        job: JobEnvelope,
        payload: RestoreDrillEvidenceCollectionJob,
        metadata: dict[str, Any] | None = None,
        schedule: RestoreDrillSchedule | None = None,
    ) -> None:
        if self.audit_service is None:
            return
        workspace_id = schedule.workspace_id if schedule is not None else payload.workspace_id
        user_id = (
            schedule.service_account_id or schedule.created_by_user_id
            if schedule is not None
            else payload.requested_by_user_id
        )
        self.audit_service.record(
            AuditEventCreate(
                tenant_id=payload.tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                event_type=event_type,
                metadata={
                    "job_id": job.id,
                    "job_type": job.type.value,
                    "worker_id": self.worker_id,
                    "schedule_id": payload.schedule_id,
                    "run_record_id": payload.run_record_id,
                    "requested_by_user_id": user_id,
                    "attempts": job.attempts,
                    **(metadata or {}),
                },
                actor=AuditActor(
                    tenant_id=payload.tenant_id,
                    user_id=user_id,
                    actor_type="worker",
                ),
            )
        )


def restore_drill_run_record_metadata(record: RestoreDrillRunRecord) -> dict[str, Any]:
    return {
        "run_record_id": record.id,
        "workspace_id": record.workspace_id,
        "schedule_id": record.schedule_id,
        "scheduled_for": serialize_datetime(record.scheduled_for),
        "requested_by_user_id": record.requested_by_user_id,
        "status": record.status.value,
        "has_evidence_object": record.evidence_object_id is not None,
        "evidence_object_id": record.evidence_object_id,
    }


def serialize_datetime(value: datetime) -> str:
    resolved = value
    if resolved.tzinfo is None:
        resolved = resolved.replace(tzinfo=timezone.utc)
    return resolved.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
