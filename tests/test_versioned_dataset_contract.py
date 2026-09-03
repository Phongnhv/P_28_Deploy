import json

import pandas as pd
import pytest
from sqlalchemy.orm import Session

from src.agents.nodes.dbt_validation import validate_dbt_yaml_structure
from src.agents.nodes.persist_report_node import aggregate_graph2_status
from src.agents.nodes.test_generator_node import build_versioned_generated_tests, generate_versioned_dbt_test_yaml
from src.models.database import (
    DatasetAccessModel,
    DatasetModel,
    DatasetVersionModel,
    ProfileRunSnapshotModel,
    RuleProposalModel,
    UserAccountModel,
    WorkspaceMembershipModel,
    WorkspaceModel,
)
from src.services.dashboard_agent_workflow import get_dataset_rule_policy
from src.services.rule_store import get_engine, save_proposed_rules
from src.services.versioned_dataset import (
    DatasetContractError,
    canonical_schema_manifest,
    execute_rules_frame,
    safe_source_object_key,
    schema_contract_hash,
    schema_hash,
    validate_rule_spec,
)


def test_schema_hash_is_stable_and_object_key_is_versioned():
    frame = pd.DataFrame({"order_id": ["a", "b"], "amount": [1.0, 2.0]})
    schema = canonical_schema_manifest(frame)
    assert schema_hash(schema) == schema_hash(json.loads(json.dumps(schema)))
    key = safe_source_object_key("ws-1", "orders", "dv-1", "abc123", "orders.csv")
    assert key == "datasets/ws-1/orders/versions/dv-1/abc123/orders.csv"
    with pytest.raises(DatasetContractError):
        safe_source_object_key("ws/escape", "orders", "dv-1", "abc123", "orders.csv")


def test_schema_contract_hash_ignores_pandas_physical_string_aliases():
    schema = canonical_schema_manifest(pd.DataFrame({"customer": ["a", "b"], "amount": [1, 2]}))
    pandas_3_manifest = [
        {**item, "physical_type": "object" if item["physical_type"] != "object" else "str"}
        if item["logical_type"] == "string"
        else item
        for item in schema
    ]
    assert schema_hash(schema) != schema_hash(pandas_3_manifest)
    assert schema_contract_hash(schema) == schema_contract_hash(pandas_3_manifest)
    invalid = [{**pandas_3_manifest[0], "logical_type": "number"}, pandas_3_manifest[1]]
    assert schema_contract_hash(schema) != schema_contract_hash(invalid)


def test_unknown_dataset_never_receives_taxi_policy_without_schema():
    assert get_dataset_rule_policy("orders-unknown") is None


def test_rule_errors_are_isolated_and_follow_graph2_status_contract():
    frame = pd.DataFrame({"order_id": ["a", "b"], "amount": [1.0, -2.0]})
    results = execute_rules_frame(frame, [
        {"rule_id": "bad", "type": "not_null", "column": "missing"},
        {"rule_id": "good", "type": "numeric_range", "column": "amount", "min_value": 0},
    ])
    assert [row["status"] for row in results] == ["ERROR", "FAIL"]
    assert aggregate_graph2_status(results) == "PARTIAL"
    assert aggregate_graph2_status([{"status": "ERROR"}]) == "FAILED"
    assert aggregate_graph2_status([{"status": "PASS"}, {"status": "FAIL"}]) == "SUCCEEDED"


def test_versioned_dbt_artifact_is_schema_driven_and_non_taxi():
    schema = canonical_schema_manifest(pd.DataFrame({"order_id": ["a"], "amount": [1.0]}))
    content = generate_versioned_dbt_test_yaml(
        schema,
        [{"table_name": "version_source", "column": "order_id", "rule_type": "NOT_NULL"}],
        schema_hash_value=schema_hash(schema),
        source_checksum="sha-orders",
    )
    parsed = validate_dbt_yaml_structure(content)
    assert parsed["models"][0]["name"] == "version_source"
    assert "trips_canonical" not in content
    assert "stg_trips" not in content
    assert "order_id" in content and "amount" in content


def test_versioned_generated_test_counter_tracks_adapter_checks():
    generated = build_versioned_generated_tests([
        {"rule_id": "not-null", "rule_type": "NOT_NULL", "table_name": "version_source", "column": "order_id"},
        {"rule_id": "row-count", "rule_type": "ROW_COUNT", "table_name": "version_source", "column": "_table"},
    ])
    assert len(generated) == 2
    assert {item["execution_mode"] for item in generated} == {"versioned_source_adapter"}


def test_freshness_parse_failure_is_execution_error_not_data_failure():
    result = execute_rules_frame(
        pd.DataFrame({"updated_at": ["not-a-timestamp"]}),
        [{"rule_id": "fresh", "type": "freshness", "column": "updated_at"}],
    )[0]
    assert result["status"] == "ERROR"
    assert result["failed_count"] == 0
    assert aggregate_graph2_status([result]) == "FAILED"


def test_versioned_regex_rule_is_validated_and_executed_with_safe_guardrails():
    frame = pd.DataFrame({"email": ["alice@example.com", "not-an-email", None]})
    spec = {
        "rule_id": "email-format",
        "type": "REGEX_FORMAT",
        "column": "email",
        "regex": r"^[^@]+@[^@]+\.[^@]+$",
    }
    normalized = validate_rule_spec(spec, ["email"])
    assert normalized["rule_type"] == "REGEX_FORMAT"
    result = execute_rules_frame(frame, [spec])[0]
    assert result["status"] == "FAIL"
    assert result["failed_count"] == 1


def test_saved_versioned_rule_spec_preserves_execution_parameters(test_db):
    rules = [
        {
            "rule_id": "email-regex",
            "rule_type": "REGEX_FORMAT",
            "column": "email",
            "parameters": {"regex": r"^[^@]+@[^@]+$"},
        },
        {
            "rule_id": "freshness",
            "rule_type": "FRESHNESS",
            "column": "updated_at",
            "parameters": {"max_age_hours": 12},
        },
        {
            "rule_id": "row-count",
            "rule_type": "ROW_COUNT",
            "column": None,
            "parameters": {"min_row_count": 10},
        },
    ]
    assert save_proposed_rules("versioned-param-run", "versioned-dataset", rules) == 3

    with Session(test_db) as db:
        proposals = {
            row.id: json.loads(row.rule_spec)
            for row in db.query(RuleProposalModel).filter(
                RuleProposalModel.id.in_([rule["rule_id"] for rule in rules])
            )
        }

    assert proposals["email-regex"]["regex"] == r"^[^@]+@[^@]+$"
    assert proposals["freshness"]["max_age_hours"] == 12
    assert proposals["row-count"]["min_row_count"] == 10


@pytest.mark.parametrize(
    ("frame", "operator", "expected_status", "expected_kind"),
    [
        (pd.DataFrame({"total": ["10.0", "4.0"], "monthly": [10.0, 5.0]}), ">=", "FAIL", "numeric"),
        (pd.DataFrame({"total": [2, 3], "monthly": [2.0, 2.5]}), ">=", "PASS", "numeric"),
        (pd.DataFrame({"start": ["2025-01-01T00:00:00Z"], "end": ["2025-01-01T01:00:00+00:00"]}), "<=", "PASS", "datetime"),
        (pd.DataFrame({"left": ["same", "other"], "right": ["same", "value"]}), "=", "FAIL", "string"),
    ],
)
def test_cross_field_comparison_normalizes_schema_types(frame, operator, expected_status, expected_kind):
    left, right = list(frame.columns)
    result = execute_rules_frame(frame, [{
        "rule_id": "cross",
        "type": "CROSS_FIELD_COMPARISON",
        "column": left,
        "parameters": {"target_column": right, "operator": operator},
    }])[0]
    assert result["status"] == expected_status
    assert result["comparison_kind"] == expected_kind
    assert "TypeError" not in str(result.get("error"))


def test_cross_field_parse_failure_is_a_degraded_violation_not_runner_error():
    result = execute_rules_frame(
        pd.DataFrame({"total": ["not-a-number", None, "4"], "monthly": [2.0, 2.0, 5.0]}),
        [{"rule_id": "cross", "type": "CROSS_FIELD_COMPARISON", "column": "total", "parameters": {"target_column": "monthly", "operator": ">="}}],
    )[0]
    assert result["status"] == "FAIL"
    assert result["parse_failure_count"] == 1
    assert result["null_pair_count"] == 1
    assert result["execution_health"] == "DEGRADED"


@pytest.mark.asyncio
async def test_changed_upload_creates_separate_datasets_with_one_source_each(client, monkeypatch, tmp_path):
    from unittest.mock import AsyncMock

    import src.services.versioned_dataset as versioned_dataset
    from src.config import get_settings

    monkeypatch.setattr(get_settings(), "app_env", "test")
    monkeypatch.setattr(versioned_dataset, "_local_storage_root", lambda: tmp_path / "source-artifacts")
    with Session(get_engine()) as db:
        account = db.query(UserAccountModel).filter_by(username="steward").first()
        db.add(WorkspaceModel(id="ws-orders", name="Orders", created_by=account.id))
        db.add(WorkspaceMembershipModel(id="wm-orders", workspace_id="ws-orders", user_id=account.id, role="STEWARD"))
        db.commit()
    login = await client.post("/api/v1/session", json={"username": "steward", "password": "steward"})
    csrf = login.json()["csrf_token"]
    headers = {"X-CSRF-Token": csrf, "Idempotency-Key": "orders-v1"}
    first = await client.post(
        "/api/v1/workspaces/ws-orders/datasets/import",
        headers=headers,
        data={"dataset_id": "orders", "dataset_name": "Orders"},
        files={"file": ("orders.csv", b"order_id,amount\na,1\nb,2\n", "text/csv")},
    )
    assert first.status_code == 202, first.text
    version1 = first.json()["version"]
    second = await client.post(
        "/api/v1/workspaces/ws-orders/datasets/import",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": "orders-v2"},
        data={"dataset_id": "orders", "dataset_name": "Orders"},
        files={"file": ("orders-v2.csv", b"order_id,amount,status\na,1,paid\n", "text/csv")},
    )
    assert second.status_code == 202, second.text
    version2 = second.json()["version"]
    assert version1["id"] != version2["id"]
    assert version1["version_number"] == 1
    assert version2["version_number"] == 1
    second_id = second.json()["dataset"]["id"]
    assert second_id != "orders"
    profile = await client.get("/api/v1/datasets/orders/profile")
    assert profile.status_code == 200, profile.text
    assert {column["name"] for column in profile.json()["columns"]} == {"order_id", "amount"}
    assert profile.json()["row_count"] == 2
    second_profile = await client.get(f"/api/v1/datasets/{second_id}/profile")
    assert second_profile.status_code == 200
    assert second_profile.json()["row_count"] == 1
    assert {column["name"] for column in second_profile.json()["columns"]} == {"order_id", "amount", "status"}
    with Session(get_engine()) as db:
        versions = db.query(DatasetVersionModel).filter_by(dataset_id="orders").order_by(DatasetVersionModel.version_number).all()
        assert [version.status for version in versions] == ["READY"]
        assert db.query(DatasetVersionModel).filter_by(dataset_id=second_id).count() == 1
        assert db.get(ProfileRunSnapshotModel, f"profile-{version1['id']}").status == "COMPLETED"
        assert db.get(ProfileRunSnapshotModel, f"profile-{version2['id']}").status == "COMPLETED"

    monkeypatch.setattr(get_settings(), "agent_mode", "graph")
    monkeypatch.setattr(get_settings(), "openai_api_key", "test-key")
    monkeypatch.setattr("src.services.graph1_workflow.execute_graph1_run", AsyncMock())
    graph1 = await client.post(
        f"/api/v1/datasets/{second_id}/graph1-runs",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": "orders-graph1-v2"},
    )
    assert graph1.status_code == 202, graph1.text
    assert graph1.json()["dataset_version_id"] == version2["id"]
    assert graph1.json()["profile_run_id"] == f"profile-{version2['id']}"


@pytest.mark.asyncio
async def test_generic_uploaded_rows_use_schema_without_supabase_table_assumption(client, monkeypatch, tmp_path):
    """A generic import must be queryable even when Supabase is configured."""
    monkeypatch.chdir(tmp_path)
    upload_dir = tmp_path / "data" / "uploads"
    upload_dir.mkdir(parents=True)
    (upload_dir / "orders.csv").write_text("order_id,amount,status\na-1,12.5,paid\na-2,4.0,pending\n", encoding="utf-8")

    with Session(get_engine()) as db:
        account = db.query(UserAccountModel).filter_by(username="steward").first()
        db.add(DatasetModel(
            id="orders",
            name="Orders",
            description="Generic import",
            status="PROFILE_READY",
            row_count=2,
            source_label="orders.csv",
            manifest_version="import-v1",
            checksum="sha-orders",
        ))
        db.add(DatasetAccessModel(
            id="access-orders-steward",
            dataset_id="orders",
            username=account.username,
            access_level="MANAGE",
            granted_by=account.username,
        ))
        db.commit()

    login = await client.post("/api/v1/session", json={"username": "steward", "password": "steward"})
    csrf = login.json()["csrf_token"]
    listed = await client.get("/api/v1/datasets")
    dataset = next(item for item in listed.json() if item["id"] == "orders")
    assert dataset["data_explorer_available"] is True

    rows = await client.get(
        "/api/v1/datasets/orders/rows",
        headers={"X-CSRF-Token": csrf},
        params={"sort_by": "amount", "sort_direction": "desc", "limit": 10},
    )
    assert rows.status_code == 200, rows.text
    payload = rows.json()
    assert [column["name"] for column in payload["schema"]] == ["order_id", "amount", "status"]
    assert payload["rows"][0] == {"order_id": "a-1", "amount": 12.5, "status": "paid"}
    assert "source_row_id" not in payload["rows"][0]

    invalid_filter = await client.get(
        "/api/v1/datasets/orders/rows",
        headers={"X-CSRF-Token": csrf},
        params={"filter_column": "missing", "filter_value": "x"},
    )
    assert invalid_filter.status_code == 422
    assert invalid_filter.json()["code"] == "INVALID_DATASET_FILTER"
