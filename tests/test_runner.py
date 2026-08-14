import json

import pytest
from sqlalchemy.orm import Session

from src.models.database import RuleConfigurationModel, RuleProposalModel, RuleVersionModel
from src.services.job_runner import compile_rule_to_sql, run_dq_checks, run_ingest_profile
from src.services.rule_store import get_engine


@pytest.fixture
def allowlist():
    return {
        'source_row_id', 'vendor_id', 'pickup_at', 'dropoff_at', 'passenger_count',
        'trip_distance', 'payment_type', 'fare_amount'
    }

def test_compiler_not_null(allowlist):
    sql = compile_rule_to_sql("not_null", {"column": "vendor_id"}, allowlist)
    assert 'IS NULL' in sql
    assert '"vendor_id"' in sql

def test_compiler_numeric_range(allowlist):
    sql = compile_rule_to_sql("numeric_range", {"column": "fare_amount", "min_value": 0.0, "max_value": 150.0}, allowlist)
    assert 'fare_amount' in sql
    assert ':min_value' in sql
    assert ':max_value' in sql

def test_compiler_accepted_values(allowlist):
    sql = compile_rule_to_sql("accepted_values", {"column": "payment_type", "allowed_values": ["1", "2"]}, allowlist)
    assert 'NOT IN (:val_0, :val_1)' in sql

def test_compiler_cross_field(allowlist):
    sql = compile_rule_to_sql("cross_field_comparison", {"columns": ["pickup_at", "dropoff_at"], "operator": "<="}, allowlist)
    assert 'NOT ("pickup_at" <= "dropoff_at")' in sql

def test_compiler_duplicate_fingerprint(allowlist):
    sql = compile_rule_to_sql("duplicate_fingerprint", {"fingerprint_columns": ["vendor_id", "pickup_at"]}, allowlist)
    assert 'GROUP BY' in sql
    assert 'HAVING COUNT(*) > 1' in sql

def test_compiler_sql_injection_rejection(allowlist):
    # Reject bad column
    with pytest.raises(ValueError, match="Unauthorized column access"):
        compile_rule_to_sql("not_null", {"column": "bad_column_name"}, allowlist)

    # Reject injection characters in column name
    with pytest.raises(ValueError):
        compile_rule_to_sql("not_null", {"column": "vendor_id; DROP TABLE trips;"}, allowlist)

    # Reject injection characters in accepted values
    with pytest.raises(ValueError):
        compile_rule_to_sql("accepted_values", {"column": "payment_type", "allowed_values": ["1'; DROP TABLE trips;"]}, allowlist)

    # Reject injection characters in cross field operator
    with pytest.raises(ValueError):
        compile_rule_to_sql("cross_field_comparison", {"columns": ["pickup_at", "dropoff_at"], "operator": "<=; DROP TABLE;"}, allowlist)

@pytest.mark.asyncio
async def test_dq_run_and_failed_ids_capped_at_20(client):
    # Log in
    login_res = await client.post("/api/v1/session", json={"username": "steward", "password": "steward"})
    assert login_res.status_code == 200
    csrf_token = login_res.json()["csrf_token"]
    # 1. Ingest dataset first to populate source_rows (required to execute queries)
    ingest_headers = {"X-CSRF-Token": csrf_token, "Idempotency-Key": "dq-run-ingest"}
    ingest_res = await client.post("/api/v1/datasets/dataset-nyc-yellow-taxi-50k/ingestions", headers=ingest_headers)
    assert ingest_res.status_code == 202
    run_ingest_profile(ingest_res.json()["job_id"], "dataset-nyc-yellow-taxi-50k")

    # 2. Write approved rule version to DB (manually or via endpoint)
    # We will write a rule version that is guaranteed to fail on some rows, e.g., trip_distance >= 0
    # but edit it to fail, say trip_distance > 50 to get many failed row ids to check the cap!
    with Session(get_engine()) as session:
        # Create a rule proposal and approve it
        prop = db_create_proposal(session, "dataset-nyc-yellow-taxi-50k")
        rv = RuleVersionModel(
            id="rv_test-run-cap",
            rule_proposal_id=prop.id,
            dataset_id="dataset-nyc-yellow-taxi-50k",
            rule_spec=json.dumps({"type": "numeric_range", "column": "trip_distance", "min_value": 50.0}), # most distances are < 50
            status="APPROVED",
            version=1
        )
        session.add(rv)
        session.add(
            RuleConfigurationModel(
                rule_proposal_id=prop.id,
                execution_status="ACTIVE",
                schedule_frequency="MANUAL",
                timezone="UTC",
            )
        )
        session.commit()

    # 3. Trigger DQ Run
    dq_headers = {"X-CSRF-Token": csrf_token, "Idempotency-Key": "dq-run-trigger"}
    dq_res = await client.post(
        "/api/v1/dq-runs",
        headers=dq_headers,
        json={"rule_ids": ["rv_test-run-cap"]}
    )
    assert dq_res.status_code == 202
    job_id = dq_res.json()["job_id"]
    run_id = dq_res.json()["run_id"]

    # 4. Execute checks
    run_dq_checks(job_id, run_id)

    # 5. Verify results are saved and failed_row_ids is capped at 20
    results_res = await client.get(f"/api/v1/dq-runs/{run_id}/results")
    assert results_res.status_code == 200
    results = results_res.json()
    assert len(results) == 1
    res = results[0]
    assert res["status"] == "FAIL"
    assert len(res["failed_row_ids"]) <= 20
    assert res["failed_count"] > 20 # there are many trips with distance < 50
    with Session(get_engine()) as session:
        configuration = session.get(RuleConfigurationModel, "test-run-cap")
        assert configuration is not None
        assert configuration.last_run_at is not None

def db_create_proposal(session: Session, dataset_id: str) -> RuleProposalModel:
    prop = RuleProposalModel(
        id="test-run-cap",
        dataset_id=dataset_id,
        title="Check distance cap",
        description="Clean distance threshold check",
        severity="MEDIUM",
        status="APPROVED",
        rule_type="numeric_range",
        rule_spec=json.dumps({"type": "numeric_range", "column": "trip_distance", "min_value": 50.0}),
        evidence_refs=json.dumps(["manual"]),
        evidence_summary="manual distance check",
        confidence=1.0,
        model_name="test"
    )
    session.add(prop)
    session.commit()
    return prop
