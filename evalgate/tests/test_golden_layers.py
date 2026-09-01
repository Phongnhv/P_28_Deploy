"""The layers beyond the final answer: interpretation, process, abstention.

A data-quality agent can be wrong in ways its output does not show. It can read a
currency column as plain numeric and lose the invariant that decides the threshold;
it can assert a rule it never checked against the data; it can answer NORMAL on a
first run where the only honest answer is "not enough history". None of those is
visible in the proposed rule itself, so each is asserted against the artefact that
does record it.
"""

from __future__ import annotations

import pytest

from evalgate.gates.gate1_ai_quality import golden_conformance as gc
from evalgate.golden.applicability import DatasetContext, Scope, SemanticColumn
from evalgate.golden.schema import LAYER_ORDER, Applicability, Assertion, GoldenCase


def _ctx(**kwargs) -> gc.HandlerContext:
    defaults = dict(
        rules=[], results=[], scope=Scope(columns=("amount",), reason="test"),
        dataset=DatasetContext(
            dataset_id="d",
            columns=("amount",),
            semantic=(SemanticColumn("amount", "currency", "transaction_amount", False),),
            profile_columns={"amount": {"min_value": -5.0}},
        ),
    )
    defaults.update(kwargs)
    return gc.HandlerContext(**defaults)


# --- interpretation --------------------------------------------------------

def test_semantic_type_mismatch_is_an_interpretation_failure() -> None:
    ctx = _ctx()
    out = gc._semantic_type_is(Assertion(type="semantic_type_is", semantic_type="numeric"), ctx)
    assert not out.passed and out.measurable


def test_semantic_type_match_passes() -> None:
    out = gc._semantic_type_is(Assertion(type="semantic_type_is", semantic_type="currency"), _ctx())
    assert out.passed


def test_no_contract_is_unmeasurable_not_failed() -> None:
    ctx = _ctx(dataset=DatasetContext(dataset_id="d", columns=("amount",)))
    out = gc._semantic_type_is(Assertion(type="semantic_type_is", semantic_type="currency"), ctx)
    assert not out.measurable


# --- process ---------------------------------------------------------------

def _tool(name: str) -> dict:
    return {"event": "tool_start", "tool": name}


def test_verification_is_recognised() -> None:
    ctx = _ctx(tool_events=[_tool("dry_run_rule_candidate"), _tool("inspect_data_samples")])
    out = gc._must_verify_before_asserting(
        Assertion(type="must_verify_before_asserting", verifying_tools=["dry_run_rule_candidate"]),
        ctx,
    )
    assert out.passed


def test_lookup_alone_is_not_verification() -> None:
    """An agent that only reads statistics is still guessing, with more context."""
    ctx = _ctx(tool_events=[_tool("inspect_data_samples"), _tool("inspect_semantic_metadata")])
    out = gc._must_verify_before_asserting(
        Assertion(type="must_verify_before_asserting", verifying_tools=["dry_run_rule_candidate"]),
        ctx,
    )
    assert not out.passed and out.measurable


def test_an_uninstrumented_run_is_unmeasurable_not_failed() -> None:
    """An empty trace means "no tools" or "not instrumented"; only one is the agent."""
    out = gc._must_verify_before_asserting(
        Assertion(type="must_verify_before_asserting"), _ctx(tool_events=[])
    )
    assert not out.measurable


def test_tool_use_counts_calls() -> None:
    ctx = _ctx(tool_events=[_tool("a"), _tool("b"), _tool("a")])
    assert gc._tools_were_used(Assertion(type="tools_were_used", min_calls=3), ctx).passed
    assert not gc._tools_were_used(Assertion(type="tools_were_used", min_calls=4), ctx).passed


# --- negative space: abstention --------------------------------------------

def test_abstention_on_cold_start_passes() -> None:
    ctx = _ctx(anomaly={"decision": "INSUFFICIENT_HISTORY", "status": "COMPLETED"})
    assert gc._must_abstain(Assertion(type="must_abstain"), ctx).passed


def test_claiming_normal_without_history_fails() -> None:
    ctx = _ctx(anomaly={"decision": "NORMAL", "status": "COMPLETED", "hypotheses": []})
    out = gc._must_abstain(Assertion(type="must_abstain"), ctx)
    assert not out.passed
    assert "NORMAL" in out.observed


def test_a_crashed_detector_is_reported_as_its_own_failure() -> None:
    """Failing to decide and deciding wrongly send a reader to different places."""
    ctx = _ctx(anomaly={"decision": "UNAVAILABLE", "status": "FAILED", "error": "boom"})
    out = gc._must_abstain(Assertion(type="must_abstain"), ctx)
    assert not out.passed
    assert "produced no decision" in out.observed and "boom" in out.observed


def test_missing_anomaly_report_is_unmeasurable() -> None:
    assert not gc._must_abstain(Assertion(type="must_abstain"), _ctx(anomaly={})).measurable


# --- attribution -----------------------------------------------------------

def _case(*assertions: Assertion) -> GoldenCase:
    return GoldenCase(
        id="C", tier=2, intent="i", source="s", ground_truth_owner="o",
        applies_to=Applicability(always=True), assertions=list(assertions),
    )


def test_attribution_blames_the_earliest_failing_layer() -> None:
    """A wrong reading produces a wrong rule; only the reading should be reported."""
    case = _case(
        Assertion(type="semantic_type_is", semantic_type="numeric"),   # interpretation, fails
        Assertion(type="rule_proposed", rule_type="RANGE", column="amount"),  # decision, fails
    )
    outcome = gc.run_case(
        case, [], [],
        scope=Scope(columns=("amount",), reason="test"),
        dataset=_ctx().dataset,
    )
    assert not outcome.passed
    assert outcome.failed_layer == "interpretation"


def test_attribution_reports_decision_when_the_reading_was_right() -> None:
    case = _case(
        Assertion(type="semantic_type_is", semantic_type="currency"),  # passes
        Assertion(type="rule_proposed", rule_type="RANGE", column="amount"),  # fails
    )
    outcome = gc.run_case(
        case, [], [],
        scope=Scope(columns=("amount",), reason="test"),
        dataset=_ctx().dataset,
    )
    assert outcome.failed_layer == "decision"


@pytest.mark.parametrize("layer", LAYER_ORDER)
def test_every_layer_is_causally_ordered(layer: str) -> None:
    """Attribution walks LAYER_ORDER, so the order has to be the causal one."""
    assert LAYER_ORDER.index("interpretation") < LAYER_ORDER.index("decision")
    assert LAYER_ORDER.index("process") < LAYER_ORDER.index("decision")
    assert LAYER_ORDER.index("evidence") < LAYER_ORDER.index("decision")
    assert LAYER_ORDER.index("decision") < LAYER_ORDER.index("negative_space")
