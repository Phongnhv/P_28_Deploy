"""Integration tests for the graph observability routes.

These four endpoints are the only way node-level detail leaves the backend, so
the tests cover both halves of that contract: the static topology the UI draws
from, and the telemetry it overlays on top.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from src.models.database import GraphNodeRunModel
from src.services.rule_store import get_engine
from src.time_utils import utc_now


def _seed_node_run(**overrides) -> str:
    row_id = overrides.get("id", "nr-test-1")
    row = GraphNodeRunModel(
        id=row_id,
        graph_run_id=overrides.get("graph_run_id", "gr-test-1"),
        graph_key=overrides.get("graph_key", "G1A"),
        node_name=overrides.get("node_name", "dataset_understanding"),
        node_kind=overrides.get("node_kind", "LLM"),
        sequence=overrides.get("sequence", 1),
        status=overrides.get("status", "SUCCEEDED"),
        started_at=utc_now(),
        completed_at=utc_now(),
        duration_ms=overrides.get("duration_ms", 4200),
        input_summary_json=overrides.get("input_summary_json", '{"dataset_id": "ds-1"}'),
        output_summary_json=overrides.get("output_summary_json", '{"columns": {"type": "list", "count": 18}}'),
        model_name=overrides.get("model_name", "gpt-4o-mini"),
        workflow_run_id=overrides.get("workflow_run_id", "wf-1"),
        dataset_id=overrides.get("dataset_id", "ds-1"),
        dq_run_id=overrides.get("dq_run_id"),
        anomaly_run_id=overrides.get("anomaly_run_id"),
    )
    with Session(get_engine()) as db:
        db.add(row)
        db.commit()
    return row_id


class TestGraphCatalog:
    async def test_returns_every_graph_and_its_nodes(self, steward_client):
        response = await steward_client.get("/api/v1/graph/catalog")
        assert response.status_code == 200

        body = response.json()
        keys = {graph["key"] for graph in body["graphs"]}
        # The six langgraph builders in src/agents/graph.py plus G2_DIRECT, the
        # bounded SQL runner that "Run approved rules" actually executes. The UI
        # needs every execution path here, not only the compiled graphs.
        assert keys == {"G1A", "G1B", "G1_FULL", "G_DASHBOARD", "G2", "G2_DIRECT", "G3"}
        assert body["totals"]["graphs"] == 7

    async def test_graph_1a_matches_the_documented_three_nodes(self, steward_client):
        body = (await steward_client.get("/api/v1/graph/catalog")).json()
        g1a = next(graph for graph in body["graphs"] if graph["key"] == "G1A")

        assert [node["name"] for node in g1a["nodes"]] == [
            "build_profile_digest",
            "data_dictionary_generator",
            "dataset_understanding",
        ]

    async def test_conditional_edges_are_described(self, steward_client):
        """The fail-closed branch is the whole point of Graph 2's safety story."""
        body = (await steward_client.get("/api/v1/graph/catalog")).json()
        g2 = next(graph for graph in body["graphs"] if graph["key"] == "G2")

        failure_edge = next(
            edge for edge in g2["edges"] if edge["to"] == "dbt_validation_failed"
        )
        assert failure_edge["condition"] == "invalid"

    async def test_requires_a_session(self, client):
        assert (await client.get("/api/v1/graph/catalog")).status_code == 401


class TestNodeRuns:
    async def test_lists_seeded_runs(self, steward_client):
        _seed_node_run()
        response = await steward_client.get("/api/v1/graph/node-runs")

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["node_name"] == "dataset_understanding"
        assert body[0]["duration_ms"] == 4200

    async def test_filters_by_workflow_run(self, steward_client):
        _seed_node_run(id="nr-a", workflow_run_id="wf-a")
        _seed_node_run(id="nr-b", workflow_run_id="wf-b")

        body = (await steward_client.get("/api/v1/graph/node-runs?workflow_run_id=wf-a")).json()
        assert [row["id"] for row in body] == ["nr-a"]

    async def test_filters_by_graph_key(self, steward_client):
        _seed_node_run(id="nr-1a", graph_key="G1A")
        _seed_node_run(id="nr-2", graph_key="G2", node_name="test_runner", node_kind="DETERMINISTIC")

        body = (await steward_client.get("/api/v1/graph/node-runs?graph_key=G2")).json()
        assert [row["id"] for row in body] == ["nr-2"]

    async def test_list_omits_payload_summaries(self, steward_client):
        """Lists stay small; summaries are fetched per node on demand."""
        _seed_node_run()
        body = (await steward_client.get("/api/v1/graph/node-runs")).json()
        assert "input_summary" not in body[0]

    async def test_detail_includes_payload_summaries(self, steward_client):
        run_id = _seed_node_run()
        body = (await steward_client.get(f"/api/v1/graph/node-runs/{run_id}")).json()

        assert body["input_summary"] == {"dataset_id": "ds-1"}
        assert body["output_summary"]["columns"]["count"] == 18

    async def test_detail_404_for_unknown_id(self, steward_client):
        response = await steward_client.get("/api/v1/graph/node-runs/does-not-exist")
        assert response.status_code == 404

    async def test_requires_a_session(self, client):
        assert (await client.get("/api/v1/graph/node-runs")).status_code == 401


class TestStewardReport:
    async def test_404_when_no_report_was_written(self, steward_client):
        response = await steward_client.get("/api/v1/dq-runs/run-with-no-report/steward-report")
        assert response.status_code == 404

    async def test_serves_the_markdown_written_by_report_writer(self, steward_client, isolate_output_dir):
        from pathlib import Path

        from src.config import get_settings

        report_dir = Path(get_settings().output_dir) / "steward_reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "steward_report_20260828_120000_dq-77.md").write_text(
            "# Steward report\n\nVolume dropped 41%.\n", encoding="utf-8"
        )

        response = await steward_client.get("/api/v1/dq-runs/dq-77/steward-report")

        assert response.status_code == 200
        body = response.json()
        assert body["run_id"] == "dq-77"
        assert "Volume dropped 41%" in body["content"]
