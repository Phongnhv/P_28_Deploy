"""SDIH and execution outcome assertion handlers."""

from __future__ import annotations

from evalgate.gates.gate1_ai_quality.golden_handlers.types import AssertionOutcome, HandlerContext
from evalgate.golden.schema import Assertion


def _min_violations(a: Assertion, ctx: HandlerContext) -> AssertionOutcome:
    results = ctx.results
    best = 0
    seen = False
    for entry in results:
        if not str(entry.get("rule_id", "")).endswith(a.rule_suffix or ""):
            continue
        seen = True
        best = max(best, int(entry.get("failed_count") or entry.get("violation_count") or 0))
    if not seen:
        return AssertionOutcome(
            a.type, False, f"no execution result for *{a.rule_suffix}", measurable=False
        )
    return AssertionOutcome(
        a.type, best >= (a.at_least or 0),
        f"best run flagged {best}, required {a.at_least}",
    )


def _max_false_positive_rate(a: Assertion, ctx: HandlerContext) -> AssertionOutcome:
    """Rows flagged that no label calls defective.

    SDIH injects at disjoint row positions, so every unlabelled row is known clean
    and the negative space needs no extra ground truth -- only the complement.
    """
    flagged: set[str] = set()
    for entry in ctx.results:
        for value in entry.get("violation_row_ids") or entry.get("sample_refs") or []:
            flagged.add(str(value))
    if not flagged:
        return AssertionOutcome(a.type, True, "nothing was flagged")
    truth = set(a.columns)  # populated by the caller from the label store
    if not truth:
        return AssertionOutcome(a.type, False, "no label set supplied", measurable=False)
    false_positives = flagged - truth
    rate = len(false_positives) / len(flagged)
    limit = a.max_rate if a.max_rate is not None else 0.0
    return AssertionOutcome(
        a.type, rate <= limit,
        f"{len(false_positives)}/{len(flagged)} flagged rows are unlabelled ({rate:.1%})",
    )


def _must_abstain(a: Assertion, ctx: HandlerContext) -> AssertionOutcome:
    """With too little history, INSUFFICIENT_HISTORY is the only honest answer.

    A detector that answers NORMAL on its first run has not observed stability, it
    has assumed it -- and a steward reading NORMAL cannot tell the two apart. This
    is the one property of an anomaly decision that a single-run bundle can settle,
    which is why it is asserted before any ANOMALY/NORMAL ground truth exists.

    A crashed detector is reported as its own observation. It also failed to
    abstain, but "answered NORMAL without evidence" and "never produced a decision"
    send a reader to different places.
    """
    if not ctx.anomaly:
        return AssertionOutcome(a.type, False, "no anomaly report in the bundle", measurable=False)
    decision = str(ctx.anomaly.get("decision") or "").upper()
    status = str(ctx.anomaly.get("status") or "").upper()
    if not decision:
        return AssertionOutcome(a.type, False, "anomaly report carries no decision", measurable=False)
    if decision == "INSUFFICIENT_HISTORY":
        return AssertionOutcome(a.type, True, "abstained on insufficient history")
    if status == "FAILED" or decision == "UNAVAILABLE":
        error = str(ctx.anomaly.get("error") or "no error recorded")[:120]
        return AssertionOutcome(
            a.type, False, f"detector produced no decision ({decision}/{status}): {error}"
        )
    return AssertionOutcome(
        a.type, False,
        f"claimed {decision} with {len(ctx.anomaly.get('hypotheses') or [])} hypothesis(es) "
        "and no prior run to compare against",
    )
