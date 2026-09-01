"""Golden assertion handlers package."""

from __future__ import annotations

from collections.abc import Callable

from evalgate.gates.gate1_ai_quality.golden_handlers.tier1_sdih import (
    _max_false_positive_rate,
    _min_violations,
    _must_abstain,
)
from evalgate.gates.gate1_ai_quality.golden_handlers.tier2_rules import (
    _confidence_monotonic,
    _enum_from_policy,
    _evidence_metric_exists,
    _evidence_references_metric,
    _evidence_refs,
    _no_rules_on_tables,
    _nullable_expected_is,
    _parameter_bound,
    _params,
    _relationship_declared,
    _rule_not_on_columns,
    _rule_proposed,
    _semantic_type_is,
    _severity_ranks_above,
    _target_columns,
)
from evalgate.gates.gate1_ai_quality.golden_handlers.tier3_llm import (
    _forbidden_tokens,
    _must_cite_numbers,
    _must_verify_before_asserting,
    _tools_were_used,
)
from evalgate.gates.gate1_ai_quality.golden_handlers.types import (
    AssertionOutcome,
    CaseOutcome,
    HandlerContext,
)
from evalgate.golden.schema import Assertion

_HANDLERS: dict[str, Callable[[Assertion, HandlerContext], AssertionOutcome]] = {
    "tools_were_used": _tools_were_used,
    "must_verify_before_asserting": _must_verify_before_asserting,
    "semantic_type_is": _semantic_type_is,
    "nullable_expected_is": _nullable_expected_is,
    "relationship_declared": _relationship_declared,
    "evidence_metric_exists": _evidence_metric_exists,
    "evidence_references_metric": _evidence_references_metric,
    "severity_ranks_above": _severity_ranks_above,
    "confidence_monotonic": _confidence_monotonic,
    "max_false_positive_rate": _max_false_positive_rate,
    "must_abstain": _must_abstain,
    "rule_proposed": _rule_proposed,
    "rule_not_on_columns": _rule_not_on_columns,
    "enum_from_policy": _enum_from_policy,
    "parameter_bound": _parameter_bound,
    "no_rules_on_tables": _no_rules_on_tables,
    "min_violations": _min_violations,
    "forbidden_tokens": _forbidden_tokens,
    "must_cite_numbers": _must_cite_numbers,
}

__all__ = [
    "AssertionOutcome",
    "CaseOutcome",
    "HandlerContext",
    "_HANDLERS",
    "_confidence_monotonic",
    "_enum_from_policy",
    "_evidence_metric_exists",
    "_evidence_references_metric",
    "_evidence_refs",
    "_forbidden_tokens",
    "_max_false_positive_rate",
    "_min_violations",
    "_must_abstain",
    "_must_cite_numbers",
    "_must_verify_before_asserting",
    "_no_rules_on_tables",
    "_nullable_expected_is",
    "_parameter_bound",
    "_params",
    "_relationship_declared",
    "_rule_not_on_columns",
    "_rule_proposed",
    "_semantic_type_is",
    "_severity_ranks_above",
    "_target_columns",
    "_tools_were_used",
]
