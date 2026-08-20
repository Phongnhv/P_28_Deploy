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


def _parse_rule_id(rule_id: str) -> tuple[str | None, str]:
    """``source_rows.fare_amount.RANGE`` -> ("fare_amount", "RANGE")."""
    parts = rule_id.split(".")
    if len(parts) >= 3:
        return parts[-2], parts[-1]
    if len(parts) == 2:
        return None, parts[-1]
    return None, rule_id


def load_archived_runs(reports_dir: Path = REPORTS_DIR) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for path in sorted(reports_dir.glob("test_run_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        results = payload.get("test_results") or payload.get("results") or []
        if not results:
            continue
        payload["__path__"] = str(path.relative_to(PROJECT_ROOT))
        runs.append(payload)
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
            )
        )
    return outcomes


def score_run(
    run: dict[str, Any],
    truth_by_class: dict[str, dict[str, int]],
) -> dict[str, Any]:
    """Score one archived run.

    ``truth_by_class`` maps a defect class to ``{column: count_of_true_defects}``.
    A class is credited as detected when a rule of an expected type, on the right
    column, reported at least as many violations as there are true defects.
    """
    outcomes = _outcomes(run)
    total_flagged = sum(o.violation_count for o in outcomes)

    recall_by_class: dict[str, float] = {}
    detected_true = 0
    total_true = 0
    per_class_detail: dict[str, Any] = {}

    for defect_name, columns in truth_by_class.items():
        defect = DefectClass(defect_name)
        expected_types = EXPECTED_RULE_TYPES[defect]
        class_true = sum(columns.values())
        total_true += class_true
        caught = 0
        matched_rules: list[str] = []
        for column, count in columns.items():
            hit = [
                o
                for o in outcomes
                if o.column == column
                and o.rule_type in expected_types
                and o.violation_count > 0
            ]
            if hit:
                caught += min(count, max(o.violation_count for o in hit))
                matched_rules.extend(o.rule_id for o in hit)
        detected_true += caught
        recall_by_class[defect_name] = (caught / class_true) if class_true else 0.0
        per_class_detail[defect_name] = {
            "true_defects": class_true,
            "credited_detections": caught,
            "recall": round(recall_by_class[defect_name], 4),
            "expected_rule_types": list(expected_types),
            "matched_rules": sorted(set(matched_rules)),
            "columns": columns,
        }

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
    truth_by_class: dict[str, dict[str, int]],
    *,
    reports_dir: Path = REPORTS_DIR,
    write_evidence: bool = True,
) -> EvalResult:
    runs = load_archived_runs(reports_dir)
    if not runs:
        return EvalResult(
            gate=GATE,
            evaluator=EVALUATOR,
            status=EvalStatus.BLOCKED_MISSING_GROUND_TRUTH,
            metrics={},
            metadata={"reason": "no archived run artefacts found"},
        )

    scored = [score_run(run, truth_by_class) for run in runs]
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
        status=EvalStatus.FAIL,
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
