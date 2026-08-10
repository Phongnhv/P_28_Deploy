"""Tests cho HITL Gate: DB, hitl_gate_node, review_rule, bulk_review, get_review_summary.

Pattern: SQLite in-memory engine, unittest.mock.patch, pytest.mark.asyncio.
Không cần LLM — dựng fake proposed_rules bằng tay.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# In-memory DB fixture — isolate khỏi data/app.db
# ---------------------------------------------------------------------------

@pytest.fixture()
def in_memory_engine(tmp_path):
    """Tạo SQLite file-based temp engine và khởi tạo schema."""
    import src.services.rule_store as rs
    from src.services.rule_store import Base

    db_file = tmp_path / "test.db"
    test_engine = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(test_engine)

    # Set _engine trực tiếp — thread pool workers cũng thấy engine đúng
    original = rs._engine
    rs._engine = test_engine
    yield test_engine
    rs._engine = original
    test_engine.dispose()


def _make_run(engine, run_id: str, dataset_id: str = "yellow_tripdata") -> None:
    """Seed một ProposalRunModel."""
    from src.services.rule_store import ProposalRunModel
    with Session(engine) as s:
        s.add(ProposalRunModel(run_id=run_id, dataset_id=dataset_id, status="DONE"))
        s.commit()


def _make_rule_dict(
    run_id: str,
    rule_id: str,
    *,
    rule_type: str = "NOT_NULL",
    column: str = "vendor_id",
    dimension: str = "COMPLETENESS",
    severity: str = "CRITICAL",
    status: str = "PENDING",
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "run_id": run_id,
        "table_name": "yellow_tripdata",
        "column": column,
        "rule_type": rule_type,
        "parameters": {},
        "confidence_score": 1.0,
        "severity": severity,
        "dimension": dimension,
        "rule_description": f"Cột {column} không được để trống.",
        "ai_reasoning": "Profiler xác nhận null_pct = 0.0 trên toàn bộ dữ liệu.",
        "status": status,
        "edited_parameters": None,
        "reviewer": None,
        "review_note": None,
        "reviewed_at": None,
        "created_at": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# 1. run_id regression — rule_proposer_node GIỮ nguyên rule_run_id từ state
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rule_proposer_keeps_run_id_from_state():
    """rule_proposer_node phải dùng state['rule_run_id'] thay vì sinh mới."""
    from src.agents.nodes.rule_proposer_node import rule_proposer_node

    preset_run_id = uuid.uuid4().hex
    fake_proposal = MagicMock()
    fake_proposal.rules = []

    with (
        patch("src.agents.nodes.rule_proposer_node.split_digest_by_table", return_value={"t1": {}}),
        patch("src.agents.nodes.rule_proposer_node.get_llm"),
        patch("src.agents.nodes.rule_proposer_node._propose_for_table", new_callable=AsyncMock, return_value=fake_proposal),
        patch("src.agents.nodes.rule_proposer_node.get_settings") as mock_settings,
    ):
        mock_settings.return_value.rule_proposer_concurrency = 1
        mock_settings.return_value.rule_proposer_max_retries = 1
        mock_settings.return_value.debug_dump_table_digests = False
        mock_settings.return_value.llm_provider = "openai"

        state = {
            "dataset_profile_digest": {"tables": {"t1": {}}},
            "rule_run_id": preset_run_id,
        }
        result = await rule_proposer_node(state)

    assert result["rule_run_id"] == preset_run_id


@pytest.mark.asyncio
async def test_rule_proposer_generates_run_id_when_missing():
    """rule_proposer_node phải tự sinh run_id khi state không có."""
    from src.agents.nodes.rule_proposer_node import rule_proposer_node

    fake_proposal = MagicMock()
    fake_proposal.rules = []

    with (
        patch("src.agents.nodes.rule_proposer_node.split_digest_by_table", return_value={"t1": {}}),
        patch("src.agents.nodes.rule_proposer_node.get_llm"),
        patch("src.agents.nodes.rule_proposer_node._propose_for_table", new_callable=AsyncMock, return_value=fake_proposal),
        patch("src.agents.nodes.rule_proposer_node.get_settings") as mock_settings,
    ):
        mock_settings.return_value.rule_proposer_concurrency = 1
        mock_settings.return_value.rule_proposer_max_retries = 1
        mock_settings.return_value.debug_dump_table_digests = False
        mock_settings.return_value.llm_provider = "openai"

        state = {"dataset_profile_digest": {"tables": {"t1": {}}}}
        result = await rule_proposer_node(state)

    assert result["rule_run_id"] != ""
    assert len(result["rule_run_id"]) == 32  # uuid4().hex


# ---------------------------------------------------------------------------
# 2. _stamp_rule backward compat và dedup
# ---------------------------------------------------------------------------

def test_stamp_rule_3_positional_args_backward_compat():
    """_stamp_rule(rule, table, run_id) 3 positional args vẫn chạy."""
    from src.agents.nodes.rule_proposer_node import _stamp_rule
    from src.models.rule_schemas import (
        DataQualityDimension,
        ProposedRule,
        RuleParameters,
        RuleType,
        Severity,
    )

    rule = ProposedRule(
        column="order_id",
        rule_type=RuleType.NOT_NULL,
        parameters=RuleParameters(),
        confidence_score=0.95,
        severity=Severity.CRITICAL,
        dimension=DataQualityDimension.COMPLETENESS,
        rule_description="Mã đơn hàng không được null.",
        ai_reasoning="null_pct = 0.0 trên 99441 dòng",
    )
    stamped = _stamp_rule(rule, "orders", "abc123")
    assert stamped["rule_id"] == "orders.order_id.NOT_NULL"
    assert stamped["status"] == "PENDING"


def test_stamp_rule_dedup_suffix():
    """_stamp_rule sinh suffix #2 khi 2 rule cùng (column, rule_type)."""
    from src.agents.nodes.rule_proposer_node import _stamp_rule
    from src.models.rule_schemas import (
        DataQualityDimension,
        ProposedRule,
        RuleParameters,
        RuleType,
        Severity,
    )

    def make_rule():
        return ProposedRule(
            column="amount",
            rule_type=RuleType.RANGE,
            parameters=RuleParameters(min=0.0, max=100.0),
            confidence_score=0.9,
            severity=Severity.HIGH,
            dimension=DataQualityDimension.VALIDITY,
            rule_description="Amount phải từ 0 đến 100.",
            ai_reasoning="range quan sát: [0, 100]",
        )

    used_ids: set[str] = set()
    s1 = _stamp_rule(make_rule(), "orders", "run1", used_ids)
    s2 = _stamp_rule(make_rule(), "orders", "run1", used_ids)

    assert s1["rule_id"] == "orders.amount.RANGE"
    assert s2["rule_id"] == "orders.amount.RANGE#2"
    assert s1["rule_id"] != s2["rule_id"]


# ---------------------------------------------------------------------------
# 3. save_proposed_rules — fields đầy đủ và idempotency
# ---------------------------------------------------------------------------

def test_save_proposed_rules_keeps_all_fields(in_memory_engine):
    """save_proposed_rules phải giữ đủ dimension, rule_description, rule_id."""
    from src.services.rule_store import ProposedRuleModel, save_proposed_rules

    run_id = uuid.uuid4().hex
    rule = _make_rule_dict(run_id, "t.col_a.NOT_NULL", dimension="COMPLETENESS")
    n = save_proposed_rules(run_id, "ds1", [rule])
    assert n == 1

    with Session(in_memory_engine) as s:
        row = s.get(ProposedRuleModel, (run_id, "t.col_a.NOT_NULL"))
    assert row is not None
    assert row.dimension == "COMPLETENESS"
    assert row.rule_description == "Cột vendor_id không được để trống."
    assert row.ai_reasoning == "Profiler xác nhận null_pct = 0.0 trên toàn bộ dữ liệu."


def test_save_proposed_rules_idempotent(in_memory_engine):
    """save_proposed_rules gọi 2 lần cùng run_id → không IntegrityError, không nhân đôi row."""
    from src.services.rule_store import list_rules, save_proposed_rules

    run_id = uuid.uuid4().hex
    rule = _make_rule_dict(run_id, "t.col_a.NOT_NULL")

    save_proposed_rules(run_id, "ds1", [rule])
    save_proposed_rules(run_id, "ds1", [rule])  # gọi lần 2

    rows = list_rules(run_id=run_id)
    assert len(rows) == 1  # không nhân đôi


def test_two_runs_same_rule_id_coexist(in_memory_engine):
    """Hai rule cùng rule_id khác run_id cùng tồn tại (chứng minh PK ghép đúng)."""
    from src.services.rule_store import list_rules, save_proposed_rules

    run_a = uuid.uuid4().hex
    run_b = uuid.uuid4().hex
    rule_id = "t.col_a.NOT_NULL"

    save_proposed_rules(run_a, "ds1", [_make_rule_dict(run_a, rule_id)])
    save_proposed_rules(run_b, "ds1", [_make_rule_dict(run_b, rule_id)])

    assert len(list_rules(run_id=run_a)) == 1
    assert len(list_rules(run_id=run_b)) == 1


# ---------------------------------------------------------------------------
# 4. hitl_gate_node
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hitl_gate_node_returns_rules_saved(in_memory_engine, tmp_path):
    """hitl_gate_node trả rules_saved chính xác."""
    from src.agents.nodes.hitl_gate_node import hitl_gate_node

    run_id = uuid.uuid4().hex
    rules = [
        _make_rule_dict(run_id, "t.col_a.NOT_NULL"),
        _make_rule_dict(run_id, "t.col_b.NOT_NULL", column="col_b"),
    ]
    state = {
        "dataset_id": "ds1",
        "rule_run_id": run_id,
        "proposed_rules": rules,
        "metadata": {},
    }

    with patch("src.agents.nodes.hitl_gate_node.get_settings") as mock_s:
        mock_s.return_value.results_dir = str(tmp_path)
        mock_s.return_value.output_dir = str(tmp_path)
        result = await hitl_gate_node(state)

    assert result["metadata"]["rules_saved"] == 2
    assert result["metadata"]["hitl_status"] == "AWAITING_REVIEW"
    assert (tmp_path / "hitl" / f"proposed_rules_{run_id}.json").exists()
    assert result["metadata"]["trace_path"] == str(tmp_path / "hitl" / f"proposed_rules_{run_id}.json")


@pytest.mark.asyncio
async def test_hitl_gate_node_does_not_fail_when_trace_dir_missing(in_memory_engine, tmp_path):
    """hitl_gate_node không raise khi thư mục trace không ghi được."""
    from src.agents.nodes.hitl_gate_node import hitl_gate_node

    run_id = uuid.uuid4().hex
    rules = [_make_rule_dict(run_id, "t.col_a.NOT_NULL")]
    state = {
        "dataset_id": "ds1",
        "rule_run_id": run_id,
        "proposed_rules": rules,
        "metadata": {},
    }

    with (
        patch("src.agents.nodes.hitl_gate_node.get_settings") as mock_s,
        patch("pathlib.Path.mkdir", side_effect=PermissionError("Mocked Permission Denied")),
    ):
        mock_s.return_value.results_dir = str(tmp_path / "protected_dir")
        mock_s.return_value.output_dir = str(tmp_path / "protected_dir")
        result = await hitl_gate_node(state)

    # Phải không raise, rules_saved vẫn đúng
    assert result["metadata"]["rules_saved"] == 1
    assert result["metadata"]["hitl_status"] == "AWAITING_REVIEW"
    assert result["metadata"]["trace_path"] is None


# ---------------------------------------------------------------------------
# 5. review_rule
# ---------------------------------------------------------------------------

def test_review_rule_sets_status_and_keeps_params(in_memory_engine):
    """review_rule → status, reviewer, reviewed_at được set; parameters gốc không đổi."""
    from src.services.rule_store import review_rule, save_proposed_rules

    run_id = uuid.uuid4().hex
    rule_id = "t.col_a.NOT_NULL"
    original_params = {}

    save_proposed_rules(run_id, "ds1", [_make_rule_dict(run_id, rule_id)])
    result = review_rule(
        run_id=run_id,
        rule_id=rule_id,
        status="APPROVED",
        reviewer="steward@test.vn",
    )

    assert result["status"] == "APPROVED"
    assert result["reviewer"] == "steward@test.vn"
    assert result["reviewed_at"] is not None
    assert result["parameters"] == original_params  # gốc không đổi


def test_review_rule_edited_parameters(in_memory_engine):
    """review_rule với edited_parameters → effective_parameters dùng bản edit."""
    from src.services.rule_store import review_rule, save_proposed_rules

    run_id = uuid.uuid4().hex
    rule_id = "t.amount.RANGE"
    original = _make_rule_dict(
        run_id, rule_id,
        rule_type="RANGE",
        column="amount",
        dimension="VALIDITY",
    )
    original["parameters"] = {"min": 0.0, "max": 100.0}
    save_proposed_rules(run_id, "ds1", [original])

    result = review_rule(
        run_id=run_id,
        rule_id=rule_id,
        status="APPROVED",
        edited_parameters={"min": 0.0, "max": 200.0},
    )

    assert result["effective_parameters"] == {"min": 0.0, "max": 200.0}
    assert result["parameters"] == {"min": 0.0, "max": 100.0}  # gốc không đổi


def test_review_rule_not_found_returns_none(in_memory_engine):
    """review_rule với rule_id không tồn tại → trả None."""
    from src.services.rule_store import review_rule

    result = review_rule(
        run_id="nonexistent_run",
        rule_id="nonexistent.rule.NOT_NULL",
        status="APPROVED",
    )
    assert result is None


# ---------------------------------------------------------------------------
# 6. bulk_review
# ---------------------------------------------------------------------------

def test_bulk_review_returns_not_found(in_memory_engine):
    """bulk_review trả đúng not_found cho id sai."""
    from src.services.rule_store import bulk_review, save_proposed_rules

    run_id = uuid.uuid4().hex
    rule_id = "t.col_a.NOT_NULL"
    save_proposed_rules(run_id, "ds1", [_make_rule_dict(run_id, rule_id)])

    updated, not_found = bulk_review(
        run_id,
        [
            {"rule_id": rule_id, "status": "APPROVED"},
            {"rule_id": "nonexistent.rule", "status": "APPROVED"},
        ],
    )
    assert len(updated) == 1
    assert "nonexistent.rule" in not_found


# ---------------------------------------------------------------------------
# 7. get_review_summary
# ---------------------------------------------------------------------------

def test_get_review_summary(in_memory_engine):
    """get_review_summary khớp số đúng."""
    from src.services.rule_store import get_review_summary, review_rule, save_proposed_rules

    run_id = uuid.uuid4().hex
    rules = [
        _make_rule_dict(run_id, "t.col_a.NOT_NULL", dimension="COMPLETENESS", severity="CRITICAL"),
        _make_rule_dict(run_id, "t.col_b.NOT_NULL", column="col_b", dimension="COMPLETENESS", severity="HIGH"),
        _make_rule_dict(run_id, "t.col_c.RANGE", column="col_c", rule_type="RANGE", dimension="VALIDITY", severity="HIGH"),
    ]
    save_proposed_rules(run_id, "ds1", rules)

    # Approve 1, reject 1
    review_rule(run_id=run_id, rule_id="t.col_a.NOT_NULL", status="APPROVED", reviewer="s@t.vn")
    review_rule(run_id=run_id, rule_id="t.col_b.NOT_NULL", status="REJECTED", review_note="lý do")

    summary = get_review_summary(run_id)
    assert summary["total"] == 3
    assert summary["approved"] == 1
    assert summary["rejected"] == 1
    assert summary["pending"] == 1
    assert summary["is_complete"] is False

    # Approve rule còn lại
    review_rule(run_id=run_id, rule_id="t.col_c.RANGE", status="APPROVED")
    summary2 = get_review_summary(run_id)
    assert summary2["is_complete"] is True


# ---------------------------------------------------------------------------
# 8. edited_parameters guardrail
# ---------------------------------------------------------------------------

def test_edited_parameters_invalid_raises_value_error(in_memory_engine):
    """edited_parameters vô lý (RANGE thiếu cả min/max) → ValueError."""
    from src.services.rule_store import review_rule, save_proposed_rules

    run_id = uuid.uuid4().hex
    rule_id = "t.amount.RANGE"
    rule = _make_rule_dict(run_id, rule_id, rule_type="RANGE", column="amount", dimension="VALIDITY")
    rule["parameters"] = {"min": 0.0, "max": 100.0}
    save_proposed_rules(run_id, "ds1", [rule])

    with pytest.raises(ValueError, match="không hợp lệ"):
        review_rule(
            run_id=run_id,
            rule_id=rule_id,
            status="APPROVED",
            edited_parameters={},  # RANGE không có min hay max → validator lỗi
        )
