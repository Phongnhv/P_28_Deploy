"""Each selected dataset keeps its own uploaded source throughout the pipeline."""
import json
from unittest.mock import AsyncMock

import pytest

from src.models.database import (
    DatasetVersionModel,
    DqRunModel,
    GovernedArtifactModel,
    ProfileModel,
    ProfileRunSnapshotModel,
)
from src.services.source_binding import workflow_binding
from tests.unit.test_source_binding import prepare
from tests.unit.test_source_binding import sources as source_fixture

sources = source_fixture


def test_missing_anomaly_case_never_uses_latest_execution(sources):
    from src.agents.tools.anomaly_investigation_tools import get_anomaly_case

    sources.add(DqRunModel(id="unrelated-latest-A", job_id="job-A", dataset_id="A",
                           rule_ids="[]", status="SUCCEEDED", total_checked=3, total_failed=0))
    sources.commit()
    result = get_anomaly_case.invoke({"anomaly_run_id": "missing-case-B"})
    assert result == {"error": "ANOMALY_RUN_NOT_FOUND", "anomaly_run_id": "missing-case-B"}


def test_inflight_anomaly_case_uses_current_detector_output(sources):
    from src.agents.tools.anomaly_investigation_tools import scoped_investigation_tools

    state = {
        "dataset_id": "B", "execution_run_id": "execution-B", "anomaly_run_id": "inflight-B",
        "anomaly_decision": {"decision": "ANOMALY", "score": 0.8, "confidence": 0.8, "severity": "HIGH"},
        "signal_observations": [{"signal_id": "signal-B", "target_id": "rule-B", "score": 0.8}],
    }
    case_tool = next(t for t in scoped_investigation_tools(state) if t.name == "get_anomaly_case")
    case = case_tool.invoke({"anomaly_run_id": "inflight-B"})
    assert case["decision"] == "ANOMALY"
    assert case["score"] == 0.8
    assert case["signals"] == state["signal_observations"]
    assert case["execution_run_id"] == "execution-B"
    assert case["dataset_id"] == "B"
    assert case_tool.invoke({"anomaly_run_id": "other-A"})["error"] == "SOURCE_BINDING_INVALID"


@pytest.mark.asyncio
async def test_prepare_api_requires_only_selected_dataset(sources, steward_client, monkeypatch):
    monkeypatch.setattr("src.api.routes.require_dataset_access", lambda *args, **kwargs: None)
    for name, expected in [("B", 2), ("A", 3), ("C", 4)]:
        result = await steward_client.post(
            f"/api/v1/datasets/{name}/workflows?fresh=true&fresh_profile=true",
            headers={"Idempotency-Key": f"dataset-only-{name}"},
        )
        assert result.status_code == 200, result.text
        run = result.json()
        job = await steward_client.post(
            f"/api/v1/workflows/{run['id']}/steps/UPLOAD_PROFILE?dataset_id={name}",
            headers={"Idempotency-Key": f"profile-only-{name}"},
        )
        assert job.status_code == 200, job.text
        ready = (await steward_client.get(f"/api/v1/workflows/{run['id']}")).json()
        assert ready["source_binding"]["dataset_id"] == name
        assert ready["source_binding"]["dataset_version_id"] == f"{name}-v1"
        profile = await steward_client.get(f"/api/v1/datasets/{name}/profile")
        assert profile.json()["row_count"] == expected


@pytest.mark.parametrize("name,rows", [("A", 3), ("B", 2), ("C", 4)])
def test_selected_source_through_understanding_proposals_checks_detector(sources, name, rows):
    from src.models.database import JobModel, RuleProposalModel, RuleVersionModel, WorkflowArtifactModel
    from src.services.anomaly_service import detect_anomalies
    from src.services.rule_proposer_workflow import (
        complete_rule_review,
        execute_step,
        queue_check_run,
        run_checks_and_prepare_analysis,
    )
    from tests.test_services.test_rule_proposer_workflow import _confirm_current_contract

    run = prepare(sources, name)
    execute_step(sources, run, "UNDERSTAND_DATA")
    _confirm_current_contract(sources, run)
    execute_step(sources, run, "PROPOSE_RULES")
    proposals = sources.query(RuleProposalModel).filter_by(workflow_run_id=run.id).all()
    assert proposals
    for proposal in proposals:
        proposal.status = "APPROVED"
        sources.add(RuleVersionModel(
            id=f"version-{proposal.id}", rule_proposal_id=proposal.id, dataset_id=name,
            dataset_version_id=f"{name}-v1", rule_spec=proposal.rule_spec, status="APPROVED", version=1,
        ))
    sources.flush()
    complete_rule_review(sources, run)
    execute_step(sources, run, "PUBLISH_RULESET")
    job = JobModel(id=f"check-{run.id}", type="RUN_DQ", status="PENDING", linked_entity=run.id, idempotency_key=f"check-{run.id}")
    sources.add(job)
    dq = queue_check_run(sources, run, job)
    sources.commit()
    run_checks_and_prepare_analysis(run.id, dq.id, job.id, None, "STEWARD")
    sources.expire_all()
    assert dq.status == "SUCCEEDED", dq.error_message
    result = detect_anomalies(sources, dq.id)
    volume = next(signal for signal in result["signals"] if signal["family"] == "VOLUME")
    assert volume["observed_value"] == str(rows)
    artifact = sources.query(WorkflowArtifactModel).filter_by(workflow_run_id=run.id, artifact_type="DQ_RUN").one()
    payload = json.loads(artifact.payload_json)
    assert payload["source_binding"]["dataset_id"] == name
    assert all(item["checked_count"] == rows for item in payload["results"])


def test_proposer_tool_keeps_dataset_source_despite_historical_versions(sources, tmp_path):
    from src.agents.tools.rule_proposer_tools import _load_versioned_frame
    from src.services.versioned_dataset import inspect_upload, schema_hash

    run = prepare(sources, "B")
    binding = workflow_binding(sources, run)
    path = tmp_path / "B-v2.csv"
    path.write_text("customer_id,amount\nb1,100\nb2,200\nb3,300\nb4,400\n", encoding="utf-8")
    inspected = inspect_upload(path.read_bytes(), path.name)
    sources.add(DatasetVersionModel(
        id="B-v2", dataset_id="B", workspace_id="routing-ws", version_number=2,
        status="READY", checksum=inspected.checksum, schema_hash=schema_hash(inspected.schema),
        row_count=4, created_by="routing-user",
        source_metadata_json=json.dumps({"source_artifact_id": "review-source-v2",
                                        "size_bytes": inspected.size_bytes, "format": "csv",
                                        "filename": path.name, "schema": inspected.schema}),
    ))
    sources.add(GovernedArtifactModel(
        id="review-source-v2", workspace_id="routing-ws", dataset_id="B",
        dataset_version_id="B-v2", artifact_type="SOURCE_DATASET",
        storage_locator=f"local:{path}", checksum=inspected.checksum,
        created_by="routing-user",
    ))
    sources.commit()
    is_versioned, frame = _load_versioned_frame("B")
    assert is_versioned
    assert binding["dataset_version_id"] == "B-v1" and binding["row_count"] == 2
    assert len(frame) == 2 and frame["amount"].min() == 10


def test_detector_uses_uploaded_profile_instead_of_legacy_profile(sources):
    from src.services.anomaly_service import detect_anomalies

    run = prepare(sources, "B")
    binding = workflow_binding(sources, run)
    sources.add(ProfileModel(dataset_id="B", row_count=999, completeness_score=0,
                             validity_score=0, duplicate_rate=0, evidence_keys="[]"))
    sources.add(DqRunModel(id="review-dq-B", job_id=f"job-{run.id}", dataset_id="B",
                           workflow_run_id=run.id, dataset_version_id="B-v1",
                           profile_run_id=binding["profile_run_id"],
                           rule_ids="[]", status="SUCCEEDED"))
    sources.commit()
    result = detect_anomalies(sources, "review-dq-B")
    volume = next(s for s in result["signals"] if s["family"] == "VOLUME")
    assert binding["row_count"] == 2
    assert volume["observed_value"] == "2"


def test_latest_execution_is_scoped_to_current_workflow(sources, monkeypatch):
    from src.api.routes import get_latest_dq_run

    run = prepare(sources, "B")
    sources.add(DqRunModel(id="old-B-execution", job_id=f"job-{run.id}", dataset_id="B",
                          rule_ids="[]", status="SUCCEEDED"))
    sources.commit()
    monkeypatch.setattr("src.api.routes.require_dataset_access", lambda *args, **kwargs: None)
    assert get_latest_dq_run("B", workflow_run_id=run.id, session=None, db=sources) is None
    sources.add(DqRunModel(id="current-B-execution", job_id=f"job-{run.id}", dataset_id="B",
                          workflow_run_id=run.id, rule_ids="[]", status="SUCCEEDED"))
    sources.commit()
    assert get_latest_dq_run("B", workflow_run_id=run.id, session=None, db=sources).id == "current-B-execution"
    assert get_latest_dq_run("A", workflow_run_id=run.id, session=None, db=sources) is None


def test_analysis_tools_keep_profile_and_reject_other_dataset(sources):
    from src.agents.tools.anomaly_investigation_tools import scoped_investigation_tools

    run = prepare(sources, "B")
    prepare(sources, "A")
    binding = workflow_binding(sources, run)
    original = sources.get(ProfileRunSnapshotModel, binding["profile_run_id"])
    sources.add(DatasetVersionModel(id="B-v2", dataset_id="B", workspace_id="routing-ws",
                                   version_number=2, status="READY", checksum="new",
                                   schema_hash="new", row_count=7, created_by="routing-user"))
    sources.flush()
    sources.add(ProfileRunSnapshotModel(id="review-profile-v2", workspace_id="routing-ws",
                                        dataset_id="B", dataset_version_id="B-v2",
                                        status="COMPLETED", row_count=7,
                                        schema_json=original.schema_json,
                                        metrics_json=original.metrics_json,
                                        triggered_by="routing-user"))
    sources.commit()
    # Same scope shape constructed by execute_analysis_run for Graph 3.
    state = {"dataset_id": "B", "dataset_version_id": "B-v1",
             "profile_run_id": binding["profile_run_id"],
             "execution_run_id": "review-test-B", "anomaly_run_id": "review-anom-B",
             "metadata": {"analysis_run_id": "review-analysis-B"}}
    tool = next(t for t in scoped_investigation_tools(state) if t.name == "get_dataset_profile")
    returned = tool.invoke({"dataset_id": "B"})
    assert returned["profile_run_id"] == binding["profile_run_id"]
    assert returned["row_count"] == 2
    assert tool.invoke({"dataset_id": "A"})["error"] == "SOURCE_BINDING_INVALID"


def test_graph2_uses_exact_source_artifact(sources):
    from src.services.job_runner import _materialize_versioned_dataset_path

    old = sources.get(GovernedArtifactModel, "source-B")
    valid_locator = old.storage_locator
    old.storage_locator += ".missing"
    sources.add(GovernedArtifactModel(id="review-current-source-B", workspace_id=old.workspace_id,
                                     dataset_id="B", dataset_version_id="B-v1",
                                     artifact_type="SOURCE_DATASET", storage_locator=valid_locator,
                                     checksum=old.checksum, created_by="routing-user"))
    version = sources.get(DatasetVersionModel, "B-v1")
    metadata = json.loads(version.source_metadata_json)
    metadata["source_artifact_id"] = "review-current-source-B"
    version.source_metadata_json = json.dumps(metadata)
    sources.commit()
    run = prepare(sources, "B")
    binding = workflow_binding(sources, run)
    assert binding["source_ref"] == "review-current-source-B"
    path, temporary = _materialize_versioned_dataset_path(sources, "B", dataset_version_id="B-v1")
    assert str(path) == valid_locator.removeprefix("local:")
    assert temporary is False


@pytest.mark.asyncio
async def test_cli_refuses_unreviewed_source_instead_of_legacy_execution(sources, monkeypatch):
    import src.agents.graph as module

    prepare(sources, "B")
    graph = AsyncMock()
    graph.ainvoke.return_value = {}
    monkeypatch.setattr(module, "build_execution_graph", lambda: graph)
    monkeypatch.setattr(module, "run_anomaly_graph", AsyncMock(return_value={}))
    monkeypatch.setattr(module, "start_graph_run", lambda **kwargs: None)
    with pytest.raises(ValueError, match="Approve"):
        await module.run_execution_graph("B")
    graph.ainvoke.assert_not_awaited()



@pytest.mark.asyncio
async def test_standalone_anomaly_rejects_other_dataset(sources, monkeypatch):
    import src.agents.graph as module

    run = prepare(sources, "A")
    sources.add(DqRunModel(id="review-dq-A", job_id=f"job-{run.id}", dataset_id="A",
                           rule_ids="[]", status="SUCCEEDED"))
    sources.commit()
    graph = AsyncMock()
    graph.ainvoke.return_value = {}
    monkeypatch.setattr(module, "build_anomaly_graph", lambda **kwargs: graph)
    monkeypatch.setattr(module, "start_graph_run", lambda **kwargs: None)
    with pytest.raises(ValueError, match="SOURCE_BINDING_INVALID"):
        await module.run_anomaly_graph(dataset_id="B", execution_run_id="review-dq-A", initialize_schema=False)
    graph.ainvoke.assert_not_awaited()
