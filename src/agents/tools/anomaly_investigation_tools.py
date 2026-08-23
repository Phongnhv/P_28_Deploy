"""Read-only tools for the anomaly investigation agent.

These tools expose bounded, persisted evidence. They do not run the anomaly
detector and cannot mutate application data.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Literal

from langchain_core.tools import tool
from sqlalchemy.orm import Session

from src.models.database import (
    AnomalyRunModel,
    AnomalySignalModel,
    ColumnProfileModel,
    DqResultModel,
    DqRunModel,
    ProfileModel,
)
from src.services.rule_store import get_engine


def _json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return default


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, (datetime, date)) else (str(value) if value else None)


@tool
def get_anomaly_case(anomaly_run_id: str) -> dict[str, Any]:
    """Return one anomaly run with its signals, failed rules, and execution metadata."""
    with Session(get_engine()) as db:
        anomaly = db.get(AnomalyRunModel, anomaly_run_id)
        if not anomaly:
            return {"error": "ANOMALY_RUN_NOT_FOUND", "anomaly_run_id": anomaly_run_id}
        execution = db.get(DqRunModel, anomaly.execution_run_id)
        signals = db.query(AnomalySignalModel).filter_by(anomaly_run_id=anomaly_run_id).all()
        results = db.query(DqResultModel).filter_by(run_id=anomaly.execution_run_id).all()
        failed = [r for r in results if r.status in {"FAIL", "FAILED", "ERROR"}]
        return {
            "anomaly_run_id": anomaly.id,
            "execution_run_id": anomaly.execution_run_id,
            "dataset_id": execution.dataset_id if execution else None,
            "decision": anomaly.decision,
            "score": anomaly.score,
            "confidence": anomaly.confidence,
            "severity": anomaly.severity,
            "status": anomaly.status,
            "signals": [
                {"signal_id": s.id, "family": s.family, "target_type": s.target_type,
                 "target_id": s.target_id, "score": s.score, "reliability": s.reliability,
                 "observed_value": s.observed_value, "baseline": _json(s.baseline, {}),
                 "detector_name": s.detector_name, "explanation": s.explanation_code,
                 "evidence_refs": _json(s.evidence_refs, [])}
                for s in signals
            ],
            "failed_rules": [
                {"result_id": r.id, "rule_id": r.rule_id, "rule_title": r.rule_title,
                 "status": r.status, "checked_count": r.checked_count,
                 "failed_count": r.failed_count, "violation_rate": r.violation_rate}
                for r in failed
            ],
        }


@tool
def get_metric_history(dataset_id: str, rule_id: str, lookback_runs: int = 30) -> dict[str, Any]:
    """Return recent violation-rate history for a DQ rule, capped at 100 runs."""
    limit = max(1, min(int(lookback_runs), 100))
    with Session(get_engine()) as db:
        rows = (db.query(DqResultModel, DqRunModel)
                .join(DqRunModel, DqRunModel.id == DqResultModel.run_id)
                .filter(DqRunModel.dataset_id == dataset_id, DqResultModel.rule_id == rule_id)
                .order_by(DqRunModel.created_at.desc()).limit(limit).all())
        points = [{"run_id": run.id, "created_at": _iso(run.created_at),
                   "value": result.violation_rate if result.violation_rate is not None
                   else (result.failed_count / result.checked_count if result.checked_count else 0.0),
                   "status": result.status} for result, run in rows]
        values = [float(p["value"]) for p in points]
        return {"dataset_id": dataset_id, "rule_id": rule_id, "points": points,
                "summary": {"count": len(values), "min": min(values) if values else None,
                             "max": max(values) if values else None,
                             "latest": values[0] if values else None}}


@tool
def get_related_quality_results(execution_run_id: str, target_id: str = "") -> dict[str, Any]:
    """Return failed quality results for a run, optionally filtered by rule/column text."""
    with Session(get_engine()) as db:
        rows = db.query(DqResultModel).filter_by(run_id=execution_run_id).all()
        rows = [r for r in rows if r.status in {"FAIL", "FAILED", "ERROR"}]
        if target_id:
            rows = [r for r in rows if target_id.lower() in (r.rule_id + " " + r.rule_title).lower()]
        return {"execution_run_id": execution_run_id, "target_id": target_id,
                "related_failures": [{"result_id": r.id, "rule_id": r.rule_id,
                "rule_title": r.rule_title, "status": r.status,
                "violation_rate": r.violation_rate, "failed_count": r.failed_count,
                "checked_count": r.checked_count} for r in rows]}


@tool
def get_dataset_profile(dataset_id: str) -> dict[str, Any]:
    """Return the latest bounded dataset and column profile, excluding raw sample values."""
    with Session(get_engine()) as db:
        profile = (db.query(ProfileModel).filter_by(dataset_id=dataset_id)
                   .order_by(ProfileModel.generated_at.desc()).first())
        if not profile:
            return {"error": "PROFILE_NOT_FOUND", "dataset_id": dataset_id}
        columns = db.query(ColumnProfileModel).filter_by(profile_dataset_id=dataset_id).all()
        return {"dataset_id": dataset_id, "generated_at": _iso(profile.generated_at),
                "row_count": profile.row_count, "completeness_score": profile.completeness_score,
                "validity_score": profile.validity_score, "duplicate_rate": profile.duplicate_rate,
                "columns": [{"name": c.name, "data_type": c.data_type, "null_rate": c.null_rate,
                "distinct_count": c.distinct_count, "negative_rate": c.negative_rate,
                "quantiles": _json(c.quantiles_json, {}), "min": c.min_value, "max": c.max_value,
                "out_of_domain_rate": c.out_of_domain_rate} for c in columns]}


@tool
def query_readonly_evidence(
    execution_run_id: str,
    operation: Literal["failed_rules", "rule_summary"] = "failed_rules",
    limit: int = 20,
) -> dict[str, Any]:
    """Run an allowlisted, read-only evidence query over persisted DQ results."""
    limit = max(1, min(int(limit), 100))
    with Session(get_engine()) as db:
        rows = db.query(DqResultModel).filter_by(run_id=execution_run_id).all()
        if operation == "failed_rules":
            rows = [r for r in rows if r.status in {"FAIL", "FAILED", "ERROR"}]
        rows = rows[:limit]
        return {"execution_run_id": execution_run_id, "operation": operation,
                "rows": [{"result_id": r.id, "rule_id": r.rule_id, "status": r.status,
                "violation_rate": r.violation_rate, "failed_count": r.failed_count,
                "checked_count": r.checked_count} for r in rows]}


ANOMALY_INVESTIGATION_TOOLS = [
    get_anomaly_case, get_metric_history, get_related_quality_results,
    get_dataset_profile, query_readonly_evidence,
]


if __name__ == "__main__":
    """Manual smoke test: run with ``python -m src.agents.tools.anomaly_investigation_tools``.

    The prompts make it possible to test against the configured database without
    hard-coding a real anomaly ID into source control. Press Enter to skip a tool.
    """
    from pprint import pprint

    print("Available tools:")
    for investigation_tool in ANOMALY_INVESTIGATION_TOOLS:
        print(f"- {investigation_tool.name}: {investigation_tool.description.splitlines()[0]}")

    # Select a real persisted run by default, so the module can be executed
    # immediately after checkout. You can still replace it interactively.
    with Session(get_engine()) as db:
        first_anomaly = db.query(AnomalyRunModel).order_by(AnomalyRunModel.created_at.desc()).first()
        default_anomaly_id = first_anomaly.id if first_anomaly else ""

    prompt = f"\nAnomaly run ID [{default_anomaly_id or 'none found'}] (Enter to use default/stop): "
    anomaly_id = input(prompt).strip() or default_anomaly_id
    if anomaly_id:
        case = get_anomaly_case.invoke({"anomaly_run_id": anomaly_id})
        pprint(case)
    else:
        case = {}

    default_dataset_id = case.get("dataset_id", "") if isinstance(case, dict) else ""
    dataset_id = input(
        f"Dataset ID for metric/profile checks [{default_dataset_id or 'none found'}] (Enter to use default/stop): "
    ).strip() or default_dataset_id
    if dataset_id:
        signals = case.get("signals", []) if isinstance(case, dict) else []
        default_rule_id = next(
            (s.get("target_id") for s in sorted(
                signals, key=lambda item: float(item.get("score", 0.0)), reverse=True
            ) if s.get("target_type") == "RULE" and s.get("target_id")),
            "",
        )
        rule_id = input(
            f"Rule ID for metric history [{default_rule_id or 'none found'}] (Enter to use default/skip): "
        ).strip() or default_rule_id
        if rule_id:
            pprint(get_metric_history.invoke({
                "dataset_id": dataset_id,
                "rule_id": rule_id,
                "lookback_runs": 10,
            }))
        pprint(get_dataset_profile.invoke({"dataset_id": dataset_id}))

    default_execution_id = case.get("execution_run_id", "") if isinstance(case, dict) else ""
    execution_id = input(
        f"Execution run ID for related results [{default_execution_id or 'none found'}] (Enter to use default/stop): "
    ).strip() or default_execution_id
    if execution_id:
        pprint(get_related_quality_results.invoke({"execution_run_id": execution_id}))
        pprint(query_readonly_evidence.invoke({
            "execution_run_id": execution_id,
            "operation": "failed_rules",
            "limit": 20,
        }))
