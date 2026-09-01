"""Evaluator for SQL and predicate compilation correctness and security.

Validates:
1. Compilation of all canonical RuleTypes to SQL predicates
2. Identifier quoting and SQL injection prevention
3. Value parameter binding (:bind_param) across dialects
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evalgate.normalizers import normalizers as norm
from evalgate.schemas.eval_result import (
    EvalResult,
    EvalStatus,
    Evidence,
    MetricValue,
)
from src.agents.nodes import test_generator_node as tgn

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = PROJECT_ROOT / "evalgate" / "evidence" / "gate1"

GATE = "ai_quality"
EVALUATOR = "sql_compilation_probe_v1"


def test_quote_ident() -> dict[str, bool]:
    """Ensure identifier quoting handles standard, weird, and malicious column names."""
    q1 = tgn._quote_ident("fare_amount", "sqlite")
    q2 = tgn._quote_ident('weird"column', "sqlite")
    q3 = tgn._quote_ident("order; DROP TABLE users;--", "postgresql")

    return {
        "standard_identifier_quoted": q1 == '"fare_amount"',
        "embedded_quotes_escaped": q2 == '"weird""column"',
        "injection_identifier_safely_contained": '"order; DROP TABLE users;--"' in q3,
    }


def test_row_predicate_compilation() -> dict[str, Any]:
    """Ensure all core rule types compile to parameterized SQL predicates."""
    results: dict[str, bool] = {}

    # 1. NOT_NULL
    p_not_null, b_not_null = tgn._build_row_predicate(
        {"rule_type": "NOT_NULL", "column": "user_id"}, 1, "sqlite"
    )
    results["not_null_predicate"] = '"user_id" IS NULL' in p_not_null and len(b_not_null) == 0

    # 2. RANGE
    p_range, b_range = tgn._build_row_predicate(
        {"rule_type": "RANGE", "column": "age", "parameters": {"min": 0, "max": 120}},
        2, "sqlite"
    )
    results["range_predicate"] = "p_min_2" in b_range and "p_max_2" in b_range and "OR" in p_range

    # 3. ACCEPTED_VALUES
    p_enum, b_enum = tgn._build_row_predicate(
        {"rule_type": "ACCEPTED_VALUES", "column": "status", "parameters": {"accepted_values": ["A", "B"]}},
        3, "sqlite"
    )
    results["accepted_values_predicate"] = "NOT IN" in p_enum and len(b_enum) == 2

    # 4. REGEX_FORMAT
    p_regex_sqlite, b_regex_sqlite = tgn._build_row_predicate(
        {"rule_type": "REGEX_FORMAT", "column": "email", "parameters": {"regex": "^[a-z]+@[a-z]+\\.[a-z]+$"}},
        4, "sqlite"
    )
    results["regex_sqlite_predicate"] = "NOT REGEXP" in p_regex_sqlite and "p_regex_4" in b_regex_sqlite

    p_regex_pg, _ = tgn._build_row_predicate(
        {"rule_type": "REGEX_FORMAT", "column": "email", "parameters": {"regex": "^[a-z]+$"}},
        5, "postgresql"
    )
    results["regex_pg_predicate"] = "!~" in p_regex_pg

    # 5. CROSS_FIELD_COMPARISON
    p_cross, _ = tgn._build_row_predicate(
        {
            "rule_type": "CROSS_FIELD_COMPARISON",
            "column": "start_date",
            "parameters": {"target_column": "end_date", "operator": "<="},
        },
        6, "sqlite"
    )
    results["cross_field_predicate"] = '"start_date"' in p_cross and '"end_date"' in p_cross

    return results


def evaluate(*, write_evidence: bool = True) -> EvalResult:
    quote_results = test_quote_ident()
    pred_results = test_row_predicate_compilation()

    all_passed = all(quote_results.values()) and all(pred_results.values())
    total_checks = len(quote_results) + len(pred_results)
    passed_checks = sum(1 for v in quote_results.values() if v) + sum(1 for v in pred_results.values() if v)
    score = (passed_checks / total_checks) * 100.0

    metrics = {
        "identifier_quoting_safety": MetricValue(
            raw=all(quote_results.values()),
            unit="boolean",
            normalized=norm.boolean(all(quote_results.values())),
        ),
        "predicate_compilation_coverage": MetricValue(
            raw=all(pred_results.values()),
            unit="boolean",
            normalized=norm.boolean(all(pred_results.values())),
        ),
        "sql_compilation_score": MetricValue(
            raw=score,
            unit="ratio",
            normalized=score,
        ),
    }

    evidence: list[Evidence] = []
    if write_evidence:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        target = EVIDENCE_DIR / "sql_compilation_probe.json"
        target.write_text(
            json.dumps({"quoting": quote_results, "predicates": pred_results}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        evidence.append(Evidence(type="file", path=str(target.relative_to(PROJECT_ROOT))))

    return EvalResult(
        gate=GATE,
        evaluator=EVALUATOR,
        status=EvalStatus.PASS if all_passed else EvalStatus.FAIL,
        score=score,
        metrics=metrics,
        evidence=evidence,
        metadata={
            "tested_rules": ["NOT_NULL", "RANGE", "ACCEPTED_VALUES", "REGEX_FORMAT", "CROSS_FIELD_COMPARISON"],
        },
    )
