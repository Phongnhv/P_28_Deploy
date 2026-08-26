"""Detector configuration module for anomaly detection thresholds, family weights, and bounds."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DetectorConfig:
    version: str = "anomaly-v1"
    family_weights: dict[str, float] = field(
        default_factory=lambda: {
            "BUSINESS_RULE": 1.0,
            "EXECUTION": 1.0,
            "VOLUME": 0.8,
            "FRESHNESS": 0.8,
            "SCHEMA_DRIFT": 0.8,
            "FAILURE_CLUSTER": 0.7,
            "STATISTICAL": 0.6,
            "ML": 0.5,
        }
    )
    min_history_size_robust: int = 5
    mad_zero_fallback_scale_pct: float = 0.10
    mad_zero_absolute_floor: float = 0.005
    z_score_threshold_watch: float = 2.0
    z_score_threshold_anomaly: float = 3.0
    aggregation_min_independent_families: int = 2


DETECTOR_CONFIGS: dict[str, DetectorConfig] = {
    "anomaly-v1": DetectorConfig()
}


def get_detector_config(version: str = "anomaly-v1") -> DetectorConfig:
    return DETECTOR_CONFIGS.get(version, DETECTOR_CONFIGS["anomaly-v1"])
