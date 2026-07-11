import base64
import json
import re
import time
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from taroai.agent.graph import build_runtime_graph
from taroai.agent.planning import PlanStep
from taroai.agent.state import AgentRetrievedContext, AgentRuntimeState
from taroai.audit import AuditActor, AuditEventCreate, AuditService
from taroai.billing import BillingPricingService
from taroai.db import SqlControlPlaneRepository
from taroai.embeddings import (
    EmbeddingGateway,
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
from taroai.knowledge import RetrievalRequest, RetrievalResult
from taroai.licensing import LicenseEntitlementDeniedError, LicensedFeature
from taroai.memory import MemoryScopeType
from taroai.tool_gateway import (
    ToolApprovalRequiredError,
    ToolExecutionError,
    ToolGateway,
    ToolResult,
)
from taroai.domain import Run, RunStatus, new_id, utc_now
from taroai.model_gateway import (
    ModelBudgetExceededError,
    ModelBudgetGuard,
    ModelGatewayError,
    ModelGateway,
    ModelGatewayRequest,
    ModelGatewayResponse,
    ModelMessage,
    ModelPolicy,
    ModelPolicyDeniedError,
    OpenAICompatibleModelGateway,
    PlannedToolCall,
)
from taroai.policy import PolicyDecision, PolicyRequest, PolicyService
from taroai.sandbox import (
    BrowserController,
    BrowserProviderUnavailableError,
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
from taroai.store import InMemoryControlPlaneStore, NotFoundError


class _RuntimeGuardrailViolation(RuntimeError):
    def __init__(self, event_type: str, reason: str, metadata: dict[str, Any]):
        super().__init__(metadata.get("message") or event_type)
        self.event_type = event_type
        self.reason = reason
        self.metadata = metadata


class _RuntimeGuardrailApprovalRequired(RuntimeError):
    def __init__(
        self,
        event_type: str,
        stage: GuardrailStage,
        guardrail_key: str,
        reason: str,
        metadata: dict[str, Any],
    ):
        super().__init__(reason)
        self.event_type = event_type
        self.stage = stage
        self.guardrail_key = guardrail_key
        self.reason = reason
        self.metadata = metadata


class _RuntimeStorageContentRejected(RuntimeError):
    def __init__(self, metadata: dict[str, Any]):
        super().__init__("storage content rejected by scan policy")
        self.metadata = metadata


class _RuntimeSandboxArtifactPathRejected(RuntimeError):
    def __init__(self, metadata: dict[str, Any]):
        super().__init__("sandbox artifact path must be under /workspace/artifacts/")
        self.metadata = metadata


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
    max_step_retries: int = 0
    runtime_mode: str = "legacy"
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
        return build_runtime_graph()

    def execute_run(self, tenant_id: str, run_id: str) -> AgentRuntimeState:
        if self.runtime_mode == "loop_v2":
            from taroai.agent.loop import AgentLoopV2

            return AgentLoopV2(self).execute_run(tenant_id, run_id)
        return self._execute_legacy_run(tenant_id, run_id)

    def _execute_legacy_run(self, tenant_id: str, run_id: str) -> AgentRuntimeState:
        run = self.store.update_run_status(tenant_id, run_id, RunStatus.RUNNING)
        state = self._initial_state(run)
        self._save_state(state)
        runtime_policy_decision = self._decide_runtime_execution(run)
        if not runtime_policy_decision.allowed:
            return self._pause_for_policy_block(state, run, runtime_policy_decision)
        state.retrieved_context = self._load_context(run)
        self._save_state(state)
        self.store.append_run_event(
            run,
            "context.loaded",
            self._context_event_payload(state.retrieved_context),
        )
        try:
            state.plan = self._create_plan(
                run,
                state.retrieved_context,
                state.approved_guardrail_keys,
            )
        except ModelBudgetExceededError as error:
            return self._fail_for_model_budget(state, run, error)
        except _RuntimeGuardrailApprovalRequired as error:
            return self._pause_for_guardrail_approval(state, run, error)
        except _RuntimeGuardrailViolation as error:
            return self._fail_for_guardrail(state, run, error)
        except ModelPolicyDeniedError as error:
            self._record_model_policy_denial(state, run, error)
            raise
        except ModelGatewayError as error:
            self._record_model_gateway_failure(state, run, error)
            raise
        self._save_state(state)
        self.store.append_run_event(
            run,
            "plan.created",
            self._plan_created_event_payload(state),
        )
        self.store.append_run_event(run, "policy.checked", {"decision": "allowed"})
        return self._execute_planned_steps(state)

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
        self.store.resolve_approval_request(
            tenant_id=tenant_id,
            run_id=run_id,
            approval_id=approval_id,
            approved_by_user_id=approved_by_user_id,
        )
        self.store.update_run_status(
            tenant_id, run_id, RunStatus.RUNNING, emit_status_event=False
        )
        state.status = RunStatus.RUNNING
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
            self._save_state(state)
            if pending_guardrail_stage in {
                GuardrailStage.MODEL_REQUEST.value,
                GuardrailStage.MODEL_RESPONSE.value,
            }:
                return self._resume_planning_after_guardrail_approval(state)
            if pending_guardrail_stage == GuardrailStage.ARTIFACT.value:
                if self.runtime_mode == "loop_v2":
                    from taroai.agent.loop import AgentLoopV2

                    run = self.store.get_run(tenant_id, run_id)
                    return AgentLoopV2(self)._finalize(state, run)
                if self._has_pending_sandbox_artifact_promotion(state):
                    return self._resume_sandbox_artifact_promotion_after_guardrail_approval(
                        state
                    )
                return self._finalize_success(state)
        if self.runtime_mode == "loop_v2":
            approved_tool_names = list(
                state.runtime_metadata.get("approved_tool_names", [])
            )
            current_step = self._planned_step_by_id(
                state,
                state.current_step_id or "",
            )
            if (
                current_step is not None
                and current_step.tool_name not in approved_tool_names
            ):
                approved_tool_names.append(current_step.tool_name)
                state.runtime_metadata["approved_tool_names"] = approved_tool_names
            state.approval_id = None
            state.status = RunStatus.RUNNING
            self._save_state(state)
            from taroai.agent.loop import AgentLoopV2

            return AgentLoopV2(self).execute_run(tenant_id, run_id)
        state.approval_id = None
        self._save_state(state)
        return self._execute_planned_steps(state)

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
        self.store.reject_approval_request(
            tenant_id=tenant_id,
            run_id=run_id,
            approval_id=approval_id,
            rejected_by_user_id=rejected_by_user_id,
        )
        run = self.store.update_run_status(
            tenant_id,
            run_id,
            RunStatus.FAILED,
            emit_status_event=False,
        )
        self.store.append_run_event(
            run,
            "run.failed",
            {
                "reason": "approval_rejected",
                "approval_id": approval_id,
                "resolved_by_user_id": rejected_by_user_id,
            },
        )
        state.status = RunStatus.FAILED
        state.approval_id = None
        state.pending_guardrail_approval_key = None
        state.pending_guardrail_approval_stage = None
        state.failure_reason = "Approval rejected"
        self._save_state(state)
        if self.runtime_mode == "loop_v2":
            from taroai.agent.loop import AgentLoopV2

            loop = AgentLoopV2(self)
            loop._complete_trigger_message(run, succeeded=False)
            loop._emit_terminal_once(
                state,
                run,
                "agent.loop.completed",
                {"outcome": "failed", "reason": "approval_rejected"},
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
            try:
                state = self._load_state(tenant_id, run_id)
            except NotFoundError:
                state = None
        if state is not None:
            active_step_id = state.current_step_id
            state.status = RunStatus.CANCELLED
            state.approval_id = None
            state.pending_guardrail_approval_key = None
            state.pending_guardrail_approval_stage = None
            state.failure_reason = "Run cancelled"
            if (
                self.sandbox_adapter is not None
                and state.sandbox_session_id is not None
                and active_step_id is not None
                and hasattr(self.sandbox_adapter, "cancel_command")
            ):
                try:
                    self.sandbox_adapter.cancel_command(
                        state.tenant_id,
                        state.sandbox_session_id,
                        active_step_id,
                    )
                except Exception:
                    pass
            state.current_step_id = None
            self._save_state(state)
            if self.runtime_mode == "loop_v2":
                from taroai.agent.loop import AgentLoopV2

                loop = AgentLoopV2(self)
                loop.checkpoint_cancel(state, run)
                loop._complete_trigger_message(run, succeeded=False)
                loop._emit_terminal_once(
                    state,
                    run,
                    "agent.loop.completed",
                    {"outcome": "cancelled", "reason": reason_code},
                )
                self._save_state(state)
            self._destroy_runtime_sandbox_session(
                state,
                reason="cancelled",
                force=True,
            )
            self._destroy_runtime_browser_session(state, reason="cancelled")
        return run

    def retry_run(
        self,
        tenant_id: str,
        run_id: str,
        requested_by_user_id: str,
        reason_code: str,
    ) -> AgentRuntimeState:
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
        if self.runtime_mode == "loop_v2":
            from taroai.agent.loop import AgentLoopV2

            try:
                state = self._load_state(tenant_id, run_id)
            except NotFoundError:
                state = self._initial_state(run)
            state.status = RunStatus.RUNNING
            state.max_iterations = state.iteration + self.loop_max_iterations
            state.repair_attempts = 0
            state.replan_count = 0
            state.failure_reason = None
            state.waiting_reason = None
            state.pending_uncertain_action_id = None
            state.terminal_event_emitted = False
            state.runtime_metadata["execution_attempt"] = (
                int(state.runtime_metadata.get("execution_attempt", 0)) + 1
            )
            state.runtime_metadata["attempt_start_iteration"] = state.iteration
            state.deadline_at = utc_now() + timedelta(seconds=self.loop_timeout_seconds)
            AgentLoopV2(self)._persist_checkpoint(state, run)
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
        return self.execute_run(tenant_id, run_id)

    def _initial_state(self, run: Run) -> AgentRuntimeState:
        return AgentRuntimeState(
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            user_id=run.user_id,
            run_id=run.id,
            goal=run.message,
            status=run.status,
            max_iterations=self.loop_max_iterations,
            max_repairs=self.loop_max_repairs,
            cost_limit=self.loop_cost_limit,
            deadline_at=utc_now() + timedelta(seconds=self.loop_timeout_seconds),
        )

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
        messages = [
            ModelMessage(
                role="system",
                content=(
                    "You are Taroai's enterprise agent planner. Return strict JSON with "
                    "a top-level steps array. Each step must include id, title, tool_name, "
                    "tool_input, and approval_required. Available built-in tools include "
                    "sandbox.command for shell or Python work in the run workspace and "
                    "browser.action for browser navigation, extraction, typing, clicking, "
                    "and screenshots. Do not invent session_id values for sandbox.command "
                    "or browser.action; the runtime injects them. When the user asks for a "
                    "deliverable, create it with sandbox.command under /workspace/artifacts/ "
                    "and include artifact_path or artifact_paths in tool_input, for example "
                    '"/workspace/artifacts/report.md". The command must create the '
                    "directory before writing files, for example "
                    "'mkdir -p /workspace/artifacts && ...'. Files outside "
                    "/workspace/artifacts/ are rejected for artifact publication."
                ),
            )
        ]
        context_message = self._context_model_message(context)
        if context_message is not None:
            messages.append(context_message)
        messages.append(ModelMessage(role="user", content=run.message))
        context_sensitivity_level = self._context_sensitivity_level(context)
        request = ModelGatewayRequest(
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            user_id=run.user_id,
            run_id=run.id,
            sensitivity_level=context_sensitivity_level,
            messages=messages,
            input=run.message,
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
        self.store.update_run_status(
            run.tenant_id,
            run.id,
            RunStatus.FAILED,
            emit_status_event=False,
        )
        self.store.append_run_event(
            run,
            "run.failed",
            {"reason": "model_budget_exceeded", "error": str(error)},
        )
        state.status = RunStatus.FAILED
        state.failure_reason = str(error)
        self._save_state(state)
        return state

    def _fail_for_guardrail(
        self,
        state: AgentRuntimeState,
        run: Run,
        error: _RuntimeGuardrailViolation,
    ) -> AgentRuntimeState:
        self.store.update_run_status(
            run.tenant_id,
            run.id,
            RunStatus.FAILED,
            emit_status_event=False,
        )
        self.store.append_run_event(
            run,
            "run.failed",
            {
                "reason": error.reason,
                "guardrail_event_type": error.event_type,
            },
        )
        state.status = RunStatus.FAILED
        state.failure_reason = str(error)
        self._save_state(state)
        self._destroy_runtime_sandbox_session(
            state,
            reason="failure",
            force=True,
        )
        self._destroy_runtime_browser_session(state, reason="failure")
        return state

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

    def _pause_for_policy_block(
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
        self._destroy_runtime_sandbox_session(
            state,
            reason="policy_blocked",
            force=True,
        )
        self._destroy_runtime_browser_session(state, reason="policy_blocked")
        self.store.update_run_status(
            run.tenant_id,
            run.id,
            RunStatus.AWAITING_POLICY,
            emit_status_event=False,
        )
        self.store.append_run_event(run, "policy.blocked", payload)
        state.status = RunStatus.AWAITING_POLICY
        state.failure_reason = reason
        self.pending_states[state.run_id] = state
        self._save_state(state)
        return state

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
        self._save_state(state)
        return self._execute_planned_steps(state)

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
        self.store.update_run_status(
            run.tenant_id,
            run.id,
            RunStatus.FAILED,
            emit_status_event=False,
        )
        self.store.append_run_event(
            run,
            "run.failed",
            {"reason": "model_policy_denied", "error": str(error)},
        )
        state.status = RunStatus.FAILED
        state.failure_reason = str(error)
        self._save_state(state)

    def _record_model_gateway_failure(
        self,
        state: AgentRuntimeState,
        run: Run,
        error: ModelGatewayError,
    ) -> None:
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
        self.store.update_run_status(
            run.tenant_id,
            run.id,
            RunStatus.FAILED,
            emit_status_event=False,
        )
        self.store.append_run_event(
            run,
            "run.failed",
            {"reason": "model_gateway_error", "error_type": error.__class__.__name__},
        )
        state.status = RunStatus.FAILED
        state.failure_reason = str(error)
        self._save_state(state)

    def _resume_planning_after_guardrail_approval(
        self,
        state: AgentRuntimeState,
    ) -> AgentRuntimeState:
        run = self.store.get_run(state.tenant_id, state.run_id)
        try:
            state.plan = self._create_plan(
                run,
                state.retrieved_context,
                state.approved_guardrail_keys,
            )
        except ModelBudgetExceededError as error:
            return self._fail_for_model_budget(state, run, error)
        except _RuntimeGuardrailApprovalRequired as error:
            return self._pause_for_guardrail_approval(state, run, error)
        except _RuntimeGuardrailViolation as error:
            return self._fail_for_guardrail(state, run, error)
        except ModelPolicyDeniedError as error:
            self._record_model_policy_denial(state, run, error)
            raise
        except ModelGatewayError as error:
            self._record_model_gateway_failure(state, run, error)
            raise
        self._save_state(state)
        self.store.append_run_event(
            run,
            "plan.created",
            self._plan_created_event_payload(state),
        )
        self.store.append_run_event(run, "policy.checked", {"decision": "allowed"})
        return self._execute_planned_steps(state)

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

    def _load_knowledge_context(self, run: Run):
        if self.knowledge_service is None:
            return []
        query_embedding = self._load_query_embedding(run)
        results = self.knowledge_service.retrieve(
            RetrievalRequest(
                tenant_id=run.tenant_id,
                query=run.message,
                query_embedding=query_embedding,
                allowed_workspace_ids=[run.workspace_id],
                acl_subjects=[
                    run.user_id,
                    f"workspace:{run.workspace_id}",
                    f"tenant:{run.tenant_id}",
                ],
                clearance_level=0,
                limit=5,
            )
        )
        return self._apply_retrieval_guardrails(run, results)

    def _load_query_embedding(self, run: Run) -> list[float]:
        if self.embedding_gateway is None:
            return []
        response = self.embedding_gateway.embed(
            EmbeddingGatewayRequest(
                tenant_id=run.tenant_id,
                workspace_id=run.workspace_id,
                user_id=run.user_id,
                run_id=run.id,
                purpose="knowledge_query",
                input=[run.message],
            )
        )
        self._record_embedding_usage(run, response)
        if not response.embeddings:
            return []
        return response.embeddings[0].embedding

    def _record_embedding_usage(
        self,
        run: Run,
        response: EmbeddingGatewayResponse,
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
                purpose="knowledge_query",
                response=response,
                input_count=1,
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
        if self.long_term_memory_service is None:
            return []
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
                records.append(record)
                seen_ids.add(record.id)
        return records

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
            for record in context.memory_records:
                lines.append(
                    f"- memory_id={record.id}; scope={record.scope_type.value}:{record.scope_id}; "
                    f"content={record.content}"
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

    def _execute_planned_steps(self, state: AgentRuntimeState) -> AgentRuntimeState:
        for step in state.plan:
            if step.id in state.completed_step_ids:
                continue
            if step.approval_required and step.id not in state.approved_step_ids:
                return self._pause_for_approval(
                    state, step, f"Step requires approval: {step.title}"
                )
            step_result = self._execute_step(state, step)
            if step_result.status in {
                RunStatus.FAILED,
                RunStatus.AWAITING_APPROVAL,
                RunStatus.AWAITING_POLICY,
            }:
                return step_result
            state = step_result
        return self._finalize_success(state)

    def _execute_step(
        self, state: AgentRuntimeState, step: PlanStep
    ) -> AgentRuntimeState:
        run = self.store.get_run(state.tenant_id, state.run_id)
        step_policy_decision = self._decide_runtime_step(state, run, step)
        if not step_policy_decision.allowed:
            return self._pause_for_policy_block(
                state,
                run,
                step_policy_decision,
                current_step_id=step.id,
            )
        try:
            step = self._prepare_step_for_execution(state, step)
        except ToolExecutionError as error:
            self.store.append_run_event(
                run, "step.started", {"step_id": step.id, "title": step.title}
            )
            state.current_step_id = step.id
            self._save_state(state)
            return self._fail_for_tool_execution_error(
                state,
                run,
                step,
                error,
                attempt=1,
            )
        self.store.append_run_event(
            run, "step.started", {"step_id": step.id, "title": step.title}
        )
        state.current_step_id = step.id
        self._save_state(state)
        for attempt in range(self.max_step_retries + 1):
            self.store.append_run_event(
                run,
                "tool_call.started",
                {
                    "step_id": step.id,
                    "tool_name": step.tool_name,
                    "attempt": attempt + 1,
                },
            )
            try:
                result = self.tool_gateway.execute_for_run(
                    state,
                    step,
                    granted_scopes=self._resolve_tool_granted_scopes(state, step),
                )
            except ToolApprovalRequiredError as error:
                self.store.append_run_event(
                    run,
                    "tool_call.approval_required",
                    {
                        "step_id": step.id,
                        "tool_name": step.tool_name,
                        "reason": str(error),
                    },
                )
                self._record_tool_policy_pause(state, step, str(error))
                return self._pause_for_approval(state, step, str(error))
            except ToolExecutionError as error:
                if attempt < self.max_step_retries:
                    self._record_tool_execution_error(
                        state,
                        run,
                        step,
                        error,
                        attempt + 1,
                    )
                    self.store.append_run_event(
                        run,
                        "step.retrying",
                        {
                            "step_id": step.id,
                            "tool_name": step.tool_name,
                            "next_attempt": attempt + 2,
                        },
                    )
                    continue
                return self._fail_for_tool_execution_error(
                    state,
                    run,
                    step,
                    error,
                    attempt + 1,
                )
            if step.tool_name == "browser.action":
                result = self._promote_browser_screenshot(state, result)
            self.store.append_run_event(
                run,
                "tool_call.completed",
                {
                    "step_id": step.id,
                    "tool_name": step.tool_name,
                    "result": self._safe_tool_result_payload(step, result),
                },
            )
            if step.tool_name == "sandbox.command":
                self._record_sandbox_command_event(run, step, result)
                failed_exit_code = self._sandbox_command_failed_exit_code(result)
                if failed_exit_code is not None:
                    return self._fail_for_sandbox_command_failure(
                        state,
                        run,
                        step,
                        failed_exit_code,
                    )
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
            if step.tool_name == "browser.action":
                self._record_browser_action_event(run, step, result)
            self._record_tool_execution(state, step)
            state.tool_results.append(result)
            state.completed_step_ids.append(step.id)
            self._save_state(state)
            return state
        return state

    def _record_tool_execution_error(
        self,
        state: AgentRuntimeState,
        run: Run,
        step: PlanStep,
        error: ToolExecutionError,
        attempt: int,
    ) -> None:
        self.store.append_run_event(
            run,
            "tool_call.failed",
            {
                "step_id": step.id,
                "tool_name": step.tool_name,
                "error": str(error),
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
        self.store.update_run_status(
            state.tenant_id,
            state.run_id,
            RunStatus.FAILED,
            emit_status_event=False,
        )
        self.store.append_run_event(
            run,
            "run.failed",
            {"step_id": step.id, "error": str(error)},
        )
        state.status = RunStatus.FAILED
        state.failure_reason = str(error)
        self._save_state(state)
        self._destroy_runtime_sandbox_session(
            state,
            reason="failure",
            force=True,
        )
        self._destroy_runtime_browser_session(state, reason="failure")
        return state

    def _prepare_step_for_execution(
        self,
        state: AgentRuntimeState,
        step: PlanStep,
    ) -> PlanStep:
        if str(step.tool_input.get("session_id", "")).strip():
            return step
        if step.tool_name == "sandbox.command" and self.sandbox_adapter is not None:
            session_id = self._ensure_sandbox_session(state).id
        elif step.tool_name == "browser.action" and self.browser_controller is not None:
            session_id = self._ensure_browser_session(state).session_id
        else:
            return step
        updated_input = dict(step.tool_input)
        updated_input["session_id"] = session_id
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
        if state.sandbox_session_id is not None:
            session = self.sandbox_adapter.get_session(
                state.tenant_id,
                state.sandbox_session_id,
            )
            self._materialize_run_attachments(state, session)
            return session
        self._enforce_sandbox_concurrency_license(state)
        session = self.sandbox_adapter.create(
            SandboxCreateRequest(
                tenant_id=state.tenant_id,
                workspace_id=state.workspace_id,
                run_id=state.run_id,
                image=str(
                    state.runtime_metadata.get(
                        "skill_runtime_image", self.sandbox_runtime_image
                    )
                ),
                network_mode=self.sandbox_network_mode,
                timeout_seconds=self.sandbox_timeout_seconds,
                metadata={"created_by": "agent_runtime"},
            )
        )
        state.sandbox_session_id = session.id
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
        self._materialize_run_attachments(state, session)
        self._save_state(state)
        return session

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
        run = self.store.get_run(state.tenant_id, state.run_id)
        descriptors = self._attachment_descriptors(run)
        for descriptor in descriptors:
            storage_object = self.storage_catalog.get(
                run.tenant_id, descriptor["storage_object_id"]
            )
            content = self.object_storage.download(storage_object).content
            self.sandbox_adapter.upload_file(
                SandboxFileWrite(
                    tenant_id=run.tenant_id,
                    workspace_id=run.workspace_id,
                    run_id=run.id,
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
        session = self.browser_controller.open_session(
            tenant_id=state.tenant_id,
            workspace_id=state.workspace_id,
            run_id=state.run_id,
            session_id=new_id("browser"),
        )
        state.browser_session_id = session.session_id
        run = self.store.get_run(state.tenant_id, state.run_id)
        self.store.append_run_event(
            run,
            "browser.session.created",
            {
                "session_id": session.session_id,
                "current_url": session.current_url,
            },
        )
        self._save_state(state)
        return session

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
            },
        )

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
        explicit_paths = self._sandbox_artifact_paths(step)
        paths = explicit_paths or self._discover_sandbox_artifact_paths(state)
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
            file_ref = self.sandbox_adapter.download_file(
                state.tenant_id,
                state.sandbox_session_id,
                path,
            )
            filename = self._sandbox_artifact_filename(file_ref.path)
            content = (file_ref.content or "").encode("utf-8")
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
        if hasattr(self.storage_catalog, "mark_deleted"):
            self.storage_catalog.mark_deleted(
                tenant_id,
                storage_object.id,
                utc_now(),
            )

    def _discover_sandbox_artifact_paths(self, state: AgentRuntimeState) -> list[str]:
        if self.sandbox_adapter is None or state.sandbox_session_id is None:
            return []
        paths: list[str] = []
        for file_ref in self.sandbox_adapter.list_files(
            state.tenant_id,
            state.sandbox_session_id,
        ):
            if file_ref.path in state.promoted_sandbox_artifact_paths:
                continue
            if not self._is_auto_discoverable_sandbox_artifact_path(file_ref.path):
                continue
            if file_ref.path not in paths:
                paths.append(file_ref.path)
        return paths

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
        if hasattr(self.storage_catalog, "mark_deleted"):
            self.storage_catalog.mark_deleted(
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
        self.store.update_run_status(
            run.tenant_id,
            run.id,
            RunStatus.FAILED,
            emit_status_event=False,
        )
        self.store.append_run_event(
            run,
            "run.failed",
            {
                "reason": "storage_content_rejected",
                "step_id": step.id,
            },
        )
        state.status = RunStatus.FAILED
        state.failure_reason = str(error)
        self._save_state(state)
        self._destroy_runtime_sandbox_session(
            state,
            reason="failure",
            force=True,
        )
        self._destroy_runtime_browser_session(state, reason="failure")
        return state

    def _fail_for_sandbox_artifact_path_rejection(
        self,
        state: AgentRuntimeState,
        run: Run,
        step: PlanStep,
        error: _RuntimeSandboxArtifactPathRejected,
    ) -> AgentRuntimeState:
        self.store.update_run_status(
            run.tenant_id,
            run.id,
            RunStatus.FAILED,
            emit_status_event=False,
        )
        self.store.append_run_event(
            run,
            "run.failed",
            {
                "reason": "sandbox_artifact_path_rejected",
                "step_id": step.id,
            },
        )
        state.status = RunStatus.FAILED
        state.failure_reason = str(error)
        self._save_state(state)
        self._destroy_runtime_sandbox_session(
            state,
            reason="failure",
            force=True,
        )
        self._destroy_runtime_browser_session(state, reason="failure")
        return state

    def _fail_for_sandbox_command_failure(
        self,
        state: AgentRuntimeState,
        run: Run,
        step: PlanStep,
        exit_code: int,
    ) -> AgentRuntimeState:
        self.store.update_run_status(
            run.tenant_id,
            run.id,
            RunStatus.FAILED,
            emit_status_event=False,
        )
        self.store.append_run_event(
            run,
            "run.failed",
            {
                "reason": "sandbox_command_failed",
                "step_id": step.id,
                "exit_code": exit_code,
            },
        )
        state.status = RunStatus.FAILED
        state.failure_reason = f"sandbox.command failed with exit code {exit_code}"
        self._save_state(state)
        self._destroy_runtime_sandbox_session(
            state,
            reason="failure",
            force=True,
        )
        self._destroy_runtime_browser_session(state, reason="failure")
        return state

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
        approval = self.store.create_approval_request(
            tenant_id=state.tenant_id,
            run_id=state.run_id,
            step_id=step.id,
            reason=reason,
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

    def _finalize_success(self, state: AgentRuntimeState) -> AgentRuntimeState:
        run = self.store.get_run(state.tenant_id, state.run_id)
        artifacts = self.store.list_artifacts(state.tenant_id, state.run_id)
        if artifacts:
            artifact = {
                "name": artifacts[-1].name,
                "artifact_type": artifacts[-1].artifact_type,
                "uri": artifacts[-1].uri,
            }
        else:
            artifact = {
                "name": "agent-result.md",
                "artifact_type": "document",
                "uri": f"s3://{state.tenant_id}/runs/{state.run_id}/agent-result.md",
            }
            try:
                artifact = self._apply_artifact_guardrails(
                    run,
                    artifact,
                    state.approved_guardrail_keys,
                )
            except _RuntimeGuardrailApprovalRequired as error:
                return self._pause_for_guardrail_approval(state, run, error)
            except _RuntimeGuardrailViolation as error:
                return self._fail_for_guardrail(state, run, error)
            self.store.create_artifact(
                tenant_id=state.tenant_id,
                run_id=state.run_id,
                name=artifact["name"],
                artifact_type=artifact["artifact_type"],
                uri=artifact["uri"],
            )
        self._destroy_runtime_sandbox_session(state, reason="success")
        self._destroy_runtime_browser_session(state, reason="success")
        run = self.store.update_run_status(
            state.tenant_id,
            state.run_id,
            RunStatus.SUCCEEDED,
            emit_status_event=False,
        )
        self.store.append_run_event(
            run, "run.succeeded", {"artifact_name": artifact["name"]}
        )
        state.status = RunStatus.SUCCEEDED
        state.current_step_id = None
        self._save_state(state)
        return state

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
        try:
            session = self.sandbox_adapter.get_session(
                state.tenant_id,
                state.sandbox_session_id,
            )
        except NotFoundError:
            return
        if getattr(session.status, "value", session.status) == "destroyed":
            return
        run = self.store.get_run(state.tenant_id, state.run_id)
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

    def _destroy_runtime_browser_session(
        self,
        state: AgentRuntimeState,
        reason: str,
    ) -> None:
        if self.browser_controller is None or state.browser_session_id is None:
            return
        try:
            destroyed = self.browser_controller.delete_session(
                state.tenant_id,
                state.browser_session_id,
            )
        except NotFoundError:
            return
        except BrowserProviderUnavailableError as error:
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
                "session_id": destroyed.session_id,
                "current_url": destroyed.current_url,
                "reason": reason,
            },
        )

    def _save_state(self, state: AgentRuntimeState) -> None:
        self.store.save_runtime_state(state)

    def _load_state(self, tenant_id: str, run_id: str) -> AgentRuntimeState:
        snapshot = self.store.get_runtime_state(tenant_id, run_id)
        return AgentRuntimeState.model_validate(snapshot.to_runtime_state_payload())

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
