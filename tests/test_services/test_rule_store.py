"""Unit & Integration tests for Data Services, Active Rules Registry & Publishing Flow."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

import src.services.rule_store as rule_store_module
from src.main import app
from src.models.database import DqResultModel, JobModel, RuleConfigurationModel, RuleProposalModel, RuleVersionModel
from src.services.rule_store import (
    create_run,
    deactivate_rule,
    get_active_rules,
    init_db,
    publish_approved_rules,
    review_rule,
    save_proposed_rules,
    should_seed_legacy_demo_dataset,
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
    test_engine.dispose()


def test_publish_approved_rules_workflow():
    """Kiểm tra quy trình duyệt rule -> publish vào Active Ruleset."""
    run_id = f"test_run_{uuid.uuid4().hex[:8]}"
    dataset_id = "yellow_tripdata"

    create_run(run_id, dataset_id)

    with Session(rule_store_module.get_engine()) as session:
        job = session.get(JobModel, run_id)
        assert job is not None
        assert job.message == "Queued for rule proposal generation"

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


def test_canonical_rule_identifiers_are_not_uuid_limited():
    assert RuleProposalModel.__table__.c.id.type.length == 512
    assert RuleVersionModel.__table__.c.rule_proposal_id.type.length == 512
    assert RuleVersionModel.__table__.c.id.type.length == 640
    assert RuleConfigurationModel.__table__.c.rule_id.type.length == 512
    assert DqResultModel.__table__.c.rule_id.type.length == 512


def test_legacy_demo_dataset_is_not_seeded_in_production_by_default(monkeypatch):
    monkeypatch.delenv("SEED_LEGACY_DEMO_DATASET", raising=False)
    assert should_seed_legacy_demo_dataset("production") is False
    assert should_seed_legacy_demo_dataset("test") is True


def test_legacy_demo_dataset_requires_explicit_production_opt_in(monkeypatch):
    monkeypatch.setenv("SEED_LEGACY_DEMO_DATASET", "true")
    assert should_seed_legacy_demo_dataset("production") is True
    monkeypatch.setenv("SEED_LEGACY_DEMO_DATASET", "false")
    assert should_seed_legacy_demo_dataset("development") is False


def test_publish_api_endpoints():
    """Kiểm tra các REST API endpoints mới cho Active Rules và Publish."""
    # Publish and deactivate now require a STEWARD session, so sign in first.
    client = TestClient(app)

    # dq_router yeu cau session (mount voi require_role trong src/main.py) va
    # get_session kiem CSRF tren moi request da xac thuc.
    _login = client.post(
        "/api/v1/session", json={"username": "steward", "password": "steward"}
    )
    assert _login.status_code == 200, f"khong dang nhap duoc: {_login.text}"
    client.headers["X-CSRF-Token"] = _login.json()["csrf_token"]
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


def test_sqlite_migration_renames_legacy_rule_configuration_key():
    """Existing local databases must match the Supabase ``rule_id`` contract."""
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE rule_proposals (id VARCHAR(64) PRIMARY KEY)"))
        connection.execute(text("INSERT INTO rule_proposals (id) VALUES ('proposal-1')"))
        connection.execute(
            text(
                """
                CREATE TABLE rule_configurations (
                    rule_proposal_id VARCHAR(64) NOT NULL PRIMARY KEY,
                    execution_status VARCHAR(16) NOT NULL,
                    schedule_frequency VARCHAR(16) NOT NULL,
                    timezone VARCHAR(64) NOT NULL,
                    last_run_at DATETIME,
                    next_run_at DATETIME,
                    model_name VARCHAR(128) NOT NULL,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO rule_configurations VALUES
                ('proposal-1', 'ACTIVE', 'MANUAL', 'UTC', NULL, NULL,
                 'legacy-model', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            )
        )

    rule_store_module._migrate_local_workflow_columns(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("rule_configurations")}
    assert "rule_id" in columns
    assert "rule_proposal_id" not in columns
    with engine.connect() as connection:
        assert connection.execute(text("SELECT rule_id FROM rule_configurations")).scalar_one() == "proposal-1"
