"""Gate 1H: is the anomaly verdict this run produced supported by this run's evidence?

The detector's arithmetic -- median, MAD, the zero-MAD fallback -- is unit-tested in
``tests/test_anomaly_math.py``, which is where a claim about ``calculate_robust_zscore``
belongs. What that arithmetic cannot tell you is whether the verdict the product *shipped*
is defensible, and that is the only question a release gate should ask here.

Four coherence rules, all decidable from the bundle alone:

1. **Abstention.** With one run and nothing to compare against, ``INSUFFICIENT_HISTORY``
   is the only honest answer. ``NORMAL`` asserts a stability that has not been observed,
   and a steward reading ``NORMAL`` cannot tell the two apart.
2. **Support.** Declaring an anomaly while every rule passed is a verdict with no
   evidence under it. The converse is deliberately not checked: failures inside normal
   variance are exactly what a detector is supposed to absorb, so ``NORMAL`` alongside
   failures is legitimate and flagging it would punish the detector for working.
3. **Explanation.** An anomaly with no hypothesis is a verdict a steward cannot act on.
4. **Range.** Score and confidence are ratios; a value outside [0, 1] means the scale the
   report is read on is not the scale it was written on.

The previous version ran seven bare ``assert`` statements against a synthetic SQLite
database. Two consequences: ``python -O`` erased them, and a product regression raised
``AssertionError`` -- which the runner records as ``EVALUATOR_ERROR``, a status that is
*excluded from the aggregate*. The product getting worse made the score go up.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from evalgate.core.context import EvalRunContext
from evalgate.normalizers import normalizers as norm
from evalgate.schemas.eval_result import (
    EvalResult,
    EvalStatus,
    Evidence,
    Finding,
    MetricValue,
    Severity,
    Threshold,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = PROJECT_ROOT / "evalgate" / "evidence" / "gate1"

GATE = "ai_quality"
EVALUATOR = "anomaly_logic_probe_v1"

#: Verdicts that assert something happened, and therefore need evidence under them.
ASSERTIVE = {"ANOMALY", "WATCH", "ALERT", "CRITICAL"}

#: The honest answer when there is no history to compare against.
ABSTENTION = "INSUFFICIENT_HISTORY"

#: Below this many observed runs, a stability claim has nothing to rest on.
COLD_START_RUNS = 2


@dataclass
class Coherence:
    rule: str
    passed: bool
    detail: str


def _payload(document: Any) -> dict[str, Any]:
    if isinstance(document, dict) and isinstance(document.get("payload"), dict):
        return document["payload"]
    return document if isinstance(document, dict) else {}


def _history_runs(context: EvalRunContext) -> int:
    """How many runs the detector could have compared against."""
    if not context.records("run-outcome"):
        return 1
    document = context.read_json("run-outcome")
    runs = document.get("runs", []) if isinstance(document, dict) else []
    return max(1, len(runs))


def check_coherence(
    anomaly: dict[str, Any], results: list[dict[str, Any]], history_runs: int
) -> list[Coherence]:
    decision = str(anomaly.get("decision") or "").upper()
    hypotheses = [h for h in (anomaly.get("hypotheses") or []) if isinstance(h, dict)]
    failed = sum(
        1 for r in results if str(r.get("status", "")).upper() in {"FAIL", "FAILED"}
    )
    checks: list[Coherence] = []

    if history_runs < COLD_START_RUNS:
        checks.append(
            Coherence(
                "abstains_without_history",
                decision == ABSTENTION,
                f"{history_runs} run observed; decision={decision or 'none'}. "
                f"{ABSTENTION} is the only answer a single run supports",
            )
        )

    if decision in ASSERTIVE:
        checks.append(
            Coherence(
                "assertive_verdict_has_failures",
                failed > 0,
                f"decision={decision} with {failed} failing rule(s)",
            )
        )
        checks.append(
            Coherence(
                "assertive_verdict_is_explained",
                bool(hypotheses),
                f"decision={decision} with {len(hypotheses)} hypothesis(es)",
            )
        )

    for field in ("score", "confidence"):
        value = anomaly.get(field)
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            checks.append(Coherence(f"{field}_is_numeric", False, f"{field}={value!r}"))
            continue
        checks.append(
            Coherence(f"{field}_within_range", 0.0 <= numeric <= 1.0, f"{field}={numeric}")
        )

    return checks


def evaluate(
    *, write_evidence: bool = True, context: EvalRunContext | None = None
) -> EvalResult:
    if context is None or not context.records("anomaly-report"):
        return EvalResult(
            gate=GATE, evaluator=EVALUATOR, status=EvalStatus.NOT_MEASURED,
            metadata={
                "reason": (
                    "the verdict is graded against the bundle's own anomaly-report; "
                    "no such artifact is available"
                )
            },
        )

    anomaly = _payload(
        json.loads(context.path_for(context.records("anomaly-report")[0]).read_text(encoding="utf-8"))
    )
    results: list[dict[str, Any]] = []
    if context.records("execution-results"):
        execution = json.loads(
            context.path_for(context.records("execution-results")[0]).read_text(encoding="utf-8")
        )
        results = execution.get("test_results") or execution.get("results") or []

    history_runs = _history_runs(context)
    checks = check_coherence(anomaly, results, history_runs)
    if not checks:
        return EvalResult(
            gate=GATE, evaluator=EVALUATOR, status=EvalStatus.NOT_MEASURED,
            metadata={"reason": "the anomaly report carries nothing that can be checked"},
        )

    failed_checks = [c for c in checks if not c.passed]
    rate = (len(checks) - len(failed_checks)) / len(checks)
    decision = str(anomaly.get("decision") or "").upper()
    abstained = next(
        (c for c in checks if c.rule == "abstains_without_history"), None
    )

    evidence: list[Evidence] = []
    if write_evidence:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        target = EVIDENCE_DIR / "anomaly_logic_probe.json"
        target.write_text(
            json.dumps(
                {
                    "decision": decision,
                    "status": anomaly.get("status"),
                    "score": anomaly.get("score"),
                    "confidence": anomaly.get("confidence"),
                    "history_runs": history_runs,
                    "failing_rules": sum(
                        1 for r in results
                        if str(r.get("status", "")).upper() in {"FAIL", "FAILED"}
                    ),
                    "checks": [asdict(c) for c in checks],
                },
                ensure_ascii=False, indent=2, default=str,
            ),
            encoding="utf-8",
        )
        evidence.append(Evidence(type="file", path=str(target.relative_to(PROJECT_ROOT))))

    findings: list[Finding] = []
    if abstained is not None and not abstained.passed:
        findings.append(
            Finding(
                id="ANOMALY-NO-ABSTENTION",
                severity=Severity.HIGH,
                title=f"The detector answered {decision or 'nothing'} with no history to compare against",
                detail=(
                    f"{abstained.detail}. A steward reading this cannot tell an observed "
                    "stability from an assumed one."
                ),
                root_cause_hint=(
                    "the cold-start branch returns a verdict instead of INSUFFICIENT_HISTORY"
                ),
                evidence_ref="evalgate/evidence/gate1/anomaly_logic_probe.json",
                blocks_release=False,
            )
        )
    unsupported = [
        c for c in failed_checks
        if c.rule in {"assertive_verdict_has_failures", "assertive_verdict_is_explained"}
    ]
    if unsupported:
        findings.append(
            Finding(
                id="ANOMALY-UNSUPPORTED",
                severity=Severity.HIGH,
                title=f"The {decision} verdict is not supported by this run's evidence",
                detail="; ".join(f"{c.rule}: {c.detail}" for c in unsupported),
                root_cause_hint=(
                    "the verdict was produced without reading the execution results it "
                    "is supposed to describe"
                ),
                evidence_ref="evalgate/evidence/gate1/anomaly_logic_probe.json",
                blocks_release=False,
            )
        )

    return EvalResult(
        gate=GATE,
        evaluator=EVALUATOR,
        status=EvalStatus.FAIL if failed_checks else EvalStatus.PASS,
        score=norm.ratio(rate),
        metrics={
            "anomaly_verdict_coherence": MetricValue(
                raw=round(rate, 4), unit="ratio", normalized=norm.ratio(rate),
                note=f"{len(checks) - len(failed_checks)}/{len(checks)} coherence rules hold",
            ),
            "anomaly_coherence_rules_checked": MetricValue(
                raw=len(checks), unit="count", normalized=None
            ),
            "anomaly_abstains_on_cold_start": MetricValue(
                raw=bool(abstained.passed) if abstained else None,
                unit="boolean",
                normalized=norm.boolean(abstained.passed) if abstained else None,
                status=None if abstained else EvalStatus.NOT_APPLICABLE,
                note=(
                    f"{history_runs} run(s) of history"
                    if abstained
                    else "enough history exists; abstention is not required"
                ),
            ),
            "anomaly_verdict_unsupported_count": MetricValue(
                raw=len(unsupported), unit="count",
                normalized=norm.zero_tolerance(len(unsupported)),
            ),
        },
        thresholds={
            "anomaly_verdict_coherence": Threshold(**{"pass": 100.0, "warn": 100.0}),
        },
        evidence=evidence,
        critical_findings=findings,
        metadata={
            "mode": "verdict coherence against the bundle's own artifacts",
            "decision": decision,
            "history_runs": history_runs,
            "failed_rules": [c.rule for c in failed_checks],
        },
    )
