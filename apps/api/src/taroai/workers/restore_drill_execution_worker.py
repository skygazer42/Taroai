from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from taroai.audit import AuditActor, AuditEventCreate, AuditService
from taroai.deployment_evidence import (
    RestoreDrillVerificationConfig,
    RestoreDrillVerificationResult,
)
from taroai.domain import utc_now
from taroai.lifecycle.restore_drill import (
    RestoreDrillRunRecord,
    RestoreDrillRunStatus,
    RestoreDrillSchedule,
    RestoreDrillScheduleStore,
    restore_drill_verification_result_ready,
)
from taroai.workers.models import (
    JobEnvelope,
    JobType,
    RestoreDrillEvidenceCollectionJob,
    RestoreDrillExecutionJob,
)
from taroai.workers.queue import JobQueue


RestoreDrillVerifier = Callable[
    [RestoreDrillVerificationConfig],
    RestoreDrillVerificationResult,
]


def verify_restore_drill_from_config(
    config: RestoreDrillVerificationConfig,
) -> RestoreDrillVerificationResult:
    from taroai.deployment.restore_drill_verification import verify_restore_drill

    return verify_restore_drill(config)


class RestoreDrillExecutionWorker(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    schedule_store: RestoreDrillScheduleStore
    queue: JobQueue
    verifier: RestoreDrillVerifier = verify_restore_drill_from_config
    audit_service: AuditService | None = None
    worker_id: str = "restore_drill_execution_worker"
    lease_seconds: int = 300
    retry_delay_seconds: int = 30
    max_attempts: int = Field(default=3, ge=1)

    def process_next(self, now: datetime | None = None) -> JobEnvelope | None:
        resolved_now = now or utc_now()
        job = self.queue.claim(
            JobType.RESTORE_DRILL_EXECUTION,
            worker_id=self.worker_id,
            now=resolved_now,
            lease_seconds=self.lease_seconds,
        )
        if job is None:
            return None

        payload = RestoreDrillExecutionJob.model_validate(job.payload)
        audit_schedule: RestoreDrillSchedule | None = None
        verifier_failed = False
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
            try:
                verification = self.verifier(payload.verification_config)
            except Exception:
                verifier_failed = True
                raise
            evidence_job = self.queue.enqueue(
                JobType.RESTORE_DRILL_EVIDENCE,
                RestoreDrillEvidenceCollectionJob(
                    tenant_id=payload.tenant_id,
                    workspace_id=payload.workspace_id,
                    schedule_id=payload.schedule_id,
                    run_record_id=payload.run_record_id,
                    requested_by_user_id=payload.requested_by_user_id,
                    verification=verification,
                    retention_expires_at=payload.retention_expires_at,
                ),
                now=resolved_now,
                max_attempts=self.max_attempts,
            )
            self._record_restore_drill_execution_completed(
                job=job,
                payload=payload,
                record=run_record,
                evidence_job=evidence_job,
                verification=verification,
            )
        except Exception as error:
            rejected = self.queue.reject(
                job.id,
                "restore drill verifier failed" if verifier_failed else str(error),
                now=resolved_now,
                retry_delay_seconds=self.retry_delay_seconds,
            )
            self._record_job_audit(
                "worker.job.failed",
                rejected,
                payload,
                {
                    "error_type": error.__class__.__name__,
                    "error": "restore drill verifier failed"
                    if verifier_failed
                    else str(error),
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
                "run_record_id": payload.run_record_id,
                "evidence_job_id": evidence_job.id,
                "verification_ready": restore_drill_verification_result_ready(
                    verification
                ),
            },
            schedule=audit_schedule,
        )
        return completed

    def _validate_payload_matches_record(
        self,
        payload: RestoreDrillExecutionJob,
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
            or run_record.runbook_ref != schedule.runbook_ref
        ):
            raise ValueError("restore drill execution job does not match run record")
        if run_record.status != RestoreDrillRunStatus.REQUESTED:
            raise ValueError("restore drill run record is already terminal")

    def _record_restore_drill_execution_completed(
        self,
        job: JobEnvelope,
        payload: RestoreDrillExecutionJob,
        record: RestoreDrillRunRecord,
        evidence_job: JobEnvelope,
        verification: RestoreDrillVerificationResult,
    ) -> None:
        if self.audit_service is None:
            return
        self.audit_service.record(
            AuditEventCreate(
                tenant_id=payload.tenant_id,
                workspace_id=record.workspace_id,
                user_id=payload.requested_by_user_id,
                event_type="restore_drill.execution_completed",
                metadata={
                    "job_id": job.id,
                    "job_type": job.type.value,
                    "worker_id": self.worker_id,
                    **restore_drill_run_record_metadata(record),
                    "evidence_job_id": evidence_job.id,
                    "verification_ready": restore_drill_verification_result_ready(
                        verification
                    ),
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
        payload: RestoreDrillExecutionJob,
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
