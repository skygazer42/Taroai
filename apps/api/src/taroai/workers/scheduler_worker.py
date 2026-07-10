from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from taroai.audit import AuditActor, AuditEventCreate, AuditService
from taroai.domain import utc_now
from taroai.triggers.models import TriggerDefinition
from taroai.triggers.service import TriggerService
from taroai.workers.models import JobEnvelope
from taroai.workers.queue import JobQueue


class TriggerSchedulerResult(BaseModel):
    scanned_triggers: int = 0
    enqueued_jobs: int = 0
    updated_triggers: int = 0
    last_trigger_id: str | None = None


class TriggerSchedulerWorker(BaseModel):
    trigger_service: TriggerService
    queue: JobQueue
    audit_service: AuditService | None = None
    worker_id: str = "trigger_scheduler"
    max_attempts: int = Field(default=3, ge=1)

    def process_due(self, now: datetime | None = None) -> TriggerSchedulerResult:
        resolved_now = now or utc_now()
        result = TriggerSchedulerResult()

        for trigger in self.trigger_service.list_schedule_triggers():
            from taroai.triggers.scheduler import evaluate_trigger_schedule

            result.scanned_triggers += 1
            result.last_trigger_id = trigger.id
            evaluation = evaluate_trigger_schedule(trigger, resolved_now)
            queued_jobs = [
                self.queue.enqueue(
                    evaluation.job_type,
                    due_job,
                    now=resolved_now,
                    max_attempts=self.max_attempts,
                )
                for due_job in evaluation.due_jobs
            ]
            result.enqueued_jobs += len(queued_jobs)

            next_run_updated = evaluation.next_run_at != trigger.next_run_at
            if next_run_updated:
                self.trigger_service.update_next_run_at(
                    tenant_id=trigger.tenant_id,
                    trigger_id=trigger.id,
                    next_run_at=evaluation.next_run_at,
                )
                result.updated_triggers += 1

            if queued_jobs or next_run_updated:
                self._record_schedule_audit(
                    trigger=trigger,
                    queued_jobs=queued_jobs,
                    next_run_at=evaluation.next_run_at,
                    evaluated_at=resolved_now,
                )

        return result

    def _record_schedule_audit(
        self,
        trigger: TriggerDefinition,
        queued_jobs: list[JobEnvelope],
        next_run_at: datetime | None,
        evaluated_at: datetime,
    ) -> None:
        if self.audit_service is None:
            return

        actor_user_id = trigger.service_account_id or trigger.created_by_user_id
        self.audit_service.record(
            AuditEventCreate(
                tenant_id=trigger.tenant_id,
                workspace_id=trigger.workspace_id,
                user_id=actor_user_id,
                event_type="trigger.schedule.evaluated",
                metadata={
                    "trigger_id": trigger.id,
                    "trigger_type": trigger.type.value,
                    "worker_id": self.worker_id,
                    "job_type": "triggers.due",
                    "due_job_count": len(queued_jobs),
                    "queued_job_ids": [job.id for job in queued_jobs],
                    "previous_next_run_at": _serialize_datetime(trigger.next_run_at),
                    "next_run_at": _serialize_datetime(next_run_at),
                    "evaluated_at": _serialize_datetime(evaluated_at),
                },
                actor=AuditActor(
                    tenant_id=trigger.tenant_id,
                    user_id=actor_user_id,
                    actor_type="worker",
                ),
            )
        )


def _serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    resolved = value
    if resolved.tzinfo is None:
        resolved = resolved.replace(tzinfo=timezone.utc)
    return resolved.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
