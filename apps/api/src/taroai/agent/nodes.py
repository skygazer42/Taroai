import json
import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

from taroai.agent.loop import AgentExecutionServices
from taroai.agent.exceptions import (
    _RuntimeGuardrailApprovalRequired,
    _RuntimeGuardrailViolation,
)
from taroai.agent.models import (
    AgentAction,
    AgentCycle,
    AgentObservation,
    AgentVerificationResult,
)
from taroai.agent.state import AgentGraphRoute, AgentRuntimeState
from taroai.domain import RunMode, RunStatus, new_id, utc_now
from taroai.model_gateway import (
    ModelBudgetExceededError,
    ModelGatewayError,
    ModelPolicyDeniedError,
    ModelSafetyRefusalError,
)
from taroai.store import TERMINAL_RUN_STATUSES
if TYPE_CHECKING:
    from taroai.agent.runtime import AgentRuntime


def _unsupported_response_urls(
    state: AgentRuntimeState,
    response_text: str,
) -> set[str]:
    urls = {
        item.rstrip("`*_~.,;:!?，。；：！？")
        for item in re.findall(r"https?://[^\s<>\])]+", response_text)
    }
    if not urls:
        return set()
    evidence = json.dumps(
        {
            "goal": state.goal,
            "conversation": state.runtime_metadata.get("conversation_context"),
            "knowledge": [
                item.model_dump(mode="json")
                for item in state.retrieved_context.knowledge_results
            ],
            "observations": [
                item.model_dump(mode="json") for item in state.observations
            ],
            "steering_messages": state.steering_messages,
        },
        ensure_ascii=False,
        default=str,
    )
    decoded_evidence = unquote(evidence)
    return {
        url for url in urls if unquote(url.rstrip("/")) not in decoded_evidence
    }


def _has_unsupported_response_urls(
    state: AgentRuntimeState,
    response_text: str,
) -> bool:
    return bool(_unsupported_response_urls(state, response_text))


def _tool_failure_verification(
    observation: "AgentObservation",
) -> AgentVerificationResult:
    """把工具失败的证据逐字放进修复反馈，供下一次决策原样引用。"""

    evidence = [
        f"failure_class: {observation.failure_class}"
        if observation.failure_class
        else None,
        f"safe_error: {observation.safe_error}" if observation.safe_error else None,
        (
            f"error: {observation.error}"
            if observation.error and observation.error != observation.safe_error
            else None
        ),
    ]
    return AgentVerificationResult(
        outcome="repair",
        feedback=observation.safe_error or observation.error or "Action failed",
        evidence=[item for item in evidence if item is not None],
    )


def _failure_signature(result: AgentVerificationResult) -> str:
    """failure_class 加归一化反馈构成连续失败的比较签名。"""

    failure_class = next(
        (
            item.removeprefix("failure_class: ")
            for item in result.evidence
            if item.startswith("failure_class: ")
        ),
        "",
    )
    normalized = re.sub(r"\s+", " ", (result.feedback or "").strip().lower())
    if not failure_class and not normalized:
        return ""
    return f"{failure_class}|{normalized}"


def _ground_chat_response_url(
    state: AgentRuntimeState,
    response_text: str,
) -> str | None:
    unsupported = _unsupported_response_urls(state, response_text)
    if len(unsupported) != 1:
        return None
    source = next(iter(unsupported))
    source_host = (urlsplit(source).hostname or "").removeprefix("www.")
    for observation in reversed(state.observations):
        target = str(observation.output.get("url") or "")
        target_host = (urlsplit(target).hostname or "").removeprefix("www.")
        if observation.success and source_host and target_host == source_host:
            return response_text.replace(source, target)
    return None


@dataclass
class AgentGraphNodes:
    """LangGraph 节点实现；业务操作继续复用现有运行时服务。"""

    runtime: "AgentRuntime"
    execution: AgentExecutionServices = field(init=False)

    def __post_init__(self) -> None:
        self.execution = AgentExecutionServices(self.runtime)

    def observe(self, state: AgentRuntimeState) -> dict[str, Any]:
        """加载上下文并恢复未完成的持久化动作。"""

        run = self.runtime.store.get_run(state.tenant_id, state.run_id)
        if run.status in TERMINAL_RUN_STATUSES:
            return self._route(state, "end")

        self.runtime.store.update_run_status(
            run.tenant_id,
            run.id,
            RunStatus.RUNNING,
        )
        state.status = RunStatus.RUNNING
        state.max_iterations = (
            int(state.runtime_metadata.get("attempt_start_iteration", 0))
            + self.runtime.loop_max_iterations
        )
        state.max_repairs = self.runtime.loop_max_repairs
        state.cost_limit = self.runtime.loop_cost_limit
        if state.deadline_at is None:
            state.deadline_at = utc_now() + timedelta(
                seconds=self.runtime.loop_timeout_seconds
            )
        if (
            not state.retrieved_context.knowledge_results
            and not state.retrieved_context.memory_records
        ):
            state.retrieved_context = self.runtime._load_context(run)
            self.runtime.store.append_run_event(
                run,
                "context.loaded",
                self.runtime._context_event_payload(state.retrieved_context),
            )
        self.runtime._save_state(state)
        self.runtime.store.append_run_event(
            run,
            "agent.loop.started",
            {
                "mode": "langgraph",
                "max_iterations": state.max_iterations,
                "max_repairs": state.max_repairs,
                "deadline_at": (
                    state.deadline_at.isoformat() if state.deadline_at else None
                ),
                "cost_limit": state.cost_limit,
                "checkpoint_sequence": state.checkpoint_sequence,
            },
        )

        policy = self.runtime._decide_runtime_execution(run)
        if not policy.allowed:
            failed = self.runtime._fail_for_policy_block(state, run, policy)
            return self._route(failed, "end")
        try:
            self.execution._load_agent_context(state, run)
            self.execution._validate_explicit_skill_refs(state, run)
        except Exception as error:
            return self._failure(
                state,
                "invalid_resource_reference",
                self.execution._safe_error(error),
            )

        conversation = self.execution._conversation_context(state, run)
        if (
            run.thread_id is not None
            and not state.runtime_metadata.get("conversation_context_announced")
        ):
            state.runtime_metadata["conversation_context_announced"] = True
            self.runtime.store.append_run_event(
                run,
                "agent.conversation.loaded",
                {
                    "message_count": len(conversation.get("messages", [])),
                    "omitted_message_count": conversation.get(
                        "omitted_message_count", 0
                    ),
                    "character_count": conversation.get("character_count", 0),
                    "compaction_version": conversation.get("compaction_version", 1),
                },
            )
            self.runtime._save_state(state)

        recovered = [
            action
            for action in self.runtime.store.recover_expired_agent_actions(run.tenant_id)
            if action.run_id == run.id
        ]
        if recovered:
            self.runtime.store.append_run_event(
                run,
                "agent.actions.recovered",
                {"action_ids": [action.id for action in recovered]},
            )
        actions = self.runtime.store.list_agent_actions(run.tenant_id, run.id)
        known_observation_ids = {item.action_id for item in state.observations}
        recovered_observations = [
            action.observation
            for action in actions
            if action.observation is not None
            and action.observation.action_id not in known_observation_ids
        ]
        if recovered_observations:
            state.observations.extend(recovered_observations)
            state.pending_uncertain_action_id = None
            state.waiting_reason = None
            self.execution._persist_checkpoint(state, run)

        uncertain = next((item for item in actions if item.status == "uncertain"), None)
        if uncertain is not None:
            waiting = self.execution._wait_for_uncertain_resolution(state, run, uncertain)
            return self._route(waiting, "end")
        running = next((item for item in actions if item.status == "running"), None)
        if running is not None:
            state.waiting_reason = "action_is_owned_by_another_live_worker"
            self.runtime._save_state(state)
            return self._route(state, "end")
        pending = next((item for item in actions if item.status == "pending"), None)
        if pending is not None:
            state.iteration = max(
                state.iteration,
                self.execution._action_iteration(pending),
            )
            state.current_cycle_id = pending.cycle_id
            state.current_action_id = pending.id
            state.last_decision = pending.decision
            step = self.execution._decision_step(pending)
            state.plan = [step]
            state.current_step_id = step.id
            return self._route(state, "policy")
        return self._route(state, "decide")

    def decide(self, state: AgentRuntimeState) -> dict[str, Any]:
        """生成下一步决策并写入当前执行周期。"""

        run = self.runtime.store.get_run(state.tenant_id, state.run_id)
        budget_failure = self.execution._budget_failure(state)
        if budget_failure is not None:
            return self._failure(state, budget_failure)
        if self.execution._stop_if_cancelled(state, run):
            return self._route(state, "end")

        self.execution._consume_steering_at_checkpoint(state, run)
        state.iteration += 1
        state.current_cycle_id = None
        state.current_action_id = None
        state.current_step_id = None
        state.verifier_result = None
        cycle_id = new_id("cycle")
        self.runtime.store.append_run_event(
            run,
            "agent.cycle.started",
            {
                "cycle_id": cycle_id,
                "iteration": state.iteration,
                "plan_revision": state.active_plan_revision,
            },
        )
        try:
            decision = self.execution._decide(state, run)
        except _RuntimeGuardrailApprovalRequired as error:
            paused = self.runtime._pause_for_guardrail_approval(state, run, error)
            return self._route(paused, "end")
        except _RuntimeGuardrailViolation as error:
            return self._failure(state, error.reason, str(error))
        except ModelBudgetExceededError as error:
            self._record_model_failure_audit(run, "model.budget_exceeded", error)
            return self._failure(state, "model_budget_exceeded", str(error))
        except ModelPolicyDeniedError as error:
            self._record_model_failure_audit(run, "model.policy_denied", error)
            return self._failure(state, "model_policy_denied", str(error))
        except ModelGatewayError as error:
            self.runtime._record_model_gateway_failure(state, run, error)
            if isinstance(error, ModelSafetyRefusalError):
                return self._route(state, "end")
            raise
        if self.execution._stop_if_cancelled(state, run):
            return self._route(state, "end")
        state.last_decision = decision
        cycle = AgentCycle(
            id=cycle_id,
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            thread_id=run.thread_id,
            run_id=run.id,
            iteration=state.iteration,
            plan_revision=state.active_plan_revision,
            decision=decision,
            budget_snapshot=self.execution._budget_snapshot(state),
        )
        self.runtime.store.create_agent_cycle(cycle)
        state.current_cycle_id = cycle_id
        self.runtime.store.append_run_event(
            run,
            "agent.decision.created",
            {
                "cycle_id": cycle.id,
                "iteration": cycle.iteration,
                "decision": decision.model_dump(mode="json"),
            },
        )
        if state.graph_failure_code is not None:
            if state.final_response_text:
                self.runtime.store.append_run_event(
                    run,
                    "assistant.delta",
                    {"delta": state.final_response_text},
                )
                self.execution._append_assistant_message(
                    run, state.final_response_text
                )
            return self._route(state, "fail")

        if (
            decision.kind == "action"
            and decision.skill_id
            and not (
                decision.action_key
                and decision.action_key.startswith("planned:")
            )
        ):
            try:
                skill_loaded = self.execution._prepare_selected_skill(state, run, decision)
            except Exception as error:
                return self._failure(
                    state,
                    "skill_load_failed",
                    self.execution._safe_error(error),
                )
            if skill_loaded:
                state.verifier_result = AgentVerificationResult(
                    outcome="replan",
                    feedback=(
                        f"Skill {decision.skill_id} was loaded and materialized; "
                        "decide the next action using its full instructions."
                    ),
                )
                return self._route(state, "replan")

        if decision.kind == "request_input":
            state.final_response_text = None
            state.waiting_reason = (
                decision.response_text
                or decision.rationale_summary
                or "More input is required"
            )
            return self._route(state, "wait_user")
        if decision.kind == "replan":
            state.verifier_result = AgentVerificationResult(
                outcome="replan",
                feedback=decision.rationale_summary,
            )
            return self._route(state, "replan")
        if decision.kind == "respond":
            state.final_response_text = decision.response_text or ""
            unsupported_urls = _has_unsupported_response_urls(
                state, state.final_response_text
            )
            if unsupported_urls and run.mode == RunMode.CHAT:
                grounded_response = _ground_chat_response_url(
                    state, state.final_response_text
                )
                if grounded_response is not None:
                    state.final_response_text = grounded_response
                    unsupported_urls = False
            if unsupported_urls:
                state.final_response_text = None
                state.verifier_result = AgentVerificationResult(
                    outcome="replan",
                    feedback=(
                        "The proposed response cites URLs that are absent from the "
                        "conversation, retrieved context, and tool observations. "
                        "Retrieve verifiable evidence with an available tool before "
                        "responding, or remove the unsupported claims and URLs."
                    ),
                    confidence=1,
                )
                self.runtime.store.append_run_event(
                    run,
                    "agent.verification.required",
                    {"reason": "response_contains_unsupported_url"},
                )
                self.runtime.store.append_run_event(
                    run,
                    "agent.verification.completed",
                    state.verifier_result.model_dump(mode="json"),
                )
                return self._route(state, "replan")
            if decision.verification_required or (
                run.mode != RunMode.CHAT and state.observations
            ):
                workflow_task = self.runtime.store.get_workflow_task_for_child_run(
                    run.tenant_id, run.id
                )
                trusted_authoring = state.runtime_metadata.get(
                    "trusted_authoring_action_completed"
                ) is True
                if decision.verification_required or (
                    workflow_task is None and not trusted_authoring
                ):
                    return self._route(state, "verify")
            state.verifier_result = AgentVerificationResult(
                outcome="complete",
                feedback="Independent verification was not required",
            )
            self.runtime.store.append_run_event(
                run,
                "agent.verification.skipped",
                {"reason": "decision_marked_response_as_grounded"},
            )
            return self._route(state, "complete")
        if not decision.tool_name:
            state.verifier_result = AgentVerificationResult(
                outcome="fail",
                feedback="Action decision did not include tool_name",
            )
            return self._failure(state, "invalid_action_decision")

        action = AgentAction(
            id=new_id("action"),
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            thread_id=run.thread_id,
            run_id=run.id,
            cycle_id=cycle.id,
            action_key=(
                f"attempt:{int(state.runtime_metadata.get('execution_attempt', 0))}:"
                f"{decision.action_key or f'cycle:{state.iteration}:action'}"
            ),
            decision=decision,
        )
        self.runtime.store.create_agent_action(action)
        step = self.execution._decision_step(action)
        state.current_action_id = action.id
        state.plan = [step]
        state.current_step_id = step.id
        return self._route(state, "policy")

    def policy(self, state: AgentRuntimeState) -> dict[str, Any]:
        """执行动作级策略与审批检查。"""

        run = self.runtime.store.get_run(state.tenant_id, state.run_id)
        if self.execution._stop_if_cancelled(state, run):
            return self._route(state, "end")
        action = self._current_action(state)
        if action is None:
            return self._failure(state, "missing_graph_action")
        step = self.execution._decision_step(action)
        state.plan = [step]
        state.current_step_id = step.id
        policy = self.runtime._decide_runtime_step(state, run, step)
        if not policy.allowed:
            self.runtime.store.complete_agent_cycle(
                run.tenant_id,
                action.cycle_id,
                status="waiting",
            )
            failed = self.runtime._fail_for_policy_block(
                state,
                run,
                policy,
                current_step_id=step.id,
            )
            return self._route(failed, "end")
        if self.execution._requires_approval(state, run, action.decision, step):
            self.runtime.store.complete_agent_cycle(
                run.tenant_id,
                action.cycle_id,
                status="waiting",
            )
            tool_policy = self.runtime.tool_gateway.policies.get(step.tool_name)
            if state.runtime_metadata.get("workflow_preview_pending"):
                reason = (
                    "Approve workflow: "
                    f"{state.runtime_metadata.get('workflow_step_count', 0)} steps"
                )
            elif action.decision.approval_required:
                reason = f"Step requires approval: {step.title}"
            elif tool_policy is not None and tool_policy.approval_required:
                reason = f"Tool approval required: {step.tool_name}"
            else:
                reason = "Agent action requires approval before execution"
            paused = self.runtime._pause_for_approval(
                state,
                step,
                reason,
            )
            return self._route(paused, "end")
        return self._route(state, "act")

    def act(self, state: AgentRuntimeState) -> dict[str, Any]:
        """通过工具网关执行持久化动作。"""

        run = self.runtime.store.get_run(state.tenant_id, state.run_id)
        if self.execution._stop_if_cancelled(state, run):
            return self._route(state, "end")
        action = self._current_action(state)
        if action is None:
            return self._failure(state, "missing_graph_action")
        observation = self.execution._execute_durable_action(state, run, action)
        if observation is None:
            return self._route(state, "end")
        return self._route(state, "observe_result")

    def observe_result(self, state: AgentRuntimeState) -> dict[str, Any]:
        """记录动作结果；成功后由模型决定继续执行还是生成答复。"""

        run = self.runtime.store.get_run(state.tenant_id, state.run_id)
        if self.execution._stop_if_cancelled(state, run):
            return self._route(state, "end")
        action = self._current_action(state)
        if action is None:
            return self._failure(state, "missing_graph_action")
        observation = next(
            (
                item
                for item in reversed(state.observations)
                if item.action_id == action.id
            ),
            None,
        )
        if observation is None:
            return self._failure(state, "missing_action_observation")
        if observation.success:
            # 成功观测中断了“连续相同失败”序列，重置升级追踪。
            state.runtime_metadata.pop("last_repair_failure_signature", None)
            self.runtime.store.complete_agent_cycle(
                run.tenant_id,
                action.cycle_id,
                status="completed",
            )
            stdout = str(observation.output.get("stdout") or "").strip()
            compiled_playbook = state.runtime_metadata.get("compiled_playbook")
            compiled_result = (
                compiled_playbook.get("result")
                if isinstance(compiled_playbook, dict)
                else None
            )
            if (
                (action.decision.action_key or "").startswith("playbook:")
                and isinstance(compiled_result, dict)
                and (
                    compiled_result.get("mode") == "artifacts"
                    or (stdout and len(stdout) <= 4_000)
                )
            ):
                state.final_response_text = (
                    stdout
                    if compiled_result.get("mode") == "raw_stdout"
                    else str(compiled_result.get("response_text") or "Playbook completed.")
                )
                state.verifier_result = AgentVerificationResult(
                    outcome="complete",
                    feedback="Compiled Playbook completed without a model call",
                    evidence=["sandbox.command completed successfully"],
                )
                self.runtime.store.append_run_event(
                    run,
                    "agent.verification.skipped",
                    {"reason": "compiled_playbook_result"},
                )
                return self._route(state, "complete")
            if (
                run.mode == RunMode.CHAT
                and action.decision.tool_name == "sandbox.command"
                and action.decision.tool_input.get("result_mode") == "raw_stdout"
                and stdout
                and len(stdout) <= 4_000
            ):
                state.final_response_text = stdout
                state.verifier_result = AgentVerificationResult(
                    outcome="complete",
                    feedback="Sandbox stdout was requested as the final response",
                    evidence=["sandbox.command completed successfully"],
                )
                self.runtime.store.append_run_event(
                    run,
                    "agent.verification.skipped",
                    {"reason": "sandbox_raw_stdout_requested"},
                )
                return self._route(state, "complete")
            return self._route(state, "decide")
        if observation.failure_class == "connector_reconnect_required":
            waiting = self.execution._pause_for_connector_reconnect(
                state,
                run,
                action.cycle_id,
                action,
                observation,
            )
            return self._route(waiting, "end")
        if observation.failure_class == "guardrail_approval_required":
            self.runtime.store.complete_agent_cycle(
                run.tenant_id,
                action.cycle_id,
                status="waiting",
                verifier_result=AgentVerificationResult(
                    outcome="wait_user",
                    feedback=observation.safe_error or "Approval required",
                ),
            )
            return self._route(state, "end")
        if observation.failure_class == "approval_required":
            self.runtime.store.complete_agent_cycle(
                run.tenant_id,
                action.cycle_id,
                status="waiting",
                verifier_result=AgentVerificationResult(
                    outcome="wait_user",
                    feedback=observation.safe_error or "Approval required",
                ),
            )
            paused = self.runtime._pause_for_approval(
                state,
                self.execution._decision_step(action),
                observation.safe_error or "Agent action requires approval",
            )
            return self._route(paused, "end")
        action_key = action.decision.action_key or ""
        if action_key.startswith(("planned:", "playbook:")):
            if observation.failure_class == "tool_execution_error":
                from taroai.tool_gateway import ToolExecutionError

                retries = dict(
                    state.runtime_metadata.get("static_plan_retries", {})
                )
                step = self.execution._decision_step(action)
                self.runtime._record_tool_execution_error(
                    state,
                    run,
                    step,
                    ToolExecutionError(
                        observation.safe_error or "Tool execution failed"
                    ),
                    int(retries.get(step.id, 0)) + 1,
                )
            if (
                action_key.startswith("planned:")
                and observation.failure_class == "tool_execution_error"
                and self._retry_static_action(
                    state,
                    run,
                    action,
                    observation.safe_error,
                )
            ):
                state.verifier_result = _tool_failure_verification(observation)
                return self._route(state, "repair")
            failure_code = observation.failure_class or "tool_execution_error"
            failure_detail = observation.safe_error or observation.error
            if failure_code == "command_failed":
                exit_code = observation.output.get("exit_code")
                failure_code = "sandbox_command_failed"
                failure_detail = (
                    f"sandbox.command failed with exit code {exit_code}"
                )
                state.graph_failure_metadata = {
                    "reason": failure_code,
                    "step_id": self.execution._decision_step(action).id,
                    "exit_code": exit_code,
                }
            elif failure_detail == (
                "sandbox artifact path must be under /workspace/artifacts/"
            ):
                failure_code = "sandbox_artifact_path_rejected"
                state.graph_failure_metadata = {
                    "reason": failure_code,
                    "step_id": self.execution._decision_step(action).id,
                }
            elif failure_detail == "storage content rejected by scan policy":
                failure_code = "storage_content_rejected"
                state.graph_failure_metadata = {
                    "reason": failure_code,
                    "step_id": self.execution._decision_step(action).id,
                }
            elif failure_code == "policy_blocked":
                failure_code = "artifact_guardrail_blocked"
            return self._failure(state, failure_code, failure_detail)
        state.verifier_result = _tool_failure_verification(observation)
        return self._route(state, "repair")

    def verify(self, state: AgentRuntimeState) -> dict[str, Any]:
        """根据可观察证据校验任务结果。"""

        run = self.runtime.store.get_run(state.tenant_id, state.run_id)
        if self.execution._stop_if_cancelled(state, run):
            return self._route(state, "end")
        decision = state.last_decision
        if decision is None:
            return self._failure(state, "missing_graph_decision")
        try:
            result = self.execution._verify(state, run, decision)
        except ModelBudgetExceededError as error:
            self._record_model_failure_audit(run, "model.budget_exceeded", error)
            return self._failure(state, "model_budget_exceeded", str(error))
        except ModelPolicyDeniedError as error:
            self._record_model_failure_audit(run, "model.policy_denied", error)
            return self._failure(state, "model_policy_denied", str(error))
        except ModelGatewayError as error:
            if (
                run.mode == RunMode.CHAT
                and decision.kind == "respond"
                and not isinstance(error, ModelSafetyRefusalError)
            ):
                self._record_model_failure_audit(run, "model.gateway_failed", error)
                state.final_response_text = None
                state.verifier_result = AgentVerificationResult(
                    outcome="replan",
                    feedback=(
                        "Independent verification was unavailable. Return a conservative response "
                        "without claimed actions or unsupported facts; when no matching connector "
                        "exists, state that nothing was performed and name the required connection."
                    ),
                )
                return self._route(state, "replan")
            self.runtime._record_model_gateway_failure(state, run, error)
            if isinstance(error, ModelSafetyRefusalError):
                return self._route(state, "end")
            raise
        if self.execution._stop_if_cancelled(state, run):
            return self._route(state, "end")
        if result.outcome != "complete":
            state.final_response_text = None
        if result.outcome == "wait_user":
            state.waiting_reason = result.feedback or "More input is required"
        return self._route(state, result.outcome)

    def repair(self, state: AgentRuntimeState) -> dict[str, Any]:
        """记录失败反馈并回到决策节点。"""

        run = self.runtime.store.get_run(state.tenant_id, state.run_id)
        if self.execution._stop_if_cancelled(state, run):
            return self._route(state, "end")
        result = state.verifier_result or AgentVerificationResult(
            outcome="repair",
            feedback="Repair required",
        )
        if state.current_cycle_id is not None:
            self.runtime.store.complete_agent_cycle(
                run.tenant_id,
                state.current_cycle_id,
                status="completed",
                verifier_result=result,
            )
        state.repair_attempts += 1
        signature = _failure_signature(result)
        # 静态计划重试有独立的 max_step_retries 预算，不参与升级追踪。
        trackable = bool(signature) and not (
            state.last_decision is not None
            and (state.last_decision.action_key or "").startswith("planned:")
        )
        escalated = trackable and signature == state.runtime_metadata.get(
            "last_repair_failure_signature"
        )
        if escalated:
            # 相同失败连续出现两次：换策略而不是再做一次相同的修复。
            state.runtime_metadata.pop("last_repair_failure_signature", None)
            state.runtime_metadata["repair_escalated"] = True
            state.active_plan_revision += 1
            state.replan_count += 1
            result = result.model_copy(
                update={
                    "feedback": (
                        "The previous approach failed twice in a row with the "
                        f"identical failure: {result.feedback}. Do not retry the "
                        "same approach or tool call with the same inputs. Choose "
                        "a different approach or tool, or respond truthfully "
                        "about the limitation."
                    ),
                }
            )
            state.verifier_result = result
            self.runtime.store.append_run_event(
                run,
                "agent.repair.escalated",
                {
                    "repair_attempt": state.repair_attempts,
                    "failure_signature": signature,
                    "plan_revision": state.active_plan_revision,
                },
            )
        elif trackable:
            state.runtime_metadata["last_repair_failure_signature"] = signature
            state.runtime_metadata.pop("repair_escalated", None)
        state.runtime_metadata["previous_verification"] = result.model_dump(
            mode="json"
        )
        self.runtime.store.append_run_event(
            run,
            "agent.repair.started",
            {
                "repair_attempt": state.repair_attempts,
                "feedback": result.feedback,
                "escalated": escalated,
            },
        )
        self.execution._persist_checkpoint(
            state,
            run,
            cycle_id=state.current_cycle_id,
        )
        if state.repair_attempts > state.max_repairs:
            return self._failure(state, "repair_budget_exhausted")
        return self._route(state, "decide")

    def replan(self, state: AgentRuntimeState) -> dict[str, Any]:
        """更新计划版本并回到决策节点。"""

        run = self.runtime.store.get_run(state.tenant_id, state.run_id)
        if self.execution._stop_if_cancelled(state, run):
            return self._route(state, "end")
        result = state.verifier_result or AgentVerificationResult(
            outcome="replan",
            feedback="Replan required",
        )
        if state.current_cycle_id is not None:
            self.runtime.store.complete_agent_cycle(
                run.tenant_id,
                state.current_cycle_id,
                status="completed",
                verifier_result=result,
            )
        state.active_plan_revision += 1
        state.replan_count += 1
        # 重规划本身就是策略切换，连续相同失败的追踪从头开始。
        state.runtime_metadata.pop("last_repair_failure_signature", None)
        state.runtime_metadata["previous_verification"] = result.model_dump(
            mode="json"
        )
        self.runtime.store.append_run_event(
            run,
            "agent.plan.revised",
            {
                "plan_revision": state.active_plan_revision,
                "feedback": result.feedback,
            },
        )
        self.execution._persist_checkpoint(
            state,
            run,
            cycle_id=state.current_cycle_id,
        )
        return self._route(state, "decide")

    def complete(self, state: AgentRuntimeState) -> dict[str, Any]:
        """完成当前周期并生成最终结果。"""

        run = self.runtime.store.get_run(state.tenant_id, state.run_id)
        if self.execution._stop_if_cancelled(state, run):
            return self._route(state, "end")
        if state.current_cycle_id is not None:
            self.runtime.store.complete_agent_cycle(
                run.tenant_id,
                state.current_cycle_id,
                status="completed",
                verifier_result=state.verifier_result,
            )
        finalized = self.execution._finalize(state, run)
        return self._route(finalized, "end")

    def wait_user(self, state: AgentRuntimeState) -> dict[str, Any]:
        """保存等待原因并结束本次图调用。"""

        run = self.runtime.store.get_run(state.tenant_id, state.run_id)
        if self.execution._stop_if_cancelled(state, run):
            return self._route(state, "end")
        if state.current_cycle_id is None:
            return self._failure(state, "missing_graph_cycle")
        waiting = self.execution._wait_for_user(
            state,
            run,
            state.current_cycle_id,
            state.waiting_reason or "More input is required",
        )
        return self._route(waiting, "end")

    def fail(self, state: AgentRuntimeState) -> dict[str, Any]:
        """统一落库失败状态和终止事件。"""

        run = self.runtime.store.get_run(state.tenant_id, state.run_id)
        if self.execution._stop_if_cancelled(state, run):
            return self._route(state, "end")
        if state.current_cycle_id is not None:
            self.runtime.store.complete_agent_cycle(
                run.tenant_id,
                state.current_cycle_id,
                status="failed",
                verifier_result=state.verifier_result,
            )
        reason = state.graph_failure_code or "agent_graph_failed"
        failed = self.execution._fail(
            state,
            run,
            reason,
            detail=state.graph_failure_detail,
            timed_out=reason == "elapsed_budget_exhausted",
        )
        return self._route(failed, "end")

    def _current_action(self, state: AgentRuntimeState) -> AgentAction | None:
        if state.current_action_id is None:
            return None
        return self.runtime.store.get_agent_action(
            state.tenant_id,
            state.current_action_id,
        )

    def _retry_static_action(
        self,
        state: AgentRuntimeState,
        run,
        action: AgentAction,
        error: str | None,
    ) -> bool:
        step = self.execution._decision_step(action)
        retries = dict(state.runtime_metadata.get("static_plan_retries", {}))
        current = int(retries.get(step.id, 0))
        if current >= self.runtime.max_step_retries:
            return False
        next_attempt = current + 1
        retries[step.id] = next_attempt
        state.runtime_metadata["static_plan_retries"] = retries
        state.pending_actions.insert(
            0,
            action.decision.model_copy(
                update={
                    "action_key": f"planned:{step.id}:retry:{next_attempt}",
                }
            ),
        )
        self.runtime.store.append_run_event(
            run,
            "step.retrying",
            {
                "step_id": step.id,
                "tool_name": step.tool_name,
                "next_attempt": next_attempt + 1,
            },
        )
        return True

    def _record_model_failure_audit(self, run, event_type: str, error) -> None:
        metadata = getattr(error, "metadata", None)
        if not isinstance(metadata, dict):
            metadata = {
                "error_type": error.__class__.__name__,
                "message": str(error),
            }
        self.runtime._record_audit_event(
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            user_id=run.user_id,
            run_id=run.id,
            event_type=event_type,
            metadata=metadata,
        )

    def _failure(
        self,
        state: AgentRuntimeState,
        code: str,
        detail: str | None = None,
    ) -> dict[str, Any]:
        state.graph_failure_code = code
        state.graph_failure_detail = detail
        return self._route(state, "fail")

    @staticmethod
    def _route(
        state: AgentRuntimeState,
        route: AgentGraphRoute,
    ) -> AgentRuntimeState:
        # 直接返回状态实例，避免每个节点跳转都全量 model_dump 再重新校验。
        state.graph_route = route
        return state
