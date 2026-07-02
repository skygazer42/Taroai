from langgraph.graph import END, START, StateGraph


def build_runtime_graph():
    graph = StateGraph(dict)
    graph.add_node("classify_intent_and_risk", lambda state: state)
    graph.add_node("load_context", lambda state: state)
    graph.add_node("create_plan", lambda state: state)
    graph.add_node("policy_check", lambda state: state)
    graph.add_node("execute_steps", lambda state: state)
    graph.add_node("finalize_artifacts", lambda state: state)
    graph.add_edge(START, "classify_intent_and_risk")
    graph.add_edge("classify_intent_and_risk", "load_context")
    graph.add_edge("load_context", "create_plan")
    graph.add_edge("create_plan", "policy_check")
    graph.add_edge("policy_check", "execute_steps")
    graph.add_edge("execute_steps", "finalize_artifacts")
    graph.add_edge("finalize_artifacts", END)
    return graph

