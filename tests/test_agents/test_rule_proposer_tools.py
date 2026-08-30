"""Unit tests for Graph 1 Rule Proposer DeepAgent tools.

Tests all 5 empirical investigation tools:
1. query_historical_approved_rules
2. dry_run_rule_candidate
3. inspect_data_samples
4. get_column_deep_stats
5. inspect_semantic_metadata
"""



from src.agents.tools.rule_proposer_tools import (
    RULE_PROPOSER_TOOLS,
    dry_run_rule_candidate,
    get_column_deep_stats,
    inspect_data_samples,
    inspect_semantic_metadata,
    query_historical_approved_rules,
)


def test_rule_proposer_tools_list():
    assert len(RULE_PROPOSER_TOOLS) == 5
    tool_names = {t.name for t in RULE_PROPOSER_TOOLS}
    assert "query_historical_approved_rules" in tool_names
    assert "dry_run_rule_candidate" in tool_names
    assert "inspect_data_samples" in tool_names
    assert "get_column_deep_stats" in tool_names
    assert "inspect_semantic_metadata" in tool_names


def test_query_historical_approved_rules_empty_db():
    result = query_historical_approved_rules.invoke({"table_name": "unknown_table"})
    assert isinstance(result, dict)
    assert "approved_rules" in result
    assert result["count"] >= 0


def test_inspect_data_samples_blocks_unsafe_sql():
    # Test SQL injection blocking
    malicious_inputs = [
        "fare_amount > 0; DROP TABLE source_rows",
        "1=1 -- comment",
        "fare_amount > 0 /* comment */",
        "DELETE FROM users",
        "UNION SELECT * FROM sessions",
    ]
    for cond in malicious_inputs:
        res = inspect_data_samples.invoke({"table_name": "source_rows", "filter_condition": cond})
        assert "error" in res
        assert "Unsafe SQL" in res["error"] or "Invalid filter_condition" in res["error"]


def test_dry_run_rule_candidate_range_calculation():
    # Test dry run execution on RANGE with mock DB / local DB
    result = dry_run_rule_candidate.invoke(
        {
            "table_name": "source_rows",
            "column_name": "trip_distance",
            "rule_type": "RANGE",
            "parameters": {"min": 0, "max": 100},
            "sample_limit": 100,
        }
    )
    assert isinstance(result, dict)
    assert result["rule_type"] == "RANGE"
    assert result["column"] == "trip_distance"
    assert "assessment" in result


def test_dry_run_rule_candidate_accepted_values():
    result = dry_run_rule_candidate.invoke(
        {
            "table_name": "source_rows",
            "column_name": "payment_type",
            "rule_type": "ACCEPTED_VALUES",
            "parameters": {"accepted_values": ["1", "2", "3", "4"]},
            "sample_limit": 100,
        }
    )
    assert isinstance(result, dict)
    assert result["rule_type"] == "ACCEPTED_VALUES"
    assert "assessment" in result


def test_dry_run_rule_candidate_empty_accepted_values_error():
    result = dry_run_rule_candidate.invoke(
        {
            "table_name": "source_rows",
            "column_name": "payment_type",
            "rule_type": "ACCEPTED_VALUES",
            "parameters": {"accepted_values": []},
        }
    )
    assert "observed_distinct_values" in result or "error" in result


def test_dry_run_rule_candidate_cross_field_comparison():
    result = dry_run_rule_candidate.invoke(
        {
            "table_name": "source_rows",
            "column_name": "pickup_at",
            "rule_type": "CROSS_FIELD_COMPARISON",
            "parameters": {"target_column": "dropoff_at", "operator": "<="},
            "sample_limit": 100,
        }
    )
    assert isinstance(result, dict)
    assert result["rule_type"] == "CROSS_FIELD_COMPARISON"
    assert "assessment" in result


def test_dry_run_rule_candidate_null_rate():
    result = dry_run_rule_candidate.invoke(
        {
            "table_name": "source_rows",
            "column_name": "fare_amount",
            "rule_type": "NULL_RATE",
            "parameters": {"max_null_pct": 5.0},
            "sample_limit": 100,
        }
    )
    assert isinstance(result, dict)
    assert result["rule_type"] == "NULL_RATE"
    assert "actual_null_pct" in result


def test_get_column_deep_stats():
    result = get_column_deep_stats.invoke(
        {
            "table_name": "source_rows",
            "column_name": "fare_amount",
        }
    )
    assert isinstance(result, dict)
    assert result["column_name"] == "fare_amount"


def test_inspect_semantic_metadata():
    result = inspect_semantic_metadata.invoke({"table_name": "source_rows"})
    assert isinstance(result, dict)
    assert "table_name" in result
