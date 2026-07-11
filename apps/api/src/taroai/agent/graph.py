from collections.abc import Callable, Mapping
from typing import Any

from langgraph.graph import END, START, StateGraph


def _phase(name: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def advance(state: dict[str, Any]) -> dict[str, Any]:
        return {**state, "phase": name}

    return advance


def build_runtime_graph(
    handlers: Mapping[str, Callable[[dict[str, Any]], dict[str, Any]]] | None = None,
):
    resolved = dict(handlers or {})
    graph = StateGraph(dict)
    for node in (
        "observe",
        "decide",
        "policy",
        "act",
        "observe_result",
        "verify",
        "repair",
        "replan",
        "complete",
        "wait_user",
        "fail",
    ):
        graph.add_node(node, resolved.get(node, _phase(node)))
    graph.add_edge(START, "observe")
    graph.add_edge("observe", "decide")
    graph.add_edge("decide", "policy")
    graph.add_edge("policy", "act")
    graph.add_edge("act", "observe_result")
    graph.add_edge("observe_result", "verify")
    graph.add_conditional_edges(
        "verify",
        lambda state: state.get("verification_outcome", "fail"),
        {
            "complete": "complete",
            "repair": "repair",
            "replan": "replan",
            "wait_user": "wait_user",
            "fail": "fail",
        },
    )
    graph.add_edge("repair", "decide")
    graph.add_edge("replan", "observe")
    graph.add_edge("complete", END)
    graph.add_edge("wait_user", END)
    graph.add_edge("fail", END)
    return graph

