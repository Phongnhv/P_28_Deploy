"""Hand-written compiler cases, kept as tests rather than as an evaluator.

These used to be ``sql_compilation_probe_v1``'s whole content and awarded the
``ai_quality`` gate 100 points on every run without reading a single rule the agent had
proposed. They still earn their place -- an identifier-quoting regression is worth
catching -- but as a statement about ``test_generator_node``, which is EvalGate's own
test suite's business. The evaluator now compiles the bundle's real proposals.
"""

from __future__ import annotations

import pytest

tgn = pytest.importorskip("src.agents.nodes.test_generator_node")


@pytest.mark.parametrize(
    ("raw", "dialect", "expected"),
    [
        ("fare_amount", "sqlite", '"fare_amount"'),
        ('weird"column', "sqlite", '"weird""column"'),
    ],
)
def test_identifiers_are_quoted_and_embedded_quotes_escaped(raw, dialect, expected):
    assert tgn._quote_ident(raw, dialect) == expected


def test_an_injection_shaped_identifier_stays_inside_quotes():
    quoted = tgn._quote_ident("order; DROP TABLE users;--", "postgresql")
    assert '"order; DROP TABLE users;--"' in quoted


def test_not_null_compiles_without_binds():
    predicate, binds = tgn._build_row_predicate(
        {"rule_type": "NOT_NULL", "column": "user_id"}, 1, "sqlite"
    )
    assert '"user_id" IS NULL' in predicate
    assert len(binds) == 0


def test_range_binds_both_bounds():
    predicate, binds = tgn._build_row_predicate(
        {"rule_type": "RANGE", "column": "age", "parameters": {"min": 0, "max": 120}},
        2, "sqlite",
    )
    assert "p_min_2" in binds and "p_max_2" in binds
    assert "OR" in predicate


def test_accepted_values_binds_every_literal():
    predicate, binds = tgn._build_row_predicate(
        {"rule_type": "ACCEPTED_VALUES", "column": "status",
         "parameters": {"accepted_values": ["A", "B"]}},
        3, "sqlite",
    )
    assert "NOT IN" in predicate
    assert len(binds) == 2


@pytest.mark.parametrize(
    ("dialect", "operator"), [("sqlite", "NOT REGEXP"), ("postgresql", "!~")]
)
def test_regex_uses_the_dialect_operator(dialect, operator):
    predicate, _ = tgn._build_row_predicate(
        {"rule_type": "REGEX_FORMAT", "column": "email",
         "parameters": {"regex": "^[a-z]+@[a-z]+\\.[a-z]+$"}},
        4, dialect,
    )
    assert operator in predicate


def test_cross_field_comparison_names_both_columns():
    predicate, _ = tgn._build_row_predicate(
        {"rule_type": "CROSS_FIELD_COMPARISON", "column": "start_date",
         "parameters": {"target_column": "end_date", "operator": "<="}},
        6, "sqlite",
    )
    assert '"start_date"' in predicate and '"end_date"' in predicate
