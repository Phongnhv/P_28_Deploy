"""Integration tests for the SSE per-node stream endpoint (Gap A / part 3.1).

GET /api/v1/workflows/{workflow_run_id}/stream fans out the events published by
a graph run (via ``node_event_stream.broker``) to the browser as Server-Sent
Events.  These tests exercise the real ASGI app through the shared ``client``
fixture: authentication, dataset-access enforcement, 404 handling, and the
replay-backlog happy path (a run that already published ``done`` closes the
stream deterministically, so the response body is fully drainable).
"""

import uuid

import pytest
from sqlalchemy.orm import Session

from src.models.database import WorkflowRunModel
from src.services.node_event_stream import broker
from src.services.rule_store import get_engine

_DATASET_ID = "dataset-nyc-yellow-taxi-50k"  # seeded by rule_store with steward=MANAGE


def _seed_workflow_run(dataset_id: str = _DATASET_ID) -> str:
    run_id = uuid.uuid4().hex
    with Session(get_engine()) as session:
        session.add(
            WorkflowRunModel(
                id=run_id,
                dataset_id=dataset_id,
                current_step="ANALYZE_REPORT",
                status="ACTIVE",
                steps_json="[]",
            )
        )
        session.commit()
    return run_id


async def _login_steward(client) -> dict:
    res = await client.post("/api/v1/session", json={"username": "steward", "password": "steward"})
    assert res.status_code == 200, res.text
    return {"X-CSRF-Token": res.json()["csrf_token"]}


@pytest.mark.asyncio
async def test_stream_requires_authentication(client):
    run_id = _seed_workflow_run()
    r = await client.get(f"/api/v1/workflows/{run_id}/stream")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_stream_unknown_run_returns_404(client):
    await _login_steward(client)
    r = await client.get(f"/api/v1/workflows/{uuid.uuid4().hex}/stream")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_stream_replays_backlog_and_closes_on_done(client):
    await _login_steward(client)
    run_id = _seed_workflow_run()

    # Simulate a completed graph run: publish the full event history to the
    # broker BEFORE the client connects.  The endpoint must replay it and then
    # close the stream when it hits the terminal ``done`` event.
    broker.reset(run_id)
    broker.publish(run_id, {"type": "run_start", "stream_id": run_id})
    broker.publish(
        run_id,
        {"type": "node", "node": "anomaly_detector", "preview": {"connection_string": "***redacted***"}},
    )
    broker.publish(run_id, {"type": "node", "node": "report_writer", "preview": {"ok": True}})
    broker.publish(run_id, {"type": "done", "stream_id": run_id})

    r = await client.get(f"/api/v1/workflows/{run_id}/stream")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")

    body = r.text
    # SSE frames use "event: <type>" lines; assert ordering and terminal close.
    assert "event: run_start" in body
    assert "event: node" in body
    assert "anomaly_detector" in body
    assert "report_writer" in body
    assert body.index("run_start") < body.index("anomaly_detector") < body.index("event: done")
    # Secrets stay redacted end-to-end.
    assert "***redacted***" in body


@pytest.mark.asyncio
async def test_stream_forbidden_without_dataset_access(client):
    """A dataset the steward cannot see must yield 403, not the stream."""
    run_id = _seed_workflow_run(dataset_id="dataset-no-access")
    # Seed the orphan dataset so the FK holds but no access grant exists.
    from src.models.database import DatasetModel

    with Session(get_engine()) as session:
        if session.get(DatasetModel, "dataset-no-access") is None:
            session.add(
                DatasetModel(
                    id="dataset-no-access",
                    name="No Access",
                    description="orphan dataset with no access grant",
                    status="REGISTERED",
                    source_label="test",
                    manifest_version="1",
                    checksum="deadbeef",
                )
            )
            session.commit()

    await _login_steward(client)
    r = await client.get(f"/api/v1/workflows/{run_id}/stream")
    assert r.status_code == 403
