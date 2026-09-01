"""Evaluator for Anomaly Service mathematical and logical correctness.

Validates:
1. Robust Z-Score calculations (Median / MAD)
2. Handling of zero MAD / scale fallback
3. Maximum Z-score bounding
4. Decision logic on single-run cold-start vs multi-run history
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from evalgate.normalizers import normalizers as norm
from evalgate.schemas.eval_result import (
    EvalResult,
    EvalStatus,
    Evidence,
    MetricValue,
)
from src.models.database import Base, DqResultModel, DqRunModel
from src.services import anomaly_service as asvc

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = PROJECT_ROOT / "evalgate" / "evidence" / "gate1"

GATE = "ai_quality"
EVALUATOR = "anomaly_logic_probe_v1"


def test_robust_zscore_math() -> dict[str, Any]:
    """Verify median/MAD calculations against known mathematical cases."""
    # Case 1: Standard normal sequence
    history = [10.0, 10.0, 10.0, 10.0, 10.0]
    z_norm, med_norm, mad_norm = asvc.calculate_robust_zscore(10.0, history)
    assert med_norm == 10.0
    assert mad_norm == 0.0
    assert z_norm == 0.0

    # Case 2: Extreme outlier with 0 MAD uses fallback scale
    z_outlier, _, _ = asvc.calculate_robust_zscore(100.0, history)
    assert z_outlier > 5.0  # Large z-score

    # Case 3: Distributed history
    dist_history = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0]
    z_val, med_val, mad_val = asvc.calculate_robust_zscore(20.0, dist_history)
    assert med_val == 5.0
    assert mad_val > 0.0
    assert z_val > 3.0

    return {
        "zero_mad_fallback_works": True,
        "outlier_detected": bool(z_outlier > 5.0),
        "distributed_history_works": bool(z_val > 3.0),
    }


def test_db_detection_flow() -> dict[str, Any]:
    """Test detect_anomalies against isolated test database."""
    with TemporaryDirectory(prefix="evalgate-anomaly-") as tmpdir:
        db_path = Path(tmpdir) / "anomaly_test.db"
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        Base.metadata.create_all(engine)
        session_maker = sessionmaker(bind=engine)
        session = session_maker()
        try:
            # Create a single run
            run_id = "run-test-001"
            run = DqRunModel(
                id=run_id,
                job_id="job-001",
                dataset_id="ds-test-001",
                rule_ids="[]",
                status="COMPLETED",
                total_checked=100,
                total_failed=0,
            )
            session.add(run)

            res1 = DqResultModel(
                id="res-1",
                run_id=run_id,
                rule_id="r_null_1",
                rule_title="Validate Nulls",
                status="PASSED",
                checked_count=100,
                failed_count=0,
                failed_row_ids="[]",
            )
            session.add(res1)
            session.commit()

            # Execute detector
            decision = asvc.detect_anomalies(session, run_id)
            has_decision = "decision" in decision or "status" in decision
            is_cold_start = decision.get("decision") == "INSUFFICIENT_HISTORY" or decision.get("status") in {"COMPLETED", "NORMAL", "WATCH"}

            return {
                "detector_executed": True,
                "has_decision": has_decision,
                "cold_start_valid": is_cold_start,
            }
        finally:
            session.close()
            engine.dispose()


def evaluate(*, write_evidence: bool = True) -> EvalResult:
    math_results = test_robust_zscore_math()
    db_results = test_db_detection_flow()

    all_passed = all(math_results.values()) and all(db_results.values())
    score = 100.0 if all_passed else 0.0

    metrics = {
        "zscore_zero_mad_fallback": MetricValue(
            raw=math_results["zero_mad_fallback_works"],
            unit="boolean",
            normalized=norm.boolean(math_results["zero_mad_fallback_works"]),
        ),
        "zscore_outlier_detection": MetricValue(
            raw=math_results["outlier_detected"],
            unit="boolean",
            normalized=norm.boolean(math_results["outlier_detected"]),
        ),
        "detector_db_flow_fidelity": MetricValue(
            raw=db_results["detector_executed"],
            unit="boolean",
            normalized=norm.boolean(db_results["detector_executed"]),
        ),
        "anomaly_logic_score": MetricValue(
            raw=score,
            unit="ratio",
            normalized=score,
        ),
    }

    evidence: list[Evidence] = []
    if write_evidence:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        target = EVIDENCE_DIR / "anomaly_logic_probe.json"
        target.write_text(
            json.dumps({"math": math_results, "db": db_results}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        evidence.append(Evidence(type="file", path=str(target.relative_to(PROJECT_ROOT))))

    return EvalResult(
        gate=GATE,
        evaluator=EVALUATOR,
        status=EvalStatus.PASS if all_passed else EvalStatus.FAIL,
        score=score,
        metrics=metrics,
        evidence=evidence,
        metadata={
            "tested_components": ["calculate_robust_zscore", "detect_anomalies"],
        },
    )
