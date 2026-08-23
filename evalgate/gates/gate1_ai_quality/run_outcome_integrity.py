"""Gate 1D: did the agent's last run actually produce anything?

Every other evaluator in this gate grades the *content* of what the agent
produced -- are the rules vacuous, do they match the golden set, do they respect
the governed domains.  All of them are silent when the answer is "the agent
produced nothing at all", because a run that emits no rules also emits no rules
to find fault with.

That silence was demonstrated, not theorised.  A live proposal run failed
outright on 2026-08-22 and the aggregate score did not move by a hundredth of a
point: 28.03 before, 28.03 after.  A total failure passed through the gate
without leaving a mark, because no evaluator was asking the first question a
reviewer would ask.

This evaluator asks it.  It reads the correlated artefact stream the product
already writes to ``output/<stage>/<name>_<date>_<time>_<run_id>.json`` and, for
each run, establishes three facts:

  did the run reach its workflow's terminal stage
  did that stage carry any output
  how much of the model's structured output was rejected by the product's own
    validators

The third fact is what ``HG-A2`` (schema violation rate) was deferred for.  The
precondition recorded there -- "a live agent invocation" -- has since been met,
and the rejection counts are sitting in the artefacts.

No product code is imported and nothing is executed: this reads files the
product already wrote.  When there are no artefacts (a clean CI checkout, where
``output/`` is git-ignored) the evaluator reports NOT_MEASURED with the reason
attached, rather than inventing a pass.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
OUTPUT_DIR = PROJECT_ROOT / "output"
EVIDENCE_DIR = PROJECT_ROOT / "evalgate" / "evidence" / "gate1"

GATE = "ai_quality"
EVALUATOR = "run_outcome_integrity_v1"

#: ``<anything>_<YYYYMMDD>_<HHMMSS>_<32 hex>.json`` -- the product's own correlator.
#: Artefacts written before this convention existed carry no run id and are skipped
#: rather than guessed at; an uncorrelated file cannot be attributed to a run.
_ARTEFACT = re.compile(r"_(\d{8})_(\d{6})_([0-9a-f]{32})\.json$")

#: Pydantic's own preamble, e.g. "15 validation errors for TableRuleProposal".
_VALIDATION_ERRORS = re.compile(r"(\d+)\s+validation errors?\s+for\s+(\w+)")

#: Why a run produced nothing. "It produced nothing" is the symptom; these are the
#: causes, and they belong to different owners. A model that answers with the wrong
#: shape is a prompt-versus-schema problem; a model that never answers inside the
#: client timeout is a configuration problem. Reporting both as "structured output
#: was rejected" sends the wrong team looking -- which is exactly what happened when
#: a 25s client timeout was introduced and every proposal call began timing out.
FAILURE_KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("TIMEOUT", re.compile(r"timed out|timeout|ReadTimeout|deadline exceeded", re.I)),
    ("SCHEMA_REJECTED", _VALIDATION_ERRORS),
    ("RATE_LIMITED", re.compile(r"rate.?limit|429", re.I)),
    ("AUTH", re.compile(r"401|403|invalid.{0,10}api.?key|unauthor", re.I)),
    ("NO_CANDIDATES", re.compile(r"no candidate|0 candidates|rỗng", re.I)),
)

#: How many recent runs the trend metrics look at. Small on purpose: a long window
#: lets a wall of healthy history from last month hide a system that is broken today.
RECENT_RUN_WINDOW = 5


@dataclass
class Workflow:
    """One of the product's two graphs, identified by the stages it writes."""

    name: str
    terminal_stage: str
    #: A stage only this workflow ever writes, used to attribute a run.
    signature_stages: frozenset[str]
    #: Field in the terminal artefact holding the count of things produced.
    output_count_field: str


WORKFLOWS: tuple[Workflow, ...] = (
    Workflow(
        name="proposal",
        terminal_stage="rule_proposer",
        signature_stages=frozenset({"rule_proposer", "candidates", "prompts"}),
        output_count_field="total_rules",
    ),
    Workflow(
        name="execution",
        terminal_stage="reports",
        signature_stages=frozenset({"test_runner", "results", "reports"}),
        output_count_field="total_rules_tested",
    ),
)


@dataclass
class RunOutcome:
    run_id: str
    workflow: str | None
    started_at: str
    stages: set[str] = field(default_factory=set)
    reached_terminal: bool = False
    output_count: int | None = None
    #: Structured-output items the product's validators refused.
    schema_rejections: int = 0
    #: Structured-output items the product's validators accepted.
    schema_accepted: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def failure_kind(self) -> str | None:
        """Why this run produced nothing, or None when it produced something.

        First match wins, and TIMEOUT is checked first on purpose: a run that timed
        out may still carry stale validation text from an earlier attempt, and the
        timeout is the cause that actually stopped it.
        """
        if self.produced_output:
            return None
        blob = " ".join(self.errors)
        if not blob:
            return "NO_ERROR_RECORDED"
        for name, pattern in FAILURE_KINDS:
            if pattern.search(blob):
                return name
        return "OTHER"

    @property
    def produced_output(self) -> bool:
        return bool(self.reached_terminal and (self.output_count or 0) > 0)

    @property
    def verdict(self) -> str:
        if not self.workflow:
            return "UNATTRIBUTED"
        if not self.reached_terminal:
            return "DIED_EARLY"
        if not self.produced_output:
            return "EMPTY_OUTPUT"
        return "PRODUCED_OUTPUT"


def _read(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def collect_runs(output_dir: Path | None = None) -> list[RunOutcome]:
    """Group every correlated artefact by run id, newest first."""
    base = output_dir or OUTPUT_DIR
    if not base.exists():
        return []

    runs: dict[str, RunOutcome] = {}
    for path in base.rglob("*.json"):
        match = _ARTEFACT.search(path.name)
        if not match:
            continue
        date, clock, run_id = match.groups()
        stage = path.parent.name
        run = runs.setdefault(
            run_id, RunOutcome(run_id=run_id, workflow=None, started_at=f"{date}T{clock}")
        )
        run.started_at = min(run.started_at, f"{date}T{clock}")
        run.stages.add(stage)

    for run in runs.values():
        run.workflow = _attribute(run.stages)
        _read_terminal(run, base)

    return sorted(runs.values(), key=lambda r: r.started_at, reverse=True)


def _attribute(stages: set[str]) -> str | None:
    """Name the workflow a run belongs to, or None when its stages match neither.

    Attribution is by signature stage rather than by terminal stage: a run that
    died before reaching its terminal stage is exactly the case that matters, and
    keying on the terminal stage would make those runs invisible.
    """
    for workflow in WORKFLOWS:
        if stages & workflow.signature_stages:
            return workflow.name
    return None


def _workflow(name: str | None) -> Workflow | None:
    return next((w for w in WORKFLOWS if w.name == name), None)


def _read_terminal(run: RunOutcome, base: Path) -> None:
    workflow = _workflow(run.workflow)
    if workflow is None:
        return
    stage_dir = base / workflow.terminal_stage
    if not stage_dir.exists():
        return

    for path in stage_dir.glob("*_" + run.run_id + ".json"):
        document = _read(path)
        if document is None:
            continue
        run.reached_terminal = True
        if not isinstance(document, dict):
            continue

        count = document.get(workflow.output_count_field)
        if isinstance(count, int):
            run.output_count = count
            run.schema_accepted = count if workflow.name == "proposal" else 0

        for entry in document.get("errors") or []:
            text = entry if isinstance(entry, str) else json.dumps(entry, ensure_ascii=False)
            run.errors.append(text[:400])
            for found, _model in _VALIDATION_ERRORS.findall(text):
                run.schema_rejections += int(found)


def schema_violation_rate(runs: list[RunOutcome]) -> float | None:
    """Rejected structured-output items over all items the model offered.

    Only runs that reached a validator contribute. A run that died before the
    model was ever called has no denominator and must not be scored as clean.
    """
    rejected = sum(r.schema_rejections for r in runs)
    accepted = sum(r.schema_accepted for r in runs)
    offered = rejected + accepted
    if offered == 0:
        return None
    return rejected / offered


def evaluate(*, write_evidence: bool = True, output_dir: Path | None = None) -> EvalResult:
    runs = collect_runs(output_dir)
    attributed = [r for r in runs if r.workflow]

    if not attributed:
        return EvalResult(
            gate=GATE,
            evaluator=EVALUATOR,
            status=EvalStatus.NOT_MEASURED,
            metadata={
                "reason": (
                    "no correlated run artefacts under output/; the directory is "
                    "git-ignored, so a clean checkout has nothing to read. Run the "
                    "product once to populate it."
                ),
                "artefact_root": "output",
            },
        )

    recent = attributed[:RECENT_RUN_WINDOW]
    latest = recent[0]
    empty = [r for r in recent if not r.produced_output]
    violation_rate = schema_violation_rate(recent)

    findings: list[Finding] = []
    if not latest.produced_output:
        findings.append(
            Finding(
                id="HG-A7",
                severity=Severity.CRITICAL,
                title=(
                    "The most recent " + str(latest.workflow) + " run produced no output"
                    + ("" if latest.failure_kind is None else " (" + latest.failure_kind + ")")
                ),
                detail=(
                    "run " + latest.run_id[:12] + " (" + latest.started_at + ") ended as "
                    + latest.verdict + "; stages reached: " + str(sorted(latest.stages))
                ),
                root_cause_hint=(
                    latest.errors[0]
                    if latest.errors
                    else "the terminal stage artefact carries no output count"
                ),
                evidence_ref="evalgate/evidence/gate1/run_outcome_integrity.json",
                blocks_release=True,
            )
        )

    if violation_rate is not None and violation_rate > 0:
        findings.append(
            Finding(
                id="HG-A2",
                severity=Severity.CRITICAL if violation_rate >= 0.5 else Severity.HIGH,
                title=(
                    f"{violation_rate:.1%}"
                    + " of structured output was rejected by the product's validators"
                    + " across the last " + str(len(recent)) + " runs"
                ),
                detail=(
                    str(sum(r.schema_rejections for r in recent)) + " rejected against "
                    + str(sum(r.schema_accepted for r in recent)) + " accepted across the last "
                    + str(len(recent)) + " runs. Runs rejected by a validator: "
                    + (", ".join(r.run_id[:12] for r in recent if r.schema_rejections) or "none")
                    + ". The most recent run failed with: " + str(latest.failure_kind)
                ),
                root_cause_hint=(
                    "the model is emitting shapes the product's own Pydantic models "
                    "refuse; compare the prompt contract against the model definition"
                ),
                evidence_ref="evalgate/evidence/gate1/run_outcome_integrity.json",
                blocks_release=violation_rate >= 0.5,
            )
        )

    breakdown = [
        DatasetBreakdown(
            dataset_id=str(run.workflow) + ":" + run.run_id[:12],
            status=EvalStatus.PASS if run.produced_output else EvalStatus.FAIL,
            score=100.0 if run.produced_output else 0.0,
            reason=(
                run.verdict + "; "
                + (str(run.output_count) if run.output_count is not None else "no")
                + " items produced"
                + ("" if run.failure_kind is None else " (" + run.failure_kind + ")")
            ),
            metrics={
                "output_count": float(run.output_count) if run.output_count is not None else None,
                "schema_rejections": float(run.schema_rejections),
            },
        )
        for run in recent
    ]

    empty_rate = len(empty) / len(recent)
    metrics = {
        "latest_run_produced_output": MetricValue(
            raw=latest.produced_output,
            unit="boolean",
            normalized=norm.boolean(latest.produced_output),
            note=str(latest.workflow) + " run " + latest.run_id[:12] + ": " + latest.verdict,
        ),
        "empty_run_rate": MetricValue(
            raw=round(empty_rate, 4),
            unit="ratio",
            normalized=norm.inverse_ratio(empty_rate),
            note=str(len(empty)) + " of the last " + str(len(recent)) + " runs produced nothing",
        ),
        "latest_run_failure_kind": MetricValue(
            raw=None,
            unit="count",
            normalized=None,
            status=None if latest.failure_kind is None else EvalStatus.NOT_APPLICABLE,
            note=(
                "latest run succeeded"
                if latest.failure_kind is None
                else "latest run failed with " + latest.failure_kind
                + "; window breakdown: "
                + ", ".join(
                    sorted({str(r.failure_kind) for r in recent if r.failure_kind})
                )
            ),
        ),
        "schema_violation_rate": MetricValue(
            raw=round(violation_rate, 6) if violation_rate is not None else None,
            unit="ratio",
            normalized=(
                norm.inverse_ratio(violation_rate) if violation_rate is not None else None
            ),
            status=None if violation_rate is not None else EvalStatus.NOT_MEASURED,
            note=(
                None
                if violation_rate is not None
                else "no run in the window reached a validator, so there is no denominator"
            ),
        ),
    }

    # The score is the latest run's outcome, not an average over the window. A
    # release decision is about the system as it stands now; averaging lets a run
    # that worked last week pay for one that is broken today.
    score = 100.0 if latest.produced_output else 0.0

    evidence: list[Evidence] = []
    if write_evidence:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        target = EVIDENCE_DIR / "run_outcome_integrity.json"
        target.write_text(
            json.dumps(
                {
                    "window": RECENT_RUN_WINDOW,
                    "runs_found": len(attributed),
                    "schema_violation_rate": violation_rate,
                    "runs": [
                        {
                            "run_id": r.run_id,
                            "workflow": r.workflow,
                            "started_at": r.started_at,
                            "verdict": r.verdict,
                            "failure_kind": r.failure_kind,
                            "stages": sorted(r.stages),
                            "output_count": r.output_count,
                            "schema_rejections": r.schema_rejections,
                            "schema_accepted": r.schema_accepted,
                            "errors": r.errors,
                        }
                        for r in recent
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        evidence.append(Evidence(type="file", path="evalgate/evidence/gate1/run_outcome_integrity.json"))

    return EvalResult(
        gate=GATE,
        evaluator=EVALUATOR,
        status=EvalStatus.FAIL if findings else EvalStatus.PASS,
        score=score,
        metrics=metrics,
        per_dataset_breakdown=breakdown,
        thresholds={
            "empty_run_rate": Threshold(**{"pass": 0.0, "warn": 0.2}),
            "schema_violation_rate": Threshold(**{"pass": 0.0, "warn": 0.05}),
        },
        evidence=evidence,
        critical_findings=findings,
        metadata={
            "runs_found": len(attributed),
            "window": RECENT_RUN_WINDOW,
            "latest_run_id": latest.run_id,
            "latest_verdict": latest.verdict,
            "latest_failure_kind": latest.failure_kind,
        },
    )
