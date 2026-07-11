from taroai.agent.graph import build_runtime_graph
from taroai.agent.loop import AgentLoopV2
from taroai.agent.planning import PlanStep
from taroai.agent.runtime import AgentRuntime
from taroai.agent.state import AgentRuntimeState
from taroai.agent.tools import ToolExecutionError, ToolGateway, ToolResult

__all__ = [
    "AgentRuntime",
    "AgentLoopV2",
    "AgentRuntimeState",
    "PlanStep",
    "ToolExecutionError",
    "ToolGateway",
    "ToolResult",
    "build_runtime_graph",
]


def apply_agent_runtime_settings(runtime: AgentRuntime, settings) -> AgentRuntime:
    """Apply the one runtime-mode contract shared by API and background workers."""
    runtime.runtime_mode = settings.agent_runtime_mode
    runtime.loop_max_iterations = settings.agent_loop_max_iterations
    runtime.loop_max_repairs = settings.agent_loop_max_repairs
    runtime.loop_timeout_seconds = settings.agent_loop_timeout_seconds
    runtime.loop_cost_limit = settings.agent_loop_cost_limit
    runtime.loop_action_lease_seconds = settings.agent_loop_action_lease_seconds
    runtime.full_auto_requires_isolation = (
        settings.agent_loop_full_auto_requires_isolation
    )
    return runtime


__all__.append("apply_agent_runtime_settings")
