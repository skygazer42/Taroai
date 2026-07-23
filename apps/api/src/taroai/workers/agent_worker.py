from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from taroai.agent import AgentRuntime
from taroai.agent.loop import AgentExecutionServices
from taroai.agent.state import AgentRuntimeState
from taroai.audit import AuditActor, AuditEventCreate, AuditService
from taroai.domain import Run, RunStatus
from taroai.store import TERMINAL_RUN_STATUSES
from taroai.workers.models import JobEnvelope, JobStatus, JobType, RunExecutionJob
from taroai.workers.queue import JobQueue


class AgentWorker(BaseModel):
    runtime: AgentRuntime
    queue: JobQueue
    audit_service: AuditService | None = None
    chat_service: Any | None = None
    workflow_coordinator: Any | None = None
    worker_id: str = "agent_worker"
    lease_seconds: int = 300
    retry_delay_seconds: int = 30
    continuation_max_attempts: int = 3
    refresh_model_runtime: Callable[[], None] | None = None

    def process_next(self) -> JobEnvelope | None:
        for expired in self.queue.reap_expired_leases(JobType.RUN_EXECUTION):
            if expired.status == JobStatus.DEAD_LETTER:
                self._finish_dead_letter(
                    RunExecutionJob.model_validate(expired.payload)
                )
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
            if self.refresh_model_runtime is not None:
                self.refresh_model_runtime()
            run_before = self.runtime.store.get_run(payload.tenant_id, payload.run_id)
            if self.workflow_coordinator is not None:
                self.workflow_coordinator.mark_running(run_before)
            state = self.runtime.execute_run(payload.tenant_id, payload.run_id)
            self.runtime.store.get_runtime_state(payload.tenant_id, payload.run_id)
            if state.waiting_reason in {
                "action_is_owned_by_another_live_worker",
                "action_lease_lost_before_commit",
                "action_commit_fence_rejected",
            }:
                raise RuntimeError(state.waiting_reason)
            run = self.runtime.store.get_run(payload.tenant_id, payload.run_id)
            self._continue_after_terminal(payload, run, state)
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
            if rejected.status == JobStatus.PENDING:
                run = self.runtime.store.get_run(payload.tenant_id, payload.run_id)
                if run.status not in TERMINAL_RUN_STATUSES:
                    self.runtime.store.update_run_status(
                        payload.tenant_id,
                        payload.run_id,
                        RunStatus.RETRYING,
                    )
            elif rejected.status == JobStatus.DEAD_LETTER:
                self._finish_dead_letter(payload)
            return rejected
        completed = self.queue.ack(job.id)
        self._record_job_audit("worker.job.succeeded", completed, payload)
        return completed

    def _finish_dead_letter(self, payload: RunExecutionJob) -> None:
        run = self.runtime.store.get_run(payload.tenant_id, payload.run_id)
        execution = AgentExecutionServices(self.runtime)
        state = execution._restore_state(run)
        if run.status not in TERMINAL_RUN_STATUSES:
            state = execution._fail(state, run, "worker_retries_exhausted")
            run = self.runtime.store.get_run(payload.tenant_id, payload.run_id)
        self._continue_after_terminal(payload, run, state)

    def _continue_after_terminal(
        self,
        payload: RunExecutionJob,
        run: Run,
        state: AgentRuntimeState,
    ) -> None:
        if state.status not in TERMINAL_RUN_STATUSES:
            return
        is_workflow_child = False
        if self.workflow_coordinator is not None:
            is_workflow_child = (
                self.runtime.store.get_workflow_task_for_child_run(
                    payload.tenant_id, payload.run_id
                )
                is not None
            )
            for next_run in self.workflow_coordinator.complete_child(run, state):
                next_job = self.queue.enqueue(
                    JobType.RUN_EXECUTION,
                    RunExecutionJob(
                        tenant_id=next_run.tenant_id,
                        workspace_id=next_run.workspace_id,
                        user_id=next_run.user_id,
                        run_id=next_run.id,
                        requested_by_user_id=next_run.user_id,
                    ),
                    max_attempts=self.continuation_max_attempts,
                )
                self.runtime.store.append_run_event(
                    next_run,
                    "run.execution_queued",
                    {"job_id": next_job.id, "reason": "workflow_dependency_ready"},
                )
        if self.chat_service is None or is_workflow_child or run.thread_id is None:
            return
        continuation = self.chat_service.continue_thread(
            payload.tenant_id,
            run.thread_id,
        )
        if continuation is None or not continuation.run_started:
            return
        next_run = self.runtime.store.get_run(
            payload.tenant_id,
            continuation.run_id,
        )
        continuation_job = self.queue.enqueue(
            JobType.RUN_EXECUTION,
            RunExecutionJob(
                tenant_id=next_run.tenant_id,
                workspace_id=next_run.workspace_id,
                user_id=next_run.user_id,
                run_id=next_run.id,
                requested_by_user_id=next_run.user_id,
            ),
            max_attempts=self.continuation_max_attempts,
        )
        self.runtime.store.append_run_event(
            next_run,
            "run.execution_queued",
            {
                "job_id": continuation_job.id,
                "reason": "thread_continuation",
                "previous_run_id": run.id,
            },
        )

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
