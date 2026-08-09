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
    """Xây dựng graph cho Run 1 — kết thúc sau khi persist rules vào DB và ghi trace.

    Luồng: raw_profiler → profiler_digest → rule_proposer → hitl_gate → END
    Conditional edge: nếu state có 'error' → END ngay (pattern giống build_graph).
    """
    from src.agents.nodes.hitl_gate_node import hitl_gate_node
    from src.agents.nodes.profiler_node import profiler_digest_node, raw_profiler_node
    from src.agents.nodes.rule_proposer_node import rule_proposer_node

    def _should_continue_proposal(state: AgentState) -> str:
        if state.get("error"):
            return END
        return "next"

    graph = StateGraph(AgentState)

    graph.add_node("raw_profiler", raw_profiler_node)
    graph.add_node("profiler_digest", profiler_digest_node)
    graph.add_node("rule_proposer", rule_proposer_node)
    graph.add_node("hitl_gate", hitl_gate_node)

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

    graph.add_edge("rule_proposer", "hitl_gate")
    graph.add_edge("hitl_gate", END)

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


async def main():
    """Hàm chạy test độc lập cho Run 1 (Proposal Graph).

    Quy trình:
      1. Khởi tạo DB (nếu chưa có).
      2. Tạo bản ghi Proposal Run mới (status=QUEUED -> RUNNING).
      3. Chạy pipeline: raw_profiler -> profiler_digest -> rule_proposer -> hitl_gate -> END.
      4. Cập nhật status run thành DONE (hoặc FAILED nếu lỗi).
      5. In tóm tắt kết quả (số rules đề xuất, phân loại dimension, trace file, v.v.).
    """
    import logging
    import uuid
    from src.config import get_settings
    from src.services.rule_store import create_run, init_db, update_run_status, list_rules, get_review_summary

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger("proposal_graph_runner")

    logger.info("Khởi tạo database...")
    init_db()

    settings = get_settings()
    dataset_id = "yellow_tripdata"
    connection_string = settings.database_url
    sampling_rate = 1.0
    run_id = uuid.uuid4().hex

    logger.info("Bắt đầu Run 1 | run_id=%s | dataset=%s | db=%s", run_id, dataset_id, connection_string)
    create_run(run_id=run_id, dataset_id=dataset_id)
    update_run_status(run_id=run_id, status="RUNNING")

    proposal_graph = build_proposal_graph()

    initial_state = {
        "dataset_id": dataset_id,
        "rule_run_id": run_id,
        "metadata": {
            "connection_string": connection_string,
            "sampling_rate": sampling_rate,
        },
    }

    try:
        final_state = await proposal_graph.ainvoke(initial_state)
        update_run_status(run_id=run_id, status="DONE")

        rules = list_rules(run_id=run_id)
        summary = get_review_summary(run_id=run_id)

        print("\n" + "=" * 70)
        print(f"🎉 RUN 1 HOÀN THÀNH THÀNH CÔNG (run_id: {run_id})")
        print("=" * 70)
        print(f"• Tổng số rules đề xuất : {summary.get('total', len(rules))}")
        print(f"• Trạng thái            : {final_state.get('metadata', {}).get('hitl_status', 'AWAITING_REVIEW')}")
        print(f"• File trace debug       : {final_state.get('metadata', {}).get('trace_path', 'N/A')}")
        print("\n📊 Phân bố theo Data Quality Dimension:")
        for dim, stats in summary.get("by_dimension", {}).items():
            print(f"   - {dim:<20}: {stats.get('total', 0)} rules")

        print("\n🔍 Top 5 rules mẫu vừa sinh:")
        for i, rule in enumerate(rules[:5], start=1):
            print(f"\n[{i}] {rule.get('rule_id')} ({rule.get('dimension')}) - Mức độ: {rule.get('severity')}")
            print(f"    Mô tả      : {rule.get('rule_description')}")
            print(f"    AI Suy luận: {rule.get('ai_reasoning')}")
            print(f"    Tham số    : {rule.get('parameters')}")

        print("\n" + "=" * 70 + "\n")

    except Exception as exc:
        logger.error("Run 1 thất bại: %s", exc, exc_info=True)
        update_run_status(run_id=run_id, status="FAILED", error=str(exc))
        print(f"\n❌ RUN 1 THẤT BẠI: {exc}\n")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

