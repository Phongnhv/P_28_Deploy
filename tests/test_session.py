import pytest
from fastapi import status


@pytest.mark.asyncio
async def test_login_success(client):
    # Test successful login with steward credentials
    response = await client.post("/api/v1/session", json={"username": "steward", "password": "steward"})
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["username"] == "steward"
    assert data["role"] == "STEWARD"
    assert "csrf_token" in data
    assert "expires_at" in data

    # Check HttpOnly cookie is set
    assert "session_id" in response.cookies

@pytest.mark.asyncio
async def test_login_invalid_credentials(client):
    # Test login fails with invalid credentials
    response = await client.post("/api/v1/session", json={"username": "steward", "password": "wrongpassword"})
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["code"] == "UNAUTHORIZED"

@pytest.mark.asyncio
async def test_csrf_token_required_for_mutating_requests(client):
    # Log in
    login_res = await client.post("/api/v1/session", json={"username": "steward", "password": "steward"})
    assert login_res.status_code == 200

    # Attempt mutation WITHOUT CSRF header -> should fail with 422
    mutate_res = await client.post("/api/v1/datasets/dataset-nyc-yellow-taxi-50k/ingestions")
    assert mutate_res.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert mutate_res.json()["code"] == "CSRF_INVALID"

@pytest.mark.asyncio
async def test_csrf_token_mismatch_fails(client):
    # Log in
    login_res = await client.post("/api/v1/session", json={"username": "steward", "password": "steward"})
    assert login_res.status_code == 200

    # Attempt mutation WITH WRONG CSRF header -> should fail with 422
    headers = {"X-CSRF-Token": "bad-csrf-token", "Idempotency-Key": "test-key"}
    mutate_res = await client.post(
        "/api/v1/datasets/dataset-nyc-yellow-taxi-50k/ingestions",
        headers=headers
    )
    assert mutate_res.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert mutate_res.json()["code"] == "CSRF_INVALID"

@pytest.mark.asyncio
async def test_role_enforcement_read_only_user(client):
    # Log in as USER
    login_res = await client.post("/api/v1/session", json={"username": "user", "password": "user"})
    assert login_res.status_code == 200
    csrf_token = login_res.json()["csrf_token"]

    # USER is allowed to call GET
    get_res = await client.get("/api/v1/datasets")
    assert get_res.status_code == 200

    # USER is NOT allowed to trigger Ingestion (returns 403)
    headers = {"X-CSRF-Token": csrf_token, "Idempotency-Key": "test-key"}
    post_res = await client.post(
        "/api/v1/datasets/dataset-nyc-yellow-taxi-50k/ingestions",
        headers=headers
    )
    assert post_res.status_code == status.HTTP_403_FORBIDDEN
    assert post_res.json()["code"] == "FORBIDDEN"

@pytest.mark.asyncio
async def test_logout_clears_cookie_and_session(client):
    # Log in
    login_res = await client.post("/api/v1/session", json={"username": "steward", "password": "steward"})
    assert login_res.status_code == 200
    csrf_token = login_res.json()["csrf_token"]

    # Logout
    logout_res = await client.delete("/api/v1/session")
    assert logout_res.status_code == status.HTTP_204_NO_CONTENT

    # Subsequent mutating request should fail with 401 since session is cleared
    headers = {"X-CSRF-Token": csrf_token, "Idempotency-Key": "test-key"}
    post_res = await client.post(
        "/api/v1/datasets/dataset-nyc-yellow-taxi-50k/ingestions",
        headers=headers
    )
    assert post_res.status_code == status.HTTP_401_UNAUTHORIZED
