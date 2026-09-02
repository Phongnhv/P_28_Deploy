"""Turn a list of EvalResult into one decision.

Three rules drive everything here:

1. Hard gates are evaluated *before* the aggregate.  A failing hard gate is a
   release blocker no score can override.
2. Per-dataset collapse uses MIN for hard-gate metrics and P25 for score metrics.
   A mean would let six healthy datasets hide the seventh broken one -- which is
   precisely the failure mode a "works on any dataset" product must not have.
3. Statuses in ``EXCLUDED_FROM_AGGREGATE`` drop out and the remaining gate weights
   are re-normalised, so an unmeasured gate never silently scores zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from evalgate.normalizers import normalizers as norm
from evalgate.schemas.eval_result import (
    EXCLUDED_FROM_AGGREGATE,
    EvalResult,
    EvalStatus,
    Finding,
)

POLICY_DIR = Path(__file__).resolve().parent / "policies"


class Decision:
    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"
    RELEASE_BLOCKED = "RELEASE_BLOCKED"
    #: The run cannot be attributed to a revision. The score is still shown, because
    #: the developer needs it, but approval is withheld: the number describes a tree
    #: that no commit corresponds to.
    EVALGATE_STALE = "EVALGATE_STALE"
    #: Too little of the system was measured for an aggregate to mean anything.
    INSUFFICIENT_COVERAGE = "INSUFFICIENT_COVERAGE"
    #: The evaluator configuration or metric namespace is ambiguous. Publishing a
    #: product verdict in this state would be false assurance.
    EVALGATE_INVALID = "EVALGATE_INVALID"


EXIT_CODES = {
    Decision.PASS: 0,
    Decision.WARNING: 1,
    Decision.FAIL: 2,
    Decision.RELEASE_BLOCKED: 3,
    Decision.EVALGATE_STALE: 4,
    Decision.INSUFFICIENT_COVERAGE: 5,
    Decision.EVALGATE_INVALID: 6,
}

#: Share of the original gate weight that must actually be measured before an
#: aggregate is published. Re-normalisation is right -- an unmeasured gate must not
#: score zero -- but it also silently concentrates the whole verdict onto whatever
#: happens to be left. Below this floor the honest answer is that there is not
#: enough evidence for a number, not that the number is low.
MIN_MEASURED_WEIGHT = 0.60


def load_policy(name: str) -> dict[str, Any]:
    return yaml.safe_load((POLICY_DIR / f"{name}.yaml").read_text(encoding="utf-8"))


@dataclass
class HardGateOutcome:
    id: str
    gate: str
    title: str
    metric: str
    status: str  # PASS | FAIL | NOT_EVALUATED
    observed: float | None = None
    reason: str = ""


@dataclass
class AggregateOutcome:
    decision: str
    score: float | None
    gate_scores: dict[str, float | None] = field(default_factory=dict)
    effective_weights: dict[str, float] = field(default_factory=dict)
    excluded_gates: dict[str, str] = field(default_factory=dict)
    hard_gates: list[HardGateOutcome] = field(default_factory=list)
    blocking_findings: list[Finding] = field(default_factory=list)
    #: Share of the original weight that was actually measured, before re-normalisation.
    #: Counted at *evaluator* level: see ``evaluator_coverage``.
    measured_weight: float = 0.0
    #: Per-gate ``(ran, declared)`` so a reader can see where the holes are.
    coverage_detail: dict[str, tuple[int, int]] = field(default_factory=dict)
    #: The aggregate that *would* have been published had coverage been sufficient.
    #: Kept for transparency; ``score`` is None whenever this is set.
    provisional_score: float | None = None
    #: Why ``score`` carries no number.
    score_withheld_reason: str | None = None
    #: Set when the runner overrides the decision, e.g. for a stale workspace.
    override_reason: str | None = None
    #: Metric names claimed by more than one evaluator. Empty is the healthy state.
    metric_collisions: dict[str, list[str]] = field(default_factory=dict)
    suppressed_findings: list[str] = field(default_factory=list)
    unsuppressed_findings: list[str] = field(default_factory=list)
    mandatory_evidence_coverage: float = 0.0
    gate_verdicts: dict[str, str] = field(default_factory=dict)
    #: Why the release is blocked for reasons that carry no finding id.
    #:
    #: Four of the six conditions that produce RELEASE_BLOCKED are statements about
    #: missing evidence rather than about a failed control: a mandatory hard gate
    #: that was never evaluated, a mandatory evaluator that errored, one that
    #: failed, and mandatory coverage below 1.0. None of them raises a Finding, so
    #: none of them appears in the id set the suppression ratchet reasons about --
    #: and the ratchet downgrades RELEASE_BLOCKED whenever that set comes back
    #: empty. With an empty suppressions.yaml, a run blocked purely for missing
    #: evidence was therefore promoted straight to a score band.
    #:
    #: Recorded here so the ratchet can see them. A suppression is an exception to
    #: a *known* finding; it must never be able to excuse evidence that was never
    #: collected.
    block_reasons: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.decision]


# ---------------------------------------------------------------------------
# Per-dataset collapse
# ---------------------------------------------------------------------------

def collapse_per_dataset(
    values: list[float | None],
    *,
    is_hard_gate_metric: bool,
) -> float | None:
    """MIN for hard-gate metrics, P25 for score metrics."""
    usable = [v for v in values if v is not None]
    if not usable:
        return None
    if is_hard_gate_metric:
        return float(min(usable))
    return norm.percentile(usable, 0.25)


def collapse_result_scores(result: EvalResult) -> float | None:
    """Collapse a multi-dataset evaluator into a single gate-level score.

    Datasets whose status is excluded (typically BLOCKED_BY_SYSTEM_CAPABILITY)
    contribute no number, but they stay in the report.
    """
    if not result.per_dataset_breakdown:
        return result.score
    usable = [
        d for d in result.per_dataset_breakdown
        if d.status not in EXCLUDED_FROM_AGGREGATE and d.score is not None
    ]
    if not usable:
        return None
    scored = [d.score for d in usable]
    # A breakdown of pass/fail cases is a pass rate, not a distribution of dataset
    # scores. P25 over binary values collapses to 0 as soon as a quarter of them fail,
    # which threw away golden_conformance's 60.0 and policy_resolution's 100.0. See
    # DatasetBreakdown.kind.
    if all(d.kind == "case" for d in usable):
        return 100.0 * sum(1 for s in scored if s >= 100.0) / len(scored)
    return norm.percentile(scored, 0.25)


# ---------------------------------------------------------------------------
# Hard gates
# ---------------------------------------------------------------------------

_ALLOWED_RULE_CHARS = set("0123456789.<>=! eE+-()")


def _evaluate_rule(rule: str, value: float) -> bool:
    """Evaluate a closed comparison DSL of the form 'value <op> <number>'."""
    expression = rule.replace("value", repr(float(value)))
    if not set(expression) <= _ALLOWED_RULE_CHARS:
        raise ValueError(f"Unsafe hard-gate rule: {rule}")
    return bool(eval(expression, {"__builtins__": {}}, {}))  # noqa: S307 - closed DSL


def detect_metric_collisions(results: list[EvalResult]) -> dict[str, list[str]]:
    """Metric names produced by more than one evaluator.

    Hard gates read metrics from a flat namespace, so a duplicate name means the
    gate silently reads whichever evaluator happened to run last. There are none
    today; this exists so that the day one is introduced, it is visible in the
    report instead of quietly changing a release decision.
    """
    owners: dict[str, list[str]] = {}
    for result in results:
        for key in result.metrics:
            owners.setdefault(key, []).append(result.evaluator)
    return {key: names for key, names in owners.items() if len(names) > 1}


def evaluate_hard_gates(results: list[EvalResult]) -> list[HardGateOutcome]:
    policy = load_policy("hard_gates")
    observed: dict[str, float] = {}
    for result in results:
        for key, metric in result.metrics.items():
            if metric.raw is None:
                continue
            observed[key] = float(metric.raw)

    outcomes: list[HardGateOutcome] = []
    for spec in policy["hard_gates"]:
        metric_name = spec["metric"]
        if metric_name not in observed:
            outcomes.append(
                HardGateOutcome(
                    id=spec["id"],
                    gate=spec["gate"],
                    title=spec["title"],
                    metric=metric_name,
                    status="NOT_EVALUATED",
                    reason="metric not produced by any evaluator in this run",
                )
            )
            continue
        value = observed[metric_name]
        breached = _evaluate_rule(spec["rule"], value)
        reason = ""
        if breached:
            reason = f"{metric_name}={value} matched blocking rule: {spec['rule']}"
        outcomes.append(
            HardGateOutcome(
                id=spec["id"],
                gate=spec["gate"],
                title=spec["title"],
                metric=metric_name,
                status="FAIL" if breached else "PASS",
                observed=value,
                reason=reason,
            )
        )
    return outcomes


# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------

def evaluator_coverage(
    results: list[EvalResult], weights: dict[str, float]
) -> tuple[float, dict[str, tuple[int, int]]]:
    """How much of the declared risk surface was actually measured.

    Counted per *evaluator*, not per gate. The earlier version summed the weight of
    gates that were not entirely excluded, which treats a gate as fully measured the
    moment one of its evaluators runs. On 2026-08-22 that reported 0.85 while only
    0.54 of the surface had been measured -- ai_security was credited its full 0.22
    with 4 of 7 evaluators running, and the two that did not run were the BOLA probe
    and the malicious-upload probe.

    Returns the weighted coverage and, per gate, ``(ran, declared)`` so the report can
    show a reader exactly where the holes are rather than a single opaque fraction.
    """
    ran: dict[str, int] = {}
    declared: dict[str, int] = {}
    for result in results:
        if result.gate not in weights:
            continue  # preflight and anything outside the weighted set
        declared[result.gate] = declared.get(result.gate, 0) + 1
        if result.counts_toward_aggregate():
            ran[result.gate] = ran.get(result.gate, 0) + 1

    detail = {
        gate: (ran.get(gate, 0), declared.get(gate, 0))
        for gate in weights
        if declared.get(gate)
    }
    covered = sum(
        weight * (ran.get(gate, 0) / declared[gate])
        for gate, weight in weights.items()
        if declared.get(gate)
    )
    return covered, detail


def re_normalize_weights(
    weights: dict[str, float], excluded: set[str]
) -> dict[str, float]:
    """Drop excluded gates and scale the rest back up to 1.0."""
    kept = {g: w for g, w in weights.items() if g not in excluded}
    total = sum(kept.values())
    if total <= 0:
        return {}
    return {g: w / total for g, w in kept.items()}


def aggregate(results: list[EvalResult], *, profile: str = "local") -> AggregateOutcome:
    central_policy = load_policy("evaluation_policy")
    weights: dict[str, float] = central_policy["score"]["weights"]
    bands = central_policy["score"]["decision_bands"]
    measured_floor = float(central_policy["minimum_measured_weight"])

    # 1. Namespace integrity and hard gates first -- always, regardless of scores.
    collisions = detect_metric_collisions(results)
    configuration_errors = [
        str(result.metadata.get("configuration_error"))
        for result in results
        if result.metadata.get("configuration_error")
    ]
    hard_gates = evaluate_hard_gates(results)
    mandatory_hard = set(central_policy.get("mandatory_hard_gates", {}).get(profile, []))
    missing_hard = [h for h in hard_gates if h.id in mandatory_hard and h.status == "NOT_EVALUATED"]
    blocking = [
        finding
        for result in results
        for finding in result.critical_findings
        if finding.blocks_release
    ]
    any_hard_gate_failed = any(h.status == "FAIL" for h in hard_gates)
    mandatory_results = [r for r in results if r.metadata.get("mandatory")]
    mandatory_ok = [r for r in mandatory_results if r.status not in EXCLUDED_FROM_AGGREGATE]
    mandatory_coverage = (
        len(mandatory_ok) / len(mandatory_results)
        if mandatory_results else (1.0 if profile == "local" else 0.0)
    )
    mandatory_errors = [
        r for r in mandatory_results
        if r.status in {EvalStatus.EVALUATOR_ERROR, EvalStatus.MISSING_MANDATORY_EVIDENCE}
    ]
    mandatory_failures = [r for r in mandatory_results if r.status == EvalStatus.FAIL]

    # Blocking conditions that carry no finding id. See AggregateOutcome.block_reasons.
    block_reasons: list[str] = []
    if missing_hard:
        block_reasons.append(
            "mandatory hard gate(s) not evaluated: "
            + ", ".join(sorted(h.id for h in missing_hard))
        )
    if mandatory_errors:
        block_reasons.append(
            "mandatory evaluator(s) could not run: "
            + ", ".join(sorted(r.evaluator for r in mandatory_errors))
        )
    if mandatory_failures:
        block_reasons.append(
            "mandatory evaluator(s) failed: "
            + ", ".join(sorted(r.evaluator for r in mandatory_failures))
        )
    if mandatory_coverage < 1.0:
        block_reasons.append(
            f"mandatory evidence coverage {mandatory_coverage:.2f} is below 1.00"
        )

    # 2. Gate scores, collapsing multi-dataset evaluators.
    per_gate: dict[str, list[float]] = {}
    excluded_reasons: dict[str, str] = {}
    for result in results:
        score = collapse_result_scores(result)
        if not result.counts_toward_aggregate() or score is None:
            excluded_reasons.setdefault(result.gate, result.status.value)
            continue
        per_gate.setdefault(result.gate, []).append(score)

    gate_scores: dict[str, float | None] = {}
    for gate in weights:
        scores = per_gate.get(gate)
        gate_scores[gate] = (sum(scores) / len(scores)) if scores else None

    excluded = {g for g, s in gate_scores.items() if s is None}
    for gate in excluded:
        excluded_reasons.setdefault(gate, EvalStatus.NOT_IMPLEMENTED.value)

    effective = re_normalize_weights(weights, excluded)
    measured_weight, coverage_detail = evaluator_coverage(results, weights)
    total_score = (
        sum(gate_scores[g] * w for g, w in effective.items()) if effective else None
    )

    # 3. Decision. Hard gates come first, then coverage, then the score band: a
    #    number computed from too little evidence should not be presented at all.
    if collisions or configuration_errors:
        decision = Decision.EVALGATE_INVALID
    elif any_hard_gate_failed or blocking or block_reasons:
        decision = Decision.RELEASE_BLOCKED
    elif total_score is None:
        decision = Decision.FAIL
    elif measured_weight < measured_floor:
        decision = Decision.INSUFFICIENT_COVERAGE
    elif total_score >= bands["pass"]:
        decision = Decision.PASS
    elif total_score >= bands["warning"]:
        decision = Decision.WARNING
    else:
        decision = Decision.FAIL

    # Withholding the number is separate from the decision, and has to be, because a
    # failing hard gate preempts the INSUFFICIENT_COVERAGE branch above. Without this,
    # an under-measured run still published a score whenever anything was blocking --
    # which is every run that matters. The rule the comment above states is only
    # actually enforced here.
    provisional_score: float | None = None
    score_withheld_reason: str | None = None
    published_score = total_score
    if total_score is not None and measured_weight < measured_floor:
        provisional_score = round(total_score, 2)
        published_score = None
        thin = ", ".join(
            f"{gate} {ran}/{dec}"
            for gate, (ran, dec) in sorted(coverage_detail.items())
            if dec and ran < dec
        )
        score_withheld_reason = (
            f"measured coverage {measured_weight:.2f} is below the "
            f"{measured_floor:.2f} floor; partially measured gates: {thin}"
        )

    gate_verdicts = {
        gate: (
            "NOT_MEASURED" if not any(r.gate == gate and r.counts_toward_aggregate() for r in results)
            else "FAIL" if any(r.gate == gate and r.status == EvalStatus.FAIL for r in results)
            else "PASS"
        )
        for gate in {r.gate for r in results if r.gate != "preflight"}
    }
    return AggregateOutcome(
        decision=decision,
        score=round(published_score, 2) if published_score is not None else None,
        gate_scores=gate_scores,
        effective_weights={g: round(w, 4) for g, w in effective.items()},
        excluded_gates={g: excluded_reasons.get(g, "unknown") for g in excluded},
        hard_gates=hard_gates,
        blocking_findings=blocking,
        measured_weight=round(measured_weight, 4),
        coverage_detail=coverage_detail,
        provisional_score=provisional_score,
        score_withheld_reason=score_withheld_reason,
        metric_collisions=collisions,
        override_reason="; ".join(configuration_errors) or None,
        mandatory_evidence_coverage=round(mandatory_coverage, 4),
        gate_verdicts=gate_verdicts,
        block_reasons=block_reasons,
    )
