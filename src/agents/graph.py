from langgraph.graph import END, StateGraph

from src.agents.nodes.example_node import analyze_node, respond_node
from src.agents.state import AgentState


def should_continue(state: AgentState) -> str:
    """Route based on whether an error occurred during analysis."""
    if state.get("error"):
        return END
    return "respond"


def build_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    # Add nodes
    graph.add_node("analyze", analyze_node)
    graph.add_node("respond", respond_node)

    # Add edges
    graph.set_entry_point("analyze")
    graph.add_conditional_edges("analyze", should_continue)
    graph.add_edge("respond", END)

    return graph.compile()


agent = build_graph()


# ---------------------------------------------------------------------------
# Run 1: Proposal Graph (profiler → digest → rule_proposer → persist_rules)
# ---------------------------------------------------------------------------

def build_proposal_graph() -> StateGraph:
    """Xây dựng graph cho Run 1 — kết thúc sau khi persist rules vào DB.

    Conditional edge: nếu state có 'error' → END ngay (pattern giống build_graph).
    """
    from src.agents.nodes.profiler_node import profiler_digest_node, raw_profiler_node
    from src.agents.nodes.rule_proposer_node import persist_rules_node, rule_proposer_node

    def _should_continue_proposal(state: AgentState) -> str:
        if state.get("error"):
            return END
        return "next"

    graph = StateGraph(AgentState)

    graph.add_node("raw_profiler", raw_profiler_node)
    graph.add_node("profiler_digest", profiler_digest_node)
    graph.add_node("rule_proposer", rule_proposer_node)
    graph.add_node("persist_rules", persist_rules_node)

    graph.set_entry_point("raw_profiler")

    # raw_profiler → profiler_digest (hoặc END nếu lỗi)
    graph.add_conditional_edges(
        "raw_profiler",
        _should_continue_proposal,
        {"next": "profiler_digest", END: END},
    )

    # profiler_digest → rule_proposer (hoặc END nếu lỗi)
    graph.add_conditional_edges(
        "profiler_digest",
        _should_continue_proposal,
        {"next": "rule_proposer", END: END},
    )

    graph.add_edge("rule_proposer", "persist_rules")
    graph.add_edge("persist_rules", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# Run 2: Execution Graph — stub (Test Generator milestone)
# ---------------------------------------------------------------------------

def build_execution_graph() -> StateGraph:
    """Run 2: load approved rules → test_generator → test_runner → anomaly → END.

    TODO: Implement khi milestone Test Generator sẵn sàng.
    """
    raise NotImplementedError(
        "build_execution_graph chưa được implement — xem docs/plan/rule_proposer_agent.md"
    )

