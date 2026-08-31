"""Node telemetry: what it records, and what it must never record."""

from __future__ import annotations

import pytest

from src.services.node_telemetry import (
    MAX_STRING_LENGTH,
    instrument,
    start_graph_run,
    summarize,
)


class TestSummarizeRedaction:
    """`summarize` is the barrier that keeps source rows out of the database.

    The platform's headline privacy claim is that raw row values never leave the
    data tier.  Telemetry is the obvious place for that to leak by accident, so
    these are the tests that matter most in this module.
    """

    def test_record_lists_collapse_to_shape_not_content(self):
        rows = [
            {"vendor_id": 2, "passenger_count": 4, "total_amount": 71.25},
            {"vendor_id": 1, "passenger_count": 1, "total_amount": 9.80},
        ]
        summary = summarize({"source_rows": rows})

        assert summary["source_rows"] == {
            "type": "records",
            "count": 2,
            "fields": ["vendor_id", "passenger_count", "total_amount"],
        }
        # The field *names* are useful; the values are exactly what must not be here.
        assert "71.25" not in str(summary)
        assert "9.8" not in str(summary)

    def test_long_strings_are_truncated(self):
        prompt = "x" * 5000
        summary = summarize({"system_prompt": prompt})

        assert len(summary["system_prompt"]) < MAX_STRING_LENGTH + 40
        assert summary["system_prompt"].endswith("chars)")

    def test_deep_nesting_collapses(self):
        payload = {"a": {"b": {"c": {"d": {"e": "buried"}}}}}
        assert "buried" not in str(summarize(payload))

    def test_scalars_and_short_strings_survive(self):
        summary = summarize({"dataset_id": "yellow_tripdata", "rows": 50_000, "ok": True})

        assert summary["dataset_id"] == "yellow_tripdata"
        assert summary["rows"] == 50_000
        assert summary["ok"] is True

    def test_wide_dicts_are_capped(self):
        summary = summarize({f"column_{index}": index for index in range(200)})
        assert len(summary) <= 41  # MAX_KEYS plus the overflow marker


class TestInstrument:
    async def test_records_a_successful_async_node(self):
        start_graph_run(dataset_id="ds-1", workflow_run_id="wf-1")

        @instrument("G1A", "dataset_understanding", "LLM")
        async def node(state):
            return {"semantic_contract": {"tables": {}}}

        result = await node({"dataset_id": "ds-1"})

        assert result == {"semantic_contract": {"tables": {}}}
        rows = _node_runs("dataset_understanding")
        assert len(rows) == 1
        assert rows[0].status == "SUCCEEDED"
        assert rows[0].graph_key == "G1A"
        assert rows[0].dataset_id == "ds-1"
        assert rows[0].workflow_run_id == "wf-1"

    async def test_supports_sync_nodes(self):
        """rule_candidate_builder is a plain function, not a coroutine."""
        start_graph_run(dataset_id="ds-2")

        @instrument("G1B", "rule_candidate_builder", "DETERMINISTIC")
        def node(state):
            return {"rule_candidates": [1, 2, 3]}

        assert await node({}) == {"rule_candidates": [1, 2, 3]}
        assert _node_runs("rule_candidate_builder")[0].status == "SUCCEEDED"

    async def test_raised_exception_is_recorded_and_re_raised(self):
        start_graph_run(dataset_id="ds-3")

        @instrument("G3", "hypothesis_agent", "LLM")
        async def node(state):
            raise RuntimeError("Install deepagents before running anomaly investigation")

        with pytest.raises(RuntimeError):
            await node({})

        row = _node_runs("hypothesis_agent")[0]
        assert row.status == "FAILED"
        assert "deepagents" in row.error_message

    async def test_error_key_counts_as_failure(self):
        """The graphs signal failure by returning an ``error`` key, not by raising."""
        start_graph_run(dataset_id="ds-4")

        @instrument("G2", "validate_dbt_project", "DETERMINISTIC")
        async def node(state):
            return {"error": "dbt project validation failed"}

        await node({})

        row = _node_runs("validate_dbt_project")[0]
        assert row.status == "FAILED"
        assert "validation failed" in row.error_message

    async def test_nodes_of_one_run_share_a_graph_run_id_and_order(self):
        start_graph_run(dataset_id="ds-5")

        @instrument("G1B", "rule_candidate_builder", "DETERMINISTIC")
        async def first(state):
            return {}

        @instrument("G1B", "rule_proposer", "LLM")
        async def second(state):
            return {}

        await first({})
        await second({})

        rows = _node_runs("rule_candidate_builder") + _node_runs("rule_proposer")
        assert len({row.graph_run_id for row in rows}) == 1
        assert sorted(row.sequence for row in rows) == [1, 2]


def _node_runs(node_name: str):
    """Read rows back through the same engine the telemetry writer uses."""
    from sqlalchemy.orm import Session

    from src.models.database import GraphNodeRunModel
    from src.services.rule_store import get_engine

    with Session(get_engine()) as db:
        return db.query(GraphNodeRunModel).filter_by(node_name=node_name).all()
