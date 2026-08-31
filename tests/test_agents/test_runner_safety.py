"""Tests cho chốt chặn an toàn và hợp đồng dữ liệu của test_runner_node."""

import pytest

from src.agents.nodes.dbt_validation import DBT_PARSE_SKIPPED, dbt_parse_was_skipped
from src.agents.nodes.test_runner_node import (
    SAMPLE_FAILURE_LIMIT,
    _assert_safe_predicate,
)


def test_unsafe_predicate_raises_not_asserts():
    """Chốt chặn phải dùng raise: assert bị Python xoá khi chạy với cờ -O."""
    for payload in ("col = 1; DROP TABLE t", "col = 1 -- comment", "col /* x */ = 1"):
        with pytest.raises(ValueError, match="Security violation"):
            _assert_safe_predicate(payload)


def test_safe_predicate_passes():
    _assert_safe_predicate('"fare_amount" < :p_min_0')


def test_sample_limit_matches_supabase_path():
    """Hai pipeline phải dùng chung một giới hạn số ID vi phạm."""
    import inspect

    from src.services.supabase_dataset import execute_rule

    supabase_default = inspect.signature(execute_rule).parameters["failed_id_limit"].default
    assert SAMPLE_FAILURE_LIMIT == supabase_default


def test_skipped_dbt_parse_is_distinguishable():
    """Phải phân biệt được 'đã kiểm tra và đạt' với 'chưa hề kiểm tra'."""
    assert dbt_parse_was_skipped(f"{DBT_PARSE_SKIPPED}: dbt executable unavailable") is True
    assert dbt_parse_was_skipped("Found 3 models, 2 tests") is False
    assert dbt_parse_was_skipped(None) is False
