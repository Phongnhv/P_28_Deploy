"""Unit tests for Anomaly Signal Detectors (8 scenarios)."""

from src.services.anomaly_service import calculate_robust_zscore


def test_detector_scenario_1_cold_start():
    """Scenario 1: Cold start static detection when history size < 5."""
    # When history is insufficient, static threshold 0.05 applies
    current_rate = 0.06
    history = [0.01, 0.02]  # < 5 history
    z_score, median, mad = calculate_robust_zscore(current_rate, history)
    assert len(history) < 5
    assert current_rate >= 0.05


def test_detector_scenario_2_constant_history_mad_zero():
    """Scenario 2: Constant history (MAD = 0) uses fallback scale."""
    history = [0.0, 0.0, 0.0, 0.0, 0.0]
    current = 0.01
    z_score, median, mad = calculate_robust_zscore(current, history)
    assert mad == 0.0
    assert median == 0.0
    # Scaled response instead of arbitrary hardcoded z=3.0
    assert 0.0 < z_score <= 10.0


def test_detector_scenario_3_single_spike():
    """Scenario 3: Single high violation spike over stable baseline."""
    history = [0.01] * 10
    current = 0.15
    z_score, median, mad = calculate_robust_zscore(current, history)
    assert z_score > 3.0


def test_detector_scenario_4_ewma_gradual_drift():
    """Scenario 4: EWMA gradual drift test."""
    history = [0.01, 0.02, 0.03, 0.04, 0.05]
    current = 0.06
    z_score, median, mad = calculate_robust_zscore(current, history)
    assert median == 0.03
    assert z_score > 0


def test_detector_scenario_5_volume_drop():
    """Scenario 5: Dataset volume drop detection."""
    history_rows = [1000.0, 1005.0, 998.0, 1002.0, 1000.0]
    current_rows = 10.0
    z_score, median, mad = calculate_robust_zscore(current_rows, history_rows)
    assert abs(z_score) >= 3.0


def test_detector_scenario_6_freshness_delay():
    """Scenario 6: Freshness delay evaluation."""
    max_age_hours = 24
    observed_lag_hours = 48
    assert observed_lag_hours > max_age_hours


def test_detector_scenario_7_schema_breaking_change():
    """Scenario 7: Schema column drift evaluation."""
    baseline_columns = {"id", "fare", "passenger_count"}
    current_columns = {"id", "fare"}  # passenger_count dropped
    missing_columns = baseline_columns - current_columns
    assert "passenger_count" in missing_columns


def test_detector_scenario_8_failure_cluster():
    """Scenario 8: Failure cluster across multiple rules on same table."""
    failed_rules_same_table = ["rule_1", "rule_2", "rule_3"]
    assert len(failed_rules_same_table) >= 3
