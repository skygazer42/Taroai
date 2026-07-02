import json
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from taroai.agent.graph import build_runtime_graph
from taroai.agent.planning import PlanStep
from taroai.agent.state import AgentRetrievedContext, AgentRuntimeState
from taroai.audit import AuditActor, AuditEventCreate, AuditService
from taroai.db import SqlControlPlaneRepository
from taroai.guardrails.models import (
    GuardrailAction,
    GuardrailDecision,
    GuardrailEvaluationRequest,
    GuardrailStage,
)
from taroai.knowledge import RetrievalRequest, RetrievalResult
from taroai.memory import MemoryScopeType
from taroai.tool_gateway import ToolApprovalRequiredError, ToolExecutionError, ToolGateway
from taroai.domain import Run, RunStatus
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


class AgentRuntime(BaseModel):
    store: InMemoryControlPlaneStore | SqlControlPlaneRepository
    model_gateway: ModelGateway = Field(default_factory=OpenAICompatibleModelGateway)
    model_policy: ModelPolicy = Field(default_factory=ModelPolicy)
    model_budget_guard: ModelBudgetGuard = Field(default_factory=ModelBudgetGuard)
    tool_gateway: ToolGateway = Field(default_factory=ToolGateway)
    audit_service: Any | None = None
    knowledge_service: Any | None = None
    long_term_memory_service: Any | None = None
    guardrail_service: Any | None = None
    max_step_retries: int = 0
    pending_states: dict[str, AgentRuntimeState] = Field(default_factory=dict)

    def build_graph(self):
        return build_runtime_graph()

    def execute_run(self, tenant_id: str, run_id: str) -> AgentRuntimeState:
        run = self.store.update_run_status(tenant_id, run_id, RunStatus.RUNNING)
        state = self._initial_state(run)
        self._save_state(state)
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
            {"steps": [step.model_dump(mode="json") for step in state.plan]},
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
        self.store.update_run_status(tenant_id, run_id, RunStatus.RUNNING, emit_status_event=False)
        state.status = RunStatus.RUNNING
        if state.current_step_id is not None and state.current_step_id not in state.approved_step_ids:
            state.approved_step_ids.append(state.current_step_id)
        if state.pending_guardrail_approval_key is not None:
            if state.pending_guardrail_approval_key not in state.approved_guardrail_keys:
                state.approved_guardrail_keys.append(state.pending_guardrail_approval_key)
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
                return self._finalize_success(state)
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
            state.status = RunStatus.CANCELLED
            state.approval_id = None
            state.current_step_id = None
            state.pending_guardrail_approval_key = None
            state.pending_guardrail_approval_stage = None
            state.failure_reason = "Run cancelled"
            self._save_state(state)
        return run

    def _initial_state(self, run: Run) -> AgentRuntimeState:
        return AgentRuntimeState(
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            user_id=run.user_id,
            run_id=run.id,
            goal=run.message,
            status=run.status,
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
                    "tool_input, and approval_required."
                ),
            )
        ]
        context_message = self._context_model_message(context)
        if context_message is not None:
            messages.append(context_message)
        messages.append(ModelMessage(role="user", content=run.message))
        request = ModelGatewayRequest(
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            user_id=run.user_id,
            run_id=run.id,
            messages=messages,
            input=run.message,
            tool_choice="auto",
            metadata={
                "agent_id": run.agent_id,
                "attachments": run.attachments,
                "mode": run.mode,
                "knowledge_result_count": len(context.knowledge_results),
                "memory_record_count": len(context.memory_records),
            },
        )
        request = self._apply_model_request_guardrails(
            run,
            request,
            approved_guardrail_keys or [],
        )
        self.model_policy.assert_request_allowed(request)
        self.model_budget_guard.assert_plan_allowed(self.store, run.tenant_id, run.id)
        response = self.model_gateway.create_plan(request)
        response = self._apply_model_response_guardrails(
            run,
            response,
            approved_guardrail_keys or [],
        )
        self._record_model_plan(run, response)
        return [
            PlanStep(
                id=step.id,
                title=step.title,
                tool_name=step.tool_name,
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
        state.current_step_id = None
        state.pending_guardrail_approval_key = error.guardrail_key
        state.pending_guardrail_approval_stage = error.stage.value
        state.approval_id = approval.id
        self.pending_states[state.run_id] = state
        self._save_state(state)
        return state

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
            {"steps": [step.model_dump(mode="json") for step in state.plan]},
        )
        self.store.append_run_event(run, "policy.checked", {"decision": "allowed"})
        return self._execute_planned_steps(state)

    def _record_model_plan(self, run: Run, response: ModelGatewayResponse) -> None:
        metadata = {
            "response_id": response.id,
            "model": response.model,
            "planned_step_count": len(response.planned_steps),
            "usage": (
                response.usage.model_dump(mode="json")
                if response.usage is not None
                else None
            ),
        }
        self.store.record_billing_meter(
            tenant_id=run.tenant_id,
            run_id=run.id,
            meter_type="model_call_count",
            quantity=1,
            unit="call",
            model=response.model,
            metadata=metadata,
        )
        if response.usage is not None:
            self.store.record_billing_meter(
                tenant_id=run.tenant_id,
                run_id=run.id,
                meter_type="model_tokens_input",
                quantity=response.usage.input_tokens,
                unit="token",
                model=response.model,
                metadata=metadata,
            )
            self.store.record_billing_meter(
                tenant_id=run.tenant_id,
                run_id=run.id,
                meter_type="model_tokens_output",
                quantity=response.usage.output_tokens,
                unit="token",
                model=response.model,
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
            guardrail_key = self._guardrail_approval_key(GuardrailStage.MODEL_REQUEST, decision)
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
        content = response.output_text or self._model_response_guardrail_content(response)
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
            guardrail_key = self._guardrail_approval_key(GuardrailStage.MODEL_RESPONSE, decision)
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
            {"steps": [step.model_dump(mode="json") for step in response.planned_steps]},
            sort_keys=True,
        )

    def _redact_model_response(
        self,
        response: ModelGatewayResponse,
        decision: GuardrailDecision,
    ) -> ModelGatewayResponse:
        source_content = response.output_text or self._model_response_guardrail_content(response)
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

    def _parse_redacted_planned_steps(self, content: str) -> list[PlannedToolCall] | None:
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

    def _apply_guardrail_redactions(self, value: str, decision: GuardrailDecision) -> str:
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
            "severity": decision.severity.value if decision.severity is not None else None,
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
    ) -> dict[str, str]:
        if self.guardrail_service is None:
            return artifact
        decision = self.guardrail_service.evaluate(
            GuardrailEvaluationRequest(
                tenant_id=run.tenant_id,
                workspace_id=run.workspace_id,
                stage=GuardrailStage.ARTIFACT,
                content=self._artifact_guardrail_content(artifact),
                attributes={
                    "artifact_type": artifact["artifact_type"],
                    "name_length": len(artifact["name"]),
                    "uri_scheme": artifact["uri"].split(":", 1)[0],
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
            guardrail_key = self._guardrail_approval_key(GuardrailStage.ARTIFACT, decision)
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
                reason=decision.message or "Artifact publication requires guardrail approval",
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

    def _artifact_guardrail_content(self, artifact: dict[str, str]) -> str:
        return json.dumps(
            {
                "name": artifact["name"],
                "artifact_type": artifact["artifact_type"],
                "uri": artifact["uri"],
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
            "severity": decision.severity.value if decision.severity is not None else None,
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
        results = self.knowledge_service.retrieve(
            RetrievalRequest(
                tenant_id=run.tenant_id,
                query=run.message,
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
            if decision.action == GuardrailAction.REDACT and decision.redacted_content is not None:
                accepted_results.append(result.model_copy(update={"excerpt": decision.redacted_content}))
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
                "severity": decision.severity.value if decision.severity is not None else None,
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
            "knowledge_document_ids": [
                result.document_id for result in context.knowledge_results
            ],
            "knowledge_chunk_ids": [
                result.chunk_id for result in context.knowledge_results
            ],
            "memory_ids": [record.id for record in context.memory_records],
        }

    def _context_model_message(self, context: AgentRetrievedContext) -> ModelMessage | None:
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

    def _execute_planned_steps(self, state: AgentRuntimeState) -> AgentRuntimeState:
        for step in state.plan:
            if step.id in state.completed_step_ids:
                continue
            if step.approval_required and step.id not in state.approved_step_ids:
                return self._pause_for_approval(state, step, f"Step requires approval: {step.title}")
            step_result = self._execute_step(state, step)
            if step_result.status in {RunStatus.FAILED, RunStatus.AWAITING_APPROVAL}:
                return step_result
            state = step_result
        return self._finalize_success(state)

    def _execute_step(self, state: AgentRuntimeState, step: PlanStep) -> AgentRuntimeState:
        run = self.store.get_run(state.tenant_id, state.run_id)
        self.store.append_run_event(run, "step.started", {"step_id": step.id, "title": step.title})
        state.current_step_id = step.id
        self._save_state(state)
        for attempt in range(self.max_step_retries + 1):
            self.store.append_run_event(
                run,
                "tool_call.started",
                {"step_id": step.id, "tool_name": step.tool_name, "attempt": attempt + 1},
            )
            try:
                result = self.tool_gateway.execute_for_run(state, step)
            except ToolApprovalRequiredError as error:
                self.store.append_run_event(
                    run,
                    "tool_call.approval_required",
                    {"step_id": step.id, "tool_name": step.tool_name, "reason": str(error)},
                )
                self._record_tool_policy_pause(state, step, str(error))
                return self._pause_for_approval(state, step, str(error))
            except ToolExecutionError as error:
                self.store.append_run_event(
                    run,
                    "tool_call.failed",
                    {"step_id": step.id, "tool_name": step.tool_name, "error": str(error)},
                )
                self._record_tool_failure(state, step, str(error), attempt + 1)
                if attempt < self.max_step_retries:
                    self.store.append_run_event(
                        run,
                        "step.retrying",
                        {"step_id": step.id, "tool_name": step.tool_name, "next_attempt": attempt + 2},
                    )
                    continue
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
                return state
            self.store.append_run_event(
                run,
                "tool_call.completed",
                {"step_id": step.id, "tool_name": step.tool_name, "result": result.model_dump(mode="json")},
            )
            self._record_tool_execution(state, step)
            state.tool_results.append(result)
            state.completed_step_ids.append(step.id)
            self._save_state(state)
            return state
        return state

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
        run = self.store.update_run_status(
            state.tenant_id,
            state.run_id,
            RunStatus.SUCCEEDED,
            emit_status_event=False,
        )
        self.store.append_run_event(run, "run.succeeded", {"artifact_name": artifact["name"]})
        state.status = RunStatus.SUCCEEDED
        state.current_step_id = None
        self._save_state(state)
        return state

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
        self.store.record_billing_meter(
            tenant_id=state.tenant_id,
            run_id=state.run_id,
            meter_type="tool_call_count",
            quantity=1,
            unit="call",
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
        if normalized in {"secret", "token", "password", "api_key", "apikey", "credential"}:
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
