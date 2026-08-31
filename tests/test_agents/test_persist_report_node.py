"""Regression tests cho persist_report_node (Run 2 terminal node)."""

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from src.agents.nodes.persist_report_node import persist_report_node
from src.models.database import DqRunModel
from src.services.rule_store import create_test_run, get_engine


def _normalized_results() -> list[dict]:
    """Đúng định dạng mà test_runner_node trả về sau bước normalize (PASS/FAIL)."""
    return [
        {
            "rule_id": "mock_trips.fare_amount.NOT_NULL",
            "rule_version": "rule-v1",
            "table_name": "mock_trips",
            "column": "fare_amount",
            "status": "PASS",
            "checked_count": 100,
            "failed_count": 0,
            "violation_rate": 0.0,
            "severity": "CRITICAL",
            "dimension": "COMPLETENESS",
            "duration_ms": 1.0,
            "dbt_status": "NOT_RUN",
            "metrics_status": "PASS",
            "sample_refs": [],
            "error": None,
            "evidence_refs": [],
        },
        {
            "rule_id": "mock_trips.fare_amount.RANGE",
            "rule_version": "rule-v1",
            "table_name": "mock_trips",
            "column": "fare_amount",
            "status": "FAIL",
            "checked_count": 100,
            "failed_count": 9,
            "violation_rate": 0.09,
            "severity": "HIGH",
            "dimension": "VALIDITY",
            "duration_ms": 2.0,
            "dbt_status": "NOT_RUN",
            "metrics_status": "FAIL",
            "sample_refs": ["row-001"],
            "error": None,
            "evidence_refs": [],
        },
    ]


@pytest.mark.asyncio
async def test_report_counts_normalized_statuses():
    """Regression: report phải đếm đúng PASS/FAIL đã normalize, không phải PASSED/FAILED."""
    test_run_id = f"persist_{uuid.uuid4().hex[:8]}"
    create_test_run(test_run_id, "mock_trips")

    out = await persist_report_node(
        {
            "test_run_id": test_run_id,
            "dataset_id": "mock_trips",
            "test_results": _normalized_results(),
        }
    )

    report_path = Path(out["metadata"]["report_file_path"])
    payload = json.loads(report_path.read_text(encoding="utf-8"))

    assert payload["total_rules_tested"] == 2
    assert payload["passed_count"] == 1, f"passed_count sai: {payload['passed_count']}"
    assert payload["failed_count"] == 1, f"failed_count sai: {payload['failed_count']}"
    assert payload["error_count"] == 0


@pytest.mark.asyncio
async def test_dq_run_timestamps_use_naive_utc():
    """Regression: dq_runs phải ghi naive-UTC theo hợp đồng của src/time_utils.py."""
    from src.time_utils import utc_now

    test_run_id = f"persist_{uuid.uuid4().hex[:8]}"
    create_test_run(test_run_id, "mock_trips")

    before = utc_now()
    await persist_report_node(
        {
            "test_run_id": test_run_id,
            "dataset_id": "mock_trips",
            "test_results": _normalized_results(),
        }
    )
    after = utc_now()

    with Session(get_engine()) as session:
        run = session.get(DqRunModel, test_run_id)
        assert run is not None
        assert run.created_at.tzinfo is None, "Hợp đồng DB là naive datetime"
        assert before <= run.created_at <= after, (
            f"created_at={run.created_at} nằm ngoài [{before}, {after}] — nhiều khả năng là giờ địa phương"
        )
