from datetime import datetime, timezone

from pydantic import BaseModel, Field

from taroai.audit import AuditActor, AuditEventCreate, AuditService
from taroai.domain import utc_now
from taroai.lifecycle.restore_drill import (
    RestoreDrillSchedule,
    RestoreDrillScheduleStore,
    ensure_aware_utc,
    evaluate_restore_drill_schedule,
)
from taroai.workers.models import JobEnvelope, JobType, RestoreDrillDueJob
from taroai.workers.queue import JobQueue


class RestoreDrillSchedulerResult(BaseModel):
    scanned_schedules: int = 0
    enqueued_jobs: int = 0
    updated_schedules: int = 0
    last_schedule_id: str | None = None


class RestoreDrillSchedulerWorker(BaseModel):
    schedule_store: RestoreDrillScheduleStore
    queue: JobQueue
    audit_service: AuditService | None = None
    worker_id: str = "restore_drill_scheduler"
    max_attempts: int = Field(default=3, ge=1)

    def process_due(self, now: datetime | None = None) -> RestoreDrillSchedulerResult:
        resolved_now = now or utc_now()
        result = RestoreDrillSchedulerResult()

        for schedule in self.schedule_store.list_schedules():
            result.scanned_schedules += 1
            result.last_schedule_id = schedule.id
            evaluation = evaluate_restore_drill_schedule(schedule, resolved_now)
            queued_jobs = [
                self.queue.enqueue(
                    JobType.RESTORE_DRILL_DUE,
                    build_restore_drill_due_job(schedule, scheduled_for),
                    now=resolved_now,
                    max_attempts=self.max_attempts,
                )
                for scheduled_for in evaluation.due_scheduled_for
            ]
            result.enqueued_jobs += len(queued_jobs)

            next_run_updated = evaluation.next_run_at != schedule.next_run_at
            if next_run_updated:
                self.schedule_store.update_next_run_at(
                    tenant_id=schedule.tenant_id,
                    schedule_id=schedule.id,
                    next_run_at=evaluation.next_run_at,
                )
                result.updated_schedules += 1

            if queued_jobs or next_run_updated:
                self._record_schedule_audit(
                    schedule=schedule,
                    queued_jobs=queued_jobs,
                    next_run_at=evaluation.next_run_at,
                    evaluated_at=resolved_now,
                )

        return result

    def _record_schedule_audit(
        self,
        schedule: RestoreDrillSchedule,
        queued_jobs: list[JobEnvelope],
        next_run_at: datetime | None,
        evaluated_at: datetime,
    ) -> None:
        if self.audit_service is None:
            return

        actor_user_id = schedule.service_account_id or schedule.created_by_user_id
        self.audit_service.record(
            AuditEventCreate(
                tenant_id=schedule.tenant_id,
                workspace_id=schedule.workspace_id,
                user_id=actor_user_id,
                event_type="restore_drill.schedule.evaluated",
                metadata={
                    "schedule_id": schedule.id,
                    "worker_id": self.worker_id,
                    "job_type": "restore_drill.due",
                    "due_job_count": len(queued_jobs),
                    "queued_job_ids": [job.id for job in queued_jobs],
                    "previous_next_run_at": serialize_datetime(schedule.next_run_at),
                    "next_run_at": serialize_datetime(next_run_at),
                    "evaluated_at": serialize_datetime(evaluated_at),
                },
                actor=AuditActor(
                    tenant_id=schedule.tenant_id,
                    user_id=actor_user_id,
                    actor_type="worker",
                ),
            )
        )


def build_restore_drill_due_job(
    schedule: RestoreDrillSchedule,
    scheduled_for: datetime,
) -> RestoreDrillDueJob:
    requested_by_user_id = schedule.service_account_id or schedule.created_by_user_id
    if requested_by_user_id is None:
        raise ValueError(f"restore drill schedule {schedule.id} has no accountable identity")
    return RestoreDrillDueJob(
        tenant_id=schedule.tenant_id,
        workspace_id=schedule.workspace_id,
        schedule_id=schedule.id,
        scheduled_for=ensure_aware_utc(scheduled_for),
        requested_by_user_id=requested_by_user_id,
        runbook_ref=schedule.runbook_ref,
    )


def serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    resolved = value
    if resolved.tzinfo is None:
        resolved = resolved.replace(tzinfo=timezone.utc)
    return resolved.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
