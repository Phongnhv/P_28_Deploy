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


EXIT_CODES = {
    Decision.PASS: 0,
    Decision.WARNING: 1,
    Decision.FAIL: 2,
    Decision.RELEASE_BLOCKED: 3,
}


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
    scored = [
        d.score
        for d in result.per_dataset_breakdown
        if d.status not in EXCLUDED_FROM_AGGREGATE and d.score is not None
    ]
    if not scored:
        return None
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

def re_normalize_weights(
    weights: dict[str, float], excluded: set[str]
) -> dict[str, float]:
    """Drop excluded gates and scale the rest back up to 1.0."""
    kept = {g: w for g, w in weights.items() if g not in excluded}
    total = sum(kept.values())
    if total <= 0:
        return {}
    return {g: w / total for g, w in kept.items()}


def aggregate(results: list[EvalResult]) -> AggregateOutcome:
    weights_policy = load_policy("weights")
    weights: dict[str, float] = weights_policy["weights"]
    bands = weights_policy["decision_bands"]

    # 1. Hard gates first -- always, regardless of scores.
    hard_gates = evaluate_hard_gates(results)
    blocking = [
        finding
        for result in results
        for finding in result.critical_findings
        if finding.blocks_release
    ]
    any_hard_gate_failed = any(h.status == "FAIL" for h in hard_gates)

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
    total_score = (
        sum(gate_scores[g] * w for g, w in effective.items()) if effective else None
    )

    # 3. Decision.
    if any_hard_gate_failed or blocking:
        decision = Decision.RELEASE_BLOCKED
    elif total_score is None:
        decision = Decision.FAIL
    elif total_score >= bands["pass"]:
        decision = Decision.PASS
    elif total_score >= bands["warning"]:
        decision = Decision.WARNING
    else:
        decision = Decision.FAIL

    return AggregateOutcome(
        decision=decision,
        score=round(total_score, 2) if total_score is not None else None,
        gate_scores=gate_scores,
        effective_weights={g: round(w, 4) for g, w in effective.items()},
        excluded_gates={g: excluded_reasons.get(g, "unknown") for g in excluded},
        hard_gates=hard_gates,
        blocking_findings=blocking,
    )
