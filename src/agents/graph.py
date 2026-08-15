import logging

from langgraph.graph import END, StateGraph

from src.agents.state import AgentState

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


def build_dashboard_proposal_graph() -> StateGraph:
    """Build the product-facing proposal graph.

    The dashboard already persists an aggregate profile.  This entrypoint deliberately
    starts at the structured proposer rather than running the legacy database-wide
    profiler or the legacy HITL persistence node.  The caller validates and persists
    the resulting typed proposals in the dashboard workflow models.
    """
    from src.agents.nodes.rule_proposer_node import rule_proposer_node

    graph = StateGraph(AgentState)
    graph.add_node("rule_proposer", rule_proposer_node)
    graph.set_entry_point("rule_proposer")
    graph.add_edge("rule_proposer", END)
    return graph.compile()


# ---------------------------------------------------------------------------
# Run 2: Execution Graph (Test Generator ➔ Validate ➔ Repair ➔ Run ➔ Anomaly ➔ Steward Insights ➔ Persist)
# ---------------------------------------------------------------------------

def _should_repair_or_run(state: AgentState) -> str:
    """Route the dbt artifact to execution, repair, or terminal failure."""
    if state.get("dbt_validation_valid") is True:
        return "run"
    if int(state.get("dbt_validation_attempts", 0)) < 3:
        return "repair"
    return "fail"


async def _fail_dbt_validation_node(state: AgentState) -> dict:
    from src.services.rule_store import update_test_run_status

    error = state.get("dbt_validation_error") or "dbt project validation failed after 3 repair attempts"
    run_id = state.get("test_run_id") or state.get("rule_run_id")
    if run_id:
        update_test_run_status(str(run_id), "FAILED", error=error)
    errors = list(state.get("test_generation_errors", []))
    if error not in errors:
        errors.append(error)
    return {"error": error, "test_generation_errors": errors}


def build_execution_graph() -> StateGraph:
    """Xây dựng graph cho Run 2 (Execution Graph).

    Luồng:
      test_generator ➔ validate_dbt_project ➔ (repair loop nếu lỗi) ➔ test_runner ➔ anomaly_detector ➔ steward_insights ➔ persist_report ➔ END
    """
    from src.agents.nodes.anomaly_detector_node import anomaly_detector_node
    from src.agents.nodes.llm_dbt_repair_node import llm_dbt_repair_node
    from src.agents.nodes.persist_report_node import persist_report_node
    from src.agents.nodes.steward_insights_node import steward_insights_node
    from src.agents.nodes.test_generator_node import test_generator_node
    from src.agents.nodes.test_runner_node import test_runner_node
    from src.agents.nodes.validate_dbt_project_node import validate_dbt_project_node

    graph = StateGraph(AgentState)

    graph.add_node("test_generator", test_generator_node)
    graph.add_node("validate_dbt_project", validate_dbt_project_node)
    graph.add_node("llm_dbt_repair", llm_dbt_repair_node)
    graph.add_node("dbt_validation_failed", _fail_dbt_validation_node)
    graph.add_node("test_runner", test_runner_node)
    graph.add_node("anomaly_detector", anomaly_detector_node)
    graph.add_node("steward_insights", steward_insights_node)
    graph.add_node("persist_report", persist_report_node)

    graph.set_entry_point("test_generator")

    # test_generator -> validate the generated dbt project
    graph.add_edge("test_generator", "validate_dbt_project")

    # Invalid projects are repaired at most three times; exhausted runs fail before execution.
    graph.add_conditional_edges(
        "validate_dbt_project",
        _should_repair_or_run,
        {"repair": "llm_dbt_repair", "run": "test_runner", "fail": "dbt_validation_failed"},
    )

    # Repair updates YAML in state and routes back through the same dbt quality gate.
    graph.add_edge("llm_dbt_repair", "validate_dbt_project")
    graph.add_edge("dbt_validation_failed", END)

    # test_runner ➔ anomaly_detector ➔ steward_insights ➔ persist_report ➔ END
    graph.add_edge("test_runner", "anomaly_detector")
    graph.add_edge("anomaly_detector", "steward_insights")
    graph.add_edge("steward_insights", "persist_report")
    graph.add_edge("persist_report", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# Pipeline Runners (Run 1 & Run 2)
# ---------------------------------------------------------------------------

logger = logging.getLogger("graph_runner")

async def run_proposal_graph(
    dataset_id: str = "dataset-nyc-yellow-taxi-50k",
    connection_string: str | None = None,
    sampling_rate: float = 1.0,
) -> dict:
    """Chạy toàn bộ pipeline Run 1 (Đề xuất Rules): Profiler -> Digest -> Proposer -> HITL Gate."""
    import uuid

    from src.config import get_settings
    from src.services.rule_store import create_run, get_review_summary, init_db, list_rules, update_run_status

    init_db()
    settings = get_settings()
    conn_str = connection_string or settings.database_url
    run_id = uuid.uuid4().hex

    logger.info("Bắt đầu Run 1 (Proposal) | run_id=%s | dataset=%s", run_id, dataset_id)
    create_run(run_id=run_id, dataset_id=dataset_id)
    update_run_status(run_id=run_id, status="RUNNING")

    proposal_graph = build_proposal_graph()
    initial_state = {
        "dataset_id": dataset_id,
        "rule_run_id": run_id,
        "metadata": {
            "connection_string": conn_str,
            "sampling_rate": sampling_rate,
        },
    }

    try:
        final_state = await proposal_graph.ainvoke(initial_state)
        update_run_status(run_id=run_id, status="DONE")

        rules = list_rules(run_id=run_id)
        summary = get_review_summary(run_id=run_id)

        print("\n" + "=" * 75)
        print(f"🎉 RUN 1 HOÀN THÀNH THÀNH CÔNG (Proposal run_id: {run_id})")
        print("=" * 75)
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

        print("\n" + "=" * 75 + "\n")
        return {"run_id": run_id, "rules": rules, "summary": summary}

    except Exception as exc:
        logger.error("Run 1 thất bại: %s", exc, exc_info=True)
        update_run_status(run_id=run_id, status="FAILED", error=str(exc))
        print(f"\n❌ RUN 1 THẤT BẠI: {exc}\n")
        raise


async def run_execution_graph(
    dataset_id: str = "dataset-nyc-yellow-taxi-50k",
    proposal_run_id: str | None = None,
) -> dict:
    """Chạy toàn bộ pipeline Run 2 (Thực thi Test): Active Rules -> Generator -> Validate -> Runner -> Anomaly -> Steward Insights -> Report."""
    import uuid

    from src.services.rule_store import (
        create_test_run,
        get_active_rules,
        get_approved_rules,
        get_test_results,
        get_test_run,
        init_db,
        update_test_run_status,
    )

    init_db()
    test_run_id = uuid.uuid4().hex
    create_test_run(test_run_id=test_run_id, dataset_id=dataset_id)
    update_test_run_status(test_run_id=test_run_id, status="RUNNING")

    # Lấy rules cần test: Ưu tiên Active Rules, hoặc load từ proposal_run_id
    if proposal_run_id:
        rules_to_test = get_approved_rules(proposal_run_id)
    else:
        rules_to_test = get_active_rules(dataset_id=dataset_id)

    logger.info("Bắt đầu Run 2 (Execution) | test_run_id=%s | rules_count=%d", test_run_id, len(rules_to_test))

    execution_graph = build_execution_graph()
    initial_state = {
        "dataset_id": dataset_id,
        "test_run_id": test_run_id,
        "rule_run_id": proposal_run_id,
        "approved_rules": rules_to_test,
    }

    try:
        final_state = await execution_graph.ainvoke(initial_state)

        _test_run_rec = get_test_run(test_run_id)
        results = get_test_results(test_run_id)
        anomalies = final_state.get("anomalies", [])
        dq_score = final_state.get("dq_score", 100.0)
        dq_grade = final_state.get("dq_grade", "A")

        passed = sum(1 for r in results if r["status"] == "PASSED")
        failed = sum(1 for r in results if r["status"] == "FAILED")
        errors = sum(1 for r in results if r["status"] == "ERROR")

        print("\n" + "=" * 75)
        print(f"🎉 RUN 2 HOÀN THÀNH THÀNH CÔNG (test_run_id: {test_run_id})")
        print("=" * 75)
        print(f"• DQ Health Score       : {dq_score}/100 (Grade {dq_grade})")
        print(f"• Tổng số rules đã test : {len(results)}")
        print(f"• Kết quả               : {passed} PASSED | {failed} FAILED | {errors} ERROR")
        print(f"• Số điểm dị thường     : {len(anomalies)}")
        print(f"• File báo cáo JSON     : {final_state.get('metadata', {}).get('report_file_path', 'data/results/')}")
        print(f"• File báo cáo Markdown : {final_state.get('metadata', {}).get('steward_report_path', 'N/A')}")

        if anomalies:
            print("\n🚨 CÁC ĐIỂM DỊ THƯỜNG PHÁT HIỆN ĐƯỢC:")
            for idx, anom in enumerate(anomalies, 1):
                print(f"   [{idx}] {anom.get('rule_id')} ({anom.get('anomaly_type')})")
                print(f"       Lý do: {anom.get('reason')}")

        print("\n" + "=" * 75 + "\n")
        return {"test_run_id": test_run_id, "results": results, "anomalies": anomalies, "dq_score": dq_score}

    except Exception as exc:
        logger.error("Run 2 thất bại: %s", exc, exc_info=True)
        update_test_run_status(test_run_id=test_run_id, status="FAILED", error=str(exc))
        print(f"\n❌ RUN 2 THẤT BẠI: {exc}\n")
        raise


async def main():
    """CLI Menu lựa chọn chạy Run 1 (Đề xuất) hoặc Run 2 (Chạy test)."""
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    args = sys.argv[1:]
    mode = args[0] if args else "all"
    dataset_id = args[1] if len(args) > 1 else "dataset-nyc-yellow-taxi-50k"

    if mode == "1" or mode == "proposal":
        print(f"🚀 Lựa chọn: CHẠY RUN 1 (Proposal Graph) cho dataset {dataset_id}")
        await run_proposal_graph(dataset_id=dataset_id)
    elif mode == "2" or mode == "execution":
        print(f"🚀 Lựa chọn: CHẠY RUN 2 (Execution Graph trên Active Rules) cho dataset {dataset_id}")
        await run_execution_graph(dataset_id=dataset_id)
    else:
        print(f"🚀 Lựa chọn mặc định: CHẠY RUN 1 ➔ DUYỆT & PUBLISH ➔ CHẠY RUN 2 cho dataset {dataset_id}")
        from src.services.rule_store import publish_approved_rules, review_rule

        # 1. Chạy Run 1
        prop_res = await run_proposal_graph(dataset_id=dataset_id)
        run_id = prop_res["run_id"]
        rules = prop_res["rules"]

        # Tự động approve các rules đề xuất mẫu để test publishing
        print(f"📝 Đang tự động duyệt và publish {len(rules)} rules vào Active Ruleset...")
        for r in rules:
            review_rule(run_id=run_id, rule_id=r["rule_id"], status="APPROVED")
        publish_approved_rules(run_id=run_id)

        # 2. Chạy Run 2 trên Active Ruleset
        await run_execution_graph(dataset_id=dataset_id)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
