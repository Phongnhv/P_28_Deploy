from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from src.models.database import (
    ColumnProfileModel,
    DatasetModel,
    Graph1NodeExecutionModel,
    Graph1RunModel,
    ProfileModel,
    RuleProposalModel,
    RuleVersionModel,
    SemanticContractModel,
)
from src.services.rule_store import ProposedRuleModel, create_run, get_engine, save_semantic_contract
from src.time_utils import utc_now

logger = logging.getLogger(__name__)

GRAPH1_NODES = [
    "raw_profiler",
    "profiler_digest",
    "data_dictionary_generator",
    "dataset_understanding",
    "hitl_semantic_gate",
    "rule_candidate_builder",
    "prompt_customizer",
    "rule_proposer",
    "hitl_gate",
]
TERMINAL_STATUSES = {"COMPLETED", "FAILED"}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _payload(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {"value": value}


def _uploaded_profile(db: Session, dataset_id: str) -> dict[str, Any]:
    dataset = db.get(DatasetModel, dataset_id)
    profile = db.get(ProfileModel, dataset_id)
    columns = (
        db.query(ColumnProfileModel)
        .filter(ColumnProfileModel.profile_dataset_id == dataset_id)
        .order_by(ColumnProfileModel.id)
        .all()
    )
    if not dataset or not profile or not columns or dataset.status != "PROFILE_READY":
        raise ValueError("Dataset profiling must finish successfully before Graph 1 can start.")
    raw_columns: dict[str, Any] = {}
    for column in columns:
        quantiles = _payload(column.quantiles_json)
        raw_columns[column.name] = {
            "type": column.data_type,
            "null_count": round(column.null_rate * profile.row_count),
            "null_pct": column.null_rate * 100,
            "distinct_in_sample": column.distinct_count,
            "full_distinct_count": column.full_distinct_count,
            "is_unique_full_table": column.is_unique_full_table,
            "min": column.min_value,
            "max": column.max_value,
            "negative_pct": (column.negative_rate or 0) * 100,
            "percentiles": quantiles,
            "sample_values": [column.sample_value] if column.sample_value else [],
            "is_categorical": column.distinct_count <= min(100, max(10, profile.row_count // 20)),
        }
    return {
        "source_rows": {
            "table_metadata": {
                "table_name": "source_rows",
                "total_rows": profile.row_count,
                "sampled_rows": profile.row_count,
                "sampling_rate": 1.0,
                "is_sampled": False,
                "dataset_id": dataset_id,
                "source_label": dataset.source_label,
            },
            "schema_constraints": {"primary_key": [], "foreign_keys": [], "unique_constraints": []},
            "cross_column_hints": _payload(profile.cross_field_metrics_json).get("value", [])
            if not (profile.cross_field_metrics_json or "").lstrip().startswith("[")
            else json.loads(profile.cross_field_metrics_json or "[]"),
            "columns": raw_columns,
            "quality_summary": {
                "completeness_score": profile.completeness_score,
                "validity_score": profile.validity_score,
                "duplicate_rate": profile.duplicate_rate,
            },
        }
    }


def create_graph1_run(db: Session, dataset_id: str, username: str, idempotency_key: str) -> Graph1RunModel:
    existing = db.query(Graph1RunModel).filter(Graph1RunModel.idempotency_key == idempotency_key).first()
    if existing:
        return existing
    profile = _uploaded_profile(db, dataset_id)
    run_id = f"g1-{uuid.uuid4().hex[:20]}"
    run = Graph1RunModel(
        id=run_id,
        dataset_id=dataset_id,
        status="PENDING",
        created_by=username,
        idempotency_key=idempotency_key,
        state_json=_json({
            "dataset_id": dataset_id,
            "rule_run_id": run_id,
            "target_tables": ["source_rows"],
            "metadata": {"uploaded_dataset_profile": profile, "workflow": "graph1-studio"},
        }),
    )
    db.add(run)
    for position, node_key in enumerate(GRAPH1_NODES, 1):
        db.add(Graph1NodeExecutionModel(
            id=f"{run_id}:{node_key}", run_id=run_id, node_key=node_key,
            position=position, status="PENDING", output_json="{}", sequence=position,
        ))
    db.commit()
    # hitl_gate persists into the legacy rule tables and expects this job record.
    create_run(run_id, dataset_id)
    return run


def serialize_run(run: Graph1RunModel) -> dict[str, Any]:
    return {
        "id": run.id, "dataset_id": run.dataset_id, "status": run.status,
        "current_node": run.current_node, "error": run.error,
        "created_by": run.created_by,
        "created_at": run.created_at.isoformat(), "updated_at": run.updated_at.isoformat(),
    }


def serialize_node(node: Graph1NodeExecutionModel) -> dict[str, Any]:
    return {
        "node_key": node.node_key, "position": node.position, "status": node.status,
        "output": _payload(node.output_json), "error": node.error, "sequence": node.sequence,
        "started_at": node.started_at.isoformat() if node.started_at else None,
        "completed_at": node.completed_at.isoformat() if node.completed_at else None,
    }


def list_nodes(db: Session, run_id: str) -> list[dict[str, Any]]:
    return [serialize_node(node) for node in db.query(Graph1NodeExecutionModel).filter_by(run_id=run_id).order_by(Graph1NodeExecutionModel.position).all()]


def _mark_skipped_before(db: Session, run_id: str, current_position: int) -> None:
    for node in db.query(Graph1NodeExecutionModel).filter_by(run_id=run_id, status="PENDING").all():
        if node.position < current_position:
            node.status = "SKIPPED"
            node.completed_at = utc_now()


def _mark_blocked_after(db: Session, run_id: str, current_node: str) -> None:
    """Mark canonical nodes after a failed node as intentionally not executed."""
    try:
        current_position = GRAPH1_NODES.index(current_node) + 1
    except ValueError:
        return
    now = utc_now()
    for node in db.query(Graph1NodeExecutionModel).filter_by(run_id=run_id, status="PENDING").all():
        if node.position > current_position:
            node.status = "SKIPPED"
            node.error = None
            node.output_json = _json({
                "blocked_by": current_node,
                "reason": f"Node was not executed because {current_node} failed.",
            })
            node.completed_at = now


async def execute_graph1_run(run_id: str) -> None:
    from src.agents.graph import build_proposal_graph

    with Session(get_engine()) as db:
        run = db.get(Graph1RunModel, run_id)
        if not run or run.status in TERMINAL_STATUSES:
            return
        state = _payload(run.state_json)
        run.status, run.error = "RUNNING", None
        db.commit()
        try:
            graph = build_proposal_graph()
            async for update in graph.astream(state, stream_mode="updates"):
                if not isinstance(update, dict):
                    continue
                for node_key, delta in update.items():
                    if node_key not in GRAPH1_NODES or not isinstance(delta, dict):
                        continue
                    node = db.get(Graph1NodeExecutionModel, f"{run_id}:{node_key}")
                    if not node:
                        continue
                    _mark_skipped_before(db, run_id, node.position)
                    now = utc_now()
                    node.started_at = node.started_at or now
                    node.output_json = _json(delta)
                    node.error = str(delta.get("error")) if delta.get("error") else None
                    node.status = "FAILED" if node.error else "SUCCEEDED"
                    node.completed_at = now
                    node.sequence += len(GRAPH1_NODES)
                    run.current_node = node_key
                    state.update(delta)
                    run.state_json = _json(state)
                    db.commit()
            if state.get("error"):
                raise RuntimeError(str(state["error"]))
            if state.get("pause_reason") == "AWAITING_SEMANTIC_REVIEW":
                run.status = "AWAITING_SEMANTIC_REVIEW"
                gate = db.get(Graph1NodeExecutionModel, f"{run_id}:hitl_semantic_gate")
                if gate:
                    gate.status = "WAITING_REVIEW"
                db.commit()
                return
            if not state.get("proposed_rules"):
                raise RuntimeError("Rule proposer returned no valid structured proposals.")
            run.status = "AWAITING_RULE_REVIEW"
            gate = db.get(Graph1NodeExecutionModel, f"{run_id}:hitl_gate")
            if gate:
                gate.status = "WAITING_REVIEW"
                gate.output_json = _json({"proposed_rules": state.get("proposed_rules", []), "metadata": state.get("metadata", {})})
            db.commit()
        except Exception as exc:
            logger.exception("Graph 1 run %s failed", run_id)
            run.status = "FAILED"
            run.error = str(exc)[:2000]
            if run.current_node:
                node = db.get(Graph1NodeExecutionModel, f"{run_id}:{run.current_node}")
                if node:
                    node.status, node.error = "FAILED", run.error
                _mark_blocked_after(db, run_id, run.current_node)
            db.commit()


def confirm_semantic_review(db: Session, run: Graph1RunModel, contract: dict[str, Any]) -> None:
    if run.status != "AWAITING_SEMANTIC_REVIEW":
        raise ValueError("This run is not waiting for semantic review.")
    if not isinstance(contract.get("tables"), dict) or not contract["tables"]:
        raise ValueError("Semantic contract must contain at least one table.")
    contract = {**contract, "status": "confirmed", "dataset_id": run.dataset_id}
    save_semantic_contract(run.id, run.dataset_id, contract, status="CONFIRMED")
    state = _payload(run.state_json)
    state["semantic_contract"] = contract
    state.pop("pause_reason", None)
    state.pop("error", None)
    run.state_json = _json(state)
    run.status = "PENDING"
    gate = db.get(Graph1NodeExecutionModel, f"{run.id}:hitl_semantic_gate")
    if gate:
        gate.status = "SUCCEEDED"
        gate.output_json = _json({"semantic_contract": contract, "decision": "APPROVED"})
        gate.completed_at = utc_now()
    db.commit()


def review_rules(db: Session, run: Graph1RunModel, decisions: list[dict[str, Any]], reviewer: str) -> None:
    if run.status != "AWAITING_RULE_REVIEW":
        raise ValueError("This run is not waiting for rule review.")
    state = _payload(run.state_json)
    run_rule_ids = {
        str(item.get("rule_id"))
        for item in state.get("proposed_rules", [])
        if isinstance(item, dict) and item.get("rule_id")
    }
    proposals = db.query(RuleProposalModel).filter(RuleProposalModel.id.in_(run_rule_ids)).all() if run_rule_ids else []
    by_id = {proposal.id: proposal for proposal in proposals}
    if not decisions or set(by_id) != {str(item.get("rule_id")) for item in decisions}:
        raise ValueError("Every proposal in this run must receive one decision.")
    approved = 0
    edited = 0
    rejected = 0
    decision_by_id: dict[str, dict[str, Any]] = {}
    for item in decisions:
        rule_id = str(item["rule_id"])
        proposal = by_id[rule_id]
        legacy = db.get(ProposedRuleModel, (run.id, rule_id))
        if not legacy:
            raise ValueError(f"Legacy rule snapshot is missing for {rule_id}.")
        action = str(item.get("action", "")).lower()
        if action not in {"approve", "reject", "edit"}:
            raise ValueError("Rule decision must be approve, reject, or edit.")
        normalized_parameters: dict[str, Any] | None = None
        if action == "edit":
            rule = item.get("rule")
            if not isinstance(rule, dict) or not rule.get("type"):
                raise ValueError("Edited rules require a valid rule payload.")
            raw_parameters = rule.get("parameters") if isinstance(rule.get("parameters"), dict) else {}
            normalized_parameters = {
                "min": raw_parameters.get("min", raw_parameters.get("min_value")),
                "max": raw_parameters.get("max", raw_parameters.get("max_value")),
                "max_null_pct": raw_parameters.get("max_null_pct"),
                "accepted_values": raw_parameters.get("accepted_values"),
                "regex": raw_parameters.get("regex"),
                "target_column": raw_parameters.get("target_column"),
                "operator": raw_parameters.get("operator"),
                "min_row_count": raw_parameters.get("min_row_count"),
            }
            normalized_parameters = {
                key: value for key, value in normalized_parameters.items()
                if value is not None and value != ""
            }
            canonical_spec: dict[str, Any] = {
                "type": str(rule["type"]),
                "column": rule.get("column") or None,
            }
            if "min" in normalized_parameters:
                canonical_spec["min_value"] = normalized_parameters["min"]
            if "max" in normalized_parameters:
                canonical_spec["max_value"] = normalized_parameters["max"]
            if "accepted_values" in normalized_parameters:
                canonical_spec["allowed_values"] = normalized_parameters["accepted_values"]
            for key in ("max_null_pct", "regex", "target_column", "operator", "min_row_count"):
                if key in normalized_parameters:
                    canonical_spec[key] = normalized_parameters[key]
            proposal.rule_type = str(rule["type"])
            proposal.title = str(rule.get("rule_name") or proposal.title)
            proposal.rule_name = str(rule.get("rule_name") or proposal.rule_name)
            proposal.description = str(rule.get("rule_description") or proposal.description)
            proposal.rule_spec = _json(canonical_spec)
            proposal.status = "APPROVED"
            legacy.rule_type = proposal.rule_type
            legacy.column_name = rule.get("column") or None
            legacy.rule_name = proposal.rule_name
            legacy.rule_description = proposal.description
            legacy.edited_parameters = _json(normalized_parameters)
            approved += 1
            edited += 1
        elif action == "approve":
            proposal.status = "APPROVED"
            approved += 1
        else:
            proposal.status = "REJECTED"
            rejected += 1
        legacy.status = proposal.status
        legacy.reviewer = reviewer
        legacy.reviewed_at = utc_now()
        proposal.updated_at = utc_now()
        if proposal.status == "APPROVED":
            version_id = f"rv_{proposal.id}"
            version = db.get(RuleVersionModel, version_id)
            if version:
                version.rule_spec = proposal.rule_spec
                version.status = "APPROVED"
                version.created_at = utc_now()
            else:
                db.add(RuleVersionModel(
                    id=version_id,
                    rule_proposal_id=proposal.id,
                    dataset_id=proposal.dataset_id,
                    rule_spec=proposal.rule_spec,
                    status="APPROVED",
                    version=1,
                    created_at=utc_now(),
                ))
        decision_by_id[rule_id] = {
            "action": action,
            "parameters": normalized_parameters,
            "rule": item.get("rule") if action == "edit" else None,
        }
    if not approved:
        raise ValueError("At least one rule must be approved or edited.")
    reviewed_rules: list[dict[str, Any]] = []
    for raw_rule in state.get("proposed_rules", []):
        if not isinstance(raw_rule, dict):
            continue
        rule_id = str(raw_rule.get("rule_id", ""))
        review = decision_by_id.get(rule_id, {})
        action = str(review.get("action", "reject"))
        next_rule = dict(raw_rule)
        next_rule["review_action"] = action
        next_rule["status"] = "REJECTED" if action == "reject" else "APPROVED"
        if action == "edit" and isinstance(review.get("rule"), dict):
            edited_rule = review["rule"]
            next_rule.update({
                "rule_type": edited_rule.get("type", next_rule.get("rule_type")),
                "rule_name": edited_rule.get("rule_name", next_rule.get("rule_name")),
                "rule_description": edited_rule.get("rule_description", next_rule.get("rule_description")),
                "column": edited_rule.get("column"),
                "parameters": review.get("parameters") or {},
            })
        reviewed_rules.append(next_rule)
    state["proposed_rules"] = reviewed_rules
    state["approved_rules"] = [rule for rule in reviewed_rules if rule.get("status") == "APPROVED"]
    state["metadata"] = {
        **(state.get("metadata") if isinstance(state.get("metadata"), dict) else {}),
        "hitl_status": "APPROVED",
        "reviewer": reviewer,
    }
    run.state_json = _json(state)
    run.status, run.current_node = "COMPLETED", "hitl_gate"
    gate = db.get(Graph1NodeExecutionModel, f"{run.id}:hitl_gate")
    if gate:
        gate.status = "SUCCEEDED"
        gate.completed_at = utc_now()
        gate.output_json = _json({
            "decision": "APPROVED",
            "proposed_rules": reviewed_rules,
            "approved_rules": state["approved_rules"],
            "total_count": len(reviewed_rules),
            "approved_count": approved,
            "edited_count": edited,
            "rejected_count": rejected,
            "pending_count": 0,
            "reviewer": reviewer,
            "metadata": state["metadata"],
        })
    db.commit()
