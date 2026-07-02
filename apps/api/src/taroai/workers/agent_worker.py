from typing import Any

from pydantic import BaseModel

from taroai.agent import AgentRuntime
from taroai.audit import AuditActor, AuditEventCreate, AuditService
from taroai.workers.models import JobEnvelope, JobType, RunExecutionJob
from taroai.workers.queue import JobQueue


class AgentWorker(BaseModel):
    runtime: AgentRuntime
    queue: JobQueue
    audit_service: AuditService | None = None
    worker_id: str = "agent_worker"
    lease_seconds: int = 300
    retry_delay_seconds: int = 30

    def process_next(self) -> JobEnvelope | None:
        job = self.queue.claim(
            JobType.RUN_EXECUTION,
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if job is None:
            return None
        payload = RunExecutionJob.model_validate(job.payload)
        self._record_job_audit("worker.job.started", job, payload)
        try:
            self.runtime.execute_run(payload.tenant_id, payload.run_id)
        except Exception as error:
            rejected = self.queue.reject(
                job.id,
                str(error),
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
        completed = self.queue.ack(job.id)
        self._record_job_audit("worker.job.succeeded", completed, payload)
        return completed

    def _record_job_audit(
        self,
        event_type: str,
        job: JobEnvelope,
        payload: RunExecutionJob,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self.audit_service is None:
            return
        self.audit_service.record(
            AuditEventCreate(
                tenant_id=payload.tenant_id,
                workspace_id=payload.workspace_id,
                user_id=payload.requested_by_user_id,
                run_id=payload.run_id,
                event_type=event_type,
                metadata={
                    "job_id": job.id,
                    "job_type": job.type.value,
                    "worker_id": self.worker_id,
                    "run_id": payload.run_id,
                    "target_user_id": payload.user_id,
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
