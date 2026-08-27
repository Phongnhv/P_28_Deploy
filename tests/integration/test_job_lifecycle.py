
import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app
from src.services.job_service import get_job, update_job_status


@pytest.mark.asyncio
async def test_job_dispatch_and_idempotency():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Login first to satisfy authentication
        login_res = await client.post("/api/v1/session", json={"username": "steward", "password": "steward"})
        assert login_res.status_code == 200
        csrf_token = login_res.json()["csrf_token"]

        ikey = "test-idem-key-12345"
        dataset_id = "dataset-nyc-yellow-taxi-50k"

        # 1. Test successful dispatch (202 Accepted)
        res1 = await client.post(
            "/api/v1/jobs",
            json={"type": "INGEST_PROFILE", "linked_entity": dataset_id},
            headers={"Idempotency-Key": ikey, "X-CSRF-Token": csrf_token}
        )
        assert res1.status_code == 202
        data = res1.json()
        assert "job_id" in data
        job_id = data["job_id"]

        # 2. Test Idempotency Conflict (409) with same key
        res2 = await client.post(
            "/api/v1/jobs",
            json={"type": "INGEST_PROFILE", "linked_entity": dataset_id},
            headers={"Idempotency-Key": ikey, "X-CSRF-Token": csrf_token}
        )
        assert res2.status_code == 409

        # 3. Test Lifecycle Status transition logic
        update_job_status(job_id, "RUNNING")
        job = get_job(job_id)
        assert job.status == "RUNNING"

        update_job_status(job_id, "COMPLETED")
        job = get_job(job_id)
        assert job.status == "COMPLETED"

        # 4. Test GET poll endpoint
        res3 = await client.get(f"/api/v1/jobs/{job_id}")
        assert res3.status_code == 200
        assert res3.json()["id"] == job_id
        assert res3.json()["status"] == "COMPLETED"
