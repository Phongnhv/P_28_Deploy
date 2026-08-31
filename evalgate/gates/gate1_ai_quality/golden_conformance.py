"""Run the golden cases against what the agent actually produced.

SDIH measures whether defects were *found*. This measures whether the agent
proposed the *right rule* -- correct type, correct column, threshold sourced from
policy rather than from the data -- and whether the generated text obeys the
constraints the system prompt sets.

Every assertion is deterministic and costs nothing, which is what makes these cases
usable as a regression baseline. A case whose outcome depends on a model call would
drift on its own, and a drifting baseline cannot detect drift in anything else.

Cases are evaluated against archived artefacts under ``output/`` because the live
agent cannot currently be invoked: ``get_dataset_rule_policy`` raises for every
dataset on this branch. When that is fixed the same cases run unchanged against a
live run; only the loader changes.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from evalgate.core.context import EvalRunContext
from evalgate.golden.applicability import (
    DatasetContext,
    Scope,
    build_dataset_context,
    resolve,
    resolve_evidence_ref,
    semantic_vocabulary,
)
from evalgate.golden.schema import (
    LAYER_ORDER,
    Assertion,
    GoldenCase,
    load_all_cases,
    load_all_suites,
)
from evalgate.normalizers import normalizers as norm
from evalgate.schemas.eval_result import (
    DatasetBreakdown,
    EvalResult,
    EvalStatus,
    Evidence,
    Finding,
    MetricValue,
    Severity,
    Threshold,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROPOSAL_DIRS = (
    PROJECT_ROOT / "output" / "hitl",
    PROJECT_ROOT / "output" / "rule_proposer",
)
REPORTS_DIR = PROJECT_ROOT / "output" / "reports"
EVIDENCE_DIR = PROJECT_ROOT / "evalgate" / "evidence" / "gate1"

GATE = "ai_quality"
EVALUATOR = "golden_conformance_v1"

_NUMERAL = re.compile(r"\d")


@dataclass
class AssertionOutcome:
    type: str
    passed: bool
    observed: str
    #: False when the artefacts contain nothing this assertion could inspect. An
    #: unmeasurable assertion must not count as a failure -- "we did not look" and
    #: "we looked and it was wrong" are different claims, and conflating them makes
    #: the pass rate meaningless.
    measurable: bool = True


@dataclass
class CaseOutcome:
    id: str
    tier: int
    severity: str
    intent: str
    source: str
    passed: bool
    assertions: list[AssertionOutcome]
    measurable: bool = True
    #: False when the case is not a statement about this dataset at all. Distinct
    #: from ``measurable``: "there is no currency column here" is not the same claim
    #: as "there was nothing in the artefacts to inspect", and neither is a failure.
    applicable: bool = True
    applicability_reason: str = ""
    #: Which decision surface failed first. A wrong semantic type produces a wrong
    #: candidate, a wrong rule and a wrong finding, and reporting all four sends a
    #: reader to fix rule_proposer for a defect owned by dataset_understanding.
    failed_layer: str | None = None


@dataclass
class HandlerContext:
    """Everything an assertion can be evaluated against."""

    rules: list[dict[str, Any]]
    results: list[dict[str, Any]]
    scope: Scope
    dataset: DatasetContext | None
    #: Graph 3's own output. A second decision surface with its own ground truth,
    #: and the only place an abstention can be observed.
    anomaly: dict[str, Any] = field(default_factory=dict)
    #: Tool lifecycle events from the run trace. The only record of *how* the agent
    #: decided; a verified rule and a guessed one are identical once written down.
    tool_events: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Loading the agent's output
# ---------------------------------------------------------------------------

def load_proposals(context: EvalRunContext | None = None) -> list[dict[str, Any]]:
    """Every archived proposed rule, flattened, with its artefact recorded."""
    rules: list[dict[str, Any]] = []
    if context is not None:
        for record in context.records("proposals"):
            payload = json.loads(context.path_for(record).read_text(encoding="utf-8"))
            batch = payload.get("proposed_rules") if isinstance(payload, dict) else payload
            if isinstance(batch, list):
                rules.extend({**rule, "__artifact__": record.relative_path}
                             for rule in batch if isinstance(rule, dict))
        return rules
    return rules


def load_results(context: EvalRunContext | None = None) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if context is not None:
        for record in context.records("execution-results"):
            payload = json.loads(context.path_for(record).read_text(encoding="utf-8"))
            results.extend(payload.get("test_results", payload.get("results", [])))
        return results
    return results


def load_anomaly(context: EvalRunContext | None = None) -> dict[str, Any]:
    """Graph 3's decision, unwrapped from its governed-artifact envelope."""
    if context is None or not context.records("anomaly-report"):
        return {}
    document = json.loads(
        context.path_for(context.records("anomaly-report")[0]).read_text(encoding="utf-8")
    )
    if isinstance(document, dict) and isinstance(document.get("payload"), dict):
        return document["payload"]
    return document if isinstance(document, dict) else {}


def load_tool_events(context: EvalRunContext | None = None) -> list[dict[str, Any]]:
    """Tool start/end/error records from the JSONL invocation trace."""
    if context is None or not context.records("llm-invocations"):
        return []
    path = context.path_for(context.records("llm-invocations")[0])
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and str(record.get("event", "")).startswith("tool_"):
            events.append(record)
    return events


def _params(rule: dict[str, Any]) -> dict[str, Any]:
    return rule.get("effective_parameters") or rule.get("parameters") or {}


# ---------------------------------------------------------------------------
# Assertion evaluation
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Layered assertions
# ---------------------------------------------------------------------------

def _target_columns(a: Assertion, ctx: HandlerContext) -> list[str]:
    """Columns this assertion is about: the explicit one, else the resolved scope."""
    if a.column:
        return [a.column]
    return list(ctx.scope.columns)


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


_HANDLERS = {
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


def run_case(
    case: GoldenCase,
    rules: list[dict],
    results: list[dict],
    *,
    scope: Scope | None = None,
    dataset: DatasetContext | None = None,
    anomaly: dict[str, Any] | None = None,
    tool_events: list[dict[str, Any]] | None = None,
) -> CaseOutcome:
    resolved = scope or Scope(columns=(), reason="unscoped")
    base = dict(
        id=case.id,
        tier=case.tier,
        severity=case.severity,
        intent=case.intent.strip(),
        source=case.source,
        applicability_reason=resolved.reason,
    )
    if not resolved.applicable:
        # Not a statement about this dataset. Reported, never scored.
        return CaseOutcome(
            **base, passed=False, assertions=[], measurable=False, applicable=False
        )

    ctx = HandlerContext(
        rules=rules, results=results, scope=resolved, dataset=dataset,
        anomaly=anomaly or {}, tool_events=tool_events or [],
    )
    outcomes: list[AssertionOutcome] = []
    for assertion in case.assertions:
        handler = _HANDLERS.get(assertion.type)
        if handler is None:
            outcomes.append(
                AssertionOutcome(assertion.type, False, "no handler for this assertion type")
            )
            continue
        outcomes.append(handler(assertion, ctx))

    checkable = [o for o in outcomes if o.measurable]
    # Blame the earliest layer that failed. Interpretation feeds evidence selection,
    # which feeds the decision: reporting a wrong semantic type as four separate
    # defects sends a reader to fix the wrong node.
    layer_of = {assertion.type: assertion.layer for assertion in case.assertions}
    failed_layers = [
        layer_of.get(o.type) for o in outcomes if o.measurable and not o.passed
    ]
    earliest = next(
        (layer for layer in LAYER_ORDER if layer in failed_layers), None
    )
    return CaseOutcome(
        **base,
        # A case with nothing to inspect is not a passing case and not a failing one.
        measurable=bool(checkable),
        passed=bool(checkable) and all(o.passed for o in checkable),
        assertions=outcomes,
        failed_layer=earliest,
    )


def evaluate(*, write_evidence: bool = True, context: EvalRunContext | None = None) -> EvalResult:
    try:
        suites = load_all_suites()
    except Exception as exc:  # noqa: BLE001 - a malformed suite is itself the finding
        return EvalResult(
            gate=GATE,
            evaluator=EVALUATOR,
            status=EvalStatus.BLOCKED_MISSING_GROUND_TRUTH,
            metadata={"reason": f"golden suite is unreadable: {exc}"},
        )
    cases = [case for _, case in load_all_cases()]
    if not cases:
        return EvalResult(
            gate=GATE, evaluator=EVALUATOR, status=EvalStatus.NOT_MEASURED,
            metadata={"reason": "no golden cases are defined"},
        )

    rules = load_proposals(context)
    results = load_results(context)
    if not rules:
        return EvalResult(
            gate=GATE, evaluator=EVALUATOR,
            status=EvalStatus.BLOCKED_BY_SYSTEM_CAPABILITY,
            metadata={
                "reason": (
                    "the current manifest contains no agent proposals"
                )
            },
        )

    anomaly = load_anomaly(context)
    tool_events = load_tool_events(context)
    dataset = build_dataset_context(context)
    if dataset is None:
        # Without a bundle there is nothing to resolve selectors against, and every
        # case would be scored against an unknown schema.
        return EvalResult(
            gate=GATE, evaluator=EVALUATOR,
            status=EvalStatus.MISSING_MANDATORY_EVIDENCE,
            metadata={"reason": "no manifest context; golden cases need a dataset to resolve against"},
        )

    scopes = {case.id: resolve(case, dataset) for case in cases}
    outcomes = [
        run_case(
            case, rules, results,
            scope=scopes[case.id], dataset=dataset, anomaly=anomaly,
            tool_events=tool_events,
        )
        for case in cases
    ]
    applicable = [o for o in outcomes if o.applicable]
    inapplicable = [o for o in outcomes if not o.applicable]
    scored = [o for o in applicable if o.measurable]
    unmeasurable = [o for o in applicable if not o.measurable]
    passed = [o for o in scored if o.passed]
    failed = [o for o in scored if not o.passed]
    tier2 = [o for o in scored if o.tier == 2]
    tier3 = [o for o in scored if o.tier == 3]

    def _rate(subset: list[CaseOutcome]) -> float | None:
        return (sum(1 for o in subset if o.passed) / len(subset)) if subset else None

    breakdown = [
        DatasetBreakdown(
            dataset_id=o.id,
            status=(
                EvalStatus.NOT_MEASURED if not o.measurable
                else EvalStatus.PASS if o.passed
                else EvalStatus.FAIL
            ),
            score=None if not o.measurable else (100.0 if o.passed else 0.0),
            reason="; ".join(f"{a.type}: {a.observed}" for a in o.assertions)[:400],
        )
        for o in outcomes
    ]

    if not scored:
        return EvalResult(
            gate=GATE, evaluator=EVALUATOR, status=EvalStatus.NOT_MEASURED,
            per_dataset_breakdown=breakdown,
            metadata={
                "reason": "no golden case could be inspected against the archived artefacts"
            },
        )

    evidence: list[Evidence] = []
    if write_evidence:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        target = EVIDENCE_DIR / "golden_conformance.json"
        target.write_text(
            json.dumps(
                {
                    "suites": [str(p.relative_to(PROJECT_ROOT)) for p, _ in suites],
                    "proposals_scored": len(rules),
                    "execution_results_scored": len(results),
                    "cases": [asdict(o) for o in outcomes],
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        evidence.append(Evidence(type="file", path=str(target.relative_to(PROJECT_ROOT))))

    findings = [
        Finding(
            id="HG-A5",
            severity=Severity.CRITICAL if o.severity == "CRITICAL" else Severity.HIGH,
            title=f"Golden case {o.id} failed",
            detail=(
                o.intent
                + " | observed: "
                + "; ".join(f"{a.type}: {a.observed}" for a in o.assertions if not a.passed)
            ),
            root_cause_hint=f"expectation recorded in {o.source}",
            evidence_ref="evalgate/evidence/gate1/golden_conformance.json",
            blocks_release=(o.severity == "CRITICAL"),
        )
        for o in failed
    ]

    return EvalResult(
        gate=GATE,
        evaluator=EVALUATOR,
        status=EvalStatus.FAIL if failed else EvalStatus.PASS,
        score=norm.ratio(len(passed) / len(scored)),
        metrics={
            "golden_case_pass_rate": MetricValue(
                raw=round(len(passed) / len(scored), 4), unit="ratio",
                normalized=norm.ratio(len(passed) / len(scored)),
                note=f"{len(scored)} case(s) inspected, {len(unmeasurable)} not inspectable",
            ),
            "golden_critical_failures": MetricValue(
                raw=sum(1 for o in failed if o.severity == "CRITICAL"), unit="count",
                normalized=norm.zero_tolerance(
                    sum(1 for o in failed if o.severity == "CRITICAL")
                ),
            ),
            "golden_rule_expectation_rate": MetricValue(
                raw=None if _rate(tier2) is None else round(_rate(tier2), 4),
                unit="ratio", normalized=norm.ratio(_rate(tier2)),
                note="tier 2: was the right rule proposed, from the right source",
            ),
            "golden_prompt_compliance_rate": MetricValue(
                raw=None if _rate(tier3) is None else round(_rate(tier3), 4),
                unit="ratio", normalized=norm.ratio(_rate(tier3)),
                note="tier 3: did the generated text obey its own system prompt",
            ),
            # Guards the evasion that applicability itself opens. Skipping a case
            # because no column matches is correct; skipping *every* case is a green
            # gate that inspected nothing, and the pass rate above cannot tell the
            # two apart because both leave it at 1.0 over an empty denominator.
            "golden_applicability_rate": MetricValue(
                raw=round(len(applicable) / len(outcomes), 4) if outcomes else 0.0,
                unit="ratio",
                normalized=norm.ratio(len(applicable) / len(outcomes)) if outcomes else 0.0,
                note=(
                    f"{len(applicable)}/{len(outcomes)} case(s) are statements about "
                    f"{dataset.dataset_id}"
                ),
            ),
        },
        per_dataset_breakdown=breakdown,
        thresholds={
            "golden_case_pass_rate": Threshold(**{"pass": 100.0, "warn": 80.0}),
            "golden_critical_failures": Threshold(**{"pass": 0.0, "warn": 0.0}),
        },
        evidence=evidence,
        critical_findings=findings,
        metadata={
            "mode": "replay against archived proposals",
            "cases": len(outcomes),
            "failed": [o.id for o in failed],
            "not_inspectable": [
                {"id": o.id, "why": next(
                    (a.observed for a in o.assertions if not a.measurable), "unknown"
                )}
                for o in unmeasurable
            ],
            "proposals_scored": len(rules),
            "dataset_id": dataset.dataset_id,
            "corpus_id": dataset.corpus_id,
            "not_applicable": [{"id": o.id, "why": o.applicability_reason} for o in inapplicable],
            # Where the failures live, so a reader knows which node to open. A wrong
            # semantic type shows up here as one interpretation failure rather than
            # as three downstream decision failures.
            "failure_attribution": {
                layer: sum(1 for o in scored if o.failed_layer == layer)
                for layer in LAYER_ORDER
                if any(o.failed_layer == layer for o in scored)
            },
            # A selector matching nothing is ambiguous on its own: the dataset may
            # have no currency column, or the interpreter may speak a different
            # vocabulary than the golden set. The distribution separates them.
            "semantic_vocabulary": semantic_vocabulary(dataset),
        },
    )
