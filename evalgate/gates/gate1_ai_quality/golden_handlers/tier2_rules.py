"""Tier 2 rule, schema, semantic and evidence assertion handlers."""

from __future__ import annotations

from typing import Any

from evalgate.gates.gate1_ai_quality.golden_handlers.types import AssertionOutcome, HandlerContext
from evalgate.golden.applicability import resolve_evidence_ref
from evalgate.golden.schema import Assertion


def _params(rule: dict[str, Any]) -> dict[str, Any]:
    return rule.get("effective_parameters") or rule.get("parameters") or {}


def _target_columns(a: Assertion, ctx: HandlerContext) -> list[str]:
    """Columns this assertion is about: the explicit one, else the resolved scope."""
    if a.column:
        return [a.column]
    return list(ctx.scope.columns)


def _rule_proposed(a: Assertion, ctx: HandlerContext) -> AssertionOutcome:
    """A rule of this type exists on every column the case is about.

    The column-existence check is the one correction that matters here. This was
    the only handler with no unmeasurable branch, so a case naming ``fare_amount``
    returned a hard FAIL against a clinical dataset -- penalising the agent for not
    proposing a rule on a column that does not exist. "The column is absent" and
    "the column is there and got no rule" are different findings and only the
    second is about the agent.
    """
    rules = ctx.rules
    targets = _target_columns(a, ctx)
    known = set(ctx.dataset.columns) if ctx.dataset and ctx.dataset.columns else None
    if known is not None:
        present = [c for c in targets if c in known]
        if not present:
            return AssertionOutcome(
                a.type, False,
                f"column(s) {targets[:4]} are not in this dataset",
                measurable=False,
            )
        targets = present
    missing = [
        column for column in targets
        if not any(r.get("rule_type") == a.rule_type and r.get("column") == column for r in rules)
    ]
    return AssertionOutcome(
        a.type, not missing,
        f"{len(targets) - len(missing)}/{len(targets)} column(s) have a {a.rule_type} rule"
        + (f"; missing on {missing[:4]}" if missing else ""),
    )


def _rule_not_on_columns(a: Assertion, ctx: HandlerContext) -> AssertionOutcome:
    rules = ctx.rules
    hits = [
        f"{r.get('table_name')}.{r.get('column')}"
        for r in rules
        if r.get("rule_type") == a.rule_type and r.get("column") in set(a.columns)
    ]
    return AssertionOutcome(
        a.type, not hits,
        "none" if not hits else f"{len(hits)} violation(s): {sorted(set(hits))[:4]}",
    )


def _enum_from_policy(a: Assertion, ctx: HandlerContext) -> AssertionOutcome:
    rules = ctx.rules
    forbidden = set(a.must_exclude)
    offending: list[str] = []
    checked = 0
    for rule in rules:
        if rule.get("rule_type") != "ACCEPTED_VALUES" or rule.get("column") != a.column:
            continue
        checked += 1
        admitted = forbidden & {str(v) for v in (_params(rule).get("accepted_values") or [])}
        if admitted:
            offending.append(f"{rule['__artifact__']}: {sorted(admitted)}")
    if checked == 0:
        return AssertionOutcome(
            a.type, False, f"no ACCEPTED_VALUES rule on {a.column} to check", measurable=False
        )
    return AssertionOutcome(
        a.type, not offending,
        f"{len(offending)}/{checked} proposal(s) admit an excluded value",
    )


def _parameter_bound(a: Assertion, ctx: HandlerContext) -> AssertionOutcome:
    rules = ctx.rules
    targets = set(_target_columns(a, ctx))
    seen: list[float] = []
    for rule in rules:
        if rule.get("rule_type") != a.rule_type or rule.get("column") not in targets:
            continue
        value = _params(rule).get(a.parameter)
        if isinstance(value, (int, float)):
            seen.append(float(value))
    if not seen:
        return AssertionOutcome(
            a.type, False, f"no {a.rule_type}.{a.parameter} on {a.column}", measurable=False
        )
    bad = [
        v for v in seen
        if (a.minimum is not None and v < a.minimum)
        or (a.maximum is not None and v > a.maximum)
    ]
    return AssertionOutcome(
        a.type, not bad,
        f"{len(bad)}/{len(seen)} value(s) outside bound; observed {sorted(set(seen))[:5]}",
    )


def _no_rules_on_tables(a: Assertion, ctx: HandlerContext) -> AssertionOutcome:
    rules = ctx.rules
    forbidden = set(a.tables)
    counts: dict[str, int] = {}
    for rule in rules:
        table = rule.get("table_name")
        if table in forbidden:
            counts[table] = counts.get(table, 0) + 1
    total = sum(counts.values())
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:4]
    return AssertionOutcome(
        a.type, total == 0,
        "none" if total == 0 else f"{total} rule(s) on operational tables, top: {top}",
    )


def _semantic_type_is(a: Assertion, ctx: HandlerContext) -> AssertionOutcome:
    if ctx.dataset is None or not ctx.dataset.has_semantic_contract:
        return AssertionOutcome(a.type, False, "no semantic contract in the bundle", measurable=False)
    wrong = [
        f"{name}={(ctx.dataset.semantic_for(name).semantic_type if ctx.dataset.semantic_for(name) else '?')}"
        for name in _target_columns(a, ctx)
        if not (
            (item := ctx.dataset.semantic_for(name)) and item.semantic_type == a.semantic_type
        )
    ]
    return AssertionOutcome(
        a.type, not wrong,
        "all match" if not wrong else f"{len(wrong)} column(s) not {a.semantic_type}: {wrong[:4]}",
    )


def _nullable_expected_is(a: Assertion, ctx: HandlerContext) -> AssertionOutcome:
    if ctx.dataset is None or not ctx.dataset.has_semantic_contract:
        return AssertionOutcome(a.type, False, "no semantic contract in the bundle", measurable=False)
    wrong = [
        name
        for name in _target_columns(a, ctx)
        if (item := ctx.dataset.semantic_for(name)) and item.nullable_expected != a.nullable_expected
    ]
    return AssertionOutcome(
        a.type, not wrong,
        "all match" if not wrong
        else f"{len(wrong)} column(s) not nullable_expected={a.nullable_expected}: {wrong[:4]}",
    )


def _relationship_declared(a: Assertion, ctx: HandlerContext) -> AssertionOutcome:
    if ctx.dataset is None or not ctx.dataset.has_semantic_contract:
        return AssertionOutcome(a.type, False, "no semantic contract in the bundle", measurable=False)
    declared = len(ctx.dataset.relationships)
    return AssertionOutcome(
        a.type, declared > 0, f"{declared} relationship(s) declared in the contract"
    )


def _evidence_refs(rule: dict) -> list[str]:
    refs = rule.get("selected_evidence_refs") or rule.get("evidence_refs") or []
    return [str(ref) for ref in refs if ref]


def _evidence_metric_exists(a: Assertion, ctx: HandlerContext) -> AssertionOutcome:
    """Every citation must resolve to a figure the profile actually published.

    Checked against ``profile.evidence_keys``, the vocabulary the product itself
    emits, so this asks whether the reference is real -- not whether the number
    supports the threshold, which is a separate and harder question.

    ``policy.*`` references name the governed policy rather than the profile and are
    accepted without resolution; a policy assertion is verified by HG-A3.
    """
    if ctx.dataset is None or not ctx.dataset.profile_columns:
        return AssertionOutcome(a.type, False, "no profile to resolve citations against", measurable=False)
    dangling: list[str] = []
    checked = 0
    for rule in ctx.rules:
        for ref in _evidence_refs(rule):
            if ref.startswith("policy."):
                continue
            checked += 1
            if not resolve_evidence_ref(ref, ctx.dataset):
                dangling.append(f"{rule.get('rule_id', rule.get('id', '?'))}: {ref}")
    if checked == 0:
        return AssertionOutcome(a.type, False, "no profile-backed citation to check", measurable=False)
    return AssertionOutcome(
        a.type, not dangling,
        f"{len(dangling)}/{checked} citation(s) resolve to nothing"
        + (f"; e.g. {dangling[:2]}" if dangling else ""),
    )


def _evidence_references_metric(a: Assertion, ctx: HandlerContext) -> AssertionOutcome:
    """The citation must name the metric that decides the threshold.

    A rule may cite a real figure that has nothing to do with its own parameter --
    a RANGE lower bound justified by a null rate is grounded in the wrong number.
    """
    wanted = set(a.metrics)
    targets = set(_target_columns(a, ctx))
    relevant = [r for r in ctx.rules if r.get("column") in targets]
    if not relevant:
        return AssertionOutcome(a.type, False, "no rule on the scoped column(s)", measurable=False)
    missing = [
        str(r.get("rule_id") or r.get("id") or "?")
        for r in relevant
        if not any(metric in ref for ref in _evidence_refs(r) for metric in wanted)
    ]
    return AssertionOutcome(
        a.type, not missing,
        f"{len(missing)}/{len(relevant)} rule(s) cite none of {sorted(wanted)}"
        + (f"; e.g. {missing[:2]}" if missing else ""),
    )


_SEVERITY_RANK = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}


def _severity_ranks_above(a: Assertion, ctx: HandlerContext) -> AssertionOutcome:
    """Ordinal, never absolute. See Assertion.ranks_above for why."""
    targets = set(_target_columns(a, ctx))
    mine = [_SEVERITY_RANK.get(str(r.get("severity", "")).upper(), 0)
            for r in ctx.rules if r.get("column") in targets]
    theirs = [_SEVERITY_RANK.get(str(r.get("severity", "")).upper(), 0)
              for r in ctx.rules if r.get("column") in set(a.ranks_above)]
    if not mine or not theirs:
        return AssertionOutcome(a.type, False, "one side of the ordering has no rule", measurable=False)
    return AssertionOutcome(
        a.type, min(mine) >= max(theirs),
        f"scoped min severity {min(mine)} vs compared max {max(theirs)}",
    )


def _confidence_monotonic(a: Assertion, ctx: HandlerContext) -> AssertionOutcome:
    """High-confidence proposals must not be less accurate than low-confidence ones.

    Accuracy proxy: a rule that executed and reported a definite PASS/FAIL is
    treated as sound; one that errored is not. It is a weak proxy on purpose --
    the claim being tested is that the score carries *any* information, not that
    it is calibrated to a target.
    """
    field_name = a.confidence_field or "confidence"
    scored: list[tuple[float, bool]] = []
    status_by_rule = {
        str(entry.get("rule_id", "")): str(entry.get("status", "")).upper()
        for entry in ctx.results
    }
    for rule in ctx.rules:
        raw = rule.get(field_name)
        value = raw.get("overall") if isinstance(raw, dict) else raw
        if not isinstance(value, (int, float)):
            continue
        rule_id = str(rule.get("rule_id") or rule.get("id") or "")
        status = next(
            (s for key, s in status_by_rule.items() if rule_id and rule_id in key), None
        )
        if status is None:
            continue
        scored.append((float(value), status in {"PASS", "FAIL"}))
    if len(scored) < 4:
        return AssertionOutcome(
            a.type, False, f"only {len(scored)} executed proposal(s) carry a confidence",
            measurable=False,
        )
    scored.sort(key=lambda pair: pair[0])
    half = len(scored) // 2
    low = sum(1 for _, ok in scored[:half] if ok) / half
    high = sum(1 for _, ok in scored[half:] if ok) / (len(scored) - half)
    return AssertionOutcome(
        a.type, high >= low,
        f"low-confidence half {low:.2f} vs high-confidence half {high:.2f}",
    )
