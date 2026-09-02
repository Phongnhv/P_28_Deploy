"""Deterministic anomaly detection for persisted dashboard DQ runs.

Consolidated to use the canonical anomaly service via an adapter.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from src.models.database import DqResultModel, RuleVersionModel
from src.services.anomaly_service import detect_anomalies

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DashboardAnomaly:
    rule_id: str
    rule_title: str
    anomaly_type: str
    current_rate: float
    historical_mean: float | None
    z_score: float | None
    history_size: int
    detection_mode: str
    checked_count: int
    failed_count: int
    reason: str
    columns: list[str]


def _columns_for_rule(db: Session, rule_id: str) -> list[str]:
    """Resolve involved columns from the approved typed rule, not its opaque id."""
    version = db.get(RuleVersionModel, rule_id)
    if version is None and rule_id.startswith("rv_"):
        version = db.get(RuleVersionModel, rule_id[3:])
    if version is None:
        parts = rule_id.replace("rv_", "", 1).split(".")
        return [parts[1]] if len(parts) >= 3 and parts[1] else []
    try:
        spec = json.loads(version.rule_spec or "{}")
    except (TypeError, ValueError):
        return []
    if not isinstance(spec, dict):
        return []
    columns: list[str] = []
    for key in ("column", "column_name", "left_column", "right_column", "target_column"):
        value = spec.get(key)
        if isinstance(value, str) and value and value not in columns:
            columns.append(value)
    for key in ("columns", "fingerprint_columns"):
        value = spec.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item and item not in columns:
                    columns.append(item)
    return columns


def detect_dashboard_anomalies(
    db: Session,
    run_id: str,
    *,
    minimum_history: int = 5,
    static_threshold: float = 0.05,
    z_score_threshold: float = 2.5,
    minimum_checked_count: int = 100,
) -> list[DashboardAnomaly]:
    """Adapter that delegates dashboard anomaly detection to the canonical anomaly_service."""
    try:
        result = detect_anomalies(db, run_id)
    except LookupError as exc:
        logger.warning("Could not calculate anomalies for dashboard: %s", exc)
        return []
    except Exception as exc:
        logger.error("Error in canonical anomaly detection: %s", exc, exc_info=True)
        return []

    rollout_mode = result.get("rollout_mode", "DISABLED")
    anomalies: list[DashboardAnomaly] = []

    # Map signals with high scores (anomaly decisions) back to DashboardAnomaly
    for sig in result.get("signals", []):
        fam = sig.get("family", "")

        # Skip all ML signals when rollout mode is SHADOW
        if fam == "ML" and rollout_mode == "SHADOW":
            continue

        # Skip disabled, failed, or insufficient-history ML signals
        if fam == "ML" and not sig.get("sufficient_history", False):
            continue

        # Only surface signals that indicate anomalies (score >= 0.70)
        if sig.get("score", 0.0) < 0.70:
            continue

        rule_id = sig["target_id"]

        # We need rule_title, checked_count, and failed_count from DqResultModel
        res_model = (
            db.query(DqResultModel).filter(DqResultModel.run_id == run_id, DqResultModel.rule_id == rule_id).first()
        )

        if not res_model:
            # Skip if the target is table/dataset volume/freshness which doesn't map directly to a rule row
            continue

        checked_count = res_model.checked_count
        failed_count = res_model.failed_count

        if checked_count < minimum_checked_count:
            logger.debug(
                "Bỏ qua signal %s: chỉ kiểm tra %d dòng (< %d), độ tin cậy không đủ.",
                rule_id,
                checked_count,
                minimum_checked_count,
            )
            continue

        current_rate = failed_count / checked_count if checked_count > 0 else 0.0
        baseline = sig.get("baseline", {})
        detector_name = sig.get("detector_name", "")

        if fam == "ML" or detector_name == "ISOLATION_FOREST":
            anomaly_type = "ISOLATION_FOREST_OUTLIER"
            detection_mode = "ISOLATION_FOREST"
            historical_mean = None
            z_score = None
        elif detector_name == "BUSINESS_INVARIANT_DETECTOR":
            anomaly_type = "BUSINESS_RULE_VIOLATION"
            detection_mode = "BUSINESS_RULE"
            historical_mean = None
            z_score = None
        elif detector_name == "ROBUST_MAD_DETECTOR":
            anomaly_type = "Z_SCORE_SPIKE"
            detection_mode = "HISTORICAL"
            historical_mean = round(baseline.get("median", 0.0), 6) if sig["sufficient_history"] else None
            z_score = round(baseline.get("robust_z", 0.0), 2) if "robust_z" in baseline else None
        else:
            anomaly_type = "HIGH_VIOLATION_RATE"
            detection_mode = "COLD_START"
            historical_mean = None
            z_score = None

        # Map back to DashboardAnomaly structure
        anomalies.append(
            DashboardAnomaly(
                rule_id=rule_id,
                rule_title=res_model.rule_title,
                anomaly_type=anomaly_type,
                current_rate=round(current_rate, 6),
                historical_mean=historical_mean,
                z_score=z_score,
                history_size=baseline.get("history_size", 0),
                detection_mode=detection_mode,
                checked_count=checked_count,
                failed_count=failed_count,
                reason=sig["explanation_code"],
                columns=_columns_for_rule(db, rule_id),
            )
        )

    return anomalies
