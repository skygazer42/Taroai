from typing import TYPE_CHECKING

from langgraph.graph import END, START, StateGraph

from taroai.agent.nodes import AgentGraphNodes
from taroai.agent.state import AgentGraphRoute, AgentRuntimeState

if TYPE_CHECKING:
    from taroai.agent.runtime import AgentRuntime


def _route(state: AgentRuntimeState) -> AgentGraphRoute:
    return state.graph_route


def build_runtime_graph(runtime: "AgentRuntime") -> StateGraph:
    """构建以 Pydantic 状态为唯一数据契约的 Agent 执行图。"""

    nodes = AgentGraphNodes(runtime)
    graph = StateGraph(AgentRuntimeState)

    # 节点只负责当前阶段，流转关系集中在本文件。
    graph.add_node("observe", nodes.observe)
    graph.add_node("decide", nodes.decide)
    graph.add_node("policy", nodes.policy)
    graph.add_node("act", nodes.act)
    graph.add_node("observe_result", nodes.observe_result)
    graph.add_node("verify", nodes.verify)
    graph.add_node("repair", nodes.repair)
    graph.add_node("replan", nodes.replan)
    graph.add_node("complete", nodes.complete)
    graph.add_node("wait_user", nodes.wait_user)
    graph.add_node("fail", nodes.fail)

    graph.add_edge(START, "observe")
    graph.add_conditional_edges(
        "observe",
        _route,
        {
            "decide": "decide",
            "policy": "policy",
            "fail": "fail",
            "end": END,
        },
    )
    graph.add_conditional_edges(
        "decide",
        _route,
        {
            "policy": "policy",
            "verify": "verify",
            "complete": "complete",
            "replan": "replan",
            "wait_user": "wait_user",
            "fail": "fail",
            "end": END,
        },
    )
    graph.add_conditional_edges(
        "policy",
        _route,
        {"act": "act", "fail": "fail", "end": END},
    )
    graph.add_conditional_edges(
        "act",
        _route,
        {"observe_result": "observe_result", "fail": "fail", "end": END},
    )
    graph.add_conditional_edges(
        "observe_result",
        _route,
        {
            "decide": "decide",
            "complete": "complete",
            "repair": "repair",
            "fail": "fail",
            "end": END,
        },
    )
    graph.add_conditional_edges(
        "verify",
        _route,
        {
            "complete": "complete",
            "repair": "repair",
            "replan": "replan",
            "wait_user": "wait_user",
            "fail": "fail",
        },
    )
    graph.add_conditional_edges(
        "repair",
        _route,
        {"decide": "decide", "fail": "fail"},
    )
    graph.add_edge("replan", "decide")

    # 终止节点结束本次调用；恢复时从持久化状态重新进入图。
    graph.add_edge("complete", END)
    graph.add_edge("wait_user", END)
    graph.add_edge("fail", END)
    return graph
