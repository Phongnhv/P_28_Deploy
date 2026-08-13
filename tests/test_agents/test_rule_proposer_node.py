"""Tests cho Rule Proposer: failure isolation, retry, schema validation, digest split.

Pattern: AsyncMock, following tests/test_profiler.py conventions.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from src.agents.tools.profile_digest import split_digest_by_table
from src.models.rule_schemas import (
    DataQualityDimension,
    ProposedRule,
    RuleParameters,
    RuleType,
    Severity,
    TableRuleProposal,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_table_proposal(table_name: str, n_rules: int = 2) -> TableRuleProposal:
    """Tạo TableRuleProposal hợp lệ để dùng trong mock."""
    rules = [
        ProposedRule(
            column=f"col_{i}",
            rule_type=RuleType.NOT_NULL,
            parameters=RuleParameters(),
            confidence_score=0.9,
            severity=Severity.HIGH,
            dimension=DataQualityDimension.COMPLETENESS,
            rule_description=f"Cột col_{i} không được rỗng.",
            ai_reasoning=f"null_pct = 0.0 trên 1000 dòng (col_{i})",
        )
        for i in range(n_rules)
    ]
    return TableRuleProposal(table=table_name, rules=rules)


def _make_digest(tables: list[str]) -> dict:
    """Tạo digest giả với các bảng cho trước."""
    digest = {}
    for t in tables:
        digest[t] = {
            "table": t,
            "rows": 100,
            "sample": {"rate": 1.0, "n": 100},
            "columns": [
                {"name": "id", "type": "INTEGER", "role": "id", "null_pct": 0.0,
                 "signals": ["no_nulls", "unique_in_sample"]},
                {"name": "value", "type": "REAL", "role": "numeric", "null_pct": 5.0,
                 "range": [0.0, 100.0]},
            ],
        }
    return digest


# ---------------------------------------------------------------------------
# 1. split_digest_by_table — tách digest và bỏ qua bảng lỗi
# ---------------------------------------------------------------------------

def test_split_digest_by_table_basic():
    """Kiểm tra tách đúng số bảng và không bị thừa bảng lỗi."""
    digest = _make_digest(["orders", "customers"])
    digest["broken_table"] = {"error": "connection timeout"}

    result = split_digest_by_table(digest)

    assert "orders" in result
    assert "customers" in result
    assert "broken_table" not in result
    assert len(result) == 2


def test_split_digest_by_table_unwrap_key():
    """Tự động bỏ bọc dataset_profile_digest key (dạng file debug)."""
    inner_digest = _make_digest(["orders"])
    wrapped = {"dataset_profile_digest": inner_digest}

    result = split_digest_by_table(wrapped)

    assert "orders" in result
    assert "dataset_profile_digest" not in result


def test_split_digest_by_table_empty():
    """Trả về dict rỗng khi đầu vào None hoặc rỗng."""
    assert split_digest_by_table({}) == {}
    assert split_digest_by_table(None) == {}  # type: ignore[arg-type]


def test_build_coverage_requirements_uses_structured_cross_field_parameters():
    """Checklist phải map cross-column hint đúng vào RuleParameters."""
    from src.agents.nodes.rule_proposer_node import _build_coverage_requirements

    digest = {
        "rows": 50_000,
        "cross_column_hints": [
            {
                "type": "datetime_order",
                "columns": ["pickup_at", "dropoff_at"],
                "violation_pct": 0.0,
            },
            {
                "type": "unsupported_relation",
                "columns": ["pickup_at", "dropoff_at"],
            },
            {
                "type": "datetime_order",
                "columns": ["pickup_at", "missing_column"],
            },
        ],
        "columns": [
            {
                "name": "pickup_at",
                "role": "datetime",
                "null_pct": 0.0,
                "range": ["2025-01-01", "2025-01-31"],
                "signals": ["no_nulls"],
            },
            {
                "name": "dropoff_at",
                "role": "datetime",
                "null_pct": 0.0,
                "range": ["2025-01-01", "2025-01-31"],
                "signals": ["no_nulls"],
            },
            {
                "name": "payment_type",
                "role": "categorical",
                "null_pct": 0.0,
                "values": ["Cash", "Credit card"],
                "signals": ["no_nulls", "low_cardinality"],
            },
            {
                "name": "fare_amount",
                "role": "numeric",
                "null_pct": 0.0,
                "signals": ["has_negative_values", "has_extreme_outliers"],
            },
        ],
    }

    requirements = _build_coverage_requirements(digest)
    cross_field_requirements = [
        item
        for item in requirements
        if item["rule_type"] == "CROSS_FIELD_COMPARISON"
    ]

    assert len(cross_field_requirements) == 1
    assert cross_field_requirements[0]["column"] == "pickup_at"
    assert cross_field_requirements[0]["parameters"] == {
        "target_column": "dropoff_at",
        "operator": "<=",
    }
    assert "target_column" not in cross_field_requirements[0]


# ---------------------------------------------------------------------------
# 2. Schema validation guardrails
# ---------------------------------------------------------------------------

def test_range_rule_requires_min_or_max():
    """RANGE không có min/max phải raise ValidationError."""
    with pytest.raises(ValidationError) as exc_info:
        ProposedRule(
            column="price",
            rule_type=RuleType.RANGE,
            parameters=RuleParameters(),  # min=None, max=None
            confidence_score=0.8,
            severity=Severity.HIGH,
            dimension=DataQualityDimension.VALIDITY,
            rule_description="Giá tiền phải nằm trong khoảng hợp lệ.",
            ai_reasoning="range quan sát: [0, 100]",
        )
    errors = exc_info.value.errors()
    assert any("RANGE" in str(e) for e in errors)


def test_accepted_values_requires_non_empty_list():
    """ACCEPTED_VALUES với list rỗng phải raise ValidationError."""
    with pytest.raises(ValidationError):
        ProposedRule(
            column="status",
            rule_type=RuleType.ACCEPTED_VALUES,
            parameters=RuleParameters(accepted_values=[]),
            confidence_score=0.9,
            severity=Severity.MEDIUM,
            dimension=DataQualityDimension.VALIDITY,
            rule_description="Trạng thái phải thuộc tập giá trị cho phép.",
            ai_reasoning="role categorical, 3 giá trị distinct",
        )


def test_regex_format_requires_regex():
    """REGEX_FORMAT không có regex phải raise ValidationError."""
    with pytest.raises(ValidationError):
        ProposedRule(
            column="email",
            rule_type=RuleType.REGEX_FORMAT,
            parameters=RuleParameters(regex=None),
            confidence_score=0.7,
            severity=Severity.LOW,
            dimension=DataQualityDimension.VALIDITY,
            rule_description="Email phải đúng định dạng.",
            ai_reasoning="cột email cần kiểm tra format",
        )


def test_valid_range_rule():
    """RANGE với min hợp lệ không được raise lỗi."""
    rule = ProposedRule(
        column="price",
        rule_type=RuleType.RANGE,
        parameters=RuleParameters(min=0.0, max=110.0),
        confidence_score=0.85,
        severity=Severity.HIGH,
        dimension=DataQualityDimension.VALIDITY,
        rule_description="Cột price phải từ 0 đến 110.",
        ai_reasoning="range quan sát: [0, 100], pad 10%",
    )
    assert rule.parameters.min == 0.0
    assert rule.parameters.max == 110.0


# ---------------------------------------------------------------------------
# 3. Failure isolation — 1 bảng fail không ảnh hưởng bảng khác
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_failure_isolation():
    """Khi 1 table LLM raise exception, các table còn lại vẫn có rules.

    Bảng lỗi phải xuất hiện trong rule_proposal_errors, không trong proposed_rules.
    """
    tables = ["orders", "customers", "drivers"]
    digest = _make_digest(tables)

    proposals = {
        "orders": _make_table_proposal("orders", n_rules=2),
        "customers": Exception("LLM timeout"),
        "drivers": _make_table_proposal("drivers", n_rules=2),
    }

    async def mock_ainvoke(messages):
        # Cần biết bảng nào đang được call — trích table_name từ message
        content = str(messages)
        for t in tables:
            if f"`{t}`" in content:
                resp = proposals[t]
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise ValueError("Unknown table in mock")

    mock_structured_llm = AsyncMock()
    mock_structured_llm.ainvoke.side_effect = mock_ainvoke

    with (
        patch("src.agents.nodes.rule_proposer_node.get_llm") as mock_get_llm,
        patch(
            "src.agents.nodes.rule_proposer_node.split_digest_by_table",
            return_value={t: digest[t] for t in tables},
        ),
    ):
        mock_llm_instance = MagicMock()
        mock_llm_instance.with_structured_output.return_value = mock_structured_llm
        mock_get_llm.return_value = mock_llm_instance

        from src.agents.nodes.rule_proposer_node import rule_proposer_node

        state = {
            "dataset_profile_digest": digest,
            "dataset_id": "test",
            "metadata": {},
        }
        result = await rule_proposer_node(state)

    # Bảng orders và drivers phải có rules
    rule_tables = {r["table_name"] for r in result["proposed_rules"]}
    assert "orders" in rule_tables
    assert "drivers" in rule_tables

    # Bảng customers phải nằm trong errors
    assert result["rule_proposal_errors"]
    error_tables = {e["table"] for e in result["rule_proposal_errors"]}
    assert "customers" in error_tables

    # customers không được có rule
    assert "customers" not in rule_tables


# ---------------------------------------------------------------------------
# 4. Retry test — raise 2 lần rồi succeed → 3 lần gọi, kết quả thành công
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retry_on_failure():
    """LLM raise 2 lần liên tiếp rồi trả về kết quả hợp lệ lần 3."""
    table_name = "orders"
    digest = _make_digest([table_name])

    success_proposal = _make_table_proposal(table_name, n_rules=2)
    call_count = {"n": 0}

    async def flaky_ainvoke(messages):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise RuntimeError(f"Simulated LLM error attempt {call_count['n']}")
        return success_proposal

    mock_structured_llm = AsyncMock()
    mock_structured_llm.ainvoke.side_effect = flaky_ainvoke

    with (
        patch("src.agents.nodes.rule_proposer_node.get_llm") as mock_get_llm,
        patch("src.agents.nodes.rule_proposer_node.split_digest_by_table",
              return_value={table_name: digest[table_name]}),
        patch("src.agents.nodes.rule_proposer_node.asyncio.sleep", new_callable=AsyncMock),
    ):
        mock_llm_instance = MagicMock()
        mock_llm_instance.with_structured_output.return_value = mock_structured_llm
        mock_get_llm.return_value = mock_llm_instance

        from src.agents.nodes.rule_proposer_node import rule_proposer_node

        state = {
            "dataset_profile_digest": digest,
            "dataset_id": "test",
            "metadata": {},
        }
        result = await rule_proposer_node(state)

    # 3 lần ainvoke được gọi (attempt 1 fail, 2 fail, 3 succeed)
    assert call_count["n"] == 3

    # Phải có rules, không có errors
    assert result["proposed_rules"]
    assert not result["rule_proposal_errors"]


# ---------------------------------------------------------------------------
# 5. rule_id stamping
# ---------------------------------------------------------------------------

def test_stamp_rule_creates_correct_id():
    """rule_id phải theo format table.column.RULE_TYPE."""
    from src.agents.nodes.rule_proposer_node import _stamp_rule

    rule = ProposedRule(
        column="order_id",
        rule_type=RuleType.NOT_NULL,
        parameters=RuleParameters(),
        confidence_score=0.95,
        severity=Severity.CRITICAL,
        dimension=DataQualityDimension.COMPLETENESS,
        rule_description="Cột order_id không được null.",
        ai_reasoning="null_pct = 0.0 trên 99441 dòng",
    )
    stamped = _stamp_rule(rule, "orders", "abc123")

    assert stamped["rule_id"] == "orders.order_id.NOT_NULL"
    assert stamped["run_id"] == "abc123"
    assert stamped["table_name"] == "orders"
    assert stamped["column"] == "order_id"


def test_stamp_rule_table_level():
    """ROW_COUNT rule (column=None) dùng '_table' trong rule_id."""
    from src.agents.nodes.rule_proposer_node import _stamp_rule

    rule = ProposedRule(
        column=None,
        rule_type=RuleType.ROW_COUNT,
        parameters=RuleParameters(min_row_count=1000),
        confidence_score=0.8,
        severity=Severity.MEDIUM,
        dimension=DataQualityDimension.VALIDITY,
        rule_description="Bảng orders phải có ít nhất 1000 dòng.",
        ai_reasoning="bảng có 99441 dòng, đặt min_row_count=1000",
    )
    stamped = _stamp_rule(rule, "orders", "run999")

    assert stamped["rule_id"] == "orders._table.ROW_COUNT"
    assert stamped["column"] is None


def test_parse_and_stamp_cross_field_comparison_from_llm_response():
    """Parse payload LLM và stamp đúng rule_id cho rule so sánh liên cột."""
    from src.agents.nodes.rule_proposer_node import _stamp_rule

    llm_response = {
        "table": "yellow_taxi_trips",
        "rules": [
            {
                "column": "tpep_pickup_datetime",
                "rule_type": "CROSS_FIELD_COMPARISON",
                "parameters": {
                    "target_column": "tpep_dropoff_datetime",
                    "operator": "<=",
                },
                "confidence_score": 0.98,
                "severity": "CRITICAL",
                "dimension": "CONSISTENCY",
                "rule_description": (
                    "Thời điểm đón khách phải xảy ra trước hoặc cùng lúc với "
                    "thời điểm trả khách."
                ),
                "ai_reasoning": (
                    "Digest có datetime_order và nghiệp vụ yêu cầu đón khách "
                    "trước khi trả khách."
                ),
            },
            {
                "column": "tpep_pickup_datetime",
                "rule_type": "NOT_NULL",
                "parameters": {},
                "confidence_score": 0.95,
                "severity": "CRITICAL",
                "dimension": "COMPLETENESS",
                "rule_description": "Thời điểm đón khách phải luôn có giá trị.",
                "ai_reasoning": "Digest xác nhận cột thời điểm đón không có giá trị thiếu.",
            },
        ],
    }

    proposal = TableRuleProposal.model_validate(llm_response)
    rule = proposal.rules[0]
    stamped = _stamp_rule(rule, proposal.table, "cross-field-run")

    assert rule.rule_type == RuleType.CROSS_FIELD_COMPARISON
    assert rule.parameters.target_column == "tpep_dropoff_datetime"
    assert rule.parameters.operator == "<="
    assert stamped["rule_id"] == (
        "yellow_taxi_trips.tpep_pickup_datetime.VS."
        "tpep_dropoff_datetime.CROSS_FIELD_COMPARISON"
    )
    assert stamped["parameters"] == {
        "target_column": "tpep_dropoff_datetime",
        "operator": "<=",
    }

    invalid_rule = dict(llm_response["rules"][0])
    invalid_rule["target_column"] = "tpep_dropoff_datetime"
    with pytest.raises(ValidationError):
        TableRuleProposal.model_validate({
            "table": llm_response["table"],
            "rules": [invalid_rule, llm_response["rules"][1]],
        })

    with pytest.raises(ValidationError):
        RuleParameters(
            target_column="tpep_dropoff_datetime",
            operator="UNSAFE_OPERATOR",
        )
