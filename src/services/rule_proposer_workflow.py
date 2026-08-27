"""Durable transitions for the steward-owned rule workflow.

Proposal/review, publishing, safe typed-rule execution, and analysis are
separate stages.  Agents never produce executable SQL: the runner only accepts
approved typed rule versions.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from typing import Any

from sqlalchemy.orm import Session

from src.config import get_settings
from src.models.database import (
    AnomalyHypothesisModel,
    AnomalyRunModel,
    ColumnProfileModel,
    DatasetModel,
    DqResultModel,
    DqRunModel,
    JobModel,
    ProfileModel,
    RuleProposalModel,
    RulesetVersionModel,
    RuleVersionModel,
    WorkflowArtifactModel,
    WorkflowRunModel,
)
from src.services.dashboard_agent_workflow import generate_dashboard_proposals
from src.services.rule_store import get_engine
from src.time_utils import utc_now

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


def _profile_snapshot(db: Session, dataset_id: str) -> dict[str, Any]:
    profile = db.get(ProfileModel, dataset_id)
    if not profile:
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
            and db.get(ProfileModel, dataset.id)
        ):
            steps = _decode_steps(run)
            _complete(steps, "UPLOAD_PROFILE")
            _step(steps, "UNDERSTAND_DATA")["status"] = "READY"
            run.current_step = "UNDERSTAND_DATA"
            _encode_steps(run, steps)
        return run
    profile_ready = dataset.status == "PROFILE_READY" and db.get(ProfileModel, dataset.id) is not None
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
    profile = db.get(ProfileModel, dataset_id)
    if not profile:
        raise WorkflowError("A completed profile is required before understanding data.")
    columns = db.query(ColumnProfileModel).filter_by(profile_dataset_id=dataset_id).all()
    semantic_columns = []
    for column in columns:
        data_type = column.data_type.lower()
        semantic_type = (
            "event_time"
            if "time" in data_type or "date" in column.name.lower()
            else "measure"
            if data_type in {"numeric", "float", "integer", "number"}
            else "identifier"
            if column.name.endswith("_id")
            else "category"
        )
        semantic_columns.append(
            {
                "name": column.name,
                "semantic_type": semantic_type,
                "confidence": 0.9,
                "null_rate": column.null_rate,
                "distinct_count": column.distinct_count,
                "sample_value": column.sample_value,
                "range": [column.min_value, column.max_value],
            }
        )
    return {
        "summary": "Profile-backed semantic contract. Review the inferred roles before requesting rules.",
        "rows": profile.row_count,
        "completeness_score": profile.completeness_score,
        "validity_score": profile.validity_score,
        "duplicate_rate": profile.duplicate_rate,
        "columns": semantic_columns,
        "evidence": json.loads(profile.evidence_keys),
    }


def _agent_semantic_payload(db: Session, dataset_id: str) -> dict[str, Any]:
    """Use the Dataset Understanding Agent over aggregate profile evidence.

    No source rows are exposed to the model: the agent receives names, types,
    aggregate counts/rates and bounded value/range metadata only.
    """
    fallback = _semantic_payload(db, dataset_id)
    if get_settings().agent_mode != "graph":
        fallback["summary"] = "Deterministic profile contract (agent mode is disabled)."
        fallback["agent_mode"] = "deterministic-fallback"
        return fallback

    profiles_by_name = {
        item.name: item for item in db.query(ColumnProfileModel).filter_by(profile_dataset_id=dataset_id).all()
    }
    digest = {
        "dataset": {
            "table": dataset_id,
            "rows": fallback["rows"],
            "columns": [
                {
                    "name": column["name"],
                    "type": profiles_by_name.get(column["name"]).data_type
                    if column["name"] in profiles_by_name
                    else "unknown",
                    "role": column["semantic_type"],
                    "null_pct": round(float(column["null_rate"]) * 100, 4),
                    "range": column["range"],
                    "distinct_count": column["distinct_count"],
                }
                for column in fallback["columns"]
            ],
        }
    }
    from src.agents.nodes.data_dictionary_generator_node import data_dictionary_generator_node
    from src.agents.nodes.dataset_understanding_node import dataset_understanding_node

    dataset = db.get(DatasetModel, dataset_id)
    domain_hint = " ".join(
        part for part in (
            dataset.name if dataset else "",
            dataset.description if dataset else "",
            dataset.source_label if dataset else "",
        ) if part
    )[:800]

    async def run_understanding_stage() -> dict[str, Any]:
        state: dict[str, Any] = {
            "dataset_id": dataset_id,
            "dataset_profile_digest": digest,
            "normalized_data_dictionary": {},
            "metadata": {
                "domain_hint": domain_hint or "NYC Yellow Taxi trip operations",
                "workflow": "dashboard-graph-1-understanding",
            },
        }
        dictionary = await asyncio.wait_for(
            data_dictionary_generator_node(state), timeout=90
        )
        if dictionary.get("error"):
            return {"error": "data_dictionary_unavailable"}
        state.update(dictionary)
        return await asyncio.wait_for(dataset_understanding_node(state), timeout=90)

    try:
        result = asyncio.run(run_understanding_stage())
    except Exception:
        result = {"error": "understanding_agent_unavailable"}
    if result.get("error") or not result.get("semantic_contract", {}).get("tables"):
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
        "agent_mode": "openai-dataset-understanding-agent",
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
    if run.current_step != step_key:
        raise WorkflowError("Complete the current workflow step before continuing.")
    steps = _decode_steps(run)
    if _step(steps, step_key)["status"] not in {"READY", "FAILED", "COMPLETED", "RUNNING"}:
        raise WorkflowError("This workflow step is not ready to run.")
    if step_key == "UPLOAD_PROFILE":
        raise WorkflowError(
            "Upload/profile runs through the dataset ingestion endpoint. Refresh this workflow when profiling completes."
        )
    if step_key == "UNDERSTAND_DATA":
        snapshot_payload = _profile_snapshot(db, run.dataset_id)
        semantic = _agent_semantic_payload(db, run.dataset_id)
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
        _add_artifact(
            db,
            run,
            "RUN_CHECKS",
            "DQ_RUN",
            {
                "run_id": dq_run.id,
                "ruleset_version_id": dq_run.ruleset_version_id,
                "status": dq_run.status,
                "total_checked": dq_run.total_checked,
                "total_failed": dq_run.total_failed,
                "results": [
                    {
                        "rule_id": row.rule_id,
                        "title": row.rule_title,
                        "status": row.status,
                        "checked_count": row.checked_count,
                        "failed_count": row.failed_count,
                    }
                    for row in rows
                ],
            },
        )
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
                run_anomaly_graph(execution_run_id=dq_run_id, dataset_id=dataset_id),
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
