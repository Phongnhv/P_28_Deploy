from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.agents.nodes import rule_proposer_node as node


@pytest.mark.asyncio
async def test_retry_requests_only_missing_candidates_and_keeps_initial_rules(monkeypatch):
    first = SimpleNamespace(candidate_id="a")
    second = SimpleNamespace(candidate_id="b")
    # The helper only needs identity here; strict narrative/schema validation is
    # exercised separately by the real binder tests.
    monkeypatch.setattr(node, "CandidateTableRuleProposal", lambda **data: SimpleNamespace(**data))
    proposer = AsyncMock(side_effect=[SimpleNamespace(rules=[first]), SimpleNamespace(rules=[second])])
    monkeypatch.setattr(node, "_propose_for_table", proposer)
    result, coverage = await node._propose_with_coverage_retry(
        table_name="taxi", candidates=[{"candidate_id": "a", "column": "fare"}, {"candidate_id": "b", "column": "tip"}],
        table_digest={"dashboard_candidate_mode": True, "columns": [{"name": "fare"}, {"name": "tip"}]},
        max_retries=2,
    )
    assert result.rules == [first, second]
    assert proposer.await_count == 2
    retry = proposer.await_args_list[1].kwargs
    assert retry["candidates"] == [{"candidate_id": "b", "column": "tip"}]
    assert retry["table_digest"]["columns"] == [{"name": "tip"}]
    assert retry["max_retries"] == 0
    assert coverage["retried_ids"] == ["b"]
    assert coverage["missing_ids"] == []


@pytest.mark.asyncio
async def test_missing_after_one_retry_fails_explicitly(monkeypatch):
    monkeypatch.setattr(node, "CandidateTableRuleProposal", lambda **data: SimpleNamespace(**data))
    proposer = AsyncMock(return_value=SimpleNamespace(rules=[]))
    monkeypatch.setattr(node, "_propose_for_table", proposer)
    with pytest.raises(ValueError, match="Missing candidate narratives"):
        await node._propose_with_coverage_retry(
            table_name="taxi", candidates=[{"candidate_id": "a", "column": "fare"}],
            table_digest={"dashboard_candidate_mode": True, "columns": [{"name": "fare"}]}, max_retries=0,
        )
    assert proposer.await_count == 2
