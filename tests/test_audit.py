import pytest
from sqlalchemy.orm import Session

from src.models.database import AuditEventModel
from src.services.job_runner import run_ingest_profile
from src.services.rule_store import get_engine


@pytest.mark.asyncio
async def test_audit_trail_recorded(client):
    # Log in
    login_res = await client.post("/api/v1/session", json={"username": "steward", "password": "steward"})
    assert login_res.status_code == 200
    csrf_token = login_res.json()["csrf_token"]

    # Verify login audit event exists in DB
    with Session(get_engine()) as session:
        events = session.query(AuditEventModel).filter(AuditEventModel.action_code == "LOGIN").all()
        assert len(events) >= 1
        assert events[0].actor_role == "STEWARD"
        assert events[0].entity_type == "session"

    # Start and run ingestion job
    headers = {
        "X-CSRF-Token": csrf_token,
        "Idempotency-Key": "audit-ingest-key"
    }
    ingest_res = await client.post(
        "/api/v1/datasets/dataset-nyc-yellow-taxi-50k/ingestions",
        headers=headers
    )
    assert ingest_res.status_code == 202
    job_id = ingest_res.json()["job_id"]

    # Run Ingestion
    run_ingest_profile(job_id, "dataset-nyc-yellow-taxi-50k")

    # Verify job transition audits are appended
    with Session(get_engine()) as session:
        job_starts = session.query(AuditEventModel).filter(AuditEventModel.action_code == "JOB_STARTED").all()
        assert len(job_starts) >= 1

        profile_creates = session.query(AuditEventModel).filter(AuditEventModel.action_code == "PROFILE_CREATED").all()
        assert len(profile_creates) >= 1

    # Logout
    logout_res = await client.delete("/api/v1/session")
    assert logout_res.status_code == 204

    # Verify logout audit exists
    with Session(get_engine()) as session:
        logouts = session.query(AuditEventModel).filter(AuditEventModel.action_code == "LOGOUT").all()
        assert len(logouts) >= 1

    # Check API audit logs endpoint
    # Re-login to access audit logs
    login_res2 = await client.post("/api/v1/session", json={"username": "steward", "password": "steward"})
    assert login_res2.status_code == 200

    logs_res = await client.get("/api/v1/audit-logs?limit=50")
    assert logs_res.status_code == 200
    logs = logs_res.json()
    assert len(logs) >= 3 # login, job, logout, etc.
