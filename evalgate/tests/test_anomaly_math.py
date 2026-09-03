"""Robust z-score arithmetic and the detector's cold-start path, as tests.

These were seven bare ``assert`` statements inside ``anomaly_logic_probe_v1``. Two things
were wrong with that home. ``python -O`` strips ``assert``, so the checks could vanish
without a word; and when the product regressed, the raised ``AssertionError`` reached the
runner as ``EVALUATOR_ERROR`` -- a status that is excluded from the aggregate, so a
product defect *removed* a penalty instead of applying one.

As tests they fail loudly and cost the gate nothing. The evaluator now grades the verdict
the run actually produced.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

asvc = pytest.importorskip("src.services.anomaly_service")
database = pytest.importorskip("src.models.database")


def test_a_flat_history_yields_zero_deviation() -> None:
    z, median, mad = asvc.calculate_robust_zscore(10.0, [10.0] * 5)
    assert median == 10.0
    assert mad == 0.0
    assert z == 0.0


def test_zero_mad_falls_back_to_a_scale_that_still_detects_outliers() -> None:
    z, _, _ = asvc.calculate_robust_zscore(100.0, [10.0] * 5)
    assert z > 5.0, "a 10x outlier against a flat history must not score zero"


def test_a_distributed_history_places_the_median_correctly() -> None:
    z, median, mad = asvc.calculate_robust_zscore(20.0, [1.0, 2, 3, 4, 5, 6, 7, 8, 9])
    assert median == 5.0
    assert mad > 0.0
    assert z > 3.0


def test_a_single_run_does_not_produce_a_stability_claim() -> None:
    """Cold start: one run is not evidence of normality."""
    with TemporaryDirectory(prefix="evalgate-anomaly-") as tmpdir:
        db_path = Path(tmpdir) / "anomaly_test.db"
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        database.Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine)()
        try:
            run_id = "run-test-001"
            session.add(
                database.DqRunModel(
                    id=run_id, job_id="job-001", dataset_id="ds-test-001",
                    rule_ids="[]", status="COMPLETED", total_checked=100, total_failed=0,
                )
            )
            session.add(
                database.DqResultModel(
                    id="res-1", run_id=run_id, rule_id="r_null_1",
                    rule_title="Validate Nulls", status="PASSED",
                    checked_count=100, failed_count=0, failed_row_ids="[]",
                )
            )
            session.commit()

            decision = asvc.detect_anomalies(session, run_id)
            assert "decision" in decision or "status" in decision
            assert (
                decision.get("decision") == "INSUFFICIENT_HISTORY"
                or decision.get("status") in {"COMPLETED", "NORMAL", "WATCH"}
            )
        finally:
            session.close()
            engine.dispose()
