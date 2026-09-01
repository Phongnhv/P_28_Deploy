from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.models.database import DqRunModel, Graph1RunModel, JobModel, WorkflowRunModel
from src.services.job_dispatch import create_persisted_job, dispatch_job
from src.services.job_service import check_and_cleanup_stale_leases


def test_canonical_dispatch_claims_and_completes_graph1_job(test_db, monkeypatch):
    with Session(test_db) as db:
        db.add(Graph1RunModel(
            id="g1-dispatch",
            dataset_id="dataset-dispatch",
            status="PENDING",
            created_by="steward",
            idempotency_key="graph1-dispatch-key",
            state_json="{}",
        ))
        db.flush()
        job, created = create_persisted_job(
            db,
            job_type="GRAPH1_EXECUTION",
            linked_entity="g1-dispatch",
            idempotency_key="dispatch-graph1",
            message="Queued Graph 1 execution",
        )
        assert created is True
        job_id = job.id

    execute = AsyncMock()
    monkeypatch.setattr("src.services.graph1_workflow.execute_graph1_run", execute)
    monkeypatch.setattr("src.services.job_dispatch.get_settings", lambda: type("Settings", (), {"app_env": "test"})())
    assert dispatch_job(job_id, "GRAPH1_EXECUTION") is True
    execute.assert_awaited_once_with("g1-dispatch")

    with Session(test_db) as db:
        stored = db.get(JobModel, job_id)
        assert stored.status == "SUCCEEDED"
        assert stored.attempt_count == 1


def test_retryable_job_can_be_reclaimed_without_duplicate_execution(test_db, monkeypatch):
    with Session(test_db) as db:
        db.add(Graph1RunModel(
            id="g1-retry-dispatch",
            dataset_id="dataset-retry-dispatch",
            status="PENDING",
            created_by="steward",
            idempotency_key="graph1-retry-dispatch-key",
            state_json="{}",
        ))
        db.flush()
        job, _ = create_persisted_job(
            db,
            job_type="GRAPH1_EXECUTION",
            linked_entity="g1-retry-dispatch",
            idempotency_key="dispatch-retry",
            message="Queued Graph 1 execution",
        )
        job_id = job.id

    execute = AsyncMock(side_effect=RuntimeError("temporary worker error"))
    monkeypatch.setattr("src.services.graph1_workflow.execute_graph1_run", execute)
    monkeypatch.setattr("src.services.job_dispatch.get_settings", lambda: type("Settings", (), {"app_env": "test"})())
    assert dispatch_job(job_id, "GRAPH1_EXECUTION") is False

    execute.side_effect = None
    assert dispatch_job(job_id, "GRAPH1_EXECUTION") is True
    assert execute.await_count == 2

    with Session(test_db) as db:
        stored = db.get(JobModel, job_id)
        assert stored.status == "SUCCEEDED"
        assert stored.attempt_count == 2


def test_workflow_quality_job_uses_durable_worker_contract(test_db, monkeypatch):
    workflow_id = "workflow-dispatch"
    job_id = "job-workflow-dispatch"
    with Session(test_db) as db:
        db.add(
            WorkflowRunModel(
                id=workflow_id,
                dataset_id="dataset-nyc-yellow-taxi-50k",
                current_step="RUN_CHECKS",
                status="ACTIVE",
                steps_json='[{"key":"RUN_CHECKS","status":"RUNNING"},{"key":"ANALYZE_REPORT","status":"LOCKED"}]',
            )
        )
        db.add(
            JobModel(
                id=job_id,
                type="WORKFLOW_RUN_CHECKS",
                status="PENDING",
                progress=0,
                message="Queued workflow quality checks",
                idempotency_key="workflow-dispatch-key",
                linked_entity=workflow_id,
                correlation_id=workflow_id,
            )
        )
        db.flush()
        db.add(
            DqRunModel(
                id="run-workflow-dispatch",
                job_id=job_id,
                dataset_id="dataset-nyc-yellow-taxi-50k",
                workflow_run_id=workflow_id,
                rule_ids="[]",
                status="PENDING",
            )
        )
        db.commit()

    called = []

    def fake_run_checks(workflow_run_id, dq_run_id, received_job_id, session_id, actor_role):
        called.append((workflow_run_id, dq_run_id, received_job_id, session_id, actor_role))
        with Session(test_db) as db:
            workflow = db.get(WorkflowRunModel, workflow_id)
            workflow.current_step = "ANALYZE_REPORT"
            workflow.steps_json = '[{"key":"RUN_CHECKS","status":"COMPLETED"},{"key":"ANALYZE_REPORT","status":"READY"}]'
            db.commit()

    monkeypatch.setattr("src.services.rule_proposer_workflow.run_checks_and_prepare_analysis", fake_run_checks)
    monkeypatch.setattr("src.services.job_dispatch.get_settings", lambda: type("Settings", (), {"app_env": "test"})())

    assert dispatch_job(job_id, "WORKFLOW_RUN_CHECKS") is True
    assert called == [(workflow_id, "run-workflow-dispatch", job_id, None, "WORKER")]

    with Session(test_db) as db:
        stored = db.get(JobModel, job_id)
        assert stored.status == "SUCCEEDED"
        assert stored.attempt_count == 1


def test_expired_running_job_is_marked_retryable(test_db):
    with Session(test_db) as db:
        job = JobModel(
            id="job-expired-lease",
            type="GRAPH1_EXECUTION",
            status="RUNNING",
            progress=10,
            message="Running",
            idempotency_key="expired-lease-key",
            linked_entity="g1-expired-lease",
            lease_expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        db.add(job)
        db.commit()

    assert check_and_cleanup_stale_leases() == 1
    with Session(test_db) as db:
        stored = db.get(JobModel, "job-expired-lease")
        assert stored.status == "FAILED_RETRYABLE"


def test_local_worker_transport_reports_spawn_failure(monkeypatch):
    import src.local_worker_api as local_worker_api

    def fail_spawn(*args, **kwargs):
        raise OSError("spawn failed")

    monkeypatch.setattr(local_worker_api.subprocess, "Popen", fail_spawn)
    response = TestClient(local_worker_api.app).post(
        "/run",
        params={"job_id": "job-spawn-failure", "job_type": "GRAPH1_EXECUTION"},
    )
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_worker_entrypoint_runs_canonical_job_without_nested_event_loop(monkeypatch):
    import src.services.job_dispatch as job_dispatch
    import src.worker as worker

    class FakeQuery:
        def filter_by(self, **kwargs):
            return self

        def first(self):
            return SimpleNamespace(linked_entity="g1-entrypoint")

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def query(self, *args, **kwargs):
            return FakeQuery()

    called = []
    init_calls = []
    monkeypatch.setenv("RUN_JOB_ID", "job-entrypoint")
    monkeypatch.setenv("RUN_JOB_TYPE", "GRAPH1_EXECUTION")
    monkeypatch.setattr(worker, "init_db", lambda: init_calls.append(True))
    monkeypatch.setattr(worker, "get_engine", lambda: object())
    monkeypatch.setattr(worker, "Session", lambda engine: FakeSession())
    monkeypatch.setattr(
        job_dispatch,
        "_run_persisted_job",
        lambda job_id, job_type: called.append((job_id, job_type)) or True,
    )

    await worker.main()

    assert called == [("job-entrypoint", "GRAPH1_EXECUTION")]
    assert init_calls == []
