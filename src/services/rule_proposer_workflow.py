"""Durable transitions for the steward-owned rule workflow.

Proposal/review, publishing, safe typed-rule execution, and analysis are
separate stages.  Agents never produce executable SQL: the runner only accepts
approved typed rule versions.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from src.config import get_settings
from src.models.database import (
    AnomalyHypothesisModel,
    AnomalyRunModel,
    ColumnProfileModel,
    DatasetModel,
    DatasetVersionModel,
    DqResultModel,
    DqRunModel,
    JobModel,
    ProfileModel,
    ProfileRunSnapshotModel,
    RuleProposalModel,
    RulesetVersionModel,
    RuleVersionModel,
    WorkflowArtifactModel,
    WorkflowRunModel,
)
from src.services.dashboard_agent_workflow import (
    generate_dashboard_proposals,
    generate_rule_proposals_via_graph_1b,
)
from src.services.node_telemetry import start_graph_run
from src.services.rule_store import get_engine
from src.time_utils import utc_now

logger = logging.getLogger(__name__)

# Graph 1A and 1B each run three nodes, two of which call an LLM.  The old
# single-node shortcut needed 90s; three nodes need proportionally more headroom
# before the workflow gives up on the agent.
UNDERSTANDING_GRAPH_TIMEOUT_SECONDS = 180
RULE_PROPOSAL_GRAPH_TIMEOUT_SECONDS = 240

STEP_KEYS = (
    "UPLOAD_PROFILE",
    "UNDERSTAND_DATA",
    "PROPOSE_RULES",
    "REVIEW_RULES",
    "PUBLISH_RULESET",
    "RUN_CHECKS",
    "ANALYZE_REPORT",
)


class WorkflowError(ValueError):
    pass


def _fingerprint(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _steps(profile_ready: bool) -> list[dict[str, Any]]:
    result = [
        {"key": "UPLOAD_PROFILE", "status": "COMPLETED" if profile_ready else "READY", "artifact_ids": []},
        {"key": "UNDERSTAND_DATA", "status": "READY" if profile_ready else "LOCKED", "artifact_ids": []},
        {"key": "PROPOSE_RULES", "status": "LOCKED", "artifact_ids": []},
        {"key": "REVIEW_RULES", "status": "LOCKED", "artifact_ids": []},
    ]
    return result + [{"key": key, "status": "LOCKED", "artifact_ids": []} for key in STEP_KEYS[4:]]


def _decode_steps(run: WorkflowRunModel) -> list[dict[str, Any]]:
    return json.loads(run.steps_json or "[]")


def _encode_steps(run: WorkflowRunModel, steps: list[dict[str, Any]]) -> None:
    run.steps_json = json.dumps(steps, ensure_ascii=False)


def _step(steps: list[dict[str, Any]], key: str) -> dict[str, Any]:
    found = next((item for item in steps if item["key"] == key), None)
    if not found:
        raise WorkflowError("Unknown workflow step.")
    return found


def _complete(steps: list[dict[str, Any]], key: str) -> None:
    item = _step(steps, key)
    item.update(status="COMPLETED", completed_at=utc_now().isoformat())


def serialize_run(run: WorkflowRunModel) -> dict[str, Any]:
    return {
        "id": run.id,
        "dataset_id": run.dataset_id,
        "current_step": run.current_step,
        "iteration": run.revision,
        "max_iterations": 1,
        "steps": _decode_steps(run),
    }


def serialize_artifact(artifact: WorkflowArtifactModel) -> dict[str, Any]:
    return {
        "id": artifact.id,
        "workflow_run_id": artifact.workflow_run_id,
        "agent_role": "DATA_RULE_AGENT",
        "type": artifact.artifact_type,
        "version": artifact.version,
        "status": "STALE" if artifact.stale else artifact.status,
        "temporary": artifact.stale,
        "payload": json.loads(artifact.payload_json or "{}"),
        "created_at": artifact.created_at.isoformat(),
    }


def _current_artifact(
    db: Session, run_id: str, step_key: str, artifact_type: str
) -> WorkflowArtifactModel | None:
    return (
        db.query(WorkflowArtifactModel)
        .filter_by(workflow_run_id=run_id, step_key=step_key, artifact_type=artifact_type, stale=False)
        .order_by(WorkflowArtifactModel.version.desc(), WorkflowArtifactModel.created_at.desc())
        .first()
    )


def _dictionary_snapshot(semantic_payload: dict[str, Any]) -> dict[str, Any]:
    """Build a durable dictionary payload from the profile-backed contract."""
    columns = semantic_payload.get("columns") or []
    return {
        "table_name": semantic_payload.get("table_name") or semantic_payload.get("dataset_id"),
        "description": semantic_payload.get("summary", ""),
        "columns": [
            {
                "name": item.get("name"),
                "description": item.get("description", ""),
                "semantic_type": item.get("semantic_type", "unknown"),
                "business_role": item.get("business_role", "unknown"),
                "nullable_expected": item.get("nullable_expected", True),
                "governance_notes": item.get("governance_notes", []),
            }
            for item in columns
            if isinstance(item, dict) and item.get("name")
        ],
        "business_rules": semantic_payload.get("assumptions", []),
        "source": "semantic_contract_projection",
    }


def _versioned_profile_snapshot_row(db: Session, dataset_id: str) -> ProfileRunSnapshotModel | None:
    """The completed aggregate snapshot a versioned import produces.

    ``POST /workspaces/{id}/datasets/import`` never writes a ``ProfileModel``
    row: it records an immutable ``profile_runs`` snapshot keyed by dataset
    version instead. ``GET /datasets/{id}/profile`` already adapts that shape
    for the dashboard, but this module did not, so a versioned dataset looked
    unprofiled to the workflow no matter how complete its profile was.
    """
    latest_version = (
        db.query(DatasetVersionModel)
        .filter_by(dataset_id=dataset_id, status="READY")
        .order_by(DatasetVersionModel.version_number.desc())
        .first()
    )
    if not latest_version:
        return None
    return (
        db.query(ProfileRunSnapshotModel)
        .filter_by(dataset_version_id=latest_version.id, status="COMPLETED")
        .order_by(ProfileRunSnapshotModel.completed_at.desc())
        .first()
    )


def _has_completed_profile(db: Session, dataset_id: str) -> bool:
    """True for either profiling path, legacy or versioned."""
    if db.get(ProfileModel, dataset_id) is not None:
        return True
    return _versioned_profile_snapshot_row(db, dataset_id) is not None


def _snapshot_from_versioned_profile(snapshot: ProfileRunSnapshotModel) -> dict[str, Any]:
    """Render a versioned snapshot in the shape Graph 1A already consumes."""
    metrics = json.loads(snapshot.metrics_json or "{}")
    raw_columns = metrics.get("columns") or json.loads(snapshot.schema_json or "[]")
    columns: list[dict[str, Any]] = []
    for column in raw_columns:
        if not isinstance(column, dict) or not column.get("name"):
            continue
        null_rate = float(column.get("null_rate") or 0.0)
        columns.append({
            "name": str(column["name"]),
            "data_type": str(column.get("logical_type") or column.get("physical_type") or "string"),
            "null_rate": null_rate,
            "null_percentage": null_rate * 100,
            "distinct_count": int(column.get("distinct_count") or 0),
            "full_distinct_count": int(column.get("distinct_count") or 0),
            "non_null_count": int(column.get("non_null_count") or 0),
            "uniqueness_rate": float(column.get("uniqueness_rate") or 0.0),
            "is_unique_full_table": column.get("is_unique_full_table"),
            # An aggregate snapshot carries no per-row detail; say so with
            # nulls rather than inventing zeroes the agent would treat as facts.
            "quantiles": column.get("quantiles"),
            "negative_rate": column.get("negative_rate"),
            "out_of_domain_rate": column.get("out_of_domain_rate"),
            "sample_value": column.get("sample_value"),
            "min_value": column.get("min_value"),
            "max_value": column.get("max_value"),
        })
    row_count = int(snapshot.row_count or 0)
    duplicate_rate = float(snapshot.duplicate_rate or metrics.get("duplicate_rate") or 0.0)
    evidence_keys = [
        "profile.row_count",
        "profile.completeness_score",
        "profile.validity_score",
        "profile.duplicate_rate",
    ]
    evidence_keys.extend(f"profile.column.{column['name']}.null_rate" for column in columns)
    completed = snapshot.completed_at or snapshot.created_at
    return {
        "dataset_id": snapshot.dataset_id,
        "row_count": row_count,
        "column_count": len(columns),
        "duplicate_rate": duplicate_rate,
        "duplicate_count": round(row_count * duplicate_rate / 100),
        "completeness_score": float(snapshot.completeness_score or metrics.get("completeness_score") or 0.0),
        "validity_score": float(snapshot.validity_score or metrics.get("validity_score") or 0.0),
        "evidence_keys": evidence_keys,
        "profile_generated_at": completed.isoformat() if completed else None,
        "columns": columns,
    }


def _profile_snapshot(db: Session, dataset_id: str) -> dict[str, Any]:
    profile = db.get(ProfileModel, dataset_id)
    if not profile:
        versioned = _versioned_profile_snapshot_row(db, dataset_id)
        if versioned:
            snapshot = _snapshot_from_versioned_profile(versioned)
            if not snapshot["columns"]:
                raise WorkflowError("The completed profile has no column profiles.")
            return snapshot
        raise WorkflowError("A completed profile is required before understanding data.")
    columns = db.query(ColumnProfileModel).filter_by(profile_dataset_id=dataset_id).all()
    if not columns:
        raise WorkflowError("The completed profile has no column profiles.")
    return {
        "dataset_id": dataset_id,
        "row_count": profile.row_count,
        "column_count": len(columns),
        "duplicate_rate": profile.duplicate_rate,
        "duplicate_count": round(profile.row_count * float(profile.duplicate_rate) / 100),
        "completeness_score": profile.completeness_score,
        "validity_score": profile.validity_score,
        "evidence_keys": json.loads(profile.evidence_keys or "[]"),
        "profile_generated_at": profile.generated_at.isoformat(),
        "columns": [
            {
                "name": column.name,
                "data_type": column.data_type,
                "null_rate": column.null_rate,
                "null_percentage": float(column.null_rate) * 100,
                "distinct_count": column.distinct_count,
                "full_distinct_count": column.full_distinct_count,
                "non_null_count": column.non_null_count,
                "uniqueness_rate": column.uniqueness_rate,
                "is_unique_full_table": column.is_unique_full_table,
                "quantiles": json.loads(column.quantiles_json) if column.quantiles_json else None,
                "negative_rate": column.negative_rate,
                "out_of_domain_rate": column.out_of_domain_rate,
                "sample_value": column.sample_value,
                "min_value": column.min_value,
                "max_value": column.max_value,
            }
            for column in columns
        ],
    }


def confirm_semantic_contract(db: Session, run: WorkflowRunModel, *, artifact_id: str, expected_version: int, contract: dict[str, Any], review_note: str | None = None) -> WorkflowArtifactModel:
    draft = db.get(WorkflowArtifactModel, artifact_id)
    current = _current_artifact(db, run.id, "UNDERSTAND_DATA", "SEMANTIC_CONTRACT")
    if not draft or draft.workflow_run_id != run.id or not current or current.id != draft.id or draft.version != expected_version or draft.stale:
        raise WorkflowError("The semantic contract version is no longer current.")
    if str(json.loads(draft.payload_json or "{}").get("status", "DRAFT")).upper() != "DRAFT":
        raise WorkflowError("Only the current draft semantic contract can be confirmed.")
    if not isinstance(contract, dict) or not (contract.get("columns") or contract.get("tables")):
        raise WorkflowError("The semantic contract must contain columns or tables.")
    profile = _current_artifact(db, run.id, "UNDERSTAND_DATA", "PROFILE_SNAPSHOT")
    dictionary = _current_artifact(db, run.id, "UNDERSTAND_DATA", "DATA_DICTIONARY")
    if not profile or not dictionary:
        raise WorkflowError("The source profile or data dictionary artifact is missing.")
    draft.stale, draft.status = True, "SUPERSEDED"
    payload = {**contract, "status": "CONFIRMED", "dataset_id": run.dataset_id, "source_profile_artifact_id": profile.id, "source_profile_version": profile.version, "source_dictionary_artifact_id": dictionary.id, "source_dictionary_version": dictionary.version, "review_note": review_note, "confirmed_at": utc_now().isoformat()}
    confirmed = _add_artifact(db, run, "UNDERSTAND_DATA", "SEMANTIC_CONTRACT", payload, status="CONFIRMED")
    steps = _decode_steps(run)
    _step(steps, "PROPOSE_RULES")["status"] = "READY"
    run.current_step = "PROPOSE_RULES"
    run.revision += 1
    _encode_steps(run, steps)
    return confirmed


def get_or_create_run(db: Session, dataset: DatasetModel, *, force_new: bool = False) -> WorkflowRunModel:
    run = (
        None
        if force_new
        else (
            db.query(WorkflowRunModel)
            .filter(WorkflowRunModel.dataset_id == dataset.id, WorkflowRunModel.status == "ACTIVE")
            .order_by(WorkflowRunModel.updated_at.desc())
            .first()
        )
    )
    if run:
        # Profiling is performed by the ingestion endpoint.  Once it finishes,
        # reconcile an already-created run instead of leaving its first stage
        # permanently stuck at UPLOAD_PROFILE.
        if (
            run.current_step == "UPLOAD_PROFILE"
            and dataset.status == "PROFILE_READY"
            and _has_completed_profile(db, dataset.id)
        ):
            steps = _decode_steps(run)
            _complete(steps, "UPLOAD_PROFILE")
            _step(steps, "UNDERSTAND_DATA")["status"] = "READY"
            run.current_step = "UNDERSTAND_DATA"
            _encode_steps(run, steps)
        return run
    profile_ready = dataset.status == "PROFILE_READY" and _has_completed_profile(db, dataset.id)
    run = WorkflowRunModel(
        id=f"workflow-{uuid.uuid4().hex[:20]}",
        dataset_id=dataset.id,
        current_step="UNDERSTAND_DATA" if profile_ready else "UPLOAD_PROFILE",
        steps_json=json.dumps(_steps(profile_ready)),
    )
    db.add(run)
    db.flush()
    return run


def _mark_downstream_stale(db: Session, run: WorkflowRunModel, from_key: str) -> None:
    start = STEP_KEYS.index(from_key)
    for artifact in db.query(WorkflowArtifactModel).filter_by(workflow_run_id=run.id).all():
        if STEP_KEYS.index(artifact.step_key) > start:
            artifact.stale = True
    if start < STEP_KEYS.index("PUBLISH_RULESET"):
        db.query(RulesetVersionModel).filter_by(workflow_run_id=run.id).update({"stale": True})
    if start < STEP_KEYS.index("RUN_CHECKS"):
        db.query(DqRunModel).filter_by(workflow_run_id=run.id).update({"stale": True})
    steps = _decode_steps(run)
    for item in steps[start + 1 :]:
        item["status"] = "LOCKED"
    _encode_steps(run, steps)


def _mark_stage_artifacts_stale(db: Session, run: WorkflowRunModel, step_key: str) -> None:
    for artifact in db.query(WorkflowArtifactModel).filter_by(workflow_run_id=run.id, step_key=step_key, stale=False):
        artifact.stale = True


def _add_artifact(
    db: Session,
    run: WorkflowRunModel,
    step_key: str,
    artifact_type: str,
    payload: dict[str, Any],
    status: str = "VALIDATED",
) -> WorkflowArtifactModel:
    previous = db.query(WorkflowArtifactModel).filter_by(workflow_run_id=run.id, step_key=step_key, artifact_type=artifact_type).count()
    dataset = db.get(DatasetModel, run.dataset_id)
    version = previous + 1
    payload = {
        **payload,
        "workflow_run_id": run.id,
        "dataset_id": run.dataset_id,
        "dataset_version_id": getattr(dataset, "manifest_version", None) or run.dataset_id,
        "step_key": step_key,
        "artifact_type": artifact_type,
        "artifact_version": version,
    }
    artifact = WorkflowArtifactModel(
        id=f"artifact-{uuid.uuid4().hex[:20]}",
        workflow_run_id=run.id,
        step_key=step_key,
        artifact_type=artifact_type,
        status=status,
        version=version,
        input_fingerprint=_fingerprint(payload),
        payload_json=json.dumps(payload, ensure_ascii=False),
    )
    db.add(artifact)
    steps = _decode_steps(run)
    item = _step(steps, step_key)
    item["artifact_ids"] = [*item.get("artifact_ids", []), artifact.id]
    _encode_steps(run, steps)
    return artifact


def _semantic_payload(db: Session, dataset_id: str) -> dict[str, Any]:
    """Deterministic contract built from whichever profile the dataset has.

    Reads the unified snapshot rather than ``ColumnProfileModel`` directly: a
    versioned import has no rows in that table, and querying it straight meant
    Graph 1A refused to start for every dataset uploaded through the canonical
    import path.
    """
    snapshot = _profile_snapshot(db, dataset_id)
    semantic_columns = []
    for column in snapshot["columns"]:
        name = str(column.get("name") or "")
        data_type = str(column.get("data_type") or "").lower()
        semantic_type = (
            "event_time"
            if "time" in data_type or "date" in name.lower()
            else "measure"
            if data_type in {"numeric", "float", "integer", "number", "int", "double", "decimal"}
            else "identifier"
            if name.endswith("_id")
            else "category"
        )
        semantic_columns.append(
            {
                "name": name,
                "data_type": column.get("data_type"),
                "semantic_type": semantic_type,
                "confidence": 0.9,
                "null_rate": column.get("null_rate"),
                "distinct_count": column.get("distinct_count"),
                "full_distinct_count": column.get("full_distinct_count"),
                "is_unique_full_table": column.get("is_unique_full_table"),
                "negative_rate": column.get("negative_rate"),
                "quantiles": column.get("quantiles"),
                "sample_value": column.get("sample_value"),
                "range": [column.get("min_value"), column.get("max_value")],
            }
        )
    return {
        "summary": "Profile-backed semantic contract. Review the inferred roles before requesting rules.",
        "rows": snapshot["row_count"],
        "completeness_score": snapshot["completeness_score"],
        "validity_score": snapshot["validity_score"],
        "duplicate_rate": snapshot["duplicate_rate"],
        "columns": semantic_columns,
        "evidence": snapshot["evidence_keys"],
    }


def _raw_profile_for_graph(db: Session, dataset_id: str, fallback: dict[str, Any]) -> dict[str, Any]:
    """Rebuild a raw-profile document from the persisted aggregate profile.

    Graph 1A begins at ``build_profile_digest``, which expects the shape the
    database-wide profiler emits (``table_metadata`` + ``columns``) rather than
    the flattened contract shape this module works in.  Reconstructing it here
    lets the wizard drive the documented three-node graph instead of calling the
    understanding node on its own.

    Only aggregate statistics are read -- counts, rates, quantile bounds.  No
    source row ever enters this document, which is what keeps the digest handed
    to the LLM free of real values.
    """
    # Everything needed is already on the contract columns, which are built from
    # the unified snapshot. Re-reading ColumnProfileModel here would reintroduce
    # the legacy-only assumption this function is downstream of.
    total_rows = int(fallback["rows"] or 0)

    columns: dict[str, Any] = {}
    for column in fallback["columns"]:
        name = column["name"]
        entry: dict[str, Any] = {
            "type": column.get("data_type") or "unknown",
            "null_pct": float(column["null_rate"] or 0.0),
            "distinct_in_sample": int(column["distinct_count"] or 0),
        }
        value_range = column.get("range")
        if isinstance(value_range, list | tuple) and len(value_range) == 2:
            entry["min"], entry["max"] = value_range[0], value_range[1]
        if column.get("full_distinct_count") is not None:
            entry["distinct_full_table"] = column["full_distinct_count"]
        if column.get("is_unique_full_table") is not None:
            entry["is_unique_full_table"] = column["is_unique_full_table"]
        if column.get("negative_rate") is not None:
            entry["negative_pct"] = column["negative_rate"]
        if column.get("quantiles"):
            entry["percentiles"] = column["quantiles"]
        # The contract's own semantic guess is the best categorical signal here;
        # the raw profiler's cardinality measurement is not persisted.
        if column.get("semantic_type") == "category":
            entry["is_categorical"] = True
        columns[name] = entry

    return {
        dataset_id: {
            "table_metadata": {
                "table_name": dataset_id,
                "total_rows": total_rows,
                "sampled_rows": total_rows,
                "sampling_rate": 1.0,
                "is_sampled": False,
            },
            "columns": columns,
        }
    }


def _agent_semantic_payload(db: Session, dataset_id: str, *, workflow_run_id: str | None = None) -> dict[str, Any]:
    """Run Graph 1A over aggregate profile evidence.

    No source rows are exposed to the model: the agent receives names, types,
    aggregate counts/rates and bounded value/range metadata only.
    """
    fallback = _semantic_payload(db, dataset_id)
    if get_settings().agent_mode != "graph":
        fallback["summary"] = "Deterministic profile contract (agent mode is disabled)."
        fallback["agent_mode"] = "deterministic-fallback"
        return fallback

    from src.agents.graph import build_understanding_graph

    async def _invoke() -> dict[str, Any]:
        start_graph_run(workflow_run_id=workflow_run_id, dataset_id=dataset_id)
        graph = build_understanding_graph()
        return await asyncio.wait_for(
            graph.ainvoke(
                {
                    "dataset_id": dataset_id,
                    "dataset_profile": _raw_profile_for_graph(db, dataset_id, fallback),
                    "target_tables": [dataset_id],
                    "metadata": {"domain_hint": "NYC Yellow Taxi trip operations"},
                }
            ),
            timeout=UNDERSTANDING_GRAPH_TIMEOUT_SECONDS,
        )

    try:
        result = asyncio.run(_invoke())
    except Exception:
        # A timeout or transport failure is the same outcome as a node error:
        # no contract.  Treat it the same way rather than letting it escape.
        result = {"error": "understanding_agent_unavailable"}
    if result.get("error") or not result.get("semantic_contract", {}).get("tables"):
        # An unreachable model provider must not strand the workflow: the
        # deterministic profile already carries enough evidence for a steward to
        # review, so degrade to it and say so rather than failing the stage.
        logger.warning(
            "Graph 1A did not return a contract for %s (%s); using the deterministic profile.",
            dataset_id,
            result.get("error", "no semantic contract returned"),
        )
        fallback["summary"] = (
            "Profile-backed semantic contract. The language-model enrichment "
            "was unavailable, so only deterministic aggregate evidence is shown."
        )
        fallback["agent_mode"] = "deterministic-fallback"
        fallback["fallback_reason"] = "agent_provider_unavailable"
        return fallback
    contract = next(iter(result["semantic_contract"]["tables"].values()))
    return {
        **fallback,
        "summary": contract.get("table_purpose") or "Agent-generated semantic contract.",
        "columns": contract.get("columns", []),
        "relationships": contract.get("relationships", []),
        "assumptions": contract.get("business_assumptions", []),
        "agent_mode": "graph-1a-dataset-understanding",
    }


def _publish_ruleset(db: Session, run: WorkflowRunModel) -> None:
    rules = (
        db.query(RuleProposalModel)
        .filter_by(workflow_run_id=run.id)
        .filter(RuleProposalModel.status == "APPROVED")
        .all()
    )
    versions = (
        (
            db.query(RuleVersionModel)
            .filter(
                RuleVersionModel.rule_proposal_id.in_([rule.id for rule in rules]),
                RuleVersionModel.status == "APPROVED",
            )
            .all()
        )
        if rules
        else []
    )
    if not rules or len(versions) != len(rules):
        raise WorkflowError("Approved rules are missing executable versions.")
    normalized = [
        {"rule_version_id": item.id, "proposal_id": item.rule_proposal_id, "rule_spec": json.loads(item.rule_spec)}
        for item in versions
    ]
    ruleset = RulesetVersionModel(
        id=f"ruleset-{uuid.uuid4().hex[:20]}",
        dataset_id=run.dataset_id,
        workflow_run_id=run.id,
        ruleset_hash=_fingerprint(normalized),
        normalized_rules=json.dumps(normalized, sort_keys=True),
        created_by="steward-workflow",
    )
    db.add(ruleset)
    _add_artifact(
        db,
        run,
        "PUBLISH_RULESET",
        "PUBLISHED_RULESET",
        {
            "ruleset_id": ruleset.id,
            "ruleset_hash": ruleset.ruleset_hash,
            "rule_version_ids": [item["rule_version_id"] for item in normalized],
            "rule_count": len(normalized),
        },
        status="APPROVED",
    )


def execute_step(db: Session, run: WorkflowRunModel, step_key: str) -> None:
    if step_key not in STEP_KEYS:
        raise WorkflowError("This step is not part of the Rule Proposer workflow.")
    if step_key == "RUN_CHECKS":
        raise WorkflowError("Run checks is queued through the execution worker.")
    if step_key == "ANALYZE_REPORT":
        raise WorkflowError("Analysis starts automatically after checks finish.")
    steps = _decode_steps(run)
    step_status = _step(steps, step_key)["status"]
    # Re-running a stage that has already produced something is regeneration and
    # is allowed wherever the cursor happens to sit; jumping to a stage that has
    # never run is not. Requiring `current_step == step_key` outright blocked
    # regeneration, because confirming the contract leaves the cursor behind the
    # stage whose output is being replaced.
    # RUNNING is included because the route flips the step to RUNNING just before
    # dispatching this job, so by the time we get here the status that authorised
    # the run has already been overwritten. The route is the gate that refuses a
    # LOCKED step, so anything reaching this point was authorised there.
    already_ran = step_status in {"COMPLETED", "WAITING_APPROVAL", "FAILED", "RUNNING"}
    if run.current_step != step_key and not already_ran:
        raise WorkflowError("Complete the current workflow step before continuing.")
    if step_status not in {"READY", "FAILED", "COMPLETED", "RUNNING", "WAITING_APPROVAL"}:
        raise WorkflowError("This workflow step is not ready to run.")
    if step_key == "UPLOAD_PROFILE":
        raise WorkflowError(
            "Upload/profile runs through the dataset ingestion endpoint. Refresh this workflow when profiling completes."
        )
    if step_key == "UNDERSTAND_DATA":
        snapshot_payload = _profile_snapshot(db, run.dataset_id)
        semantic = _agent_semantic_payload(db, run.dataset_id, workflow_run_id=run.id)
        _mark_downstream_stale(db, run, step_key)
        _mark_stage_artifacts_stale(db, run, step_key)
        snapshot = _add_artifact(db, run, step_key, "PROFILE_SNAPSHOT", snapshot_payload)
        dictionary = _add_artifact(db, run, step_key, "DATA_DICTIONARY", {"tables": {run.dataset_id: _dictionary_snapshot(semantic)}, "inferred": True}, status="DRAFT")
        _add_artifact(db, run, step_key, "SEMANTIC_CONTRACT", {**semantic, "status": "DRAFT", "source_profile_artifact_id": snapshot.id, "source_dictionary_artifact_id": dictionary.id}, status="DRAFT")
        next_key = "PROPOSE_RULES"
    elif step_key == "PROPOSE_RULES":
        contract_artifact = _current_artifact(db, run.id, "UNDERSTAND_DATA", "SEMANTIC_CONTRACT")
        profile_artifact = _current_artifact(db, run.id, "UNDERSTAND_DATA", "PROFILE_SNAPSHOT")
        dictionary_artifact = _current_artifact(db, run.id, "UNDERSTAND_DATA", "DATA_DICTIONARY")
        if not contract_artifact or not profile_artifact or not dictionary_artifact:
            raise WorkflowError("Current understanding artifacts are missing or stale.")
        contract = json.loads(contract_artifact.payload_json or "{}")
        if contract_artifact.status != "CONFIRMED" or str(contract.get("status", "")).upper() != "CONFIRMED":
            raise WorkflowError("Confirm the current semantic contract before proposing rules.")
        if contract.get("source_profile_artifact_id") != profile_artifact.id or contract.get("source_dictionary_artifact_id") != dictionary_artifact.id:
            raise WorkflowError("The confirmed contract references stale understanding artifacts.")
        # Graph 1B is the documented path (candidates ➔ prompt ➔ proposer).  The
        # single-node shortcut stays as a fallback so a Graph 1B failure degrades
        # to the previous behaviour instead of blocking the steward.
        try:
            proposals = generate_rule_proposals_via_graph_1b(
                db, run.dataset_id, contract, workflow_run_id=run.id
            )
        except Exception as exc:
            logger.warning(
                "Graph 1B failed for workflow %s, falling back to the single-node proposer: %s", run.id, exc
            )
            proposals = generate_dashboard_proposals(db, run.dataset_id, contract)
        if not proposals:
            raise WorkflowError("No usable rule proposals were produced.")
        _mark_downstream_stale(db, run, step_key)
        _mark_stage_artifacts_stale(db, run, step_key)
        for old in (
            db.query(RuleProposalModel)
            .filter_by(workflow_run_id=run.id)
            .filter(RuleProposalModel.status.in_(["PROPOSED", "EDITED", "REJECTED"]))
            .all()
        ):
            old.status = "STALE"
        proposal_ids = []
        for proposal in proposals:
            proposal_id = f"wf-{run.id[-8:]}-{uuid.uuid4().hex[:20]}"
            db.add(
                RuleProposalModel(
                    id=proposal_id,
                    dataset_id=run.dataset_id,
                    workflow_run_id=run.id,
                    title=proposal.title,
                    description=proposal.description,
                    severity=proposal.severity,
                    status="PROPOSED",
                    rule_type=proposal.rule_type,
                    rule_spec=json.dumps(proposal.rule_spec),
                    evidence_refs=json.dumps(proposal.evidence_refs),
                    evidence_summary=proposal.evidence_summary,
                    confidence=proposal.confidence,
                    rule_name=proposal.rule_name,
                    business_rationale=proposal.business_rationale,
                    proposal_basis=proposal.proposal_basis,
                    evidence=json.dumps(proposal.evidence),
                    parameter_provenance="[]",
                    assumptions="[]",
                    confidence_breakdown=json.dumps(proposal.confidence_breakdown),
                    model_name=proposal.model_name,
                )
            )
            proposal_ids.append(proposal_id)
        rule_set = _add_artifact(
            db,
            run,
            step_key,
            "RULE_SET",
            {"proposal_ids": proposal_ids, "proposal_count": len(proposal_ids), "source_semantic_contract_artifact_id": contract_artifact.id, "source_profile_artifact_id": profile_artifact.id, "source_dictionary_artifact_id": dictionary_artifact.id, "generated_at": utc_now().isoformat()},
            status="DRAFT",
        )
        next_key = "REVIEW_RULES"
    elif step_key == "PUBLISH_RULESET":
        _mark_downstream_stale(db, run, step_key)
        _mark_stage_artifacts_stale(db, run, step_key)
        _publish_ruleset(db, run)
        next_key = "RUN_CHECKS"
    else:
        raise WorkflowError("Rules are confirmed by the steward review action.")
    steps = _decode_steps(run)
    _complete(steps, step_key)
    following = _step(steps, next_key)
    following["status"] = "WAITING_APPROVAL" if next_key in {"PROPOSE_RULES", "REVIEW_RULES"} else "READY"
    if step_key == "PROPOSE_RULES":
        following["artifact_ids"] = [*following.get("artifact_ids", []), rule_set.id]
    # Understanding is a steward-visible checkpoint: preserve the semantic
    # contract on screen and require an explicit Continue action before rule
    # generation.  Other transitions remain automatic once their work ends.
    run.current_step = step_key if step_key == "UNDERSTAND_DATA" else next_key
    run.revision += 1
    _encode_steps(run, steps)


def run_workflow_stage_job(workflow_run_id: str, step_key: str, job_id: str) -> None:
    """Execute a durable Graph 1 stage outside the HTTP request lifecycle."""
    with Session(get_engine()) as db:
        run = db.get(WorkflowRunModel, workflow_run_id)
        job = db.get(JobModel, job_id)
        if not run or not job:
            return
        try:
            job.status = "RUNNING"
            job.progress = 20.0
            job.message = f"Running {step_key}"
            db.commit()
            execute_step(db, run, step_key)
            job.status = "SUCCEEDED"
            job.progress = 100.0
            job.message = "Completed"
            db.commit()
        except Exception as exc:
            db.rollback()
            run = db.get(WorkflowRunModel, workflow_run_id)
            job = db.get(JobModel, job_id)
            if run:
                steps = _decode_steps(run)
                _step(steps, step_key)["status"] = "FAILED"
                run.status = "ACTIVE"
                _encode_steps(run, steps)
            if job:
                job.status = "FAILED"
                job.progress = 100.0
                job.message = "Workflow stage failed"
                job.error = str(exc)[:2000]
            db.commit()


def queue_check_run(db: Session, run: WorkflowRunModel, job: JobModel) -> DqRunModel:
    if run.current_step != "RUN_CHECKS":
        raise WorkflowError("The workflow is not ready to run quality checks.")
    ruleset = (
        db.query(RulesetVersionModel)
        .filter_by(workflow_run_id=run.id, stale=False)
        .order_by(RulesetVersionModel.created_at.desc())
        .first()
    )
    if not ruleset:
        raise WorkflowError("Publish the current approved rule set before running checks.")
    rule_ids = [item["rule_version_id"] for item in json.loads(ruleset.normalized_rules)]
    if not rule_ids:
        raise WorkflowError("The published ruleset has no executable rules.")
    dq_run = DqRunModel(
        id=f"run_{uuid.uuid4().hex[:8]}",
        job_id=job.id,
        dataset_id=run.dataset_id,
        workflow_run_id=run.id,
        ruleset_version_id=ruleset.id,
        rule_ids=json.dumps(rule_ids),
        status="PENDING",
        total_failed=0,
        total_checked=0,
        created_at=utc_now(),
    )
    db.add(dq_run)
    steps = _decode_steps(run)
    _step(steps, "RUN_CHECKS")["status"] = "RUNNING"
    _encode_steps(run, steps)
    job.type, job.linked_entity, job.message, job.progress = "RUN_DQ", dq_run.id, "Queued deterministic checks", 0.0
    return dq_run


def run_checks_and_prepare_analysis(
    workflow_run_id: str, dq_run_id: str, job_id: str, session_id: str | None, actor_role: str
) -> None:
    """Run Graph 2 and create its visible result before Graph 3 is requested."""
    from src.services.job_runner import run_dq_checks

    run_dq_checks(job_id, dq_run_id, session_id, actor_role, trigger_anomaly=False, finalize_job=False)
    with Session(get_engine()) as db:
        dq_run, run = db.get(DqRunModel, dq_run_id), db.get(WorkflowRunModel, workflow_run_id)
        if not dq_run or not run:
            return
        if dq_run.status != "SUCCEEDED":
            steps = _decode_steps(run)
            _step(steps, "RUN_CHECKS")["status"] = "FAILED"
            run.status = "FAILED"
            _encode_steps(run, steps)
            db.commit()
            return
        rows = db.query(DqResultModel).filter_by(run_id=dq_run.id).all()
        total_checked = dq_run.total_checked
        total_failed = dq_run.total_failed
        dq_score = round((1.0 - (total_failed / total_checked if total_checked > 0 else 0.0)) * 100, 1)
        dq_grade = "A" if dq_score >= 90 else ("B" if dq_score >= 80 else ("C" if dq_score >= 70 else "D"))
        _add_artifact(db, run, "RUN_CHECKS", "DQ_RUN", {
            "run_id": dq_run.id, "ruleset_version_id": dq_run.ruleset_version_id, "status": dq_run.status,
            "total_checked": total_checked, "total_failed": total_failed,
            "dq_score": dq_score, "dq_grade": dq_grade,
            "results": [{"rule_id": row.rule_id, "title": row.rule_title, "status": row.status,
                         "checked_count": row.checked_count, "failed_count": row.failed_count} for row in rows],
        })
        steps = _decode_steps(run)
        _complete(steps, "RUN_CHECKS")
        _step(steps, "ANALYZE_REPORT")["status"] = "READY"
        run.current_step = "ANALYZE_REPORT"
        run.revision += 1
        _encode_steps(run, steps)
        job = db.get(JobModel, job_id)
        if job:
            job.status, job.progress, job.message = (
                "SUCCEEDED",
                100.0,
                "Checks completed; Graph 3 is ready for steward review",
            )
        db.commit()


def run_analysis_report(workflow_run_id: str, job_id: str, session_id: str | None, actor_role: str) -> None:
    """Run Graph 3 only after the steward explicitly starts the analysis mini-step."""
    from src.agents.graph import run_anomaly_graph

    dataset_id: str | None = None
    dq_run_id: str | None = None
    with Session(get_engine()) as db:
        run = db.get(WorkflowRunModel, workflow_run_id)
        if not run or run.current_step != "ANALYZE_REPORT":
            return
        steps = _decode_steps(run)
        if _step(steps, "ANALYZE_REPORT")["status"] not in {"READY", "FAILED"}:
            return
        dq_run = (
            db.query(DqRunModel)
            .filter_by(workflow_run_id=run.id, stale=False)
            .filter(DqRunModel.status == "SUCCEEDED")
            .order_by(DqRunModel.created_at.desc())
            .first()
        )
        if not dq_run:
            raise WorkflowError("A completed Graph 2 run is required before Graph 3 analysis.")
        dataset_id, dq_run_id = run.dataset_id, dq_run.id
        _step(steps, "ANALYZE_REPORT")["status"] = "RUNNING"
        _encode_steps(run, steps)
        db.commit()

    try:
        asyncio.run(
            asyncio.wait_for(
                run_anomaly_graph(execution_run_id=dq_run_id, dataset_id=dataset_id, stream_id=workflow_run_id),
                timeout=90,
            )
        )
        analysis_error = None
    except Exception as exc:  # the DQ result remains valuable even when analysis fails
        analysis_error = str(exc)
    with Session(get_engine()) as db:
        run = db.get(WorkflowRunModel, workflow_run_id)
        job = db.get(JobModel, job_id)
        if not run:
            return
        anomaly = (
            db.query(AnomalyRunModel)
            .filter_by(execution_run_id=dq_run_id)
            .order_by(AnomalyRunModel.created_at.desc())
            .first()
        )
        hypotheses = db.query(AnomalyHypothesisModel).filter_by(anomaly_run_id=anomaly.id).all() if anomaly else []
        _add_artifact(
            db,
            run,
            "ANALYZE_REPORT",
            "ANOMALY_REPORT",
            {
                "execution_run_id": dq_run_id,
                "status": anomaly.status if anomaly else "FAILED",
                "decision": anomaly.decision if anomaly else "UNAVAILABLE",
                "score": anomaly.score if anomaly else 0.0,
                "confidence": anomaly.confidence if anomaly else 0.0,
                "error": analysis_error or (anomaly.error_message if anomaly else "Analysis was not persisted."),
                "hypotheses": [
                    {
                        "summary": item.summary,
                        "confidence": item.confidence,
                        "recommended_checks": json.loads(item.recommended_checks),
                    }
                    for item in hypotheses
                ],
            },
            status="APPROVED" if anomaly and anomaly.status == "SUCCEEDED" else "VALIDATED",
        )
        steps = _decode_steps(run)
        _complete(steps, "ANALYZE_REPORT")
        run.status = "COMPLETED"
        run.revision += 1
        _encode_steps(run, steps)
        if job:
            job.status = "SUCCEEDED"
            job.progress = 100.0
            job.message = "Analysis report completed" if not analysis_error else "Analysis report recorded an error"
        db.commit()


def run_checks_and_analyze(
    workflow_run_id: str, dq_run_id: str, job_id: str, session_id: str | None, actor_role: str
) -> None:
    """Compatibility helper for legacy callers that expect Graph 2 and 3 together."""
    run_checks_and_prepare_analysis(workflow_run_id, dq_run_id, job_id, session_id, actor_role)
    run_analysis_report(workflow_run_id, job_id, session_id, actor_role)


def rewind(db: Session, run: WorkflowRunModel, target_step: str) -> None:
    if target_step not in STEP_KEYS:
        raise WorkflowError("Unknown workflow step.")
    if _step(_decode_steps(run), target_step)["status"] == "LOCKED":
        raise WorkflowError("This stage has not been reached yet.")
    run.current_step = target_step


def navigate_forward(run: WorkflowRunModel) -> None:
    index = STEP_KEYS.index(run.current_step)
    if index >= len(STEP_KEYS) - 1:
        raise WorkflowError("There is no later workflow stage.")
    target = _step(_decode_steps(run), STEP_KEYS[index + 1])
    if target["status"] != "READY":
        raise WorkflowError("The next stage is not ready. Confirm the required artifact first.")
    run.current_step = target["key"]


def complete_rule_review(db: Session, run: WorkflowRunModel) -> WorkflowArtifactModel:
    if run.current_step != "REVIEW_RULES":
        raise WorkflowError("The workflow is not waiting for rule review.")
    rules = (
        db.query(RuleProposalModel).filter_by(workflow_run_id=run.id).filter(RuleProposalModel.status != "STALE").all()
    )
    if not rules or any(rule.status in {"PROPOSED", "EDITED"} for rule in rules):
        raise WorkflowError("Decide every current rule before continuing.")
    if not any(rule.status == "APPROVED" for rule in rules):
        raise WorkflowError("Keep at least one approved rule before continuing.")
    artifact = (
        db.query(WorkflowArtifactModel)
        .filter_by(workflow_run_id=run.id, step_key="PROPOSE_RULES", artifact_type="RULE_SET", stale=False)
        .order_by(WorkflowArtifactModel.created_at.desc())
        .first()
    )
    if not artifact:
        raise WorkflowError("The current rule set artifact is unavailable.")
    artifact.status = "APPROVED"
    steps = _decode_steps(run)
    _complete(steps, "REVIEW_RULES")
    _step(steps, "PUBLISH_RULESET")["status"] = "READY"
    run.current_step = "PUBLISH_RULESET"
    run.revision += 1
    _encode_steps(run, steps)
    return artifact
