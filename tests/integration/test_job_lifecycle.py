import pytest
from httpx import AsyncClient, ASGITransport
import asyncio
from src.main import app
from src.services.job_service import update_job_status, get_job

@pytest.mark.asyncio
async def test_job_dispatch_and_idempotency():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        ikey = "test-idem-key-12345"
        
        # 1. Test successful dispatch (202 Accepted)
        res1 = await client.post(
            "/api/v1/jobs", 
            json={"type": "INGEST_PROFILE", "linked_entity": "dataset-001"},
            headers={"Idempotency-Key": ikey}
        )
        assert res1.status_code == 202
        data = res1.json()
        assert "job_id" in data
        job_id = data["job_id"]
        
        # 2. Test Idempotency Conflict (409) with same key
        res2 = await client.post(
            "/api/v1/jobs", 
            json={"type": "INGEST_PROFILE", "linked_entity": "dataset-001"},
            headers={"Idempotency-Key": ikey}
        )
        assert res2.status_code == 409
        
        # 3. Test Lifecycle Status transition logic
        update_job_status(job_id, "RUNNING")
        job = get_job(job_id)
        assert job.status == "RUNNING"
        
        update_job_status(job_id, "COMPLETED")
        job = get_job(job_id)
        assert job.status == "COMPLETED"
