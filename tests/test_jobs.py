import pytest
from fastapi import status
from sqlalchemy.orm import Session

from src.models.database import JobModel
from src.services.rule_store import get_engine


@pytest.mark.asyncio
async def test_job_idempotency_conflict(client):
    # Log in as steward
    login_res = await client.post("/api/v1/session", json={"username": "steward", "password": "steward"})
    assert login_res.status_code == 200
    csrf_token = login_res.json()["csrf_token"]

    headers = {"X-CSRF-Token": csrf_token, "Idempotency-Key": "unique-idempotency-key-1"}

    # Trigger first job (expect 202)
    response1 = await client.post("/api/v1/datasets/dataset-nyc-yellow-taxi-50k/ingestions", headers=headers)
    assert response1.status_code == status.HTTP_202_ACCEPTED
    job_id1 = response1.json()["job_id"]
    assert response1.json()["status"] == "PENDING"

    # Manually write active PENDING job to DB to ensure status is pending/running when second request arrives
    with Session(get_engine()) as db_sess:
        job = db_sess.query(JobModel).filter(JobModel.id == job_id1).first()
        if job:
            job.status = "PENDING"
            db_sess.commit()

    # Trigger second job with SAME idempotency key (expect 409)
    response2 = await client.post("/api/v1/datasets/dataset-nyc-yellow-taxi-50k/ingestions", headers=headers)
    assert response2.status_code == status.HTTP_409_CONFLICT
    assert response2.json()["code"] == "CONFLICT"


@pytest.mark.asyncio
async def test_job_state_polling(client):
    # Log in as steward
    login_res = await client.post("/api/v1/session", json={"username": "steward", "password": "steward"})
    assert login_res.status_code == 200
    csrf_token = login_res.json()["csrf_token"]

    headers = {"X-CSRF-Token": csrf_token, "Idempotency-Key": "unique-idempotency-key-2"}

    # Trigger job
    response = await client.post("/api/v1/datasets/dataset-nyc-yellow-taxi-50k/ingestions", headers=headers)
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    # Poll job status
    poll_res = await client.get(f"/api/v1/jobs/{job_id}")
    assert poll_res.status_code == 200
    assert poll_res.json()["id"] == job_id
    assert poll_res.json()["type"] == "INGEST_PROFILE"
    assert poll_res.json()["status"] in ("PENDING", "RUNNING", "SUCCEEDED")
