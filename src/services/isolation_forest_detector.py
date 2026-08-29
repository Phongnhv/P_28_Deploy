"""Isolation Forest Anomaly Detector Module.

Implements unsupervised multivariate anomaly detection for execution snapshots
with history gating, deterministic calibration, degenerate history protection,
and fault isolation.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from src.config.detector_config import DetectorConfig
from src.services.anomaly_features import RuleFeatureFrame

logger = logging.getLogger(__name__)


def run_isolation_forest_rule_detector(
    rule_id: str,
    current_vector: list[float] | None,
    history_vectors: list[list[float]],
    evidence_refs: list[str],
    config: DetectorConfig | None = None,
    # Backward compatibility keyword arguments
    min_history_size: int | None = None,
    preferred_history_size: int | None = None,
    n_estimators: int | None = None,
    contamination: float | None = None,
    random_state: int | None = None,
    disable_reason: str | None = None,
) -> dict[str, Any]:
    """Run Isolation Forest detection on a single rule with calibration and safety checks."""
    if config is None:
        from src.config.detector_config import get_detector_config
        config = get_detector_config("anomaly-v2-iforest")

    min_hist = min_history_size if min_history_size is not None else config.min_history_size_iforest
    pref_hist = preferred_history_size if preferred_history_size is not None else config.preferred_history_size_iforest
    n_est = n_estimators if n_estimators is not None else config.iforest_n_estimators
    contam = contamination if contamination is not None else config.iforest_contamination
    rnd_seed = random_state if random_state is not None else config.iforest_random_state
    epsilon = config.iforest_score_spread_epsilon
    feature_schema_version = config.feature_schema_version

    history_size = len(history_vectors)

    # 1. Check upstream disable reasons or missing current vector
    if disable_reason is not None or current_vector is None:
        reason = disable_reason or "INVALID_CURRENT_VECTOR"
        return {
            "signal_id": f"sig-{uuid.uuid4().hex[:12]}",
            "family": "ML",
            "target_type": "RULE",
            "target_id": rule_id,
            "score": 0.0,
            "reliability": 0.0,
            "observed_value": "0.0",
            "baseline": {
                "history_size": history_size,
                "feature_schema_version": feature_schema_version,
                "status": reason,
            },
            "sufficient_history": False,
            "detector_name": "ISOLATION_FOREST",
            "detector_version": "iforest-v1",
            "explanation_code": f"Isolation Forest không khả dụng: {reason}.",
            "evidence_refs": evidence_refs,
        }

    # 2. History Gating Check
    if history_size < min_hist:
        return {
            "signal_id": f"sig-{uuid.uuid4().hex[:12]}",
            "family": "ML",
            "target_type": "RULE",
            "target_id": rule_id,
            "score": 0.0,
            "reliability": 0.30,
            "observed_value": "0.0",
            "baseline": {
                "history_size": history_size,
                "min_history_required": min_hist,
                "feature_schema_version": feature_schema_version,
                "status": "INSUFFICIENT_HISTORY",
            },
            "sufficient_history": False,
            "detector_name": "ISOLATION_FOREST",
            "detector_version": "iforest-v1",
            "explanation_code": f"Không đủ mẫu lịch sử sạch ({history_size}/{min_hist}) để huấn luyện Isolation Forest.",
            "evidence_refs": evidence_refs,
        }

    # 3. Model Training & Scoring with Fault Isolation
    try:
        import numpy as np
        from sklearn.ensemble import IsolationForest

        x_train = np.array(history_vectors, dtype=np.float64)
        x_current = np.array([current_vector], dtype=np.float64)

        if x_train.ndim != 2 or x_train.shape[1] != 5 or x_current.shape[1] != 5:
            return {
                "signal_id": f"sig-{uuid.uuid4().hex[:12]}",
                "family": "ML",
                "target_type": "RULE",
                "target_id": rule_id,
                "score": 0.0,
                "reliability": 0.0,
                "observed_value": "0.0",
                "baseline": {
                    "history_size": history_size,
                    "feature_schema_version": feature_schema_version,
                    "status": "INVALID_FEATURE_DIMENSIONS",
                },
                "sufficient_history": False,
                "detector_name": "ISOLATION_FOREST",
                "detector_version": "iforest-v1",
                "explanation_code": "Kích thước vector đặc trưng không khớp schema.",
                "evidence_refs": evidence_refs,
            }

        # 4. Check for Degenerate History (negligible variance across all feature dimensions)
        col_ranges = np.ptp(x_train, axis=0)
        if np.all(col_ranges < epsilon):
            return {
                "signal_id": f"sig-{uuid.uuid4().hex[:12]}",
                "family": "ML",
                "target_type": "RULE",
                "target_id": rule_id,
                "score": 0.0,
                "reliability": 0.30,
                "observed_value": "0.0",
                "baseline": {
                    "history_size": history_size,
                    "feature_schema_version": feature_schema_version,
                    "status": "DEGENERATE_HISTORY",
                },
                "sufficient_history": False,
                "detector_name": "ISOLATION_FOREST",
                "detector_version": "iforest-v1",
                "explanation_code": "Lịch sử dữ liệu hoàn toàn phẳng (degenerate history), không thể phân biệt bất thường.",
                "evidence_refs": evidence_refs,
            }

        # Calculate reliability
        if history_size < pref_hist:
            frac = (history_size - min_hist) / max(1, (pref_hist - min_hist))
            reliability = 0.45 + frac * 0.30
        else:
            reliability = min(0.90, 0.75 + (history_size - pref_hist) * 0.003)

        # Initialize model with deterministic parameters and single thread (n_jobs=1)
        model = IsolationForest(
            n_estimators=n_est,
            contamination=contam,
            max_samples="auto",
            random_state=rnd_seed,
            n_jobs=1,
        )
        model.fit(x_train)

        # Compute raw anomaly scores in [0, 1] using -score_samples
        train_raw_scores = -model.score_samples(x_train)
        current_raw_score = float(-model.score_samples(x_current)[0])

        train_min = float(np.min(train_raw_scores))
        train_max = float(np.max(train_raw_scores))
        train_spread = train_max - train_min

        # Check training score spread
        if train_spread < epsilon:
            return {
                "signal_id": f"sig-{uuid.uuid4().hex[:12]}",
                "family": "ML",
                "target_type": "RULE",
                "target_id": rule_id,
                "score": 0.0,
                "reliability": 0.30,
                "observed_value": "0.0",
                "baseline": {
                    "history_size": history_size,
                    "feature_schema_version": feature_schema_version,
                    "status": "DEGENERATE_TRAINING_SCORES",
                },
                "sufficient_history": False,
                "detector_name": "ISOLATION_FOREST",
                "detector_version": "iforest-v1",
                "explanation_code": "Mô hình Isolation Forest không tạo ra phân tách điểm số (degenerate training scores).",
                "evidence_refs": evidence_refs,
            }

        # Calibration threshold: percentile based on training distribution
        calib_percentile = max(50.0, min(99.9, 100.0 * (1.0 - contam)))
        threshold = float(np.percentile(train_raw_scores, calib_percentile))
        train_median = float(np.median(train_raw_scores))

        # Normalize score into [0.0, 1.0] with strict epsilon thresholding
        if current_raw_score > threshold + epsilon:
            # Score strictly above threshold scales from 0.70 to 1.00
            diff = current_raw_score - threshold
            scale_range = max(0.05, 1.0 - threshold)
            normalized_score = min(1.0, 0.70 + 0.30 * min(1.0, diff / scale_range))
        elif current_raw_score > train_median:
            # Score between median and threshold scales from 0.20 to 0.45
            scale_range = max(1e-4, threshold - train_median)
            normalized_score = 0.20 + 0.25 * min(1.0, (current_raw_score - train_median) / scale_range)
        else:
            # Score below median scales from 0.00 to 0.20
            scale_range = max(1e-4, train_median - train_min)
            normalized_score = max(0.0, 0.20 * ((current_raw_score - train_min) / scale_range))

        score = round(float(normalized_score), 4)
        reliability = round(float(reliability), 4)

        if score >= 0.70:
            explanation_code = (
                f"Phát hiện bất thường đa biến (Multivariate Anomaly) bởi Isolation Forest "
                f"với score={score:.2f} (raw={current_raw_score:.3f}, threshold={threshold:.3f})."
            )
        elif score >= 0.45:
            explanation_code = f"Tín hiệu cảnh báo nghi ngờ đa biến bởi Isolation Forest (score={score:.2f})."
        else:
            explanation_code = f"Hồ sơ thực thi bình thường theo Isolation Forest (score={score:.2f})."

        return {
            "signal_id": f"sig-{uuid.uuid4().hex[:12]}",
            "family": "ML",
            "target_type": "RULE",
            "target_id": rule_id,
            "score": score,
            "reliability": reliability,
            "observed_value": str(score),
            "baseline": {
                "history_size": history_size,
                "feature_schema_version": feature_schema_version,
                "raw_score": round(current_raw_score, 4),
                "calibration_threshold": round(threshold, 4),
                "train_median": round(train_median, 4),
                "train_min": round(train_min, 4),
                "train_max": round(train_max, 4),
                "n_estimators": n_est,
                "contamination": contam,
                "random_state": rnd_seed,
            },
            "sufficient_history": True,
            "detector_name": "ISOLATION_FOREST",
            "detector_version": "iforest-v1",
            "explanation_code": explanation_code,
            "evidence_refs": evidence_refs,
        }

    except Exception as exc:
        logger.warning(
            "Isolation Forest execution failed for rule %s: %s", rule_id, exc, exc_info=True
        )
        return {
            "signal_id": f"sig-{uuid.uuid4().hex[:12]}",
            "family": "ML",
            "target_type": "RULE",
            "target_id": rule_id,
            "score": 0.0,
            "reliability": 0.0,
            "observed_value": "ERROR",
            "baseline": {
                "history_size": history_size,
                "feature_schema_version": feature_schema_version,
                "error": str(exc),
            },
            "sufficient_history": False,
            "detector_name": "ISOLATION_FOREST",
            "detector_version": "iforest-v1",
            "explanation_code": f"Không thể tính toán Isolation Forest do lỗi hệ thống: {exc}",
            "evidence_refs": evidence_refs,
        }


def run_isolation_forest_for_frame(
    frame: RuleFeatureFrame,
    evidence_refs: list[str],
    config: DetectorConfig,
) -> dict[str, Any]:
    """Execute Isolation Forest detector directly on a prebuilt RuleFeatureFrame."""
    return run_isolation_forest_rule_detector(
        rule_id=frame.rule_id,
        current_vector=frame.current_vector,
        history_vectors=frame.history_vectors,
        evidence_refs=evidence_refs,
        config=config,
        disable_reason=frame.disable_reason,
    )
