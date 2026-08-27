import json
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.orm import Session

from src.config import get_settings
from src.models.database import (
    ColumnProfileModel,
    DatasetAccessModel,
    DatasetModel,
    Graph1NodeExecutionModel,
    Graph1RunModel,
    ProfileModel,
)
from src.services.graph1_workflow import (
    GRAPH1_NODES,
    confirm_semantic_review,
    create_graph1_run,
    execute_graph1_run,
    list_nodes,
)
from src.services.rule_store import get_engine
from src.time_utils import utc_now


def _ready_dataset(db: Session) -> None:
    db.add(DatasetModel(id="uploaded-1", name="Uploaded", description="test", status="PROFILE_READY", row_count=3,
                        source_label="new.csv", manifest_version="v1", checksum="abc", updated_at=utc_now()))
    db.add(ProfileModel(dataset_id="uploaded-1", row_count=3, completeness_score=100, validity_score=100,
                        duplicate_rate=0, cross_field_metrics_json="[]", evidence_keys="[]", generated_at=utc_now()))
    db.add(ColumnProfileModel(profile_dataset_id="uploaded-1", name="ride_id", data_type="string", null_rate=0,
                              distinct_count=3, non_null_count=3, negative_rate=None, quantiles_json="{}",
                              full_distinct_count=3, uniqueness_rate=1, is_unique_full_table=True,
                              min_value=None, max_value=None, sample_value="r1"))
    db.commit()


def test_create_graph1_run_initializes_nine_real_nodes(test_db):
    with Session(test_db) as db:
        _ready_dataset(db)
        run = create_graph1_run(db, "uploaded-1", "steward", "idem-1")
        nodes = list_nodes(db, run.id)
        assert [node["node_key"] for node in nodes] == GRAPH1_NODES
        assert all(node["status"] == "PENDING" for node in nodes)
        state = json.loads(run.state_json)
        assert state["metadata"]["uploaded_dataset_profile"]["source_rows"]["table_metadata"]["total_rows"] == 3


def test_semantic_review_is_persisted_and_resumable(test_db):
    with Session(test_db) as db:
        _ready_dataset(db)
        run = create_graph1_run(db, "uploaded-1", "steward", "idem-2")
        run.status = "AWAITING_SEMANTIC_REVIEW"
        db.commit()
        contract = {"status": "draft", "tables": {"source_rows": {"table_name": "source_rows", "columns": []}}}
        confirm_semantic_review(db, run, contract)
        db.refresh(run)
        gate = db.get(Graph1NodeExecutionModel, f"{run.id}:hitl_semantic_gate")
        assert run.status == "PENDING"
        assert json.loads(run.state_json)["semantic_contract"]["status"] == "confirmed"
        assert gate and gate.status == "SUCCEEDED"


@pytest.mark.asyncio
async def test_graph1_api_creates_real_run_without_fixture(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_mode", "graph")
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr("src.services.graph1_workflow.execute_graph1_run", AsyncMock())
    with Session(get_engine()) as db:
        _ready_dataset(db)
        db.add(DatasetAccessModel(id="g1-access", dataset_id="uploaded-1", username="steward",
                                  access_level="MANAGE", granted_by="admin"))
        db.commit()
    login = await client.post("/api/v1/session", json={"username": "steward", "password": "steward"})
    csrf = login.json()["csrf_token"]
    created = await client.post("/api/v1/datasets/uploaded-1/graph1-runs",
                                headers={"X-CSRF-Token": csrf, "Idempotency-Key": "api-g1-1"})
    assert created.status_code == 202
    run_id = created.json()["id"]
    nodes = await client.get(f"/api/v1/graph1-runs/{run_id}/nodes")
    assert nodes.status_code == 200
    assert len(nodes.json()) == 9


@pytest.mark.asyncio
async def test_rule_proposer_failure_marks_hitl_gate_skipped(test_db, monkeypatch):
    with Session(test_db) as db:
        _ready_dataset(db)
        run = create_graph1_run(db, "uploaded-1", "steward", "idem-node8-fail")
        run_id = run.id

    class FailedRuleGraph:
        async def astream(self, _state, stream_mode):
            assert stream_mode == "updates"
            yield {"rule_proposer": {
                "proposed_rules": [],
                "rule_proposal_errors": [{"table": "source_rows", "batch": 1, "error": "timeout"}],
                "error": "Rule proposer failed closed",
            }}

    monkeypatch.setattr("src.agents.graph.build_proposal_graph", lambda: FailedRuleGraph())
    await execute_graph1_run(run_id)

    with Session(test_db) as db:
        run = db.get(Graph1RunModel, run_id)
        proposer = db.get(Graph1NodeExecutionModel, f"{run_id}:rule_proposer")
        gate = db.get(Graph1NodeExecutionModel, f"{run_id}:hitl_gate")
        assert run and run.status == "FAILED" and run.current_node == "rule_proposer"
        assert proposer and proposer.status == "FAILED"
        assert gate and gate.status == "SKIPPED"
        assert json.loads(gate.output_json)["blocked_by"] == "rule_proposer"
