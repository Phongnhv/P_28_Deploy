"""Unit tests for Isolation Forest multivariate anomaly detector and feature builder."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from src.config.detector_config import get_detector_config
from src.models.database import (
    AnomalyFeedbackModel,
    AnomalyRunModel,
    DqResultModel,
    DqRunModel,
)
from src.services.anomaly_features import (
    build_bulk_rule_feature_frames,
    extract_validated_feature_vector,
)
from src.services.isolation_forest_detector import (
    run_isolation_forest_rule_detector,
)


def test_detector_config_registry():
    """Verify versioned detector configs and strict error handling on unknown versions."""
    cfg_v1 = get_detector_config("anomaly-v1")
    assert cfg_v1.isolation_forest_enabled is False
    assert cfg_v1.isolation_forest_mode == "DISABLED"

    cfg_v2 = get_detector_config("anomaly-v2-iforest")
    assert cfg_v2.isolation_forest_enabled is True
    assert cfg_v2.isolation_forest_mode == "SHADOW"
    assert cfg_v2.iforest_n_jobs == 1

    with pytest.raises(ValueError, match="Unknown detector configuration version"):
        get_detector_config("anomaly-unknown-v999")


def test_extract_validated_feature_vector():
    """Verify strict validation rules for feature vectors."""
    # Valid vector
    vec = extract_validated_feature_vector(
        violation_count=50,
        total_rows=1000,
        duration_ms=150.0,
        prev_violation_rate=0.02,
    )
    assert vec is not None
    assert len(vec) == 5
    assert math.isclose(vec[0], 0.05)
    assert math.isclose(vec[1], 0.03)

    # First chronological sample (prev_violation_rate=None) -> delta = 0.0
    vec_first = extract_validated_feature_vector(
        violation_count=10,
        total_rows=500,
        duration_ms=100.0,
        prev_violation_rate=None,
    )
    assert vec_first is not None
    assert math.isclose(vec_first[0], 0.02)
    assert math.isclose(vec_first[1], 0.0)

    # Invalid: total_rows <= 0
    assert extract_validated_feature_vector(0, 0, 100.0, None) is None
    assert extract_validated_feature_vector(0, -10, 100.0, None) is None

    # Invalid: violation_count > total_rows or negative
    assert extract_validated_feature_vector(100, 50, 100.0, None) is None
    assert extract_validated_feature_vector(-5, 50, 100.0, None) is None

    # Invalid: negative or non-finite duration
    assert extract_validated_feature_vector(5, 50, -10.0, None) is None
    assert extract_validated_feature_vector(5, 50, float("inf"), None) is None
    assert extract_validated_feature_vector(float("nan"), 50, 10.0, None) is None


def test_isolation_forest_degenerate_history():
    """Verify degenerate history (identical data points) is safely detected without crash."""
    flat_vec = [0.01, 0.0, math.log1p(10), math.log1p(1000), math.log1p(100)]
    history_flat = [list(flat_vec) for _ in range(50)]

    sig = run_isolation_forest_rule_detector(
        rule_id="rule_degenerate",
        current_vector=flat_vec,
        history_vectors=history_flat,
        evidence_refs=["ref"],
    )
    assert sig["sufficient_history"] is False
    assert sig["score"] == 0.0
    assert sig["baseline"]["status"] == "DEGENERATE_HISTORY"


def test_isolation_forest_history_gating():
    """Verify gating behavior for <30, 30-49, and 50+ history samples."""
    dummy_vec = [0.01, 0.0, math.log1p(10), math.log1p(1000), math.log1p(200)]

    # 1. Below minimum history (<30)
    history_20 = [dummy_vec for _ in range(20)]
    sig_20 = run_isolation_forest_rule_detector(
        rule_id="rule_1",
        current_vector=dummy_vec,
        history_vectors=history_20,
        evidence_refs=["ref_1"],
        min_history_size=30,
    )
    assert sig_20["sufficient_history"] is False
    assert sig_20["score"] == 0.0
    assert sig_20["baseline"]["status"] == "INSUFFICIENT_HISTORY"

    # 2. Advisory / reduced reliability range (30-49)
    history_35 = [
        [0.01 + float(i % 5) * 0.001, 0.0, math.log1p(10 + i), math.log1p(1000), math.log1p(200 + i * 2)]
        for i in range(35)
    ]
    sig_35 = run_isolation_forest_rule_detector(
        rule_id="rule_1",
        current_vector=dummy_vec,
        history_vectors=history_35,
        evidence_refs=["ref_1"],
        min_history_size=30,
        preferred_history_size=50,
    )
    assert sig_35["sufficient_history"] is True
    assert 0.45 <= sig_35["reliability"] <= 0.75
    assert sig_35["baseline"]["history_size"] == 35

    # 3. Full reliability range (50+)
    history_60 = [
        [0.01 + float(i % 5) * 0.001, 0.0, math.log1p(10 + i), math.log1p(1000), math.log1p(200 + i * 2)]
        for i in range(60)
    ]
    sig_60 = run_isolation_forest_rule_detector(
        rule_id="rule_1",
        current_vector=dummy_vec,
        history_vectors=history_60,
        evidence_refs=["ref_1"],
        min_history_size=30,
        preferred_history_size=50,
    )
    assert sig_60["sufficient_history"] is True
    assert sig_60["reliability"] >= 0.75


def test_isolation_forest_deterministic():
    """Verify deterministic outputs with fixed random_state."""
    history = [
        [0.01 + float(i % 5) * 0.001, 0.0, math.log1p(10 + i), math.log1p(1000), math.log1p(200 + i * 2)]
        for i in range(50)
    ]
    curr = [0.08, 0.07, math.log1p(80), math.log1p(1000), math.log1p(1200)]

    sig1 = run_isolation_forest_rule_detector(
        rule_id="rule_det",
        current_vector=curr,
        history_vectors=history,
        evidence_refs=["ref"],
        random_state=42,
    )
    sig2 = run_isolation_forest_rule_detector(
        rule_id="rule_det",
        current_vector=curr,
        history_vectors=history,
        evidence_refs=["ref"],
        random_state=42,
    )

    assert sig1["score"] == sig2["score"]
    assert sig1["reliability"] == sig2["reliability"]
    assert sig1["baseline"]["calibration_threshold"] == sig2["baseline"]["calibration_threshold"]


def test_isolation_forest_multivariate_outlier_detection():
    """Verify that a multivariate outlier receives a higher score than a normal inlier."""
    history = []
    for i in range(60):
        v_rate = 0.01 + (i % 3) * 0.002
        dur = 100.0 + (i % 5) * 10.0
        rows = 1000 + (i % 4) * 20
        vec = extract_validated_feature_vector(int(v_rate * rows), rows, dur, 0.01)
        assert vec is not None
        history.append(vec)

    # Inlier sample
    inlier_vec = extract_validated_feature_vector(11, 1000, 105.0, 0.01)
    inlier_sig = run_isolation_forest_rule_detector(
        rule_id="rule_test",
        current_vector=inlier_vec,
        history_vectors=history,
        evidence_refs=["ref_inlier"],
        random_state=42,
    )

    # Multivariate Outlier: moderate rate change + 20x duration increase + sudden 70% row drop
    outlier_vec = extract_validated_feature_vector(14, 300, 3000.0, 0.01)
    outlier_sig = run_isolation_forest_rule_detector(
        rule_id="rule_test",
        current_vector=outlier_vec,
        history_vectors=history,
        evidence_refs=["ref_outlier"],
        random_state=42,
    )

    assert outlier_sig["score"] > inlier_sig["score"]
    assert inlier_sig["score"] < 0.40


def test_clean_history_exclusion_and_causal_boundary(test_db):
    """Verify build_bulk_rule_feature_frames respects causal boundary and excludes future/failed runs."""
    base_time = datetime(2026, 8, 1, 0, 0, 0, tzinfo=UTC)
    dataset_id = "ds_clean_test"
    rule_id = "rule_filter"

    with Session(test_db) as session:
        # 1. Add 35 normal clean historical runs
        for i in range(35):
            run_id = f"clean_run_{i}"
            session.add(
                DqRunModel(
                    id=run_id,
                    job_id="job_1",
                    dataset_id=dataset_id,
                    rule_ids=f'["{rule_id}"]',
                    status="SUCCEEDED",
                    created_at=base_time + timedelta(hours=i),
                )
            )
            session.add(
                DqResultModel(
                    run_id=run_id,
                    rule_id=rule_id,
                    rule_title="Check nulls",
                    status="PASS",
                    checked_count=1000,
                    failed_count=10,
                    failed_row_ids="[]",
                    violation_rate=0.01,
                    duration_ms=100.0,
                )
            )

        # 2. Add 1 future run relative to current evaluation (created_at = base_time + 40h)
        session.add(
            DqRunModel(
                id="future_run_1",
                job_id="job_1",
                dataset_id=dataset_id,
                rule_ids=f'["{rule_id}"]',
                status="SUCCEEDED",
                created_at=base_time + timedelta(hours=40),
            )
        )
        session.add(
            DqResultModel(
                run_id="future_run_1",
                rule_id=rule_id,
                rule_title="Check nulls",
                status="PASS",
                checked_count=1000,
                failed_count=10,
                failed_row_ids="[]",
                duration_ms=100.0,
            )
        )

        # 3. Add 1 TRUE_ANOMALY feedback run
        anom_run_id = "steward_anom_run"
        session.add(
            DqRunModel(
                id=anom_run_id,
                job_id="job_1",
                dataset_id=dataset_id,
                rule_ids=f'["{rule_id}"]',
                status="SUCCEEDED",
                created_at=base_time + timedelta(hours=30),
            )
        )
        session.add(
            DqResultModel(
                run_id=anom_run_id,
                rule_id=rule_id,
                rule_title="Check nulls",
                status="FAIL",
                checked_count=1000,
                failed_count=500,
                failed_row_ids="[]",
                violation_rate=0.50,
            )
        )
        session.add(
            AnomalyRunModel(
                id="anom_record_1",
                execution_run_id=anom_run_id,
                status="SUCCEEDED",
                decision="ANOMALY",
            )
        )
        session.add(
            AnomalyFeedbackModel(
                id="fb_1",
                anomaly_run_id="anom_record_1",
                username="steward",
                feedback_label="TRUE_ANOMALY",
            )
        )

        # 4. Current run at base_time + 35h
        current_run_id = "current_eval_run"
        cur_dq_run = DqRunModel(
            id=current_run_id,
            job_id="job_1",
            dataset_id=dataset_id,
            rule_ids=f'["{rule_id}"]',
            status="RUNNING",
            created_at=base_time + timedelta(hours=35),
        )
        cur_dq_res = DqResultModel(
            run_id=current_run_id,
            rule_id=rule_id,
            rule_title="Check nulls",
            status="PASS",
            checked_count=1000,
            failed_count=10,
            failed_row_ids="[]",
            duration_ms=100.0,
        )
        session.add(cur_dq_run)
        session.add(cur_dq_res)
        session.commit()

        # Query bulk frames
        excluded = {anom_run_id}
        frames = build_bulk_rule_feature_frames(
            db=session,
            current_run=cur_dq_run,
            current_results=[cur_dq_res],
            uses_test_store=False,
            excluded_run_ids=excluded,
        )

        frame = frames[rule_id]
        # Should only contain 35 clean historical runs (excluding future run and steward anomaly)
        assert len(frame.history_vectors) == 35
        assert frame.current_vector is not None
        assert frame.disable_reason is None
