import pytest
from fastapi import status
from sqlalchemy.orm import Session

from src.models.database import DatasetModel
from src.services.job_runner import run_ingest_profile
from src.services.rule_store import get_engine


@pytest.mark.asyncio
async def test_profile_hidden_before_completion(client):
    # Log in as steward
    login_res = await client.post("/api/v1/session", json={"username": "steward", "password": "steward"})
    assert login_res.status_code == 200
    csrf_token = login_res.json()["csrf_token"]

    # 1. Profile should return 404 initially because status is REGISTERED and no profile exists
    profile_res = await client.get("/api/v1/datasets/dataset-nyc-yellow-taxi-50k/profile")
    assert profile_res.status_code == status.HTTP_404_NOT_FOUND
    assert profile_res.json()["code"] == "NOT_FOUND"

    # 2. Trigger ingestion
    headers = {
        "X-CSRF-Token": csrf_token,
        "Idempotency-Key": "profile-ingest-key"
    }
    ingest_res = await client.post(
        "/api/v1/datasets/dataset-nyc-yellow-taxi-50k/ingestions",
        headers=headers
    )
    assert ingest_res.status_code == 202
    job_id = ingest_res.json()["job_id"]

    # 3. Profile should still return 404 because job is PENDING/RUNNING and not SUCCEEDED
    # Reset dataset status to REGISTERED in DB (since background task ran inline) to simulate PENDING
    with Session(get_engine()) as session:
        d = session.query(DatasetModel).filter(DatasetModel.id == "dataset-nyc-yellow-taxi-50k").first()
        if d:
            d.status = "REGISTERED"
            session.commit()

    profile_res2 = await client.get("/api/v1/datasets/dataset-nyc-yellow-taxi-50k/profile")
    assert profile_res2.status_code == status.HTTP_404_NOT_FOUND

    # 4. Synchronously execute background task to simulate completion
    run_ingest_profile(job_id, "dataset-nyc-yellow-taxi-50k")

    # 5. Now profile should return 200 and correct calculations
    profile_res3 = await client.get("/api/v1/datasets/dataset-nyc-yellow-taxi-50k/profile")
    assert profile_res3.status_code == status.HTTP_200_OK
    data = profile_res3.json()
    assert data["dataset_id"] == "dataset-nyc-yellow-taxi-50k"
    assert data["row_count"] == 50000
    assert "completeness_score" in data
    assert "validity_score" in data
    assert "duplicate_rate" in data
    assert len(data["columns"]) == 21

    # Check that a column profile exists
    col_names = [c["name"] for c in data["columns"]]
    assert "vendor_id" in col_names
    assert "fare_amount" in col_names
