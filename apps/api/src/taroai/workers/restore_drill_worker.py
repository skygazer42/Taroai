from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from taroai.audit import AuditActor, AuditEventCreate, AuditService
from taroai.domain import utc_now
from taroai.lifecycle.restore_drill import (
    RestoreDrillRunRecord,
    RestoreDrillSchedule,
    RestoreDrillScheduleStatus,
    RestoreDrillScheduleStore,
)
from taroai.workers.models import JobEnvelope, JobType, RestoreDrillDueJob
from taroai.workers.queue import JobQueue


class RestoreDrillDueWorker(BaseModel):
    schedule_store: RestoreDrillScheduleStore
    queue: JobQueue
    audit_service: AuditService | None = None
    worker_id: str = "restore_drill_due_worker"
    lease_seconds: int = 300
    retry_delay_seconds: int = 30
    max_attempts: int = Field(default=3, ge=1)

    def process_next(self, now: datetime | None = None) -> JobEnvelope | None:
        resolved_now = now or utc_now()
        job = self.queue.claim(
            JobType.RESTORE_DRILL_DUE,
            worker_id=self.worker_id,
            now=resolved_now,
            lease_seconds=self.lease_seconds,
        )
        if job is None:
            return None

        payload = RestoreDrillDueJob.model_validate(job.payload)
        audit_schedule: RestoreDrillSchedule | None = None
        job_started_recorded = False
        try:
            schedule = self.schedule_store.get_schedule(
                payload.tenant_id,
                payload.schedule_id,
            )
            audit_schedule = schedule
            self._record_job_audit(
                "worker.job.started",
                job,
                payload,
                schedule=audit_schedule,
            )
            job_started_recorded = True
            self._validate_due_payload_matches_schedule(payload, schedule)
            if schedule.status == RestoreDrillScheduleStatus.DISABLED:
                completed = self.queue.ack(job.id, now=resolved_now)
                skip_reason = "schedule_disabled"
                self._record_restore_drill_skipped(job, payload, skip_reason)
                self._record_job_audit(
                    "worker.job.succeeded",
                    completed,
                    payload,
                    {
                        "skipped": True,
                        "skip_reason": skip_reason,
                    },
                )
                return completed
            existing_record = self.schedule_store.get_run_record_by_schedule_time(
                tenant_id=payload.tenant_id,
                schedule_id=payload.schedule_id,
                scheduled_for=payload.scheduled_for,
            )
            if existing_record is not None:
                completed = self.queue.ack(job.id, now=resolved_now)
                skip_reason = "run_record_exists"
                self._record_restore_drill_skipped(
                    job,
                    payload,
                    skip_reason,
                    existing_run_record_id=existing_record.id,
                )
                self._record_job_audit(
                    "worker.job.succeeded",
                    completed,
                    payload,
                    {
                        "skipped": True,
                        "skip_reason": skip_reason,
                        "existing_run_record_id": existing_record.id,
                    },
                )
                return completed
            record = self._create_run_record(payload, schedule)
            self._record_restore_drill_requested(job, payload, record)
        except Exception as error:
            if not job_started_recorded:
                self._record_job_audit(
                    "worker.job.started",
                    job,
                    payload,
                    schedule=audit_schedule,
                )
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
            {"run_record_id": record.id},
        )
        return completed

    def _create_run_record(
        self,
        payload: RestoreDrillDueJob,
        schedule: RestoreDrillSchedule,
    ) -> RestoreDrillRunRecord:
        return self.schedule_store.create_run_record(
            RestoreDrillRunRecord(
                tenant_id=payload.tenant_id,
                workspace_id=payload.workspace_id,
                schedule_id=payload.schedule_id,
                scheduled_for=payload.scheduled_for,
                requested_by_user_id=payload.requested_by_user_id,
                runbook_ref=payload.runbook_ref,
            )
        )

    def _validate_due_payload_matches_schedule(
        self,
        payload: RestoreDrillDueJob,
        schedule: RestoreDrillSchedule,
    ) -> None:
        expected_user_id = schedule.service_account_id or schedule.created_by_user_id
        if (
            schedule.tenant_id != payload.tenant_id
            or schedule.workspace_id != payload.workspace_id
            or schedule.id != payload.schedule_id
            or schedule.runbook_ref != payload.runbook_ref
            or expected_user_id != payload.requested_by_user_id
        ):
            raise ValueError("restore drill due job does not match schedule")

    def _record_restore_drill_skipped(
        self,
        job: JobEnvelope,
        payload: RestoreDrillDueJob,
        skip_reason: str,
        existing_run_record_id: str | None = None,
    ) -> None:
        if self.audit_service is None:
            return
        metadata = {
            "job_id": job.id,
            "job_type": job.type.value,
            "worker_id": self.worker_id,
            "schedule_id": payload.schedule_id,
            "scheduled_for": serialize_datetime(payload.scheduled_for),
            "requested_by_user_id": payload.requested_by_user_id,
            "skip_reason": skip_reason,
        }
        if existing_run_record_id is not None:
            metadata["existing_run_record_id"] = existing_run_record_id
        self.audit_service.record(
            AuditEventCreate(
                tenant_id=payload.tenant_id,
                workspace_id=payload.workspace_id,
                user_id=payload.requested_by_user_id,
                event_type="restore_drill.skipped",
                metadata=metadata,
                actor=AuditActor(
                    tenant_id=payload.tenant_id,
                    user_id=payload.requested_by_user_id,
                    actor_type="worker",
                ),
            )
        )

    def _record_restore_drill_requested(
        self,
        job: JobEnvelope,
        payload: RestoreDrillDueJob,
        record: RestoreDrillRunRecord,
    ) -> None:
        if self.audit_service is None:
            return
        self.audit_service.record(
            AuditEventCreate(
                tenant_id=payload.tenant_id,
                workspace_id=payload.workspace_id,
                user_id=payload.requested_by_user_id,
                event_type="restore_drill.requested",
                metadata={
                    "job_id": job.id,
                    "job_type": job.type.value,
                    "worker_id": self.worker_id,
                    "schedule_id": payload.schedule_id,
                    "run_record_id": record.id,
                    "scheduled_for": serialize_datetime(payload.scheduled_for),
                    "runbook_ref": payload.runbook_ref,
                    "requested_by_user_id": payload.requested_by_user_id,
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
        payload: RestoreDrillDueJob,
        metadata: dict[str, Any] | None = None,
        schedule: RestoreDrillSchedule | None = None,
    ) -> None:
        if self.audit_service is None:
            return
        workspace_id = schedule.workspace_id if schedule is not None else payload.workspace_id
        requested_by_user_id = (
            schedule.service_account_id or schedule.created_by_user_id
            if schedule is not None
            else payload.requested_by_user_id
        )
        self.audit_service.record(
            AuditEventCreate(
                tenant_id=payload.tenant_id,
                workspace_id=workspace_id,
                user_id=requested_by_user_id,
                event_type=event_type,
                metadata={
                    "job_id": job.id,
                    "job_type": job.type.value,
                    "worker_id": self.worker_id,
                    "schedule_id": payload.schedule_id,
                    "scheduled_for": serialize_datetime(payload.scheduled_for),
                    "requested_by_user_id": requested_by_user_id,
                    "attempts": job.attempts,
                    **(metadata or {}),
                },
                actor=AuditActor(
                    tenant_id=payload.tenant_id,
                    user_id=requested_by_user_id,
                    actor_type="worker",
                ),
            )
        )


def serialize_datetime(value: datetime) -> str:
    resolved = value
    if resolved.tzinfo is None:
        resolved = resolved.replace(tzinfo=timezone.utc)
    return resolved.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
