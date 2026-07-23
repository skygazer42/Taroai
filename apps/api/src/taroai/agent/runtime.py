import base64
import hashlib
import json
import math
import re
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from taroai.agent.exceptions import (
    _RuntimeGuardrailApprovalRequired,
    _RuntimeGuardrailViolation,
    _RuntimeSandboxArtifactPathRejected,
    _RuntimeStorageContentRejected,
)
from taroai.agent.graph import build_runtime_graph
from taroai.agent.loop import AgentExecutionServices
from taroai.agent_engines import AgentEngineSessionCreate
from taroai.coding_workspaces import CodingWorkspaceCreate
from taroai.agent.planning import PlanStep
from taroai.agent.state import AgentRetrievedContext, AgentRuntimeState
from taroai.audit import AuditActor, AuditEventCreate, AuditService
from taroai.billing import BillingPricingService
from taroai.db import SqlControlPlaneRepository
from taroai.embeddings import (
    EmbeddingGateway,
    EmbeddingGatewayError,
    EmbeddingGatewayRequest,
    EmbeddingGatewayResponse,
    EmbeddingUsageRecord,
    EmbeddingUsageRecorder,
)
from taroai.guardrails.models import (
    GuardrailAction,
    GuardrailDecision,
    GuardrailEvaluationRequest,
    GuardrailStage,
)
from taroai.knowledge import (
    RetrievalRequest,
    RetrievalResult,
    cosine_similarity,
    retrieval_terms,
    term_relevance,
)
from taroai.licensing import LicenseEntitlementDeniedError, LicensedFeature
from taroai.memory import MemoryScopeType
from taroai.tool_gateway import (
    ToolExecutionError,
    ToolGateway,
    ToolResult,
)
from taroai.domain import ApprovalStatus, Run, RunMode, RunStatus, new_id, utc_now
from taroai.model_gateway import (
    ModelBudgetExceededError,
    ModelBudgetGuard,
    ModelGatewayError,
    ModelGateway,
    ModelGatewayRequest,
    ModelGatewayResponse,
    ModelSafetyRefusalError,
    ModelMessage,
    ModelPolicy,
    ModelPolicyDeniedError,
    OpenAICompatibleModelGateway,
    PlannedToolCall,
)
from taroai.policy import PolicyDecision, PolicyRequest, PolicyService
from taroai.sandbox import (
    BrowserController,
    SandboxAdapter,
    SandboxCreateRequest,
    SandboxExecutionError,
    SandboxFileWrite,
    SandboxNetworkMode,
    SandboxProviderUnavailableError,
    SandboxSessionStatus,
)
from taroai.storage import ObjectStorageAdapter, StorageObjectCreate, StoragePurpose
from taroai.storage import StorageContentScanner, StorageContentScanRequest
from taroai.store import InMemoryControlPlaneStore, NotFoundError, TERMINAL_RUN_STATUSES
from taroai.workflow import workflow_goal, workflow_spec_from_plan


_MEMORY_CONTEXT_LIMIT = 12
_MEMORY_EMBEDDING_CANDIDATE_LIMIT = 63
_MEMORY_SEMANTIC_RELEVANCE_THRESHOLD = 0.6
_DEFAULT_MEMORY_HALF_LIFE_DAYS = 30.0
_ALWAYS_APPLY_USER_MEMORY_KEYS = {
    "profile.answer_format",
    "profile.language",
    "profile.response_style",
    "profile.tone",
}


def _split_platform_context(message: str) -> tuple[str | None, str]:
    prefix = "[Platform context: "
    if not message.startswith(prefix):
        return None, message
    context, separator, request = message.partition("]\n\n")
    if not separator or not request.strip():
        return None, message
    return f"{context}]", request


class AgentRuntime(BaseModel):
    store: InMemoryControlPlaneStore | SqlControlPlaneRepository
    model_gateway: ModelGateway = Field(default_factory=OpenAICompatibleModelGateway)
    model_policy: ModelPolicy = Field(default_factory=ModelPolicy)
    model_budget_guard: ModelBudgetGuard = Field(default_factory=ModelBudgetGuard)
    tool_gateway: ToolGateway = Field(default_factory=ToolGateway)
    policy_service: PolicyService | None = None
    audit_service: Any | None = None
    license_service: Any | None = None
    knowledge_service: Any | None = None
    sandbox_adapter: SandboxAdapter | None = None
    browser_controller: BrowserController | None = None
    storage_catalog: Any | None = None
    object_storage: ObjectStorageAdapter | None = None
    storage_content_scanner: StorageContentScanner | None = None
    sandbox_runtime_image: str = "python:3.12-slim"
    sandbox_network_mode: SandboxNetworkMode = SandboxNetworkMode.DISABLED
    sandbox_timeout_seconds: int = 300
    sandbox_artifact_max_bytes: int = Field(default=25_000_000, ge=1)
    sandbox_destroy_on_success: bool = True
    embedding_gateway: EmbeddingGateway | None = None
    billing_pricing_service: BillingPricingService = Field(
        default_factory=BillingPricingService
    )
    long_term_memory_service: Any | None = None
    guardrail_service: Any | None = None
    skill_service: Any | None = None
    connector_registry: Any | None = None
    connector_dispatcher: Any | None = None
    connector_invocation_service: Any | None = None
    agent_registry: Any | None = None
    browser_profile_service: Any | None = None
    agent_engine_service: Any | None = None
    coding_workspace_service: Any | None = None
    max_step_retries: int = 0
    loop_max_iterations: int = Field(default=12, ge=1)
    loop_max_repairs: int = Field(default=4, ge=0)
    loop_timeout_seconds: int = Field(default=1800, ge=1)
    loop_cost_limit: float = Field(default=0, ge=0)
    loop_action_lease_seconds: int = Field(default=600, ge=1)
    loop_worker_id: str = "agent-loop-v2"
    full_auto_requires_isolation: bool = True
    pending_states: dict[str, AgentRuntimeState] = Field(default_factory=dict)
    model_plan_metadata: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        exclude=True,
    )

    def build_graph(self):
        return build_runtime_graph(self)

    def execute_run(self, tenant_id: str, run_id: str) -> AgentRuntimeState:
        run = self.store.get_run(tenant_id, run_id)
        execution = AgentExecutionServices(self)
        state = execution._restore_state(run)
        if run.mode == RunMode.WORKFLOW:
            return self._execute_workflow_preview(run, state)
        if (
            run.mode == RunMode.CHAT
            and run.thread_id is not None
            and run.trigger_message_id is not None
            and run.agent_id is None
            and not run.attachments
            and not run.resource_refs
            and not state.runtime_metadata.get("stream_chat_tool_loop")
        ):
            # ponytail: 普通聊天失败时整轮重试；需要断点恢复时使用 Agent 模式。
            return execution.execute_chat(state, run)
        snapshot = self._agent_runtime_snapshot(state)
        if str(snapshot.get("engine_type") or "native") != "native":
            return self._execute_external_engine(run, state, snapshot)
        result = self.build_graph().compile().invoke(
            state,
            config={"recursion_limit": self.loop_max_iterations * 8 + 32},
        )
        return AgentRuntimeState.model_validate(result)

    def _execute_workflow_preview(
        self, run: Run, state: AgentRuntimeState
    ) -> AgentRuntimeState:
        existing = self.store.get_workflow_for_parent_run(run.tenant_id, run.id)
        if existing is not None:
            state.status = {
                "awaiting_approval": RunStatus.AWAITING_APPROVAL,
                "running": RunStatus.RUNNING,
                "paused": RunStatus.WAITING_FOR_USER,
                "succeeded": RunStatus.SUCCEEDED,
                "failed": RunStatus.FAILED,
                "cancelled": RunStatus.CANCELLED,
            }[existing.status]
            self._save_state(state)
            return state

        if (
            not state.retrieved_context.knowledge_results
            and not state.retrieved_context.memory_records
        ):
            state.retrieved_context = self._load_context(run)
            self.store.append_run_event(
                run,
                "context.loaded",
                self._context_event_payload(state.retrieved_context),
            )
        plan = self._create_plan(run, state.retrieved_context)
        if not plan:
            raise ValueError("workflow planner returned no tasks")
        state.plan = plan
        spec = workflow_spec_from_plan(workflow_goal(self.store, run), plan)
        workflow = self.store.create_workflow(run, spec)
        self.store.append_run_event(
            run,
            "plan.created",
            self._plan_created_event_payload(state),
        )
        self.store.append_run_event(
            run,
            "workflow_preview",
            {
                "workflowId": workflow.id,
                "previewId": workflow.id,
                "status": "pending",
                "spec": spec.model_dump(mode="json", by_alias=True),
            },
        )
        approval = self.store.create_approval_request(
            run.tenant_id,
            run.id,
            f"workflow:{workflow.id}",
            f"Approve workflow: {len([task for phase in spec.phases for task in phase.tasks])} steps",
            kind="workflow",
            subject_type="workflow",
            subject_id=workflow.id,
            preview_payload=spec.model_dump(mode="json", by_alias=True),
            validation_payload={"valid": True},
        )
        self.store.update_workflow(
            run.tenant_id, workflow.id, approval_id=approval.id
        )
        self.store.update_run_status(
            run.tenant_id, run.id, RunStatus.AWAITING_APPROVAL
        )
        state.status = RunStatus.AWAITING_APPROVAL
        state.approval_id = approval.id
        state.runtime_metadata.update(
            {"workflow_id": workflow.id, "workflow_preview_id": workflow.id}
        )
        self.pending_states[run.id] = state
        self._save_state(state)
        return state

    def _execute_external_engine(
        self,
        run: Run,
        state: AgentRuntimeState,
        snapshot: dict[str, Any],
    ) -> AgentRuntimeState:
        agent_engine_service = self.agent_engine_service
        if agent_engine_service is None:
            raise ToolExecutionError("Agent Engine service is not configured")
        existing_session_id = state.runtime_metadata.get("engine_session_id")
        if existing_session_id:
            events = agent_engine_service.refresh_events(
                run.tenant_id,
                str(existing_session_id),
            )
            session = agent_engine_service.registry.get_session(
                run.tenant_id,
                str(existing_session_id),
            )
            state.runtime_metadata["engine_event_count"] = len(events)
        else:
            self.store.update_run_status(run.tenant_id, run.id, RunStatus.RUNNING)
            self._save_state(state)
            runtime_policy_decision = self._decide_runtime_execution(run)
            if not runtime_policy_decision.allowed:
                return self._fail_for_policy_block(
                    state, run, runtime_policy_decision
                )
            coding_workspace = self._ensure_coding_workspace(run, state)
            session = agent_engine_service.start_session(
                run.tenant_id,
                run.user_id,
                AgentEngineSessionCreate(
                    workspace_id=run.workspace_id,
                    connection_id=str(snapshot["engine_connection_id"]),
                    run_id=run.id,
                    task=run.message,
                    cwd=str(snapshot.get("cwd") or "/workspace"),
                    metadata={
                        "agent_id": run.agent_id,
                        "engine_type": snapshot.get("engine_type"),
                        "coding_workspace": coding_workspace.model_dump(mode="json") if coding_workspace is not None else None,
                    },
                ),
            )
            if coding_workspace is not None:
                coding_workspace = coding_workspace.model_copy(update={"engine_session_id": session.id, "updated_at": utc_now()})
                coding_workspace_service = self.coding_workspace_service
                if coding_workspace_service is not None:
                    coding_workspace_service.registry.save_workspace(coding_workspace)
            state.runtime_metadata.update(
                {
                    "engine_session_id": session.id,
                    "engine_connection_id": session.connection_id,
                    "engine_type": session.engine_type.value,
                }
            )
        status_map = {
            "completed": RunStatus.SUCCEEDED,
            "failed": RunStatus.FAILED,
            "cancelled": RunStatus.CANCELLED,
        }
        state.status = status_map.get(session.status, RunStatus.RUNNING)
        self._save_state(state)
        return state

    def _ensure_coding_workspace(self, run: Run, state: AgentRuntimeState):
        if self.coding_workspace_service is None:
            return None
        repository_refs = [item for item in run.resource_refs if item.type == "repository"]
        snapshot_repository_id = self._agent_runtime_snapshot(state).get("repository_id")
        repository_id = repository_refs[0].id if repository_refs else snapshot_repository_id
        if not repository_id:
            return None
        for item in self.coding_workspace_service.registry.list_workspaces(run.tenant_id, run.workspace_id):
            if item.run_id == run.id:
                state.runtime_metadata["coding_workspace_id"] = item.id
                return item
        item = self.coding_workspace_service.create_workspace(
            run.tenant_id,
            run.user_id,
            CodingWorkspaceCreate(
                workspace_id=run.workspace_id,
                repository_id=str(repository_id),
                run_id=run.id,
                branch=str(self._agent_runtime_snapshot(state).get("branch") or f"taroai/{run.id}"),
            ),
        )
        state.runtime_metadata["coding_workspace_id"] = item.id
        self.store.append_run_event(run, "coding.workspace.created", {"coding_workspace_id": item.id, "repository_id": item.repository_id, "branch": item.branch, "worktree_path": item.worktree_path})
        return item

    def resume_after_approval(
        self,
        tenant_id: str,
        run_id: str,
        approval_id: str,
        approved_by_user_id: str,
    ) -> AgentRuntimeState:
        state = self.pending_states.pop(run_id, None)
        if state is None:
            state = self._load_state(tenant_id, run_id)
        run = self.store.get_run(tenant_id, run_id)
        workflow = self.store.get_workflow_for_parent_run(tenant_id, run_id)
        approval = next(
            (
                item
                for item in self.store.list_approval_requests(tenant_id, run_id)
                if item.id == approval_id
            ),
            None,
        )
        if approval is None:
            raise NotFoundError(f"Approval request not found: {approval_id}")
        if workflow is not None and workflow.approval_id != approval_id:
            raise ValueError("approval does not match workflow preview")
        if workflow is None and state.approval_id != approval_id:
            raise ValueError("approval does not match the paused run")
        if approval.status == ApprovalStatus.PENDING:
            self.store.resolve_approval_request(
                tenant_id=tenant_id,
                run_id=run_id,
                approval_id=approval_id,
                approved_by_user_id=approved_by_user_id,
            )
        elif approval.status != ApprovalStatus.APPROVED:
            raise ValueError("approval is not approved")
        if workflow is not None:
            self.store.update_workflow(tenant_id, workflow.id, status="running")
            self.store.update_approval_execution(
                tenant_id, run_id, approval_id, "applying"
            )
            self.store.update_run_status(
                tenant_id, run_id, RunStatus.RUNNING, emit_status_event=False
            )
            self.store.append_run_event(
                run,
                "workflow.started",
                {
                    "workflowId": workflow.id,
                    "taskCount": len(self.store.list_workflow_tasks(tenant_id, workflow.id)),
                },
            )
            self.store.append_run_event(
                run,
                "workflow_started",
                {
                    "previewId": workflow.id,
                    "stepCount": len(
                        self.store.list_workflow_tasks(tenant_id, workflow.id)
                    ),
                },
            )
            state.status = RunStatus.RUNNING
            state.approval_id = None
            self._save_state(state)
            return state
        self.store.update_approval_execution(
            tenant_id, run_id, approval_id, "applying"
        )
        self.store.update_run_status(
            tenant_id, run_id, RunStatus.RUNNING, emit_status_event=False
        )
        state.status = RunStatus.RUNNING
        if state.runtime_metadata.pop("workflow_preview_pending", False):
            state.runtime_metadata["workflow_approved"] = True
            self.store.append_run_event(
                run,
                "workflow_started",
                {
                    "previewId": state.runtime_metadata.get("workflow_preview_id"),
                    "stepCount": state.runtime_metadata.get("workflow_step_count", 0),
                },
            )
        if (
            state.current_step_id is not None
            and state.current_step_id not in state.approved_step_ids
        ):
            state.approved_step_ids.append(state.current_step_id)
        if state.pending_guardrail_approval_key is not None:
            if (
                state.pending_guardrail_approval_key
                not in state.approved_guardrail_keys
            ):
                state.approved_guardrail_keys.append(
                    state.pending_guardrail_approval_key
                )
            pending_guardrail_stage = state.pending_guardrail_approval_stage
            state.pending_guardrail_approval_key = None
            state.pending_guardrail_approval_stage = None
            state.approval_id = None
            self.store.update_approval_execution(
                tenant_id, run_id, approval_id, "applied"
            )
            self._save_state(state)
            if pending_guardrail_stage == GuardrailStage.ARTIFACT.value:
                if self._has_pending_sandbox_artifact_promotion(state):
                    finalized = (
                        self._resume_sandbox_artifact_promotion_after_guardrail_approval(
                            state
                        )
                    )
                    execution = AgentExecutionServices(self)
                    if finalized.status == RunStatus.SUCCEEDED:
                        execution._complete_trigger_message(run, succeeded=True)
                        execution._emit_terminal_once(
                            finalized,
                            run,
                            "agent.loop.completed",
                            {"outcome": "complete", "iterations": finalized.iteration},
                        )
                        self._save_state(finalized)
                    return finalized
                return AgentExecutionServices(self)._finalize(state, run)
        state.runtime_metadata["active_approval_execution"] = {
            "approval_id": approval_id,
            "step_id": state.current_step_id,
        }
        state.approval_id = None
        state.status = RunStatus.RUNNING
        self._save_state(state)
        return self.execute_run(tenant_id, run_id)

    def reject_approval(
        self,
        tenant_id: str,
        run_id: str,
        approval_id: str,
        rejected_by_user_id: str,
    ) -> AgentRuntimeState:
        state = self.pending_states.pop(run_id, None)
        if state is None:
            state = self._load_state(tenant_id, run_id)
        workflow = self.store.get_workflow_for_parent_run(tenant_id, run_id)
        approval = next(
            (
                item
                for item in self.store.list_approval_requests(tenant_id, run_id)
                if item.id == approval_id
            ),
            None,
        )
        if approval is None:
            raise NotFoundError(f"Approval request not found: {approval_id}")
        if workflow is not None and workflow.approval_id != approval_id:
            raise ValueError("approval does not match workflow preview")
        if approval.status == ApprovalStatus.REJECTED:
            return state
        if approval.status != ApprovalStatus.PENDING:
            raise ValueError("approval is not pending")
        if workflow is None and state.approval_id != approval_id:
            raise ValueError("approval does not match the paused run")
        self.store.reject_approval_request(
            tenant_id=tenant_id,
            run_id=run_id,
            approval_id=approval_id,
            rejected_by_user_id=rejected_by_user_id,
        )
        run = self.store.cancel_run(
            tenant_id=tenant_id,
            run_id=run_id,
            cancelled_by_user_id=rejected_by_user_id,
            reason_code="approval_rejected",
        )
        state.status = RunStatus.CANCELLED
        state.approval_id = None
        state.pending_guardrail_approval_key = None
        state.pending_guardrail_approval_stage = None
        state.failure_reason = "Approval rejected"
        if workflow is not None:
            self.store.update_workflow(
                tenant_id, workflow.id, status="cancelled", completed_at=utc_now()
            )
            self.store.append_run_event(
                run,
                "workflow.cancelled",
                {"workflowId": workflow.id, "reason": "approval_rejected"},
            )
        self._save_state(state)
        execution = AgentExecutionServices(self)
        execution._complete_trigger_message(run, succeeded=False, cancelled=True)
        execution._emit_terminal_once(
            state,
            run,
            "agent.loop.completed",
            {"outcome": "cancelled", "reason": "approval_rejected"},
        )
        self._save_state(state)
        self._destroy_runtime_sandbox_session(
            state,
            reason="approval_rejected",
            force=True,
        )
        self._destroy_runtime_browser_session(state, reason="approval_rejected")
        return state

    def cancel_run(
        self,
        tenant_id: str,
        run_id: str,
        cancelled_by_user_id: str,
        reason_code: str,
    ) -> Run:
        self.store.cancel_pending_approval_requests(
            tenant_id=tenant_id,
            run_id=run_id,
            cancelled_by_user_id=cancelled_by_user_id,
        )
        run = self.store.cancel_run(
            tenant_id=tenant_id,
            run_id=run_id,
            cancelled_by_user_id=cancelled_by_user_id,
            reason_code=reason_code,
        )
        state = self.pending_states.pop(run_id, None)
        if state is None:
            state = self._load_or_initial_state(run)
        active_step_id = state.current_step_id
        state.status = RunStatus.CANCELLED
        state.approval_id = None
        state.pending_guardrail_approval_key = None
        state.pending_guardrail_approval_stage = None
        state.failure_reason = "Run cancelled"
        if state.current_action_id is not None:
            self.store.cancel_agent_action(tenant_id, state.current_action_id)
        state.current_step_id = None
        self._save_state(state)
        sandbox_adapter = self.sandbox_adapter
        command_cancel_attempted = (
            sandbox_adapter is not None
            and state.sandbox_session_id is not None
            and active_step_id is not None
        )
        cancel_metadata = {
            "session_id": state.sandbox_session_id,
            "command_id": active_step_id,
        }
        if command_cancel_attempted:
            self.store.append_run_event(
                run,
                "sandbox.command.cancel_requested",
                cancel_metadata,
            )
        execution = AgentExecutionServices(self)
        execution.checkpoint_cancel(state, run)
        execution._complete_trigger_message(
            run,
            succeeded=False,
            cancelled=True,
        )
        execution._emit_terminal_once(
            state,
            run,
            "agent.loop.completed",
            {"outcome": "cancelled", "reason": reason_code},
        )
        self._save_state(state)
        command_cancelled = False
        if command_cancel_attempted:
            assert sandbox_adapter is not None
            assert state.sandbox_session_id is not None
            assert active_step_id is not None
            try:
                command_cancelled = sandbox_adapter.cancel_command(
                    state.tenant_id,
                    state.sandbox_session_id,
                    active_step_id,
                )
            except Exception:
                pass
            self.store.append_run_event(
                run,
                (
                    "sandbox.command.cancelled"
                    if command_cancelled
                    else "sandbox.command.cancel_failed"
                ),
                cancel_metadata,
            )
        preserved_thread_session = command_cancelled and self._pause_thread_sandbox_session(
            state, run
        )
        if not preserved_thread_session:
            self._destroy_runtime_sandbox_session(
                state,
                reason="cancelled",
                force=True,
            )
        self._destroy_runtime_browser_session(state, reason="cancelled")
        return run

    def request_run_retry(
        self,
        tenant_id: str,
        run_id: str,
        requested_by_user_id: str,
        reason_code: str,
    ) -> Run:
        self.pending_states.pop(run_id, None)
        run = self.store.request_run_retry(
            tenant_id=tenant_id,
            run_id=run_id,
            requested_by_user_id=requested_by_user_id,
            reason_code=reason_code,
        )
        self.store.cancel_pending_approval_requests(
            tenant_id=tenant_id,
            run_id=run_id,
            cancelled_by_user_id=requested_by_user_id,
        )
        execution = AgentExecutionServices(self)
        state = execution._restore_state(run)
        current_action = (
            self.store.get_agent_action(tenant_id, state.current_action_id)
            if state.current_action_id is not None
            else None
        )
        if (
            current_action is not None
            and not (
                current_action.observation is not None
                and current_action.observation.success
            )
            and current_action.decision not in state.pending_actions
        ):
            state.pending_actions.insert(0, current_action.decision)
            retry_step_id = execution._decision_step(current_action).id
            state.approved_step_ids = [
                step_id
                for step_id in state.approved_step_ids
                if step_id != retry_step_id
            ]
        if state.pending_actions:
            state.runtime_metadata["prefetched_action"] = True
        else:
            state.runtime_metadata.pop("prefetched_action", None)
        state.status = RunStatus.RUNNING
        state.max_iterations = state.iteration + self.loop_max_iterations
        state.repair_attempts = 0
        state.replan_count = 0
        state.failure_reason = None
        state.waiting_reason = None
        state.pending_uncertain_action_id = None
        state.current_action_id = None
        state.current_cycle_id = None
        state.current_step_id = None
        state.approval_id = None
        state.pending_guardrail_approval_key = None
        state.pending_guardrail_approval_stage = None
        state.terminal_event_emitted = False
        state.graph_failure_code = None
        state.graph_failure_detail = None
        state.graph_failure_metadata = {}
        state.runtime_metadata.pop("active_approval_execution", None)
        state.runtime_metadata["execution_attempt"] = (
            int(state.runtime_metadata.get("execution_attempt", 0)) + 1
        )
        state.runtime_metadata["attempt_start_iteration"] = state.iteration
        state.deadline_at = utc_now() + timedelta(seconds=self.loop_timeout_seconds)
        execution._persist_checkpoint(state, run)
        if run.trigger_message_id is not None:
            from taroai.domain import (
                ChatMessageDeliveryStatus,
                ChatMessageDispatchStatus,
            )

            self.store.update_chat_message(
                tenant_id,
                run.trigger_message_id,
                dispatch_status=ChatMessageDispatchStatus.INFLIGHT,
                delivery_status=ChatMessageDeliveryStatus.PENDING,
            )
        return run

    def retry_run(
        self,
        tenant_id: str,
        run_id: str,
        requested_by_user_id: str,
        reason_code: str,
    ) -> AgentRuntimeState:
        self.request_run_retry(
            tenant_id,
            run_id,
            requested_by_user_id,
            reason_code,
        )
        return self.execute_run(tenant_id, run_id)

    def _initial_state(self, run: Run) -> AgentRuntimeState:
        platform_context, goal = _split_platform_context(run.message)
        state = AgentRuntimeState(
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            user_id=run.user_id,
            run_id=run.id,
            goal=goal,
            status=run.status,
            max_iterations=self.loop_max_iterations,
            max_repairs=self.loop_max_repairs,
            cost_limit=self.loop_cost_limit,
            deadline_at=utc_now() + timedelta(seconds=self.loop_timeout_seconds),
        )
        if platform_context is not None:
            state.runtime_metadata["platform_context"] = platform_context
        return state

    def _decide_runtime_execution(self, run: Run) -> PolicyDecision:
        if self.policy_service is None:
            return PolicyDecision.allow()
        return self.policy_service.decide_runtime_execution(
            PolicyRequest(
                tenant_id=run.tenant_id,
                workspace_id=run.workspace_id,
                user_id=run.user_id,
                run_id=run.id,
                action="runs.execute",
                resource=f"run:{run.id}",
                context={"agent_id": run.agent_id},
            )
        )

    def _decide_runtime_step(
        self,
        state: AgentRuntimeState,
        run: Run,
        step: PlanStep,
    ) -> PolicyDecision:
        if self.policy_service is None:
            return PolicyDecision.allow()
        tool_policy = self.tool_gateway.policies.get(step.tool_name)
        risk_level = tool_policy.risk_level.value if tool_policy is not None else None
        return self.policy_service.decide_runtime_step(
            PolicyRequest(
                tenant_id=state.tenant_id,
                workspace_id=state.workspace_id,
                user_id=state.user_id,
                run_id=state.run_id,
                action="runtime.step.execute",
                resource=f"tool:{step.tool_name}",
                risk_level=risk_level,
                context={
                    "agent_id": run.agent_id,
                    "tool_name": step.tool_name,
                    "skill_id": step.skill_id,
                    "connector_id": step.tool_input.get("connector_id"),
                    "external_write": step.tool_input.get("external_write") is True,
                    "risk_level": risk_level,
                    "trigger_id": step.tool_input.get("trigger_id"),
                    "step_id": step.id,
                },
            )
        )

    def _create_plan(
        self,
        run: Run,
        context: AgentRetrievedContext,
        approved_guardrail_keys: list[str] | None = None,
    ) -> list[PlanStep]:
        goal = workflow_goal(self.store, run) if run.mode == RunMode.WORKFLOW else run.message
        workflow_guidance = (
            " This is a workflow preview, not tool execution. Split the goal into 2 to 6 "
            "meaningful worker tasks and honor any step count requested by the user. Use "
            "tool_name \"none\" and an empty tool_input when a worker can reason or write "
            "the answer itself; name a real built-in tool only when the task truly needs it. "
            "Every worker runs in its own isolated tool session. Pass dependency results "
            "through task summaries only; never make a later task read a sandbox path or "
            "browser session created by an earlier task. Keep work that requires the same "
            "file or session in one task. "
            "Do not create pass-through tasks solely to receive unchanged input or emit the "
            "final response; final synthesis handles the response. "
            "Do not invent browser work, files, or external actions. Keep the DAG minimal."
            if run.mode == RunMode.WORKFLOW
            else ""
        )
        messages = [
            ModelMessage(
                role="system",
                content=(
                    "You are Taroai's enterprise agent planner. Return strict JSON with "
                    "a top-level steps array. Each step must include id, title, tool_name, "
                    "tool_input, approval_required, depends_on, phase_id, phase_title, "
                    "tool_mode, and model_hint. Build a dependency DAG: use an empty "
                    "depends_on array for independent work and task ids for prerequisites. "
                    "tool_mode is read_only, standard, or code; model_hint is fast or strong. "
                    "Tasks without dependencies may run concurrently. tool_input must always be a JSON "
                    "object, never a string. For sandbox.command use a shape such as "
                    '{"command":"mkdir -p /workspace/artifacts && ...",'
                    '"artifact_path":"/workspace/artifacts/report.md"}. '
                    "Available built-in tools include "
                    "sandbox.command for shell or Python work in the run workspace and "
                    "browser.action for browser navigation, extraction, typing, clicking, "
                    "and screenshots. For browser.action, tool_input must contain "
                    'action_type set to "navigate", "click", "type", "screenshot", or '
                    '"extract"; add url, selector, or text only when needed. browser.action '
                    "cannot generate images, video, or audio. Do not invent session_id "
                    "values for sandbox.command "
                    "or browser.action; the runtime injects them. When the user asks for a "
                    "deliverable, create it with sandbox.command under /workspace/artifacts/ "
                    "and include artifact_path or artifact_paths in tool_input, for example "
                    '"/workspace/artifacts/report.md". The command must create the '
                    "directory before writing files, for example "
                    "'mkdir -p /workspace/artifacts && ...'. Files outside "
                    "/workspace/artifacts/ are rejected for artifact publication."
                    + workflow_guidance
                ),
            )
        ]
        context_message = self._context_model_message(context)
        if context_message is not None:
            messages.append(context_message)
        messages.append(ModelMessage(role="user", content=goal))
        context_sensitivity_level = self._context_sensitivity_level(context)
        request = ModelGatewayRequest(
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            user_id=run.user_id,
            run_id=run.id,
            provider_id=run.provider_id,
            model=run.model_id,
            reasoning_effort=run.reasoning_effort,
            sensitivity_level=context_sensitivity_level,
            messages=messages,
            input=goal,
            tool_choice="auto",
            metadata={
                "agent_id": run.agent_id,
                "attachments": run.attachments,
                "mode": run.mode,
                "knowledge_result_count": len(context.knowledge_results),
                "memory_record_count": len(context.memory_records),
                "context_sensitivity_level": context_sensitivity_level,
            },
        )
        request = self._apply_model_request_guardrails(
            run,
            request,
            approved_guardrail_keys or [],
        )
        resolved_model = self.model_policy.assert_request_allowed(request)
        if resolved_model is not None and request.model != resolved_model:
            request = request.model_copy(update={"model": resolved_model})
        self.model_budget_guard.assert_plan_allowed(self.store, run.tenant_id, run.id)
        plan_started_at = time.perf_counter()
        response = self.model_gateway.create_plan(request)
        latency_ms = max(0, round((time.perf_counter() - plan_started_at) * 1000))
        response = self._apply_model_response_guardrails(
            run,
            response,
            approved_guardrail_keys or [],
        )
        self.model_plan_metadata[run.id] = self._record_model_plan(
            run,
            response,
            latency_ms,
        )
        return [
            PlanStep(
                id=step.id,
                title=step.title,
                tool_name=step.tool_name,
                skill_id=step.skill_id,
                tool_input=step.tool_input,
                approval_required=step.approval_required,
                depends_on=step.depends_on,
                phase_id=step.phase_id,
                phase_title=step.phase_title,
                tool_mode=step.tool_mode,
                model_hint=step.model_hint,
            )
            for step in response.planned_steps
        ]

    def _fail_for_model_budget(
        self,
        state: AgentRuntimeState,
        run: Run,
        error: ModelBudgetExceededError,
    ) -> AgentRuntimeState:
        self._record_audit_event(
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            user_id=run.user_id,
            run_id=run.id,
            event_type="model.budget_exceeded",
            metadata=error.metadata,
        )
        return AgentExecutionServices(self)._fail(
            state,
            run,
            "model_budget_exceeded",
            detail=str(error),
            metadata={"reason": "model_budget_exceeded", "error": str(error)},
        )

    def _fail_for_guardrail(
        self,
        state: AgentRuntimeState,
        run: Run,
        error: _RuntimeGuardrailViolation,
    ) -> AgentRuntimeState:
        return AgentExecutionServices(self)._fail(
            state,
            run,
            error.reason,
            detail=str(error),
            metadata={
                "reason": error.reason,
                "guardrail_event_type": error.event_type,
            },
        )

    def _pause_for_guardrail_approval(
        self,
        state: AgentRuntimeState,
        run: Run,
        error: _RuntimeGuardrailApprovalRequired,
    ) -> AgentRuntimeState:
        approval = self.store.create_approval_request(
            tenant_id=run.tenant_id,
            run_id=run.id,
            step_id=f"guardrail:{error.stage.value}",
            reason=error.reason,
            kind="guardrail",
            subject_type="guardrail_stage",
            subject_id=error.stage.value,
            preview_payload={"stage": error.stage.value},
            validation_payload={"valid": False, "reason": error.reason},
        )
        self.store.update_run_status(
            run.tenant_id,
            run.id,
            RunStatus.AWAITING_APPROVAL,
            emit_status_event=False,
        )
        state.status = RunStatus.AWAITING_APPROVAL
        if not self._should_preserve_current_step_for_guardrail_pause(state, error):
            state.current_step_id = None
        state.pending_guardrail_approval_key = error.guardrail_key
        state.pending_guardrail_approval_stage = error.stage.value
        state.approval_id = approval.id
        self.pending_states[state.run_id] = state
        self._save_state(state)
        return state

    def _fail_for_policy_block(
        self,
        state: AgentRuntimeState,
        run: Run,
        decision: PolicyDecision,
        current_step_id: str | None = None,
    ) -> AgentRuntimeState:
        reason = decision.reason or "runtime execution denied by policy"
        payload = {
            "decision": "denied",
            "reason": reason,
            **decision.metadata,
        }
        if current_step_id is not None:
            state.current_step_id = current_step_id
        self.store.append_run_event(run, "policy.blocked", payload)
        return AgentExecutionServices(self)._fail(
            state,
            run,
            "policy_denied",
            detail=reason,
        )

    def _should_preserve_current_step_for_guardrail_pause(
        self,
        state: AgentRuntimeState,
        error: _RuntimeGuardrailApprovalRequired,
    ) -> bool:
        if error.stage != GuardrailStage.ARTIFACT:
            return False
        return self._has_pending_sandbox_artifact_promotion(state)

    def _has_pending_sandbox_artifact_promotion(
        self,
        state: AgentRuntimeState,
    ) -> bool:
        if state.current_step_id is None or state.sandbox_session_id is None:
            return False
        if state.current_step_id in state.completed_step_ids:
            return False
        step = self._planned_step_by_id(state, state.current_step_id)
        return step is not None and step.tool_name == "sandbox.command"

    def _planned_step_by_id(
        self,
        state: AgentRuntimeState,
        step_id: str,
    ) -> PlanStep | None:
        for step in state.plan:
            if step.id == step_id:
                return step
        return None

    def _resume_sandbox_artifact_promotion_after_guardrail_approval(
        self,
        state: AgentRuntimeState,
    ) -> AgentRuntimeState:
        step = self._planned_step_by_id(state, state.current_step_id or "")
        if step is None:
            return self._finalize_success(state)
        run = self.store.get_run(state.tenant_id, state.run_id)
        try:
            self._promote_sandbox_artifacts(state, step)
        except _RuntimeSandboxArtifactPathRejected as error:
            return self._fail_for_sandbox_artifact_path_rejection(
                state,
                run,
                step,
                error,
            )
        except _RuntimeStorageContentRejected as error:
            return self._fail_for_storage_content_rejection(
                state,
                run,
                step,
                error,
            )
        except _RuntimeGuardrailApprovalRequired as error:
            return self._pause_for_guardrail_approval(state, run, error)
        except _RuntimeGuardrailViolation as error:
            return self._fail_for_guardrail(state, run, error)
        if step.id not in state.completed_step_ids:
            self._record_tool_execution(state, step)
            state.completed_step_ids.append(step.id)
        AgentExecutionServices(self)._persist_checkpoint(
            state,
            run,
            cycle_id=state.current_cycle_id,
        )
        return self.execute_run(state.tenant_id, state.run_id)

    def _record_model_policy_denial(
        self,
        state: AgentRuntimeState,
        run: Run,
        error: ModelPolicyDeniedError,
    ) -> None:
        self._record_audit_event(
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            user_id=run.user_id,
            run_id=run.id,
            event_type="model.policy_denied",
            metadata=error.metadata,
        )
        AgentExecutionServices(self)._fail(
            state,
            run,
            "model_policy_denied",
            detail=str(error),
            metadata={"reason": "model_policy_denied", "error": str(error)},
        )

    def _record_model_gateway_failure(
        self,
        state: AgentRuntimeState,
        run: Run,
        error: ModelGatewayError,
    ) -> None:
        current = self.store.get_run(run.tenant_id, run.id)
        if current.status in TERMINAL_RUN_STATUSES:
            state.status = current.status
            try:
                state.failure_reason = self._load_state(
                    run.tenant_id, run.id
                ).failure_reason
            except NotFoundError:
                pass
            return
        if isinstance(error, ModelSafetyRefusalError):
            provider = error.provider or run.provider_id or "model_gateway"
            model_id = error.model_id or run.model_id or ""
            self._record_audit_event(
                tenant_id=run.tenant_id,
                workspace_id=run.workspace_id,
                user_id=run.user_id,
                run_id=run.id,
                event_type="model.safety_refused",
                metadata={"provider": provider, "model_id": model_id},
            )
            self.store.append_run_event(
                run,
                "classifier_refusal",
                {
                    "provider": provider,
                    "modelId": model_id,
                    "originalText": error.original_text,
                    "detectedAt": utc_now().isoformat(),
                },
            )
            AgentExecutionServices(self)._fail(
                state,
                run,
                "model_safety_refusal",
                detail=str(error),
                metadata={"reason": "model_safety_refusal"},
            )
            return
        self._record_audit_event(
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            user_id=run.user_id,
            run_id=run.id,
            event_type="model.gateway_failed",
            metadata={
                "error_type": error.__class__.__name__,
                "message": str(error),
            },
        )
        AgentExecutionServices(self)._fail(
            state,
            run,
            "model_gateway_error",
            detail=str(error),
            metadata={
                "reason": "model_gateway_error",
                "error_type": error.__class__.__name__,
            },
        )

    def _record_model_plan(
        self,
        run: Run,
        response: ModelGatewayResponse,
        latency_ms: int,
    ) -> dict[str, Any]:
        metadata = {
            "response_id": response.id,
            "provider": response.provider,
            "model": response.model,
            "planned_step_count": len(response.planned_steps),
            "latency_ms": latency_ms,
            "usage": (
                response.usage.model_dump(mode="json")
                if response.usage is not None
                else None
            ),
        }
        if response.provider_attempts:
            metadata["provider_attempts"] = [
                attempt.model_dump(mode="json") for attempt in response.provider_attempts
            ]
        self.store.record_billing_meter(
            tenant_id=run.tenant_id,
            run_id=run.id,
            meter_type="model_call_count",
            quantity=1,
            unit="call",
            provider=response.provider,
            model=response.model,
            cost_estimate=self._estimate_billing_cost(
                meter_type="model_call_count",
                quantity=1,
                unit="call",
                provider=response.provider,
                model=response.model,
                tenant_id=run.tenant_id,
                workspace_id=run.workspace_id,
            ),
                metadata=metadata,
            )
        if response.usage is not None:
            self.store.record_billing_meter(
                tenant_id=run.tenant_id,
                run_id=run.id,
                meter_type="model_tokens_input",
                quantity=response.usage.input_tokens,
                unit="token",
                provider=response.provider,
                model=response.model,
                cost_estimate=self._estimate_billing_cost(
                    meter_type="model_tokens_input",
                    quantity=response.usage.input_tokens,
                    unit="token",
                    provider=response.provider,
                    model=response.model,
                    tenant_id=run.tenant_id,
                    workspace_id=run.workspace_id,
                ),
                metadata=metadata,
            )
            self.store.record_billing_meter(
                tenant_id=run.tenant_id,
                run_id=run.id,
                meter_type="model_tokens_output",
                quantity=response.usage.output_tokens,
                unit="token",
                provider=response.provider,
                model=response.model,
                cost_estimate=self._estimate_billing_cost(
                    meter_type="model_tokens_output",
                    quantity=response.usage.output_tokens,
                    unit="token",
                    provider=response.provider,
                    model=response.model,
                    tenant_id=run.tenant_id,
                    workspace_id=run.workspace_id,
                ),
                metadata=metadata,
            )
            if response.usage.cached_input_tokens > 0:
                self.store.record_billing_meter(
                    tenant_id=run.tenant_id,
                    run_id=run.id,
                    meter_type="model_tokens_cached_input",
                    quantity=response.usage.cached_input_tokens,
                    unit="token",
                    provider=response.provider,
                    model=response.model,
                    cost_estimate=self._estimate_billing_cost(
                        meter_type="model_tokens_cached_input",
                        quantity=response.usage.cached_input_tokens,
                        unit="token",
                        provider=response.provider,
                        model=response.model,
                        tenant_id=run.tenant_id,
                        workspace_id=run.workspace_id,
                    ),
                    metadata=metadata,
                )
        self.store.record_billing_meter(
            tenant_id=run.tenant_id,
            run_id=run.id,
            meter_type="model_latency_ms",
            quantity=latency_ms,
            unit="millisecond",
            provider=response.provider,
            model=response.model,
            cost_estimate=self._estimate_billing_cost(
                meter_type="model_latency_ms",
                quantity=latency_ms,
                unit="millisecond",
                provider=response.provider,
                model=response.model,
                tenant_id=run.tenant_id,
                workspace_id=run.workspace_id,
            ),
            metadata=metadata,
        )
        self._record_audit_event(
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            user_id=run.user_id,
            run_id=run.id,
            event_type="model.plan.created",
            metadata=metadata,
        )
        return metadata

    def _plan_created_event_payload(
        self,
        state: AgentRuntimeState,
    ) -> dict[str, Any]:
        payload = {
            "steps": [step.model_dump(mode="json") for step in state.plan],
        }
        metadata = self.model_plan_metadata.pop(state.run_id, None)
        if metadata:
            payload.update(metadata)
        return payload

    def _estimate_billing_cost(
        self,
        meter_type: str,
        quantity: float,
        unit: str,
        provider: str | None = None,
        model: str | None = None,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        skill_id: str | None = None,
    ) -> float | None:
        return self.billing_pricing_service.estimate_cost(
            meter_type=meter_type,
            quantity=quantity,
            unit=unit,
            provider=provider,
            model=model,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            skill_id=skill_id,
        )

    def _apply_model_request_guardrails(
        self,
        run: Run,
        request: ModelGatewayRequest,
        approved_guardrail_keys: list[str],
    ) -> ModelGatewayRequest:
        if self.guardrail_service is None:
            return request
        decision = self.guardrail_service.evaluate(
            GuardrailEvaluationRequest(
                tenant_id=run.tenant_id,
                workspace_id=run.workspace_id,
                stage=GuardrailStage.MODEL_REQUEST,
                content=self._model_request_guardrail_content(request),
                attributes={
                    "model": request.model,
                    "message_count": len(request.messages),
                    "tool_count": len(request.tools),
                    "tool_choice": request.tool_choice,
                    "input_length": len(request.input or ""),
                    "metadata_keys": sorted(request.metadata.keys()),
                },
            )
        )
        return self._apply_model_guardrail_decision_to_request(
            run,
            request,
            decision,
            approved_guardrail_keys,
        )

    def _apply_model_guardrail_decision_to_request(
        self,
        run: Run,
        request: ModelGatewayRequest,
        decision: GuardrailDecision,
        approved_guardrail_keys: list[str],
    ) -> ModelGatewayRequest:
        if decision.blocked:
            metadata = self._record_model_guardrail_audit(
                run,
                decision,
                "guardrail.model_request_blocked",
                {
                    "stage": GuardrailStage.MODEL_REQUEST.value,
                    "model": request.model,
                    "message_count": len(request.messages),
                    "tool_count": len(request.tools),
                    "input_length": len(request.input or ""),
                },
            )
            raise _RuntimeGuardrailViolation(
                event_type="guardrail.model_request_blocked",
                reason="model_guardrail_blocked",
                metadata=metadata,
            )
        if decision.approval_required:
            guardrail_key = self._guardrail_approval_key(
                GuardrailStage.MODEL_REQUEST, decision
            )
            if guardrail_key in approved_guardrail_keys:
                return request
            metadata = self._record_model_guardrail_audit(
                run,
                decision,
                "guardrail.model_request_approval_required",
                {
                    "stage": GuardrailStage.MODEL_REQUEST.value,
                    "model": request.model,
                    "message_count": len(request.messages),
                    "tool_count": len(request.tools),
                    "input_length": len(request.input or ""),
                },
            )
            raise _RuntimeGuardrailApprovalRequired(
                event_type="guardrail.model_request_approval_required",
                stage=GuardrailStage.MODEL_REQUEST,
                guardrail_key=guardrail_key,
                reason=decision.message or "Model request requires guardrail approval",
                metadata=metadata,
            )
        if decision.action == GuardrailAction.REDACT and decision.redactions:
            redacted_request = request.model_copy(
                update={
                    "messages": [
                        message.model_copy(
                            update={
                                "content": self._apply_guardrail_redactions(
                                    message.content,
                                    decision,
                                )
                            }
                        )
                        for message in request.messages
                    ],
                    "input": (
                        self._apply_guardrail_redactions(request.input, decision)
                        if request.input is not None
                        else None
                    ),
                }
            )
            self._record_model_guardrail_audit(
                run,
                decision,
                "guardrail.model_request_redacted",
                {
                    "stage": GuardrailStage.MODEL_REQUEST.value,
                    "model": request.model,
                    "message_count": len(request.messages),
                    "tool_count": len(request.tools),
                    "input_length": len(request.input or ""),
                },
            )
            return redacted_request
        if decision.audit_required and decision.warnings:
            self._record_model_guardrail_audit(
                run,
                decision,
                "guardrail.model_request_warned",
                {
                    "stage": GuardrailStage.MODEL_REQUEST.value,
                    "model": request.model,
                    "message_count": len(request.messages),
                    "tool_count": len(request.tools),
                    "input_length": len(request.input or ""),
                },
            )
        return request

    def _apply_model_response_guardrails(
        self,
        run: Run,
        response: ModelGatewayResponse,
        approved_guardrail_keys: list[str],
    ) -> ModelGatewayResponse:
        if self.guardrail_service is None:
            return response
        content = response.output_text or self._model_response_guardrail_content(
            response
        )
        decision = self.guardrail_service.evaluate(
            GuardrailEvaluationRequest(
                tenant_id=run.tenant_id,
                workspace_id=run.workspace_id,
                stage=GuardrailStage.MODEL_RESPONSE,
                content=content,
                attributes={
                    "response_id": response.id,
                    "model": response.model,
                    "planned_step_count": len(response.planned_steps),
                    "output_length": len(content),
                },
            )
        )
        if decision.blocked:
            metadata = self._record_model_guardrail_audit(
                run,
                decision,
                "guardrail.model_response_blocked",
                {
                    "stage": GuardrailStage.MODEL_RESPONSE.value,
                    "response_id": response.id,
                    "model": response.model,
                    "planned_step_count": len(response.planned_steps),
                    "output_length": len(content),
                },
            )
            raise _RuntimeGuardrailViolation(
                event_type="guardrail.model_response_blocked",
                reason="model_guardrail_blocked",
                metadata=metadata,
            )
        if decision.approval_required:
            guardrail_key = self._guardrail_approval_key(
                GuardrailStage.MODEL_RESPONSE, decision
            )
            if guardrail_key in approved_guardrail_keys:
                return response
            metadata = self._record_model_guardrail_audit(
                run,
                decision,
                "guardrail.model_response_approval_required",
                {
                    "stage": GuardrailStage.MODEL_RESPONSE.value,
                    "response_id": response.id,
                    "model": response.model,
                    "planned_step_count": len(response.planned_steps),
                    "output_length": len(content),
                },
            )
            raise _RuntimeGuardrailApprovalRequired(
                event_type="guardrail.model_response_approval_required",
                stage=GuardrailStage.MODEL_RESPONSE,
                guardrail_key=guardrail_key,
                reason=decision.message or "Model response requires guardrail approval",
                metadata=metadata,
            )
        if decision.action == GuardrailAction.REDACT and decision.redactions:
            self._record_model_guardrail_audit(
                run,
                decision,
                "guardrail.model_response_redacted",
                {
                    "stage": GuardrailStage.MODEL_RESPONSE.value,
                    "response_id": response.id,
                    "model": response.model,
                    "planned_step_count": len(response.planned_steps),
                    "output_length": len(content),
                },
            )
            return self._redact_model_response(response, decision)
        if decision.audit_required and decision.warnings:
            self._record_model_guardrail_audit(
                run,
                decision,
                "guardrail.model_response_warned",
                {
                    "stage": GuardrailStage.MODEL_RESPONSE.value,
                    "response_id": response.id,
                    "model": response.model,
                    "planned_step_count": len(response.planned_steps),
                    "output_length": len(content),
                },
            )
        return response

    def _model_request_guardrail_content(self, request: ModelGatewayRequest) -> str:
        parts = [f"{message.role}: {message.content}" for message in request.messages]
        if request.input is not None:
            parts.append(f"input: {request.input}")
        return "\n".join(parts)

    def _model_response_guardrail_content(self, response: ModelGatewayResponse) -> str:
        return json.dumps(
            {
                "steps": [
                    step.model_dump(mode="json") for step in response.planned_steps
                ]
            },
            sort_keys=True,
        )

    def _redact_model_response(
        self,
        response: ModelGatewayResponse,
        decision: GuardrailDecision,
    ) -> ModelGatewayResponse:
        source_content = response.output_text or self._model_response_guardrail_content(
            response
        )
        redacted_content = self._apply_guardrail_redactions(source_content, decision)
        parsed_steps = self._parse_redacted_planned_steps(redacted_content)
        if parsed_steps is None:
            parsed_steps = [
                self._redact_planned_tool_call(step, decision)
                for step in response.planned_steps
            ]
        return response.model_copy(
            update={
                "output_text": redacted_content,
                "planned_steps": parsed_steps,
            }
        )

    def _parse_redacted_planned_steps(
        self, content: str
    ) -> list[PlannedToolCall] | None:
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        steps = parsed.get("steps")
        if not isinstance(steps, list):
            return None
        try:
            return [PlannedToolCall.model_validate(step) for step in steps]
        except ValidationError:
            return None

    def _redact_planned_tool_call(
        self,
        step: PlannedToolCall,
        decision: GuardrailDecision,
    ) -> PlannedToolCall:
        return step.model_copy(
            update={
                "title": self._apply_guardrail_redactions(step.title, decision),
                "tool_input": self._redact_guarded_value(step.tool_input, decision),
            }
        )

    def _redact_guarded_value(self, value, decision: GuardrailDecision):
        if isinstance(value, str):
            return self._apply_guardrail_redactions(value, decision)
        if isinstance(value, dict):
            return {
                key: self._redact_guarded_value(item, decision)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._redact_guarded_value(item, decision) for item in value]
        return value

    def _apply_guardrail_redactions(
        self, value: str, decision: GuardrailDecision
    ) -> str:
        redacted = value
        for redaction in decision.redactions:
            flags = 0 if redaction.case_sensitive else re.IGNORECASE
            redacted = re.sub(
                re.escape(redaction.text),
                redaction.replacement,
                redacted,
                flags=flags,
            )
        return redacted

    def _guardrail_approval_key(
        self,
        stage: GuardrailStage,
        decision: GuardrailDecision,
    ) -> str:
        identifiers = decision.matched_rule_ids or decision.detector_finding_ids
        if identifiers:
            return f"{stage.value}:{','.join(identifiers)}"
        return f"{stage.value}:{decision.action.value}"

    def _record_model_guardrail_audit(
        self,
        run: Run,
        decision: GuardrailDecision,
        event_type: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        audit_metadata = {
            "guardrail_action": decision.action.value,
            "guardrail_rule_ids": decision.matched_rule_ids,
            "guardrail_detector_finding_ids": decision.detector_finding_ids,
            "severity": (
                decision.severity.value if decision.severity is not None else None
            ),
            "message": decision.message,
            **metadata,
        }
        self._record_audit_event(
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            user_id=run.user_id,
            run_id=run.id,
            event_type=event_type,
            metadata=audit_metadata,
        )
        return audit_metadata

    def _apply_artifact_guardrails(
        self,
        run: Run,
        artifact: dict[str, str],
        approved_guardrail_keys: list[str],
        content: str | None = None,
    ) -> dict[str, str]:
        if self.guardrail_service is None:
            return artifact
        decision = self.guardrail_service.evaluate(
            GuardrailEvaluationRequest(
                tenant_id=run.tenant_id,
                workspace_id=run.workspace_id,
                stage=GuardrailStage.ARTIFACT,
                content=self._artifact_guardrail_content(artifact, content),
                attributes={
                    "artifact_type": artifact["artifact_type"],
                    "name_length": len(artifact["name"]),
                    "uri_scheme": artifact["uri"].split(":", 1)[0],
                    "content_length": len(content or ""),
                },
            )
        )
        if decision.blocked:
            metadata = self._record_artifact_guardrail_audit(
                run,
                decision,
                "guardrail.artifact_blocked",
                artifact,
            )
            raise _RuntimeGuardrailViolation(
                event_type="guardrail.artifact_blocked",
                reason="artifact_guardrail_blocked",
                metadata=metadata,
            )
        if decision.approval_required:
            guardrail_key = self._guardrail_approval_key(
                GuardrailStage.ARTIFACT, decision
            )
            if guardrail_key in approved_guardrail_keys:
                return artifact
            metadata = self._record_artifact_guardrail_audit(
                run,
                decision,
                "guardrail.artifact_approval_required",
                artifact,
            )
            raise _RuntimeGuardrailApprovalRequired(
                event_type="guardrail.artifact_approval_required",
                stage=GuardrailStage.ARTIFACT,
                guardrail_key=guardrail_key,
                reason=decision.message
                or "Artifact publication requires guardrail approval",
                metadata=metadata,
            )
        if decision.action == GuardrailAction.REDACT and decision.redactions:
            self._record_artifact_guardrail_audit(
                run,
                decision,
                "guardrail.artifact_redacted",
                artifact,
            )
            return {
                "name": self._apply_guardrail_redactions(artifact["name"], decision),
                "artifact_type": artifact["artifact_type"],
                "uri": self._apply_guardrail_redactions(artifact["uri"], decision),
            }
        if decision.audit_required and decision.warnings:
            self._record_artifact_guardrail_audit(
                run,
                decision,
                "guardrail.artifact_warned",
                artifact,
            )
        return artifact

    def _artifact_guardrail_content(
        self,
        artifact: dict[str, str],
        content: str | None = None,
    ) -> str:
        return json.dumps(
            {
                "name": artifact["name"],
                "artifact_type": artifact["artifact_type"],
                "uri": artifact["uri"],
                "content": content or "",
            },
            sort_keys=True,
        )

    def _record_artifact_guardrail_audit(
        self,
        run: Run,
        decision: GuardrailDecision,
        event_type: str,
        artifact: dict[str, str],
    ) -> dict[str, Any]:
        audit_metadata = {
            "guardrail_action": decision.action.value,
            "guardrail_rule_ids": decision.matched_rule_ids,
            "guardrail_detector_finding_ids": decision.detector_finding_ids,
            "severity": (
                decision.severity.value if decision.severity is not None else None
            ),
            "message": decision.message,
            "artifact_type": artifact["artifact_type"],
            "artifact_name_length": len(artifact["name"]),
            "uri_scheme": artifact["uri"].split(":", 1)[0],
        }
        self._record_audit_event(
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            user_id=run.user_id,
            run_id=run.id,
            event_type=event_type,
            metadata=audit_metadata,
        )
        return audit_metadata

    def _load_context(self, run: Run) -> AgentRetrievedContext:
        return AgentRetrievedContext(
            knowledge_results=self._load_knowledge_context(run),
            memory_records=self._load_memory_context(run),
        )

    def _retrieval_query(self, run: Run) -> str:
        if run.trigger_message_id is not None:
            try:
                return self.store.get_chat_message(
                    run.tenant_id, run.trigger_message_id
                ).content
            except NotFoundError:
                pass
        return run.message

    def _load_knowledge_context(self, run: Run):
        if self.knowledge_service is None:
            return []
        query = self._retrieval_query(run)
        knowledge_base_ids = {
            reference.id
            for reference in run.resource_refs
            if reference.type == "knowledge"
        }
        agent_bound = any(reference.type == "agent" for reference in run.resource_refs)
        results = []
        documents = self.knowledge_service.list_documents(
            run.tenant_id, workspace_id=run.workspace_id
        )
        if knowledge_base_ids:
            documents = [
                document
                for document in documents
                if document.knowledge_base_id in knowledge_base_ids
            ]
        if documents and (knowledge_base_ids or not agent_bound):
            query_embedding = self._load_query_embedding(run, query)
            results = self.knowledge_service.retrieve(
                RetrievalRequest(
                    tenant_id=run.tenant_id,
                    query=query,
                    query_embedding=query_embedding,
                    allowed_workspace_ids=[run.workspace_id],
                    allowed_knowledge_base_ids=sorted(knowledge_base_ids),
                    acl_subjects=[
                        f"user:{run.user_id}",
                        f"workspace:{run.workspace_id}",
                        f"tenant:{run.tenant_id}",
                    ],
                    clearance_level=0,
                    limit=5,
                )
            )
        results = self._apply_retrieval_guardrails(run, results)
        self._record_audit_event(
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            user_id=run.user_id,
            run_id=run.id,
            event_type="knowledge.context.retrieved",
            metadata={
                "result_count": len(results),
                "document_ids": [result.document_id for result in results],
                "chunk_ids": [result.chunk_id for result in results],
                "source_document_ids": [
                    result.source_document_id for result in results
                ],
                "knowledge_base_ids": sorted(knowledge_base_ids),
            },
        )
        return results

    def _load_query_embedding(self, run: Run, query: str) -> list[float]:
        embeddings = self._load_embeddings(run, "knowledge_query", [query])
        return embeddings[0] if embeddings else []

    def _load_embeddings(
        self,
        run: Run,
        purpose: Literal["knowledge_query", "memory_query"],
        inputs: list[str],
    ) -> list[list[float]]:
        if self.embedding_gateway is None or not inputs:
            return []
        try:
            response = self.embedding_gateway.embed(
                EmbeddingGatewayRequest(
                    tenant_id=run.tenant_id,
                    workspace_id=run.workspace_id,
                    user_id=run.user_id,
                    run_id=run.id,
                    purpose=purpose,
                    input=inputs,
                )
            )
        except EmbeddingGatewayError as error:
            self._record_audit_event(
                tenant_id=run.tenant_id,
                workspace_id=run.workspace_id,
                user_id=run.user_id,
                run_id=run.id,
                event_type="embedding.gateway_failed",
                metadata={
                    "purpose": purpose,
                    "input_count": len(inputs),
                    "error_type": error.__class__.__name__,
                    "degraded_to": "lexical",
                },
            )
            return []
        self._record_embedding_usage(
            run,
            response,
            purpose=purpose,
            input_count=len(inputs),
        )
        return [
            item.embedding for item in sorted(response.embeddings, key=lambda item: item.index)
        ]

    def _record_embedding_usage(
        self,
        run: Run,
        response: EmbeddingGatewayResponse,
        *,
        purpose: Literal["knowledge_query", "memory_query"],
        input_count: int,
    ) -> None:
        EmbeddingUsageRecorder(
            store=self.store,
            audit_service=self.audit_service,
            billing_pricing_service=self.billing_pricing_service,
        ).record(
            EmbeddingUsageRecord(
                tenant_id=run.tenant_id,
                workspace_id=run.workspace_id,
                user_id=run.user_id,
                run_id=run.id,
                purpose=purpose,
                response=response,
                input_count=input_count,
                metadata={
                    "allowed_workspace_count": 1,
                    "clearance_level": 0,
                },
            )
        )

    def _apply_retrieval_guardrails(
        self,
        run: Run,
        results: list[RetrievalResult],
    ) -> list[RetrievalResult]:
        if self.guardrail_service is None:
            return results
        accepted_results: list[RetrievalResult] = []
        for result in results:
            decision = self.guardrail_service.evaluate(
                GuardrailEvaluationRequest(
                    tenant_id=run.tenant_id,
                    workspace_id=run.workspace_id,
                    stage=GuardrailStage.RETRIEVAL,
                    content=result.excerpt,
                    attributes={
                        "document_id": result.document_id,
                        "chunk_id": result.chunk_id,
                        "source_document_id": result.source_document_id,
                        "source_uri": result.source_uri,
                    },
                )
            )
            if decision.blocked:
                self._record_retrieval_guardrail_audit(
                    run,
                    result,
                    decision,
                    "guardrail.retrieval_blocked",
                )
                continue
            if decision.approval_required:
                self._record_retrieval_guardrail_audit(
                    run,
                    result,
                    decision,
                    "guardrail.retrieval_approval_required",
                )
                continue
            if (
                decision.action == GuardrailAction.REDACT
                and decision.redacted_content is not None
            ):
                accepted_results.append(
                    result.model_copy(update={"excerpt": decision.redacted_content})
                )
                continue
            accepted_results.append(result)
        return accepted_results

    def _record_retrieval_guardrail_audit(
        self,
        run: Run,
        result: RetrievalResult,
        decision: GuardrailDecision,
        event_type: str,
    ) -> None:
        self._record_audit_event(
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            user_id=run.user_id,
            run_id=run.id,
            event_type=event_type,
            metadata={
                "guardrail_action": decision.action.value,
                "guardrail_rule_ids": decision.matched_rule_ids,
                "guardrail_detector_finding_ids": decision.detector_finding_ids,
                "severity": (
                    decision.severity.value if decision.severity is not None else None
                ),
                "message": decision.message,
                "document_id": result.document_id,
                "chunk_id": result.chunk_id,
                "source_document_id": result.source_document_id,
                "source_uri": result.source_uri,
            },
        )

    def _load_memory_context(self, run: Run):
        """过滤硬过期记忆，再按重要度和半衰期选取模型上下文。"""

        if self.long_term_memory_service is None:
            return []
        now = utc_now()
        query = self._retrieval_query(run)
        query_terms = retrieval_terms(query)
        query_has_cjk = re.search(r"[\u3400-\u9fff]", query) is not None
        minimum_relevance = 0.25 if query_has_cjk else 0.5
        records = []
        scopes = [
            (MemoryScopeType.USER, run.user_id),
            (MemoryScopeType.COMPANY, run.tenant_id),
            (MemoryScopeType.TASK, run.id),
        ]
        if run.agent_id is not None:
            scopes.append((MemoryScopeType.AGENT, run.agent_id))
        seen_ids = set()
        for scope_type, scope_id in scopes:
            for record in self.long_term_memory_service.list_by_scope(
                run.tenant_id,
                scope_type,
                scope_id,
            ):
                if record.id in seen_ids:
                    continue
                if record.expires_at is not None and record.expires_at <= now:
                    continue
                records.append(record)
                seen_ids.add(record.id)

        def memory_relevance(record) -> float:
            indexed_terms = record.metadata.get("retrieval_terms", [])
            indexed_text = (
                " ".join(term for term in indexed_terms if isinstance(term, str))
                if isinstance(indexed_terms, list)
                else ""
            )
            return term_relevance(query_terms, f"{record.content}\n{indexed_text}")

        lexical_scores = {record.id: memory_relevance(record) for record in records}

        semantic_candidates = [
            record
            for record in records
            if (
                record.scope_type == MemoryScopeType.USER
                or record.metadata.get("source") == "agent_session_summary"
            )
            and record.sensitivity_level == 0
            and record.metadata.get("pinned") is not True
            and record.metadata.get("memory_key")
            not in _ALWAYS_APPLY_USER_MEMORY_KEYS
            and lexical_scores[record.id] < minimum_relevance
            and query_has_cjk
            != (re.search(r"[\u3400-\u9fff]", record.content) is not None)
        ]
        # ponytail: one provider request supports 64 inputs; persist vectors if this grows.
        semantic_candidates = sorted(
            semantic_candidates,
            key=lambda record: (record.created_at, record.id),
            reverse=True,
        )[:_MEMORY_EMBEDDING_CANDIDATE_LIMIT]
        semantic_inputs = [query, *(record.content for record in semantic_candidates)]
        semantic_embeddings = (
            self._load_embeddings(run, "memory_query", semantic_inputs)
            if semantic_candidates
            else []
        )
        semantic_scores = (
            {
                record.id: cosine_similarity(semantic_embeddings[0], embedding)
                for record, embedding in zip(
                    semantic_candidates,
                    semantic_embeddings[1:],
                )
            }
            if len(semantic_embeddings) == len(semantic_inputs)
            else {}
        )
        records = [
            record
            for record in records
            if (
                record.scope_type != MemoryScopeType.USER
                and record.metadata.get("source") != "agent_session_summary"
            )
            or record.metadata.get("pinned") is True
            or record.metadata.get("memory_key") in _ALWAYS_APPLY_USER_MEMORY_KEYS
            or lexical_scores[record.id] >= minimum_relevance
            or semantic_scores.get(record.id, 0)
            >= _MEMORY_SEMANTIC_RELEVANCE_THRESHOLD
        ]

        def finite(value, default: float) -> float:
            try:
                resolved = float(value)
            except (TypeError, ValueError):
                return default
            return resolved if math.isfinite(resolved) else default

        def score(record) -> float:
            importance = min(
                max(finite(record.metadata.get("importance", 1.0), 1.0), 0.0),
                1.0,
            )
            half_life_days = finite(
                record.metadata.get(
                    "half_life_days", _DEFAULT_MEMORY_HALF_LIFE_DAYS
                ),
                _DEFAULT_MEMORY_HALF_LIFE_DAYS,
            )
            if half_life_days <= 0:
                half_life_days = _DEFAULT_MEMORY_HALF_LIFE_DAYS
            confidence = min(max(finite(record.confidence, 1.0), 0.0), 1.0)
            age_days = max((now - record.created_at).total_seconds(), 0.0) / 86400
            retention = (
                1.0
                if record.metadata.get("pinned") is True
                else 0.5 ** (age_days / half_life_days)
            )
            return confidence * importance * retention

        # ponytail: 当前规模直接读时排序；记忆量显著增长后再下推到检索层。
        return sorted(
            records,
            key=lambda record: (score(record), record.created_at, record.id),
            reverse=True,
        )[:_MEMORY_CONTEXT_LIMIT]

    def _context_event_payload(self, context: AgentRetrievedContext) -> dict:
        return {
            "knowledge_result_count": len(context.knowledge_results),
            "memory_record_count": len(context.memory_records),
            "context_sensitivity_level": self._context_sensitivity_level(context),
            "knowledge_document_ids": [
                result.document_id for result in context.knowledge_results
            ],
            "knowledge_chunk_ids": [
                result.chunk_id for result in context.knowledge_results
            ],
            "memory_ids": [record.id for record in context.memory_records],
        }

    def _context_model_message(
        self, context: AgentRetrievedContext
    ) -> ModelMessage | None:
        lines: list[str] = []
        if context.knowledge_results:
            lines.append("Retrieved knowledge context:")
            for result in context.knowledge_results:
                lines.append(
                    f"- document_id={result.document_id}; chunk_id={result.chunk_id}; "
                    f"source={result.source_uri}; excerpt={result.excerpt}"
                )
        if context.memory_records:
            lines.append("Reviewed long-term memory:")
            lines.append(
                "Use only records directly relevant to the current request. If none directly "
                "answers it, say you do not know without mentioning unrelated memories. Never "
                "enumerate private memory unless the user explicitly asks to review it."
            )
            for record in context.memory_records:
                memory_key = record.metadata.get("memory_key")
                key_text = (
                    f"key={memory_key}; "
                    if memory_key
                    and str(memory_key).lower()
                    not in {"fact", "general", "legacy", "memory"}
                    else ""
                )
                lines.append(
                    f"- memory_id={record.id}; scope={record.scope_type.value}:{record.scope_id}; "
                    f"{key_text}content={record.content}"
                )
        if not lines:
            return None
        return ModelMessage(role="system", content="\n".join(lines))

    def _context_sensitivity_level(self, context: AgentRetrievedContext) -> int:
        levels = [result.sensitivity_level for result in context.knowledge_results] + [
            record.sensitivity_level for record in context.memory_records
        ]
        if not levels:
            return 0
        return max(levels)

    def _record_tool_execution_error(
        self,
        state: AgentRuntimeState,
        run: Run,
        step: PlanStep,
        error: ToolExecutionError,
        attempt: int,
    ) -> None:
        if not any(
            event.type == "tool_call.failed"
            and event.payload.get("step_id") == step.id
            for event in self.store.list_run_events(run.tenant_id, run.id)
        ):
            self.store.append_run_event(
                run,
                "tool_call.failed",
                {
                    "step_id": step.id,
                    "tool_name": step.tool_name,
                    "status": "failed",
                    "summary": f"{step.tool_name} failed",
                },
            )
        self._record_tool_failure(state, step, str(error), attempt)

    def _fail_for_tool_execution_error(
        self,
        state: AgentRuntimeState,
        run: Run,
        step: PlanStep,
        error: ToolExecutionError,
        attempt: int,
    ) -> AgentRuntimeState:
        self._record_tool_execution_error(state, run, step, error, attempt)
        return AgentExecutionServices(self)._fail(
            state,
            run,
            "tool_execution_error",
            detail=str(error),
            metadata={"step_id": step.id, "error": str(error)},
        )

    def _prepare_step_for_execution(
        self,
        state: AgentRuntimeState,
        step: PlanStep,
    ) -> PlanStep:
        if step.tool_name == "sandbox.command" and self.sandbox_adapter is not None:
            session_id = self._ensure_sandbox_session(state).id
        elif step.tool_name == "browser.action" and self.browser_controller is not None:
            session_id = self._ensure_browser_session(state).session_id
        else:
            return step
        cwd = None
        if step.tool_name == "sandbox.command":
            skill = state.runtime_metadata.get("loaded_skill_context", {}).get(
                step.skill_id or ""
            )
            if skill and "cwd" not in step.tool_input:
                cwd = skill.get("root_path")
        if step.tool_input.get("session_id") == session_id and cwd is None:
            return step
        updated_input = dict(step.tool_input)
        updated_input["session_id"] = session_id
        if cwd is not None:
            updated_input["cwd"] = cwd
        updated_step = step.model_copy(update={"tool_input": updated_input})
        state.plan = [
            updated_step if existing.id == step.id else existing
            for existing in state.plan
        ]
        self._save_state(state)
        return updated_step

    def _ensure_sandbox_session(self, state: AgentRuntimeState):
        if self.sandbox_adapter is None:
            raise ToolExecutionError(
                "runtime sandbox adapter is not configured for automatic sandbox sessions"
            )
        run = self.store.get_run(state.tenant_id, state.run_id)
        if run.status == RunStatus.CANCELLED:
            state.status = RunStatus.CANCELLED
            self._save_state(state)
            raise ToolExecutionError("Run cancelled")
        thread = self._sandbox_thread(run)
        reused = False
        if state.sandbox_session_id is None and thread is not None:
            state.sandbox_session_id = thread.sandbox_session_id
            reused = state.sandbox_session_id is not None
        if state.sandbox_session_id is not None:
            try:
                session = self.sandbox_adapter.get_session(
                    state.tenant_id,
                    state.sandbox_session_id,
                )
            except NotFoundError:
                if thread is None or thread.sandbox_session_id != state.sandbox_session_id:
                    raise
                self.store.update_chat_thread(
                    run.tenant_id,
                    thread.id,
                    sandbox_session_id=None,
                )
                state.sandbox_session_id = None
            else:
                if thread is not None:
                    if (
                        session.workspace_id != run.workspace_id
                        or session.metadata.get("taroai_thread_id") != thread.id
                    ):
                        raise ToolExecutionError(
                            "Thread sandbox session is outside the current thread scope"
                        )
                    if thread.sandbox_session_id != session.id:
                        raise ToolExecutionError(
                            "Thread sandbox session reference changed during the run"
                        )
                if reused:
                    self.store.append_run_event(
                        run,
                        "sandbox.session.reused",
                        {"session_id": session.id, "provider": session.provider},
                    )
                    self._save_state(state)
                self._materialize_run_attachments(state, session)
                self._materialize_runtime_snapshot_files(
                    state, session, self._agent_runtime_snapshot(state)
                )
                self._ensure_coding_workspace(run, state)
                return session
        self._enforce_sandbox_concurrency_license(state)
        runtime_snapshot = self._agent_runtime_snapshot(state)
        snapshot_network_mode = str(runtime_snapshot.get("network_mode") or "")
        network_mode = self.sandbox_network_mode
        if snapshot_network_mode:
            requested_network_mode = SandboxNetworkMode(snapshot_network_mode)
            network_rank = {
                SandboxNetworkMode.DISABLED: 0,
                SandboxNetworkMode.ALLOWLIST: 1,
                SandboxNetworkMode.OPEN: 2,
            }
            network_mode = min(
                (self.sandbox_network_mode, requested_network_mode),
                key=lambda item: network_rank[item],
            )
        snapshot_timeout = int(runtime_snapshot.get("timeout_seconds") or self.sandbox_timeout_seconds)
        timeout_seconds = min(self.sandbox_timeout_seconds, max(1, snapshot_timeout))
        session = self.sandbox_adapter.create(
            SandboxCreateRequest(
                tenant_id=state.tenant_id,
                workspace_id=state.workspace_id,
                run_id=state.run_id,
                thread_id=run.thread_id,
                image=str(
                    state.runtime_metadata.get("skill_runtime_image")
                    or runtime_snapshot.get("image")
                    or self.sandbox_runtime_image
                ),
                network_mode=network_mode,
                timeout_seconds=timeout_seconds,
                metadata={
                    "created_by": "agent_runtime",
                    "agent_runtime_snapshot": bool(runtime_snapshot),
                    **(
                        {"taroai_thread_id": run.thread_id}
                        if thread is not None
                        else {}
                    ),
                },
            )
        )
        state.sandbox_session_id = session.id
        if thread is not None:
            self.store.update_chat_thread(
                run.tenant_id,
                thread.id,
                sandbox_session_id=session.id,
            )
        run = self.store.get_run(state.tenant_id, state.run_id)
        self.store.append_run_event(
            run,
            "sandbox.session.created",
            {
                "session_id": session.id,
                "provider": session.provider,
                "network_mode": session.network_mode.value,
            },
        )
        # 先暴露会话 ID，取消请求才能在命令启动前立即销毁远端会话。
        self._save_state(state)
        if self.store.get_run(state.tenant_id, state.run_id).status == RunStatus.CANCELLED:
            state.status = RunStatus.CANCELLED
            self._destroy_runtime_sandbox_session(
                state,
                reason="cancelled",
                force=True,
            )
            self._save_state(state)
            raise ToolExecutionError("Run cancelled")
        self._materialize_run_attachments(state, session)
        self._materialize_runtime_snapshot_files(state, session, runtime_snapshot)
        self._ensure_coding_workspace(run, state)
        self._save_state(state)
        return session

    def _sandbox_thread(self, run: Run):
        adapter = self.sandbox_adapter
        if (
            adapter is None
            or adapter.provider != "e2b"
            or not callable(getattr(adapter, "pause", None))
            or run.thread_id is None
        ):
            return None
        return self.store.get_chat_thread(run.tenant_id, run.thread_id)

    def release_thread_sandbox(self, tenant_id: str, thread_id: str) -> bool:
        adapter = self.sandbox_adapter
        if (
            adapter is None
            or adapter.provider != "e2b"
            or not callable(getattr(adapter, "pause", None))
        ):
            return True
        thread = self.store.get_chat_thread(tenant_id, thread_id)
        if thread.sandbox_session_id is None:
            return True
        try:
            adapter.destroy(tenant_id, thread.sandbox_session_id)
        except NotFoundError:
            pass
        except (SandboxExecutionError, SandboxProviderUnavailableError):
            return False
        self.store.update_chat_thread(
            tenant_id,
            thread_id,
            sandbox_session_id=None,
        )
        return True

    def _agent_runtime_snapshot(self, state: AgentRuntimeState) -> dict[str, Any]:
        context = state.runtime_metadata.get("agent_context")
        if isinstance(context, dict) and isinstance(context.get("runtime_snapshot"), dict):
            return context["runtime_snapshot"]
        if self.agent_registry is not None:
            run = self.store.get_run(state.tenant_id, state.run_id)
            references = [item for item in run.resource_refs if item.type == "agent"]
            agent_id = references[0].id if references else run.agent_id
            if agent_id:
                try:
                    definition = self.agent_registry.get(run.tenant_id, agent_id)
                except NotFoundError:
                    return {}
                version_number = (
                    int(references[0].version)
                    if references and references[0].version
                    else definition.published_version
                )
                if version_number is not None:
                    return self.agent_registry.get_version(
                        run.tenant_id, agent_id, version_number
                    ).spec.runtime_snapshot
        return {}

    def _materialize_runtime_snapshot_files(
        self,
        state: AgentRuntimeState,
        session,
        runtime_snapshot: dict[str, Any],
    ) -> None:
        if state.runtime_metadata.get("runtime_snapshot_materialized"):
            return
        sandbox_adapter = self.sandbox_adapter
        if sandbox_adapter is None:
            raise ToolExecutionError("runtime sandbox adapter is not configured")
        run = self.store.get_run(state.tenant_id, state.run_id)
        materialized = []
        agent_context = state.runtime_metadata.get("agent_context")
        if isinstance(agent_context, dict) and agent_context.get("instructions"):
            for path, content, content_type in (
                (
                    "/workspace/agent/SKILL.md",
                    str(agent_context["instructions"]),
                    "text/markdown",
                ),
                (
                    "/workspace/agent/config.json",
                    json.dumps(
                        {
                            "agent_id": agent_context.get("agent_id"),
                            "name": agent_context.get("name"),
                            "version": agent_context.get("version"),
                            "app_kind": agent_context.get("app_kind"),
                            "write_autonomy": agent_context.get("write_autonomy"),
                            "input_schema": agent_context.get("input_schema", {}),
                            "output_contract": agent_context.get("output_contract", {}),
                        },
                        ensure_ascii=False,
                    ),
                    "application/json",
                ),
            ):
                sandbox_adapter.upload_file(
                    SandboxFileWrite(
                        tenant_id=run.tenant_id,
                        workspace_id=run.workspace_id,
                        run_id=run.id,
                        thread_id=run.thread_id,
                        session_id=session.id,
                        path=path,
                        content_base64=base64.b64encode(content.encode()).decode("ascii"),
                        content_type=content_type,
                    )
                )
                materialized.append({"sandbox_path": path, "size_bytes": len(content)})
        if self.storage_catalog is not None and self.object_storage is not None:
            for item in runtime_snapshot.get("files", []):
                storage_object_id = str(item.get("storage_object_id") or "")
                path = str(item.get("sandbox_path") or "")
                if (
                    not storage_object_id
                    or not path.startswith("/workspace/")
                    or path.startswith("/workspace/inputs/")
                    or path.startswith("/workspace/artifacts/")
                    or ".." in path.split("/")
                ):
                    raise ToolExecutionError(
                        "Runtime snapshot contains an unsafe sandbox path"
                    )
                storage_object = self.storage_catalog.get(
                    run.tenant_id, storage_object_id
                )
                if storage_object.workspace_id != run.workspace_id:
                    raise ToolExecutionError(
                        "Runtime snapshot file is outside the Agent workspace"
                    )
                content = self.object_storage.download(storage_object).content
                sandbox_adapter.upload_file(
                    SandboxFileWrite(
                        tenant_id=run.tenant_id,
                        workspace_id=run.workspace_id,
                        run_id=run.id,
                        thread_id=run.thread_id,
                        session_id=session.id,
                        path=path,
                        content_base64=base64.b64encode(content).decode("ascii"),
                        content_type=storage_object.content_type,
                    )
                )
                materialized.append({**item, "size_bytes": len(content)})
        if isinstance(agent_context, dict):
            manifest_path = "/workspace/agent/app-files.json"
            manifest = json.dumps(
                [
                    {
                        "name": str(item["sandbox_path"]).rsplit("/", 1)[-1],
                        "path": item["sandbox_path"],
                    }
                    for item in materialized
                ],
                ensure_ascii=False,
                indent=2,
            )
            sandbox_adapter.upload_file(
                SandboxFileWrite(
                    tenant_id=run.tenant_id,
                    workspace_id=run.workspace_id,
                    run_id=run.id,
                    thread_id=run.thread_id,
                    session_id=session.id,
                    path=manifest_path,
                    content_base64=base64.b64encode(manifest.encode()).decode("ascii"),
                    content_type="application/json",
                )
            )
            materialized.append(
                {"sandbox_path": manifest_path, "size_bytes": len(manifest)}
            )
        state.runtime_metadata["runtime_snapshot_materialized"] = True
        state.runtime_metadata["restored_runtime_files"] = materialized
        if materialized:
            self.store.append_run_event(
                run,
                "agent.runtime_snapshot.restored",
                {"session_id": session.id, "files": materialized},
            )
        self._save_state(state)

    def _capture_reusable_runtime_snapshot(
        self,
        state: AgentRuntimeState,
        run: Run,
    ) -> None:
        if state.runtime_metadata.get("runtime_snapshot"):
            return
        if (
            self.sandbox_adapter is None
            or self.storage_catalog is None
            or self.object_storage is None
            or state.sandbox_session_id is None
        ):
            return
        try:
            session = self.sandbox_adapter.get_session(
                state.tenant_id, state.sandbox_session_id
            )
            files = self.sandbox_adapter.list_files(
                state.tenant_id, state.sandbox_session_id
            )
        except (NotFoundError, SandboxExecutionError, SandboxProviderUnavailableError):
            self._clear_thread_sandbox_reference(state)
            return
        captured: list[dict[str, Any]] = []
        total_bytes = 0
        for file_ref in files:
            path = file_ref.path
            if (
                path.startswith("/workspace/inputs/")
                or path.startswith("/workspace/artifacts/")
                or path.startswith("/workspace/.taroai/")
                or path.startswith("/workspace/agent/")
                or "/node_modules/" in path
                or "/.git/" in path
                or len(captured) >= 128
                or file_ref.size_bytes > 1_000_000
                or total_bytes + file_ref.size_bytes > 5_000_000
            ):
                continue
            try:
                downloaded = self.sandbox_adapter.download_file(
                    state.tenant_id, state.sandbox_session_id, path
                )
            except Exception:
                continue
            if downloaded.content is None and downloaded.content_base64 is None:
                continue
            content = downloaded.content_bytes()
            filename = path.removeprefix("/workspace/")
            storage_object = self.storage_catalog.register(
                StorageObjectCreate(
                    tenant_id=run.tenant_id,
                    workspace_id=run.workspace_id,
                    run_id=run.id,
                    purpose=StoragePurpose.SANDBOX_FILE,
                    filename=filename,
                    content_type=downloaded.content_type,
                    size_bytes=len(content),
                )
            )
            try:
                self._scan_sandbox_artifact_content(run, storage_object, content)
                self.object_storage.upload(storage_object, content)
            except Exception:
                self._mark_storage_object_deleted(run.tenant_id, storage_object)
                continue
            total_bytes += len(content)
            captured.append(
                {
                    "storage_object_id": storage_object.id,
                    "sandbox_path": path,
                    "content_type": storage_object.content_type,
                    "size_bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            )
        snapshot = {
            "provider": session.provider,
            "image": session.image,
            "network_mode": session.network_mode.value,
            "timeout_seconds": session.timeout_seconds,
            "files": captured,
            "source_run_id": run.id,
        }
        state.runtime_metadata["runtime_snapshot"] = snapshot
        self.store.append_run_event(
            run,
            "agent.runtime_snapshot.captured",
            {
                "session_id": session.id,
                "file_count": len(captured),
                "size_bytes": total_bytes,
                "image": session.image,
                "network_mode": session.network_mode.value,
            },
        )
        self._save_state(state)

    def _attachment_descriptors(self, run: Run) -> list[dict[str, Any]]:
        if self.storage_catalog is None:
            return []
        storage_object_ids = list(run.attachments)
        storage_object_ids.extend(
            reference.id
            for reference in run.resource_refs
            if reference.type == "file" and reference.id not in storage_object_ids
        )
        descriptors: list[dict[str, Any]] = []
        used_names: set[str] = set()
        for storage_object_id in storage_object_ids:
            storage_object = self.storage_catalog.get(
                run.tenant_id, storage_object_id
            )
            if storage_object.workspace_id != run.workspace_id:
                raise ToolExecutionError(
                    f"Attachment is not available in this workspace: {storage_object_id}"
                )
            source_name = storage_object.filename.replace("\\", "/").rsplit("/", 1)[-1]
            safe_name = re.sub(r"[^A-Za-z0-9._ -]", "_", source_name).strip(" .")
            if not safe_name:
                safe_name = f"file-{storage_object.id[-8:]}"
            if safe_name in used_names:
                stem, dot, suffix = safe_name.rpartition(".")
                safe_name = (
                    f"{stem or suffix}-{storage_object.id[-8:]}{dot}{suffix if dot else ''}"
                )
            used_names.add(safe_name)
            descriptors.append(
                {
                    "storage_object_id": storage_object.id,
                    "filename": storage_object.filename,
                    "content_type": storage_object.content_type,
                    "size_bytes": storage_object.size_bytes,
                    "sandbox_path": f"/workspace/inputs/{safe_name}",
                }
            )
        return descriptors

    def _materialize_run_attachments(self, state: AgentRuntimeState, session) -> None:
        if state.runtime_metadata.get("attachments_materialized"):
            return
        if self.object_storage is None or self.storage_catalog is None:
            return
        sandbox_adapter = self.sandbox_adapter
        if sandbox_adapter is None:
            raise ToolExecutionError("runtime sandbox adapter is not configured")
        run = self.store.get_run(state.tenant_id, state.run_id)
        descriptors = self._attachment_descriptors(run)
        for descriptor in descriptors:
            storage_object = self.storage_catalog.get(
                run.tenant_id, descriptor["storage_object_id"]
            )
            content = self.object_storage.download(storage_object).content
            sandbox_adapter.upload_file(
                SandboxFileWrite(
                    tenant_id=run.tenant_id,
                    workspace_id=run.workspace_id,
                    run_id=run.id,
                    thread_id=run.thread_id,
                    session_id=session.id,
                    path=descriptor["sandbox_path"],
                    content_base64=base64.b64encode(content).decode("ascii"),
                    content_type=storage_object.content_type,
                )
            )
        state.runtime_metadata["attachments_materialized"] = True
        state.runtime_metadata["materialized_attachments"] = descriptors
        if descriptors:
            self.store.append_run_event(
                run,
                "run.attachments.materialized",
                {
                    "session_id": session.id,
                    "count": len(descriptors),
                    "files": descriptors,
                },
            )
        self._save_state(state)

    def _enforce_sandbox_concurrency_license(
        self,
        state: AgentRuntimeState,
    ) -> None:
        if self.license_service is None or self.sandbox_adapter is None:
            return
        active_session_count = len(
            [
                session
                for session in self.sandbox_adapter.list_sessions(state.tenant_id)
                if session.status == SandboxSessionStatus.ACTIVE
            ]
        )
        try:
            self.license_service.require_entitlement(
                tenant_id=state.tenant_id,
                feature=LicensedFeature.SANDBOX_CONCURRENCY,
                requested_amount=active_session_count + 1,
            )
        except LicenseEntitlementDeniedError as error:
            raise ToolExecutionError(str(error)) from error

    def _ensure_browser_session(self, state: AgentRuntimeState):
        if self.browser_controller is None:
            raise ToolExecutionError(
                "runtime browser controller is not configured for automatic browser sessions"
            )
        if state.browser_session_id is not None:
            return self.browser_controller.get_session(
                state.tenant_id,
                state.browser_session_id,
            )
        run = self.store.get_run(state.tenant_id, state.run_id)
        profile_id = self._browser_profile_id(state, run)
        if self.browser_profile_service is not None:
            record = self.browser_profile_service.open_session(
                tenant_id=state.tenant_id,
                workspace_id=state.workspace_id,
                profile_id=profile_id,
                run_id=state.run_id,
                user_id=run.user_id,
            )
            state.browser_session_id = record.session_id
            session = self.browser_controller.get_session(
                state.tenant_id, record.session_id
            )
        else:
            session = self.browser_controller.open_session(
                tenant_id=state.tenant_id,
                workspace_id=state.workspace_id,
                run_id=state.run_id,
                session_id=new_id("browser"),
            )
            state.browser_session_id = session.session_id
        self.store.append_run_event(
            run,
            "browser.session.created",
            {
                "session_id": session.session_id,
                "current_url": session.current_url,
                "profile_id": profile_id,
            },
        )
        self._save_state(state)
        return session

    def _browser_profile_id(self, state: AgentRuntimeState, run: Run) -> str | None:
        explicit = next(
            (item.id for item in run.resource_refs if item.type == "browser_profile"),
            None,
        )
        if explicit:
            return explicit
        snapshot = self._agent_runtime_snapshot(state)
        value = snapshot.get("browser_profile_id")
        return str(value) if value else None

    def _record_sandbox_command_event(
        self,
        run: Run,
        step: PlanStep,
        result: ToolResult,
    ) -> None:
        output = result.output if isinstance(result.output, dict) else {}
        self.store.append_run_event(
            run,
            "sandbox.command.executed",
            {
                "step_id": step.id,
                "session_id": output.get("session_id"),
                "exit_code": output.get("exit_code"),
                "stdout_length": len(str(output.get("stdout", ""))),
                "stderr_length": len(str(output.get("stderr", ""))),
                "output_uri": output.get("output_uri"),
                "storage_object_id": output.get("storage_object_id"),
            },
        )

    def _persist_sandbox_command_output(
        self,
        state: AgentRuntimeState,
        result: ToolResult,
    ) -> ToolResult:
        if (
            not isinstance(result.output, dict)
            or result.output.get("output_uri")
            or self.storage_catalog is None
            or self.object_storage is None
        ):
            return result
        output = dict(result.output)
        content = json.dumps(
            {
                key: output.get(key)
                for key in (
                    "session_id",
                    "workspace_id",
                    "run_id",
                    "command",
                    "exit_code",
                    "stdout",
                    "stderr",
                    "created_at",
                )
            },
            sort_keys=True,
        ).encode("utf-8")
        session_id = str(
            output.get("session_id") or state.sandbox_session_id or "sandbox"
        )
        storage_object = self.storage_catalog.register(
            StorageObjectCreate(
                tenant_id=state.tenant_id,
                workspace_id=state.workspace_id,
                run_id=state.run_id,
                purpose=StoragePurpose.SANDBOX_COMMAND_OUTPUT,
                filename=f"{session_id}-{new_id('output')}.json",
                content_type="application/json",
                size_bytes=len(content),
            )
        )
        self.object_storage.upload(storage_object, content)
        output["output_uri"] = storage_object.uri
        output["storage_object_id"] = storage_object.id
        return result.model_copy(update={"output": output})

    def _sandbox_command_failed_exit_code(self, result: ToolResult) -> int | None:
        output = result.output if isinstance(result.output, dict) else {}
        exit_code = output.get("exit_code")
        if exit_code is None:
            return None
        try:
            normalized_exit_code = int(str(exit_code))
        except (TypeError, ValueError):
            return None
        if normalized_exit_code == 0:
            return None
        return normalized_exit_code

    def _safe_tool_result_payload(
        self,
        step: PlanStep,
        result: ToolResult,
    ) -> dict[str, Any]:
        output = result.output if isinstance(result.output, dict) else {}
        if step.tool_name == "sandbox.command":
            return {
                "tool_name": result.tool_name,
                "output": {
                    "session_id": output.get("session_id"),
                    "exit_code": output.get("exit_code"),
                    "stdout_length": len(str(output.get("stdout", ""))),
                    "stderr_length": len(str(output.get("stderr", ""))),
                    "output_uri": output.get("output_uri"),
                },
            }
        if step.tool_name == "browser.action":
            return {
                "tool_name": result.tool_name,
                "output": {
                    "session_id": output.get("session_id"),
                    "action_type": output.get("action_type"),
                    "current_url": output.get("current_url"),
                    "screenshot_uri": output.get("screenshot_uri"),
                    "storage_object_id": output.get("storage_object_id"),
                    "text_length": len(str(output.get("text", ""))),
                },
            }
        if step.tool_name == "web.search":
            results = [
                {
                    key: str(item[key])[:limit]
                    for key, limit in (
                        ("title", 500),
                        ("url", 2000),
                        ("published_date", 100),
                    )
                    if item.get(key)
                }
                for item in output.get("results", [])
                if isinstance(item, dict)
                and str(item.get("url", "")).startswith(("https://", "http://"))
            ]
            return {
                "tool_name": result.tool_name,
                "output": {
                    "query": str(output.get("query", ""))[:1000],
                    "results": results,
                },
            }
        if step.tool_name == "web.fetch":
            return {
                "tool_name": result.tool_name,
                "output": {
                    "url": str(output.get("url", ""))[:2000],
                    "content_length": len(str(output.get("content", ""))),
                },
            }
        return {
            "tool_name": result.tool_name,
            "output_keys": sorted(str(key) for key in output.keys()),
            "output_field_count": len(output),
        }

    def _record_browser_action_event(
        self,
        run: Run,
        step: PlanStep,
        result: ToolResult,
    ) -> None:
        output = result.output if isinstance(result.output, dict) else {}
        self.store.append_run_event(
            run,
            "browser.action.performed",
            {
                "step_id": step.id,
                "session_id": output.get("session_id"),
                "action_type": output.get("action_type"),
                "current_url": output.get("current_url"),
                "screenshot_uri": output.get("screenshot_uri"),
                "storage_object_id": output.get("storage_object_id"),
                "text_length": len(str(output.get("text", ""))),
            },
        )

    def _promote_browser_screenshot(
        self,
        state: AgentRuntimeState,
        result: ToolResult,
    ) -> ToolResult:
        if not isinstance(result.output, dict):
            return result
        output = dict(result.output)
        encoded_content = output.pop("screenshot_content_base64", None)
        if not encoded_content:
            return result.model_copy(update={"output": output})
        if self.storage_catalog is None or self.object_storage is None:
            return result.model_copy(update={"output": output})
        try:
            content = base64.b64decode(str(encoded_content), validate=True)
        except ValueError:
            return result.model_copy(update={"output": output})
        session_id = str(
            output.get("session_id") or state.browser_session_id or "browser"
        )
        storage_object = self.storage_catalog.register(
            StorageObjectCreate(
                tenant_id=state.tenant_id,
                workspace_id=state.workspace_id,
                run_id=state.run_id,
                purpose=StoragePurpose.BROWSER_SCREENSHOT,
                filename=f"{session_id}.png",
                content_type="image/png",
                size_bytes=len(content),
            )
        )
        self.object_storage.upload(storage_object, content)
        output["screenshot_uri"] = storage_object.uri
        output["storage_object_id"] = storage_object.id
        run = self.store.get_run(state.tenant_id, state.run_id)
        self.store.append_run_event(
            run,
            "browser.screenshot.uploaded",
            {
                "session_id": session_id,
                "storage_object_id": storage_object.id,
                "screenshot_uri": storage_object.uri,
                "size_bytes": len(content),
            },
        )
        return result.model_copy(update={"output": output})

    def _promote_sandbox_artifacts(
        self,
        state: AgentRuntimeState,
        step: PlanStep,
    ) -> None:
        if (
            self.sandbox_adapter is None
            or self.storage_catalog is None
            or self.object_storage is None
            or state.sandbox_session_id is None
        ):
            return
        run = self.store.get_run(state.tenant_id, state.run_id)
        listed_files = self.sandbox_adapter.list_files(
            state.tenant_id,
            state.sandbox_session_id,
        )
        files_by_path = {file_ref.path: file_ref for file_ref in listed_files}
        explicit_paths = self._sandbox_artifact_paths(step)
        paths = explicit_paths or [
            file_ref.path
            for file_ref in listed_files
            if file_ref.path not in state.promoted_sandbox_artifact_paths
            and self._is_auto_discoverable_sandbox_artifact_path(file_ref.path)
        ]
        if paths and not explicit_paths:
            self.store.append_run_event(
                run,
                "sandbox.artifacts.discovered",
                {"paths": paths},
            )
        for path in paths:
            if not self._is_publishable_sandbox_artifact_path(path):
                metadata = {
                    "path": path,
                    "allowed_prefix": "/workspace/artifacts/",
                }
                self.store.append_run_event(
                    run,
                    "sandbox.artifact.rejected",
                    metadata,
                )
                raise _RuntimeSandboxArtifactPathRejected(metadata)
            if path in state.promoted_sandbox_artifact_paths:
                continue
            listed_file = files_by_path.get(path)
            if (
                listed_file is not None
                and listed_file.size_bytes > self.sandbox_artifact_max_bytes
            ):
                self._reject_oversized_sandbox_artifact(run, path, listed_file.size_bytes)
            file_ref = self.sandbox_adapter.download_file(
                state.tenant_id,
                state.sandbox_session_id,
                path,
            )
            filename = self._sandbox_artifact_filename(file_ref.path)
            content = file_ref.content_bytes()
            if len(content) > self.sandbox_artifact_max_bytes:
                self._reject_oversized_sandbox_artifact(run, path, len(content))
            storage_object = self.storage_catalog.register(
                StorageObjectCreate(
                    tenant_id=state.tenant_id,
                    workspace_id=state.workspace_id,
                    run_id=state.run_id,
                    purpose=StoragePurpose.ARTIFACT,
                    filename=filename,
                    content_type=file_ref.content_type,
                    size_bytes=len(content),
                )
            )
            try:
                artifact = self._apply_artifact_guardrails(
                    run,
                    {
                        "name": filename,
                        "artifact_type": self._sandbox_artifact_type(
                            filename,
                            file_ref.content_type,
                        ),
                        "uri": storage_object.uri,
                    },
                    state.approved_guardrail_keys,
                    content=content.decode("utf-8", errors="replace"),
                )
            except (
                _RuntimeGuardrailApprovalRequired,
                _RuntimeGuardrailViolation,
            ):
                self._mark_storage_object_deleted(run.tenant_id, storage_object)
                raise
            self._scan_sandbox_artifact_content(run, storage_object, content)
            self.object_storage.upload(storage_object, content)
            self.store.create_artifact(
                tenant_id=state.tenant_id,
                run_id=state.run_id,
                name=artifact["name"],
                artifact_type=artifact["artifact_type"],
                uri=artifact["uri"],
                storage_object_id=storage_object.id,
                content_type=file_ref.content_type,
                size_bytes=len(content),
            )
            state.promoted_sandbox_artifact_paths.append(path)
            self.store.append_run_event(
                run,
                "sandbox.artifact.promoted",
                {
                    "path": path,
                    "artifact_name": artifact["name"],
                    "storage_object_id": storage_object.id,
                },
            )
        self._save_state(state)

    def _mark_storage_object_deleted(self, tenant_id: str, storage_object) -> None:
        mark_deleted = getattr(self.storage_catalog, "mark_deleted", None)
        if callable(mark_deleted):
            mark_deleted(
                tenant_id,
                storage_object.id,
                utc_now(),
            )

    def _reject_oversized_sandbox_artifact(
        self,
        run: Run,
        path: str,
        size_bytes: int,
    ) -> None:
        self.store.append_run_event(
            run,
            "sandbox.artifact.rejected",
            {
                "path": path,
                "reason": "size_limit",
                "size_bytes": size_bytes,
                "max_bytes": self.sandbox_artifact_max_bytes,
            },
        )
        raise SandboxExecutionError(
            f"sandbox artifact exceeds the {self.sandbox_artifact_max_bytes}-byte size limit"
        )

    def _is_publishable_sandbox_artifact_path(self, path: str) -> bool:
        return path.startswith("/workspace/artifacts/") and not path.endswith("/")

    def _is_auto_discoverable_sandbox_artifact_path(self, path: str) -> bool:
        return self._is_publishable_sandbox_artifact_path(path)

    def _sandbox_artifact_paths(self, step: PlanStep) -> list[str]:
        values: list[Any] = []
        if "artifact_path" in step.tool_input:
            values.append(step.tool_input["artifact_path"])
        artifact_paths = step.tool_input.get("artifact_paths", [])
        if isinstance(artifact_paths, list):
            values.extend(artifact_paths)
        elif artifact_paths:
            values.append(artifact_paths)
        paths: list[str] = []
        for value in values:
            path = str(value).strip()
            if path and path not in paths:
                paths.append(path)
        return paths

    def _sandbox_artifact_filename(self, path: str) -> str:
        filename = path.rstrip("/").rsplit("/", 1)[-1].strip()
        return filename or "sandbox-artifact"

    def _sandbox_artifact_type(self, filename: str, content_type: str) -> str:
        if filename.endswith((".md", ".txt")) or content_type.startswith("text/"):
            return "document"
        return "file"

    def _scan_sandbox_artifact_content(
        self,
        run: Run,
        storage_object,
        content: bytes,
    ) -> None:
        if self.storage_content_scanner is None:
            return
        scan_result = self.storage_content_scanner.scan(
            StorageContentScanRequest(
                storage_object=storage_object,
                content=content,
            )
        )
        if scan_result.allowed:
            return
        metadata = {
            "storage_object_id": storage_object.id,
            "filename": storage_object.filename,
            "content_type": storage_object.content_type,
            "size_bytes": len(content),
            "matched_term_count": scan_result.matched_term_count,
        }
        self.store.append_run_event(
            run,
            "storage.content_rejected",
            metadata,
        )
        self._record_audit_event(
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            user_id=run.user_id,
            run_id=run.id,
            event_type="storage.content_rejected",
            metadata=metadata,
        )
        mark_deleted = getattr(self.storage_catalog, "mark_deleted", None)
        if callable(mark_deleted):
            mark_deleted(
                run.tenant_id,
                storage_object.id,
                utc_now(),
            )
        raise _RuntimeStorageContentRejected(metadata)

    def _fail_for_storage_content_rejection(
        self,
        state: AgentRuntimeState,
        run: Run,
        step: PlanStep,
        error: _RuntimeStorageContentRejected,
    ) -> AgentRuntimeState:
        return AgentExecutionServices(self)._fail(
            state,
            run,
            "storage_content_rejected",
            detail=str(error),
            metadata={
                "reason": "storage_content_rejected",
                "step_id": step.id,
            },
        )

    def _fail_for_sandbox_artifact_path_rejection(
        self,
        state: AgentRuntimeState,
        run: Run,
        step: PlanStep,
        error: _RuntimeSandboxArtifactPathRejected,
    ) -> AgentRuntimeState:
        return AgentExecutionServices(self)._fail(
            state,
            run,
            "sandbox_artifact_path_rejected",
            detail=str(error),
            metadata={
                "reason": "sandbox_artifact_path_rejected",
                "step_id": step.id,
            },
        )

    def _fail_for_sandbox_command_failure(
        self,
        state: AgentRuntimeState,
        run: Run,
        step: PlanStep,
        exit_code: int,
    ) -> AgentRuntimeState:
        detail = f"sandbox.command failed with exit code {exit_code}"
        return AgentExecutionServices(self)._fail(
            state,
            run,
            "sandbox_command_failed",
            detail=detail,
            metadata={
                "reason": "sandbox_command_failed",
                "step_id": step.id,
                "exit_code": exit_code,
            },
        )

    def _resolve_tool_granted_scopes(
        self,
        state: AgentRuntimeState,
        step: PlanStep,
    ) -> list[str]:
        policy = self.tool_gateway.policies.get(step.tool_name)
        if policy is None or self.policy_service is None:
            return []
        resource = f"tenant:{state.tenant_id}"
        granted_scopes: list[str] = []
        for scope in policy.required_scopes:
            decision = self.policy_service.decide(
                PolicyRequest(
                    tenant_id=state.tenant_id,
                    workspace_id=state.workspace_id,
                    user_id=state.user_id,
                    run_id=state.run_id,
                    action=scope,
                    resource=resource,
                    context={
                        "tool_name": step.tool_name,
                        "skill_id": step.skill_id,
                        "step_id": step.id,
                    },
                )
            )
            if decision.allowed:
                granted_scopes.append(scope)
        return granted_scopes

    def _pause_for_approval(
        self,
        state: AgentRuntimeState,
        step: PlanStep,
        reason: str,
    ) -> AgentRuntimeState:
        connector_parts = step.tool_name.split(".", 2)
        is_connector = len(connector_parts) == 3 and connector_parts[0] == "connector"
        preview_payload = {
            "toolName": step.tool_name,
            "title": step.title,
            "inputKeys": sorted(step.tool_input),
            "input": step.tool_input,
        }
        if is_connector and self.connector_registry is not None:
            connector = self.connector_registry.get_connector(
                state.tenant_id, connector_parts[1]
            )
            capability = next(
                item
                for item in connector.capabilities
                if item.name == connector_parts[2]
            )
            preview_payload.update(
                {
                    "provider": connector.display_name,
                    "connectorId": connector.id,
                    "capability": capability.name,
                    "riskLevel": capability.risk_level,
                }
            )
        approval = self.store.create_approval_request(
            tenant_id=state.tenant_id,
            run_id=state.run_id,
            step_id=step.id,
            reason=reason,
            kind="connector_action" if is_connector else "tool_action",
            subject_type="connector" if is_connector else "tool",
            subject_id=connector_parts[1] if is_connector else step.tool_name,
            preview_payload=preview_payload,
            validation_payload={"valid": True},
        )
        self.store.update_run_status(
            state.tenant_id,
            state.run_id,
            RunStatus.AWAITING_APPROVAL,
            emit_status_event=False,
        )
        state.status = RunStatus.AWAITING_APPROVAL
        state.current_step_id = step.id
        state.approval_id = approval.id
        self.pending_states[state.run_id] = state
        self._save_state(state)
        return state

    def _finalize_success(
        self,
        state: AgentRuntimeState,
        *,
        emit_event: bool = True,
        before_runtime_cleanup: Callable[[], None] | None = None,
    ) -> AgentRuntimeState:
        run = self.store.get_run(state.tenant_id, state.run_id)
        artifacts = self.store.list_artifacts(state.tenant_id, state.run_id)
        if before_runtime_cleanup is not None:
            before_runtime_cleanup()
        self._capture_reusable_runtime_snapshot(state, run)
        if not self._pause_thread_sandbox_session(state, run):
            self._destroy_runtime_sandbox_session(state, reason="success")
        self._destroy_runtime_browser_session(state, reason="success")
        run = self.store.get_run(state.tenant_id, state.run_id)
        if run.status == RunStatus.CANCELLED:
            state.status = RunStatus.CANCELLED
            self._save_state(state)
            return state
        run = self.store.update_run_status(
            state.tenant_id,
            state.run_id,
            RunStatus.SUCCEEDED,
            emit_status_event=False,
        )
        if emit_event:
            payload = {"artifact_name": artifacts[-1].name} if artifacts else {}
            self.store.append_run_event(run, "run.succeeded", payload)
        state.status = RunStatus.SUCCEEDED
        state.current_step_id = None
        self._save_state(state)
        return state

    def _pause_thread_sandbox_session(
        self,
        state: AgentRuntimeState,
        run: Run,
    ) -> bool:
        thread = self._sandbox_thread(run)
        if thread is None or state.sandbox_session_id is None:
            return False
        if thread.sandbox_session_id != state.sandbox_session_id:
            return False
        pause = getattr(self.sandbox_adapter, "pause")
        try:
            pause(state.tenant_id, state.sandbox_session_id)
        except Exception as error:
            self.store.append_run_event(
                run,
                "sandbox.session.pause_failed",
                {
                    "session_id": state.sandbox_session_id,
                    "provider": self.sandbox_adapter.provider,
                    "error_type": error.__class__.__name__,
                },
            )
            # Cleanup is best-effort; retain the thread reference for reuse or TTL expiry.
            return True
        self.store.append_run_event(
            run,
            "sandbox.session.paused",
            {
                "session_id": state.sandbox_session_id,
                "provider": self.sandbox_adapter.provider,
                "thread_id": thread.id,
            },
        )
        return True

    def _clear_thread_sandbox_reference(self, state: AgentRuntimeState) -> None:
        run = self.store.get_run(state.tenant_id, state.run_id)
        thread = self._sandbox_thread(run)
        if (
            thread is not None
            and thread.sandbox_session_id == state.sandbox_session_id
        ):
            self.store.update_chat_thread(
                run.tenant_id,
                thread.id,
                sandbox_session_id=None,
            )
        state.sandbox_session_id = None
        self._save_state(state)

    def _destroy_runtime_sandbox_session(
        self,
        state: AgentRuntimeState,
        reason: str,
        force: bool = False,
    ) -> None:
        if self.sandbox_adapter is None or state.sandbox_session_id is None:
            return
        if not force and not self.sandbox_destroy_on_success:
            return
        run = self.store.get_run(state.tenant_id, state.run_id)
        if reason == "cancelled":
            cancel_event = next(
                (
                    event
                    for event in reversed(
                        self.store.list_run_events(state.tenant_id, state.run_id)
                    )
                    if event.type
                    in {
                        "sandbox.command.cancel_requested",
                        "sandbox.command.cancelled",
                        "sandbox.command.cancel_failed",
                    }
                    and event.payload.get("session_id") == state.sandbox_session_id
                ),
                None,
            )
            thread = self._sandbox_thread(run)
            if (
                cancel_event is not None
                and cancel_event.type
                in {
                    "sandbox.command.cancel_requested",
                    "sandbox.command.cancelled",
                }
                and thread is not None
                and thread.sandbox_session_id == state.sandbox_session_id
            ):
                return
        try:
            session = self.sandbox_adapter.get_session(
                state.tenant_id,
                state.sandbox_session_id,
            )
        except NotFoundError:
            self._clear_thread_sandbox_reference(state)
            return
        if getattr(session.status, "value", session.status) == "destroyed":
            self._clear_thread_sandbox_reference(state)
            return
        try:
            destroyed = self.sandbox_adapter.destroy(
                state.tenant_id,
                state.sandbox_session_id,
            )
        except (SandboxExecutionError, SandboxProviderUnavailableError) as error:
            self.store.append_run_event(
                run,
                "sandbox.session.destroy_failed",
                {
                    "session_id": state.sandbox_session_id,
                    "provider": session.provider,
                    "reason": reason,
                    "error_type": error.__class__.__name__,
                },
            )
            return
        self.store.append_run_event(
            run,
            "sandbox.session.destroyed",
            {
                "session_id": destroyed.id,
                "provider": destroyed.provider,
                "reason": reason,
            },
        )
        self._clear_thread_sandbox_reference(state)

    def _destroy_runtime_browser_session(
        self,
        state: AgentRuntimeState,
        reason: str,
    ) -> None:
        if self.browser_controller is None or state.browser_session_id is None:
            return
        current_url = None
        try:
            if self.browser_profile_service is not None:
                record = self.browser_profile_service.close_session(
                    state.tenant_id, state.browser_session_id
                )
                current_url = record.current_url
            else:
                destroyed = self.browser_controller.delete_session(
                    state.tenant_id,
                    state.browser_session_id,
                )
                current_url = destroyed.current_url
        except NotFoundError:
            return
        except Exception as error:
            run = self.store.get_run(state.tenant_id, state.run_id)
            self.store.append_run_event(
                run,
                "browser.session.destroy_failed",
                {
                    "session_id": state.browser_session_id,
                    "provider": self.browser_controller.provider,
                    "reason": reason,
                    "error_type": error.__class__.__name__,
                },
            )
            return
        run = self.store.get_run(state.tenant_id, state.run_id)
        self.store.append_run_event(
            run,
            "browser.session.destroyed",
            {
                "session_id": state.browser_session_id,
                "current_url": current_url,
                "reason": reason,
            },
        )

    def _save_state(self, state: AgentRuntimeState) -> None:
        if self.store.get_run(state.tenant_id, state.run_id).status == RunStatus.CANCELLED:
            try:
                persisted = self._load_state(state.tenant_id, state.run_id)
            except NotFoundError:
                persisted = None
            state.status = RunStatus.CANCELLED
            state.current_step_id = None
            state.approval_id = None
            state.pending_guardrail_approval_key = None
            state.pending_guardrail_approval_stage = None
            state.failure_reason = "Run cancelled"
            if persisted is not None and persisted.terminal_event_emitted:
                state.terminal_event_emitted = True
        self.store.save_runtime_state(state)

    def _load_state(self, tenant_id: str, run_id: str) -> AgentRuntimeState:
        snapshot = self.store.get_runtime_state(tenant_id, run_id)
        return AgentRuntimeState.model_validate(snapshot.to_runtime_state_payload())

    def _load_or_initial_state(self, run: Run) -> AgentRuntimeState:
        try:
            return self._load_state(run.tenant_id, run.id)
        except NotFoundError:
            return self._initial_state(run)

    def _record_tool_execution(self, state: AgentRuntimeState, step: PlanStep) -> None:
        metadata = {
            "step_id": step.id,
            "tool_name": step.tool_name,
        }
        if step.skill_id is not None:
            metadata["skill_id"] = step.skill_id
        self.store.record_billing_meter(
            tenant_id=state.tenant_id,
            run_id=state.run_id,
            meter_type="tool_call_count",
            quantity=1,
            unit="call",
            skill_id=step.skill_id,
            metadata=metadata,
        )
        if step.skill_id is not None:
            self.store.record_billing_meter(
                tenant_id=state.tenant_id,
                run_id=state.run_id,
                meter_type="skill_call_count",
                quantity=1,
                unit="call",
                skill_id=step.skill_id,
                cost_estimate=self._estimate_billing_cost(
                    meter_type="skill_call_count",
                    quantity=1,
                    unit="call",
                    tenant_id=state.tenant_id,
                    workspace_id=state.workspace_id,
                    skill_id=step.skill_id,
                ),
                metadata=metadata,
            )
        self._record_audit_event(
            tenant_id=state.tenant_id,
            workspace_id=state.workspace_id,
            user_id=state.user_id,
            run_id=state.run_id,
            event_type="tool.executed",
            metadata=metadata,
        )

    def _record_tool_policy_pause(
        self,
        state: AgentRuntimeState,
        step: PlanStep,
        reason: str,
    ) -> None:
        self._record_audit_event(
            tenant_id=state.tenant_id,
            workspace_id=state.workspace_id,
            user_id=state.user_id,
            run_id=state.run_id,
            event_type="tool.approval_required",
            metadata={
                "step_id": step.id,
                "tool_name": step.tool_name,
                "reason": reason,
                "tool_input": self._redact_tool_input(step.tool_input),
            },
        )

    def _record_tool_failure(
        self,
        state: AgentRuntimeState,
        step: PlanStep,
        error: str,
        attempt: int,
    ) -> None:
        self._record_audit_event(
            tenant_id=state.tenant_id,
            workspace_id=state.workspace_id,
            user_id=state.user_id,
            run_id=state.run_id,
            event_type="tool.failed",
            metadata={
                "step_id": step.id,
                "tool_name": step.tool_name,
                "attempt": attempt,
                "error": error,
                "tool_input": self._redact_tool_input(step.tool_input),
            },
        )

    def _redact_tool_input(self, value):
        if isinstance(value, dict):
            redacted = {}
            for key, item in value.items():
                if self._is_sensitive_key(key):
                    redacted[key] = "[REDACTED]"
                else:
                    redacted[key] = self._redact_tool_input(item)
            return redacted
        if isinstance(value, list):
            return [self._redact_tool_input(item) for item in value]
        return value

    def _is_sensitive_key(self, key: str) -> bool:
        normalized = key.lower().replace("-", "_")
        if normalized in {
            "secret",
            "token",
            "password",
            "api_key",
            "apikey",
            "credential",
        }:
            return True
        return normalized.endswith(
            (
                "_secret",
                "_token",
                "_password",
                "_api_key",
                "_apikey",
                "_credential",
            )
        )

    def _record_audit_event(
        self,
        tenant_id: str,
        workspace_id: str | None,
        user_id: str | None,
        run_id: str | None,
        event_type: str,
        metadata: dict[str, Any],
    ) -> None:
        service = self.audit_service or AuditService(store=self.store)
        service.record(
            AuditEventCreate(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                run_id=run_id,
                event_type=event_type,
                metadata=metadata,
                actor=AuditActor(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    actor_type="user" if user_id is not None else "system",
                ),
            )
        )
