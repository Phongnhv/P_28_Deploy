import json
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.orm import Session

from src.models.database import (
    AnalysisNodeExecutionModel,
    AnalysisRunModel,
    AnomalyRunModel,
    AnomalySignalModel,
    ColumnProfileModel,
    DatasetAccessModel,
    DatasetModel,
    DqRunModel,
    Graph1NodeExecutionModel,
    JobModel,
    ProfileModel,
    RuleProposalModel,
    RuleVersionModel,
)
from src.services.analysis_workflow import build_analysis_result, create_analysis_run, execute_analysis_run
from src.services.graph1_workflow import create_graph1_run, review_rules
from src.services.rule_store import (
    ProposedRuleModel,
    create_test_run,
    get_engine,
    save_proposed_rules,
    save_test_results,
)
from src.time_utils import utc_now

RULE = {
    "rule_id": "source_rows.fare_amount.range",
    "rule_name": "Fare amount is in range",
    "rule_description": "Fare amount must remain in the accepted range.",
    "table_name": "source_rows",
    "column": "fare_amount",
    "rule_type": "numeric_range",
    "parameters": {"min": 0, "max": 500},
    "severity": "HIGH",
    "dimension": "VALIDITY",
    "confidence_score": 0.92,
    "selected_evidence_refs": ["profile:fare_amount"],
}


def _ready_dataset(db: Session) -> None:
    db.add(DatasetModel(
        id="analysis-dataset",
        name="Analysis dataset",
        description="test",
        status="PROFILE_READY",
        row_count=3,
        source_label="analysis.csv",
        manifest_version="v1",
        checksum="analysis-checksum",
        updated_at=utc_now(),
    ))
    db.add(ProfileModel(
        dataset_id="analysis-dataset",
        row_count=3,
        completeness_score=100,
        validity_score=100,
        duplicate_rate=0,
        cross_field_metrics_json="[]",
        evidence_keys="[]",
        generated_at=utc_now(),
    ))
    db.add(ColumnProfileModel(
        profile_dataset_id="analysis-dataset",
        name="fare_amount",
        data_type="float",
        null_rate=0,
        distinct_count=3,
        non_null_count=3,
        negative_rate=0,
        quantiles_json="{}",
        full_distinct_count=3,
        uniqueness_rate=1,
        is_unique_full_table=True,
        min_value=1,
        max_value=100,
        sample_value="12.5",
    ))
    db.commit()


def _completed_graph1(db: Session, suffix: str = "base"):
    run = create_graph1_run(db, "analysis-dataset", "steward", f"graph1-{suffix}")
    save_proposed_rules(run.id, run.dataset_id, [RULE])
    db.refresh(run)
    state = json.loads(run.state_json)
    state["proposed_rules"] = [RULE]
    run.state_json = json.dumps(state)
    run.status = "AWAITING_RULE_REVIEW"
    db.commit()
    review_rules(db, run, [{
        "rule_id": RULE["rule_id"],
        "action": "edit",
        "rule": {
            "type": "numeric_range",
            "rule_name": "Fare amount approved",
            "rule_description": RULE["rule_description"],
            "column": "fare_amount",
            "parameters": {"min_value": 1, "max_value": 450, "operator": "between"},
        },
    }], "steward")
    db.refresh(run)
    return run


def test_graph1_review_synchronizes_both_rule_stores_and_node9(test_db):
    with Session(test_db) as db:
        _ready_dataset(db)
        run = _completed_graph1(db, "handoff")
        proposal = db.get(RuleProposalModel, RULE["rule_id"])
        legacy = db.get(ProposedRuleModel, (run.id, RULE["rule_id"]))
        version = db.get(RuleVersionModel, f"rv_{RULE['rule_id']}")
        gate = db.get(Graph1NodeExecutionModel, f"{run.id}:hitl_gate")

        assert run.status == "COMPLETED"
        assert proposal and proposal.status == "APPROVED"
        assert legacy and legacy.status == "APPROVED" and legacy.reviewer == "steward"
        assert json.loads(legacy.edited_parameters) == {"min": 1, "max": 450, "operator": "between"}
        assert version and version.status == "APPROVED"
        output = json.loads(gate.output_json)
        assert output["total_count"] == output["approved_count"] == output["edited_count"] == 1
        assert len(output["proposed_rules"]) == len(output["approved_rules"]) == 1


def test_analysis_run_is_idempotent_and_initializes_observable_nodes(test_db):
    # Keep this path representative of PostgreSQL, where FK checks are always
    # active. SQLite leaves them disabled by default, which previously masked
    # inserting analysis child nodes before their parent run row was flushed.
    with Session(test_db) as db:
        db.connection().exec_driver_sql("PRAGMA foreign_keys=ON")
        assert db.connection().exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        _ready_dataset(db)
        graph1 = _completed_graph1(db, "idempotent")
        legacy_gate = db.get(Graph1NodeExecutionModel, f"{graph1.id}:hitl_gate")
        legacy_gate.output_json = json.dumps({"approved_count": 1, "total_count": 0})
        db.commit()
        first, created = create_analysis_run(db, graph1, "steward", "analysis-key")
        second, created_again = create_analysis_run(db, graph1, "steward", "another-key")

        assert created is True
        assert created_again is False
        assert second.id == first.id
        nodes = db.query(AnalysisNodeExecutionModel).filter_by(run_id=first.id).order_by(AnalysisNodeExecutionModel.position).all()
        assert len(nodes) == 10
        assert nodes[0].node_key == "prepare_approved_rules"
        assert nodes[-1].node_key == "report_writer"
        repaired_gate = json.loads(legacy_gate.output_json)
        assert repaired_gate["total_count"] == repaired_gate["approved_count"] == 1


def test_graph3_rule_signal_threshold_projects_to_graph2_row(test_db):
    with Session(test_db) as db:
        _ready_dataset(db)
        graph1 = _completed_graph1(db, "projection")
        analysis, _ = create_analysis_run(db, graph1, "steward", "projection-key")
        create_test_run("test-projection", graph1.dataset_id)
        save_test_results("test-projection", [{
            "rule_id": RULE["rule_id"],
            "table_name": "source_rows",
            "column": "fare_amount",
            "rule_type": "numeric_range",
            "status": "FAILED",
            "violation_count": 2,
            "total_rows": 10,
            "violation_rate": 0.2,
            "sample_failures": ["row-2", "row-8"],
            "duration_ms": 14.5,
        }])
        db.add(JobModel(id="job-projection", type="RUN_DQ", status="SUCCEEDED", progress=100,
                        idempotency_key="job-projection", linked_entity=graph1.dataset_id))
        db.add(DqRunModel(id="test-projection", job_id="job-projection", dataset_id=graph1.dataset_id,
                          rule_ids=json.dumps([RULE["rule_id"]]), status="SUCCEEDED", total_failed=2,
                          total_checked=10))
        db.add(AnomalyRunModel(id="anomaly-projection", execution_run_id="test-projection", status="SUCCEEDED",
                               decision="ANOMALY", score=.8, confidence=.8, severity="HIGH"))
        signal = AnomalySignalModel(
            id="signal-projection",
            anomaly_run_id="anomaly-projection",
            family="RULE_FAILURE_RATE",
            target_type="RULE",
            target_id=RULE["rule_id"],
            score=.69,
            reliability=.9,
            observed_value="0.2",
            baseline=json.dumps({"median": .01}),
            sufficient_history=True,
            detector_name="ROBUST_Z_SCORE",
            detector_version="1",
            explanation_code="Rule failure rate increased.",
            evidence_refs=json.dumps(["test-projection"]),
        )
        db.add(signal)
        analysis.test_run_id = "test-projection"
        analysis.anomaly_run_id = "anomaly-projection"
        db.commit()

        below_threshold = build_analysis_result(db, analysis)
        assert below_threshold["graph2"]["results"][0]["anomaly"]["flagged"] is False
        signal.score = .70
        db.commit()
        at_threshold = build_analysis_result(db, analysis)
        assert at_threshold["graph2"]["results"][0]["anomaly"]["flagged"] is True
        assert at_threshold["graph2"]["results"][0]["sample_row_ids"] == ["row-2", "row-8"]


@pytest.mark.asyncio
async def test_analysis_orchestrator_completes_and_records_node_telemetry(test_db, monkeypatch):
    with Session(test_db) as db:
        _ready_dataset(db)
        graph1 = _completed_graph1(db, "orchestrator-success")
        analysis, _ = create_analysis_run(db, graph1, "steward", "orchestrator-success-key")
        analysis_id = analysis.id

    class FakeExecutionGraph:
        def __init__(self, observer):
            self.observer = observer

        async def ainvoke(self, state):
            outputs = {
                "test_generator": {"generated_tests": [{"rule_id": RULE["rule_id"]}]},
                "validate_dbt_project": {"dbt_validation_valid": True, "dbt_validation_attempts": 1},
                "test_runner": {"test_results": [], "metadata": {"dbt_execution_mode": "sql_fallback"}},
                "persist_report": {"metadata": {"test_run_status": "DONE"}},
            }
            for key, output in outputs.items():
                await self.observer("GRAPH2", key, None, None)
                await self.observer("GRAPH2", key, output, None)
            return {**state, "dbt_validation_valid": True}

    class FakeAnomalyGraph:
        def __init__(self, observer):
            self.observer = observer

        async def ainvoke(self, state):
            outputs = {
                "anomaly_detector": {"anomaly_decision": {"decision": "NORMAL"}, "signal_observations": []},
                "hypothesis_agent": {"hypotheses": [], "hypothesis_status": "NOT_REQUIRED"},
                "persist_analysis": {"anomaly_run_id": state["anomaly_run_id"]},
                "report_writer": {"steward_report_markdown": "# Báo cáo", "report_source": "LLM"},
            }
            for key, output in outputs.items():
                await self.observer("GRAPH3", key, None, None)
                await self.observer("GRAPH3", key, output, None)
            return {**state, "steward_report_markdown": "# Báo cáo", "report_source": "LLM"}

    monkeypatch.setattr("src.agents.graph.build_execution_graph", lambda observer=None: FakeExecutionGraph(observer))
    monkeypatch.setattr("src.agents.graph.build_anomaly_graph", lambda observer=None: FakeAnomalyGraph(observer))
    await execute_analysis_run(analysis_id)

    with Session(test_db) as db:
        completed = db.get(AnalysisRunModel, analysis_id)
        nodes = db.query(AnalysisNodeExecutionModel).filter_by(run_id=analysis_id).all()
        assert completed.status == "COMPLETED"
        assert completed.report_markdown == "# Báo cáo" and completed.report_source == "LLM"
        assert next(node for node in nodes if node.node_key == "dbt_validation_failed").status == "SKIPPED"
        assert all(node.status == "SUCCEEDED" for node in nodes if node.node_key != "dbt_validation_failed")


@pytest.mark.asyncio
async def test_analysis_orchestrator_keeps_graph2_when_graph3_fails(test_db, monkeypatch):
    with Session(test_db) as db:
        _ready_dataset(db)
        graph1 = _completed_graph1(db, "orchestrator-partial")
        analysis, _ = create_analysis_run(db, graph1, "steward", "orchestrator-partial-key")
        analysis_id = analysis.id

    class FakeExecutionGraph:
        def __init__(self, observer):
            self.observer = observer

        async def ainvoke(self, state):
            for key in ("test_generator", "validate_dbt_project", "test_runner", "persist_report"):
                output = {"dbt_validation_valid": True} if key == "validate_dbt_project" else {}
                await self.observer("GRAPH2", key, None, None)
                await self.observer("GRAPH2", key, output, None)
            return {**state, "dbt_validation_valid": True}

    class FailedAnomalyGraph:
        def __init__(self, observer):
            self.observer = observer

        async def ainvoke(self, _state):
            error = RuntimeError("detector unavailable")
            await self.observer("GRAPH3", "anomaly_detector", None, None)
            await self.observer("GRAPH3", "anomaly_detector", None, error)
            raise error

    monkeypatch.setattr("src.agents.graph.build_execution_graph", lambda observer=None: FakeExecutionGraph(observer))
    monkeypatch.setattr("src.agents.graph.build_anomaly_graph", lambda observer=None: FailedAnomalyGraph(observer))
    monkeypatch.setattr("src.services.report_renderer.render_steward_report_vi", lambda *_args: "# Fallback report")
    monkeypatch.setattr("src.agents.nodes.report_writer_node._write_report_file", lambda *_args: "C:/internal/fallback.md")
    await execute_analysis_run(analysis_id)

    with Session(test_db) as db:
        partial = db.get(AnalysisRunModel, analysis_id)
        nodes = {node.node_key: node for node in db.query(AnalysisNodeExecutionModel).filter_by(run_id=analysis_id).all()}
        assert partial.status == "PARTIAL"
        assert partial.report_markdown == "# Fallback report" and partial.report_source == "FALLBACK"
        assert nodes["persist_report"].status == "SUCCEEDED"
        assert nodes["anomaly_detector"].status == "FAILED"
        assert nodes["hypothesis_agent"].status == "SKIPPED"


@pytest.mark.asyncio
async def test_analysis_api_requires_csrf_and_reuses_existing_run(client, monkeypatch):
    monkeypatch.setattr("src.services.analysis_workflow.execute_analysis_run", AsyncMock())
    with Session(get_engine()) as db:
        _ready_dataset(db)
        graph1 = _completed_graph1(db, "api")
        graph1_id = graph1.id
        db.add(DatasetAccessModel(id="analysis-access", dataset_id=graph1.dataset_id, username="steward",
                                  access_level="MANAGE", granted_by="admin"))
        db.commit()

    login = await client.post("/api/v1/session", json={"username": "steward", "password": "steward"})
    csrf = login.json()["csrf_token"]
    url = f"/api/v1/graph1-runs/{graph1_id}/analysis-runs"
    without_csrf = await client.post(url, headers={"Idempotency-Key": "analysis-api-key"})
    assert without_csrf.status_code == 422

    headers = {"X-CSRF-Token": csrf, "Idempotency-Key": "analysis-api-key"}
    first = await client.post(url, headers=headers)
    second = await client.post(url, headers=headers)
    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]

    with Session(get_engine()) as db:
        stored = db.get(AnalysisRunModel, first.json()["id"])
        stored.report_markdown = "# Steward report"
        stored.report_source = "FALLBACK"
        stored.report_path = "C:/private/output/reports/steward.md"
        db.commit()

    nodes = await client.get(f"/api/v1/analysis-runs/{first.json()['id']}/nodes")
    result = await client.get(f"/api/v1/analysis-runs/{first.json()['id']}/result")
    assert nodes.status_code == 200 and len(nodes.json()) == 10
    assert result.status_code == 200
    assert result.json()["report"]["file_name"] == "steward.md"
    assert "private" not in json.dumps(result.json()["report"])
