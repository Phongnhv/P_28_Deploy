"""Unit & Integration tests for Data Services, Active Rules Registry & Publishing Flow."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

import src.services.rule_store as rule_store_module
from src.main import app
from src.services.rule_store import (
    create_run,
    deactivate_rule,
    get_active_rules,
    init_db,
    publish_approved_rules,
    review_rule,
    save_proposed_rules,
)


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    """Sử dụng SQLite file tạm cho test suite."""
    db_file = tmp_path / "test_redesign.db"
    sqlite_url = f"sqlite:///{db_file}"
    test_engine = create_engine(
        sqlite_url,
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(rule_store_module, "_engine", test_engine)
    init_db()
    yield test_engine


def test_publish_approved_rules_workflow():
    """Kiểm tra quy trình duyệt rule -> publish vào Active Ruleset."""
    run_id = f"test_run_{uuid.uuid4().hex[:8]}"
    dataset_id = "yellow_tripdata"

    create_run(run_id, dataset_id)

    mock_rules = [
        {
            "rule_id": "yellow_tripdata.fare_amount.NOT_NULL",
            "table_name": "yellow_tripdata",
            "column": "fare_amount",
            "rule_type": "NOT_NULL",
            "parameters": {},
            "confidence_score": 1.0,
            "severity": "CRITICAL",
            "dimension": "COMPLETENESS",
            "rule_description": "Cước không được rỗng",
            "ai_reasoning": "Bắt buộc",
        },
        {
            "rule_id": "yellow_tripdata.fare_amount.RANGE",
            "table_name": "yellow_tripdata",
            "column": "fare_amount",
            "rule_type": "RANGE",
            "parameters": {"min": 0.0, "max": 100.0},
            "confidence_score": 0.9,
            "severity": "HIGH",
            "dimension": "VALIDITY",
            "rule_description": "Cước từ 0 đến 100",
            "ai_reasoning": "Hợp lệ",
        },
    ]

    # 1. Lưu proposed rules (mặc định PENDING)
    save_proposed_rules(run_id, dataset_id, mock_rules)
    assert len(get_active_rules(dataset_id)) == 0

    # 2. Steward duyệt 1 rule và chỉnh sửa tham số (APPROVED), từ chối 1 rule (REJECTED)
    review_rule(
        run_id=run_id,
        rule_id="yellow_tripdata.fare_amount.NOT_NULL",
        status="APPROVED",
        reviewer="data_steward@example.com",
    )
    review_rule(
        run_id=run_id,
        rule_id="yellow_tripdata.fare_amount.RANGE",
        status="APPROVED",
        edited_parameters={"min": 2.5, "max": 150.0},
        reviewer="data_steward@example.com",
    )

    # 3. Publish vào Active Ruleset
    published_count = publish_approved_rules(run_id)
    assert published_count == 2

    # 4. Kiểm tra Active Ruleset
    active_rules = get_active_rules(dataset_id)
    assert len(active_rules) == 2

    active_map = {r["rule_id"]: r for r in active_rules}
    not_null_rule = active_map["yellow_tripdata.fare_amount.NOT_NULL"]
    assert not_null_rule["status"] == "ACTIVE"
    assert not_null_rule["last_run_id"] == run_id

    range_rule = active_map["yellow_tripdata.fare_amount.RANGE"]
    # Kiểm tra effective parameters đã được cập nhật từ edited_parameters của Steward
    assert range_rule["parameters"] == {"min": 2.5, "max": 150.0}

    # 5. Kiểm tra Deactivate rule
    deact_res = deactivate_rule("yellow_tripdata.fare_amount.NOT_NULL")
    assert deact_res is True

    active_after_deact = get_active_rules(dataset_id)
    assert len(active_after_deact) == 1
    assert active_after_deact[0]["rule_id"] == "yellow_tripdata.fare_amount.RANGE"


def test_publish_api_endpoints():
    """Kiểm tra các REST API endpoints mới cho Active Rules và Publish."""
    client = TestClient(app)
    run_id = f"api_prop_{uuid.uuid4().hex[:8]}"
    dataset_id = "yellow_tripdata"

    create_run(run_id, dataset_id)

    mock_rules = [
        {
            "rule_id": "yellow_tripdata.payment_type.ACCEPTED_VALUES",
            "table_name": "yellow_tripdata",
            "column": "payment_type",
            "rule_type": "ACCEPTED_VALUES",
            "parameters": {"accepted_values": ["Credit card", "Cash"]},
            "confidence_score": 0.95,
            "severity": "MEDIUM",
            "dimension": "VALIDITY",
            "rule_description": "Thanh toán hợp lệ",
            "ai_reasoning": "Tiền mặt hoặc thẻ",
        }
    ]

    save_proposed_rules(run_id, dataset_id, mock_rules)
    review_rule(run_id, "yellow_tripdata.payment_type.ACCEPTED_VALUES", "APPROVED")

    # 1. Gọi API POST /api/v1/dq/runs/{run_id}/publish
    publish_res = client.post(f"/api/v1/dq/runs/{run_id}/publish")
    assert publish_res.status_code == 200
    assert publish_res.json()["published_count"] == 1

    # 2. Gọi API GET /api/v1/dq/active-rules
    active_res = client.get("/api/v1/dq/active-rules")
    assert active_res.status_code == 200
    active_data = active_res.json()
    assert active_data["total_rules"] == 1
    assert active_data["rules"][0]["rule_id"] == "yellow_tripdata.payment_type.ACCEPTED_VALUES"
    assert active_data["rules"][0]["created_at"] is not None

    # 3. Gọi API PATCH /api/v1/dq/active-rules/{rule_id}/deactivate
    deact_res = client.patch("/api/v1/dq/active-rules/yellow_tripdata.payment_type.ACCEPTED_VALUES/deactivate")
    assert deact_res.status_code == 200

    active_res_after = client.get("/api/v1/dq/active-rules")
    assert active_res_after.json()["total_rules"] == 0
