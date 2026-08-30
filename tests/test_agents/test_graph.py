"""Integration tests for LangGraph workflows (Proposal Graph & Execution Graph)."""

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, text

import src.services.rule_store as rule_store_module
from src.agents.graph import (
    _should_run_or_fail,
    build_anomaly_graph,
    build_dashboard_proposal_graph,
    build_execution_graph,
    build_proposal_graph,
    build_rule_proposal_graph,
    build_understanding_graph,
    run_execution_graph,
    run_proposal_graph,
)
from src.services.rule_store import (
    create_run,
    create_test_run,
    get_run,
    get_test_results,
    get_test_run,
    init_db,
    publish_approved_rules,
    review_rule,
)


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    """Tạo SQLite file tạm để kiểm thử Graph."""
    db_file = tmp_path / "test_graph.db"
    sqlite_url = f"sqlite:///{db_file}"
    test_engine = create_engine(
        sqlite_url,
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(rule_store_module, "_engine", test_engine)
    # The graph tests exercise the legacy SQL metrics fallback; dbt CLI integration
    # is covered by the dedicated validation/runner integration tests.
    monkeypatch.setattr("src.agents.nodes.test_runner_node._run_dbt_cli_test", lambda _dbt_dir: False)
    monkeypatch.setattr(
        "src.agents.nodes.validate_dbt_project_node.run_dbt_parse", lambda _dbt_dir: (True, "mocked output", 0)
    )
    init_db()

    with test_engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS demo_graph_table;"))
        conn.execute(
            text("""
            CREATE TABLE demo_graph_table (
                id INTEGER PRIMARY KEY,
                fare REAL,
                status TEXT
            );
        """)
        )
        conn.execute(
            text("""
            INSERT INTO demo_graph_table VALUES
            (1, 10.0, 'OK'),
            (2, 20.0, 'OK'),
            (3, NULL, 'ERROR'),
            (4, 50.0, 'OK');
        """)
        )
        conn.commit()

    yield test_engine

    test_engine.dispose()


def test_build_graphs():
    """Kiểm tra việc biên dịch các Graph không phát sinh lỗi."""
    proposal_graph = build_proposal_graph()
    assert proposal_graph is not None

    execution_graph = build_execution_graph()
    assert execution_graph is not None

    anomaly_graph = build_anomaly_graph()
    assert anomaly_graph is not None


def test_conditional_edges_routing():
    """The execution graph routes on dbt artifact validation state."""
    state_valid = {"dbt_validation_valid": True}
    assert _should_run_or_fail(state_valid) == "run"

    state_fail = {"dbt_validation_valid": False}
    assert _should_run_or_fail(state_fail) == "fail"


@pytest.mark.asyncio
async def test_proposal_graph_skips_hitl_gate_when_rule_proposer_fails(monkeypatch):
    async def candidates(_state):
        return {"rule_candidates": [{"table": "source_rows", "rule_type": "ROW_COUNT"}]}

    async def prompts(_state):
        return {"specialized_system_prompts": {"source_rows": "prompt"}}

    async def failed_proposer(_state):
        return {"proposed_rules": [], "error": "batch timeout", "rule_proposal_errors": [{"batch": 1}]}

    gate = AsyncMock(return_value={"metadata": {"rules_saved": 0}})
    monkeypatch.setattr("src.agents.nodes.rule_candidate_builder_node.rule_candidate_builder_node", candidates)
    monkeypatch.setattr("src.agents.nodes.prompt_customizer_node.prompt_customizer_node", prompts)
    monkeypatch.setattr("src.agents.nodes.rule_proposer_node.rule_proposer_node", failed_proposer)
    monkeypatch.setattr("src.agents.nodes.hitl_gate_node.hitl_gate_node", gate)

    graph = build_proposal_graph()
    final_state = await graph.ainvoke({
        "dataset_id": "uploaded-1",
        "semantic_contract": {"status": "confirmed", "tables": {}},
    })

    assert final_state["error"] == "batch timeout"
    gate.assert_not_awaited()


@pytest.mark.asyncio
async def test_proposal_graph_execution(monkeypatch, tmp_path):
    """Kiểm thử Run 1 (Proposal Graph) end-to-end với mock LLM."""
    run_id = f"graph_prop_{uuid.uuid4().hex[:8]}"
    create_run(run_id, "demo_graph_table")

    # Mock rule_proposer trả về 2 rules
    mock_rules = [
        {
            "rule_id": "demo_graph_table.fare.NOT_NULL",
            "table_name": "demo_graph_table",
            "column": "fare",
            "rule_type": "NOT_NULL",
            "parameters": {},
            "confidence_score": 1.0,
            "severity": "CRITICAL",
            "dimension": "COMPLETENESS",
            "rule_description": "Fare không được null",
            "ai_reasoning": "Yêu cầu nghiệp vụ",
            "status": "PENDING",
        }
    ]

    async def mock_rule_proposer(state):
        return {
            "proposed_rules": mock_rules,
            "rule_run_id": run_id,
            "rule_proposal_errors": [],
        }

    async def mock_dataset_understanding(state):
        return {
            "semantic_contract": {"dataset_id": state.get("dataset_id"), "tables": {}, "status": "confirmed"},
            "progress_state": "PROPOSING_RULES",
        }

    async def mock_prompt_customizer(state):
        return {"specialized_system_prompts": {}}

    async def mock_data_dict_gen(state):
        return {"normalized_data_dictionary": {"demo_graph_table": {}}, "data_dictionary_source": "inferred"}

    async def mock_rule_candidate_builder(state):
        return {"progress_state": "PROPOSING_RULES"}

    monkeypatch.setattr("src.agents.nodes.rule_proposer_node.rule_proposer_node", mock_rule_proposer)
    monkeypatch.setattr(
        "src.agents.nodes.dataset_understanding_node.dataset_understanding_node", mock_dataset_understanding
    )
    monkeypatch.setattr(
        "src.agents.nodes.data_dictionary_generator_node.data_dictionary_generator_node", mock_data_dict_gen
    )
    monkeypatch.setattr("src.agents.nodes.prompt_customizer_node.prompt_customizer_node", mock_prompt_customizer)
    monkeypatch.setattr(
        "src.agents.nodes.rule_candidate_builder_node.rule_candidate_builder_node", mock_rule_candidate_builder
    )

    graph = build_proposal_graph()
    initial_state = {
        "dataset_id": "demo_graph_table",
        "rule_run_id": run_id,
        "semantic_contract": {"status": "confirmed", "tables": {}},
        "metadata": {
            "connection_string": f"sqlite:///{tmp_path / 'test_graph.db'}",
            "sampling_rate": 1.0,
        },
    }

    final_state = await graph.ainvoke(initial_state)

    # Xác minh metadata từ hitl_gate_node
    meta = final_state.get("metadata", {})
    assert meta.get("hitl_status") == "AWAITING_REVIEW"
    assert meta.get("rules_saved") == 1

    # Kiểm tra DB đã lưu rule
    saved_run = get_run(run_id)
    assert saved_run is not None


@pytest.mark.asyncio
async def test_execution_graph_execution():
    """Kiểm thử Run 2 (Execution Graph) end-to-end trên Active Ruleset."""
    test_run_id = f"graph_exec_{uuid.uuid4().hex[:8]}"
    create_test_run(test_run_id, "demo_graph_table")

    rules = [
        {
            "rule_id": "demo_graph_table.fare.NOT_NULL",
            "dataset_id": "demo_graph_table",
            "table_name": "demo_graph_table",
            "column": "fare",
            "rule_type": "NOT_NULL",
            "parameters": {},
            "severity": "CRITICAL",
            "dimension": "COMPLETENESS",
            "rule_description": "Fare không được null",
            "status": "ACTIVE",
        },
        {
            "rule_id": "demo_graph_table._table.ROW_COUNT",
            "dataset_id": "demo_graph_table",
            "table_name": "demo_graph_table",
            "column": None,
            "rule_type": "ROW_COUNT",
            "parameters": {"min_row_count": 2},
            "severity": "MEDIUM",
            "dimension": "COMPLETENESS",
            "rule_description": "Bảng có >= 2 dòng",
            "status": "ACTIVE",
        },
    ]

    graph = build_execution_graph()
    initial_state = {
        "dataset_id": "demo_graph_table",
        "test_run_id": test_run_id,
        "approved_rules": rules,
    }

    final_state = await graph.ainvoke(initial_state)

    # 1. Kiểm tra kết quả test_results
    test_results = final_state.get("test_results", [])
    assert len(test_results) == 2

    res_map = {r["rule_id"]: r for r in test_results}
    # fare NOT_NULL có 1 dòng null trên 4 dòng -> FAIL
    assert res_map["demo_graph_table.fare.NOT_NULL"]["status"] == "FAIL"
    assert res_map["demo_graph_table.fare.NOT_NULL"]["failed_count"] == 1

    # ROW_COUNT có 4 dòng >= 2 -> PASS
    assert res_map["demo_graph_table._table.ROW_COUNT"]["status"] == "PASS"

    # 2. Kiểm tra test_run record trong DB
    db_run = get_test_run(test_run_id)
    assert db_run is not None
    assert db_run["status"] == "DONE"

    # 3. Kiểm tra test_results lưu trong DB
    db_results = get_test_results(test_run_id)
    assert len(db_results) == 2


@pytest.mark.asyncio
async def test_runners(monkeypatch, tmp_path):
    """Kiểm tra 2 hàm pipeline runner tiện ích: run_proposal_graph & run_execution_graph."""
    mock_rules = [
        {
            "rule_id": "demo_graph_table.status.ACCEPTED_VALUES",
            "table_name": "demo_graph_table",
            "column": "status",
            "rule_type": "ACCEPTED_VALUES",
            "parameters": {"accepted_values": ["OK", "ERROR"]},
            "confidence_score": 1.0,
            "severity": "HIGH",
            "dimension": "VALIDITY",
            "rule_description": "Trạng thái hợp lệ",
            "ai_reasoning": "Mẫu",
            "status": "PENDING",
        }
    ]

    async def mock_rule_proposer(state):
        return {
            "proposed_rules": mock_rules,
            "rule_run_id": state.get("rule_run_id"),
            "rule_proposal_errors": [],
        }

    async def mock_dataset_understanding(state):
        return {
            "semantic_contract": {
                "dataset_id": state.get("dataset_id"),
                "tables": {"demo_graph_table": {"columns": {"status": {"data_type": "string"}}}},
                "status": "confirmed",
            },
            "progress_state": "PROPOSING_RULES",
        }

    async def mock_data_dict_gen(state):
        return {"normalized_data_dictionary": {"demo_graph_table": {}}, "data_dictionary_source": "inferred"}

    async def mock_prompt_customizer(state):
        return {"specialized_system_prompts": {}}

    async def mock_anomaly_graph(**_kwargs):
        return {}

    monkeypatch.setattr("src.agents.nodes.rule_proposer_node.rule_proposer_node", mock_rule_proposer)
    monkeypatch.setattr(
        "src.agents.nodes.dataset_understanding_node.dataset_understanding_node", mock_dataset_understanding
    )
    monkeypatch.setattr(
        "src.agents.nodes.data_dictionary_generator_node.data_dictionary_generator_node", mock_data_dict_gen
    )
    monkeypatch.setattr("src.agents.nodes.prompt_customizer_node.prompt_customizer_node", mock_prompt_customizer)
    monkeypatch.setattr("src.agents.graph.run_anomaly_graph", mock_anomaly_graph)

    # 1. Chạy run_proposal_graph
    prop_res = await run_proposal_graph(
        dataset_id="demo_graph_table",
        connection_string=f"sqlite:///{tmp_path / 'test_graph.db'}",
    )
    prop_run_id = prop_res["run_id"]
    assert prop_run_id is not None
    assert len(prop_res["rules"]) >= 1

    # 2. Steward duyệt và publish
    rule_id_to_approve = prop_res["rules"][0]["rule_id"]
    review_rule(prop_run_id, rule_id_to_approve, "APPROVED")
    publish_approved_rules(prop_run_id)

    # 3. Chạy run_execution_graph
    exec_res = await run_execution_graph(dataset_id="demo_graph_table")
    assert exec_res["test_run_id"] is not None
    assert len(exec_res["results"]) >= 1
    assert exec_res["results"][0]["status"] in ("PASS", "FAIL")


# ---------------------------------------------------------------------------
# Catalog / builder agreement
#
# graph_catalog.py is a hand-written mirror of the builders. Nothing forces the
# two to stay aligned, and a stale catalog would silently draw a graph the
# backend no longer runs -- so assert the agreement here instead.
# ---------------------------------------------------------------------------
def _builder_node_names(compiled) -> set[str]:
    """Node names LangGraph actually compiled, minus its own bookkeeping nodes."""
    return {name for name in compiled.get_graph().nodes if name not in {"__start__", "__end__"}}


def test_catalog_matches_the_compiled_builders():
    from src.agents.graph_catalog import GRAPH_CATALOG

    builders = {
        "G1A": build_understanding_graph(),
        "G1B": build_rule_proposal_graph(),
        "G1_FULL": build_proposal_graph(),
        "G_DASHBOARD": build_dashboard_proposal_graph(),
        "G2": build_execution_graph(),
        "G3": build_anomaly_graph(investigation_mode="legacy"),
    }

    # Only graphs compiled from a langgraph builder can drift from one. The
    # catalog also describes G2_DIRECT, the plain-Python SQL runner behind
    # "Run approved rules", which has no compiled graph to compare against.
    langgraph_keys = {key for key, graph in GRAPH_CATALOG.items() if graph.get("langgraph", True)}
    assert langgraph_keys == set(builders)
    for key, compiled in builders.items():
        catalog_names = {node["name"] for node in GRAPH_CATALOG[key]["nodes"]}
        assert catalog_names == _builder_node_names(compiled), f"{key} catalog drifted from its builder"


def test_catalog_node_kinds_are_valid():
    from src.agents.graph_catalog import DETERMINISTIC, GATE, GRAPH_CATALOG, LLM

    for graph in GRAPH_CATALOG.values():
        for node in graph["nodes"]:
            assert node["kind"] in {LLM, DETERMINISTIC, GATE}
