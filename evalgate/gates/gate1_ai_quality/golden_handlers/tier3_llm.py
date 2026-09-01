"""Tier 3 LLM prompt compliance and tool usage handlers."""

from __future__ import annotations

import re

from evalgate.gates.gate1_ai_quality.golden_handlers.types import AssertionOutcome, HandlerContext
from evalgate.golden.schema import Assertion

_NUMERAL = re.compile(r"\d")


def _forbidden_tokens(a: Assertion, ctx: HandlerContext) -> AssertionOutcome:
    rules = ctx.rules
    offenders: list[str] = []
    checked = 0
    for rule in rules:
        text = str(rule.get(a.field or "") or "")
        if not text:
            continue
        checked += 1
        found = [t for t in a.tokens if t in text]
        if found:
            offenders.append(f"{rule.get('rule_id', '?')}: {found[:3]}")
    if checked == 0:
        return AssertionOutcome(
            a.type, False, f"no rule carries a {a.field} to check", measurable=False
        )
    rate = len(offenders) / checked
    return AssertionOutcome(
        a.type, not offenders,
        f"{len(offenders)}/{checked} ({rate:.1%}) contain a forbidden token; "
        f"e.g. {offenders[:2]}" if offenders else f"0/{checked}",
    )


def _must_cite_numbers(a: Assertion, ctx: HandlerContext) -> AssertionOutcome:
    rules = ctx.rules
    missing = 0
    checked = 0
    for rule in rules:
        text = str(rule.get(a.field or "") or "")
        if not text:
            continue
        checked += 1
        if not _NUMERAL.search(text):
            missing += 1
    if checked == 0:
        return AssertionOutcome(
            a.type, False, f"no rule carries a {a.field} to check", measurable=False
        )
    return AssertionOutcome(
        a.type, missing == 0,
        f"{missing}/{checked} rationale(s) cite no figure at all",
    )


def _tools_were_used(a: Assertion, ctx: HandlerContext) -> AssertionOutcome:
    """Did the agent consult the data at all before answering?

    Reported unmeasurable rather than failed when the trace carries no tool events,
    because an empty trace has two causes -- the agent used no tools, or the run was
    not instrumented -- and only the first is about the agent.
    """
    starts = [e for e in ctx.tool_events if e.get("event") == "tool_start"]
    if not ctx.tool_events:
        return AssertionOutcome(
            a.type, False, "no tool lifecycle in the trace; run may be uninstrumented",
            measurable=False,
        )
    minimum = a.min_calls if a.min_calls is not None else 1
    names = sorted({str(e.get("tool")) for e in starts})
    return AssertionOutcome(
        a.type, len(starts) >= minimum,
        f"{len(starts)} tool call(s) across {names[:5]}",
    )


def _must_verify_before_asserting(a: Assertion, ctx: HandlerContext) -> AssertionOutcome:
    """Not merely that a tool ran, but that a *verifying* one did.

    Reading a column's statistics is lookup; dry-running a candidate against the
    real data is verification. An agent that only ever looks up is still guessing,
    just with more context.
    """
    if not ctx.tool_events:
        return AssertionOutcome(
            a.type, False, "no tool lifecycle in the trace; run may be uninstrumented",
            measurable=False,
        )
    verifying = set(a.verifying_tools) or {"dry_run_rule_candidate"}
    used = {
        str(e.get("tool")) for e in ctx.tool_events if e.get("event") == "tool_start"
    }
    hit = sorted(used & verifying)
    return AssertionOutcome(
        a.type, bool(hit),
        f"verifying tools used: {hit}" if hit else f"none of {sorted(verifying)} was called; used {sorted(used)[:5]}",
    )
