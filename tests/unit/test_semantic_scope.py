import asyncio
from unittest.mock import AsyncMock

import pytest

from src.agents.nodes.dataset_understanding_node import _understand_table
from src.models.semantic_contract import TableSemanticContract


@pytest.mark.asyncio
@pytest.mark.parametrize("columns,relationships", [
    (["signup_date", "taxi_id"], []),
    (["signup_date", "signup_date"], []),
    (["signup_date"], [{"left_column": "signup_date", "operator": "<=", "right_column": "signup_date"}]),
    (["signup_date"], [{"left_column": "signup_date", "operator": "<=", "right_column": "current_date"}]),
])
async def test_semantic_rejects_foreign_columns_and_invalid_relationships(columns, relationships):
    contract = TableSemanticContract(
        table_name="customers", domain="customer", table_purpose="Customer registrations",
        columns=[{"name": name, "semantic_type": "timestamp", "business_role": "signup"} for name in columns],
        relationships=relationships,
    )
    with pytest.raises(ValueError):
        await _understand_table("customers", {"columns": [{"name": "signup_date"}]},
                                "", "", AsyncMock(ainvoke=AsyncMock(return_value=contract)), asyncio.Semaphore(1))


@pytest.mark.asyncio
async def test_semantic_accepts_grounded_cross_column_relationship():
    contract = TableSemanticContract(
        table_name="customers", domain="customer", table_purpose="Customer registrations",
        columns=[{"name": name, "semantic_type": "timestamp", "business_role": name}
                 for name in ["signup_date", "updated_date"]],
        relationships=[{"left_column": "signup_date", "operator": "<=", "right_column": "updated_date"}],
    )
    result = await _understand_table(
        "customers", {"columns": [{"name": "signup_date"}, {"name": "updated_date"}]}, "", "",
        AsyncMock(ainvoke=AsyncMock(return_value=contract)), asyncio.Semaphore(1),
    )
    assert result.relationships[0].right_column == "updated_date"


def test_dashboard_candidates_preserve_ids_and_confirmed_nullability(tmp_path, monkeypatch):
    from src.agents.nodes.rule_candidate_builder_node import rule_candidate_builder_node
    from src.config import get_settings

    monkeypatch.setattr(get_settings(), "output_dir", str(tmp_path))
    candidates = [
        {"candidate_id": "not-null:customer_id", "column": "customer_id", "rule_type": "NOT_NULL", "parameters": {}, "evidence": []},
        {"candidate_id": "not-null:email", "column": "email", "rule_type": "NOT_NULL", "parameters": {}, "evidence": []},
    ]
    result = rule_candidate_builder_node({
        "semantic_contract": {"tables": {"customers": {"columns": [
            {"name": "customer_id", "nullable_expected": False}, {"name": "email", "nullable_expected": True},
        ]}}},
        "dataset_profile_digest": {"customers": {"table": "customers", "rows": 12,
            "columns": [{"name": "customer_id"}, {"name": "email"}], "dashboard_candidate_mode": True,
            "dashboard_rule_candidates": candidates}},
    })
    assert [c["candidate_id"] for c in result["rule_candidates"]] == ["not-null:customer_id"]
    assert result["rule_candidates"][0]["parameters"] == {}
    assert result["rule_candidates"][0]["table"] == "customers"
