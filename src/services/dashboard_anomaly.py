"""Deterministic anomaly detection for persisted dashboard DQ runs.
Consolidated to use the canonical anomaly service via an adapter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from src.models.database import DqResultModel
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

    anomalies: list[DashboardAnomaly] = []

    # Map signals with high scores (anomaly decisions) back to DashboardAnomaly
    for sig in result.get("signals", []):
        # Only surface signals that indicate anomalies (score >= 0.70)
        # Or if the family is execution health / business invariant and failed
        if sig.get("score", 0.0) < 0.70:
            continue

        rule_id = sig["target_id"]

        # We need rule_title, checked_count, and failed_count from DqResultModel
        res_model = db.query(DqResultModel).filter(
            DqResultModel.run_id == run_id,
            DqResultModel.rule_id == rule_id
        ).first()

        if not res_model:
            # Skip if the target is table/dataset volume/freshness which doesn't map directly to a rule row
            continue

        checked_count = res_model.checked_count
        failed_count = res_model.failed_count

        # Mẫu quá nhỏ thì tỷ lệ vi phạm không đủ tin cậy để báo động cho Steward.
        # `minimum_checked_count` đã được khai báo trong chữ ký hàm từ đầu nhưng không
        # dòng nào dùng tới — một rule chạy trên 50 dòng vẫn nổi lên như bất thường thật.
        if checked_count < minimum_checked_count:
            logger.debug(
                "Bỏ qua signal %s: chỉ kiểm tra %d dòng (< %d), độ tin cậy không đủ.",
                rule_id, checked_count, minimum_checked_count,
            )
            continue

        current_rate = failed_count / checked_count if checked_count > 0 else 0.0

        baseline = sig.get("baseline", {})

        anomaly_type = "Z_SCORE_SPIKE" if sig["detector_name"] == "ROBUST_MAD_DETECTOR" else "HIGH_VIOLATION_RATE"
        if sig["detector_name"] == "BUSINESS_INVARIANT_DETECTOR":
            anomaly_type = "BUSINESS_RULE_VIOLATION"

        detection_mode = "HISTORICAL" if sig["sufficient_history"] else "COLD_START"

        # Map back to DashboardAnomaly structure
        anomalies.append(
            DashboardAnomaly(
                rule_id=rule_id,
                rule_title=res_model.rule_title,
                anomaly_type=anomaly_type,
                current_rate=round(current_rate, 6),
                historical_mean=round(baseline.get("median", 0.0), 6) if sig["sufficient_history"] else None,
                z_score=round(sig["score"] * 3.0, 2),  # Scaled back for UI representation
                history_size=baseline.get("history_size", 0),
                detection_mode=detection_mode,
                checked_count=checked_count,
                failed_count=failed_count,
                reason=sig["explanation_code"],
            )
        )

    return anomalies
