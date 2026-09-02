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
from dataclasses import asdict
from pathlib import Path
from typing import Any

from evalgate.core.context import EvalRunContext
from evalgate.gates.gate1_ai_quality.golden_handlers import (
    _HANDLERS,
    AssertionOutcome,
    CaseOutcome,
    HandlerContext,
    _confidence_monotonic,
    _enum_from_policy,
    _evidence_metric_exists,
    _evidence_references_metric,
    _evidence_refs,
    _forbidden_tokens,
    _max_false_positive_rate,
    _min_violations,
    _must_abstain,
    _must_cite_numbers,
    _must_verify_before_asserting,
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
    _tools_were_used,
)
from evalgate.golden.applicability import (
    DatasetContext,
    Scope,
    build_dataset_context,
    resolve,
    semantic_vocabulary,
)
from evalgate.golden.schema import (
    LAYER_ORDER,
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

__all__ = [
    "AssertionOutcome",
    "CaseOutcome",
    "EVALUATOR",
    "EVIDENCE_DIR",
    "GATE",
    "HandlerContext",
    "PROJECT_ROOT",
    "PROPOSAL_DIRS",
    "REPORTS_DIR",
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
    "evaluate",
    "load_anomaly",
    "load_proposals",
    "load_results",
    "load_tool_events",
    "run_case",
]

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROPOSAL_DIRS = (
    PROJECT_ROOT / "output" / "hitl",
    PROJECT_ROOT / "output" / "rule_proposer",
)
REPORTS_DIR = PROJECT_ROOT / "output" / "reports"
EVIDENCE_DIR = PROJECT_ROOT / "evalgate" / "evidence" / "gate1"

GATE = "ai_quality"
EVALUATOR = "golden_conformance_v1"


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


# ---------------------------------------------------------------------------
# Runner logic
# ---------------------------------------------------------------------------


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
            # Each row is one golden case, not one dataset. See DatasetBreakdown.kind.
            kind="case",
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
