from taroai.agent.graph import build_runtime_graph  # Agent 的执行流程图。
from taroai.agent.loop import AgentLoopV2  # Agent 执行任务时一轮轮往前跑的主循环。
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
    "AgentLoopV2",
    "AgentRuntimeState",
    "PlanStep",
    "ToolExecutionError",
    "ToolGateway",
    "ToolResult",
    "build_runtime_graph",
]


def apply_agent_runtime_settings(runtime: AgentRuntime, settings) -> AgentRuntime:

    # 先决定这次任务按哪种方式跑。
    runtime.runtime_mode = settings.agent_runtime_mode

    # loop循环限制
    runtime.loop_max_iterations = settings.agent_loop_max_iterations
    runtime.loop_max_repairs = settings.agent_loop_max_repairs
    runtime.loop_timeout_seconds = settings.agent_loop_timeout_seconds
    runtime.loop_cost_limit = settings.agent_loop_cost_limit

    # 单个动作最多占多久；全自动模式下是否必须在隔离环境里跑。
    runtime.loop_action_lease_seconds = settings.agent_loop_action_lease_seconds
    runtime.full_auto_requires_isolation = (
        settings.agent_loop_full_auto_requires_isolation
    )
    return runtime


__all__.append("apply_agent_runtime_settings")
