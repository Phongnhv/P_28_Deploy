import urllib.request
import json
import time

API_URL = "http://localhost:8000"

def run_smoke_test():
    print("[1/4] Checking /health endpoint...")
    try:
        with urllib.request.urlopen(f"{API_URL}/health") as response:
            health = json.loads(response.read().decode())
            print(f"   -> {health}")
            assert health.get("status") == "ok", "API health status is not ok"
    except Exception as e:
        print(f"[-] API is not running: {e}")
        return False

    print("\n[2/4] Checking /ready endpoint...")
    try:
        with urllib.request.urlopen(f"{API_URL}/ready") as response:
            ready = json.loads(response.read().decode())
            print(f"   -> {ready}")
            assert ready.get("status") == "ready", "Database is not ready"
    except Exception as e:
        print(f"[-] Database connection failed: {e}")
        return False

    # Establish authenticated session
    print("\n[Auth] Logging in as admin to establish session...")
    login_payload = json.dumps({
        "username": "admin",
        "password": "admin"
    }).encode('utf-8')
    
    login_req = urllib.request.Request(
        f"{API_URL}/api/v1/session",
        data=login_payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    
    session_cookie = None
    try:
        with urllib.request.urlopen(login_req) as response:
            headers = response.info()
            cookie_header = headers.get("Set-Cookie")
            if cookie_header:
                # Parse session_id cookie
                parts = cookie_header.split(";")
                for part in parts:
                    if part.strip().startswith("session_id="):
                        session_cookie = part.strip()
                        break
            login_res = json.loads(response.read().decode())
            print(f"   -> Logged in successfully: {login_res}")
            print(f"   -> Session Cookie: {session_cookie}")
    except Exception as e:
        print(f"[-] Login failed: {e}")
        return False

    if not session_cookie:
        print("[-] Failed to retrieve session cookie from login response.")
        return False

    print("\n[3/4] Testing Job Dispatcher & Idempotency...")
    idempotency_key = f"local-smoke-{int(time.time())}"
    job_payload = json.dumps({
        "type": "INGEST_PROFILE",
        "linked_entity": "yellow_tripdata"
    }).encode('utf-8')

    req = urllib.request.Request(
        f"{API_URL}/api/v1/jobs",
        data=job_payload,
        headers={
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req) as response:
            job_res = json.loads(response.read().decode())
            print(f"   -> Job dispatched: {job_res}")
            job_id = job_res.get("job_id")
            assert job_id, "Failed to parse job_id from response"
    except Exception as e:
        print(f"[-] Failed to dispatch job: {e}")
        return False

    # Test Idempotency (expects 409 Conflict)
    print("   -> Dispatching Job again with same Idempotency-Key...")
    req_duplicate = urllib.request.Request(
        f"{API_URL}/api/v1/jobs",
        data=job_payload,
        headers={
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key
        },
        method="POST"
    )
    try:
        urllib.request.urlopen(req_duplicate)
        print("[-] Idempotency check failed (expected 409, got success)!")
        return False
    except urllib.error.HTTPError as e:
        print(f"      HTTP Status: {e.code}")
        if e.code == 409:
            print("   -> Idempotency check passed (409 Conflict received)!")
        else:
            print(f"[-] Idempotency check failed (expected 409, got {e.code})!")
            return False

    print(f"\n[4/4] Polling Job Status (GET /api/v1/jobs/{job_id})...")
    max_retries = 15
    retry_count = 0
    status = "PENDING"

    while retry_count < max_retries:
        print(f"   -> Polling status (Attempt {retry_count + 1}/{max_retries})...")
        poll_req = urllib.request.Request(
            f"{API_URL}/api/v1/jobs/{job_id}",
            headers={"Cookie": session_cookie}
        )
        try:
            with urllib.request.urlopen(poll_req) as response:
                poll_res = json.loads(response.read().decode())
                print(f"      Response: {poll_res}")
                status = poll_res.get("status")
                if status in ("SUCCEEDED", "COMPLETED"):
                    print("   -> Job succeeded!")
                    break
                elif status == "FAILED":
                    print("[-] Job execution failed!")
                    return False
        except Exception as e:
            print(f"[-] Error polling job: {e}")
            return False

        retry_count += 1
        time.sleep(2)

    if status not in ("SUCCEEDED", "COMPLETED"):
        print("[-] Job timed out or failed to reach success status!")
        return False

    print("\n[+] ALL SMOKE TESTS PASSED SUCCESSFULLY!")
    return True

if __name__ == "__main__":
    run_smoke_test()
