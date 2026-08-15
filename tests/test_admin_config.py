import pytest


async def login(client, username: str, password: str):
    response = await client.post("/api/v1/session", json={"username": username, "password": password})
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"]}


@pytest.mark.asyncio
async def test_admin_provisions_user_and_grants_dataset_access(client):
    admin_headers = await login(client, "admin", "admin")

    created = await client.post(
        "/api/v1/admin/users",
        headers=admin_headers,
        json={"username": "analyst", "display_name": "Local Analyst", "password": "analyst-pass", "role": "USER"},
    )
    assert created.status_code == 201
    assert "password_hash" not in created.json()

    analyst_headers = await login(client, "analyst", "analyst-pass")
    assert (await client.get("/api/v1/datasets", headers=analyst_headers)).json() == []

    admin_headers = await login(client, "admin", "admin")
    granted = await client.put(
        "/api/v1/admin/datasets/dataset-nyc-yellow-taxi-50k/access/analyst",
        headers=admin_headers,
        json={"access_level": "READ"},
    )
    assert granted.status_code == 200
    assert granted.json()["access_level"] == "READ"

    analyst_headers = await login(client, "analyst", "analyst-pass")
    assert len((await client.get("/api/v1/datasets", headers=analyst_headers)).json()) == 1
    denied = await client.post(
        "/api/v1/datasets/dataset-nyc-yellow-taxi-50k/ingestions",
        headers={**analyst_headers, "Idempotency-Key": "analyst-ingest"},
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "ROLE_FORBIDDEN"


@pytest.mark.asyncio
async def test_configuration_and_delete_follow_proposal_state(client):
    headers = await login(client, "steward", "steward")
    created = await client.post(
        "/api/v1/datasets/dataset-nyc-yellow-taxi-50k/rule-proposals/manual",
        headers=headers,
        json={
            "title": "Manual distance rule",
            "description": "A reviewable local rule.",
            "severity": "MEDIUM",
            "rule": {"type": "numeric_range", "column": "trip_distance", "min_value": 0},
        },
    )
    assert created.status_code == 200
    proposal_id = created.json()["id"]
    assert created.json()["status"] == "PROPOSED"

    assert (await client.delete(f"/api/v1/rule-proposals/{proposal_id}", headers=headers)).status_code == 204

    created = await client.post(
        "/api/v1/datasets/dataset-nyc-yellow-taxi-50k/rule-proposals/manual",
        headers=headers,
        json={
            "title": "Approved distance rule",
            "description": "Configuration test.",
            "severity": "MEDIUM",
            "rule": {"type": "numeric_range", "column": "trip_distance", "min_value": 0},
        },
    )
    proposal_id = created.json()["id"]
    approved = await client.patch(f"/api/v1/rule-proposals/{proposal_id}", headers=headers, json={"action": "approve"})
    assert approved.status_code == 200
    configuration = await client.patch(
        f"/api/v1/rule-proposals/{proposal_id}/configuration",
        headers=headers,
        json={"execution_status": "PAUSED", "schedule_frequency": "MANUAL", "timezone": "UTC"},
    )
    assert configuration.status_code == 200
    assert configuration.json()["execution_status"] == "PAUSED"

    blocked = await client.post(
        "/api/v1/dq-runs",
        headers={**headers, "Idempotency-Key": "paused-rule-run"},
        json={"rule_ids": [proposal_id]},
    )
    assert blocked.status_code == 422
    assert blocked.json()["code"] == "ACTIVE_RULES_REQUIRED"
