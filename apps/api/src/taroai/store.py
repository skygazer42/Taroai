from __future__ import annotations

import json
from datetime import datetime
from threading import RLock
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, PrivateAttr

from taroai.domain import (
    Artifact,
    AuditEvent,
    ApprovalRequest,
    ApprovalStatus,
    BillingMeterEvent,
    ChatMessage,
    ChatMessageCreate,
    ChatMessageDispatchStatus,
    ChatThread,
    ChatThreadCreate,
    ChatThreadStatus,
    IdempotencyRecord,
    Run,
    RunCreate,
    RunEvent,
    RunStatus,
    new_id,
    utc_now,
)
from taroai.errors import NotFoundError, RunTransitionError, TenantAccessError
from taroai.licensing.models import LicenseValidationResult

if TYPE_CHECKING:
    from taroai.agent.models import (
        AgentAction,
        AgentCheckpoint,
        AgentCycle,
        AgentObservation,
        AgentVerificationResult,
    )


TERMINAL_RUN_STATUSES = {
    RunStatus.SUCCEEDED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
    RunStatus.TIMED_OUT,
}

RETRYABLE_RUN_STATUSES = {
    RunStatus.FAILED,
    RunStatus.CANCELLED,
    RunStatus.TIMED_OUT,
}


class RunStateSnapshot(BaseModel):
    tenant_id: str
    workspace_id: str
    user_id: str
    run_id: str
    goal: str
    status: RunStatus
    plan: list[dict[str, Any]] = Field(default_factory=list)
    current_step_id: str | None = None
    completed_step_ids: list[str] = Field(default_factory=list)
    approved_step_ids: list[str] = Field(default_factory=list)
    approved_guardrail_keys: list[str] = Field(default_factory=list)
    pending_guardrail_approval_key: str | None = None
    pending_guardrail_approval_stage: str | None = None
    tool_results: list[dict[str, Any]] = Field(default_factory=list)
    retrieved_context: dict[str, Any] = Field(default_factory=dict)
    sandbox_session_id: str | None = None
    browser_session_id: str | None = None
    approval_id: str | None = None
    failure_reason: str | None = None
    state_payload: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime

    @classmethod
    def from_runtime_state(cls, state: Any) -> "RunStateSnapshot":
        state_payload = state.model_dump(mode="json")
        return cls(
            **state_payload,
            state_payload=state_payload,
            updated_at=utc_now(),
        )

    def to_runtime_state_payload(self) -> dict[str, Any]:
        if self.state_payload:
            return dict(self.state_payload)
        return self.model_dump(
            mode="json",
            exclude={"state_payload", "updated_at"},
        )


class InMemoryControlPlaneStore(BaseModel):
    runs: dict[str, Run] = Field(default_factory=dict)
    run_events: dict[str, list[RunEvent]] = Field(default_factory=dict)
    artifacts: dict[str, list[Artifact]] = Field(default_factory=dict)
    billing_meters: dict[str, list[BillingMeterEvent]] = Field(default_factory=dict)
    audit_events: dict[str, list[AuditEvent]] = Field(default_factory=dict)
    approval_requests: dict[str, list[ApprovalRequest]] = Field(default_factory=dict)
    runtime_states: dict[str, RunStateSnapshot] = Field(default_factory=dict)
    idempotency_records: dict[str, IdempotencyRecord] = Field(default_factory=dict)
    license_validations: dict[str, LicenseValidationResult] = Field(default_factory=dict)
    chat_threads: dict[str, ChatThread] = Field(default_factory=dict)
    chat_messages: dict[str, ChatMessage] = Field(default_factory=dict)
    agent_cycles: dict[str, Any] = Field(default_factory=dict)
    agent_actions: dict[str, Any] = Field(default_factory=dict)
    agent_checkpoints: dict[str, list[Any]] = Field(default_factory=dict)
    _repository_lock: RLock = PrivateAttr(default_factory=RLock)

    def create_run(self, tenant_id: str, user_id: str, payload: RunCreate) -> Run:
        now = utc_now()
        run = Run(
            id=new_id("run"),
            tenant_id=tenant_id,
            workspace_id=payload.workspace_id,
            user_id=user_id,
            agent_id=payload.agent_id,
            message=payload.message,
            attachments=payload.attachments,
            mode=payload.mode,
            status=RunStatus.CREATED,
            created_at=now,
            updated_at=now,
            thread_id=payload.thread_id,
            trigger_message_id=payload.trigger_message_id,
            provider_id=payload.provider_id,
            model_id=payload.model_id,
            reasoning_effort=payload.reasoning_effort,
            resource_refs=payload.resource_refs,
        )
        self.runs[run.id] = run
        self._append_run_event(
            run,
            "run.created",
            {
                "status": run.status.value,
                "mode": run.mode.value,
                "agent_id": run.agent_id,
            },
        )
        self._record_run_meter(run)
        self._record_audit_event(
            tenant_id=tenant_id,
            workspace_id=run.workspace_id,
            user_id=user_id,
            run_id=run.id,
            event_type="run.created",
            metadata={"mode": run.mode.value, "agent_id": run.agent_id},
        )
        return run

    def create_chat_thread(
        self,
        tenant_id: str,
        user_id: str,
        payload: ChatThreadCreate,
    ) -> ChatThread:
        now = utc_now()
        thread = ChatThread(
            id=new_id("thread"),
            tenant_id=tenant_id,
            workspace_id=payload.workspace_id,
            created_by_user_id=user_id,
            title=payload.title,
            status=ChatThreadStatus.ACTIVE,
            pinned=False,
            provider_id=payload.provider_id,
            model_id=payload.model_id,
            reasoning_effort=payload.reasoning_effort,
            sandbox_session_id=payload.sandbox_session_id,
            created_at=now,
            updated_at=now,
        )
        with self._repository_lock:
            self.chat_threads[thread.id] = thread.model_copy(deep=True)
        return thread

    def get_chat_thread(self, tenant_id: str, thread_id: str) -> ChatThread:
        thread = self.chat_threads.get(thread_id)
        if thread is None:
            raise NotFoundError(f"Chat thread not found: {thread_id}")
        if thread.tenant_id != tenant_id:
            raise TenantAccessError(f"Chat thread {thread_id} is not in tenant {tenant_id}")
        return thread.model_copy(deep=True)

    def list_chat_threads(
        self,
        tenant_id: str,
        workspace_id: str | None = None,
    ) -> list[ChatThread]:
        threads = [
            thread.model_copy(deep=True)
            for thread in self.chat_threads.values()
            if thread.tenant_id == tenant_id
            and (workspace_id is None or thread.workspace_id == workspace_id)
        ]
        return sorted(threads, key=lambda thread: (thread.updated_at, thread.id), reverse=True)

    def update_chat_thread(
        self,
        tenant_id: str,
        thread_id: str,
        **changes: Any,
    ) -> ChatThread:
        allowed_fields = {
            "title",
            "status",
            "pinned",
            "provider_id",
            "model_id",
            "reasoning_effort",
            "sandbox_session_id",
        }
        unknown_fields = set(changes) - allowed_fields
        if unknown_fields:
            raise ValueError(f"Unsupported chat thread fields: {sorted(unknown_fields)}")
        with self._repository_lock:
            thread = self.get_chat_thread(tenant_id, thread_id)
            updated = ChatThread.model_validate(
                {
                    **thread.model_dump(),
                    **changes,
                    "updated_at": utc_now(),
                }
            )
            self.chat_threads[thread_id] = updated.model_copy(deep=True)
        return updated

    def append_chat_message(
        self,
        tenant_id: str,
        thread_id: str,
        user_id: str | None,
        payload: ChatMessageCreate,
    ) -> ChatMessage:
        with self._repository_lock:
            thread = self.get_chat_thread(tenant_id, thread_id)
            sequence = max(
                (
                    message.sequence
                    for message in self.chat_messages.values()
                    if message.thread_id == thread_id
                ),
                default=0,
            ) + 1
            now = utc_now()
            message = ChatMessage(
                id=new_id("message"),
                tenant_id=tenant_id,
                workspace_id=thread.workspace_id,
                thread_id=thread_id,
                sequence=sequence,
                created_by_user_id=user_id,
                role=payload.role,
                content=payload.content,
                kind=payload.kind,
                dispatch_status=payload.dispatch_status,
                delivery_status=payload.delivery_status,
                attachments=payload.attachments,
                resource_refs=payload.resource_refs,
                created_at=now,
                updated_at=now,
            )
            self.chat_messages[message.id] = message.model_copy(deep=True)
        return message

    def get_chat_message(self, tenant_id: str, message_id: str) -> ChatMessage:
        message = self.chat_messages.get(message_id)
        if message is None:
            raise NotFoundError(f"Chat message not found: {message_id}")
        if message.tenant_id != tenant_id:
            raise TenantAccessError(f"Chat message {message_id} is not in tenant {tenant_id}")
        return message.model_copy(deep=True)

    def list_chat_messages(self, tenant_id: str, thread_id: str) -> list[ChatMessage]:
        self.get_chat_thread(tenant_id, thread_id)
        messages = [
            message.model_copy(deep=True)
            for message in self.chat_messages.values()
            if message.tenant_id == tenant_id and message.thread_id == thread_id
        ]
        return sorted(messages, key=lambda message: (message.sequence, message.id))

    def update_chat_message(
        self,
        tenant_id: str,
        message_id: str,
        **changes: Any,
    ) -> ChatMessage:
        allowed_fields = {"content", "dispatch_status", "delivery_status"}
        unknown_fields = set(changes) - allowed_fields
        if unknown_fields:
            raise ValueError(f"Unsupported chat message fields: {sorted(unknown_fields)}")
        with self._repository_lock:
            message = self.get_chat_message(tenant_id, message_id)
            updated = ChatMessage.model_validate(
                {
                    **message.model_dump(),
                    **changes,
                    "updated_at": utc_now(),
                }
            )
            self.chat_messages[message_id] = updated.model_copy(deep=True)
        return updated

    def claim_next_queued_message(
        self,
        tenant_id: str,
        thread_id: str,
    ) -> ChatMessage | None:
        with self._repository_lock:
            candidates = [
                message
                for message in self.list_chat_messages(tenant_id, thread_id)
                if message.dispatch_status
                in {
                    ChatMessageDispatchStatus.READY,
                    ChatMessageDispatchStatus.QUEUED,
                }
            ]
            if not candidates:
                return None
            return self.update_chat_message(
                tenant_id,
                candidates[0].id,
                dispatch_status=ChatMessageDispatchStatus.INFLIGHT,
            )

    def list_pending_steering_messages(
        self,
        tenant_id: str,
        thread_id: str,
    ) -> list[ChatMessage]:
        return [
            message
            for message in self.list_chat_messages(tenant_id, thread_id)
            if message.dispatch_status == ChatMessageDispatchStatus.STEERING
        ]

    def mark_steering_applied(self, tenant_id: str, message_id: str) -> ChatMessage:
        with self._repository_lock:
            message = self.get_chat_message(tenant_id, message_id)
            if message.dispatch_status != ChatMessageDispatchStatus.STEERING:
                raise ValueError(f"Chat message {message_id} is not pending steering")
            return self.update_chat_message(
                tenant_id,
                message_id,
                dispatch_status=ChatMessageDispatchStatus.COMPLETED,
            )

    def create_agent_cycle(self, cycle: AgentCycle) -> AgentCycle:
        run = self.get_run(cycle.tenant_id, cycle.run_id)
        if run.workspace_id != cycle.workspace_id:
            raise TenantAccessError(
                f"Run {run.id} is not in workspace {cycle.workspace_id}"
            )
        if run.thread_id != cycle.thread_id:
            raise ValueError(
                f"Agent cycle {cycle.id} thread does not match run {run.id}"
            )
        with self._repository_lock:
            if cycle.id in self.agent_cycles:
                raise ValueError(f"Agent cycle already exists: {cycle.id}")
            if any(
                existing.run_id == cycle.run_id
                and existing.iteration == cycle.iteration
                for existing in self.agent_cycles.values()
            ):
                raise ValueError(
                    f"Agent cycle iteration already exists: {cycle.run_id}:{cycle.iteration}"
                )
            self.agent_cycles[cycle.id] = cycle.model_copy(deep=True)
        return cycle.model_copy(deep=True)

    def complete_agent_cycle(
        self,
        tenant_id: str,
        cycle_id: str,
        *,
        status: str,
        verifier_result: AgentVerificationResult | None = None,
    ) -> AgentCycle:
        from taroai.agent.models import AgentCycle

        if status not in {"completed", "failed", "waiting"}:
            raise ValueError(f"Unsupported completed agent cycle status: {status}")
        with self._repository_lock:
            cycle = self._get_agent_cycle(tenant_id, cycle_id)
            updated = AgentCycle.model_validate(
                {
                    **cycle.model_dump(),
                    "status": status,
                    "verifier_result": (
                        verifier_result.model_dump() if verifier_result is not None else None
                    ),
                    "completed_at": utc_now() if status != "waiting" else None,
                }
            )
            self.agent_cycles[cycle_id] = updated.model_copy(deep=True)
        return updated

    def create_agent_action(self, action: AgentAction) -> AgentAction:
        cycle = self._get_agent_cycle(action.tenant_id, action.cycle_id)
        if cycle.run_id != action.run_id or cycle.workspace_id != action.workspace_id:
            raise TenantAccessError(f"Agent action {action.id} does not match its cycle")
        if cycle.thread_id != action.thread_id:
            raise ValueError(
                f"Agent action {action.id} thread does not match cycle {cycle.id}"
            )
        with self._repository_lock:
            if action.id in self.agent_actions:
                raise ValueError(f"Agent action already exists: {action.id}")
            if any(
                existing.run_id == action.run_id
                and existing.action_key == action.action_key
                for existing in self.agent_actions.values()
            ):
                raise ValueError(
                    f"Duplicate action_key for run {action.run_id}: {action.action_key}"
                )
            self.agent_actions[action.id] = action.model_copy(deep=True)
        return action.model_copy(deep=True)

    def get_agent_action(self, tenant_id: str, action_id: str) -> AgentAction:
        action = self.agent_actions.get(action_id)
        if action is None:
            raise NotFoundError(f"Agent action not found: {action_id}")
        if action.tenant_id != tenant_id:
            raise TenantAccessError(f"Agent action {action_id} is not in tenant {tenant_id}")
        return action.model_copy(deep=True)

    def commit_agent_action_observation(
        self,
        tenant_id: str,
        action_id: str,
        observation: AgentObservation,
        *,
        usage: dict[str, Any],
        state_payload: dict[str, Any],
        checksum: str,
        sandbox_checkpoint_ref: str | None = None,
    ) -> tuple[AgentAction, AgentCheckpoint]:
        from taroai.agent.models import AgentAction, AgentCheckpoint

        json.dumps(
            {
                "observation": observation.model_dump(mode="json"),
                "usage": usage,
                "state_payload": state_payload,
            }
        )
        with self._repository_lock:
            action = self.get_agent_action(tenant_id, action_id)
            if observation.action_id != action.id:
                raise ValueError("Observation action_id does not match the committed action")
            if action.status not in {"pending", "running", "uncertain"}:
                raise ValueError(
                    f"Agent action {action_id} observation is already committed"
                )
            checkpoint_sequence = self._next_checkpoint_sequence(action.run_id)
            completed_at = utc_now()
            updated_action = AgentAction.model_validate(
                {
                    **action.model_dump(),
                    "status": "succeeded" if observation.success else "failed",
                    "observation": observation.model_dump(),
                    "usage": usage,
                    "completed_at": completed_at,
                }
            )
            checkpoint = AgentCheckpoint(
                id=new_id("checkpoint"),
                tenant_id=action.tenant_id,
                workspace_id=action.workspace_id,
                thread_id=action.thread_id,
                run_id=action.run_id,
                cycle_id=action.cycle_id,
                sequence=checkpoint_sequence,
                last_committed_action_id=action.id,
                state_payload=state_payload,
                sandbox_checkpoint_ref=sandbox_checkpoint_ref,
                checksum=checksum,
                created_at=completed_at,
            )
            self.agent_actions[action_id] = updated_action.model_copy(deep=True)
            self.agent_checkpoints.setdefault(action.run_id, []).append(
                checkpoint.model_copy(deep=True)
            )
        return updated_action, checkpoint

    def create_agent_checkpoint(self, checkpoint: AgentCheckpoint) -> AgentCheckpoint:
        run = self.get_run(checkpoint.tenant_id, checkpoint.run_id)
        if run.workspace_id != checkpoint.workspace_id:
            raise TenantAccessError(
                f"Run {run.id} is not in workspace {checkpoint.workspace_id}"
            )
        if run.thread_id != checkpoint.thread_id:
            raise ValueError(
                f"Agent checkpoint {checkpoint.id} thread does not match run {run.id}"
            )
        if checkpoint.cycle_id is not None:
            cycle = self._get_agent_cycle(checkpoint.tenant_id, checkpoint.cycle_id)
            if cycle.run_id != checkpoint.run_id:
                raise ValueError(
                    f"Agent checkpoint {checkpoint.id} cycle does not match its run"
                )
        if checkpoint.last_committed_action_id is not None:
            action = self.get_agent_action(
                checkpoint.tenant_id,
                checkpoint.last_committed_action_id,
            )
            if action.run_id != checkpoint.run_id:
                raise ValueError(
                    f"Agent checkpoint {checkpoint.id} action does not match its run"
                )
        json.dumps(checkpoint.model_dump(mode="json"))
        with self._repository_lock:
            expected_sequence = self._next_checkpoint_sequence(checkpoint.run_id)
            if checkpoint.sequence != expected_sequence:
                raise ValueError(
                    "Agent checkpoint sequence must be the next checkpoint sequence "
                    f"({expected_sequence}), got {checkpoint.sequence}"
                )
            stored = checkpoint.model_copy(deep=True)
            self.agent_checkpoints.setdefault(checkpoint.run_id, []).append(stored)
        return checkpoint.model_copy(deep=True)

    def get_latest_agent_checkpoint(
        self,
        tenant_id: str,
        run_id: str,
    ) -> AgentCheckpoint | None:
        self.get_run(tenant_id, run_id)
        checkpoints = self.agent_checkpoints.get(run_id, [])
        if not checkpoints:
            return None
        return max(checkpoints, key=lambda item: item.sequence).model_copy(deep=True)

    def _get_agent_cycle(self, tenant_id: str, cycle_id: str) -> AgentCycle:
        cycle = self.agent_cycles.get(cycle_id)
        if cycle is None:
            raise NotFoundError(f"Agent cycle not found: {cycle_id}")
        if cycle.tenant_id != tenant_id:
            raise TenantAccessError(f"Agent cycle {cycle_id} is not in tenant {tenant_id}")
        return cycle.model_copy(deep=True)

    def _next_checkpoint_sequence(self, run_id: str) -> int:
        return max(
            (
                checkpoint.sequence
                for checkpoint in self.agent_checkpoints.get(run_id, [])
            ),
            default=0,
        ) + 1

    def get_run(self, tenant_id: str, run_id: str) -> Run:
        run = self.runs.get(run_id)
        if run is None:
            raise NotFoundError(f"Run not found: {run_id}")
        if run.tenant_id != tenant_id:
            raise TenantAccessError(f"Run {run_id} is not in tenant {tenant_id}")
        return run

    def get_idempotency_record(
        self,
        tenant_id: str,
        key: str,
        method: str,
        path: str,
    ) -> IdempotencyRecord | None:
        return self.idempotency_records.get(
            self._idempotency_record_key(tenant_id, key, method, path)
        )

    def save_idempotency_record(self, record: IdempotencyRecord) -> IdempotencyRecord:
        self.idempotency_records[
            self._idempotency_record_key(
                record.tenant_id,
                record.key,
                record.method,
                record.path,
            )
        ] = record
        return record

    def save_license_validation(
        self,
        validation: LicenseValidationResult,
    ) -> LicenseValidationResult:
        self.license_validations[validation.license.tenant_id] = validation.model_copy(deep=True)
        return validation

    def get_active_license_validation(
        self,
        tenant_id: str,
    ) -> LicenseValidationResult | None:
        validation = self.license_validations.get(tenant_id)
        if validation is None:
            return None
        return validation.model_copy(deep=True)

    def list_runs(
        self,
        tenant_id: str,
        workspace_id: str | None = None,
        status: RunStatus | None = None,
    ) -> list[Run]:
        return [
            run
            for run in self.runs.values()
            if run.tenant_id == tenant_id
            and (workspace_id is None or run.workspace_id == workspace_id)
            and (status is None or run.status == status)
        ]

    def list_run_events(
        self,
        tenant_id: str,
        run_id: str,
        after_sequence: int | None = None,
    ) -> list[RunEvent]:
        self.get_run(tenant_id, run_id)
        events = list(self.run_events.get(run_id, []))
        if after_sequence is None:
            return events
        return [event for event in events if event.sequence > after_sequence]

    def update_run_status(
        self,
        tenant_id: str,
        run_id: str,
        status: RunStatus,
        emit_status_event: bool = True,
    ) -> Run:
        run = self.get_run(tenant_id, run_id)
        updated_run = run.model_copy(update={"status": status, "updated_at": utc_now()})
        self.runs[run_id] = updated_run
        if emit_status_event:
            self.append_run_event(
                updated_run,
                "run.status_changed",
                {"status": status.value},
            )
        return updated_run

    def cancel_run(
        self,
        tenant_id: str,
        run_id: str,
        cancelled_by_user_id: str,
        reason_code: str,
    ) -> Run:
        run = self.get_run(tenant_id, run_id)
        if run.status in TERMINAL_RUN_STATUSES:
            raise RunTransitionError(f"Run {run_id} cannot be cancelled from {run.status.value}")
        cancelled_run = run.model_copy(
            update={"status": RunStatus.CANCELLED, "updated_at": utc_now()}
        )
        self.runs[run_id] = cancelled_run
        metadata = {
            "cancelled_by_user_id": cancelled_by_user_id,
            "reason_code": reason_code,
            "status": RunStatus.CANCELLED.value,
        }
        self._record_audit_event(
            tenant_id=tenant_id,
            workspace_id=run.workspace_id,
            user_id=cancelled_by_user_id,
            run_id=run_id,
            event_type="run.cancelled",
            metadata=metadata,
        )
        self._append_run_event(cancelled_run, "run.cancelled", metadata)
        return cancelled_run

    def request_run_retry(
        self,
        tenant_id: str,
        run_id: str,
        requested_by_user_id: str,
        reason_code: str,
    ) -> Run:
        run = self.get_run(tenant_id, run_id)
        if run.status not in RETRYABLE_RUN_STATUSES:
            raise RunTransitionError(f"Run {run_id} cannot be retried from {run.status.value}")
        retrying_run = run.model_copy(
            update={"status": RunStatus.RETRYING, "updated_at": utc_now()}
        )
        self.runs[run_id] = retrying_run
        metadata = {
            "requested_by_user_id": requested_by_user_id,
            "reason_code": reason_code,
            "previous_status": run.status.value,
            "status": RunStatus.RETRYING.value,
        }
        self._record_audit_event(
            tenant_id=tenant_id,
            workspace_id=run.workspace_id,
            user_id=requested_by_user_id,
            run_id=run_id,
            event_type="run.retry_requested",
            metadata=metadata,
        )
        self._append_run_event(retrying_run, "run.retry_requested", metadata)
        return retrying_run

    def append_run_event(self, run: Run, event_type: str, payload: dict) -> RunEvent:
        return self._append_run_event(run, event_type, payload)

    def create_artifact(
        self,
        tenant_id: str,
        run_id: str,
        name: str,
        artifact_type: str,
        uri: str,
    ) -> Artifact:
        run = self.get_run(tenant_id, run_id)
        artifact = Artifact(
            id=new_id("artifact"),
            tenant_id=tenant_id,
            run_id=run_id,
            name=name,
            artifact_type=artifact_type,
            uri=uri,
            created_at=utc_now(),
        )
        self.artifacts.setdefault(run_id, []).append(artifact)
        self._append_run_event(
            run,
            "artifact.created",
            {"artifact_id": artifact.id, "name": artifact.name, "type": artifact.artifact_type},
        )
        return artifact

    def list_artifacts(self, tenant_id: str, run_id: str) -> list[Artifact]:
        self.get_run(tenant_id, run_id)
        return list(self.artifacts.get(run_id, []))

    def create_approval_request(
        self,
        tenant_id: str,
        run_id: str,
        step_id: str,
        reason: str,
    ) -> ApprovalRequest:
        run = self.get_run(tenant_id, run_id)
        approval = ApprovalRequest(
            id=new_id("approval"),
            tenant_id=tenant_id,
            workspace_id=run.workspace_id,
            run_id=run_id,
            step_id=step_id,
            reason=reason,
            status=ApprovalStatus.PENDING,
            requested_by_user_id=run.user_id,
            created_at=utc_now(),
        )
        self.approval_requests.setdefault(run_id, []).append(approval)
        self._append_run_event(
            run,
            "approval.requested",
            {"approval_id": approval.id, "step_id": step_id, "reason": reason},
        )
        return approval

    def resolve_approval_request(
        self,
        tenant_id: str,
        run_id: str,
        approval_id: str,
        approved_by_user_id: str,
    ) -> ApprovalRequest:
        return self._complete_approval_request(
            tenant_id=tenant_id,
            run_id=run_id,
            approval_id=approval_id,
            status=ApprovalStatus.APPROVED,
            resolved_by_user_id=approved_by_user_id,
            event_type="approval.resolved",
        )

    def reject_approval_request(
        self,
        tenant_id: str,
        run_id: str,
        approval_id: str,
        rejected_by_user_id: str,
    ) -> ApprovalRequest:
        return self._complete_approval_request(
            tenant_id=tenant_id,
            run_id=run_id,
            approval_id=approval_id,
            status=ApprovalStatus.REJECTED,
            resolved_by_user_id=rejected_by_user_id,
            event_type="approval.rejected",
        )

    def cancel_pending_approval_requests(
        self,
        tenant_id: str,
        run_id: str,
        cancelled_by_user_id: str,
    ) -> list[ApprovalRequest]:
        run = self.get_run(tenant_id, run_id)
        approvals = self.approval_requests.get(run_id, [])
        cancelled: list[ApprovalRequest] = []
        for index, approval in enumerate(approvals):
            if approval.status != ApprovalStatus.PENDING:
                continue
            resolved = approval.model_copy(
                update={
                    "status": ApprovalStatus.CANCELLED,
                    "resolved_by_user_id": cancelled_by_user_id,
                    "resolved_at": utc_now(),
                }
            )
            approvals[index] = resolved
            metadata = {
                "approval_id": approval.id,
                "status": ApprovalStatus.CANCELLED.value,
                "resolved_by_user_id": cancelled_by_user_id,
            }
            self._record_audit_event(
                tenant_id=tenant_id,
                workspace_id=run.workspace_id,
                user_id=cancelled_by_user_id,
                run_id=run_id,
                event_type="approval.cancelled",
                metadata=metadata,
            )
            self._append_run_event(run, "approval.cancelled", metadata)
            cancelled.append(resolved)
        return cancelled

    def _complete_approval_request(
        self,
        tenant_id: str,
        run_id: str,
        approval_id: str,
        status: ApprovalStatus,
        resolved_by_user_id: str,
        event_type: str,
    ) -> ApprovalRequest:
        run = self.get_run(tenant_id, run_id)
        approvals = self.approval_requests.get(run_id, [])
        for index, approval in enumerate(approvals):
            if approval.id == approval_id:
                resolved = approval.model_copy(
                    update={
                        "status": status,
                        "resolved_by_user_id": resolved_by_user_id,
                        "resolved_at": utc_now(),
                    }
                )
                approvals[index] = resolved
                self._append_run_event(
                    run,
                    event_type,
                    {
                        "approval_id": approval_id,
                        "status": status.value,
                        "resolved_by_user_id": resolved_by_user_id,
                    },
                )
                self._record_audit_event(
                    tenant_id=tenant_id,
                    workspace_id=run.workspace_id,
                    user_id=resolved_by_user_id,
                    run_id=run_id,
                    event_type=event_type,
                    metadata={
                        "approval_id": approval_id,
                        "status": status.value,
                        "resolved_by_user_id": resolved_by_user_id,
                    },
                )
                return resolved
        raise NotFoundError(f"Approval request not found: {approval_id}")

    def list_approval_requests(self, tenant_id: str, run_id: str) -> list[ApprovalRequest]:
        self.get_run(tenant_id, run_id)
        return list(self.approval_requests.get(run_id, []))

    def save_runtime_state(self, state: Any) -> RunStateSnapshot:
        self.get_run(state.tenant_id, state.run_id)
        snapshot = RunStateSnapshot.from_runtime_state(state)
        self.runtime_states[state.run_id] = snapshot
        return snapshot

    def get_runtime_state(self, tenant_id: str, run_id: str) -> RunStateSnapshot:
        self.get_run(tenant_id, run_id)
        snapshot = self.runtime_states.get(run_id)
        if snapshot is None:
            raise NotFoundError(f"Runtime state not found: {run_id}")
        return snapshot

    def list_billing_meters(self, tenant_id: str) -> list[BillingMeterEvent]:
        return list(self.billing_meters.get(tenant_id, []))

    def list_audit_events(self, tenant_id: str) -> list[AuditEvent]:
        return list(self.audit_events.get(tenant_id, []))

    def record_billing_meter(
        self,
        tenant_id: str,
        run_id: str | None,
        meter_type: str,
        quantity: float,
        unit: str,
        metadata: dict[str, Any] | None = None,
        skill_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        cost_estimate: float | None = None,
        workspace_id: str | None = None,
        user_id: str | None = None,
        agent_id: str | None = None,
    ) -> BillingMeterEvent:
        run = self.get_run(tenant_id, run_id) if run_id is not None else None
        resolved_workspace_id = run.workspace_id if run is not None else workspace_id
        resolved_user_id = run.user_id if run is not None else user_id
        resolved_agent_id = run.agent_id if run is not None else agent_id
        if resolved_workspace_id is None or resolved_user_id is None:
            raise ValueError("workspace_id and user_id are required when run_id is not provided")
        meter = BillingMeterEvent(
            id=new_id("meter"),
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            user_id=resolved_user_id,
            run_id=run.id if run is not None else None,
            agent_id=resolved_agent_id,
            skill_id=skill_id,
            meter_type=meter_type,
            quantity=quantity,
            unit=unit,
            provider=provider,
            model=model,
            cost_estimate=cost_estimate,
            metadata=metadata or {},
            created_at=utc_now(),
        )
        self.billing_meters.setdefault(meter.tenant_id, []).append(meter)
        if run is not None:
            self._append_run_event(
                run,
                "billing.metered",
                {"meter_id": meter.id, "type": meter.meter_type},
            )
        self._record_billing_audit_event(meter)
        return meter

    def record_audit_event(
        self,
        tenant_id: str,
        workspace_id: str | None,
        user_id: str | None,
        run_id: str | None,
        event_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> AuditEvent:
        if run_id is not None:
            self.get_run(tenant_id, run_id)
        return self._record_audit_event(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            run_id=run_id,
            event_type=event_type,
            metadata=metadata or {},
        )

    def _append_run_event(self, run: Run, event_type: str, payload: dict) -> RunEvent:
        with self._repository_lock:
            sequence = len(self.run_events.get(run.id, [])) + 1
            thread_sequence: int | None = None
            if run.thread_id is not None:
                thread_sequence = max(
                    (
                        event.thread_sequence or 0
                        for events in self.run_events.values()
                        for event in events
                        if event.tenant_id == run.tenant_id
                        and event.thread_id == run.thread_id
                    ),
                    default=0,
                ) + 1
            event = RunEvent(
                id=new_id("event"),
                sequence=sequence,
                tenant_id=run.tenant_id,
                workspace_id=run.workspace_id,
                run_id=run.id,
                type=event_type,
                payload=payload,
                created_at=utc_now(),
                thread_id=run.thread_id,
                thread_sequence=thread_sequence,
            )
            self.run_events.setdefault(run.id, []).append(event)
        return event

    def _idempotency_record_key(
        self,
        tenant_id: str,
        key: str,
        method: str,
        path: str,
    ) -> str:
        return json.dumps([tenant_id, key, method, path], separators=(",", ":"))

    def _record_run_meter(self, run: Run) -> BillingMeterEvent:
        meter = BillingMeterEvent(
            id=new_id("meter"),
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            user_id=run.user_id,
            run_id=run.id,
            agent_id=run.agent_id,
            meter_type="run_count",
            quantity=1,
            unit="run",
            metadata={"mode": run.mode.value},
            created_at=utc_now(),
        )
        self.billing_meters.setdefault(run.tenant_id, []).append(meter)
        self._append_run_event(run, "billing.metered", {"meter_id": meter.id, "type": meter.meter_type})
        self._record_billing_audit_event(meter)
        return meter

    def _record_billing_audit_event(self, meter: BillingMeterEvent) -> AuditEvent:
        return self._record_audit_event(
            tenant_id=meter.tenant_id,
            workspace_id=meter.workspace_id,
            user_id=meter.user_id,
            run_id=meter.run_id,
            event_type="billing.metered",
            metadata=self._billing_audit_metadata(meter),
        )

    def _billing_audit_metadata(self, meter: BillingMeterEvent) -> dict[str, Any]:
        return {
            "meter_id": meter.id,
            "meter_type": meter.meter_type,
            "quantity": meter.quantity,
            "unit": meter.unit,
            "skill_id": meter.skill_id,
            "provider": meter.provider,
            "model": meter.model,
            "cost_estimate": meter.cost_estimate,
        }

    def _record_audit_event(
        self,
        tenant_id: str,
        workspace_id: str | None,
        user_id: str | None,
        run_id: str | None,
        event_type: str,
        metadata: dict,
    ) -> AuditEvent:
        audit_event = AuditEvent(
            id=new_id("audit"),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            run_id=run_id,
            event_type=event_type,
            metadata=metadata,
            created_at=utc_now(),
        )
        self.audit_events.setdefault(tenant_id, []).append(audit_event)
        if run_id is not None:
            run = self.runs[run_id]
            self._append_run_event(run, "audit.recorded", {"audit_event_id": audit_event.id})
        return audit_event
