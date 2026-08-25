"""Gate 1 in replay mode: score archived agent runs instead of invoking the agent.

Live mode is unavailable on this branch -- ``get_dataset_rule_policy`` raises for
every dataset because ``src/resources/rule_policies.json`` was deleted in
``ac4b663`` -- so calling the agent would yield BLOCKED for all seven datasets and
no number at all.

The repository nevertheless contains 120 archived JSON artefacts from real runs.
Replay scores those against ground truth recovered from the fixture, which costs
no LLM call and produces the first genuine detection figures for the project.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evalgate.core.context import EvalRunContext
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
from evalgate.sdih.defect_taxonomy import EXPECTED_RULE_TYPES, DefectClass

PROJECT_ROOT = Path(__file__).resolve().parents[3]
REPORTS_DIR = PROJECT_ROOT / "output" / "reports"
EVIDENCE_DIR = PROJECT_ROOT / "evalgate" / "evidence" / "gate1"

GATE = "ai_quality"
EVALUATOR = "replay_detection_v1"


@dataclass
class RuleOutcome:
    rule_id: str
    column: str | None
    rule_type: str
    status: str
    violation_count: int
    total_rows: int
    sample_ids: set[str]


def _parse_rule_id(rule_id: str) -> tuple[str | None, str]:
    """``source_rows.fare_amount.RANGE`` -> ("fare_amount", "RANGE")."""
    parts = rule_id.split(".")
    if len(parts) >= 3:
        return parts[-2], parts[-1]
    if len(parts) == 2:
        return None, parts[-1]
    return None, rule_id


def _display_path(path: Path) -> str:
    """Repo-relative when possible, absolute otherwise.

    ``relative_to`` raises for anything outside the project root, which took the
    whole evaluator down the first time it was pointed at a temp directory. A probe
    must never crash on an unexpected path -- it should report what it can.
    """
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def load_archived_runs(
    reports_dir: Path = REPORTS_DIR, *, context: EvalRunContext | None = None
) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    if context is not None:
        for record in context.records("execution-results"):
            payload = json.loads(context.path_for(record).read_text(encoding="utf-8"))
            results = payload.get("test_results") or payload.get("results") or []
            if results:
                runs.append({**payload, "__path__": record.relative_path})
        return runs
    # Explicit diagnostic/test helper only. Production orchestration always passes
    # EvalRunContext and therefore never performs this directory scan.
    for path in reports_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        results = payload.get("test_results") if isinstance(payload, dict) else None
        if results:
            runs.append({**payload, "__path__": _display_path(path)})
    return runs


def _outcomes(run: dict[str, Any]) -> list[RuleOutcome]:
    outcomes: list[RuleOutcome] = []
    for entry in run.get("test_results", []):
        rule_id = entry.get("rule_id", "")
        column, rule_type = _parse_rule_id(rule_id)
        outcomes.append(
            RuleOutcome(
                rule_id=rule_id,
                column=entry.get("column") or column,
                rule_type=entry.get("rule_type") or rule_type,
                status=entry.get("status", ""),
                violation_count=int(entry.get("violation_count") or 0),
                total_rows=int(entry.get("total_rows") or 0),
                sample_ids={
                    str(value.get("source_row_id") if isinstance(value, dict) else value)
                    for value in (entry.get("sample_refs") or entry.get("sample_failures") or [])
                    if value is not None
                },
            )
        )
    return outcomes


def score_run(
    run: dict[str, Any],
    truth_by_class: dict[str, dict[str, list[str] | set[str]]],
) -> dict[str, Any]:
    """Score one archived run.

    ``truth_by_class`` maps a defect class to labelled row IDs by column. A
    prediction only receives credit when its row ID intersects that ground truth;
    matching a violation count is never treated as a true positive.
    """
    outcomes = _outcomes(run)
    recall_by_class: dict[str, float] = {}
    detected_true_ids: set[tuple[str, str, str]] = set()
    all_predicted_ids = {
        (outcome.rule_id, row_id)
        for outcome in outcomes
        for row_id in outcome.sample_ids
    }
    total_true = 0
    per_class_detail: dict[str, Any] = {}

    for defect_name, columns in truth_by_class.items():
        defect = DefectClass(defect_name)
        expected_types = EXPECTED_RULE_TYPES[defect]
        class_true = sum(len(set(row_ids)) for row_ids in columns.values())
        total_true += class_true
        caught = 0
        matched_rules: list[str] = []
        for column, labelled_rows in columns.items():
            truth_ids = {str(value) for value in labelled_rows}
            hit = [
                o
                for o in outcomes
                if o.column == column
                and o.rule_type in expected_types
                and o.sample_ids
            ]
            if hit:
                predicted = set().union(*(o.sample_ids for o in hit))
                overlap = predicted & truth_ids
                caught += len(overlap)
                detected_true_ids.update((defect_name, column, row_id) for row_id in overlap)
                matched_rules.extend(o.rule_id for o in hit)
        recall_by_class[defect_name] = (caught / class_true) if class_true else 0.0
        per_class_detail[defect_name] = {
            "true_defects": class_true,
            "credited_detections": caught,
            "recall": round(recall_by_class[defect_name], 4),
            "expected_rule_types": list(expected_types),
            "matched_rules": sorted(set(matched_rules)),
            "columns": columns,
        }

    detected_true = len(detected_true_ids)
    total_flagged = len(all_predicted_ids)
    precision = (detected_true / total_flagged) if total_flagged else 0.0
    recall = (detected_true / total_true) if total_true else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    class_recalls = list(recall_by_class.values())
    macro_recall = sum(class_recalls) / len(class_recalls) if class_recalls else 0.0

    return {
        "path": run.get("__path__"),
        "test_run_id": run.get("test_run_id"),
        "dataset_id": run.get("dataset_id"),
        "rule_count": len(outcomes),
        "total_flagged_rows": total_flagged,
        "count_only_violations": sum(
            outcome.violation_count for outcome in outcomes if outcome.violation_count and not outcome.sample_ids
        ),
        "true_defects": total_true,
        "credited_detections": detected_true,
        "precision": round(precision, 6),
        "recall_row_level": round(recall, 6),
        "recall_macro_by_class": round(macro_recall, 6),
        "f1": round(f1, 6),
        "recall_by_class": {k: round(v, 4) for k, v in recall_by_class.items()},
        "per_class_detail": per_class_detail,
        "reported_dq_score": run.get("dq_score"),
        "reported_dq_grade": run.get("dq_grade"),
    }


def evaluate(
    truth_by_class: dict[str, dict[str, list[str] | set[str]]],
    *,
    reports_dir: Path = REPORTS_DIR,
    write_evidence: bool = True,
    context: EvalRunContext | None = None,
) -> EvalResult:
    runs = load_archived_runs(reports_dir, context=context)
    if not runs:
        return EvalResult(
            gate=GATE,
            evaluator=EVALUATOR,
            status=EvalStatus.BLOCKED_MISSING_GROUND_TRUTH,
            metrics={},
            metadata={"reason": "no execution-results artifact in the current manifest"},
        )

    scored = [score_run(run, truth_by_class) for run in runs]
    if (not any(s["total_flagged_rows"] for s in scored)
            and any(s["count_only_violations"] for s in scored)):
        return EvalResult(
            gate=GATE,
            evaluator=EVALUATOR,
            status=EvalStatus.NOT_MEASURED,
            metadata={
                "reason": (
                    "archived results do not contain failed row IDs; count-only "
                    "artifacts cannot support row-level precision or recall"
                )
            },
        )
    # The richest run is the one that exercised the most rules.
    scored.sort(key=lambda s: s["rule_count"], reverse=True)
    primary = scored[0]

    breakdown = [
        DatasetBreakdown(
            dataset_id=f"{s['dataset_id']}@{(s['test_run_id'] or '')[:8]}",
            status=EvalStatus.FAIL if s["f1"] < 0.40 else EvalStatus.WARN,
            score=norm.ratio(s["f1"]),
            metrics={
                "precision": s["precision"],
                "recall_macro": s["recall_macro_by_class"],
                "f1": s["f1"],
                "rules": float(s["rule_count"]),
            },
            recall_by_class=s["recall_by_class"],
            applicable_classes=sorted(truth_by_class),
        )
        for s in scored
    ]

    zero_recall = [
        name for name, value in primary["recall_by_class"].items() if value <= 0.0
    ]
    min_recall = min(primary["recall_by_class"].values(), default=0.0)

    findings = [
        Finding(
            id="HG-A1",
            severity=Severity.CRITICAL,
            title=f"Recall = 0 on defect class {name} ({primary['dataset_id']})",
            detail=(
                f"{primary['per_class_detail'][name]['true_defects']} true defects of "
                f"class {name} went undetected. Expected rule types: "
                f"{primary['per_class_detail'][name]['expected_rule_types']}."
            ),
            root_cause_hint=_root_cause_hint(name),
            evidence_ref=f"evalgate/evidence/gate1/{EVALUATOR}.json#{name}",
            blocks_release=True,
        )
        for name in zero_recall
    ]

    evidence: list[Evidence] = []
    if write_evidence:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        target = EVIDENCE_DIR / f"{EVALUATOR}.json"
        target.write_text(
            json.dumps(
                {"truth_by_class": truth_by_class, "runs": scored},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        evidence.append(
            Evidence(type="file", path=str(target.relative_to(PROJECT_ROOT)))
        )
        for run in runs:
            evidence.append(Evidence(type="file", path=run["__path__"]))

    return EvalResult(
        gate=GATE,
        evaluator=EVALUATOR,
        status=(
            EvalStatus.PASS
            if primary["f1"] >= 0.60 and not zero_recall
            else EvalStatus.WARN
            if primary["f1"] >= 0.40 and not zero_recall
            else EvalStatus.FAIL
        ),
        score=norm.ratio(primary["f1"]),
        metrics={
            "detection_precision": MetricValue(
                raw=primary["precision"], unit="ratio",
                normalized=norm.ratio(primary["precision"]),
            ),
            "detection_recall_macro": MetricValue(
                raw=primary["recall_macro_by_class"], unit="ratio",
                normalized=norm.ratio(primary["recall_macro_by_class"]),
            ),
            "detection_f1_macro": MetricValue(
                raw=primary["f1"], unit="ratio", normalized=norm.ratio(primary["f1"])
            ),
            "min_recall_per_class": MetricValue(
                raw=min_recall, unit="ratio", normalized=norm.ratio(min_recall)
            ),
            "archived_runs_scored": MetricValue(
                raw=len(scored), unit="count", normalized=None
            ),
        },
        per_dataset_breakdown=breakdown,
        thresholds={
            "detection_f1_macro": Threshold(**{"pass": 60.0, "warn": 40.0}),
            "min_recall_per_class": Threshold(
                **{"pass": 80.0, "warn": 60.0, "hard_gate_floor": 0.0001}
            ),
        },
        evidence=evidence,
        critical_findings=findings,
        metadata={
            "mode": "replay",
            "why_replay": (
                "live agent invocation raises AgentWorkflowError on this branch: "
                "src/resources/rule_policies.json was deleted in ac4b663"
            ),
            "primary_run": primary["path"],
            "reported_dq_score": primary["reported_dq_score"],
        },
    )


def _root_cause_hint(defect_class: str) -> str:
    hints = {
        "MISSING_VALUE": (
            "the semantic transform replaced injected NaN with the literal "
            "'Unknown Vendor', so the null no longer exists as a null and "
            "NOT_NULL legitimately passes -- the ground truth was destroyed "
            "upstream of the agent"
        ),
        "INVALID_CATEGORY": (
            "ACCEPTED_VALUES learns its enum from observed top_categories, so the "
            "injected invalid literal is admitted into its own allow-list "
            "(tautological rule)"
        ),
        "DUPLICATE_ROW": (
            "UNIQUE was proposed on the surrogate key source_row_id rather than on "
            "the business fingerprint, so duplicates are structurally invisible"
        ),
    }
    return hints.get(defect_class, "not yet attributed")
