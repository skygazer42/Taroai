import time
from decimal import Decimal
from typing import Any

from taroai.agents import AgentRunRequest
from taroai.domain import RunStatus
from taroai.evaluation.models import EvaluationObservation, EvaluationTargetKind
from taroai.evaluation.service import EvaluationExecutionRequest


class AgentEvaluationExecutor:
    version = "taroai-agent-evaluator-v1"

    def __init__(self, *, agent_service: Any, runtime: Any, store: Any):
        self.agent_service = agent_service
        self.runtime = runtime
        self.store = store

    def execute(self, request: EvaluationExecutionRequest) -> EvaluationObservation:
        if request.target_kind != EvaluationTargetKind.AGENT:
            return EvaluationObservation(error_type="unsupported_target_kind")
        started = time.monotonic()
        try:
            definition = self.agent_service.registry.get(
                request.tenant_id, request.target_id
            )
            invocation = self.agent_service.run(
                request.tenant_id,
                definition.created_by_user_id,
                request.target_id,
                AgentRunRequest(
                    input=request.case.input,
                    version=int(request.target_version),
                    mode="autonomous",
                ),
            )
            deadline = started + request.case.budget.max_duration_seconds
            state = self.runtime.execute_run(request.tenant_id, invocation.run_id)
            while state.status == RunStatus.RUNNING and time.monotonic() < deadline:
                time.sleep(0.25)
                state = self.runtime.execute_run(request.tenant_id, invocation.run_id)
            output = self._output(request.tenant_id, invocation.run_id, state)
            meters = [
                item
                for item in self.store.list_billing_meters(request.tenant_id)
                if item.run_id == invocation.run_id
            ]
            tokens = int(sum(float(item.quantity) for item in meters if "token" in item.meter_type))
            cost = Decimal(str(sum(float(item.metadata.get("cost", 0)) for item in meters)))
            side_effects = tuple(
                sorted(
                    {
                        str(event.type)
                        for event in self.store.list_run_events(
                            request.tenant_id, invocation.run_id
                        )
                        if any(word in str(event.type) for word in ("write", "commit", "delivery", "connector"))
                    }
                )
            )
            terminal = state.status in {RunStatus.SUCCEEDED}
            error_type = None if terminal else (
                "evaluation_timeout" if time.monotonic() >= deadline else state.failure_reason or state.status.value
            )
            return EvaluationObservation(
                output=output,
                tokens=tokens,
                cost=cost,
                duration_seconds=time.monotonic() - started,
                tool_calls=len(state.observations),
                tool_errors=sum(1 for item in state.observations if not item.success),
                human_interventions=1 if state.status == RunStatus.AWAITING_APPROVAL else 0,
                side_effects=side_effects,
                external_writes=sum(1 for item in side_effects if "connector" in item or "delivery" in item),
                error_type=error_type,
            )
        except Exception as error:
            return EvaluationObservation(
                duration_seconds=time.monotonic() - started,
                error_type=error.__class__.__name__,
                output={"safe_error": str(error)[:1000]},
            )

    def _output(self, tenant_id: str, run_id: str, state: Any):
        if state.final_response_text:
            return state.final_response_text
        if state.observations:
            return state.observations[-1].output
        artifacts = self.store.list_artifacts(tenant_id, run_id)
        if artifacts:
            artifact = artifacts[-1]
            return artifact.preview_payload or artifact.dashboard_payload or {
                "artifact_id": artifact.id,
                "name": artifact.name,
            }
        return None
