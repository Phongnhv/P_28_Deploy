import json

import pandas as pd
import pytest

from src.agents.nodes.persist_report_node import aggregate_graph2_status
from src.services.dashboard_agent_workflow import get_dataset_rule_policy
from src.services.versioned_dataset import (
    DatasetContractError,
    canonical_schema_manifest,
    execute_rules_frame,
    safe_source_object_key,
    schema_hash,
)


def test_schema_hash_is_stable_and_object_key_is_versioned():
    frame = pd.DataFrame({"order_id": ["a", "b"], "amount": [1.0, 2.0]})
    schema = canonical_schema_manifest(frame)
    assert schema_hash(schema) == schema_hash(json.loads(json.dumps(schema)))
    key = safe_source_object_key("ws-1", "orders", "dv-1", "abc123", "orders.csv")
    assert key == "datasets/ws-1/orders/versions/dv-1/abc123/orders.csv"
    with pytest.raises(DatasetContractError):
        safe_source_object_key("ws/escape", "orders", "dv-1", "abc123", "orders.csv")


def test_unknown_dataset_never_receives_taxi_policy_without_schema():
    assert get_dataset_rule_policy("orders-unknown") is None


def test_rule_errors_are_isolated_and_follow_graph2_status_contract():
    frame = pd.DataFrame({"order_id": ["a", "b"], "amount": [1.0, -2.0]})
    results = execute_rules_frame(frame, [
        {"rule_id": "bad", "type": "not_null", "column": "missing"},
        {"rule_id": "good", "type": "numeric_range", "column": "amount", "min_value": 0},
    ])
    assert [row["status"] for row in results] == ["ERROR", "FAIL"]
    assert aggregate_graph2_status(results) == "PARTIAL"
    assert aggregate_graph2_status([{"status": "ERROR"}]) == "FAILED"
    assert aggregate_graph2_status([{"status": "PASS"}, {"status": "FAIL"}]) == "SUCCEEDED"

