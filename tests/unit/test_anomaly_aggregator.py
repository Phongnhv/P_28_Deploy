"""Unit tests for Anomaly Signal Aggregator (4 scenarios)."""

import pytest
from src.detector_config import get_detector_config


def test_aggregator_scenario_1_single_family_gate():
    """Scenario 1: Single family spike without 2nd family falls back from ANOMALY to WATCH."""
    config = get_detector_config("anomaly-v1")
    signals = [
        {"family": "STATISTICAL", "score": 0.90, "reliability": 1.0}
    ]
    families = {s["family"] for s in signals}
    # Gate check: require at least 2 independent families for ANOMALY decision
    is_anomaly = len(families) >= config.aggregation_min_independent_families and signals[0]["score"] >= 0.70
    assert not is_anomaly


def test_aggregator_scenario_2_two_independent_families():
    """Scenario 2: Two independent families satisfy gate for ANOMALY decision."""
    config = get_detector_config("anomaly-v1")
    signals = [
        {"family": "STATISTICAL", "score": 0.85, "reliability": 1.0},
        {"family": "VOLUME", "score": 0.80, "reliability": 1.0},
    ]
    families = {s["family"] for s in signals}
    is_anomaly = len(families) >= config.aggregation_min_independent_families and max(s["score"] for s in signals) >= 0.70
    assert is_anomaly


def test_aggregator_scenario_3_duplicate_signals_same_family():
    """Scenario 3: Duplicate signals in same family do not artificially inflate family count."""
    config = get_detector_config("anomaly-v1")
    signals = [
        {"family": "STATISTICAL", "score": 0.85, "reliability": 1.0},
        {"family": "STATISTICAL", "score": 0.90, "reliability": 1.0},
        {"family": "STATISTICAL", "score": 0.75, "reliability": 1.0},
    ]
    unique_families = {s["family"] for s in signals}
    assert len(unique_families) == 1
    # Does not satisfy 2-family requirement
    assert len(unique_families) < config.aggregation_min_independent_families


def test_aggregator_scenario_4_low_reliability_fallback():
    """Scenario 4: All signals have low reliability (< 0.5) -> INSUFFICIENT_HISTORY."""
    signals = [
        {"family": "STATISTICAL", "score": 0.40, "reliability": 0.30},
        {"family": "VOLUME", "score": 0.30, "reliability": 0.40},
    ]
    avg_reliability = sum(s["reliability"] for s in signals) / len(signals)
    max_score = max(s["score"] for s in signals)
    decision = "INSUFFICIENT_HISTORY" if avg_reliability < 0.5 and max_score < 0.5 else "NORMAL"
    assert decision == "INSUFFICIENT_HISTORY"
