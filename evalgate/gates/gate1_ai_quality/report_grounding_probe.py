"""Gate 1G: does every figure in the steward's report exist in the execution results?

The anomaly narrative is the one artefact a Data Steward reads instead of the data. It
says things like *"14 quy tắc kiểm thử bị vi phạm nghiêm trọng"*. If that 14 came from
the model rather than from the run, the steward is acting on a number nobody computed --
and it is the most expensive kind of wrong, because it is expensive precisely when it is
believed.

Grounding is checkable without a judge. Pull every integer out of the narrative, build
the set of figures the execution actually produced, and ask whether each one resolves.
A number that matches nothing is not proof of a hallucination -- a report may legitimately
mention a threshold or a year -- so small values and four-digit years are excluded and the
rest is reported as a rate rather than asserted as a lie.

The previous version rendered a hand-built Vietnamese template with hard-coded arguments
and checked the output contained the strings it had just passed in. That is a template
test, it returned 100.0 for every run, and it said nothing about the report the product
actually produced. It now lives in ``tests/test_report_rendering.py``.
"""

from __future__ import annotations

import json
import re
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
EVALUATOR = "report_grounding_probe_v1"

_INTEGER = re.compile(r"\b\d{1,9}\b")

#: Numbers below this are ordinals, list positions and rhetorical counts ("2 bước"),
#: not claims about the data. Checking them produces noise, not findings.
_MIN_CHECKED = 3

#: Four-digit values in a plausible year range are dates, not measurements.
_YEAR_RANGE = range(1990, 2100)


@dataclass
class NumberClaim:
    value: int
    field: str
    grounded: bool
    matched: str | None


def _payload(document: Any) -> dict[str, Any]:
    if isinstance(document, dict) and isinstance(document.get("payload"), dict):
        return document["payload"]
    return document if isinstance(document, dict) else {}


def executed_figures(results: list[dict[str, Any]]) -> dict[int, str]:
    """Every integer the execution legitimately supports, and what it stands for."""
    figures: dict[int, str] = {}

    def record(value: Any, label: str) -> None:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return
        figures.setdefault(number, label)

    statuses = [str(r.get("status", "")).upper() for r in results]
    record(len(results), "total rules executed")
    record(sum(1 for s in statuses if s in {"FAIL", "FAILED"}), "rules failed")
    record(sum(1 for s in statuses if s in {"PASS", "PASSED"}), "rules passed")
    record(sum(1 for s in statuses if s in {"ERROR", "ERRORED"}), "rules errored")

    for entry in results:
        record(entry.get("failed_count"), f"failed_count of {entry.get('rule_id')}")
        record(entry.get("violation_count"), f"violation_count of {entry.get('rule_id')}")
        record(entry.get("checked_count"), f"checked_count of {entry.get('rule_id')}")
        record(entry.get("total_rows"), "total_rows")
        rate = entry.get("violation_rate")
        if isinstance(rate, (int, float)):
            record(round(rate * 100), f"violation_rate% of {entry.get('rule_id')}")
    return figures


def _narrative_fields(anomaly: dict[str, Any]) -> list[tuple[str, str]]:
    """Every free-text field a steward reads, paired with its path."""
    fields: list[tuple[str, str]] = []
    for key in ("summary", "narrative", "explanation", "recommendation"):
        value = anomaly.get(key)
        if isinstance(value, str) and value.strip():
            fields.append((key, value))
    for index, hypothesis in enumerate(anomaly.get("hypotheses") or []):
        if not isinstance(hypothesis, dict):
            continue
        for key, value in hypothesis.items():
            if isinstance(value, str) and value.strip():
                fields.append((f"hypotheses[{index}].{key}", value))
            elif isinstance(value, list):
                for position, item in enumerate(value):
                    if isinstance(item, str) and item.strip():
                        fields.append((f"hypotheses[{index}].{key}[{position}]", item))
    return fields


def check_grounding(anomaly: dict[str, Any], results: list[dict[str, Any]]) -> list[NumberClaim]:
    figures = executed_figures(results)
    claims: list[NumberClaim] = []
    for field, text in _narrative_fields(anomaly):
        for token in _INTEGER.findall(text):
            value = int(token)
            if value < _MIN_CHECKED or value in _YEAR_RANGE:
                continue
            claims.append(
                NumberClaim(value, field, value in figures, figures.get(value))
            )
    return claims


def evaluate(
    *, write_evidence: bool = True, context: EvalRunContext | None = None
) -> EvalResult:
    if context is None or not context.records("anomaly-report") or not context.records("execution-results"):
        return EvalResult(
            gate=GATE, evaluator=EVALUATOR, status=EvalStatus.NOT_MEASURED,
            metadata={
                "reason": (
                    "grounding is measured by resolving the report's own figures against "
                    "the execution results; both artifacts are required"
                )
            },
        )

    anomaly = _payload(
        json.loads(context.path_for(context.records("anomaly-report")[0]).read_text(encoding="utf-8"))
    )
    execution = json.loads(
        context.path_for(context.records("execution-results")[0]).read_text(encoding="utf-8")
    )
    results = execution.get("test_results") or execution.get("results") or []

    if not results:
        return EvalResult(
            gate=GATE, evaluator=EVALUATOR, status=EvalStatus.NOT_MEASURED,
            metadata={"reason": "the execution-results artifact carries no rule outcomes"},
        )

    claims = check_grounding(anomaly, results)
    ungrounded = [c for c in claims if not c.grounded]
    grounding_rate = (len(claims) - len(ungrounded)) / len(claims) if claims else None

    # Structural facts a steward relies on, independent of the prose.
    decision = str(anomaly.get("decision") or "").upper()
    status = str(anomaly.get("status") or "").upper()
    produced_decision = bool(decision) and decision != "UNAVAILABLE"
    consistent = not (status == "SUCCEEDED" and anomaly.get("error"))
    linked = bool(anomaly.get("execution_run_id"))

    evidence: list[Evidence] = []
    if write_evidence:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        target = EVIDENCE_DIR / "report_grounding_probe.json"
        target.write_text(
            json.dumps(
                {
                    "decision": decision,
                    "status": status,
                    "execution_run_id": anomaly.get("execution_run_id"),
                    "figures_available": executed_figures(results),
                    "numbers_checked": len(claims),
                    "claims": [asdict(c) for c in claims],
                    "ungrounded": [asdict(c) for c in ungrounded],
                },
                ensure_ascii=False, indent=2, default=str,
            ),
            encoding="utf-8",
        )
        evidence.append(Evidence(type="file", path=str(target.relative_to(PROJECT_ROOT))))

    findings: list[Finding] = []
    if ungrounded:
        findings.append(
            Finding(
                id="REPORT-UNGROUNDED",
                severity=Severity.HIGH,
                title=f"{len(ungrounded)} figure(s) in the steward report resolve to nothing",
                detail=(
                    "The steward reads this instead of the data. Numbers with no source "
                    "in the execution results: "
                    + "; ".join(f"{c.value} in {c.field}" for c in ungrounded[:6])
                ),
                root_cause_hint=(
                    "the narrative was generated from the model's own summary rather than "
                    "from the execution figures passed to it"
                ),
                evidence_ref="evalgate/evidence/gate1/report_grounding_probe.json",
                blocks_release=False,
            )
        )
    if not produced_decision:
        findings.append(
            Finding(
                id="REPORT-NO-DECISION",
                severity=Severity.HIGH,
                title="The anomaly report carries no usable decision",
                detail=f"decision={decision!r}, status={status!r}, error={anomaly.get('error')!r}",
                root_cause_hint="the investigation node fell back without producing a verdict",
                evidence_ref="evalgate/evidence/gate1/report_grounding_probe.json",
                blocks_release=False,
            )
        )

    structural = [produced_decision, consistent, linked]
    structural_rate = sum(1 for value in structural if value) / len(structural)
    score = (
        norm.ratio((grounding_rate + structural_rate) / 2)
        if grounding_rate is not None
        else norm.ratio(structural_rate)
    )

    return EvalResult(
        gate=GATE,
        evaluator=EVALUATOR,
        status=EvalStatus.FAIL if findings else EvalStatus.PASS,
        score=score,
        metrics={
            "report_figure_grounding_rate": MetricValue(
                raw=round(grounding_rate, 4) if grounding_rate is not None else None,
                unit="ratio",
                normalized=norm.ratio(grounding_rate) if grounding_rate is not None else None,
                status=None if grounding_rate is not None else EvalStatus.NOT_MEASURED,
                note=(
                    f"{len(claims) - len(ungrounded)}/{len(claims)} narrative figures resolve"
                    if grounding_rate is not None
                    else "the narrative cites no checkable figure"
                ),
            ),
            # Denominator, published: 1.0 over zero citations is a report that says
            # nothing, not a report that is accurate.
            "report_figures_checked": MetricValue(
                raw=len(claims), unit="count", normalized=None
            ),
            "report_produced_decision": MetricValue(
                raw=produced_decision, unit="boolean", normalized=norm.boolean(produced_decision),
                note=f"decision={decision or 'none'}",
            ),
            "report_linked_to_execution": MetricValue(
                raw=linked, unit="boolean", normalized=norm.boolean(linked),
                note="the report names the execution run it describes",
            ),
        },
        thresholds={
            "report_figure_grounding_rate": Threshold(**{"pass": 100.0, "warn": 95.0}),
        },
        evidence=evidence,
        critical_findings=findings,
        metadata={
            "mode": "figures resolved against the bundle's own execution-results",
            "decision": decision,
            "ungrounded_examples": [c.value for c in ungrounded[:5]],
        },
    )
