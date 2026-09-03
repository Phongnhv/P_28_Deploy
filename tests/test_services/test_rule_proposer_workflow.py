from __future__ import annotations

import json

import pytest
from sqlalchemy.orm import Session

from src.models.database import (
    ColumnProfileModel,
    DatasetModel,
    JobModel,
    ProfileModel,
    RuleProposalModel,
    RulesetVersionModel,
    RuleVersionModel,
    WorkflowArtifactModel,
    WorkflowRunModel,
)
from src.services.dashboard_agent_workflow import DashboardProposal
from src.services.rule_proposer_workflow import (
    WorkflowError,
    complete_rule_review,
    confirm_semantic_contract,
    execute_step,
    get_or_create_run,
    queue_check_run,
    rewind,
    run_analysis_report,
    run_checks_and_analyze,
    run_checks_and_prepare_analysis,
)
from src.services.rule_store import ActiveRuleModel, get_engine

DATASET_ID = "dataset-nyc-yellow-taxi-50k"


def _seed_profile() -> None:
    with Session(get_engine()) as db:
        dataset = db.get(DatasetModel, DATASET_ID)
        assert dataset
        dataset.status = "PROFILE_READY"
        db.add(
            ProfileModel(
                dataset_id=DATASET_ID,
                row_count=12,
                completeness_score=97.0,
                validity_score=94.0,
                duplicate_rate=0.5,
                cross_field_metrics_json="[]",
                evidence_keys=json.dumps(["profile.row_count"]),
            )
        )
        db.add_all(
            [
                ColumnProfileModel(
                    profile_dataset_id=DATASET_ID,
                    name="trip_id",
                    data_type="string",
                    null_rate=0,
                    distinct_count=12,
                    sample_value="redacted",
                ),
                ColumnProfileModel(
                    profile_dataset_id=DATASET_ID,
                    name="trip_distance",
                    data_type="numeric",
                    null_rate=0.1,
                    distinct_count=10,
                    min_value=-1,
                    max_value=42,
                    sample_value="redacted",
                ),
            ]
        )
        db.commit()


def _proposal() -> DashboardProposal:
    return DashboardProposal(
        id="distance-range",
        title="Trip distance is non-negative",
        description="Reject negative distances.",
        severity="HIGH",
        rule_type="numeric_range",
        rule_spec={"type": "numeric_range", "column": "trip_distance", "min_value": 0},
        evidence_refs=["profile.trip_distance.negative_rate"],
        evidence_summary="Negative values were observed.",
        confidence=0.9,
        model_name="agent-mock-v1",
        rule_name="Trip distance is non-negative",
        business_rationale="Negative distances distort totals.",
        proposal_basis="POLICY",
        evidence={"source_refs": ["profile.trip_distance.negative_rate"]},
        confidence_breakdown={"overall": 0.9},
        rule_description="Trip distance must be non-negative.",
        ai_reasoning="Aggregate negative-rate evidence supports this rule.",
        assumptions=["The source unit is miles."],
        parameter_provenance=[
            {
                "parameter_name": "min_value",
                "source_type": "DATA_PROFILE",
                "source_ref": "profile.trip_distance.negative_rate",
                "derivation_method": "server_policy",
            }
        ],
    )


def _confirm_current_contract(db: Session, run: WorkflowRunModel) -> None:
    draft = (
        db.query(WorkflowArtifactModel)
        .filter_by(
            workflow_run_id=run.id,
            step_key="UNDERSTAND_DATA",
            artifact_type="SEMANTIC_CONTRACT",
            stale=False,
        )
        .order_by(WorkflowArtifactModel.version.desc())
        .first()
    )
    assert draft is not None
    confirm_semantic_contract(
        db,
        run,
        artifact_id=draft.id,
        expected_version=draft.version,
        contract=json.loads(draft.payload_json),
    )


def test_back_navigation_preserves_artifacts_until_a_stage_is_rerun(monkeypatch):
    _seed_profile()
    # Graph 1B is the primary proposer; the single-node generator is only its
    # fallback. Stubbing the fallback alone let the real Graph 1B run and emit
    # its own candidate set, which is why these tests saw eight proposals where
    # they had asked for one.
    monkeypatch.setattr(
        "src.services.rule_proposer_workflow.generate_rule_proposals_via_graph_1b",
        lambda *_args, **_kwargs: [_proposal()],
    )
    monkeypatch.setattr("src.services.rule_proposer_workflow.generate_dashboard_policy_fallback_proposals", lambda *_: [_proposal()])
    with Session(get_engine()) as db:
        dataset = db.get(DatasetModel, DATASET_ID)
        run = get_or_create_run(db, dataset)
        execute_step(db, run, "UNDERSTAND_DATA")
        _confirm_current_contract(db, run)
        execute_step(db, run, "PROPOSE_RULES")
        stored_rule = db.query(RuleProposalModel).filter_by(workflow_run_id=run.id).one()
        assert json.loads(stored_rule.assumptions) == ["The source unit is miles."]
        assert json.loads(stored_rule.parameter_provenance)[0]["parameter_name"] == "min_value"
        rule_artifact = (
            db.query(WorkflowArtifactModel).filter_by(workflow_run_id=run.id, artifact_type="RULE_SET").one()
        )
        rewind(db, run, "UNDERSTAND_DATA")
        assert rule_artifact.stale is False
        execute_step(db, run, "UNDERSTAND_DATA")
        assert rule_artifact.stale is True


def test_understanding_requires_explicit_continue_before_rule_generation():
    _seed_profile()
    with Session(get_engine()) as db:
        dataset = db.get(DatasetModel, DATASET_ID)
        run = get_or_create_run(db, dataset)
        execute_step(db, run, "UNDERSTAND_DATA")
        assert run.current_step == "UNDERSTAND_DATA"
        assert (
            next(step for step in json.loads(run.steps_json) if step["key"] == "UNDERSTAND_DATA")["status"]
            == "COMPLETED"
        )
        assert next(step for step in json.loads(run.steps_json) if step["key"] == "PROPOSE_RULES")["status"] == "WAITING_APPROVAL"
        _confirm_current_contract(db, run)
        assert run.current_step == "PROPOSE_RULES"


def test_understanding_falls_back_when_agent_provider_is_unavailable(monkeypatch):
    _seed_profile()
    from src.config import get_settings

    async def unavailable_dictionary(_state):
        return {"error": "provider unavailable"}

    async def unavailable_understanding(_state):
        return {"error": "provider unavailable"}

    monkeypatch.setattr(get_settings(), "agent_mode", "graph")
    monkeypatch.setattr(
        "src.agents.nodes.data_dictionary_generator_node.data_dictionary_generator_node",
        unavailable_dictionary,
    )
    monkeypatch.setattr(
        "src.agents.nodes.dataset_understanding_node.dataset_understanding_node",
        unavailable_understanding,
    )
    with Session(get_engine()) as db:
        dataset = db.get(DatasetModel, DATASET_ID)
        run = get_or_create_run(db, dataset)
        execute_step(db, run, "UNDERSTAND_DATA")
        artifact = db.query(WorkflowArtifactModel).filter_by(
            workflow_run_id=run.id,
            artifact_type="SEMANTIC_CONTRACT",
        ).one()
        payload = json.loads(artifact.payload_json)
        assert payload["agent_mode"] == "deterministic-fallback"
        assert payload["fallback_reason"] == "agent_provider_unavailable"


def test_deleted_pending_rule_is_retained_as_stale_and_does_not_block_review(monkeypatch):
    _seed_profile()
    # Graph 1B is the primary proposer; the single-node generator is only its
    # fallback. Stubbing the fallback alone let the real Graph 1B run and emit
    # its own candidate set, which is why these tests saw eight proposals where
    # they had asked for one.
    monkeypatch.setattr(
        "src.services.rule_proposer_workflow.generate_rule_proposals_via_graph_1b",
        lambda *_args, **_kwargs: [_proposal()],
    )
    monkeypatch.setattr("src.services.rule_proposer_workflow.generate_dashboard_policy_fallback_proposals", lambda *_: [_proposal()])
    with Session(get_engine()) as db:
        dataset = db.get(DatasetModel, DATASET_ID)
        run = get_or_create_run(db, dataset)
        execute_step(db, run, "UNDERSTAND_DATA")
        _confirm_current_contract(db, run)
        execute_step(db, run, "PROPOSE_RULES")
        rule = db.query(RuleProposalModel).filter_by(workflow_run_id=run.id).one()
        assert len(rule.id) <= 36
        rule.status = "STALE"  # equivalent to a steward removal through the API
        with pytest.raises(WorkflowError):
            complete_rule_review(db, run)
        rule.status = "APPROVED"
        complete_rule_review(db, run)
        assert run.current_step == "PUBLISH_RULESET"
        assert run.status == "ACTIVE"


def test_publish_creates_immutable_ruleset_and_queues_only_approved_versions(monkeypatch):
    _seed_profile()
    # Graph 1B is the primary proposer; the single-node generator is only its
    # fallback. Stubbing the fallback alone let the real Graph 1B run and emit
    # its own candidate set, which is why these tests saw eight proposals where
    # they had asked for one.
    monkeypatch.setattr(
        "src.services.rule_proposer_workflow.generate_rule_proposals_via_graph_1b",
        lambda *_args, **_kwargs: [_proposal()],
    )
    monkeypatch.setattr("src.services.rule_proposer_workflow.generate_dashboard_policy_fallback_proposals", lambda *_: [_proposal()])
    with Session(get_engine()) as db:
        dataset = db.get(DatasetModel, DATASET_ID)
        run = get_or_create_run(db, dataset)
        execute_step(db, run, "UNDERSTAND_DATA")
        _confirm_current_contract(db, run)
        execute_step(db, run, "PROPOSE_RULES")
        rule = db.query(RuleProposalModel).filter_by(workflow_run_id=run.id).one()
        rule.status = "APPROVED"
        db.add(
            RuleVersionModel(
                id=f"rv_{rule.id}",
                rule_proposal_id=rule.id,
                dataset_id=rule.dataset_id,
                rule_spec=rule.rule_spec,
                status="APPROVED",
                version=1,
            )
        )
        complete_rule_review(db, run)
        execute_step(db, run, "PUBLISH_RULESET")
        ruleset = db.query(RulesetVersionModel).filter_by(workflow_run_id=run.id, stale=False).one()
        active_rule = db.get(ActiveRuleModel, rule.id)
        assert active_rule is not None
        assert active_rule.dataset_id == DATASET_ID
        assert active_rule.status == "ACTIVE"
        assert active_rule.table_name == DATASET_ID
        assert active_rule.column_name == "trip_distance"
        job = JobModel(
            id="workflow-dq-job",
            type="RUN_DQ",
            status="PENDING",
            progress=0,
            idempotency_key="workflow-dq-job",
            attempt_count=1,
        )
        db.add(job)
        queued = queue_check_run(db, run, job)
        assert run.current_step == "RUN_CHECKS"
        assert queued.ruleset_version_id == ruleset.id
        assert json.loads(queued.rule_ids) == [f"rv_{rule.id}"]


def test_mock_execution_and_analysis_complete_the_same_workflow(monkeypatch):
    _seed_profile()
    # Graph 1B is the primary proposer; the single-node generator is only its
    # fallback. Stubbing the fallback alone let the real Graph 1B run and emit
    # its own candidate set, which is why these tests saw eight proposals where
    # they had asked for one.
    monkeypatch.setattr(
        "src.services.rule_proposer_workflow.generate_rule_proposals_via_graph_1b",
        lambda *_args, **_kwargs: [_proposal()],
    )
    monkeypatch.setattr("src.services.rule_proposer_workflow.generate_dashboard_policy_fallback_proposals", lambda *_: [_proposal()])
    with Session(get_engine()) as db:
        dataset = db.get(DatasetModel, DATASET_ID)
        run = get_or_create_run(db, dataset)
        execute_step(db, run, "UNDERSTAND_DATA")
        _confirm_current_contract(db, run)
        execute_step(db, run, "PROPOSE_RULES")
        rule = db.query(RuleProposalModel).filter_by(workflow_run_id=run.id).one()
        rule.status = "APPROVED"
        db.add(
            RuleVersionModel(
                id=f"rv_{rule.id}",
                rule_proposal_id=rule.id,
                dataset_id=rule.dataset_id,
                rule_spec=rule.rule_spec,
                status="APPROVED",
                version=1,
            )
        )
        complete_rule_review(db, run)
        execute_step(db, run, "PUBLISH_RULESET")
        job = JobModel(
            id="workflow-mock-run-job",
            type="RUN_DQ",
            status="PENDING",
            progress=0,
            idempotency_key="workflow-mock-run-job",
            attempt_count=1,
        )
        db.add(job)
        dq_run = queue_check_run(db, run, job)
        run_id, dq_run_id, job_id = run.id, dq_run.id, job.id
        db.commit()
    run_checks_and_analyze(run_id, dq_run_id, job_id, None, "STEWARD")
    with Session(get_engine()) as db:
        run = db.get(WorkflowRunModel, run_id)
        assert run.status == "COMPLETED"
        assert {item.artifact_type for item in db.query(WorkflowArtifactModel).filter_by(workflow_run_id=run_id)} >= {
            "PUBLISHED_RULESET",
            "DQ_RUN",
            "ANOMALY_REPORT",
        }


def test_graph_2_result_is_visible_before_graph_3_is_started(monkeypatch):
    _seed_profile()
    # Graph 1B is the primary proposer; the single-node generator is only its
    # fallback. Stubbing the fallback alone let the real Graph 1B run and emit
    # its own candidate set, which is why these tests saw eight proposals where
    # they had asked for one.
    monkeypatch.setattr(
        "src.services.rule_proposer_workflow.generate_rule_proposals_via_graph_1b",
        lambda *_args, **_kwargs: [_proposal()],
    )
    monkeypatch.setattr("src.services.rule_proposer_workflow.generate_dashboard_policy_fallback_proposals", lambda *_: [_proposal()])

    async def no_op_analysis(**_kwargs):
        return {
            "steward_report_markdown": "# Báo cáo Graph 3\n\nNội dung do report_writer sinh ra.",
            "report_source": "LLM",
            "steward_report_path": "output/steward_reports/test.md",
        }

    monkeypatch.setattr("src.agents.graph.run_anomaly_graph", no_op_analysis)
    with Session(get_engine()) as db:
        dataset = db.get(DatasetModel, DATASET_ID)
        run = get_or_create_run(db, dataset)
        execute_step(db, run, "UNDERSTAND_DATA")
        _confirm_current_contract(db, run)
        execute_step(db, run, "PROPOSE_RULES")
        rule = db.query(RuleProposalModel).filter_by(workflow_run_id=run.id).one()
        rule.status = "APPROVED"
        db.add(
            RuleVersionModel(
                id=f"rv_{rule.id}",
                rule_proposal_id=rule.id,
                dataset_id=rule.dataset_id,
                rule_spec=rule.rule_spec,
                status="APPROVED",
                version=1,
            )
        )
        complete_rule_review(db, run)
        execute_step(db, run, "PUBLISH_RULESET")
        job = JobModel(
            id="workflow-split-run-job",
            type="RUN_DQ",
            status="PENDING",
            progress=0,
            idempotency_key="workflow-split-run-job",
            attempt_count=1,
        )
        db.add(job)
        dq_run = queue_check_run(db, run, job)
        run_id, dq_run_id, job_id = run.id, dq_run.id, job.id
        db.commit()
    run_checks_and_prepare_analysis(run_id, dq_run_id, job_id, None, "STEWARD")
    with Session(get_engine()) as db:
        run = db.get(WorkflowRunModel, run_id)
        steps = json.loads(run.steps_json)
        assert run.current_step == "ANALYZE_REPORT"
        assert next(step for step in steps if step["key"] == "RUN_CHECKS")["status"] == "COMPLETED"
        assert next(step for step in steps if step["key"] == "ANALYZE_REPORT")["status"] == "READY"
        assert db.query(WorkflowArtifactModel).filter_by(workflow_run_id=run_id, artifact_type="DQ_RUN").one()
        assert (
            not db.query(WorkflowArtifactModel)
            .filter_by(workflow_run_id=run_id, artifact_type="ANOMALY_REPORT")
            .count()
        )
    run_analysis_report(run_id, job_id, None, "STEWARD")
    with Session(get_engine()) as db:
        run = db.get(WorkflowRunModel, run_id)
        assert run.status == "COMPLETED"
        artifact = db.query(WorkflowArtifactModel).filter_by(workflow_run_id=run_id, artifact_type="ANOMALY_REPORT").one()
        payload = json.loads(artifact.payload_json)
        assert payload["report_markdown"].startswith("# Báo cáo Graph 3")
        assert payload["report_source"] == "LLM"
