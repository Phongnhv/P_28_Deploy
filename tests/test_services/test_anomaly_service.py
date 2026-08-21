"""Unit tests for the canonical anomaly service (Median/MAD, exclusions, and family aggregations)."""

from datetime import datetime

import pytest
from sqlalchemy.orm import Session

from src.models.database import AnomalyFeedbackModel, DqResultModel, DqRunModel
from src.services.anomaly_service import (
    compute_mad,
    compute_median,
    detect_anomalies,
)


def test_calculate_median_mad_basic():
    """Verify median and MAD values for simple datasets."""
    data = [1.0, 2.0, 3.0, 4.0, 5.0]
    med = compute_median(data)
    mad = compute_mad(data, med)
    assert med == 3.0
    assert mad == 1.0

    data_const = [5.0, 5.0, 5.0]
    med = compute_median(data_const)
    mad = compute_mad(data_const, med)
    assert med == 5.0
    assert mad == 0.0


def test_calculate_median_mad_even():
    """Verify median and MAD for even-sized datasets."""
    data = [1.0, 2.0, 9.0, 10.0]
    med = compute_median(data)
    mad = compute_mad(data, med)
    assert med == 5.5
    assert mad == 4.0


def test_detect_anomalies_no_run(test_db):
    """Verify LookupError is raised when run_id does not exist."""
    with Session(test_db) as session:
        with pytest.raises(LookupError, match="Execution run non_existent not found"):
            detect_anomalies(session, "non_existent")


def test_detect_anomalies_cold_start(test_db):
    """Verify cold start behavior when history is insufficient."""
    with Session(test_db) as session:
        # Create a single run
        run = DqRunModel(id="run_1", job_id="job_1", dataset_id="dataset_1", rule_ids="[]", status="SUCCEEDED")
        session.add(run)

        # Add a passed result (violation_rate = 0.0) and a failed result (violation_rate = 0.08)
        res_pass = DqResultModel(run_id="run_1", rule_id="rule_pass", rule_title="Pass check", status="PASS", checked_count=100, failed_count=0, failed_row_ids="[]", violation_rate=0.0)
        res_fail = DqResultModel(run_id="run_1", rule_id="rule_fail", rule_title="Fail check", status="FAIL", checked_count=100, failed_count=8, failed_row_ids="[]", violation_rate=0.08)
        session.add_all([res_pass, res_fail])
        session.commit()

        # Execute anomaly detection
        result = detect_anomalies(session, "run_1")

        # Since rule_fail violation rate (0.08) >= 0.05, it gets score = 0.80 -> decision = ANOMALY
        assert result["decision"] == "ANOMALY"
        assert len(result["signals"]) == 2

        # Verify signal details
        sigs = {s["target_id"]: s for s in result["signals"]}
        assert sigs["rule_pass"]["score"] == 0.0
        assert sigs["rule_pass"]["sufficient_history"] is False

        assert sigs["rule_fail"]["score"] == 0.80  # Cold start static score = 0.80
        assert sigs["rule_fail"]["sufficient_history"] is False


def test_detect_anomalies_with_exclusions(test_db):
    """Verify that failed runs, feedback runs, and current runs are excluded from baselines."""
    with Session(test_db) as session:
        # 1. Historical runs
        # Run 1: Failed
        run1 = DqRunModel(id="run_h1", job_id="job_1", dataset_id="dataset_1", rule_ids="[]", status="FAILED", completed_at=datetime.now())
        res1 = DqResultModel(run_id="run_h1", rule_id="rule_x", rule_title="Check X", status="FAIL", checked_count=100, failed_count=2, failed_row_ids="[]", violation_rate=0.02)

        # Run 2: Succeeded (but has TRUE_ANOMALY feedback)
        run2 = DqRunModel(id="run_h2", job_id="job_1", dataset_id="dataset_1", rule_ids="[]", status="SUCCEEDED", completed_at=datetime.now())
        res2 = DqResultModel(run_id="run_h2", rule_id="rule_x", rule_title="Check X", status="FAIL", checked_count=100, failed_count=50, failed_row_ids="[]", violation_rate=0.50)
        feedback2 = AnomalyFeedbackModel(id="fb_2", anomaly_run_id="anom_h2", username="steward", feedback_label="TRUE_ANOMALY")
        # To link feedback, we need an anomaly run record for run2
        from src.models.database import AnomalyRunModel
        anom_run2 = AnomalyRunModel(id="anom_h2", execution_run_id="run_h2", decision="ANOMALY", score=0.9, severity="HIGH")

        # Run 3, 4, 5, 6, 7: Normal succeeded runs (violation_rate = 0.05, 0.05, 0.06, 0.04, 0.05)
        runs_normal = []
        res_normal = []
        for i in range(3, 8):
            rid = f"run_h{i}"
            runs_normal.append(DqRunModel(id=rid, job_id="job_1", dataset_id="dataset_1", rule_ids="[]", status="SUCCEEDED", completed_at=datetime.now()))
            res_normal.append(DqResultModel(run_id=rid, rule_id="rule_x", rule_title="Check X", status="FAIL", checked_count=100, failed_count=5, failed_row_ids="[]", violation_rate=0.05 if i != 5 else 0.06))

        # Current Run: Succeeded, violation rate spikes to 0.40
        run_curr = DqRunModel(id="run_curr", job_id="job_1", dataset_id="dataset_1", rule_ids="[]", status="SUCCEEDED", completed_at=datetime.now())
        res_curr = DqResultModel(run_id="run_curr", rule_id="rule_x", rule_title="Check X", status="FAIL", checked_count=100, failed_count=40, failed_row_ids="[]", violation_rate=0.40)

        session.add_all([run1, res1, run2, res2, anom_run2, feedback2, run_curr, res_curr] + runs_normal + res_normal)
        session.commit()

        # Run anomaly detection on current run
        # History should consist of only runs 3, 4, 5, 6, 7 (size = 5)
        # Baselines: [0.05, 0.05, 0.06, 0.05, 0.05] (approx, median = 0.05)
        result = detect_anomalies(session, "run_curr")

        assert len(result["signals"]) == 1
        sig = result["signals"][0]
        assert sig["sufficient_history"] is True
        assert sig["baseline"]["history_size"] == 5
        # The median should be 0.05
        assert sig["baseline"]["median"] == 0.05
        # 0.40 is far above 0.05, so it triggers an anomaly
        assert sig["score"] >= 0.80  # ANOMALY score
        assert result["decision"] == "ANOMALY"


def test_detect_anomalies_critical_override(test_db):
    """Verify that failing critical business or execution rules triggers immediate CRITICAL decision."""
    with Session(test_db) as session:
        run = DqRunModel(id="run_over", job_id="job_1", dataset_id="dataset_1", rule_ids="[]", status="SUCCEEDED")
        # Failing a business rule (dimension = BUSINESS_RULE, status = FAIL)
        res_biz = DqResultModel(run_id="run_over", rule_id="rule_biz", rule_title="Business invariant", status="FAIL", checked_count=100, failed_count=1, failed_row_ids="[]", violation_rate=0.01)
        session.add_all([run, res_biz])
        session.commit()

        # Since it is a business rule failure, it should trigger critical decision directly
        result = detect_anomalies(session, "run_over")
        assert result["decision"] == "CRITICAL"
        assert "Vi phạm nghiêm trọng luật nghiệp vụ" in result["override_reason"]


def test_volume_signal_does_not_dilute_rule_anomaly(test_db):
    """Regression: một family khỏe mạnh (VOLUME=0.0) KHÔNG được kéo tụt điểm của family đang báo động.

    Kịch bản production thật: sau bước ingest luôn tồn tại bản ghi `profiles`, nên
    VOLUME_DRIFT_DETECTOR luôn sinh một signal. Trước khi sửa, phép trung bình có trọng số
    biến score 0.80 (ANOMALY) thành 0.3429 (NORMAL).
    """
    from src.models.database import ProfileModel

    with Session(test_db) as session:
        run = DqRunModel(id="run_dilute", job_id="job_1", dataset_id="dataset_1", rule_ids="[]", status="SUCCEEDED")
        res_fail = DqResultModel(
            run_id="run_dilute", rule_id="rule_fail", rule_title="Fail check", status="FAIL",
            checked_count=100, failed_count=8, failed_row_ids="[]", violation_rate=0.08,
        )
        # Bản ghi profile khiến VOLUME_DRIFT_DETECTOR sinh signal score = 0.0 (không đủ lịch sử)
        profile = ProfileModel(
            dataset_id="dataset_1", row_count=100, completeness_score=100.0,
            validity_score=100.0, duplicate_rate=0.0, evidence_keys="[]",
        )
        session.add_all([run, res_fail, profile])
        session.commit()

        result = detect_anomalies(session, "run_dilute")

        families = {s["family"] for s in result["signals"]}
        assert "VOLUME" in families, "Fixture phải sinh được signal VOLUME"

        stat_scores = [s["score"] for s in result["signals"] if s["family"] == "STATISTICAL"]
        assert max(stat_scores) == 0.80

        assert result["score"] >= 0.70, (
            f"Điểm tổng hợp bị pha loãng: {result['score']} (kỳ vọng >= 0.70)"
        )
        assert result["decision"] == "ANOMALY"
