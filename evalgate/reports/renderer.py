"""Render an EvalGate run as JSON and as a readable Markdown report."""

from __future__ import annotations

from typing import Any

from evalgate.aggregator import AggregateOutcome
from evalgate.schemas.eval_result import EvalResult

_STATUS_ICON = {
    "PASS": "PASS",
    "WARN": "WARN",
    "FAIL": "FAIL",
}


def render_json(
    results: list[EvalResult], outcome: AggregateOutcome, *, mode: str
) -> dict[str, Any]:
    return {
        "decision": outcome.decision,
        "score": outcome.score,
        "exit_code": outcome.exit_code,
        "mode": mode,
        "run_id": results[0].run_id if results else None,
        "git_ref": results[0].git_ref if results else None,
        "timestamp": results[0].timestamp if results else None,
        "gate_scores": outcome.gate_scores,
        "effective_weights": outcome.effective_weights,
        "excluded_gates": outcome.excluded_gates,
        "measured_weight": outcome.measured_weight,
        "coverage_detail": {g: list(v) for g, v in outcome.coverage_detail.items()},
        "provisional_score": outcome.provisional_score,
        "score_withheld_reason": outcome.score_withheld_reason,
        "override_reason": outcome.override_reason,
        "baseline_run_id": next(
            (r.baseline_run_id for r in results if r.baseline_run_id), None
        ),
        "hard_gates": [
            {
                "id": h.id, "gate": h.gate, "title": h.title, "metric": h.metric,
                "status": h.status, "observed": h.observed, "reason": h.reason,
            }
            for h in outcome.hard_gates
        ],
        "results": [r.model_dump(mode="json", by_alias=True) for r in results],
    }


def _table(rows: list[list[str]], headers: list[str]) -> str:
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join("---" for _ in headers) + "|"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def render_markdown(
    results: list[EvalResult], outcome: AggregateOutcome, *, mode: str
) -> str:
    head = results[0] if results else None
    parts: list[str] = [
        "# EvalGate Report",
        "",
        f"- **Decision:** `{outcome.decision}` (exit code {outcome.exit_code})",
        (
            f"- **Score:** {outcome.score}"
            if outcome.score is not None
            else f"- **Score:** WITHHELD — {outcome.score_withheld_reason}"
            if outcome.score_withheld_reason
            else "- **Score:** n/a"
        ),
        f"- **Mode:** `{mode}`",
        f"- **Run:** `{head.run_id if head else '-'}`",
        f"- **Git ref:** `{head.git_ref if head else '-'}`",
        f"- **Timestamp:** {head.timestamp if head else '-'}",
        f"- **Measured coverage:** {outcome.measured_weight * 100:.1f}% "
        f"(counted per evaluator, not per gate)",
        "- **Coverage by gate:** "
        + " · ".join(
            f"{g} {ran}/{dec}" for g, (ran, dec) in sorted(outcome.coverage_detail.items())
        ),
    ]
    baseline = next((r.baseline_run_id for r in results if r.baseline_run_id), None)
    parts.append(f"- **Baseline:** `{baseline or 'none stored yet'}`")
    if outcome.override_reason:
        parts += [
            "",
            f"> **This verdict is qualified.** {outcome.override_reason}",
        ]
    parts += [
        "",
        "## Hard gates",
        "",
        "Evaluated before the aggregate. A failing hard gate blocks release regardless of score.",
        "",
    ]

    parts.append(
        _table(
            [
                [h.id, h.gate, h.title, h.status,
                 "" if h.observed is None else str(h.observed)]
                for h in outcome.hard_gates
            ],
            ["ID", "Gate", "Condition", "Status", "Observed"],
        )
    )

    parts += ["", "## Gate scores", ""]
    parts.append(
        _table(
            [
                [
                    gate,
                    "n/a" if score is None else f"{score:.2f}",
                    f"{outcome.effective_weights.get(gate, 0) * 100:.1f}%"
                    if gate in outcome.effective_weights else "excluded",
                    outcome.excluded_gates.get(gate, ""),
                ]
                for gate, score in outcome.gate_scores.items()
            ],
            ["Gate", "Score", "Effective weight", "Excluded because"],
        )
    )

    parts += ["", "## Evaluators", ""]
    parts.append(
        _table(
            [
                [
                    r.evaluator, r.gate, r.status.value,
                    "n/a" if r.score is None else f"{r.score:.2f}",
                    str(r.metadata.get("reason", ""))[:80],
                ]
                for r in results
            ],
            ["Evaluator", "Gate", "Status", "Score", "Note"],
        )
    )

    findings = [f for r in results for f in r.critical_findings]
    if findings:
        parts += ["", "## Critical findings", ""]
        for finding in findings:
            parts += [
                f"### {finding.id} — {finding.title}",
                "",
                f"- **Severity:** {finding.severity.value}",
                f"- **Blocks release:** {finding.blocks_release}",
                f"- **Detail:** {finding.detail}",
            ]
            if finding.root_cause_hint:
                parts.append(f"- **Root cause hint:** {finding.root_cause_hint}")
            if finding.evidence_ref:
                parts.append(f"- **Evidence:** `{finding.evidence_ref}`")
            parts.append("")

    parts += ["", "## Metrics", ""]
    rows = [
        [r.evaluator, name,
         "null" if metric.raw is None else str(metric.raw),
         metric.unit,
         "n/a" if metric.normalized is None else f"{metric.normalized:.2f}"]
        for r in results
        for name, metric in r.metrics.items()
    ]
    parts.append(_table(rows, ["Evaluator", "Metric", "Raw", "Unit", "Normalized"]))

    blocked = [
        r for r in results
        if r.status.value.startswith(("BLOCKED", "NOT_"))
    ]
    if blocked:
        parts += [
            "", "## Not measured", "",
            "These are reported rather than hidden: a capability gap in the product is not "
            "the same thing as a missing evaluator.", "",
        ]
        parts.append(
            _table(
                [[r.evaluator, r.gate, r.status.value, str(r.metadata.get("reason", ""))]
                 for r in blocked],
                ["Evaluator", "Gate", "Status", "Reason"],
            )
        )

    parts.append("")
    return "\n".join(parts)
