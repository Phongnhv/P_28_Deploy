import json
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.agents.nodes.profiler_node import raw_profiler_node
from src.agents.tools.db_profiler_tool import profile_database
from src.models.database import (
    DatasetModel,
    DatasetVersionModel,
    GovernedArtifactModel,
    JobModel,
    ProfileModel,
    ProfileRunSnapshotModel,
    UserAccountModel,
    WorkspaceModel,
)
from src.services.dashboard_agent_workflow import build_proposal_evidence
from src.services.job_runner import _materialize_versioned_dataset_path, execute_uploaded_rule
from src.services.rule_proposer_workflow import _add_artifact, _profile_snapshot, execute_step, get_or_create_run
from src.services.source_binding import resolve_source_binding, workflow_binding
from src.services.versioned_dataset import SourceIntegrityError, inspect_upload, schema_hash


@pytest.fixture
def sources(test_db, tmp_path):
    db = Session(test_db)
    db.add(UserAccountModel(id="routing-user", username="routing-user", display_name="Test", password_hash="x", role="STEWARD"))
    db.add(WorkspaceModel(id="routing-ws", name="Routing", created_by="routing-user"))
    db.flush()
    for name, content in {"A": "taxi_id,fare\n1,5\n2,6\n3,7\n", "B": "customer_id,amount\nc1,10\nc2,\n", "C": "product,stock\np1,0\np2,5\np3,7\np4,9\n"}.items():
        source = tmp_path / f"{name}.csv"
        source.write_text(content, encoding="utf-8")
        inspected = inspect_upload(source.read_bytes(), source.name)
        db.add(DatasetModel(id=name, name=name, description="", status="PROFILE_READY", row_count=inspected.row_count, source_label=source.name, manifest_version="versioned-v1", checksum=inspected.checksum))
        db.add(DatasetVersionModel(id=f"{name}-v1", dataset_id=name, workspace_id="routing-ws", version_number=1, status="READY", checksum=inspected.checksum, schema_hash=schema_hash(inspected.schema), row_count=inspected.row_count, created_by="routing-user", source_metadata_json=json.dumps({"source_artifact_id": f"source-{name}", "size_bytes": inspected.size_bytes, "format": "csv", "filename": source.name, "schema": inspected.schema})))
        db.add(GovernedArtifactModel(id=f"source-{name}", workspace_id="routing-ws", dataset_id=name, dataset_version_id=f"{name}-v1", artifact_type="SOURCE_DATASET", storage_locator=f"local:{source}", checksum=inspected.checksum, created_by="routing-user"))
    db.commit()
    yield db
    db.close()


def prepare(db, dataset_id):
    run = get_or_create_run(db, db.get(DatasetModel, dataset_id), force_new=True, fresh_profile=True, dataset_version_id=f"{dataset_id}-v1")
    db.add(JobModel(id=f"job-{run.id}", type="WORKFLOW_PROFILE", status="RUNNING", linked_entity=run.id, idempotency_key=run.id))
    db.commit()
    execute_step(db, run, "UPLOAD_PROFILE")
    db.commit()
    return run


@pytest.mark.parametrize("dataset_id,rows,columns", [("A", 3, ["taxi_id", "fare"]), ("B", 2, ["customer_id", "amount"]), ("C", 4, ["product", "stock"])])
def test_three_sources_fresh_profile_and_rule_semantics(sources, dataset_id, rows, columns):
    run = prepare(sources, dataset_id)
    binding = workflow_binding(sources, run)
    snapshot = _profile_snapshot(sources, dataset_id, binding=binding)
    assert snapshot["row_count"] == rows
    assert [c["name"] for c in snapshot["columns"]] == columns
    assert binding["profile_run_id"] == f"profile-{run.id}"
    evidence = build_proposal_evidence(sources, dataset_id, workflow_run_id=run.id)
    assert evidence.row_count == rows
    assert snapshot["validity_score"] is None
    assert evidence.validity_score is None
    assert list(evidence.to_agent_digest()) == [dataset_id]
    assert evidence.to_agent_digest()[dataset_id]["table"] == dataset_id
    from src.services.dashboard_agent_workflow import _build_dashboard_rule_candidates
    for candidate in _build_dashboard_rule_candidates(evidence):
        assert set(candidate.evidence_refs).issubset(evidence.evidence_keys)
    path, temporary = _materialize_versioned_dataset_path(sources, dataset_id, dataset_version_id=binding["dataset_version_id"])
    checked, _, failed = execute_uploaded_rule(path, "not_null", {"type": "not_null", "column": columns[-1]})
    assert (checked, failed) == (rows, 1 if dataset_id == "B" else 0)
    assert temporary is False
    if dataset_id == "B":
        assert snapshot["columns"][1]["null_rate"] == 0.5
    if dataset_id == "C":
        assert snapshot["columns"][1]["min_value"] == 0
    for step, kind in [("UNDERSTAND_DATA", "PROFILE_SNAPSHOT"), ("PROPOSE_RULES", "RULE_SET"), ("RUN_CHECKS", "DQ_RUN"), ("ANALYZE_REPORT", "ANOMALY_REPORT")]:
        artifact = _add_artifact(sources, run, step, kind, {"checked_count": checked, "failed_count": failed})
        payload = json.loads(artifact.payload_json)
        assert payload["dataset_version_id"] == f"{dataset_id}-v1"
        assert payload["source_binding"] == binding


def test_pinned_profile_ignores_legacy_and_later_version(sources):
    run = prepare(sources, "B")
    binding = workflow_binding(sources, run)
    sources.add(ProfileModel(dataset_id="B", row_count=999, completeness_score=0, validity_score=0, duplicate_rate=0, evidence_keys="[]"))
    sources.add(DatasetVersionModel(id="B-v2", dataset_id="B", workspace_id="routing-ws", version_number=2, status="READY", checksum="other", schema_hash="other", row_count=888, created_by="routing-user"))
    sources.commit()
    assert _profile_snapshot(sources, "B", binding=binding)["row_count"] == 2
    assert build_proposal_evidence(sources, "B", workflow_run_id=run.id).row_count == 2
    path, _ = _materialize_versioned_dataset_path(sources, "B", dataset_version_id=binding["dataset_version_id"])
    assert path.name == "B.csv"


def test_profile_retry_is_idempotent_but_new_workflow_preserves_history(sources):
    run = prepare(sources, "B")
    original = workflow_binding(sources, run)["profile_run_id"]
    execute_step(sources, run, "UPLOAD_PROFILE")
    assert workflow_binding(sources, run)["profile_run_id"] == original
    second = prepare(sources, "B")
    assert workflow_binding(sources, second)["profile_run_id"] != original
    assert sources.query(ProfileRunSnapshotModel).filter_by(dataset_id="B").count() == 2


@pytest.mark.parametrize("failure", ["missing", "size", "checksum"])
def test_bad_source_fails_without_fallback(sources, failure):
    artifact = sources.get(GovernedArtifactModel, "source-B")
    if failure == "missing":
        artifact.storage_locator += ".missing"
    elif failure == "size":
        metadata = json.loads(sources.get(DatasetVersionModel, "B-v1").source_metadata_json)
        metadata["size_bytes"] += 1
        sources.get(DatasetVersionModel, "B-v1").source_metadata_json = json.dumps(metadata)
    else:
        from pathlib import Path
        path = Path(artifact.storage_locator.removeprefix("local:"))
        path.write_bytes(path.read_bytes().replace(b"c1", b"z1"))
    sources.commit()
    with pytest.raises(SourceIntegrityError):
        prepare(sources, "B")
    assert sources.query(ProfileRunSnapshotModel).filter_by(dataset_id="B", status="COMPLETED").count() == 0


def test_binding_rejects_other_dataset_version(sources):
    with pytest.raises(SourceIntegrityError, match="another dataset"):
        resolve_source_binding(sources, "B", dataset_version_id="A-v1", require_profile=False)


@pytest.mark.asyncio
async def test_api_prepare_replay_and_mismatched_resume(sources, steward_client, monkeypatch):
    monkeypatch.setattr("src.api.routes.require_dataset_access", lambda *args, **kwargs: None)
    legacy = await steward_client.post("/api/v1/datasets/B/ingestions", headers={"Idempotency-Key": "legacy-B"})
    assert legacy.status_code == 409
    assert legacy.json()["code"] == "VERSIONED_PROFILE_REQUIRED"
    path = "/api/v1/datasets/B/workflows?fresh=true&fresh_profile=true&dataset_version_id=B-v1"
    response = await steward_client.post(path, headers={"Idempotency-Key": "prepare-B"})
    assert response.status_code == 200, response.text
    workflow = response.json()
    replay = await steward_client.post(path, headers={"Idempotency-Key": "prepare-B"})
    assert replay.json()["id"] == workflow["id"]
    mismatch = await steward_client.post(path.replace("B-v1", "B-v2"), headers={"Idempotency-Key": "prepare-B"})
    assert mismatch.status_code == 409
    base = f"/api/v1/workflows/{workflow['id']}/steps/"
    rejected = await steward_client.post(base + "UNDERSTAND_DATA?dataset_id=B&dataset_version_id=B-v1")
    assert rejected.status_code == 409
    queued = await steward_client.post(base + "UPLOAD_PROFILE?dataset_id=B&dataset_version_id=B-v1", headers={"Idempotency-Key": "profile-B"})
    assert queued.status_code == 200, queued.text
    sources.expire_all()
    ready = await steward_client.get(f"/api/v1/workflows/{workflow['id']}")
    binding = ready.json()["source_binding"]
    assert binding["profile_run_id"]
    profile = await steward_client.get(f"/api/v1/datasets/B/profile?dataset_version_id=B-v1&profile_run_id={binding['profile_run_id']}")
    assert profile.status_code == 200, profile.text
    assert profile.json()["row_count"] == 2
    for query in ("dataset_id=A&dataset_version_id=B-v1", "dataset_id=B&dataset_version_id=A-v1"):
        rejected = await steward_client.post(base + "UNDERSTAND_DATA?" + query)
        assert rejected.status_code == 409
    replay = await steward_client.post(base + "UPLOAD_PROFILE?dataset_id=B&dataset_version_id=B-v1", headers={"Idempotency-Key": "profile-B"})
    assert replay.json()["job_id"] == queued.json()["job_id"]
    assert sources.query(ProfileRunSnapshotModel).filter_by(dataset_id="B").count() == 1


def test_graph3_profile_tool_is_scoped_to_workflow(sources):
    from src.agents.tools.anomaly_investigation_tools import scoped_investigation_tools
    run = prepare(sources, "B")
    tools = scoped_investigation_tools({"dataset_id": "B", "execution_run_id": "dq-B", "anomaly_run_id": "anom-B", "metadata": {"workflow_run_id": run.id}})
    profile = next(t for t in tools if t.name == "get_dataset_profile")
    assert profile.invoke({"dataset_id": "B"})["row_count"] == 2
    assert profile.invoke({"dataset_id": "A"})["error"] == "SOURCE_BINDING_INVALID"
    assert {t.name for t in tools} == {"get_dataset_profile", "get_metric_history", "get_related_quality_results", "get_anomaly_case", "query_readonly_evidence"}


def test_cloud_runtime_never_uses_local_source_even_with_development_env(monkeypatch):
    from src.config import get_settings
    from src.services.versioned_dataset import _local_source_storage_allowed
    monkeypatch.setattr(get_settings(), "app_env", "development")
    monkeypatch.setenv("K_SERVICE", "routing-test-api")
    assert not _local_source_storage_allowed()


def test_empty_dataset_is_not_a_missing_source(sources):
    from pathlib import Path

    from src.services.rule_proposer_workflow import WorkflowError
    artifact = sources.get(GovernedArtifactModel, "source-B")
    path = Path(artifact.storage_locator.removeprefix("local:"))
    path.write_bytes(b"customer_id,amount\n")
    inspected = inspect_upload(path.read_bytes(), path.name)
    version = sources.get(DatasetVersionModel, "B-v1")
    metadata = json.loads(version.source_metadata_json)
    metadata.update(size_bytes=inspected.size_bytes, schema=inspected.schema)
    version.source_metadata_json = json.dumps(metadata)
    version.checksum = artifact.checksum = inspected.checksum
    sources.get(DatasetModel, "B").checksum = inspected.checksum
    version.schema_hash = schema_hash(inspected.schema)
    version.row_count = 0
    sources.commit()
    with pytest.raises(WorkflowError, match="EMPTY_DATASET"):
        prepare(sources, "B")
    snapshot = sources.query(ProfileRunSnapshotModel).filter_by(dataset_id="B").one()
    assert snapshot.row_count == 0 and snapshot.status == "COMPLETED"


def test_studio_profile_uses_metrics_not_schema_defaults(sources):
    from src.services.graph1_workflow import _versioned_profile
    run = prepare(sources, "B")
    binding = workflow_binding(sources, run)
    _, profile = _versioned_profile(sources, "B", "B-v1", binding["profile_run_id"])
    assert profile["version_source"]["columns"]["amount"]["null_pct"] == 0.5
    assert profile["version_source"]["columns"]["amount"]["distinct_in_sample"] == 1


@pytest.mark.asyncio
async def test_no_target_never_profiles_shared_rows(tmp_path):
    url = f"sqlite:///{tmp_path / 'shared.db'}"
    from sqlalchemy import create_engine
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE source_rows (dataset_id TEXT, value INTEGER)"))
        conn.execute(text("INSERT INTO source_rows VALUES ('A', 1), ('A', 2)"))
    for target in (None, ["source_rows"]):
        with patch("src.agents.nodes.profiler_node._profile_all_tables") as profile:
            result = await raw_profiler_node({"dataset_id": "B", "target_tables": target, "metadata": {"connection_string": url}})
        assert "SOURCE_" in result["error"]
        profile.assert_not_called()
    result = json.loads(profile_database.invoke({"connection_string": url, "table_name": "source_rows"}))
    assert "SOURCE_SCOPE_REQUIRED" in result["error"]
    engine.dispose()
