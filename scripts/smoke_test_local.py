import sys
import time

import httpx

API_URL = "http://localhost:8000"


def test_flow():
    print("[1/4] Checking /health endpoint...")
    try:
        res = httpx.get(f"{API_URL}/health", timeout=5.0)
        print(f"   -> status: {res.status_code}, body: {res.json()}")
        assert res.status_code == 200
        assert res.json().get("status") == "ok"
    except Exception as e:
        print(f"ERROR: API is not running: {e}")
        sys.exit(1)

    print("\n[2/4] Checking /ready endpoint...")
    try:
        res = httpx.get(f"{API_URL}/ready", timeout=5.0)
        print(f"   -> status: {res.status_code}, body: {res.json()}")
        assert res.status_code == 200
        assert "connected" in str(res.json().get("database"))
    except Exception as e:
        print(f"ERROR: Database connection failed: {e}")
        sys.exit(1)

    print("\n[3/4] Testing Job Dispatcher & Idempotency...")
    idem_key = f"local-smoke-{int(time.time())}"

    print("   -> Dispatching INGEST_PROFILE Job (expecting 202):")
    res1 = httpx.post(
        f"{API_URL}/api/v1/jobs",
        json={"type": "INGEST_PROFILE", "linked_entity": "yellow_tripdata"},
        headers={"Idempotency-Key": idem_key},
        timeout=5.0,
    )
    print(f"      status: {res1.status_code}, body: {res1.json()}")
    assert res1.status_code == 202
    job_id = res1.json()["job_id"]
    print(f"      Parsed Job ID: {job_id}")

    print("   -> Dispatching Job again with same Idempotency-Key (expecting 409):")
    res2 = httpx.post(
        f"{API_URL}/api/v1/jobs",
        json={"type": "INGEST_PROFILE", "linked_entity": "yellow_tripdata"},
        headers={"Idempotency-Key": idem_key},
        timeout=5.0,
    )
    print(f"      status: {res2.status_code}")
    assert res2.status_code == 409

    print("\n[4/4] Polling Job Status (GET /api/v1/jobs/{job_id})...")
    max_retries = 15
    retry_count = 0
    status = "PENDING"

    while retry_count < max_retries:
        print(f"   -> Polling status (Attempt {retry_count + 1}/{max_retries})...")
        res_poll = httpx.get(f"{API_URL}/api/v1/jobs/{job_id}", timeout=5.0)
        print(f"      Response: {res_poll.json()}")
        status = res_poll.json()["status"]
        if status in ["SUCCEEDED", "COMPLETED"]:
            print("   -> Job succeeded!")
            break
        elif status == "FAILED":
            print(f"ERROR: Job execution failed! Error: {res_poll.json().get('error')}")
            sys.exit(1)

        retry_count += 1
        time.sleep(2)

    if status not in ["SUCCEEDED", "COMPLETED"]:
        print("ERROR: Job timed out or failed to reach success status!")
        sys.exit(1)

    print("\nALL SMOKE TESTS PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    test_flow()
