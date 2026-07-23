from taroai.agent.graph import build_runtime_graph  # Agent 的执行流程图。
from taroai.agent.planning import PlanStep  # 计划里的单个待办事项。
from taroai.agent.runtime import AgentRuntime  # 真正负责跑任务的运行时。
from taroai.agent.state import AgentRuntimeState  # 记录一次任务当前跑到了哪一步。
from taroai.agent.tools import (
    ToolExecutionError,
    ToolGateway,
    ToolResult,
)  # 工具调用的入口、结果和异常。

__all__ = [
    "AgentRuntime",
    "AgentRuntimeState",
    "PlanStep",
    "ToolExecutionError",
    "ToolGateway",
    "ToolResult",
    "build_runtime_graph",
]


def apply_agent_runtime_settings(runtime: AgentRuntime, settings) -> AgentRuntime:
    # Agent 循环的次数、修复次数、时长和费用上限。
    runtime.loop_max_iterations = settings.agent_loop_max_iterations
    runtime.loop_max_repairs = settings.agent_loop_max_repairs
    runtime.loop_timeout_seconds = settings.agent_loop_timeout_seconds
    runtime.loop_cost_limit = settings.agent_loop_cost_limit
    runtime.sandbox_artifact_max_bytes = settings.upload_max_bytes

    # 动作租约与全自动隔离要求。
    runtime.loop_action_lease_seconds = settings.agent_loop_action_lease_seconds
    runtime.full_auto_requires_isolation = (
        settings.agent_loop_full_auto_requires_isolation
    )
    return runtime


__all__.append("apply_agent_runtime_settings")
