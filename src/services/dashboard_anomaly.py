"""Deterministic anomaly detection for persisted dashboard DQ runs."""

from __future__ import annotations

import math
from dataclasses import dataclass

from sqlalchemy.orm import Session

from src.models.database import DqResultModel, DqRunModel


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


def _z_score(current: float, history: list[float]) -> tuple[float, float]:
    mean = sum(history) / len(history)
    variance = sum((value - mean) ** 2 for value in history) / len(history)
    standard_deviation = math.sqrt(variance)
    if standard_deviation == 0:
        return (0.0 if current == mean else 3.0), mean
    return (current - mean) / standard_deviation, mean


def detect_dashboard_anomalies(
    db: Session,
    run_id: str,
    *,
    minimum_history: int = 5,
    static_threshold: float = 0.05,
    z_score_threshold: float = 2.5,
    minimum_checked_count: int = 100,
) -> list[DashboardAnomaly]:
    current_run = db.query(DqRunModel).filter(DqRunModel.id == run_id).first()
    if current_run is None:
        raise LookupError("DQ run not found")

    current_results = db.query(DqResultModel).filter(DqResultModel.run_id == run_id).all()
    anomalies: list[DashboardAnomaly] = []
    for result in current_results:
        if result.checked_count < minimum_checked_count:
            continue
        current_rate = result.failed_count / result.checked_count if result.checked_count else 0.0
        history_rows = (
            db.query(DqResultModel)
            .join(DqRunModel, DqRunModel.id == DqResultModel.run_id)
            .filter(
                DqResultModel.rule_id == result.rule_id,
                DqResultModel.run_id != run_id,
                DqRunModel.dataset_id == current_run.dataset_id,
                DqRunModel.status == "SUCCEEDED",
            )
            .order_by(DqRunModel.created_at.desc())
            .limit(20)
            .all()
        )
        history = [
            row.failed_count / row.checked_count if row.checked_count else 0.0
            for row in history_rows
        ]
        if len(history) >= minimum_history:
            score, mean = _z_score(current_rate, history)
            if score >= z_score_threshold and current_rate > 0.01:
                anomalies.append(
                    DashboardAnomaly(
                        rule_id=result.rule_id,
                        rule_title=result.rule_title,
                        anomaly_type="Z_SCORE_SPIKE",
                        current_rate=round(current_rate, 6),
                        historical_mean=round(mean, 6),
                        z_score=round(score, 2),
                        history_size=len(history),
                        detection_mode="HISTORICAL",
                        checked_count=result.checked_count,
                        failed_count=result.failed_count,
                        reason=(
                            f"Violation rate {current_rate:.2%} is above the historical "
                            f"baseline {mean:.2%} (z-score {score:.2f})."
                        ),
                    )
                )
        elif result.status == "FAIL" and current_rate >= static_threshold:
            anomalies.append(
                DashboardAnomaly(
                    rule_id=result.rule_id,
                    rule_title=result.rule_title,
                    anomaly_type="HIGH_VIOLATION_RATE",
                    current_rate=round(current_rate, 6),
                    historical_mean=None,
                    z_score=None,
                    history_size=len(history),
                    detection_mode="COLD_START",
                    checked_count=result.checked_count,
                    failed_count=result.failed_count,
                    reason=(
                        f"Violation rate {current_rate:.2%} exceeds the "
                        f"{static_threshold:.0%} cold-start threshold."
                    ),
                )
            )
    return anomalies
