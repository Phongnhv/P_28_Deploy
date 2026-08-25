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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from evalgate.core.context import EvalRunContext
from evalgate.golden.schema import Assertion, GoldenCase, load_all_suites
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


def _params(rule: dict[str, Any]) -> dict[str, Any]:
    return rule.get("effective_parameters") or rule.get("parameters") or {}


# ---------------------------------------------------------------------------
# Assertion evaluation
# ---------------------------------------------------------------------------

def _rule_proposed(a: Assertion, rules: list[dict], _r: list[dict]) -> AssertionOutcome:
    hits = [
        r for r in rules
        if r.get("rule_type") == a.rule_type and r.get("column") == a.column
    ]
    return AssertionOutcome(
        a.type, bool(hits),
        f"{len(hits)} {a.rule_type} rule(s) on {a.column}",
    )


def _rule_not_on_columns(a: Assertion, rules: list[dict], _r: list[dict]) -> AssertionOutcome:
    hits = [
        f"{r.get('table_name')}.{r.get('column')}"
        for r in rules
        if r.get("rule_type") == a.rule_type and r.get("column") in set(a.columns)
    ]
    return AssertionOutcome(
        a.type, not hits,
        "none" if not hits else f"{len(hits)} violation(s): {sorted(set(hits))[:4]}",
    )


def _enum_from_policy(a: Assertion, rules: list[dict], _r: list[dict]) -> AssertionOutcome:
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


def _parameter_bound(a: Assertion, rules: list[dict], _r: list[dict]) -> AssertionOutcome:
    seen: list[float] = []
    for rule in rules:
        if rule.get("rule_type") != a.rule_type or rule.get("column") != a.column:
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


def _no_rules_on_tables(a: Assertion, rules: list[dict], _r: list[dict]) -> AssertionOutcome:
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


def _min_violations(a: Assertion, _p: list[dict], results: list[dict]) -> AssertionOutcome:
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


def _forbidden_tokens(a: Assertion, rules: list[dict], _r: list[dict]) -> AssertionOutcome:
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


def _must_cite_numbers(a: Assertion, rules: list[dict], _r: list[dict]) -> AssertionOutcome:
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


_HANDLERS = {
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
    case: GoldenCase, rules: list[dict], results: list[dict]
) -> CaseOutcome:
    outcomes: list[AssertionOutcome] = []
    for assertion in case.assertions:
        handler = _HANDLERS.get(assertion.type)
        if handler is None:
            outcomes.append(
                AssertionOutcome(assertion.type, False, "no handler for this assertion type")
            )
            continue
        outcomes.append(handler(assertion, rules, results))
    checkable = [o for o in outcomes if o.measurable]
    return CaseOutcome(
        id=case.id,
        tier=case.tier,
        severity=case.severity,
        intent=case.intent.strip(),
        source=case.source,
        # A case with nothing to inspect is not a passing case and not a failing one.
        measurable=bool(checkable),
        passed=bool(checkable) and all(o.passed for o in checkable),
        assertions=outcomes,
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
    cases = [case for _, suite in suites for case in suite.cases]
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

    outcomes = [run_case(case, rules, results) for case in cases]
    scored = [o for o in outcomes if o.measurable]
    unmeasurable = [o for o in outcomes if not o.measurable]
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
        },
    )
