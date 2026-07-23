from pydantic import Field

from taroai.agent import PlanStep, ToolExecutionError, ToolGateway, ToolResult
from taroai.model_gateway import (
    ModelGateway,
    ModelGatewayRequest,
    ModelGatewayResponse,
    ModelUsage,
    PlannedToolCall,
)


class DeterministicModelGateway(ModelGateway):
    plan: list[PlannedToolCall] = Field(default_factory=list)
    usage: ModelUsage | None = None
    model_name: str = "deterministic-test"
    call_count: int = 0

    def create_plan(self, request: ModelGatewayRequest) -> ModelGatewayResponse:
        self.call_count += 1
        return ModelGatewayResponse(
            id=f"response_{request.run_id}",
            model=self.model_name,
            planned_steps=self.plan,
            usage=self.usage,
        )


class DeterministicToolGateway(ToolGateway):
    fail_once_for: list[str] = Field(default_factory=list)
    call_counts: dict[str, int] = Field(default_factory=dict)

    def execute(self, step: PlanStep) -> ToolResult:
        call_count = self.call_counts.get(step.tool_name, 0) + 1
        self.call_counts[step.tool_name] = call_count
        if step.tool_name in self.fail_once_for and call_count == 1:
            raise ToolExecutionError(f"Transient failure for {step.tool_name}")
        return ToolResult(
            tool_name=step.tool_name,
            output={
                "step_id": step.id,
                "title": step.title,
                "ok": True,
            },
        )

    def execute_for_run(
        self,
        state,
        step: PlanStep,
        granted_scopes: list[str] | None = None,
        thread_id: str | None = None,
    ) -> ToolResult:
        return self.execute(step)
