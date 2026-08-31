"""Canonical Anomaly Detection Service for Graph 3 and Dashboard Parity.

Calculates robust statistical estimators (Median/MAD), business invariant violations,
multivariate Isolation Forest anomaly signals, and aggregated decisions.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config import get_settings
from src.config.detector_config import get_detector_config
from src.models.database import (
    AnomalyFeedbackModel,
    AnomalyRunModel,
    DqResultModel,
    DqRunModel,
    ProfileModel,
)
from src.services.anomaly_features import (
    build_bulk_rule_feature_frames,
)
from src.services.isolation_forest_detector import (
    run_isolation_forest_for_frame,
)
from src.services.rule_store import TestResultModel, TestRunModel

logger = logging.getLogger(__name__)

# Constants for sliding history window and estimators
_HISTORY_WINDOW = 20
_VOLUME_HISTORY_WINDOW = 20
_MIN_HISTORY_ROBUST = 5
_MAD_ZERO_FLOOR = 0.005
_MAX_ROBUST_Z = 10.0


def calculate_robust_zscore(current: float, history: list[float]) -> tuple[float, float, float]:
    """Calculate Modified Z-score using Median and Median Absolute Deviation (MAD).

    Formula: Robust_Z = 0.6745 * (x - median) / MAD
    Returns: (robust_z, median, mad)
    """
    if not history:
        return 0.0, current, 0.0

    sorted_hist = sorted(history)

    def _median(arr: list[float]) -> float:
        mid = len(arr) // 2
        return (arr[mid] + arr[~mid]) / 2.0

    median = _median(sorted_hist)
    abs_deviations = sorted(abs(x - median) for x in history)
    mad = _median(abs_deviations)

    if mad == 0.0:
        # Fallback to percentage-based or absolute floor scale if MAD is 0
        fallback_scale = max(abs(median) * 0.1, _MAD_ZERO_FLOOR)
        robust_z = 0.6745 * (current - median) / fallback_scale
        return max(-_MAX_ROBUST_Z, min(_MAX_ROBUST_Z, robust_z)), median, 0.0

    robust_z = 0.6745 * (current - median) / mad
    return robust_z, median, mad


def get_excluded_execution_run_ids(db: Session) -> set[str]:
    """Get all execution run IDs that are marked as true anomalies by the steward."""
    subquery = select(AnomalyFeedbackModel.anomaly_run_id).where(AnomalyFeedbackModel.feedback_label == "TRUE_ANOMALY")
    runs = db.query(AnomalyRunModel.execution_run_id).filter(AnomalyRunModel.id.in_(subquery)).all()
    return {r.execution_run_id for r in runs}


def detect_anomalies(db: Session, execution_run_id: str, detector_config_version: str | None = None) -> dict[str, Any]:
    """Canonical function to calculate signals, aggregate decisions, and return anomaly outcomes."""
    settings = get_settings()
    if detector_config_version is None:
        detector_config_version = getattr(settings, "detector_config_version", "anomaly-v2-iforest")

    # Load versioned detector configuration (raises ValueError on unknown versions)
    config = get_detector_config(detector_config_version)

    # Graph 2 writes the dashboard's durable execution evidence to
    # ``test_runs`` / ``test_results``.  Older API flows use ``dq_runs`` /
    # ``dq_results`` instead.  Graph 3 must consume either contract; otherwise
    # a completed Graph 2 is incorrectly reported as "execution run not found".
    current_run = db.query(DqRunModel).filter(DqRunModel.id == execution_run_id).first()
    uses_test_store = current_run is None
    if current_run is None:
        current_run = (
            db.query(TestRunModel)
            .filter(TestRunModel.test_run_id == execution_run_id)
            .first()
        )
    if not current_run:
        raise LookupError(f"Execution run {execution_run_id} not found")

    if uses_test_store:
        current_results = (
            db.query(TestResultModel)
            .filter(TestResultModel.test_run_id == execution_run_id)
            .all()
        )
    else:
        current_results = db.query(DqResultModel).filter(DqResultModel.run_id == execution_run_id).all()

    # Get excluded execution runs (failed runs + TRUE_ANOMALY feedback)
    excluded_run_ids = get_excluded_execution_run_ids(db)

    signals: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    # 1. Nạp lịch sử của TẤT CẢ rules trong một query duy nhất cho statistical detector.
    # Sắp xếp mới nhất trước rồi cắt cửa sổ trượt _HISTORY_WINDOW cho từng rule.
    history_by_rule: dict[str, list[float]] = {}
    rule_ids = [res.rule_id for res in current_results]
    current_created_at = getattr(current_run, "created_at", None)

    if rule_ids and uses_test_store:
        stat_query = (
            db.query(TestResultModel.rule_id, TestResultModel.violation_count, TestResultModel.total_rows, TestRunModel.test_run_id)
            .join(TestRunModel, TestRunModel.test_run_id == TestResultModel.test_run_id)
            .filter(
                TestResultModel.rule_id.in_(rule_ids),
                TestRunModel.dataset_id == current_run.dataset_id,
                TestRunModel.test_run_id != execution_run_id,
                TestRunModel.status == "DONE",
                TestResultModel.status.in_(["PASS", "FAIL", "PASSED", "FAILED"]),
            )
        )
        if current_created_at is not None:
            stat_query = stat_query.filter(TestRunModel.created_at < current_created_at)

        history_rows = stat_query.order_by(TestRunModel.created_at.desc()).all()

        for h_row in history_rows:
            if h_row.test_run_id in excluded_run_ids:
                continue
            if h_row.total_rows and h_row.total_rows > 0:
                rate = float(h_row.violation_count or 0) / float(h_row.total_rows)
                arr = history_by_rule.setdefault(h_row.rule_id, [])
                if len(arr) < _HISTORY_WINDOW:
                    arr.append(rate)

    elif rule_ids and not uses_test_store:
        stat_query = (
            db.query(DqResultModel.rule_id, DqResultModel.failed_count, DqResultModel.checked_count, DqRunModel.id)
            .join(DqRunModel, DqRunModel.id == DqResultModel.run_id)
            .filter(
                DqResultModel.rule_id.in_(rule_ids),
                DqRunModel.dataset_id == current_run.dataset_id,
                DqRunModel.id != execution_run_id,
                DqRunModel.status.in_(["SUCCEEDED", "DONE"]),
                DqResultModel.status.in_(["PASS", "FAIL", "PASSED", "FAILED"]),
            )
        )
        if current_created_at is not None:
            stat_query = stat_query.filter(DqRunModel.created_at < current_created_at)

        history_rows = stat_query.order_by(DqRunModel.created_at.desc()).all()

        for h_row in history_rows:
            if h_row.id in excluded_run_ids:
                continue
            if h_row.checked_count and h_row.checked_count > 0:
                rate = float(h_row.failed_count or 0) / float(h_row.checked_count)
                arr = history_by_rule.setdefault(h_row.rule_id, [])
                if len(arr) < _HISTORY_WINDOW:
                    arr.append(rate)

    # 2. Extract statistical signals for each rule
    for res in current_results:
        rule_id = res.rule_id
        if uses_test_store:
            checked_count = res.total_rows
            failed_count = res.violation_count
        else:
            checked_count = res.checked_count
            failed_count = res.failed_count

        current_rate = (float(failed_count or 0) / float(checked_count)) if checked_count and checked_count > 0 else 0.0

        history_rates = history_by_rule.get(rule_id, [])
        sufficient_history = len(history_rates) >= config.min_history_size_robust

        score = 0.0
        reliability = 1.0
        observed_value = round(current_rate, 4)
        detector_name = "ROBUST_MAD_DETECTOR"
        explanation_code = ""
        baseline_stats = {}

        if sufficient_history:
            robust_z, median, mad = calculate_robust_zscore(current_rate, history_rates)
            baseline_stats = {
                "median": round(median, 4),
                "mad": round(mad, 4),
                "history_size": len(history_rates),
                "robust_z": round(robust_z, 4),
            }

            if robust_z >= config.z_score_threshold_anomaly:
                score = min(1.0, 0.70 + (robust_z - config.z_score_threshold_anomaly) * 0.1)
                explanation_code = f"Tỷ lệ vi phạm ({current_rate:.2%}) đột biến so với baseline lịch sử (median={median:.2%}, MAD={mad:.4f}) với Robust Z = {robust_z:.2f}."
            elif robust_z >= config.z_score_threshold_watch:
                score = 0.45 + (robust_z - config.z_score_threshold_watch) * 0.25
                explanation_code = f"Tỷ lệ vi phạm ({current_rate:.2%}) có dấu hiệu cảnh báo so với baseline lịch sử với Robust Z = {robust_z:.2f}."
            else:
                score = 0.0
                explanation_code = "Tỷ lệ vi phạm bình thường so với baseline lịch sử."

            reliability = min(1.0, 0.7 + len(history_rates) * 0.015)

        else:
            # Cold-start fallback
            baseline_stats = {
                "static_threshold": 0.05,
                "history_size": len(history_rates),
            }
            detector_name = "COLD_START_DETECTOR"
            reliability = 0.6 if history_rates else 0.4

            if res.status in ("FAIL", "FAILED"):
                if current_rate >= 0.05:
                    score = 0.8
                    explanation_code = f"Tỷ lệ vi phạm ({current_rate:.2%}) vượt quá ngưỡng Cold-Start tĩnh 5%."
                else:
                    score = 0.4
                    explanation_code = "Tỷ lệ vi phạm nằm dưới ngưỡng Cold-Start tĩnh 5%."
            else:
                score = 0.0
                explanation_code = "Quy tắc kiểm thử ĐẠT."

        # Business rule override check
        rule_title = str(getattr(res, "rule_title", ""))
        is_business_rule = (
            rule_title.startswith("BUSINESS_")
            or "invariant" in rule_title.lower()
            or res.rule_id.endswith(".BUSINESS_RULE")
        )
        if is_business_rule and res.status in ("FAIL", "FAILED"):
            score = 1.0
            detector_name = "BUSINESS_INVARIANT_DETECTOR"
            explanation_code = f"Vi phạm nghiêm trọng luật nghiệp vụ (Business Invariant): {rule_title}."

        evidence_ref = (
            f"dq_results.id={res.id}"
            if not uses_test_store
            else f"test_results.{res.test_run_id}:{res.rule_id}"
        )

        signals.append({
            "signal_id": f"sig-{uuid.uuid4().hex[:12]}",
            "family": "BUSINESS_RULE" if is_business_rule else "STATISTICAL",
            "target_type": "RULE",
            "target_id": rule_id,
            "score": round(score, 4),
            "reliability": round(reliability, 4),
            "observed_value": str(observed_value),
            "baseline": baseline_stats,
            "sufficient_history": sufficient_history,
            "detector_name": detector_name,
            "detector_version": "1.0.0",
            "explanation_code": explanation_code,
            "evidence_refs": [evidence_ref],
        })

    # 3. Isolation Forest Detector (Bulk Builder & Model Fitting)
    if config.isolation_forest_enabled and config.isolation_forest_mode != "DISABLED":
        start_ml_time = time.perf_counter()
        try:
            frames = build_bulk_rule_feature_frames(
                db=db,
                current_run=current_run,
                current_results=current_results,
                uses_test_store=uses_test_store,
                excluded_run_ids=excluded_run_ids,
                feature_schema_version=config.feature_schema_version,
                max_history=config.max_history_size_iforest,
            )

            eligible_count = len(frames)
            fitted_count = 0
            insufficient_count = 0
            invalid_current_count = 0
            degenerate_count = 0

            for frame in frames.values():
                evidence_ref = (
                    f"test_results.{execution_run_id}:{frame.rule_id}"
                    if uses_test_store
                    else f"dq_results.{execution_run_id}:{frame.rule_id}"
                )
                ml_sig = run_isolation_forest_for_frame(
                    frame=frame,
                    evidence_refs=[evidence_ref],
                    config=config,
                )
                signals.append(ml_sig)

                status = ml_sig.get("baseline", {}).get("status")
                if status == "INSUFFICIENT_HISTORY":
                    insufficient_count += 1
                elif status == "INVALID_CURRENT_VECTOR":
                    invalid_current_count += 1
                elif status in ("DEGENERATE_HISTORY", "DEGENERATE_TRAINING_SCORES"):
                    degenerate_count += 1
                elif ml_sig.get("sufficient_history", False):
                    fitted_count += 1

                if "error" in ml_sig.get("baseline", {}):
                    errors.append({
                        "detector": "ISOLATION_FOREST",
                        "rule_id": frame.rule_id,
                        "error": ml_sig["baseline"]["error"],
                    })

            ml_duration = time.perf_counter() - start_ml_time
            logger.info(
                "Isolation Forest completed: eligible_rules=%d, fitted=%d, insufficient=%d, "
                "invalid_current=%d, degenerate=%d, duration=%.3fs",
                eligible_count,
                fitted_count,
                insufficient_count,
                invalid_current_count,
                degenerate_count,
                ml_duration,
            )

        except Exception as ml_exc:
            logger.warning("Isolation Forest bulk processing failed: %s", ml_exc, exc_info=True)
            errors.append({"detector": "ISOLATION_FOREST", "error": str(ml_exc)})

    # 4. Table/Dataset Level Detectors (Volume & Freshness)
    profile = (
        db.query(ProfileModel)
        .filter(ProfileModel.dataset_id == current_run.dataset_id)
        .order_by(ProfileModel.generated_at.desc())
        .first()
    )
    if profile:
        current_rows = profile.row_count
        hist_profiles = (
            db.query(ProfileModel)
            .filter(ProfileModel.dataset_id == current_run.dataset_id, ProfileModel.generated_at < profile.generated_at)
            .order_by(ProfileModel.generated_at.desc())
            .limit(_VOLUME_HISTORY_WINDOW)
            .all()
        )
        hist_rows = [p.row_count for p in hist_profiles]
        sufficient_vol_history = len(hist_rows) >= 5

        vol_score = 0.0
        vol_reliability = 1.0
        vol_explanation = ""

        if sufficient_vol_history:
            vol_z, vol_median, vol_mad = calculate_robust_zscore(float(current_rows), [float(x) for x in hist_rows])
            vol_baseline = {"median": vol_median, "mad": vol_mad, "history_size": len(hist_rows)}
            if abs(vol_z) >= 3.0:
                vol_score = min(1.0, 0.8 + (abs(vol_z) - 3.0) * 0.05)
                vol_explanation = f"Số lượng dòng ({current_rows}) đột biến so với baseline lịch sử (median={vol_median:.0f}, MAD={vol_mad:.0f}) với Robust Z = {vol_z:.2f}."
            else:
                vol_score = 0.0
                vol_explanation = "Số lượng dòng bình thường."
        else:
            vol_baseline = {"static_change_threshold": 0.5}
            vol_reliability = 0.5
            vol_explanation = "Không đủ lịch sử để đánh giá đột biến số lượng dòng."

        signals.append(
            {
                "signal_id": f"sig-{uuid.uuid4().hex[:12]}",
                "family": "VOLUME",
                "target_type": "DATASET",
                "target_id": current_run.dataset_id,
                "score": round(vol_score, 4),
                "reliability": round(vol_reliability, 4),
                "observed_value": str(current_rows),
                "baseline": vol_baseline,
                "sufficient_history": sufficient_vol_history,
                "detector_name": "VOLUME_DRIFT_DETECTOR",
                "detector_version": "1.0.0",
                "explanation_code": vol_explanation,
                "evidence_refs": [],
            }
        )

    # Freshness Check
    freshness_signals = [s for s in signals if s["target_id"].endswith(".FRESHNESS")]
    for fs in freshness_signals:
        fs["family"] = "FRESHNESS"
        fs["detector_name"] = "FRESHNESS_DETECTOR"

    # Execution Health Check (failures in tests or runtime error)
    exec_errors = [res for res in current_results if res.status == "ERROR"]
    current_error = getattr(current_run, "error_message", None) or getattr(current_run, "error", None)
    if exec_errors or current_error:
        signals.append({
            "signal_id": f"sig-{uuid.uuid4().hex[:12]}",
            "family": "EXECUTION",
            "target_type": "DATASET",
            "target_id": current_run.dataset_id,
            "score": 1.0,
            "reliability": 1.0,
            "observed_value": "ERROR",
            "baseline": {},
            "sufficient_history": True,
            "detector_name": "EXECUTION_HEALTH_DETECTOR",
            "detector_version": "1.0.0",
            "explanation_code": f"Phát hiện {len(exec_errors)} lỗi kiểm thử hệ thống hoặc lỗi thực thi chạy pipeline.",
            "evidence_refs": [
                f"dq_results.id={e.id}"
                if not uses_test_store
                else f"test_results.{e.test_run_id}:{e.rule_id}"
                for e in exec_errors
            ],
        })

    # 5. Deterministic Family Aggregation & Rollout Policies
    family_scores: dict[str, list[float]] = {}
    family_weights = config.family_weights
    rollout_mode = config.isolation_forest_mode if config.isolation_forest_enabled else "DISABLED"

    for sig in signals:
        fam = sig["family"]
        # In SHADOW mode: ML signals are logged and persisted, but excluded from decision aggregation
        if fam == "ML" and (rollout_mode == "SHADOW" or not sig.get("sufficient_history", False)):
            continue
        family_scores.setdefault(fam, []).append(sig["score"])

    # Base score = MAX of non-ML families
    non_ml_reps: dict[str, float] = {}
    for fam, scs in family_scores.items():
        if fam != "ML":
            non_ml_reps[fam] = max(scs) if scs else 0.0

    base_score = max(non_ml_reps.values()) if non_ml_reps else 0.0
    family_reps = dict(non_ml_reps)

    # ML Conservative Bounded Uplift in ADVISORY and CALIBRATED modes
    ml_score = 0.0
    ml_reliability = 0.0
    ml_signals = [
        sig for sig in signals
        if sig["family"] == "ML" and sig.get("sufficient_history", False) and sig.get("score", 0.0) > 0.0
    ]

    if ml_signals and rollout_mode in ("ADVISORY", "CALIBRATED"):
        best_ml_signal = max(ml_signals, key=lambda s: s["score"])
        ml_score = best_ml_signal["score"]
        ml_reliability = best_ml_signal["reliability"]
        family_reps["ML"] = ml_score

        uplift = config.ml_family_weight * ml_reliability * max(0.0, ml_score - base_score)
        final_score = min(1.0, base_score + uplift)
    else:
        final_score = base_score

    # Determine dominant family
    dominant_family = ""
    if family_reps:
        dominant_family = max(
            family_reps,
            key=lambda fam: (family_reps[fam], family_weights.get(fam, 0.5)),
        )

    # Priority overrides (Business invariant >= 0.8 or Execution health == 1.0)
    has_critical_override = False
    override_reason = ""
    if family_reps.get("BUSINESS_RULE", 0.0) >= 0.8:
        has_critical_override = True
        override_reason = "Vi phạm nghiêm trọng luật nghiệp vụ (Business Invariant)."
    elif family_reps.get("EXECUTION", 0.0) >= 1.0:
        has_critical_override = True
        override_reason = "Lỗi thực thi kiểm thử hệ thống (Execution Health)."

    if has_critical_override:
        final_score = max(family_reps.values())
        decision = "CRITICAL"
        confidence = 0.95
        severity = "HIGH"
    else:
        # Calculate avg_reliability over active participating signals
        active_signals = [
            sig for sig in signals
            if not (sig["family"] == "ML" and (rollout_mode == "SHADOW" or not sig.get("sufficient_history", False)))
        ]
        rel_sum = sum(sig["reliability"] for sig in active_signals)
        avg_reliability = (rel_sum / len(active_signals)) if active_signals else 0.0

        if not active_signals or (avg_reliability < 0.5 and final_score < 0.5):
            decision = "INSUFFICIENT_HISTORY"
            confidence = avg_reliability
            severity = "LOW"
        elif final_score >= 0.70:
            # Corroboration Guardrail: ML alone cannot produce ANOMALY without non-ML corroboration (>= 0.45)
            if dominant_family == "ML" and base_score < 0.45:
                decision = "WATCH"
                confidence = 0.70
                severity = "MEDIUM"
            else:
                decision = "ANOMALY"
                confidence = 0.80
                severity = "HIGH"
        elif final_score >= 0.45:
            decision = "WATCH"
            confidence = 0.70
            severity = "MEDIUM"
        else:
            decision = "NORMAL"
            confidence = 0.90
            severity = "LOW"

    final_score = round(final_score, 4)

    return {
        "decision": decision,
        "score": final_score,
        "confidence": round(confidence, 4),
        "severity": severity,
        "signals": signals,
        "errors": errors,
        "override_reason": override_reason,
        "dominant_family": dominant_family,
        "detector_config_version": detector_config_version,
        "rollout_mode": rollout_mode,
    }
