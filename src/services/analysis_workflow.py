from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from src.models.database import (
    AnalysisNodeExecutionModel,
    AnalysisRunModel,
    AnomalyHypothesisModel,
    AnomalyRunModel,
    AnomalySignalModel,
    DqResultModel,
    Graph1NodeExecutionModel,
    Graph1RunModel,
    RuleProposalModel,
    RuleVersionModel,
)
from src.services.rule_store import (
    ProposedRuleModel,
    create_test_run,
    get_approved_rules,
    get_engine,
    get_test_results,
    update_test_run_status,
)
from src.time_utils import utc_now

logger = logging.getLogger(__name__)

ANALYSIS_NODES = [
    ("PREPARING", "prepare_approved_rules"),
    ("GRAPH2", "test_generator"),
    ("GRAPH2", "validate_dbt_project"),
    ("GRAPH2", "dbt_validation_failed"),
    ("GRAPH2", "test_runner"),
    ("GRAPH2", "persist_report"),
    ("GRAPH3", "anomaly_detector"),
    ("GRAPH3", "hypothesis_agent"),
    ("GRAPH3", "persist_analysis"),
    ("REPORT", "report_writer"),
]
TERMINAL_STATUSES = {"COMPLETED", "PARTIAL", "FAILED"}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _payload(raw: str | None, fallback: Any = None) -> Any:
    try:
        return json.loads(raw or "")
    except (TypeError, ValueError):
        return fallback


def _basename(value: Any) -> str | None:
    if not value:
        return None
    return Path(str(value)).name


def _normalise_status(value: Any) -> str:
    raw = str(value or "SKIPPED").upper()
    return {
        "PASSED": "PASS",
        "FAILED": "FAIL",
    }.get(raw, raw)


def _spec_parameters(spec: dict[str, Any], legacy: ProposedRuleModel) -> dict[str, Any]:
    nested = spec.get("parameters") if isinstance(spec.get("parameters"), dict) else {}
    original = _payload(legacy.parameters, {}) if legacy.parameters else {}
    values = {
        "min": nested.get("min", nested.get("min_value", spec.get("min_value", original.get("min")))),
        "max": nested.get("max", nested.get("max_value", spec.get("max_value", original.get("max")))),
        "max_null_pct": nested.get("max_null_pct", spec.get("max_null_pct", original.get("max_null_pct"))),
        "accepted_values": nested.get(
            "accepted_values", spec.get("allowed_values", original.get("accepted_values"))
        ),
        "regex": nested.get("regex", spec.get("regex", original.get("regex"))),
        "target_column": nested.get("target_column", spec.get("target_column", original.get("target_column"))),
        "operator": nested.get("operator", spec.get("operator", original.get("operator"))),
        "min_row_count": nested.get("min_row_count", spec.get("min_row_count", original.get("min_row_count"))),
    }
    return {key: value for key, value in values.items() if value is not None and value != ""}


def ensure_completed_graph1_snapshot(db: Session, run: Graph1RunModel) -> int:
    """Backfill completed Graph 1 runs created before the dual-store review fix."""
    state = _payload(run.state_json, {}) or {}
    rule_ids = [
        str(item.get("rule_id"))
        for item in state.get("proposed_rules", [])
        if isinstance(item, dict) and item.get("rule_id")
    ]
    if not rule_ids:
        return 0
    proposals = db.query(RuleProposalModel).filter(RuleProposalModel.id.in_(rule_ids)).all()
    approved = 0
    status_by_id: dict[str, str] = {}
    for proposal in proposals:
        legacy = db.get(ProposedRuleModel, (run.id, proposal.id))
        if not legacy:
            continue
        legacy.status = proposal.status
        status_by_id[proposal.id] = proposal.status
        if proposal.status != "APPROVED":
            continue
        approved += 1
        spec = _payload(proposal.rule_spec, {}) or {}
        legacy.rule_type = str(spec.get("type") or proposal.rule_type or legacy.rule_type)
        legacy.column_name = spec.get("column", legacy.column_name)
        legacy.edited_parameters = _json(_spec_parameters(spec, legacy))
        version_id = f"rv_{proposal.id}"
        version = db.get(RuleVersionModel, version_id)
        if version:
            version.rule_spec = proposal.rule_spec
            version.status = "APPROVED"
            version.dataset_version_id = run.dataset_version_id
        else:
            db.add(RuleVersionModel(
                id=version_id,
                rule_proposal_id=proposal.id,
                dataset_id=proposal.dataset_id,
                dataset_version_id=run.dataset_version_id,
                rule_spec=proposal.rule_spec,
                status="APPROVED",
                version=1,
                created_at=utc_now(),
            ))
    reviewed_rules: list[dict[str, Any]] = []
    for raw in state.get("proposed_rules", []):
        if not isinstance(raw, dict):
            continue
        rule = dict(raw)
        rule["status"] = status_by_id.get(str(rule.get("rule_id")), str(rule.get("status") or "PENDING"))
        reviewed_rules.append(rule)
    state["proposed_rules"] = reviewed_rules
    state["approved_rules"] = [rule for rule in reviewed_rules if rule.get("status") == "APPROVED"]
    run.state_json = _json(state)
    gate = db.get(Graph1NodeExecutionModel, f"{run.id}:hitl_gate")
    if gate:
        previous = _payload(gate.output_json, {}) or {}
        edited = sum(1 for rule in reviewed_rules if rule.get("review_action") == "edit")
        rejected = sum(1 for rule in reviewed_rules if rule.get("status") == "REJECTED")
        pending = max(0, len(reviewed_rules) - approved - rejected)
        gate.output_json = _json({
            **previous,
            "proposed_rules": reviewed_rules,
            "approved_rules": state["approved_rules"],
            "total_count": len(reviewed_rules),
            "approved_count": approved,
            "edited_count": edited,
            "rejected_count": rejected,
            "pending_count": pending,
        })
    db.commit()
    return approved


def create_analysis_run(
    db: Session,
    graph1_run: Graph1RunModel,
    username: str,
    idempotency_key: str,
) -> tuple[AnalysisRunModel, bool]:
    existing = (
        db.query(AnalysisRunModel)
        .filter(AnalysisRunModel.graph1_run_id == graph1_run.id)
        .order_by(AnalysisRunModel.created_at.desc())
        .first()
    )
    # Successful/in-flight analyses remain idempotent. A failed terminal run,
    # however, must not permanently brick the Graph 1 snapshot. The schema has
    # one analysis per Graph 1, so reset that failed row and its observable
    # nodes when the UI submits a fresh idempotency key.
    if existing:
        if existing.status != "FAILED":
            return existing, False
        conflicting_key = (
            db.query(AnalysisRunModel)
            .filter(
                AnalysisRunModel.idempotency_key == idempotency_key,
                AnalysisRunModel.id != existing.id,
            )
            .first()
        )
        if conflicting_key:
            raise ValueError("Idempotency-Key is already bound to another Graph 1 run.")
        existing.status = "PENDING"
        existing.phase = "PREPARING"
        existing.current_node = None
        existing.test_run_id = None
        existing.anomaly_run_id = None
        existing.report_markdown = None
        existing.report_source = None
        existing.report_path = None
        existing.error = None
        existing.completed_at = None
        existing.idempotency_key = idempotency_key
        existing.updated_at = utc_now()
        for node in db.query(AnalysisNodeExecutionModel).filter_by(run_id=existing.id).all():
            node.status = "PENDING"
            node.output_json = "{}"
            node.error = None
            node.started_at = None
            node.completed_at = None
            node.sequence += len(ANALYSIS_NODES)
        db.commit()
        return existing, True
    existing_key = db.query(AnalysisRunModel).filter(AnalysisRunModel.idempotency_key == idempotency_key).first()
    if existing_key:
        if existing_key.graph1_run_id != graph1_run.id:
            raise ValueError("Idempotency-Key is already bound to another Graph 1 run.")
        return existing_key, False
    if graph1_run.status != "COMPLETED":
        raise ValueError("Graph 1 must be COMPLETED before analysis can start.")
    approved = ensure_completed_graph1_snapshot(db, graph1_run)
    if not approved:
        raise ValueError("Graph 1 has no approved rules for Graph 2.")
    run_id = f"analysis-{uuid.uuid4().hex[:18]}"
    graph1_state = _payload(graph1_run.state_json, {}) or {}
    run = AnalysisRunModel(
        id=run_id,
        graph1_run_id=graph1_run.id,
        dataset_id=graph1_run.dataset_id,
        workspace_id=graph1_run.workspace_id,
        dataset_version_id=graph1_run.dataset_version_id,
        profile_run_id=graph1_run.profile_run_id,
        rule_review_snapshot_id=graph1_state.get("rule_review_snapshot_id"),
        status="PENDING",
        phase="PREPARING",
        created_by=username,
        idempotency_key=idempotency_key,
    )
    db.add(run)
    # PostgreSQL enforces the child foreign key while flushing the batch.  Make
    # the parent row visible before adding the observable node records so the
    # Graph 2/3 launch remains atomic without violating that constraint.
    db.flush()
    for position, (graph_name, node_key) in enumerate(ANALYSIS_NODES, 1):
        db.add(AnalysisNodeExecutionModel(
            id=f"{run_id}:{node_key}",
            run_id=run_id,
            graph_name=graph_name,
            node_key=node_key,
            position=position,
            status="PENDING",
            output_json="{}",
            sequence=position,
        ))
    db.commit()
    return run, True


def serialize_analysis_run(run: AnalysisRunModel) -> dict[str, Any]:
    return {
        "id": run.id,
        "graph1_run_id": run.graph1_run_id,
        "dataset_id": run.dataset_id,
        "workspace_id": run.workspace_id,
        "dataset_version_id": run.dataset_version_id,
        "profile_run_id": run.profile_run_id,
        "status": run.status,
        "phase": run.phase,
        "current_node": run.current_node,
        "test_run_id": run.test_run_id,
        "anomaly_run_id": run.anomaly_run_id,
        "report_available": bool(run.report_markdown),
        "error": run.error,
        "created_by": run.created_by,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


def _safe_node_output(node_key: str, output: dict[str, Any] | None) -> dict[str, Any]:
    value = output or {}
    metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
    if node_key == "prepare_approved_rules":
        return {"approved_rules_count": int(value.get("approved_rules_count") or 0)}
    if node_key == "test_generator":
        artifact = value.get("dbt_artifact_ref") if isinstance(value.get("dbt_artifact_ref"), dict) else {}
        return {
            "generated_tests_count": len(value.get("generated_tests") or []),
            "generated_yaml_size": len(str(value.get("generated_dbt_yaml") or "").encode("utf-8")),
            "artifact": {
                "storage_kind": "object_storage" if artifact else "local_trace",
                "sha256": artifact.get("sha256"),
                "size_bytes": artifact.get("size_bytes"),
                "bucket": artifact.get("bucket"),
                "object_name": _basename(artifact.get("object_key")),
            },
            "errors": value.get("test_generation_errors") or [],
        }
    if node_key == "validate_dbt_project":
        return {
            "valid": value.get("dbt_validation_valid"),
            "skipped": bool(value.get("dbt_validation_skipped")),
            "error": value.get("dbt_validation_error"),
            "attempts": int(value.get("dbt_validation_attempts") or 0),
            "trace_file": _basename(value.get("dbt_validation_trace_path")),
        }
    if node_key == "dbt_validation_failed":
        return {"error": value.get("error"), "errors": value.get("test_generation_errors") or []}
    if node_key == "test_runner":
        results = value.get("test_results") or []
        counts: dict[str, int] = {"PASS": 0, "FAIL": 0, "ERROR": 0, "SKIPPED": 0}
        for row in results:
            status = _normalise_status(row.get("status"))
            counts[status] = counts.get(status, 0) + 1
        return {
            "results_count": len(results),
            "status_counts": counts,
            "execution_mode": metadata.get("dbt_execution_mode", "unknown"),
        }
    if node_key == "persist_report":
        return {
            "test_run_status": metadata.get("test_run_status"),
            "report_file": _basename(metadata.get("report_file_path")),
        }
    if node_key == "anomaly_detector":
        return {
            "decision": value.get("anomaly_decision") or {},
            "signals_count": len(value.get("signal_observations") or []),
            "status": value.get("anomaly_status"),
        }
    if node_key == "hypothesis_agent":
        return {
            "hypotheses_count": len(value.get("hypotheses") or []),
            "status": value.get("hypothesis_status"),
            "fallback_used": value.get("hypothesis_status") == "FALLBACK_USED",
            "model_name": metadata.get("model_name"),
            "latency_ms": metadata.get("hypothesis_latency_ms"),
        }
    if node_key == "persist_analysis":
        return {
            "anomaly_run_id": value.get("anomaly_run_id"),
            "persistence_error": metadata.get("persistence_error"),
        }
    if node_key == "report_writer":
        return {
            "source": value.get("report_source") or metadata.get("report_source"),
            "report_file": _basename(value.get("steward_report_path")),
            "markdown_length": len(str(value.get("steward_report_markdown") or "")),
        }
    return {}


def serialize_analysis_node(node: AnalysisNodeExecutionModel) -> dict[str, Any]:
    duration_ms = None
    if node.started_at and node.completed_at:
        duration_ms = round((node.completed_at - node.started_at).total_seconds() * 1000, 2)
    return {
        "graph_name": node.graph_name,
        "node_key": node.node_key,
        "position": node.position,
        "status": node.status,
        "output": _payload(node.output_json, {}) or {},
        "error": node.error,
        "sequence": node.sequence,
        "started_at": node.started_at.isoformat() if node.started_at else None,
        "completed_at": node.completed_at.isoformat() if node.completed_at else None,
        "duration_ms": duration_ms,
    }


def list_analysis_nodes(db: Session, run_id: str) -> list[dict[str, Any]]:
    rows = (
        db.query(AnalysisNodeExecutionModel)
        .filter(AnalysisNodeExecutionModel.run_id == run_id)
        .order_by(AnalysisNodeExecutionModel.position)
        .all()
    )
    return [serialize_analysis_node(row) for row in rows]


def _set_node(
    run_id: str,
    graph_name: str,
    node_key: str,
    output: dict[str, Any] | None = None,
    error: Exception | None = None,
) -> None:
    with Session(get_engine()) as db:
        run = db.get(AnalysisRunModel, run_id)
        node = db.get(AnalysisNodeExecutionModel, f"{run_id}:{node_key}")
        if not run or not node:
            return
        now = utc_now()
        if output is None and error is None:
            node.status = "RUNNING"
            node.started_at = node.started_at or now
            node.completed_at = None
            node.error = None
            run.status = "RUNNING"
            run.phase = "REPORT" if node_key == "report_writer" else graph_name
            run.current_node = node_key
        else:
            node.started_at = node.started_at or now
            node.completed_at = now
            node.output_json = _json(_safe_node_output(node_key, output))
            node.error = str(error)[:2000] if error else None
            node.status = "FAILED" if error else "SUCCEEDED"
        node.sequence += len(ANALYSIS_NODES)
        db.commit()


def _skip_nodes(run_id: str, node_keys: set[str], reason: str) -> None:
    with Session(get_engine()) as db:
        now = utc_now()
        rows = db.query(AnalysisNodeExecutionModel).filter(
            AnalysisNodeExecutionModel.run_id == run_id,
            AnalysisNodeExecutionModel.node_key.in_(node_keys),
            AnalysisNodeExecutionModel.status == "PENDING",
        ).all()
        for node in rows:
            node.status = "SKIPPED"
            node.completed_at = now
            node.output_json = _json({"reason": reason})
            node.sequence += len(ANALYSIS_NODES)
        db.commit()


async def execute_analysis_run(run_id: str) -> None:
    from src.agents.graph import build_anomaly_graph, build_execution_graph
    from src.agents.nodes.report_writer_node import _write_report_file
    from src.services.report_renderer import render_steward_report_vi

    with Session(get_engine()) as db:
        run = db.get(AnalysisRunModel, run_id)
        if not run or run.status in TERMINAL_STATUSES:
            return
        run.status = "RUNNING"
        run.phase = "PREPARING"
        run.error = None
        graph1_run_id = run.graph1_run_id
        dataset_id = run.dataset_id
        graph1_run = db.get(Graph1RunModel, graph1_run_id)
        graph1_state = _payload(graph1_run.state_json, {}) if graph1_run else {}
        graph1_metadata = graph1_state.get("metadata") if isinstance(graph1_state.get("metadata"), dict) else {}
        uploaded_dataset_profile = graph1_metadata.get("uploaded_dataset_profile")
        workspace_id = run.workspace_id or (graph1_run.workspace_id if graph1_run else None)
        dataset_version_id = run.dataset_version_id or (graph1_run.dataset_version_id if graph1_run else None)
        profile_run_id = run.profile_run_id or (graph1_run.profile_run_id if graph1_run else None)
        rule_review_snapshot_id = run.rule_review_snapshot_id or graph1_state.get("rule_review_snapshot_id")
        db.commit()

    try:
        approved_rules = get_approved_rules(graph1_run_id)
        if not approved_rules:
            raise RuntimeError("Graph 1 approved-rule snapshot is empty.")
        _set_node(run_id, "PREPARING", "prepare_approved_rules")
        _set_node(run_id, "PREPARING", "prepare_approved_rules", {"approved_rules_count": len(approved_rules)})

        test_run_id = f"dq-{uuid.uuid4().hex[:20]}"
        create_test_run(test_run_id, dataset_id)
        update_test_run_status(test_run_id, "RUNNING")
        with Session(get_engine()) as db:
            run = db.get(AnalysisRunModel, run_id)
            if not run:
                return
            run.test_run_id = test_run_id
            run.phase = "GRAPH2"
            db.commit()

        async def observer(graph_name: str, node_key: str, output: dict | None, error: Exception | None) -> None:
            _set_node(run_id, graph_name, node_key, output, error)

        graph2 = build_execution_graph(observer=observer)
        graph2_state = await graph2.ainvoke({
            "dataset_id": dataset_id,
            "workspace_id": workspace_id,
            "dataset_version_id": dataset_version_id,
            "profile_run_id": profile_run_id,
            "rule_review_snapshot_id": rule_review_snapshot_id,
            "test_run_id": test_run_id,
            "rule_run_id": graph1_run_id,
            "approved_rules": approved_rules,
            "metadata": {
                "analysis_run_id": run_id,
                "uploaded_dataset_profile": uploaded_dataset_profile,
                "source_checksum": graph1_metadata.get("source_checksum"),
            },
        })
        graph2_status = graph2_state.get("graph2_status") or (graph2_state.get("metadata") or {}).get("graph2_status")
        if dataset_version_id and (graph2_state.get("execution_mode") == "versioned_source_adapter" or (graph2_state.get("metadata") or {}).get("execution_mode") == "versioned_source_adapter"):
            graph2_state.setdefault("dbt_validation_valid", True)
        if graph2_state.get("dbt_validation_valid") is True:
            _skip_nodes(run_id, {"dbt_validation_failed"}, "Validation succeeded; failure branch was not selected.")
        if graph2_state.get("error") or (not dataset_version_id and graph2_state.get("dbt_validation_valid") is not True):
            message = str(graph2_state.get("error") or graph2_state.get("dbt_validation_error") or "Graph 2 failed.")
            update_test_run_status(test_run_id, "FAILED", error=message)
            _skip_nodes(
                run_id,
                {"test_runner", "persist_report", "anomaly_detector", "hypothesis_agent", "persist_analysis", "report_writer"},
                "Graph 2 did not complete successfully.",
            )
            with Session(get_engine()) as db:
                run = db.get(AnalysisRunModel, run_id)
                if run:
                    run.status = "FAILED"
                    run.error = message[:2000]
                    run.completed_at = utc_now()
                    db.commit()
            return

        if graph2_status == "FAILED":
            message = "Graph 2 produced no usable rule evidence."
            update_test_run_status(test_run_id, "FAILED", error=message)
            _skip_nodes(run_id, {"anomaly_detector", "hypothesis_agent", "persist_analysis", "report_writer"}, message)
            with Session(get_engine()) as db:
                failed_run = db.get(AnalysisRunModel, run_id)
                if failed_run:
                    failed_run.status = "FAILED"
                    failed_run.phase = "GRAPH2"
                    failed_run.error = message
                    failed_run.completed_at = utc_now()
                    db.commit()
            return
        graph2_partial = graph2_status == "PARTIAL"

        anomaly_run_id = f"anom-{uuid.uuid4().hex[:12]}"
        with Session(get_engine()) as db:
            run = db.get(AnalysisRunModel, run_id)
            if run:
                run.phase = "GRAPH3"
                run.anomaly_run_id = anomaly_run_id
                db.commit()

        graph3_state: dict[str, Any] = {
            "anomaly_run_id": anomaly_run_id,
            "execution_run_id": test_run_id,
            "dataset_id": dataset_id,
            "workspace_id": workspace_id,
            "dataset_version_id": dataset_version_id,
            "profile_run_id": profile_run_id,
            "rule_review_snapshot_id": rule_review_snapshot_id,
            "detector_config_version": "anomaly-v1",
            "metadata": {"analysis_run_id": run_id},
        }
        graph3_failed = False
        try:
            graph3 = build_anomaly_graph(observer=observer)
            graph3_state = await graph3.ainvoke(graph3_state)
            graph3_failed = bool(graph3_state.get("error")) or graph3_state.get("anomaly_status") == "FAILED"
        except Exception as exc:
            logger.exception("Graph 3 failed for analysis run %s", run_id)
            graph3_failed = True
            graph3_state["error"] = str(exc)
            _skip_nodes(run_id, {"hypothesis_agent", "persist_analysis", "report_writer"}, "Graph 3 stopped before this node.")

        report_markdown = str(graph3_state.get("steward_report_markdown") or "")
        report_source = str(graph3_state.get("report_source") or "")
        report_path = str(graph3_state.get("steward_report_path") or "")
        if not report_markdown:
            report_markdown = render_steward_report_vi(test_run_id, dataset_id, graph3_state)
            report_source = "FALLBACK"
            try:
                report_path = _write_report_file(test_run_id, report_markdown)
            except Exception:
                report_path = ""

        persisted_anomaly_id = str(
            graph3_state.get("anomaly_run_id")
            or (graph3_state.get("metadata") or {}).get("persisted_anomaly_run_id")
            or anomaly_run_id
        )
        with Session(get_engine()) as db:
            run = db.get(AnalysisRunModel, run_id)
            if run:
                run.anomaly_run_id = persisted_anomaly_id
                run.report_markdown = report_markdown
                run.report_source = report_source or "FALLBACK"
                run.report_path = report_path or None
                run.status = "PARTIAL" if graph3_failed or graph2_partial else "COMPLETED"
                run.phase = "REPORT"
                run.current_node = "report_writer"
                run.error = (
                    str(graph3_state.get("error"))[:2000]
                    if graph3_failed
                    else ("Graph 2 completed partially; some rule evidence is unavailable." if graph2_partial else None)
                )
                run.completed_at = utc_now()
                db.commit()
    except Exception as exc:
        logger.exception("Analysis run %s failed", run_id)
        _skip_nodes(run_id, {node_key for _, node_key in ANALYSIS_NODES}, "Analysis stopped after an unrecoverable error.")
        with Session(get_engine()) as db:
            run = db.get(AnalysisRunModel, run_id)
            if run:
                run.status = "FAILED"
                run.error = str(exc)[:2000]
                run.completed_at = utc_now()
                db.commit()


def build_analysis_result(db: Session, run: AnalysisRunModel) -> dict[str, Any]:
    approved = {row["rule_id"]: row for row in get_approved_rules(run.graph1_run_id)}
    test_results = get_test_results(run.test_run_id) if run.test_run_id else []
    proposal_rows = db.query(RuleProposalModel).filter(RuleProposalModel.id.in_(list(approved))).all() if approved else []
    proposal_by_id = {row.id: row for row in proposal_rows}
    dq_rows = db.query(DqResultModel).filter(DqResultModel.run_id == run.test_run_id).all() if run.test_run_id else []
    dq_by_rule = {row.rule_id: row for row in dq_rows}

    anomaly_run = None
    signals: list[AnomalySignalModel] = []
    hypotheses: list[AnomalyHypothesisModel] = []
    if run.test_run_id:
        anomaly_run = db.query(AnomalyRunModel).filter(AnomalyRunModel.execution_run_id == run.test_run_id).first()
    if anomaly_run:
        signals = db.query(AnomalySignalModel).filter(AnomalySignalModel.anomaly_run_id == anomaly_run.id).all()
        hypotheses = db.query(AnomalyHypothesisModel).filter(AnomalyHypothesisModel.anomaly_run_id == anomaly_run.id).all()

    alert_by_rule: dict[str, AnomalySignalModel] = {}
    for signal in signals:
        if signal.target_type == "RULE" and signal.score >= 0.70:
            current = alert_by_rule.get(signal.target_id)
            if current is None or signal.score > current.score:
                alert_by_rule[signal.target_id] = signal

    result_rows = []
    status_counts = {"PASS": 0, "FAIL": 0, "ERROR": 0, "SKIPPED": 0}
    total_checked = 0
    total_failed = 0
    total_duration = 0.0
    for row in test_results:
        rule = approved.get(row["rule_id"], {})
        proposal = proposal_by_id.get(row["rule_id"])
        dq_row = dq_by_rule.get(row["rule_id"])
        status = _normalise_status(row.get("status"))
        status_counts[status] = status_counts.get(status, 0) + 1
        checked = int(row.get("total_rows") or row.get("checked_count") or 0)
        failed = int(row.get("violation_count") or row.get("failed_count") or 0)
        duration = float(row.get("duration_ms") or 0.0)
        total_checked += checked
        total_failed += failed
        total_duration += duration
        alert = alert_by_rule.get(row["rule_id"])
        evidence_refs = _payload(proposal.evidence_refs, []) if proposal else []
        result_rows.append({
            "rule_id": row["rule_id"],
            "rule_title": rule.get("rule_name") or rule.get("rule_description") or row["rule_id"],
            "rule_type": rule.get("rule_type") or row.get("rule_type") or "UNKNOWN",
            "table_name": row.get("table_name") or rule.get("table_name") or "source_rows",
            "column": row.get("column") if row.get("column") is not None else rule.get("column"),
            "severity": rule.get("severity") or "MEDIUM",
            "dimension": rule.get("dimension") or "VALIDITY",
            "status": status,
            "checked_count": checked,
            "failed_count": failed,
            "violation_rate": float(row.get("violation_rate") or 0.0),
            "duration_ms": duration,
            "dbt_status": (dq_row.dbt_status if dq_row else None) or "NOT_RUN",
            "metrics_status": (dq_row.metrics_status if dq_row else None) or status,
            "sample_row_ids": row.get("sample_failures") or [],
            "evidence_refs": evidence_refs if isinstance(evidence_refs, list) else [],
            "error": row.get("error"),
            "anomaly": ({
                "flagged": True,
                "signal_id": alert.id,
                "score": alert.score,
                "reliability": alert.reliability,
                "family": alert.family,
                "explanation": alert.explanation_code,
            } if alert else {"flagged": False}),
        })

    node_rows = list_analysis_nodes(db, run.id)
    node_by_key = {node["node_key"]: node for node in node_rows}
    generator_output = node_by_key.get("test_generator", {}).get("output", {})
    validation_output = node_by_key.get("validate_dbt_project", {}).get("output", {})
    runner_output = node_by_key.get("test_runner", {}).get("output", {})
    generated_tests_count = int(generator_output.get("generated_tests_count") or 0)
    if (
        generated_tests_count == 0
        and runner_output.get("execution_mode") == "not_run_versioned_source_adapter"
    ):
        # Older persisted node summaries were written before versioned adapter
        # checks were represented in ``generated_tests``. The result rows are
        # the durable one-to-one execution evidence, so repair the observable
        # counter without rewriting historical node payloads.
        generated_tests_count = len(result_rows)
    dominant_signal = max(signals, key=lambda item: (item.score, item.reliability), default=None)

    graph3_signals = [{
        "signal_id": signal.id,
        "family": signal.family,
        "target_type": signal.target_type,
        "target_id": signal.target_id,
        "score": signal.score,
        "reliability": signal.reliability,
        "observed_value": signal.observed_value,
        "baseline": _payload(signal.baseline, {}) or {},
        "sufficient_history": signal.sufficient_history,
        "detector_name": signal.detector_name,
        "detector_version": signal.detector_version,
        "explanation": signal.explanation_code,
        "evidence_refs": _payload(signal.evidence_refs, []) or [],
    } for signal in sorted(signals, key=lambda item: item.score, reverse=True)]

    graph3_hypotheses = [{
        "id": hypothesis.id,
        "hypothesis_type": hypothesis.hypothesis_type,
        "summary": hypothesis.summary,
        "confidence": hypothesis.confidence,
        "supporting_signal_ids": _payload(hypothesis.supporting_signal_ids, []) or [],
        "contradicting_signal_ids": _payload(hypothesis.contradicting_signal_ids, []) or [],
        "evidence_refs": _payload(hypothesis.evidence_refs, []) or [],
        "recommended_checks": _payload(hypothesis.recommended_checks, []) or [],
        "missing_evidence": hypothesis.missing_evidence,
        "limitations": hypothesis.limitations,
        "model_name": hypothesis.model_name,
        "prompt_version": hypothesis.prompt_version,
        "latency_ms": hypothesis.latency_ms,
        "fallback_used": hypothesis.fallback_used,
    } for hypothesis in sorted(hypotheses, key=lambda item: item.confidence, reverse=True)]

    return {
        "run": serialize_analysis_run(run),
        "nodes": node_rows,
        "graph2": {
            "available": bool(run.test_run_id),
            "summary": {
                "total": len(result_rows),
                "passed": status_counts.get("PASS", 0),
                "failed": status_counts.get("FAIL", 0),
                "errors": status_counts.get("ERROR", 0),
                "skipped": status_counts.get("SKIPPED", 0),
                "total_checked": total_checked,
                "total_failed": total_failed,
                "duration_ms": round(total_duration, 2),
            },
            "dbt": {
                "generated_tests_count": generated_tests_count,
                "validation_status": "SKIPPED" if validation_output.get("skipped") else "PASS" if validation_output.get("valid") else "FAIL",
                "validation_skipped": bool(validation_output.get("skipped")),
                "validation_error": validation_output.get("error"),
                "validation_attempts": validation_output.get("attempts", 0),
                "execution_mode": runner_output.get("execution_mode", "pending"),
                "artifact": generator_output.get("artifact") or {},
            },
            "results": result_rows,
        },
        "graph3": {
            "available": anomaly_run is not None,
            "decision": ({
                "decision": anomaly_run.decision,
                "score": anomaly_run.score,
                "confidence": anomaly_run.confidence,
                "severity": anomaly_run.severity,
                "dominant_family": dominant_signal.family if dominant_signal else None,
                "override_reason": anomaly_run.error_message,
            } if anomaly_run else None),
            "signals": graph3_signals,
            "hypotheses": graph3_hypotheses,
        },
        "report": {
            "available": bool(run.report_markdown),
            "markdown": run.report_markdown or "",
            "source": run.report_source,
            "file_name": _basename(run.report_path),
            "generated_at": run.completed_at.isoformat() if run.completed_at else None,
        },
    }
