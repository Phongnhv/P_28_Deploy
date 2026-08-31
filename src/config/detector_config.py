"""Detector configuration module for anomaly detection thresholds, family weights, and bounds."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DetectorConfig:
    version: str = "anomaly-v1"
    isolation_forest_enabled: bool = False
    isolation_forest_mode: str = "DISABLED"  # "DISABLED", "SHADOW", "ADVISORY", "CALIBRATED"
    feature_schema_version: str = "iforest-features-v1"
    min_history_size_iforest: int = 30
    preferred_history_size_iforest: int = 50
    max_history_size_iforest: int = 90
    iforest_n_estimators: int = 200
    iforest_contamination: float = 0.02
    iforest_random_state: int = 42
    iforest_n_jobs: int = 1
    iforest_score_spread_epsilon: float = 1e-5
    ml_family_weight: float = 0.15
    family_weights: dict[str, float] = field(
        default_factory=lambda: {
            "BUSINESS_RULE": 1.0,
            "EXECUTION": 1.0,
            "VOLUME": 0.8,
            "FRESHNESS": 0.8,
            "SCHEMA_DRIFT": 0.8,
            "FAILURE_CLUSTER": 0.7,
            "STATISTICAL": 0.6,
            "ML": 0.15,
        }
    )
    min_history_size_robust: int = 5
    mad_zero_fallback_scale_pct: float = 0.10
    mad_zero_absolute_floor: float = 0.005
    z_score_threshold_watch: float = 2.0
    z_score_threshold_anomaly: float = 3.0
    aggregation_min_independent_families: int = 2


DETECTOR_CONFIGS: dict[str, DetectorConfig] = {
    "anomaly-v1": DetectorConfig(
        version="anomaly-v1",
        isolation_forest_enabled=False,
        isolation_forest_mode="DISABLED",
    ),
    "anomaly-v2-iforest": DetectorConfig(
        version="anomaly-v2-iforest",
        isolation_forest_enabled=True,
        isolation_forest_mode="SHADOW",
    ),
    "anomaly-v2-iforest-advisory": DetectorConfig(
        version="anomaly-v2-iforest-advisory",
        isolation_forest_enabled=True,
        isolation_forest_mode="ADVISORY",
    ),
    "anomaly-v2-iforest-calibrated": DetectorConfig(
        version="anomaly-v2-iforest-calibrated",
        isolation_forest_enabled=True,
        isolation_forest_mode="CALIBRATED",
    ),
}


def get_detector_config(version: str = "anomaly-v1") -> DetectorConfig:
    """Retrieve an immutable detector configuration by version name.

    Raises ValueError if the version is not registered in DETECTOR_CONFIGS.
    """
    if version not in DETECTOR_CONFIGS:
        raise ValueError(
            f"Unknown detector configuration version: '{version}'. "
            f"Supported versions: {list(DETECTOR_CONFIGS.keys())}"
        )
    return DETECTOR_CONFIGS[version]
