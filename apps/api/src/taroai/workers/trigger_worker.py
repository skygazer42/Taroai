from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from taroai.audit import AuditActor, AuditEventCreate, AuditService
from taroai.domain import (
    ChatMessageCreate,
    ChatMessageDispatchStatus,
    ChatThreadCreate,
    ResourceReference,
    RunCreate,
    RunMode,
    RunStatus,
    utc_now,
)
from taroai.triggers.service import TriggerService
from taroai.workers.models import (
    JobEnvelope,
    JobType,
    RunExecutionJob,
    TriggerDueJob,
)
from taroai.workers.queue import JobQueue


class TriggerDueWorker(BaseModel):
    store: Any
    trigger_service: TriggerService
    queue: JobQueue
    audit_service: AuditService | None = None
    worker_id: str = "trigger_due_worker"
    lease_seconds: int = 300
    retry_delay_seconds: int = 30
    max_attempts: int = Field(default=3, ge=1)
    run_execution_queue_name: str = "runs.execute"

    def process_next(self, now: datetime | None = None) -> JobEnvelope | None:
        resolved_now = now or utc_now()
        job = self.queue.claim(
            JobType.TRIGGER_DUE,
            worker_id=self.worker_id,
            now=resolved_now,
            lease_seconds=self.lease_seconds,
        )
        if job is None:
            return None

        payload = TriggerDueJob.model_validate(job.payload)
        self._record_job_audit("worker.job.started", job, payload)
        try:
            run = self._create_run(payload)
            execution_job = self._enqueue_run_execution(payload, run.id, resolved_now)
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
            )
            return rejected

        completed = self.queue.ack(job.id, now=resolved_now)
        self._record_job_audit(
            "worker.job.succeeded",
            completed,
            payload,
            {
                "run_id": run.id,
                "execution_job_id": execution_job.id,
            },
        )
        return completed

    def _create_run(self, payload: TriggerDueJob):
        run_request = self.trigger_service.build_run_request(
            tenant_id=payload.tenant_id,
            trigger_id=payload.trigger_id,
        )
        thread = None
        message = None
        resource_refs = []
        if run_request.agent_id:
            resource_refs = [ResourceReference(type="agent", id=run_request.agent_id)]
            thread = self.store.create_chat_thread(
                payload.tenant_id,
                run_request.requested_by_user_id,
                ChatThreadCreate(
                    workspace_id=run_request.workspace_id,
                    title=run_request.message[:160],
                ),
            )
            message = self.store.append_chat_message(
                payload.tenant_id,
                thread.id,
                run_request.requested_by_user_id,
                ChatMessageCreate(
                    content=run_request.message,
                    kind="agent",
                    dispatch_status=ChatMessageDispatchStatus.INFLIGHT,
                    resource_refs=resource_refs,
                ),
            )
        run = self.store.create_run(
            tenant_id=payload.tenant_id,
            user_id=run_request.requested_by_user_id,
            payload=RunCreate(
                workspace_id=run_request.workspace_id,
                agent_id=run_request.agent_id,
                message=run_request.message,
                mode=RunMode.AUTONOMOUS,
                thread_id=thread.id if thread else None,
                trigger_message_id=message.id if message else None,
                resource_refs=resource_refs,
            ),
        )
        audit_service = self.audit_service or AuditService(store=self.store)
        audit_service.record(
            AuditEventCreate(
                tenant_id=payload.tenant_id,
                workspace_id=run.workspace_id,
                user_id=run_request.requested_by_user_id,
                run_id=run.id,
                event_type="trigger.invoked",
                metadata={
                    "trigger_id": run_request.trigger_id,
                    "trigger_type": run_request.trigger_type.value,
                    "run_id": run.id,
                    "scheduled_for": _serialize_datetime(payload.scheduled_for),
                    "worker_id": self.worker_id,
                },
                actor=AuditActor(
                    tenant_id=payload.tenant_id,
                    user_id=run_request.requested_by_user_id,
                    actor_type="worker",
                ),
            )
        )
        self.store.record_billing_meter(
            tenant_id=payload.tenant_id,
            run_id=run.id,
            meter_type="trigger_invocation_count",
            quantity=1,
            unit="invocation",
            metadata={
                "trigger_id": run_request.trigger_id,
                "trigger_type": run_request.trigger_type.value,
            },
        )
        return run

    def _enqueue_run_execution(
        self,
        payload: TriggerDueJob,
        run_id: str,
        now: datetime,
    ) -> JobEnvelope:
        run = self.store.update_run_status(
            payload.tenant_id,
            run_id,
            RunStatus.QUEUED,
        )
        job = self.queue.enqueue(
            JobType.RUN_EXECUTION,
            RunExecutionJob(
                tenant_id=run.tenant_id,
                workspace_id=run.workspace_id,
                user_id=run.user_id,
                run_id=run.id,
                requested_by_user_id=payload.requested_by_user_id,
            ),
            now=now,
            max_attempts=self.max_attempts,
        )
        self.store.append_run_event(
            run,
            "run.execution_queued",
            {
                "job_id": job.id,
                "queue": self.run_execution_queue_name,
                "reason": "trigger_due",
                "trigger_id": payload.trigger_id,
            },
        )
        return job

    def _record_job_audit(
        self,
        event_type: str,
        job: JobEnvelope,
        payload: TriggerDueJob,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self.audit_service is None:
            return
        self.audit_service.record(
            AuditEventCreate(
                tenant_id=payload.tenant_id,
                workspace_id=payload.workspace_id,
                user_id=payload.requested_by_user_id,
                run_id=metadata.get("run_id") if metadata else None,
                event_type=event_type,
                metadata={
                    "job_id": job.id,
                    "job_type": job.type.value,
                    "worker_id": self.worker_id,
                    "trigger_id": payload.trigger_id,
                    "trigger_type": payload.trigger_type,
                    "scheduled_for": _serialize_datetime(payload.scheduled_for),
                    "requested_by_user_id": payload.requested_by_user_id,
                    "attempts": job.attempts,
                    **(metadata or {}),
                },
                actor=AuditActor(
                    tenant_id=payload.tenant_id,
                    user_id=payload.requested_by_user_id,
                    actor_type="worker",
                ),
            )
        )


def _serialize_datetime(value: datetime) -> str:
    resolved = value
    if resolved.tzinfo is None:
        resolved = resolved.replace(tzinfo=timezone.utc)
    return resolved.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
