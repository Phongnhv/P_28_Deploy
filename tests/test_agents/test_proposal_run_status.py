"""Regression tests cho việc truyền trạng thái lỗi/pause của Run 1 (Proposal Graph)."""

import pytest

import src.agents.graph as graph_module
from src.services.rule_store import get_run


@pytest.fixture(autouse=True)
def resolved_source(monkeypatch):
    """These tests isolate run-status handling, not source resolution."""
    monkeypatch.setattr("src.services.source_binding.resolve_source_binding", lambda db, dataset_id, **kw: {"dataset_id": dataset_id, "dataset_version_id": "test-v1", "profile_run_id": "test-profile"})
    monkeypatch.setattr("src.services.rule_proposer_workflow._semantic_payload", lambda *args, **kw: {"rows": 1, "columns": []})


class _FakeGraph:
    """Graph giả lập trả về đúng final_state đã định sẵn."""

    def __init__(self, final_state: dict):
        self._final_state = final_state

    async def ainvoke(self, _initial_state):
        return self._final_state


@pytest.mark.asyncio
async def test_run_marked_failed_when_graph_returns_error(monkeypatch):
    """Regression: graph trả về error thì run KHÔNG được đánh dấu DONE."""
    monkeypatch.setattr(
        graph_module,
        "build_proposal_graph",
        lambda: _FakeGraph({"error": "LLM provider unreachable"}),
    )

    result = await graph_module.run_proposal_graph(dataset_id="dataset_err")
    run = get_run(result["run_id"])

    assert run is not None
    assert run["status"] == "FAILED", f"Trạng thái sai: {run['status']}"


@pytest.mark.asyncio
async def test_run_stays_awaiting_review_when_paused(monkeypatch):
    """Regression: gate ngữ nghĩa tạm dừng thì run phải giữ AWAITING_SEMANTIC_REVIEW, không bị ghi đè DONE."""
    monkeypatch.setattr(
        graph_module,
        "build_proposal_graph",
        lambda: _FakeGraph({"pause_reason": "AWAITING_SEMANTIC_REVIEW"}),
    )

    result = await graph_module.run_proposal_graph(dataset_id="dataset_pause")
    run = get_run(result["run_id"])

    assert run is not None
    assert run["status"] == "AWAITING_SEMANTIC_REVIEW", f"Trạng thái sai: {run['status']}"


@pytest.mark.asyncio
async def test_run_marked_done_on_success(monkeypatch):
    """Đường thành công vẫn phải đánh dấu DONE."""
    monkeypatch.setattr(
        graph_module,
        "build_proposal_graph",
        lambda: _FakeGraph({"proposed_rules": [], "metadata": {"hitl_status": "AWAITING_REVIEW"}}),
    )

    result = await graph_module.run_proposal_graph(dataset_id="dataset_ok")
    run = get_run(result["run_id"])

    assert run is not None
    assert run["status"] == "DONE"
