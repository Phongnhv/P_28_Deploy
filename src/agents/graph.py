import logging
import os
import sys
from typing import Literal
from dotenv import load_dotenv

load_dotenv()

# Only enable Phoenix OpenTelemetry tracing when explicitly requested and not in test/disabled mode
if "pytest" not in sys.modules and not os.getenv("DISABLE_TRACING") and (os.getenv("ENABLE_PHOENIX") == "true" or os.getenv("PHOENIX_COLLECTOR_ENDPOINT")):
    try:
        from openinference.instrumentation.langchain import LangChainInstrumentor
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        phoenix_host = "localhost" if os.name == "nt" or not os.path.exists("/.dockerenv") else "host.docker.internal"
        phoenix_endpoint = os.getenv("PHOENIX_COLLECTOR_ENDPOINT") or f"http://{phoenix_host}:6006/v1/traces"
        tracer_provider = TracerProvider()
        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=phoenix_endpoint))
        )
        trace.set_tracer_provider(tracer_provider)
        LangChainInstrumentor().instrument()
    except Exception:
        pass


from langgraph.graph import END, StateGraph

from src.agents.state import AgentState


# ---------------------------------------------------------------------------
# Run 1: Proposal Graph (profiler → digest → rule_proposer → persist_rules)
# ---------------------------------------------------------------------------
def build_proposal_graph() -> StateGraph:
    """Xây dựng graph cho Run 1 — kết thúc sau khi persist rules vào DB và ghi trace.

    Luồng: raw_profiler → profiler_digest → dataset_understanding → hitl_semantic_gate → rule_candidate_builder → prompt_customizer → rule_proposer → hitl_gate → END
    Bao gồm chốt chặn duyệt Semantic Contract động và tự động viết lại system prompt theo nghiệp vụ riêng của từng bảng.
    """
    from src.agents.nodes.data_dictionary_generator_node import data_dictionary_generator_node
    from src.agents.nodes.dataset_understanding_node import dataset_understanding_node
    from src.agents.nodes.hitl_gate_node import hitl_gate_node
    from src.agents.nodes.hitl_semantic_gate_node import hitl_semantic_gate_node
    from src.agents.nodes.profiler_node import profiler_digest_node, raw_profiler_node
    from src.agents.nodes.prompt_customizer_node import prompt_customizer_node
    from src.agents.nodes.rule_candidate_builder_node import rule_candidate_builder_node
    from src.agents.nodes.rule_proposer_node import rule_proposer_node

    def _should_continue_proposal(state: AgentState) -> str:
        # Dừng khi có lỗi thật HOẶC khi một gate chủ động tạm dừng chờ người duyệt.
        # Hai trường hợp này dẫn tới cùng một điểm kết thúc graph nhưng runner sẽ ghi
        # trạng thái run khác nhau (FAILED vs AWAITING_SEMANTIC_REVIEW).
        if state.get("error") or state.get("pause_reason"):
            return END
        return "next"

    def _route_entry(state: AgentState) -> str:
        contract = state.get("semantic_contract")
        if contract and contract.get("status") == "confirmed":
            return "rule_candidate_builder"
        return "raw_profiler"

    graph = StateGraph(AgentState)

    graph.add_node("raw_profiler", raw_profiler_node)
    graph.add_node("profiler_digest", profiler_digest_node)
    graph.add_node("dataset_understanding", dataset_understanding_node)
    graph.add_node("data_dictionary_generator", data_dictionary_generator_node)
    graph.add_node("hitl_semantic_gate", hitl_semantic_gate_node)
    graph.add_node("rule_candidate_builder", rule_candidate_builder_node)
    graph.add_node("prompt_customizer", prompt_customizer_node)
    graph.add_node("rule_proposer", rule_proposer_node)
    graph.add_node("hitl_gate", hitl_gate_node)

    # Entry point động
    graph.set_conditional_entry_point(
        _route_entry,
        {"rule_candidate_builder": "rule_candidate_builder", "raw_profiler": "raw_profiler"}
    )

    # raw_profiler → profiler_digest (hoặc END nếu lỗi)
    graph.add_conditional_edges(
        "raw_profiler",
        _should_continue_proposal,
        {"next": "profiler_digest", END: END},
    )

    # profiler_digest → dataset_understanding (hoặc END nếu lỗi)
    graph.add_conditional_edges(
        "profiler_digest",
        lambda state: END if state.get("error") else ("dataset_understanding" if state.get("normalized_data_dictionary") else "data_dictionary_generator"),
        {"dataset_understanding": "dataset_understanding", "data_dictionary_generator": "data_dictionary_generator", END: END},
    )

    graph.add_conditional_edges(
        "data_dictionary_generator",
        _should_continue_proposal,
        {"next": "dataset_understanding", END: END},
    )

    # dataset_understanding → hitl_semantic_gate (hoặc END nếu lỗi)
    graph.add_conditional_edges(
        "dataset_understanding",
        _should_continue_proposal,
        {"next": "hitl_semantic_gate", END: END},
    )

    # hitl_semantic_gate → rule_candidate_builder (hoặc END nếu lỗi/pause)
    graph.add_conditional_edges(
        "hitl_semantic_gate",
        _should_continue_proposal,
        {"next": "rule_candidate_builder", END: END},
    )

    # rule_candidate_builder → prompt_customizer (hoặc END nếu lỗi)
    graph.add_conditional_edges(
        "rule_candidate_builder",
        _should_continue_proposal,
        {"next": "prompt_customizer", END: END},
    )

    # prompt_customizer → rule_proposer (hoặc END nếu lỗi)
    graph.add_conditional_edges(
        "prompt_customizer",
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
# Run 2: Execution Graph (Test Generator ➔ Validate ➔ Run ➔ Persist)
# ---------------------------------------------------------------------------

def _should_run_or_fail(state: AgentState) -> str:
    """Route the dbt artifact to execution or terminal failure."""
    if state.get("dbt_validation_valid") is True:
        return "run"
    return "fail"


async def _fail_dbt_validation_node(state: AgentState) -> dict:
    from src.services.rule_store import update_test_run_status

    error = state.get("dbt_validation_error") or "dbt project validation failed"
    run_id = state.get("test_run_id") or state.get("rule_run_id")
    if run_id:
        update_test_run_status(str(run_id), "FAILED", error=error)
    errors = list(state.get("test_generation_errors", []))
    if error not in errors:
        errors.append(error)
    return {"error": error, "test_generation_errors": errors}


def build_execution_graph() -> StateGraph:
    """Xây dựng graph cho Run 2 (Execution Graph - Deterministic).

    Luồng:
      test_generator ➔ validate_dbt_project ➔ (run or fail) ➔ test_runner ➔ persist_report ➔ END
    """
    from src.agents.nodes.persist_report_node import persist_report_node
    from src.agents.nodes.test_generator_node import test_generator_node
    from src.agents.nodes.test_runner_node import test_runner_node
    from src.agents.nodes.validate_dbt_project_node import validate_dbt_project_node

    graph = StateGraph(AgentState)

    graph.add_node("test_generator", test_generator_node)
    graph.add_node("validate_dbt_project", validate_dbt_project_node)
    graph.add_node("dbt_validation_failed", _fail_dbt_validation_node)
    graph.add_node("test_runner", test_runner_node)
    graph.add_node("persist_report", persist_report_node)

    graph.set_entry_point("test_generator")

    # test_generator -> validate the generated dbt project
    graph.add_edge("test_generator", "validate_dbt_project")

    # Route based on validation
    graph.add_conditional_edges(
        "validate_dbt_project",
        _should_run_or_fail,
        {"run": "test_runner", "fail": "dbt_validation_failed"},
    )

    graph.add_edge("dbt_validation_failed", END)
    graph.add_edge("test_runner", "persist_report")
    graph.add_edge("persist_report", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# Run 3: Anomaly Graph (Detector ➔ Hypothesis ➔ Persist)
# ---------------------------------------------------------------------------

def build_anomaly_graph(investigation_mode: Literal["deepagent", "legacy"] | None = None) -> StateGraph:
    """Xây dựng graph cho Run 3 (Anomaly Analysis Graph).

    Args:
        investigation_mode: "deepagent" (Deep Agent + Tools + Skills) hoặc "legacy" (Steward Insights prompt cũ).
                           Nếu None, lấy từ config (settings.anomaly_investigation_mode).

    Luồng:
      anomaly_detector ➔ hypothesis_agent ➔ persist_analysis ➔ report_writer ➔ END
    """
    from src.config import get_settings
    from src.agents.nodes.anomaly_detector_node import anomaly_detector_node
    from src.agents.nodes.persist_analysis_node import persist_analysis_node
    from src.agents.nodes.report_writer_node import report_writer_node
    from src.agents.state import AnomalyGraphState

    mode = investigation_mode or get_settings().anomaly_investigation_mode

    if mode == "legacy":
        from src.agents.nodes.steward_insights_node import steward_insights_node
        hypothesis_agent = steward_insights_node
    else:
        from src.agents.nodes.anomaly_investigation_node import anomaly_investigation_node
        hypothesis_agent = anomaly_investigation_node

    graph = StateGraph(AnomalyGraphState)

    graph.add_node("anomaly_detector", anomaly_detector_node)
    graph.add_node("hypothesis_agent", hypothesis_agent)
    graph.add_node("persist_analysis", persist_analysis_node)
    graph.add_node("report_writer", report_writer_node)

    graph.set_entry_point("anomaly_detector")
    graph.add_edge("anomaly_detector", "hypothesis_agent")
    graph.add_edge("hypothesis_agent", "persist_analysis")
    graph.add_edge("persist_analysis", "report_writer")
    graph.add_edge("report_writer", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# Pipeline Runners (Run 1 & Run 2)
# ---------------------------------------------------------------------------

logger = logging.getLogger("graph_runner")

#: Dataset mặc định CHỈ dùng cho CLI (`python -m src.agents.graph`). Các hàm runner bên
#: dưới bắt buộc truyền dataset_id — trước đây cả ba đều mặc định về dataset NYC taxi nên
#: caller quên truyền sẽ âm thầm chạy trên dataset sai mà không có cảnh báo nào.
DEFAULT_CLI_DATASET_ID = "dataset-nyc-yellow-taxi-50k"

async def run_proposal_graph(
    dataset_id: str,
    connection_string: str | None = None,
    sampling_rate: float = 1.0,
    auto_confirm_semantic: bool = True,
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
            "auto_confirm_semantic": auto_confirm_semantic,
        },
    }

    try:
        final_state = await proposal_graph.ainvoke(initial_state)

        # `ainvoke` KHÔNG ném exception khi một node trả về {"error": ...} — graph chỉ
        # định tuyến sang END. Trước đây runner ghi "DONE" vô điều kiện nên một Run 1
        # thất bại hoàn toàn (LLM hết quota → 0 rules) vẫn được báo là thành công, và
        # trạng thái AWAITING_SEMANTIC_REVIEW do gate ghi cũng bị ghi đè ngay lập tức.
        pause_reason = final_state.get("pause_reason")
        graph_error = final_state.get("error")

        if pause_reason:
            update_run_status(run_id=run_id, status=str(pause_reason))
            logger.info("Run 1 tạm dừng chờ người duyệt | run_id=%s | lý do=%s", run_id, pause_reason)
            print(f"\n⏸️  RUN 1 TẠM DỪNG — {pause_reason} (Proposal run_id: {run_id})\n")
            return {
                "run_id": run_id,
                "status": str(pause_reason),
                "rules": list_rules(run_id=run_id),
                "summary": get_review_summary(run_id=run_id),
            }

        if graph_error:
            update_run_status(run_id=run_id, status="FAILED", error=str(graph_error))
            logger.error("Run 1 thất bại trong graph | run_id=%s | error=%s", run_id, graph_error)
            print(f"\n❌ RUN 1 THẤT BẠI: {graph_error}\n")
            return {
                "run_id": run_id,
                "status": "FAILED",
                "error": str(graph_error),
                "rules": list_rules(run_id=run_id),
                "summary": get_review_summary(run_id=run_id),
            }

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
        return {"run_id": run_id, "status": "DONE", "rules": rules, "summary": summary}

    except Exception as exc:
        logger.error("Run 1 thất bại: %s", exc, exc_info=True)
        update_run_status(run_id=run_id, status="FAILED", error=str(exc))
        print(f"\n❌ RUN 1 THẤT BẠI: {exc}\n")
        raise


async def run_execution_graph(
    dataset_id: str,
    proposal_run_id: str | None = None,
) -> dict:
    """Chạy toàn bộ pipeline Run 2 (Thực thi Test): Active Rules -> Generator -> Validate -> Runner -> Report."""
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

        passed = sum(1 for r in results if r["status"] == "PASS")
        failed = sum(1 for r in results if r["status"] == "FAIL")
        errors = sum(1 for r in results if r["status"] == "ERROR")

        print("\n" + "=" * 75)
        print(f"🎉 RUN 2 HOÀN THÀNH THÀNH CÔNG (test_run_id: {test_run_id})")
        print("=" * 75)
        print(f"• Tổng số rules đã test : {len(results)}")
        print(f"• Kết quả               : {passed} PASSED | {failed} FAILED | {errors} ERROR")
        print(f"• File báo cáo JSON     : {final_state.get('metadata', {}).get('report_file_path', 'data/results/')}")

        # Trigger Graph 3 (Anomaly Analysis) dynamically for CLI/test parity
        anomaly_state = await run_anomaly_graph(execution_run_id=test_run_id, dataset_id=dataset_id)
        anomalies = anomaly_state.get("signal_observations", [])
        decision_data = anomaly_state.get("anomaly_decision", {})

        print("\n" + "=" * 75 + "\n")
        return {
            "test_run_id": test_run_id,
            "results": results,
            "anomalies": anomalies,
            "dq_score": final_state.get("dq_score", 100.0),
            "anomaly_decision": decision_data
        }

    except Exception as exc:
        logger.error("Run 2 thất bại: %s", exc, exc_info=True)
        update_test_run_status(test_run_id=test_run_id, status="FAILED", error=str(exc))
        print(f"\n❌ RUN 2 THẤT BẠI: {exc}\n")
        raise


async def run_anomaly_graph(
    execution_run_id: str | None = None,
    dataset_id: str = DEFAULT_CLI_DATASET_ID,
    investigation_mode: Literal["deepagent", "legacy"] | None = None,
) -> dict:
    """Chạy toàn bộ pipeline Run 3 (Anomaly Analysis & Hypothesis).

    Args:
        execution_run_id: ID của lần chạy test (DqRun). Nếu None, tự động lấy run mới nhất từ CSDL.
        dataset_id: ID của dataset cần phân tích bất thường.
        investigation_mode: "deepagent" hoặc "legacy". Nếu None, lấy từ config.
    """
    import uuid
    from src.config import get_settings
    from src.models.database import DqRunModel
    from src.services.rule_store import get_engine
    from sqlalchemy.orm import Session

    settings = get_settings()
    active_mode = investigation_mode or settings.anomaly_investigation_mode

    # Tự động tìm execution_run_id mới nhất nếu caller không truyền
    if not execution_run_id:
        try:
            with Session(get_engine()) as db:
                latest_run = (
                    db.query(DqRunModel)
                    .filter(DqRunModel.dataset_id == dataset_id)
                    .order_by(DqRunModel.created_at.desc())
                    .first()
                )
                if latest_run:
                    execution_run_id = latest_run.id
                else:
                    # Lấy run bất kỳ mới nhất nếu không khớp dataset_id
                    any_run = db.query(DqRunModel).order_by(DqRunModel.created_at.desc()).first()
                    execution_run_id = any_run.id if any_run else uuid.uuid4().hex
        except Exception:
            execution_run_id = uuid.uuid4().hex

    anomaly_run_id = f"anom-{uuid.uuid4().hex[:12]}"

    anomaly_graph = build_anomaly_graph(investigation_mode=active_mode)
    initial_state = {
        "anomaly_run_id": anomaly_run_id,
        "execution_run_id": execution_run_id,
        "dataset_id": dataset_id,
        "detector_config_version": "anomaly-v1",
        "metadata": {
            "investigation_mode": active_mode,
        },
    }

    logger.info(
        "Bắt đầu Run 3 (Anomaly Analysis) [Mode: %s] | anomaly_run_id=%s | execution_run_id=%s",
        active_mode,
        anomaly_run_id,
        execution_run_id,
    )

    try:
        final_state = await anomaly_graph.ainvoke(initial_state)
        decision_data = final_state.get("anomaly_decision", {})
        signals = final_state.get("signal_observations", [])
        hypotheses = final_state.get("hypotheses", [])
        # report_writer_node sets steward_report_path in state and metadata
        steward_report_path = (
            final_state.get("steward_report_path")
            or final_state.get("metadata", {}).get("steward_report_path")
        )
        llm_used = final_state.get("metadata", {}).get("steward_report_llm_used", False)
        trace_path = final_state.get("metadata", {}).get("investigation_trace_path", "")

        logger.info(
            "Run 3 completed | mode=%s anomaly_run_id=%s decision=%s score=%s confidence=%s signals=%d hypotheses=%d report=%s trace=%s",
            active_mode,
            anomaly_run_id,
            decision_data.get("decision", "NORMAL"),
            decision_data.get("score", 0.0),
            decision_data.get("confidence", 0.0),
            len(signals),
            len(hypotheses),
            steward_report_path or "not-written",
            trace_path or "none",
        )
        return final_state
    except Exception as exc:
        logger.error("Run 3 thất bại: %s", exc, exc_info=True)
        raise


async def main():
    """CLI Menu lựa chọn chạy Run 1, Run 2 hoặc Run 3 (với DeepAgent / Legacy switch)."""
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    args = sys.argv[1:]
    mode = args[0] if args else "all"

    # Trích xuất các flag điều tra (investigation mode)
    inv_mode: Literal["deepagent", "legacy"] = "deepagent"
    cleaned_args = []
    for arg in args:
        if arg in ("--legacy", "-legacy", "legacy", "--mode=legacy"):
            inv_mode = "legacy"
        elif arg in ("--deepagent", "-deepagent", "deepagent", "--mode=deepagent"):
            inv_mode = "deepagent"
        else:
            cleaned_args.append(arg)

    mode = cleaned_args[0] if cleaned_args else "all"
    dataset_id = cleaned_args[1] if len(cleaned_args) > 1 else DEFAULT_CLI_DATASET_ID

    if mode in ("1", "proposal"):
        print(f"🚀 Lựa chọn: CHẠY RUN 1 (Proposal Graph) cho dataset {dataset_id}")
        await run_proposal_graph(dataset_id=dataset_id)
    elif mode in ("2", "execution"):
        print(f"🚀 Lựa chọn: CHẠY RUN 2 (Execution Graph trên Active Rules) cho dataset {dataset_id}")
        await run_execution_graph(dataset_id=dataset_id)
    elif mode in ("3", "anomaly", "investigate"):
        print(f"🚀 Lựa chọn: CHẠY RUN 3 (Anomaly Investigation Graph) cho dataset {dataset_id}")
        print(f"   ⚙️ Investigation Mode: [{inv_mode.upper()}] (Sử dụng {'Deep Agent + Tools + Skills' if inv_mode == 'deepagent' else 'Legacy Single-Shot Prompt'})")
        res = await run_anomaly_graph(dataset_id=dataset_id, investigation_mode=inv_mode)
        print("\n" + "=" * 70)
        print(f"🎉 HOÀN TẤT RUN 3 [{inv_mode.upper()}]: Quyết định = {res.get('anomaly_decision', {}).get('decision')} | Số giả thuyết = {len(res.get('hypotheses', []))}")
        print("=" * 70 + "\n")
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
