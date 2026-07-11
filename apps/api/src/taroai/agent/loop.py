from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from taroai.agent.models import (
    AgentAction,
    AgentCheckpoint,
    AgentCycle,
    AgentDecision,
    AgentObservation,
    AgentVerificationResult,
)
from taroai.agent.planning import PlanStep
from taroai.agent.state import AgentRuntimeState
from taroai.connectors import (
    CONNECTOR_INVOCATION_METER,
    ConnectorCredentialExpiredError,
    ConnectorInvocationRequest,
    ConnectorInvocationStatus,
    ConnectorStatus,
)
from taroai.domain import (
    ChatMessageCreate,
    ChatMessageDeliveryStatus,
    ChatMessageDispatchStatus,
    ChatMessageRole,
    Run,
    RunMode,
    RunStatus,
    new_id,
    utc_now,
)
from taroai.errors import AgentActionLeaseConflictError, NotFoundError
from taroai.model_gateway import (
    ModelBudgetExceededError,
    ModelGatewayError,
    ModelGatewayRequest,
    ModelMessage,
    ModelPolicyDeniedError,
)
from taroai.sandbox.models import SandboxFileWrite
from taroai.store import TERMINAL_RUN_STATUSES
from taroai.tool_gateway import (
    ToolApprovalRequiredError,
    ToolExecutionError,
    ToolResult,
)

if TYPE_CHECKING:
    from taroai.agent.runtime import AgentRuntime


@dataclass
class AgentLoopV2:
    runtime: "AgentRuntime"

    def execute_run(self, tenant_id: str, run_id: str) -> AgentRuntimeState:
        run = self.runtime.store.get_run(tenant_id, run_id)
        if run.status in TERMINAL_RUN_STATUSES:
            return self._restore_state(run)
        self.runtime.store.update_run_status(
            tenant_id,
            run_id,
            RunStatus.RUNNING,
            emit_status_event=False,
        )
        state = self._restore_state(run)
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
        if not state.retrieved_context.knowledge_results and not state.retrieved_context.memory_records:
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
                "mode": "loop_v2",
                "max_iterations": state.max_iterations,
                "max_repairs": state.max_repairs,
                "deadline_at": state.deadline_at.isoformat() if state.deadline_at else None,
                "cost_limit": state.cost_limit,
                "checkpoint_sequence": state.checkpoint_sequence,
            },
        )

        policy = self.runtime._decide_runtime_execution(run)
        if not policy.allowed:
            return self.runtime._pause_for_policy_block(state, run, policy)
        try:
            self._validate_explicit_skill_refs(state, run)
            self._load_agent_context(state, run)
        except Exception as error:
            return self._fail(
                state,
                run,
                "invalid_resource_reference",
                detail=self._safe_error(error),
            )
        conversation = self._conversation_context(state, run)
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
                    "compaction_version": conversation.get(
                        "compaction_version", 1
                    ),
                },
            )
            self.runtime._save_state(state)

        recovered = [
            action
            for action in self.runtime.store.recover_expired_agent_actions(tenant_id)
            if action.run_id == run.id
        ]
        if recovered:
            self.runtime.store.append_run_event(
                run,
                "agent.actions.recovered",
                {"action_ids": [action.id for action in recovered]},
            )
        actions = self.runtime.store.list_agent_actions(tenant_id, run.id)
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
            self._persist_checkpoint(state, run)
        uncertain = next((item for item in actions if item.status == "uncertain"), None)
        if uncertain is not None:
            return self._wait_for_uncertain_resolution(state, run, uncertain)
        running = next((item for item in actions if item.status == "running"), None)
        if running is not None:
            state.waiting_reason = "action_is_owned_by_another_live_worker"
            self.runtime._save_state(state)
            return state
        pending = next((item for item in actions if item.status == "pending"), None)
        if pending is not None:
            state.iteration = max(state.iteration, self._action_iteration(pending))
            pending_step = self._decision_step(pending)
            state.plan = [pending_step]
            state.current_step_id = pending_step.id
            pending_policy = self.runtime._decide_runtime_step(
                state,
                run,
                pending_step,
            )
            if not pending_policy.allowed:
                return self.runtime._pause_for_policy_block(
                    state,
                    run,
                    pending_policy,
                    current_step_id=pending_step.id,
                )
            if self._requires_approval(
                state,
                run,
                pending.decision,
                pending_step,
            ):
                return self.runtime._pause_for_approval(
                    state,
                    pending_step,
                    "Agent action requires approval before execution",
                )
            observation = self._execute_durable_action(state, run, pending)
            if observation is None:
                return state
            if observation.success:
                verification = self._verify(state, run, pending.decision)
                routed = self._route_verification(
                    state,
                    run,
                    pending.cycle_id,
                    verification,
                )
                if routed is not None:
                    return routed
            else:
                if observation.failure_class == "connector_reconnect_required":
                    return self._pause_for_connector_reconnect(
                        state, run, pending.cycle_id, pending, observation
                    )
                if observation.failure_class in {"approval_required", "policy_blocked"}:
                    self.runtime.store.complete_agent_cycle(
                        run.tenant_id,
                        pending.cycle_id,
                        status="waiting",
                        verifier_result=AgentVerificationResult(
                            outcome="wait_user",
                            feedback=observation.safe_error or "Approval required",
                        ),
                    )
                    return self.runtime._pause_for_approval(
                        state,
                        pending_step,
                        observation.safe_error or "Agent action requires approval",
                    )
                state.repair_attempts += 1
                self.runtime.store.complete_agent_cycle(
                    run.tenant_id,
                    pending.cycle_id,
                    status="completed",
                    verifier_result=AgentVerificationResult(
                        outcome="repair",
                        feedback=observation.safe_error or observation.error or "Action failed",
                    ),
                )
                if state.repair_attempts > state.max_repairs:
                    return self._fail(state, run, "repair_budget_exhausted")

        while True:
            budget_failure = self._budget_failure(state)
            if budget_failure is not None:
                return self._fail(state, run, budget_failure, timed_out=budget_failure == "elapsed_budget_exhausted")
            if self._is_cancelled(run):
                state.status = RunStatus.CANCELLED
                self.runtime._save_state(state)
                return state
            self._consume_steering_at_checkpoint(state, run)
            state.iteration += 1
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
                decision = self._decide(state, run)
            except ModelBudgetExceededError as error:
                return self._fail(state, run, "model_budget_exceeded", detail=str(error))
            except ModelPolicyDeniedError as error:
                return self._fail(state, run, "model_policy_denied", detail=str(error))
            except ModelGatewayError as error:
                return self._fail(state, run, "model_gateway_error", detail=str(error))
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
                budget_snapshot=self._budget_snapshot(state),
            )
            self.runtime.store.create_agent_cycle(cycle)
            self.runtime.store.append_run_event(
                run,
                "agent.decision.created",
                {
                    "cycle_id": cycle.id,
                    "iteration": cycle.iteration,
                    "decision": decision.model_dump(mode="json"),
                },
            )

            if decision.kind == "action" and decision.skill_id:
                try:
                    skill_loaded = self._prepare_selected_skill(state, run, decision)
                except Exception as error:
                    self.runtime.store.complete_agent_cycle(
                        run.tenant_id,
                        cycle.id,
                        status="failed",
                        verifier_result=AgentVerificationResult(
                            outcome="fail",
                            feedback=self._safe_error(error),
                        ),
                    )
                    return self._fail(
                        state,
                        run,
                        "skill_load_failed",
                        detail=self._safe_error(error),
                    )
                if skill_loaded:
                    state.active_plan_revision += 1
                    self.runtime.store.complete_agent_cycle(
                        run.tenant_id,
                        cycle.id,
                        status="completed",
                        verifier_result=AgentVerificationResult(
                            outcome="replan",
                            feedback=(
                                f"Skill {decision.skill_id} was loaded and materialized; "
                                "decide the next action using its full instructions."
                            ),
                        ),
                    )
                    self._persist_checkpoint(state, run, cycle_id=cycle.id)
                    continue

            if decision.kind == "request_input":
                return self._wait_for_user(
                    state,
                    run,
                    cycle.id,
                    decision.response_text or decision.rationale_summary or "More input is required",
                )
            if decision.kind == "replan":
                state.active_plan_revision += 1
                state.replan_count += 1
                self.runtime.store.complete_agent_cycle(
                    run.tenant_id,
                    cycle.id,
                    status="completed",
                    verifier_result=AgentVerificationResult(
                        outcome="replan",
                        feedback=decision.rationale_summary,
                    ),
                )
                self._persist_checkpoint(state, run, cycle_id=cycle.id)
                self.runtime.store.append_run_event(
                    run,
                    "agent.plan.revised",
                    {"plan_revision": state.active_plan_revision},
                )
                continue
            if decision.kind == "respond":
                state.final_response_text = decision.response_text or ""
                verification = self._verify(state, run, decision)
                routed = self._route_verification(
                    state,
                    run,
                    cycle.id,
                    verification,
                )
                if routed is not None:
                    return routed
                continue

            if not decision.tool_name:
                self.runtime.store.complete_agent_cycle(
                    run.tenant_id,
                    cycle.id,
                    status="failed",
                    verifier_result=AgentVerificationResult(
                        outcome="fail",
                        feedback="Action decision did not include tool_name",
                    ),
                )
                return self._fail(state, run, "invalid_action_decision")

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
            step = self._decision_step(action)
            state.plan = [step]
            state.current_step_id = step.id
            policy_decision = self.runtime._decide_runtime_step(state, run, step)
            if not policy_decision.allowed:
                self.runtime.store.complete_agent_cycle(
                    run.tenant_id, cycle.id, status="waiting"
                )
                return self.runtime._pause_for_policy_block(
                    state,
                    run,
                    policy_decision,
                    current_step_id=step.id,
                )
            if self._requires_approval(state, run, decision, step):
                self.runtime.store.complete_agent_cycle(
                    run.tenant_id, cycle.id, status="waiting"
                )
                return self.runtime._pause_for_approval(
                    state,
                    step,
                    "Agent action requires approval before execution",
                )
            observation = self._execute_durable_action(state, run, action)
            if observation is None:
                return state
            if not observation.success:
                if observation.failure_class == "connector_reconnect_required":
                    return self._pause_for_connector_reconnect(
                        state, run, cycle.id, action, observation
                    )
                if observation.failure_class in {
                    "approval_required",
                    "policy_blocked",
                }:
                    self.runtime.store.complete_agent_cycle(
                        run.tenant_id,
                        cycle.id,
                        status="waiting",
                        verifier_result=AgentVerificationResult(
                            outcome="wait_user",
                            feedback=observation.safe_error or "Approval required",
                        ),
                    )
                    return self.runtime._pause_for_approval(
                        state,
                        step,
                        observation.safe_error or "Agent action requires approval",
                    )
                state.repair_attempts += 1
                self.runtime.store.complete_agent_cycle(
                    run.tenant_id,
                    cycle.id,
                    status="completed",
                    verifier_result=AgentVerificationResult(
                        outcome="repair",
                        feedback=observation.safe_error or observation.error or "Action failed",
                    ),
                )
                self.runtime.store.append_run_event(
                    run,
                    "agent.repair.started",
                    {
                        "repair_attempt": state.repair_attempts,
                        "failure_class": observation.failure_class,
                    },
                )
                if state.repair_attempts > state.max_repairs:
                    return self._fail(state, run, "repair_budget_exhausted")
                continue
            verification = self._verify(state, run, decision)
            routed = self._route_verification(
                state,
                run,
                cycle.id,
                verification,
            )
            if routed is not None:
                return routed

    def checkpoint_cancel(self, state: AgentRuntimeState, run: Run) -> None:
        state.status = RunStatus.CANCELLED
        state.waiting_reason = "cancelled_by_user"
        self._persist_checkpoint(state, run)

    def _restore_state(self, run: Run) -> AgentRuntimeState:
        checkpoint = self.runtime.store.get_latest_agent_checkpoint(run.tenant_id, run.id)
        if checkpoint is not None and checkpoint.state_payload:
            state = AgentRuntimeState.model_validate(checkpoint.state_payload)
            state.checkpoint_sequence = checkpoint.sequence
            return state
        try:
            return self.runtime._load_state(run.tenant_id, run.id)
        except NotFoundError:
            return self.runtime._initial_state(run)

    def _discover_skill_summaries(self, run: Run) -> list[dict[str, Any]]:
        service = self.runtime.skill_service
        if service is None:
            return []
        return [
            item.model_dump(mode="json")
            for item in service.discover(
                tenant_id=run.tenant_id,
                workspace_id=run.workspace_id,
                user_id=run.user_id,
            )
        ]

    def _discover_connector_tools(self, run: Run) -> list[dict[str, Any]]:
        registry = self.runtime.connector_registry
        if registry is None:
            return []
        explicit_ids = {
            reference.id for reference in run.resource_refs if reference.type == "connector"
        }
        tools: list[dict[str, Any]] = []
        for connector in registry.list_connectors(run.tenant_id, run.workspace_id):
            if connector.status != ConnectorStatus.ENABLED:
                continue
            if explicit_ids and connector.id not in explicit_ids:
                continue
            for capability in connector.capabilities:
                if not capability.enabled:
                    continue
                tools.append(
                    {
                        "tool_name": f"connector.{connector.id}.{capability.name}",
                        "connector_id": connector.id,
                        "display_name": connector.display_name,
                        "capability": capability.name,
                        "input_schema": capability.input_schema,
                        "required_scopes": capability.required_scopes,
                        "risk_level": capability.risk_level,
                        "approval_required": capability.approval_required,
                    }
                )
        return tools

    def _conversation_context(
        self,
        state: AgentRuntimeState,
        run: Run,
    ) -> dict[str, Any]:
        cached = state.runtime_metadata.get("conversation_context")
        if isinstance(cached, dict):
            return cached
        if run.thread_id is None:
            return {"messages": [], "omitted_message_count": 0}
        messages = self.runtime.store.list_chat_messages(run.tenant_id, run.thread_id)
        trigger_sequence = None
        if run.trigger_message_id is not None:
            trigger = next(
                (item for item in messages if item.id == run.trigger_message_id),
                None,
            )
            trigger_sequence = trigger.sequence if trigger is not None else None
        prior = [
            item
            for item in messages
            if item.id != run.trigger_message_id
            and (trigger_sequence is None or item.sequence < trigger_sequence)
            and item.dispatch_status
            not in {
                ChatMessageDispatchStatus.CANCELLED,
                ChatMessageDispatchStatus.FAILED,
            }
        ]
        selected: list[dict[str, Any]] = []
        used_characters = 0
        character_budget = 24_000
        for message in reversed(prior):
            content = message.content.strip()
            if not content:
                continue
            remaining = character_budget - used_characters
            if remaining <= 0:
                break
            clipped = content[:remaining]
            selected.append(
                {
                    "sequence": message.sequence,
                    "role": message.role.value,
                    "content": clipped,
                    "attachments": message.attachments,
                    "resource_refs": [
                        item.model_dump(mode="json") for item in message.resource_refs
                    ],
                }
            )
            used_characters += len(clipped)
        selected.reverse()
        context = {
            "messages": selected,
            "total_prior_message_count": len(prior),
            "omitted_message_count": max(0, len(prior) - len(selected)),
            "character_count": used_characters,
            "compaction_version": 1,
        }
        state.runtime_metadata["conversation_context"] = context
        state.runtime_metadata["context_compaction_version"] = 1
        return context

    def _load_agent_context(
        self,
        state: AgentRuntimeState,
        run: Run,
    ) -> dict[str, Any] | None:
        cached = state.runtime_metadata.get("agent_context")
        if isinstance(cached, dict):
            return cached
        references = [item for item in run.resource_refs if item.type == "agent"]
        agent_ids = {item.id for item in references}
        if run.agent_id:
            agent_ids.add(run.agent_id)
        if not agent_ids:
            return None
        if len(agent_ids) != 1:
            raise ValueError("A chat turn can bind only one reusable agent")
        registry = self.runtime.agent_registry
        if registry is None:
            raise ValueError("agent registry is not configured")
        agent_id = next(iter(agent_ids))
        definition = registry.get(run.tenant_id, agent_id)
        if definition.workspace_id != run.workspace_id:
            raise ValueError("agent is not available in this workspace")
        reference = next((item for item in references if item.id == agent_id), None)
        version_number = (
            int(reference.version)
            if reference is not None and reference.version is not None
            else definition.published_version
        )
        if version_number is None:
            if reference is not None:
                raise ValueError("mentioned agent has no published version")
            return None
        version = registry.get_version(run.tenant_id, agent_id, version_number)
        if reference is not None and version.status != "published":
            raise ValueError("mentioned agent version is not published")
        context = {
            "agent_id": definition.id,
            "name": definition.name,
            "description": definition.description,
            "version": version.version,
            "instructions": version.spec.instructions,
            "input_schema": version.spec.input_schema,
            "output_contract": version.spec.output_contract,
            "skill_bindings": version.spec.skill_bindings,
            "connector_bindings": version.spec.connector_bindings,
            "knowledge_bindings": version.spec.knowledge_bindings,
            "reference_files": version.spec.reference_files,
        }
        state.runtime_metadata["agent_context"] = context
        self.runtime.store.append_run_event(
            run,
            "agent.definition.loaded",
            {
                "agent_id": definition.id,
                "agent_version": version.version,
                "source": "mention" if reference is not None else "run",
            },
        )
        self.runtime._save_state(state)
        return context

    def _validate_explicit_skill_refs(
        self,
        state: AgentRuntimeState,
        run: Run,
    ) -> None:
        references = [item for item in run.resource_refs if item.type == "skill"]
        if not references:
            return
        if self.runtime.skill_service is None:
            raise ValueError("skill runtime is not configured")
        summaries = {
            item["skill_id"]: item for item in self._discover_skill_summaries(run)
        }
        resolved: list[dict[str, Any]] = []
        for reference in references:
            summary = summaries.get(reference.id)
            if summary is None:
                raise ValueError(
                    f"Skill is not installed, enabled, visible, or completely pinned: {reference.id}"
                )
            if reference.version is not None and reference.version != summary["version"]:
                raise ValueError(
                    f"Installed skill version does not match resource reference: {reference.id}"
                )
            self.runtime.skill_service.load_skill(
                tenant_id=run.tenant_id,
                workspace_id=run.workspace_id,
                skill_id=reference.id,
                expected_version=summary["version"],
                expected_package_digest=summary["package_digest"],
                expected_source_digest=summary["source_digest"],
            )
            resolved.append(summary)
        state.runtime_metadata["explicit_skill_refs"] = resolved
        self.runtime._save_state(state)

    def _prepare_selected_skill(
        self,
        state: AgentRuntimeState,
        run: Run,
        decision: AgentDecision,
    ) -> bool:
        service = self.runtime.skill_service
        if service is None or decision.skill_id is None:
            raise ValueError("skill runtime is not configured")
        summary = next(
            (
                item
                for item in self._discover_skill_summaries(run)
                if item["skill_id"] == decision.skill_id
            ),
            None,
        )
        if summary is None:
            raise ValueError(
                f"Skill is not installed, enabled, visible, or completely pinned: {decision.skill_id}"
            )
        loaded_context = state.runtime_metadata.setdefault("loaded_skill_context", {})
        existing = loaded_context.get(decision.skill_id)
        if existing is not None:
            if any(
                existing.get(key) != summary[key]
                for key in ("version", "package_digest", "source_digest")
            ):
                raise ValueError("loaded skill pin changed during the run")
            self._validate_skill_requirements(run, decision, decision.skill_id)
            return False

        loaded = service.load_skill(
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            skill_id=decision.skill_id,
            expected_version=summary["version"],
            expected_package_digest=summary["package_digest"],
            expected_source_digest=summary["source_digest"],
        )
        self._validate_skill_requirements(run, decision, decision.skill_id)
        plan = service.materialization_plan(
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            skill_id=decision.skill_id,
        )
        requested_image = plan.runtime_sandbox
        if requested_image and requested_image != "skill-package":
            current_image = state.runtime_metadata.get("skill_runtime_image")
            if current_image is not None and current_image != requested_image:
                raise ValueError("Selected skills require incompatible runtime images")
            state.runtime_metadata["skill_runtime_image"] = requested_image
        session = self.runtime._ensure_sandbox_session(state)
        for item in plan.writes:
            self.runtime.sandbox_adapter.upload_file(
                SandboxFileWrite(
                    tenant_id=run.tenant_id,
                    workspace_id=run.workspace_id,
                    run_id=run.id,
                    session_id=session.id,
                    path=item.path,
                    content_base64=base64.b64encode(item.content).decode("ascii"),
                    content_type="application/octet-stream",
                )
            )
        pin = {
            "skill_id": loaded.skill_id,
            "version": loaded.version,
            "package_digest": loaded.package_digest,
            "source_digest": loaded.source_digest,
            "source_type": loaded.source_type,
            "root_path": plan.root_path,
        }
        loaded_context[decision.skill_id] = {
            **pin,
            "name": summary["name"],
            "description": summary["description"],
            "skill_md": loaded.skill_md,
        }
        used_skills = state.runtime_metadata.setdefault("used_skills", [])
        used_skills[:] = [
            item for item in used_skills if item.get("skill_id") != loaded.skill_id
        ]
        used_skills.append(pin)
        state.runtime_metadata.setdefault("materialized_skills", {})[
            decision.skill_id
        ] = {
            **pin,
            "file_count": len(plan.writes),
            "size_bytes": sum(item.size_bytes for item in plan.writes),
        }
        self.runtime.store.append_run_event(
            run,
            "agent.skill.loaded",
            pin,
        )
        self.runtime.store.append_run_event(
            run,
            "agent.skill.materialized",
            {
                **pin,
                "file_count": len(plan.writes),
                "size_bytes": sum(item.size_bytes for item in plan.writes),
            },
        )
        self.runtime.store.record_billing_meter(
            tenant_id=run.tenant_id,
            run_id=run.id,
            skill_id=loaded.skill_id,
            meter_type="skill_call_count",
            quantity=1,
            unit="call",
            metadata={
                "version": loaded.version,
                "package_digest": loaded.package_digest,
                "source_digest": loaded.source_digest,
            },
        )
        self.runtime._save_state(state)
        return True

    def _validate_skill_requirements(
        self,
        run: Run,
        decision: AgentDecision,
        skill_id: str,
    ) -> None:
        package = self.runtime.skill_service.registry.get_installed_package(
            run.tenant_id,
            run.workspace_id,
            skill_id,
        )
        spec = package.taroai_config.get("spec", {})
        tools = self._requirement_ids(spec.get("tools", []))
        if tools and decision.tool_name not in tools:
            raise ValueError(
                f"Skill {skill_id} does not authorize tool {decision.tool_name}"
            )
        refs = {(item.type, item.id) for item in run.resource_refs}
        for kind, key in (
            ("connector", "connectors"),
            ("knowledge", "knowledgeBindings"),
        ):
            missing = [
                item
                for item in self._requirement_ids(spec.get(key, []))
                if (kind, item) not in refs
            ]
            if missing:
                raise ValueError(
                    f"Skill {skill_id} requires explicit {kind} bindings: {', '.join(missing)}"
                )

    def _requirement_ids(self, values: Any) -> list[str]:
        if not isinstance(values, list):
            return []
        resolved: list[str] = []
        for item in values:
            if isinstance(item, str) and item:
                resolved.append(item)
            elif isinstance(item, dict):
                value = item.get("id") or item.get("name")
                if value:
                    resolved.append(str(value))
        return resolved

    def _decide(self, state: AgentRuntimeState, run: Run) -> AgentDecision:
        observations = [item.model_dump(mode="json") for item in state.observations[-8:]]
        skill_summaries = self._discover_skill_summaries(run)
        connector_tools = self._discover_connector_tools(run)
        conversation = self._conversation_context(state, run)
        agent_context = self._load_agent_context(state, run)
        loaded_skills = list(
            state.runtime_metadata.get("loaded_skill_context", {}).values()
        )
        messages = [
            ModelMessage(
                role="system",
                content=(
                    "You are Taroai's iterative agent controller. Decide exactly one next "
                    "observable action. Return strict JSON matching: kind=action|respond|"
                    "request_input|replan; for action include tool_name and tool_input; for "
                    "respond include response_text. If reusable_agent is present, treat its "
                    "published instructions and output contract as the active workflow. "
                    "When current_request includes files, sandbox actions can read them from "
                    "their declared /workspace/inputs paths. Never guess a different path. "
                    "Never repeat a failed side-effecting action "
                    "unchanged. Available skills are compact summaries: select one by setting "
                    "skill_id on an action, then the controller will load its full SKILL.md and "
                    "ask you to decide again. Keep skill_id on actions performed for a loaded "
                    "skill. When the user explicitly asks to create a reusable skill, use "
                    "skill.package.create_draft with complete SKILL.md instructions and any "
                    "small supporting text files; explain that evaluation and publish are the "
                    "next governance steps. Use the observations and steering messages to "
                    "repair or replan."
                ),
            ),
            ModelMessage(
                role="user",
                content=json.dumps(
                    {
                        "goal": state.goal,
                        "current_request": {
                            "attachments": run.attachments,
                            "files": self.runtime._attachment_descriptors(run),
                            "resource_refs": [
                                item.model_dump(mode="json")
                                for item in run.resource_refs
                            ],
                        },
                        "conversation": conversation,
                        "reusable_agent": agent_context,
                        "iteration": state.iteration,
                        "plan_revision": state.active_plan_revision,
                        "observations": observations,
                        "steering_messages": state.steering_messages,
                        "available_tools": sorted(
                            [*self.runtime.tool_gateway.policies]
                            + [item["tool_name"] for item in connector_tools]
                        ),
                        "available_connectors": connector_tools,
                        "available_skills": skill_summaries,
                        "loaded_skills": loaded_skills,
                    },
                    ensure_ascii=False,
                ),
            ),
        ]
        request = self._model_request(run, messages, operation="decide")
        self.runtime.model_budget_guard.assert_plan_allowed(
            self.runtime.store, run.tenant_id, run.id
        )
        decision = self.runtime.model_gateway.decide_next_action(request)
        if (
            decision.tool_name is not None
            and decision.tool_name not in self.runtime.tool_gateway.policies
        ):
            canonical_tool_name = decision.tool_name.replace("__", ".")
            available_connector_names = {
                item["tool_name"] for item in connector_tools
            }
            if (
                canonical_tool_name in self.runtime.tool_gateway.policies
                or canonical_tool_name in available_connector_names
            ):
                decision = decision.model_copy(
                    update={"tool_name": canonical_tool_name}
                )
        self._record_model_operation(run, "decide", request)
        return decision

    def _verify(
        self,
        state: AgentRuntimeState,
        run: Run,
        decision: AgentDecision,
    ) -> AgentVerificationResult:
        request = self._model_request(
            run,
            [
                ModelMessage(
                    role="system",
                    content=(
                        "Verify whether the user's goal is actually complete using only the "
                        "observable evidence. Return strict JSON with outcome=complete|repair|"
                        "replan|wait_user|fail, feedback, evidence, and optional confidence."
                    ),
                ),
                ModelMessage(
                    role="user",
                    content=json.dumps(
                        {
                            "goal": state.goal,
                            "conversation": self._conversation_context(state, run),
                            "decision": decision.model_dump(mode="json"),
                            "observations": [
                                item.model_dump(mode="json")
                                for item in state.observations[-8:]
                            ],
                            "candidate_response": state.final_response_text,
                        },
                        ensure_ascii=False,
                    ),
                ),
            ],
            operation="verify",
        )
        self.runtime.model_budget_guard.assert_plan_allowed(
            self.runtime.store, run.tenant_id, run.id
        )
        result = self.runtime.model_gateway.verify_completion(request)
        state.verifier_result = result
        self._record_model_operation(run, "verify", request)
        self.runtime.store.append_run_event(
            run,
            "agent.verification.completed",
            result.model_dump(mode="json"),
        )
        return result

    def _route_verification(
        self,
        state: AgentRuntimeState,
        run: Run,
        cycle_id: str,
        result: AgentVerificationResult,
    ) -> AgentRuntimeState | None:
        if result.outcome == "complete":
            self.runtime.store.complete_agent_cycle(
                run.tenant_id, cycle_id, status="completed", verifier_result=result
            )
            return self._finalize(state, run)
        if result.outcome == "wait_user":
            return self._wait_for_user(state, run, cycle_id, result.feedback)
        if result.outcome == "fail":
            self.runtime.store.complete_agent_cycle(
                run.tenant_id, cycle_id, status="failed", verifier_result=result
            )
            return self._fail(state, run, "verification_failed", detail=result.feedback)
        self.runtime.store.complete_agent_cycle(
            run.tenant_id, cycle_id, status="completed", verifier_result=result
        )
        if result.outcome == "replan":
            state.active_plan_revision += 1
            state.replan_count += 1
            self.runtime.store.append_run_event(
                run,
                "agent.plan.revised",
                {
                    "plan_revision": state.active_plan_revision,
                    "feedback": result.feedback,
                },
            )
        else:
            state.repair_attempts += 1
            self.runtime.store.append_run_event(
                run,
                "agent.repair.started",
                {"repair_attempt": state.repair_attempts, "feedback": result.feedback},
            )
        self._persist_checkpoint(state, run, cycle_id=cycle_id)
        return None

    def _execute_durable_action(
        self,
        state: AgentRuntimeState,
        run: Run,
        action: AgentAction,
    ) -> AgentObservation | None:
        claimed = self.runtime.store.claim_agent_action(
            run.tenant_id,
            action.id,
            lease_owner_id=self.runtime.loop_worker_id,
            lease_seconds=self.runtime.loop_action_lease_seconds,
        )
        if claimed is None:
            current = self.runtime.store.get_agent_action(run.tenant_id, action.id)
            if current.status == "uncertain":
                self._wait_for_uncertain_resolution(state, run, current)
            return None
        step = self._decision_step(claimed)
        state.plan = [step]
        state.current_step_id = step.id
        self.runtime.store.append_run_event(
            run,
            "agent.action.started",
            {
                "action_id": claimed.id,
                "cycle_id": claimed.cycle_id,
                "tool_name": step.tool_name,
                "lease_generation": claimed.lease_generation,
            },
        )
        started = time.perf_counter()
        result = None
        failure_class = None
        safe_error = None
        connector_id = None
        try:
            prepared = self.runtime._prepare_step_for_execution(state, step)
            if prepared.tool_name.startswith("connector."):
                result = self._execute_connector_action(state, run, prepared)
            else:
                result = self.runtime.tool_gateway.execute_for_run(
                    state,
                    prepared,
                    granted_scopes=self.runtime._resolve_tool_granted_scopes(
                        state, prepared
                    ),
                )
            if prepared.tool_name == "browser.action":
                result = self.runtime._promote_browser_screenshot(state, result)
                self.runtime._record_browser_action_event(run, prepared, result)
            if prepared.tool_name == "sandbox.command":
                self.runtime._record_sandbox_command_event(run, prepared, result)
                exit_code = self.runtime._sandbox_command_failed_exit_code(result)
                if exit_code is not None:
                    failure_class = "command_failed"
                    safe_error = f"Command exited with status {exit_code}"
                else:
                    self.runtime._promote_sandbox_artifacts(state, prepared)
            if failure_class is None:
                self.runtime._record_tool_execution(state, prepared)
                state.tool_results.append(result)
        except ToolApprovalRequiredError as error:
            failure_class = "approval_required"
            safe_error = str(error)
        except ToolExecutionError as error:
            failure_class = "tool_execution_error"
            safe_error = str(error)
        except ConnectorCredentialExpiredError as error:
            failure_class = "connector_reconnect_required"
            connector_id = error.connector_id
            safe_error = "Connector authorization expired; reconnect to continue"
        except Exception as error:
            failure_class = self._classify_failure(error)
            safe_error = self._safe_error(error)

        observation = AgentObservation(
            action_id=claimed.id,
            success=failure_class is None,
            output=(
                result.output
                if result is not None
                else ({"connector_id": connector_id} if connector_id else {})
            ),
            error=safe_error,
            safe_error=safe_error,
            failure_class=failure_class,
        )
        state.observations = [
            item for item in state.observations if item.action_id != claimed.id
        ]
        state.observations.append(observation)
        if observation.success and step.id not in state.completed_step_ids:
            state.completed_step_ids.append(step.id)
        renewed = self.runtime.store.renew_agent_action_lease(
            run.tenant_id,
            claimed.id,
            lease_owner_id=self.runtime.loop_worker_id,
            lease_generation=claimed.lease_generation,
            lease_seconds=self.runtime.loop_action_lease_seconds,
        )
        if renewed is None:
            state.pending_uncertain_action_id = claimed.id
            state.waiting_reason = "action_lease_lost_before_commit"
            self.runtime._save_state(state)
            return None
        state.checkpoint_sequence += 1
        payload = state.model_dump(mode="json")
        checksum = self._checksum(payload)
        usage = {
            "elapsed_ms": max(0, round((time.perf_counter() - started) * 1000)),
            "tool_name": step.tool_name,
        }
        try:
            _, checkpoint = self.runtime.store.commit_agent_action_observation(
                run.tenant_id,
                claimed.id,
                observation,
                lease_owner_id=self.runtime.loop_worker_id,
                lease_generation=claimed.lease_generation,
                usage=usage,
                state_payload=payload,
                checksum=checksum,
                sandbox_checkpoint_ref=self._sandbox_checkpoint_ref(state),
            )
        except AgentActionLeaseConflictError:
            state.pending_uncertain_action_id = claimed.id
            state.waiting_reason = "action_commit_fence_rejected"
            self.runtime._save_state(state)
            return None
        state.checkpoint_sequence = checkpoint.sequence
        self.runtime._save_state(state)
        self.runtime.store.append_run_event(
            run,
            "agent.observation.recorded",
            {
                "action_id": claimed.id,
                "checkpoint_sequence": checkpoint.sequence,
                "success": observation.success,
                "failure_class": observation.failure_class,
                "output": self.runtime._redact_tool_input(observation.output),
                "safe_error": observation.safe_error,
            },
        )
        return observation

    def _execute_connector_action(
        self,
        state: AgentRuntimeState,
        run: Run,
        step: PlanStep,
    ) -> ToolResult:
        registry = self.runtime.connector_registry
        dispatcher = self.runtime.connector_dispatcher
        invocation_service = self.runtime.connector_invocation_service
        if registry is None or dispatcher is None or invocation_service is None:
            raise ToolExecutionError("Connector runtime is not configured")
        connector_id, capability_name = self._parse_connector_tool(step.tool_name)
        connector = registry.get_connector(run.tenant_id, connector_id)
        if connector.workspace_id != run.workspace_id:
            raise ToolExecutionError("Connector is not available in this workspace")
        granted_scopes = self._resolve_connector_granted_scopes(
            state,
            step,
            connector_id,
            capability_name,
        )
        decision = invocation_service.evaluate(
            connector,
            ConnectorInvocationRequest(
                tenant_id=run.tenant_id,
                workspace_id=run.workspace_id,
                user_id=run.user_id,
                run_id=run.id,
                step_id=step.id,
                connector_id=connector_id,
                capability_name=capability_name,
                tool_input=step.tool_input,
                granted_scopes=granted_scopes,
                approved=step.id in state.approved_step_ids,
            ),
        )
        if decision.status == ConnectorInvocationStatus.APPROVAL_REQUIRED:
            raise ToolApprovalRequiredError(
                f"Connector approval required: {step.tool_name}"
            )
        if decision.status == ConnectorInvocationStatus.DENIED:
            raise ToolExecutionError(
                decision.reason or f"Connector invocation denied: {step.tool_name}"
            )
        dispatch_result = dispatcher.dispatch(
            connector=connector,
            tool_input=step.tool_input,
            tool_name=decision.tool_name,
        )
        if dispatch_result is None:
            raise ToolExecutionError(
                f"Connector type does not support direct invocation: {connector.type.value}"
            )
        self.runtime.store.record_billing_meter(
            tenant_id=run.tenant_id,
            run_id=run.id,
            meter_type=decision.billing_meter_type or CONNECTOR_INVOCATION_METER,
            quantity=1,
            unit="invocation",
            metadata={
                "connector_id": connector_id,
                "capability_name": capability_name,
                "tool_name": decision.tool_name,
                "risk_level": decision.risk_level,
            },
        )
        self.runtime.store.append_run_event(
            run,
            "connector.invoked",
            {
                "action_id": step.id,
                "connector_id": connector_id,
                "capability_name": capability_name,
                "tool_name": decision.tool_name,
                "status_code": dispatch_result.status_code,
                "response_size_bytes": dispatch_result.response_size_bytes,
            },
        )
        return ToolResult(tool_name=decision.tool_name, output=dispatch_result.output)

    def _resolve_connector_granted_scopes(
        self,
        state: AgentRuntimeState,
        step: PlanStep,
        connector_id: str,
        capability_name: str,
    ) -> list[str]:
        connector = self.runtime.connector_registry.get_connector(
            state.tenant_id, connector_id
        )
        capability = next(
            (
                item
                for item in connector.capabilities
                if item.name == capability_name and item.enabled
            ),
            None,
        )
        if capability is None:
            return []
        if self.runtime.policy_service is None:
            return list(capability.required_scopes)
        granted: list[str] = []
        from taroai.policy import PolicyRequest

        for scope in capability.required_scopes:
            decision = self.runtime.policy_service.decide(
                PolicyRequest(
                    tenant_id=state.tenant_id,
                    workspace_id=state.workspace_id,
                    user_id=state.user_id,
                    run_id=state.run_id,
                    action=scope,
                    resource=f"connector:{connector_id}",
                    risk_level=capability.risk_level,
                    context={
                        "tool_name": step.tool_name,
                        "connector_id": connector_id,
                        "capability_name": capability_name,
                        "step_id": step.id,
                    },
                )
            )
            if decision.allowed:
                granted.append(scope)
        return granted

    def _parse_connector_tool(self, tool_name: str) -> tuple[str, str]:
        parts = tool_name.split(".", 2)
        if len(parts) != 3 or parts[0] != "connector" or not all(parts[1:]):
            raise ToolExecutionError(f"Invalid connector tool name: {tool_name}")
        return parts[1], parts[2]

    def _pause_for_connector_reconnect(
        self,
        state: AgentRuntimeState,
        run: Run,
        cycle_id: str,
        action: AgentAction,
        observation: AgentObservation,
    ) -> AgentRuntimeState:
        connector_id = str(observation.output.get("connector_id") or "")
        if connector_id and self.runtime.connector_registry is not None:
            self.runtime.connector_registry.update_connector_status(
                run.tenant_id,
                connector_id,
                ConnectorStatus.NEEDS_REAUTH,
            )
        self.runtime.store.pause_connector_action_for_reconnect(
            run.tenant_id,
            action.id,
            connector_id=connector_id,
        )
        self.runtime.store.complete_agent_cycle(
            run.tenant_id,
            cycle_id,
            status="waiting",
            verifier_result=AgentVerificationResult(
                outcome="wait_user",
                feedback="Reconnect the Connector to retry this action once",
            ),
        )
        state.status = RunStatus.AWAITING_APPROVAL
        state.pending_uncertain_action_id = action.id
        state.waiting_reason = f"connector_reconnect_required:{connector_id}"
        self.runtime._save_state(state)
        return state

    def _wait_for_uncertain_resolution(
        self,
        state: AgentRuntimeState,
        run: Run,
        action: AgentAction,
    ) -> AgentRuntimeState:
        state.pending_uncertain_action_id = action.id
        state.waiting_reason = "uncertain_side_effect_requires_human_resolution"
        state.status = RunStatus.WAITING_FOR_USER
        self.runtime.store.update_run_status(
            run.tenant_id,
            run.id,
            RunStatus.WAITING_FOR_USER,
            emit_status_event=False,
        )
        self.runtime._save_state(state)
        self.runtime.store.append_run_event(
            run,
            "agent.action.resolution_required",
            {
                "action_id": action.id,
                "action_key": action.action_key,
                "reason": state.waiting_reason,
            },
        )
        return state

    def _wait_for_user(
        self,
        state: AgentRuntimeState,
        run: Run,
        cycle_id: str,
        reason: str,
    ) -> AgentRuntimeState:
        state.status = RunStatus.WAITING_FOR_USER
        state.waiting_reason = reason or "user_input_required"
        self.runtime.store.update_run_status(
            run.tenant_id,
            run.id,
            RunStatus.WAITING_FOR_USER,
            emit_status_event=False,
        )
        self.runtime.store.complete_agent_cycle(
            run.tenant_id,
            cycle_id,
            status="waiting",
            verifier_result=AgentVerificationResult(
                outcome="wait_user",
                feedback=state.waiting_reason,
            ),
        )
        self._persist_checkpoint(state, run, cycle_id=cycle_id)
        self.runtime.store.append_run_event(
            run,
            "agent.waiting_for_user",
            {"reason": state.waiting_reason, "cycle_id": cycle_id},
        )
        return state

    def _finalize(self, state: AgentRuntimeState, run: Run) -> AgentRuntimeState:
        response_text = state.final_response_text or self._stream_final_response(state, run)
        state.final_response_text = response_text
        finalized = self.runtime._finalize_success(state)
        if finalized.status != RunStatus.SUCCEEDED:
            return finalized
        if run.thread_id is not None and response_text:
            message = self.runtime.store.append_chat_message(
                run.tenant_id,
                run.thread_id,
                None,
                ChatMessageCreate(
                    role=ChatMessageRole.ASSISTANT,
                    content=response_text,
                    dispatch_status=ChatMessageDispatchStatus.COMPLETED,
                    delivery_status=ChatMessageDeliveryStatus.DELIVERED,
                ),
            )
            self.runtime.store.append_run_event(
                run,
                "assistant.message.completed",
                {"message_id": message.id, "content": response_text},
            )
        self._complete_trigger_message(run, succeeded=True)
        self._emit_terminal_once(
            finalized,
            run,
            "agent.loop.completed",
            {"outcome": "complete", "iterations": finalized.iteration},
        )
        self.runtime._save_state(finalized)
        return finalized

    def _stream_final_response(self, state: AgentRuntimeState, run: Run) -> str:
        request = self._model_request(
            run,
            [
                ModelMessage(
                    role="system",
                    content=(
                        "Write the concise final answer for the user from the verified "
                        "observations. Do not expose hidden reasoning."
                    ),
                ),
                ModelMessage(
                    role="user",
                    content=json.dumps(
                        {
                            "goal": state.goal,
                            "conversation": self._conversation_context(state, run),
                            "observations": [
                                item.model_dump(mode="json") for item in state.observations
                            ],
                            "verification": (
                                state.verifier_result.model_dump(mode="json")
                                if state.verifier_result
                                else None
                            ),
                        },
                        ensure_ascii=False,
                    ),
                ),
            ],
            operation="respond",
        )
        chunks: list[str] = []
        self._record_model_operation(run, "respond", request)
        try:
            for delta in self.runtime.model_gateway.stream_response(request):
                chunks.append(delta)
                self.runtime.store.append_run_event(
                    run,
                    "assistant.delta",
                    {"delta": delta},
                )
        except ModelGatewayError:
            return "任务已完成。"
        return "".join(chunks).strip() or "任务已完成。"

    def _fail(
        self,
        state: AgentRuntimeState,
        run: Run,
        reason: str,
        *,
        detail: str | None = None,
        timed_out: bool = False,
    ) -> AgentRuntimeState:
        status = RunStatus.TIMED_OUT if timed_out else RunStatus.FAILED
        state.status = status
        state.failure_reason = detail or reason
        self.runtime.store.update_run_status(
            run.tenant_id, run.id, status, emit_status_event=False
        )
        self._persist_checkpoint(state, run)
        self.runtime.store.append_run_event(
            run,
            "run.failed" if not timed_out else "run.timed_out",
            {"reason": reason, "detail": detail},
        )
        self._complete_trigger_message(run, succeeded=False)
        self._emit_terminal_once(
            state,
            run,
            "agent.loop.completed",
            {"outcome": "failed", "reason": reason},
        )
        self.runtime._destroy_runtime_sandbox_session(state, reason="failure", force=True)
        self.runtime._destroy_runtime_browser_session(state, reason="failure")
        self.runtime._save_state(state)
        return state

    def _persist_checkpoint(
        self,
        state: AgentRuntimeState,
        run: Run,
        *,
        cycle_id: str | None = None,
    ) -> AgentCheckpoint:
        latest = self.runtime.store.get_latest_agent_checkpoint(run.tenant_id, run.id)
        sequence = (latest.sequence if latest is not None else 0) + 1
        state.checkpoint_sequence = sequence
        payload = state.model_dump(mode="json")
        checkpoint = AgentCheckpoint(
            id=new_id("checkpoint"),
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            thread_id=run.thread_id,
            run_id=run.id,
            cycle_id=cycle_id,
            sequence=sequence,
            state_payload=payload,
            sandbox_checkpoint_ref=self._sandbox_checkpoint_ref(state),
            checksum=self._checksum(payload),
        )
        created = self.runtime.store.create_agent_checkpoint(checkpoint)
        self.runtime._save_state(state)
        return created

    def _consume_steering_at_checkpoint(
        self, state: AgentRuntimeState, run: Run
    ) -> None:
        if run.thread_id is None or state.checkpoint_sequence < 1:
            return
        messages = self.runtime.store.list_pending_steering_messages(
            run.tenant_id, run.thread_id
        )
        for message in messages:
            state.steering_messages.append(message.content)
            self.runtime.store.mark_steering_applied(run.tenant_id, message.id)
            self.runtime.store.append_run_event(
                run,
                "agent.steering.applied",
                {"message_id": message.id, "content": message.content},
            )
        if messages:
            self.runtime._save_state(state)

    def _decision_step(self, action: AgentAction) -> PlanStep:
        decision = action.decision
        return PlanStep(
            id=action.id,
            title=decision.expected_outcome or decision.rationale_summary or decision.tool_name or "Agent action",
            tool_name=decision.tool_name or "",
            skill_id=decision.skill_id,
            tool_input=decision.tool_input,
            approval_required=decision.approval_required,
        )

    def _requires_approval(
        self,
        state: AgentRuntimeState,
        run: Run,
        decision: AgentDecision,
        step: PlanStep,
    ) -> bool:
        approved_tool_names = set(
            state.runtime_metadata.get("approved_tool_names", [])
        )
        if step.tool_name in approved_tool_names:
            if step.id not in state.approved_step_ids:
                state.approved_step_ids.append(step.id)
            return False
        if step.id in self._approved_steps(run):
            return False
        policy = self.runtime.tool_gateway.policies.get(step.tool_name)
        connector_approval_required = False
        if step.tool_name.startswith("connector.") and self.runtime.connector_registry:
            try:
                connector_id, capability_name = self._parse_connector_tool(
                    step.tool_name
                )
                connector = self.runtime.connector_registry.get_connector(
                    run.tenant_id, connector_id
                )
                connector_approval_required = any(
                    capability.name == capability_name
                    and capability.enabled
                    and capability.approval_required
                    for capability in connector.capabilities
                )
            except Exception:
                connector_approval_required = False
        if (
            decision.approval_required
            or (policy is not None and policy.approval_required)
            or connector_approval_required
        ):
            return True
        if run.mode != RunMode.AUTONOMOUS or not self.runtime.full_auto_requires_isolation:
            return False
        adapter = self.runtime.sandbox_adapter
        if adapter is None or getattr(adapter, "provider", "disabled") in {
            "disabled",
            "local_process",
        }:
            self.runtime.store.append_run_event(
                run,
                "agent.full_auto.downgraded",
                {"reason": "isolated_runtime_unavailable", "tool_name": step.tool_name},
            )
            return True
        try:
            capabilities = adapter.get_capabilities()
        except Exception:
            return True
        return not capabilities.runtime_isolation

    def _approved_steps(self, run: Run) -> set[str]:
        try:
            state = self.runtime._load_state(run.tenant_id, run.id)
        except NotFoundError:
            return set()
        return set(state.approved_step_ids)

    def _model_request(
        self,
        run: Run,
        messages: list[ModelMessage],
        *,
        operation: str,
    ) -> ModelGatewayRequest:
        request = ModelGatewayRequest(
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
            user_id=run.user_id,
            run_id=run.id,
            provider_id=run.provider_id,
            model=run.model_id,
            reasoning_effort=run.reasoning_effort,
            messages=messages,
            tools=self._tool_definitions(),
            tool_choice="auto",
            metadata={
                "operation": operation,
                "agent_id": run.agent_id,
                "thread_id": run.thread_id,
            },
        )
        resolved_model = self.runtime.model_policy.assert_request_allowed(request)
        if resolved_model is not None and request.model != resolved_model:
            request = request.model_copy(update={"model": resolved_model})
        return request

    def _tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": name.replace(".", "__"),
                    "description": f"Execute Taroai tool {name}",
                    "parameters": policy.input_schema,
                },
            }
            for name, policy in sorted(self.runtime.tool_gateway.policies.items())
            if policy.enabled
        ]

    def _record_model_operation(
        self,
        run: Run,
        operation: str,
        request: ModelGatewayRequest,
    ) -> None:
        self.runtime.store.record_billing_meter(
            tenant_id=run.tenant_id,
            run_id=run.id,
            meter_type="model_call_count",
            quantity=1,
            unit="call",
            provider=request.provider_id,
            model=request.model,
            metadata={"operation": operation, "reasoning_effort": request.reasoning_effort},
        )

    def _budget_failure(self, state: AgentRuntimeState) -> str | None:
        if state.iteration >= state.max_iterations:
            return "iteration_budget_exhausted"
        if state.repair_attempts > state.max_repairs:
            return "repair_budget_exhausted"
        if state.deadline_at is not None and utc_now() >= state.deadline_at:
            return "elapsed_budget_exhausted"
        state.cost_consumed = self._run_cost(state)
        if state.cost_limit > 0 and state.cost_consumed >= state.cost_limit:
            return "cost_budget_exhausted"
        return None

    def _run_cost(self, state: AgentRuntimeState) -> float:
        try:
            meters = self.runtime.store.list_billing_meters(state.tenant_id)
        except Exception:
            return state.cost_consumed
        return float(
            sum(
                meter.cost_estimate or 0
                for meter in meters
                if meter.run_id == state.run_id
            )
        )

    def _budget_snapshot(self, state: AgentRuntimeState) -> dict[str, Any]:
        return {
            "iteration": state.iteration,
            "max_iterations": state.max_iterations,
            "repair_attempts": state.repair_attempts,
            "max_repairs": state.max_repairs,
            "cost_consumed": self._run_cost(state),
            "cost_limit": state.cost_limit,
            "deadline_at": state.deadline_at.isoformat() if state.deadline_at else None,
            "used_skills": list(state.runtime_metadata.get("used_skills", [])),
        }

    def _sandbox_checkpoint_ref(self, state: AgentRuntimeState) -> str | None:
        if self.runtime.sandbox_adapter is None or state.sandbox_session_id is None:
            return None
        try:
            return self.runtime.sandbox_adapter.snapshot(
                state.tenant_id, state.sandbox_session_id
            ).uri
        except Exception:
            return None

    def _complete_trigger_message(self, run: Run, *, succeeded: bool) -> None:
        if run.trigger_message_id is None:
            return
        try:
            self.runtime.store.update_chat_message(
                run.tenant_id,
                run.trigger_message_id,
                dispatch_status=(
                    ChatMessageDispatchStatus.COMPLETED
                    if succeeded
                    else ChatMessageDispatchStatus.FAILED
                ),
                delivery_status=(
                    ChatMessageDeliveryStatus.DELIVERED
                    if succeeded
                    else ChatMessageDeliveryStatus.FAILED
                ),
            )
        except NotFoundError:
            return

    def _emit_terminal_once(
        self,
        state: AgentRuntimeState,
        run: Run,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        if state.terminal_event_emitted:
            return
        state.terminal_event_emitted = True
        self.runtime.store.append_run_event(run, event_type, payload)

    def _classify_failure(self, error: Exception) -> str:
        if isinstance(error, ConnectorCredentialExpiredError):
            return "connector_reconnect_required"
        name = error.__class__.__name__.lower()
        if any(token in name for token in ("timeout", "connection", "transport", "unavailable")):
            return "transient_transport_error"
        if "policy" in name or "guardrail" in name:
            return "policy_blocked"
        return "tool_execution_error"

    def _safe_error(self, error: Exception) -> str:
        text = str(error).strip()
        return text[:500] if text else error.__class__.__name__

    def _checksum(self, payload: dict[str, Any]) -> str:
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def _is_cancelled(self, run: Run) -> bool:
        current = self.runtime.store.get_run(run.tenant_id, run.id)
        return current.status == RunStatus.CANCELLED

    def _action_iteration(self, action: AgentAction) -> int:
        key = action.action_key.split(":")
        if "cycle" in key:
            index = key.index("cycle")
            if len(key) > index + 1 and key[index + 1].isdigit():
                return int(key[index + 1])
        return 0
