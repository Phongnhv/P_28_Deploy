import pytest
from sqlalchemy.orm import Session

from src.models.database import ColumnProfileModel, DatasetModel, ProfileModel, RuleVersionModel
from src.services.job_runner import run_propose_rules
from src.services.rule_store import get_engine


def seed_completed_profile(session: Session, dataset_id: str) -> None:
    dataset = session.query(DatasetModel).filter(DatasetModel.id == dataset_id).first()
    assert dataset is not None
    dataset.status = "PROFILE_READY"
    session.add(
        ProfileModel(
            dataset_id=dataset_id,
            row_count=50_000,
            completeness_score=99.5,
            validity_score=98.0,
            duplicate_rate=0.5,
            evidence_keys='["profile.row_count"]',
        )
    )
    for name, data_type, minimum, maximum in [
        ("vendor_id", "string", None, None),
        ("trip_distance", "float", -1.0, 100.0),
        ("payment_type", "string", None, None),
        ("pickup_at", "string", None, None),
        ("dropoff_at", "string", None, None),
        ("passenger_count", "integer", 0.0, 8.0),
    ]:
        session.add(
            ColumnProfileModel(
                profile_dataset_id=dataset_id,
                name=name,
                data_type=data_type,
                null_rate=0.0,
                distinct_count=50_000 if name == "vendor_id" else 6,
                min_value=minimum,
                max_value=maximum,
                sample_value="not-used-by-agent",
            )
        )
    session.commit()


@pytest.mark.asyncio
async def test_proposal_review_transitions(client):
    # Log in as steward
    login_res = await client.post("/api/v1/session", json={"username": "steward", "password": "steward"})
    assert login_res.status_code == 200
    csrf_token = login_res.json()["csrf_token"]
    headers = {"X-CSRF-Token": csrf_token}

    # 1. Trigger rule proposals job and run it to seed proposals
    job_headers = {"X-CSRF-Token": csrf_token, "Idempotency-Key": "proposals-key"}
    # Force profile status ready in DB first
    with Session(get_engine()) as session:
        seed_completed_profile(session, "dataset-nyc-yellow-taxi-50k")

    job_res = await client.post("/api/v1/datasets/dataset-nyc-yellow-taxi-50k/rule-proposals", headers=job_headers)
    assert job_res.status_code == 202
    job_id = job_res.json()["job_id"]
    run_propose_rules(job_id, "dataset-nyc-yellow-taxi-50k")

    # 2. List proposals and verify they are all PROPOSED
    list_res = await client.get("/api/v1/rule-proposals?dataset_id=dataset-nyc-yellow-taxi-50k")
    assert list_res.status_code == 200
    proposals = list_res.json()
    assert len(proposals) == 5
    for p in proposals:
        assert p["status"] == "PROPOSED"
    range_id = next(p["id"] for p in proposals if p["rule"]["type"] == "numeric_range")
    not_null_id = next(p["id"] for p in proposals if p["rule"]["type"] == "not_null")
    accepted_values_id = next(p["id"] for p in proposals if p["rule"]["type"] == "accepted_values")

    # 3. Approve the generated range proposal.
    approve_res = await client.patch(
        f"/api/v1/rule-proposals/{range_id}", headers=headers, json={"action": "approve"}
    )
    assert approve_res.status_code == 200
    assert approve_res.json()["status"] == "APPROVED"

    # Verify rule version is created in DB
    with Session(get_engine()) as session:
        rv = session.query(RuleVersionModel).filter(RuleVersionModel.rule_proposal_id == range_id).first()
        assert rv is not None
        assert rv.status == "APPROVED"

    # 4. Reject the generated not-null proposal.
    reject_res = await client.patch(
        f"/api/v1/rule-proposals/{not_null_id}", headers=headers, json={"action": "reject"}
    )
    assert reject_res.status_code == 200
    assert reject_res.json()["status"] == "REJECTED"

    # Verify rule version is deleted/absent in DB
    with Session(get_engine()) as session:
        rv = session.query(RuleVersionModel).filter(RuleVersionModel.rule_proposal_id == not_null_id).first()
        assert rv is None

    # 5. Edit the generated accepted-values proposal.
    edit_res = await client.patch(
        f"/api/v1/rule-proposals/{accepted_values_id}",
        headers=headers,
        json={
            "action": "edit",
            "severity": "LOW",
            "rule": {"type": "accepted_values", "column": "payment_type", "allowed_values": ["1", "2"]},
        },
    )
    assert edit_res.status_code == 200
    assert edit_res.json()["status"] == "APPROVED"
    assert edit_res.json()["severity"] == "LOW"

    # Verify edited rule version exists in DB with new parameters
    with Session(get_engine()) as session:
        rv = (
            session.query(RuleVersionModel)
            .filter(RuleVersionModel.rule_proposal_id == accepted_values_id)
            .first()
        )
        assert rv is not None
        import json

        spec = json.loads(rv.rule_spec)
        assert spec["allowed_values"] == ["1", "2"]


@pytest.mark.asyncio
async def test_manual_rule_creation(client):
    # Log in
    login_res = await client.post("/api/v1/session", json={"username": "steward", "password": "steward"})
    assert login_res.status_code == 200
    csrf_token = login_res.json()["csrf_token"]
    headers = {"X-CSRF-Token": csrf_token}

    # Create manual rule
    res = await client.post(
        "/api/v1/datasets/dataset-nyc-yellow-taxi-50k/rule-proposals/manual",
        headers=headers,
        json={
            "title": "Manual clean trip distance",
            "description": "Clean trip distance threshold",
            "severity": "MEDIUM",
            "rule": {"type": "numeric_range", "column": "trip_distance", "min_value": 0.1, "max_value": 100.0},
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "PROPOSED"
    assert data["rule"]["type"] == "numeric_range"
    assert data["rule"]["min_value"] == 0.1
    assert data["rule"]["max_value"] == 100.0
