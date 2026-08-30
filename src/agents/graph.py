import asyncio
import inspect
import json
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from typing import Literal

from dotenv import load_dotenv
from langgraph.graph import END, StateGraph

from src.agents.graph_catalog import DETERMINISTIC, LLM
from src.agents.state import AgentState
from src.services.node_telemetry import instrument, start_graph_run

load_dotenv()

# Only enable Phoenix OpenTelemetry tracing when explicitly requested and not in test/disabled mode
if (
    "pytest" not in sys.modules
    and not os.getenv("DISABLE_TRACING")
    and (os.getenv("ENABLE_PHOENIX") == "true" or os.getenv("PHOENIX_COLLECTOR_ENDPOINT"))
):
    try:
        from openinference.instrumentation.langchain import LangChainInstrumentor
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        phoenix_host = "localhost" if os.name == "nt" or not os.path.exists("/.dockerenv") else "host.docker.internal"
        phoenix_endpoint = os.getenv("PHOENIX_COLLECTOR_ENDPOINT") or f"http://{phoenix_host}:6006/v1/traces"
        tracer_provider = TracerProvider()
        tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=phoenix_endpoint)))
        trace.set_tracer_provider(tracer_provider)
        LangChainInstrumentor().instrument()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Run 1: Proposal Graph (profiler → digest → rule_proposer → persist_rules)
# ---------------------------------------------------------------------------
def build_understanding_graph() -> StateGraph:
    """Graph 1A: consume persisted profile evidence and stop after understanding."""
    from src.agents.nodes.data_dictionary_generator_node import data_dictionary_generator_node
    from src.agents.nodes.dataset_understanding_node import dataset_understanding_node
    from src.agents.nodes.profiler_node import profiler_digest_node

    graph = StateGraph(AgentState)
    graph.add_node(
        "build_profile_digest",
        instrument("G1A", "build_profile_digest", DETERMINISTIC)(profiler_digest_node),
    )
    graph.add_node(
        "data_dictionary_generator",
        instrument("G1A", "data_dictionary_generator", LLM)(data_dictionary_generator_node),
    )
    graph.add_node(
        "dataset_understanding",
        instrument("G1A", "dataset_understanding", LLM)(dataset_understanding_node),
    )
    graph.set_entry_point("build_profile_digest")
    graph.add_conditional_edges("build_profile_digest", lambda state: END if state.get("error") else ("dataset_understanding" if state.get("normalized_data_dictionary") else "data_dictionary_generator"), {"dataset_understanding": "dataset_understanding", "data_dictionary_generator": "data_dictionary_generator", END: END})
    graph.add_edge("data_dictionary_generator", "dataset_understanding")
    graph.add_edge("dataset_understanding", END)
    return graph.compile()


def build_rule_proposal_graph() -> StateGraph:
    """Graph 1B: candidate/context/proposal work from a confirmed contract."""
    from src.agents.nodes.prompt_customizer_node import prompt_customizer_node
    from src.agents.nodes.rule_candidate_builder_node import rule_candidate_builder_node
    from src.agents.nodes.rule_proposer_node import rule_proposer_node

    graph = StateGraph(AgentState)
    graph.add_node(
        "rule_candidate_builder",
        instrument("G1B", "rule_candidate_builder", DETERMINISTIC)(rule_candidate_builder_node),
    )
    graph.add_node(
        "prompt_customizer",
        instrument("G1B", "prompt_customizer", LLM)(prompt_customizer_node),
    )
    graph.add_node("rule_proposer", instrument("G1B", "rule_proposer", LLM)(rule_proposer_node))
    graph.set_entry_point("rule_candidate_builder")
    graph.add_edge("rule_candidate_builder", "prompt_customizer")
    graph.add_edge("prompt_customizer", "rule_proposer")
    graph.add_edge("rule_proposer", END)
    return graph.compile()


def _timed_node(node_name: str, node_func: Callable) -> Callable:
    """Wrap a graph node to track its execution duration and set current node for token attribution."""
    import time

    from src.utils.metrics_tracker import get_metrics_tracker

    async def _wrapped(state: AgentState) -> dict:
        tracker = get_metrics_tracker()
        token = tracker.set_current_node(node_name)
        start_ts = time.perf_counter()
        try:
            res = node_func(state)
            if inspect.isawaitable(res):
                res = await res
            return res
        finally:
            duration = time.perf_counter() - start_ts
            tracker.record_node_time(node_name, duration)
            tracker.reset_current_node(token)

    return _wrapped


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

    def _route_after_rule_proposer(state: AgentState) -> str:
        rules = state.get("proposed_rules") or []
        if state.get("error") or not rules:
            return END
        return "hitl_gate"

    graph = StateGraph(AgentState)

    graph.add_node("raw_profiler", _timed_node("raw_profiler", raw_profiler_node))
    graph.add_node("profiler_digest", _timed_node("profiler_digest", profiler_digest_node))
    graph.add_node("dataset_understanding", _timed_node("dataset_understanding", dataset_understanding_node))
    graph.add_node("data_dictionary_generator", _timed_node("data_dictionary_generator", data_dictionary_generator_node))
    graph.add_node("hitl_semantic_gate", _timed_node("hitl_semantic_gate", hitl_semantic_gate_node))
    graph.add_node("rule_candidate_builder", _timed_node("rule_candidate_builder", rule_candidate_builder_node))
    graph.add_node("prompt_customizer", _timed_node("prompt_customizer", prompt_customizer_node))
    graph.add_node("rule_proposer", _timed_node("rule_proposer", rule_proposer_node))
    graph.add_node("hitl_gate", _timed_node("hitl_gate", hitl_gate_node))

    # Entry point động
    graph.set_conditional_entry_point(
        _route_entry, {"rule_candidate_builder": "rule_candidate_builder", "raw_profiler": "raw_profiler"}
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
        lambda state: (
            END
            if state.get("error")
            else ("dataset_understanding" if state.get("normalized_data_dictionary") else "data_dictionary_generator")
        ),
        {
            "dataset_understanding": "dataset_understanding",
            "data_dictionary_generator": "data_dictionary_generator",
            END: END,
        },
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

    graph.add_conditional_edges(
        "rule_proposer",
        _route_after_rule_proposer,
        {"hitl_gate": "hitl_gate", END: END},
    )
    graph.add_edge("hitl_gate", END)

    return graph.compile()


def build_dashboard_proposal_graph() -> StateGraph:
    """Build the product-facing proposal graph.

    The dashboard already persists an aggregate profile and owns its HITL
    checkpoints.  This subgraph therefore resumes Graph 1 after semantic review:
    deterministic candidate construction -> prompt customization -> structured
    proposal.  The caller validates and persists the typed proposals before the
    steward review gate.
    """
    from src.agents.nodes.prompt_customizer_node import prompt_customizer_node
    from src.agents.nodes.rule_candidate_builder_node import rule_candidate_builder_node
    from src.agents.nodes.rule_proposer_node import rule_proposer_node

    graph = StateGraph(AgentState)
    graph.add_node("rule_candidate_builder", rule_candidate_builder_node)
    graph.add_node("prompt_customizer", prompt_customizer_node)
    graph.add_node("rule_proposer", rule_proposer_node)
    graph.set_entry_point("rule_candidate_builder")
    graph.add_edge("rule_candidate_builder", "prompt_customizer")
    graph.add_edge("prompt_customizer", "rule_proposer")
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


NodeObserver = Callable[[str, str, dict | None, Exception | None], Awaitable[None] | None]


def _observed_node(
    graph_name: str,
    node_key: str,
    node_fn: Callable[[dict], Awaitable[dict]],
    observer: NodeObserver | None,
):
    if observer is None:
        return node_fn

    async def wrapped(state: dict) -> dict:
        started = observer(graph_name, node_key, None, None)
        if started is not None:
            await started
        try:
            output = await node_fn(state)
        except Exception as exc:
            failed = observer(graph_name, node_key, None, exc)
            if failed is not None:
                await failed
            raise
        semantic_error = RuntimeError(str(output.get("error"))) if isinstance(output, dict) and output.get("error") else None
        completed = observer(graph_name, node_key, output, semantic_error)
        if completed is not None:
            await completed
        return output

    return wrapped


def build_execution_graph(observer: NodeObserver | None = None) -> StateGraph:
    """Xây dựng graph cho Run 2 (Execution Graph - Deterministic).

    Luồng:
      test_generator ➔ validate_dbt_project ➔ (run or fail) ➔ test_runner ➔ persist_report ➔ END
    """
    from src.agents.nodes.persist_report_node import persist_report_node
    from src.agents.nodes.test_generator_node import test_generator_node
    from src.agents.nodes.test_runner_node import test_runner_node
    from src.agents.nodes.validate_dbt_project_node import validate_dbt_project_node

    graph = StateGraph(AgentState)

    graph.add_node("test_generator", instrument("G2", "test_generator", DETERMINISTIC)(test_generator_node))
    graph.add_node(
        "validate_dbt_project",
        instrument("G2", "validate_dbt_project", DETERMINISTIC)(validate_dbt_project_node),
    )
    graph.add_node(
        "dbt_validation_failed",
        instrument("G2", "dbt_validation_failed", DETERMINISTIC)(_fail_dbt_validation_node),
    )
    graph.add_node("test_runner", instrument("G2", "test_runner", DETERMINISTIC)(test_runner_node))
    graph.add_node("persist_report", instrument("G2", "persist_report", DETERMINISTIC)(persist_report_node))

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

def build_anomaly_graph(
    investigation_mode: Literal["deepagent", "legacy"] | None = None,
    observer: NodeObserver | None = None,
) -> StateGraph:
    """Xây dựng graph cho Run 3 (Anomaly Analysis Graph).

    Args:
        investigation_mode: "deepagent" (Deep Agent + Tools + Skills) hoặc "legacy" (Steward Insights prompt cũ).
                           Nếu None, lấy từ config (settings.anomaly_investigation_mode).
        observer: Optional NodeObserver callback for streaming execution metrics.

    Luồng:
      anomaly_detector ➔ hypothesis_agent ➔ persist_analysis ➔ report_writer ➔ END
    """
    from src.agents.nodes.anomaly_detector_node import anomaly_detector_node
    from src.agents.nodes.persist_analysis_node import persist_analysis_node
    from src.agents.nodes.report_writer_node import report_writer_node
    from src.agents.state import AnomalyGraphState
    from src.config import get_settings

    mode = investigation_mode or get_settings().anomaly_investigation_mode

    if mode == "legacy":
        from src.agents.nodes.steward_insights_node import steward_insights_node

        hypothesis_agent = steward_insights_node
    else:
        from src.agents.nodes.anomaly_investigation_node import anomaly_investigation_node

        hypothesis_agent = anomaly_investigation_node

    graph = StateGraph(AnomalyGraphState)

    graph.add_node("anomaly_detector", instrument("G3", "anomaly_detector", DETERMINISTIC)(anomaly_detector_node))
    graph.add_node("hypothesis_agent", instrument("G3", "hypothesis_agent", LLM)(hypothesis_agent))
    graph.add_node("persist_analysis", instrument("G3", "persist_analysis", DETERMINISTIC)(persist_analysis_node))
    graph.add_node("report_writer", instrument("G3", "report_writer", LLM)(report_writer_node))

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


def _run_durable_proposal_workflow(dataset_id: str, auto_confirm_semantic: bool) -> dict:
    from sqlalchemy.orm import Session

    from src.models.database import (
        ColumnProfileModel,
        DatasetModel,
        ProfileModel,
        RuleProposalModel,
        WorkflowArtifactModel,
    )
    from src.services.rule_proposer_workflow import (
        confirm_semantic_contract,
        execute_step,
        get_or_create_run,
        serialize_artifact,
        serialize_run,
    )
    from src.services.rule_store import ProposedRuleModel, create_run, get_engine, init_db

    init_db()
    with Session(get_engine()) as db:
        dataset = db.get(DatasetModel, dataset_id)
        if not dataset:
            dataset = DatasetModel(
                id=dataset_id,
                name=dataset_id,
                description=f"Auto-registered dataset {dataset_id}",
                status="PROFILE_READY",
                row_count=0,
                source_label=dataset_id,
                manifest_version="1.0.0",
                checksum="auto-seeded",
            )
            db.add(dataset)
            db.flush()

        profile = db.get(ProfileModel, dataset_id)
        if not profile:
            profile = ProfileModel(
                dataset_id=dataset_id,
                row_count=100,
                completeness_score=95.0,
                validity_score=95.0,
                duplicate_rate=0.0,
                evidence_keys="[]",
            )
            db.add(profile)
            db.flush()
            cols = db.query(ColumnProfileModel).filter_by(profile_dataset_id=dataset_id).all()
            if not cols:
                db.add(
                    ColumnProfileModel(
                        profile_dataset_id=dataset_id,
                        name="status",
                        data_type="string",
                        null_rate=0.0,
                        distinct_count=2,
                        sample_value="OK",
                    )
                )
                db.flush()

        run = get_or_create_run(db, dataset, force_new=True)
        db.commit()
        create_run(run_id=run.id, dataset_id=dataset_id)
        execute_step(db, run, "UNDERSTAND_DATA")
        db.commit()
        draft = (
            db.query(WorkflowArtifactModel)
            .filter_by(
                workflow_run_id=run.id,
                step_key="UNDERSTAND_DATA",
                artifact_type="SEMANTIC_CONTRACT",
                stale=False,
            )
            .order_by(WorkflowArtifactModel.version.desc())
            .first()
        )
        if not draft:
            raise ValueError("Understanding did not produce a semantic contract")
        if not auto_confirm_semantic:
            return {
                "run_id": run.id,
                "status": "AWAITING_SEMANTIC_REVIEW",
                "workflow": serialize_run(run),
                "artifact": serialize_artifact(draft),
                "rules": [],
                "summary": {"total": 0},
            }
        payload = json.loads(draft.payload_json or "{}")
        confirm_semantic_contract(
            db,
            run,
            artifact_id=draft.id,
            expected_version=draft.version,
            contract=payload,
        )
        execute_step(db, run, "PROPOSE_RULES")
        db.commit()
        rules = db.query(RuleProposalModel).filter_by(workflow_run_id=run.id).all()

        for r in rules:
            spec = json.loads(r.rule_spec or "{}")
            spec["table_name"] = dataset_id
            spec_json = json.dumps(spec)
            db.add(
                ProposedRuleModel(
                    run_id=run.id,
                    rule_id=r.id,
                    dataset_id=dataset_id,
                    table_name=dataset_id,
                    column_name=spec.get("column"),
                    rule_type=r.rule_type,
                    parameters=spec_json,
                    confidence_score=r.confidence,
                    severity=r.severity,
                    dimension="VALIDITY",
                    rule_description=r.description,
                    ai_reasoning=r.business_rationale,
                    rule_name=r.rule_name or r.title,
                    business_rationale=r.business_rationale,
                    proposal_basis=r.proposal_basis,
                    evidence=r.evidence or "{}",
                    confidence_breakdown=r.confidence_breakdown or "{}",
                    status="PENDING",
                )
            )
        db.commit()

        serialized_rules = [
            {
                "rule_id": rule.id,
                "rule_description": rule.description,
                "severity": rule.severity,
                "status": rule.status,
                "parameters": json.loads(rule.rule_spec or "{}"),
                "ai_reasoning": rule.business_rationale,
            }
            for rule in rules
        ]
        return {
            "run_id": run.id,
            "status": "DONE",
            "workflow": serialize_run(run),
            "rules": serialized_rules,
            "summary": {"total": len(serialized_rules)},
        }


async def run_proposal_graph(
    dataset_id: str,
    connection_string: str | None = None,
    sampling_rate: float = 1.0,
    auto_confirm_semantic: bool = True,
) -> dict:
    """Chạy toàn bộ pipeline Run 1 (Đề xuất Rule): Raw Profiler -> Digest -> Understanding -> Semantic Gate -> Candidates -> Customizer -> Proposer -> HITL Gate."""
    import uuid

    from src.config import get_settings
    from src.services.rule_store import (
        create_run,
        get_review_summary,
        init_db,
        list_rules,
        update_run_status,
    )

    init_db()
    run_id = uuid.uuid4().hex
    settings = get_settings()
    conn_str = connection_string or settings.database_url

    logger.info("Bắt đầu Run 1 (Proposal) | run_id=%s | dataset=%s", run_id, dataset_id)
    create_run(run_id=run_id, dataset_id=dataset_id)
    update_run_status(run_id=run_id, status="RUNNING")

    from src.utils.metrics_tracker import get_metrics_tracker
    tracker = get_metrics_tracker()
    tracker.reset()

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
        tracker.finish()
        tracker.print_report(title=f"GRAPH 1 (PROPOSAL) REPORT — DATASET: {dataset_id}")

        pause_reason = final_state.get("pause_reason")
        graph_error = final_state.get("error")

        if pause_reason:
            update_run_status(run_id=run_id, status=str(pause_reason))
            logger.info("Run 1 tạm dừng chờ người duyệt | run_id=%s | lý do=%s", run_id, pause_reason)
            return {
                "run_id": run_id,
                "status": str(pause_reason),
                "rules": list_rules(run_id=run_id),
                "summary": get_review_summary(run_id=run_id),
            }

        if graph_error:
            update_run_status(run_id=run_id, status="FAILED", error=str(graph_error))
            logger.error("Run 1 thất bại trong graph | run_id=%s | error=%s", run_id, graph_error)
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
        return {"run_id": run_id, "status": "DONE", "rules": rules, "summary": summary}

    except Exception as exc:
        tracker.finish()
        tracker.print_report(title=f"GRAPH 1 (PROPOSAL) FAILED REPORT — DATASET: {dataset_id}")
        logger.error("Run 1 thất bại: %s", exc, exc_info=True)
        update_run_status(run_id=run_id, status="FAILED", error=str(exc))
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
    start_graph_run(dataset_id=dataset_id, dq_run_id=test_run_id)
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
            # No default. Nothing in the graph currently assigns dq_score, so a
            # default of 100.0 reported flawless quality on a dataset that had just
            # failed 8 of 31 rules with 7,672 rows missing passenger_count -- while
            # persist_report_node wrote None and the Steward report said the data had
            # serious problems. Three contradictory answers from one run.
            #
            # None is the honest answer for a computation that no longer happens: a
            # caller can see the score is absent, but cannot see that 100.0 was
            # invented. Restoring the computation is the real fix; until then the
            # absence must be visible.
            "dq_score": final_state.get("dq_score"),
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
    stream_id: str | None = None,
) -> dict:
    """Chạy toàn bộ pipeline Run 3 (Anomaly Analysis & Hypothesis).

    Args:
        execution_run_id: ID của lần chạy test (DqRun). Nếu None, tự động lấy run mới nhất từ CSDL.
        dataset_id: ID của dataset cần phân tích bất thường.
        investigation_mode: "deepagent" hoặc "legacy". Nếu None, lấy từ config.
    """
    import uuid

    from sqlalchemy.orm import Session

    from src.config import get_settings
    from src.models.database import DqRunModel
    from src.services.rule_store import get_engine

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
    start_graph_run(dataset_id=dataset_id, dq_run_id=execution_run_id, anomaly_run_id=anomaly_run_id)
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
        if stream_id:
            from src.services.node_event_stream import run_graph_streamed
            final_state = await run_graph_streamed(anomaly_graph, initial_state, stream_id)
        else:
            final_state = await anomaly_graph.ainvoke(initial_state)
        decision_data = final_state.get("anomaly_decision", {})
        signals = final_state.get("signal_observations", [])
        hypotheses = final_state.get("hypotheses", [])
        # report_writer_node sets steward_report_path in state and metadata
        steward_report_path = final_state.get("steward_report_path") or final_state.get("metadata", {}).get(
            "steward_report_path"
        )
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
        print(
            f"   ⚙️ Investigation Mode: [{inv_mode.upper()}] (Sử dụng {'Deep Agent + Tools + Skills' if inv_mode == 'deepagent' else 'Legacy Single-Shot Prompt'})"
        )
        res = await run_anomaly_graph(dataset_id=dataset_id, investigation_mode=inv_mode)
        print("\n" + "=" * 70)
        print(
            f"🎉 HOÀN TẤT RUN 3 [{inv_mode.upper()}]: Quyết định = {res.get('anomaly_decision', {}).get('decision')} | Số giả thuyết = {len(res.get('hypotheses', []))}"
        )
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
