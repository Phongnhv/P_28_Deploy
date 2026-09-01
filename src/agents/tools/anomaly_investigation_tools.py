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
    DatasetVersionModel,
    DqResultModel,
    DqRunModel,
    ProfileModel,
    ProfileRunSnapshotModel,
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
    try:
        with Session(get_engine()) as db:
            anomaly = db.get(AnomalyRunModel, anomaly_run_id)
            if not anomaly:
                # Look up by execution run id or latest dq run
                dq_run = db.get(DqRunModel, anomaly_run_id)
                if not dq_run:
                    # Look up any recent dq run if anomaly_run_id contains prefix or hex
                    cleaned_id = anomaly_run_id.replace("anom-", "")
                    dq_run = (
                        db.query(DqRunModel)
                        .filter((DqRunModel.id == cleaned_id) | (DqRunModel.id.like(f"%{cleaned_id}%")))
                        .first()
                    )
                if not dq_run:
                    dq_run = db.query(DqRunModel).order_by(DqRunModel.created_at.desc()).first()

                if dq_run:
                    results = db.query(DqResultModel).filter_by(run_id=dq_run.id).all()
                    failed = [r for r in results if r.status in {"FAIL", "FAILED", "ERROR"}]
                    return {
                        "anomaly_run_id": anomaly_run_id,
                        "execution_run_id": dq_run.id,
                        "dataset_id": dq_run.dataset_id,
                        "decision": "CRITICAL" if failed else "NORMAL",
                        "score": 1.0 if failed else 0.0,
                        "confidence": 0.95 if failed else 0.90,
                        "severity": "HIGH" if failed else "LOW",
                        "status": "SUCCEEDED",
                        "signals": [],
                        "failed_rules": [
                            {
                                "result_id": r.id,
                                "rule_id": r.rule_id,
                                "rule_title": r.rule_title,
                                "status": r.status,
                                "checked_count": r.checked_count,
                                "failed_count": r.failed_count,
                                "violation_rate": r.violation_rate,
                            }
                            for r in failed
                        ],
                    }
                return {"error": "ANOMALY_RUN_NOT_FOUND", "anomaly_run_id": anomaly_run_id}
            try:
                execution_dataset_id = (
                    db.query(DqRunModel.dataset_id).filter(DqRunModel.id == anomaly.execution_run_id).scalar()
                )
            except Exception:
                execution_dataset_id = None
            signals = db.query(AnomalySignalModel).filter_by(anomaly_run_id=anomaly_run_id).all()
            results = db.query(DqResultModel).filter_by(run_id=anomaly.execution_run_id).all()
            failed = [r for r in results if r.status in {"FAIL", "FAILED", "ERROR"}]
            return {
                "anomaly_run_id": anomaly.id,
                "execution_run_id": anomaly.execution_run_id,
                "dataset_id": execution_dataset_id,
                "decision": anomaly.decision,
                "score": anomaly.score,
                "confidence": anomaly.confidence,
                "severity": anomaly.severity,
                "status": anomaly.status,
                "signals": [
                    {
                        "signal_id": s.id,
                        "family": s.family,
                        "target_type": s.target_type,
                        "target_id": s.target_id,
                        "score": s.score,
                        "reliability": s.reliability,
                        "observed_value": s.observed_value,
                        "baseline": _json(s.baseline, {}),
                        "detector_name": s.detector_name,
                        "explanation": s.explanation_code,
                        "evidence_refs": _json(s.evidence_refs, []),
                    }
                    for s in signals
                ],
                "failed_rules": [
                    {
                        "result_id": r.id,
                        "rule_id": r.rule_id,
                        "rule_title": r.rule_title,
                        "status": r.status,
                        "checked_count": r.checked_count,
                        "failed_count": r.failed_count,
                        "violation_rate": r.violation_rate,
                    }
                    for r in failed
                ],
            }
    except Exception as exc:
        return {"error": f"FAILED_TO_LOAD_ANOMALY_CASE: {exc}", "anomaly_run_id": anomaly_run_id}


@tool
def get_metric_history(dataset_id: str, rule_id: str, lookback_runs: int = 30) -> dict[str, Any]:
    """Return recent violation-rate history for a DQ rule, capped at 100 runs."""
    limit = max(1, min(int(lookback_runs), 100))
    try:
        with Session(get_engine()) as db:
            rows = (
                db.query(
                    DqResultModel.violation_rate,
                    DqResultModel.failed_count,
                    DqResultModel.checked_count,
                    DqResultModel.status,
                    DqRunModel.id.label("run_id"),
                    DqRunModel.created_at,
                )
                .join(DqRunModel, DqRunModel.id == DqResultModel.run_id)
                .filter(DqRunModel.dataset_id == dataset_id, DqResultModel.rule_id == rule_id)
                .order_by(DqRunModel.created_at.desc())
                .limit(limit)
                .all()
            )
            points = [
                {
                    "run_id": str(r.run_id),
                    "created_at": _iso(r.created_at),
                    "value": float(r.violation_rate)
                    if r.violation_rate is not None
                    else (float(r.failed_count) / float(r.checked_count) if r.checked_count else 0.0),
                    "status": str(r.status),
                }
                for r in rows
            ]
            values = [float(p["value"]) for p in points]
            return {
                "dataset_id": dataset_id,
                "rule_id": rule_id,
                "points": points,
                "summary": {
                    "count": len(values),
                    "min": min(values) if values else None,
                    "max": max(values) if values else None,
                    "latest": values[0] if values else None,
                },
            }
    except Exception as exc:
        return {"dataset_id": dataset_id, "rule_id": rule_id, "points": [], "summary": {"count": 0, "error": str(exc)}}


@tool
def get_related_quality_results(execution_run_id: str, target_id: str = "") -> dict[str, Any]:
    """Return failed quality results for a run, optionally filtered by rule/column text."""
    try:
        with Session(get_engine()) as db:
            rows = db.query(DqResultModel).filter_by(run_id=execution_run_id).all()
            rows = [r for r in rows if r.status in {"FAIL", "FAILED", "ERROR"}]
            if target_id:
                rows = [r for r in rows if target_id.lower() in (r.rule_id + " " + r.rule_title).lower()]
            return {
                "execution_run_id": execution_run_id,
                "target_id": target_id,
                "related_failures": [
                    {
                        "result_id": r.id,
                        "rule_id": r.rule_id,
                        "rule_title": r.rule_title,
                        "status": r.status,
                        "violation_rate": r.violation_rate,
                        "failed_count": r.failed_count,
                        "checked_count": r.checked_count,
                    }
                    for r in rows
                ],
            }
    except Exception as exc:
        return {"execution_run_id": execution_run_id, "related_failures": [], "error": str(exc)}


@tool
def get_dataset_profile(dataset_id: str) -> dict[str, Any]:
    """Return the latest bounded dataset and column profile, excluding raw sample values."""
    try:
        with Session(get_engine()) as db:
            profile = (
                db.query(ProfileModel)
                .filter_by(dataset_id=dataset_id)
                .order_by(ProfileModel.generated_at.desc())
                .first()
            )
            if profile:
                columns = db.query(ColumnProfileModel).filter_by(profile_dataset_id=dataset_id).all()
                return {
                    "dataset_id": dataset_id,
                    "generated_at": _iso(profile.generated_at),
                    "row_count": profile.row_count,
                    "completeness_score": profile.completeness_score,
                    "validity_score": profile.validity_score,
                    "duplicate_rate": profile.duplicate_rate,
                    "columns": [
                        {
                            "name": c.name,
                            "data_type": c.data_type,
                            "null_rate": c.null_rate,
                            "distinct_count": c.distinct_count,
                            "negative_rate": c.negative_rate,
                            "quantiles": _json(c.quantiles_json, {}),
                            "min": c.min_value,
                            "max": c.max_value,
                            "out_of_domain_rate": c.out_of_domain_rate,
                        }
                        for c in columns
                    ],
                }

            # Canonical versioned imports persist immutable aggregate profiles in
            # profile_runs, not in the legacy profiles/column_profiles pair.
            # Graph 1A and the dashboard already use this source; the anomaly
            # investigation tool must use it too or every versioned dataset is
            # reported as PROFILE_NOT_FOUND in the final report.
            latest_version = (
                db.query(DatasetVersionModel)
                .filter_by(dataset_id=dataset_id, status="READY")
                .order_by(DatasetVersionModel.version_number.desc())
                .first()
            )
            snapshot = (
                db.query(ProfileRunSnapshotModel)
                .filter_by(
                    dataset_id=dataset_id,
                    dataset_version_id=latest_version.id,
                    status="COMPLETED",
                )
                .order_by(ProfileRunSnapshotModel.completed_at.desc())
                .first()
                if latest_version
                else None
            )
            if not snapshot:
                return {"error": "PROFILE_NOT_FOUND", "dataset_id": dataset_id}

            metrics_payload = _json(snapshot.metrics_json, {})
            metrics = metrics_payload if isinstance(metrics_payload, dict) else {}
            schema_payload = _json(snapshot.schema_json, [])
            raw_columns = metrics.get("columns") or (schema_payload if isinstance(schema_payload, list) else [])
            return {
                "dataset_id": dataset_id,
                "generated_at": _iso(snapshot.completed_at or snapshot.created_at),
                "row_count": snapshot.row_count,
                "completeness_score": snapshot.completeness_score,
                "validity_score": snapshot.validity_score,
                "duplicate_rate": snapshot.duplicate_rate,
                "columns": [
                    {
                        "name": str(c.get("name")),
                        "data_type": c.get("logical_type") or c.get("physical_type") or "string",
                        "null_rate": float(c.get("null_rate") or 0.0),
                        "distinct_count": int(c.get("distinct_count") or 0),
                        "negative_rate": c.get("negative_rate"),
                        "quantiles": c.get("quantiles") or {},
                        "min": c.get("min_value"),
                        "max": c.get("max_value"),
                        "out_of_domain_rate": c.get("out_of_domain_rate"),
                    }
                    for c in raw_columns
                    if isinstance(c, dict) and c.get("name")
                ],
            }
    except Exception as exc:
        return {"dataset_id": dataset_id, "error": f"FAILED_TO_LOAD_PROFILE: {exc}"}


@tool
def query_readonly_evidence(
    execution_run_id: str,
    operation: Literal["failed_rules", "rule_summary"] = "failed_rules",
    limit: int = 20,
) -> dict[str, Any]:
    """Run an allowlisted, read-only evidence query over persisted DQ results."""
    limit = max(1, min(int(limit), 100))
    try:
        with Session(get_engine()) as db:
            rows = db.query(DqResultModel).filter_by(run_id=execution_run_id).all()
            if operation == "failed_rules":
                rows = [r for r in rows if r.status in {"FAIL", "FAILED", "ERROR"}]
            rows = rows[:limit]
            return {
                "execution_run_id": execution_run_id,
                "operation": operation,
                "rows": [
                    {
                        "result_id": r.id,
                        "rule_id": r.rule_id,
                        "status": r.status,
                        "violation_rate": r.violation_rate,
                        "failed_count": r.failed_count,
                        "checked_count": r.checked_count,
                    }
                    for r in rows
                ],
            }
    except Exception as exc:
        return {"execution_run_id": execution_run_id, "rows": [], "error": str(exc)}


ANOMALY_INVESTIGATION_TOOLS = [
    get_anomaly_case,
    get_metric_history,
    get_related_quality_results,
    get_dataset_profile,
    query_readonly_evidence,
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
    dataset_id = (
        input(
            f"Dataset ID for metric/profile checks [{default_dataset_id or 'none found'}] (Enter to use default/stop): "
        ).strip()
        or default_dataset_id
    )
    if dataset_id:
        signals = case.get("signals", []) if isinstance(case, dict) else []
        default_rule_id = next(
            (
                s.get("target_id")
                for s in sorted(signals, key=lambda item: float(item.get("score", 0.0)), reverse=True)
                if s.get("target_type") == "RULE" and s.get("target_id")
            ),
            "",
        )
        rule_id = (
            input(
                f"Rule ID for metric history [{default_rule_id or 'none found'}] (Enter to use default/skip): "
            ).strip()
            or default_rule_id
        )
        if rule_id:
            pprint(
                get_metric_history.invoke(
                    {
                        "dataset_id": dataset_id,
                        "rule_id": rule_id,
                        "lookback_runs": 10,
                    }
                )
            )
        pprint(get_dataset_profile.invoke({"dataset_id": dataset_id}))

    default_execution_id = case.get("execution_run_id", "") if isinstance(case, dict) else ""
    execution_id = (
        input(
            f"Execution run ID for related results [{default_execution_id or 'none found'}] (Enter to use default/stop): "
        ).strip()
        or default_execution_id
    )
    if execution_id:
        pprint(get_related_quality_results.invoke({"execution_run_id": execution_id}))
        pprint(
            query_readonly_evidence.invoke(
                {
                    "execution_run_id": execution_id,
                    "operation": "failed_rules",
                    "limit": 20,
                }
            )
        )
