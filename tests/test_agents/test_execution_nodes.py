"""Unit & Integration tests for Test Generator & Execution Flow Nodes (Run 2)."""

import re
import uuid

import pytest
from sqlalchemy import create_engine, event, text

import src.services.rule_store as rule_store_module
from src.agents.graph import build_execution_graph
from src.agents.nodes.test_generator_node import generate_tests_for_table
from src.agents.nodes.validate_sql_node import validate_single_sql
from src.services.rule_store import (
    create_test_run,
    get_test_results,
    get_test_run,
    init_db,
    save_proposed_rules,
)


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    """Tạo bảng mock và khởi tạo database SQLite."""
    db_file = tmp_path / "test_exec.db"
    sqlite_url = f"sqlite:///{db_file}"

    test_engine = create_engine(sqlite_url, connect_args={"check_same_thread": False})

    @event.listens_for(test_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record):
        def _sqlite_regexp(expr, item):
            if item is None:
                return False
            return re.search(expr, str(item)) is not None
        dbapi_conn.create_function("REGEXP", 2, _sqlite_regexp)

    monkeypatch.setattr(rule_store_module, "_engine", test_engine)

    init_db()
    engine = test_engine

    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS mock_trips;"))
        # - license_plate: 1 invalid regex ("INVALID_123")
        # - dropoff_datetime: 1 invalid cross-field (TRIP_5 has pickup > dropoff)
        conn.execute(text("""
            CREATE TABLE mock_trips (
                trip_id TEXT,
                fare_amount REAL,
                passenger_count INTEGER,
                payment_type TEXT,
                license_plate TEXT,
                pickup_datetime TEXT,
                dropoff_datetime TEXT
            );
        """))

        conn.execute(text("""
            INSERT INTO mock_trips VALUES
            ('TRIP_1', 15.0, 1, 'Credit card', 'NY-1234', '2026-08-10T10:00:00Z', '2026-08-10T10:30:00Z'),
            ('TRIP_1', 20.0, 2, 'Cash',        'NY-5678', '2026-08-10T11:00:00Z', '2026-08-10T11:45:00Z'),
            ('TRIP_2', NULL, 1, 'Credit card', 'NY-9999', '2026-08-10T12:00:00Z', '2026-08-10T12:15:00Z'),
            ('TRIP_3', -5.0, 3, 'Credit card', 'NY-1111', '2026-08-10T13:00:00Z', '2026-08-10T13:20:00Z'),
            ('TRIP_4', 150.0, 1, 'Crypto',     'NY-2222', '2026-08-10T14:00:00Z', '2026-08-10T14:50:00Z'),
            ('TRIP_5', 25.0, 2, 'Cash',        'INVALID',   '2026-08-10T15:00:00Z', '2026-08-10T14:00:00Z'),
            ('TRIP_6', 30.0, 1, 'Cash',        'NY-3333', '2026-08-10T16:00:00Z', '2026-08-10T16:10:00Z'),
            ('TRIP_7', 40.0, 4, 'Credit card', 'NY-4444', '2026-08-10T17:00:00Z', '2026-08-10T17:35:00Z'),
            ('TRIP_8', 12.0, 1, 'Gold',        'NY-5555', '2026-08-10T18:00:00Z', '2026-08-10T18:25:00Z'),
            ('TRIP_9', 50.0, 1, 'Credit card', 'NY-6666', '2026-08-10T19:00:00Z', '2026-08-10T19:40:00Z');
        """))
        conn.commit()

    yield

    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS mock_trips;"))
        conn.commit()


@pytest.mark.asyncio
async def test_generate_tests_compilation():
    """Kiểm tra logic biên dịch rules thành SQL batch (bao gồm CROSS_FIELD_COMPARISON)."""
    rules = [
        {
            "rule_id": "rule_not_null",
            "column": "fare_amount",
            "rule_type": "NOT_NULL",
            "parameters": {},
        },
        {
            "rule_id": "rule_range",
            "column": "fare_amount",
            "rule_type": "RANGE",
            "parameters": {"min": 0.0, "max": 100.0},
        },
        {
            "rule_id": "rule_accepted",
            "column": "payment_type",
            "rule_type": "ACCEPTED_VALUES",
            "parameters": {"accepted_values": ["Credit card", "Cash"]},
        },
        {
            "rule_id": "rule_cross_field",
            "column": "pickup_datetime",
            "rule_type": "CROSS_FIELD_COMPARISON",
            "parameters": {"target_column": "dropoff_datetime", "operator": "<="},
        },
        {
            "rule_id": "rule_unique",
            "column": "trip_id",
            "rule_type": "UNIQUE",
            "parameters": {},
        },
    ]

    generated = generate_tests_for_table("mock_trips", rules, dialect_name="sqlite")
    assert len(generated) == 2  # 1 batch row (4 rules) + 1 unique

    batch_test = next(t for t in generated if t["query_type"] == "batch_row")
    assert "SUM(CASE WHEN \"fare_amount\" IS NULL THEN 1 ELSE 0 END) AS v_0" in batch_test["sql_text"]
    assert "NOT (\"pickup_datetime\" <= \"dropoff_datetime\")" in batch_test["sql_text"]
    assert "p_min_1" in batch_test["bind_params"]
    assert batch_test["bind_params"]["p_min_1"] == 0.0


@pytest.mark.asyncio
async def test_validate_single_sql():
    """Kiểm tra EXPLAIN validation."""
    valid_sql = "SELECT COUNT(*) AS total_rows FROM mock_trips"
    is_valid, err = validate_single_sql(valid_sql, {}, "sqlite")
    assert is_valid is True
    assert err is None

    invalid_sql = "SELECT NON_EXISTENT_COL FROM mock_trips"
    is_valid, err = validate_single_sql(invalid_sql, {}, "sqlite")
    assert is_valid is False
    assert err is not None


@pytest.mark.asyncio
async def test_execution_graph_end_to_end():
    """Chạy toàn bộ Run 2 LangGraph pipeline trên dữ liệu mock."""
    proposal_run_id = f"test_prop_{uuid.uuid4().hex[:8]}"
    test_run_id = f"test_exec_{uuid.uuid4().hex[:8]}"

    rules = [
        {
            "rule_id": "mock_trips.fare_amount.NOT_NULL",
            "table_name": "mock_trips",
            "column": "fare_amount",
            "rule_type": "NOT_NULL",
            "parameters": {},
            "confidence_score": 1.0,
            "severity": "CRITICAL",
            "dimension": "COMPLETENESS",
            "rule_description": "Cước phí không được rỗng",
            "ai_reasoning": "Mọi chuyến đi cần có cước phí",
            "status": "APPROVED",
        },
        {
            "rule_id": "mock_trips.fare_amount.RANGE",
            "table_name": "mock_trips",
            "column": "fare_amount",
            "rule_type": "RANGE",
            "parameters": {"min": 0.0, "max": 100.0},
            "confidence_score": 0.9,
            "severity": "HIGH",
            "dimension": "VALIDITY",
            "rule_description": "Cước phí từ 0 đến 100",
            "ai_reasoning": "Không có cước âm hoặc quá lớn",
            "status": "APPROVED",
        },
        {
            "rule_id": "mock_trips.payment_type.ACCEPTED_VALUES",
            "table_name": "mock_trips",
            "column": "payment_type",
            "rule_type": "ACCEPTED_VALUES",
            "parameters": {"accepted_values": ["Credit card", "Cash"]},
            "confidence_score": 0.95,
            "severity": "MEDIUM",
            "dimension": "VALIDITY",
            "rule_description": "Hình thức thanh toán hợp lệ",
            "ai_reasoning": "Chỉ nhận thẻ hoặc tiền mặt",
            "status": "APPROVED",
        },
        {
            "rule_id": "mock_trips.pickup_datetime.VS.dropoff_datetime.CROSS_FIELD_COMPARISON",
            "table_name": "mock_trips",
            "column": "pickup_datetime",
            "rule_type": "CROSS_FIELD_COMPARISON",
            "parameters": {"target_column": "dropoff_datetime", "operator": "<="},
            "confidence_score": 0.99,
            "severity": "CRITICAL",
            "dimension": "CONSISTENCY",
            "rule_description": "Thời gian đón khách phải trước hoặc bằng thời gian trả khách",
            "ai_reasoning": "Chuyến đi không thể kết thúc trước khi bắt đầu",
            "status": "APPROVED",
        },
        {
            "rule_id": "mock_trips.trip_id.UNIQUE",
            "table_name": "mock_trips",
            "column": "trip_id",
            "rule_type": "UNIQUE",
            "parameters": {},
            "confidence_score": 1.0,
            "severity": "CRITICAL",
            "dimension": "UNIQUENESS",
            "rule_description": "Mã chuyến đi là duy nhất",
            "ai_reasoning": "Khóa chính",
            "status": "APPROVED",
        },
        {
            "rule_id": "mock_trips._table.ROW_COUNT",
            "table_name": "mock_trips",
            "column": None,
            "rule_type": "ROW_COUNT",
            "parameters": {"min_row_count": 5},
            "confidence_score": 0.8,
            "severity": "LOW",
            "dimension": "COMPLETENESS",
            "rule_description": "Bảng có ít nhất 5 dòng",
            "ai_reasoning": "Kiểm tra bảng không rỗng",
            "status": "APPROVED",
        },
    ]

    # Lưu rules vào DB
    save_proposed_rules(proposal_run_id, "mock_trips", rules)
    create_test_run(test_run_id, "mock_trips")

    execution_graph = build_execution_graph()
    initial_state = {
        "dataset_id": "mock_trips",
        "rule_run_id": proposal_run_id,
        "test_run_id": test_run_id,
        "approved_rules": rules,
    }

    _final_state = await execution_graph.ainvoke(initial_state)

    # 1. Kiểm tra trạng thái run
    run_rec = get_test_run(test_run_id)
    assert run_rec is not None
    assert run_rec["status"] == "DONE"

    # 2. Kiểm tra test_results
    results = get_test_results(test_run_id)
    assert len(results) == 6

    res_map = {r["rule_id"]: r for r in results}

    # NOT_NULL: 1 dòng null trên 10 dòng
    assert res_map["mock_trips.fare_amount.NOT_NULL"]["violation_count"] == 1
    assert res_map["mock_trips.fare_amount.NOT_NULL"]["total_rows"] == 10
    assert res_map["mock_trips.fare_amount.NOT_NULL"]["violation_rate"] == 0.1
    assert res_map["mock_trips.fare_amount.NOT_NULL"]["status"] == "FAIL"

    # RANGE [0, 100]: -5.0 và 150.0 vi phạm -> 2 dòng
    assert res_map["mock_trips.fare_amount.RANGE"]["violation_count"] == 2
    assert res_map["mock_trips.fare_amount.RANGE"]["status"] == "FAIL"

    # ACCEPTED_VALUES ['Credit card', 'Cash']: 'Crypto' và 'Gold' vi phạm -> 2 dòng
    assert res_map["mock_trips.payment_type.ACCEPTED_VALUES"]["violation_count"] == 2
    assert res_map["mock_trips.payment_type.ACCEPTED_VALUES"]["status"] == "FAIL"

    # CROSS_FIELD_COMPARISON (pickup_datetime <= dropoff_datetime): TRIP_5 vi phạm (15:00 > 14:00) -> 1 dòng
    cross_res = res_map["mock_trips.pickup_datetime.VS.dropoff_datetime.CROSS_FIELD_COMPARISON"]
    assert cross_res["violation_count"] == 1
    assert cross_res["status"] == "FAIL"
    # Mẫu vi phạm chỉ chứa ID dòng (list[str]), không phải nguyên bản ghi:
    # kết quả này được ghi vào cột dq_results.failed_row_ids và hiển thị trên UI.
    assert cross_res["sample_failures"] == ["TRIP_5"]

    # UNIQUE: 'TRIP_1' bị lặp lại 1 lần -> violation_count == 1
    assert res_map["mock_trips.trip_id.UNIQUE"]["violation_count"] == 1
    assert res_map["mock_trips.trip_id.UNIQUE"]["status"] == "FAIL"

    # ROW_COUNT: 10 dòng >= 5 -> PASSED
    assert res_map["mock_trips._table.ROW_COUNT"]["status"] == "PASS"
    assert res_map["mock_trips._table.ROW_COUNT"]["violation_count"] == 0


@pytest.mark.asyncio
async def test_agentic_repair_loop_recovery(monkeypatch):
    """Kiểm tra Agentic Loop: LLM sửa câu SQL lỗi và pipeline vẫn chạy thành công."""
    from unittest.mock import AsyncMock, MagicMock

    from src.agents.nodes.llm_repair_node import llm_repair_node
    from src.agents.nodes.validate_sql_node import validate_sql_node

    # Giả lập query bị lỗi cú pháp
    bad_test = {
        "test_id": "test_broken",
        "table_name": "mock_trips",
        "query_type": "batch_row",
        "sql_text": "SELECT SYNTAX_ERROR_TYPO FROM mock_trips",
        "bind_params": {},
        "rules_meta": [],
        "attempts": 0,
        "valid": False,
        "error": "syntax error",
    }

    state = {"generated_tests": [bad_test]}

    # 1. validate_sql_node đánh dấu invalid
    state_after_val = await validate_sql_node(state)
    assert state_after_val["generated_tests"][0]["valid"] is False

    # 2. Mock LLM trả về câu SQL đã sửa đúng
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "```sql\nSELECT COUNT(*) AS total_rows FROM mock_trips\n```"
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)

    monkeypatch.setattr("src.agents.nodes.llm_repair_node.get_llm", lambda *a, **kw: mock_llm)

    # 3. Chạy llm_repair_node
    state_after_repair = await llm_repair_node(state_after_val)
    repaired_test = state_after_repair["generated_tests"][0]
    assert repaired_test["attempts"] == 1
    assert "COUNT(*)" in repaired_test["sql_text"]

    # 4. validate lại câu SQL đã sửa
    state_final = await validate_sql_node(state_after_repair)
    assert state_final["generated_tests"][0]["valid"] is True


@pytest.mark.asyncio
async def test_api_execute_tests_endpoint():
    """Kiểm tra gọi qua FastAPI API endpoints của Run 2."""
    from fastapi.testclient import TestClient

    from src.main import app
    from src.services.rule_store import create_run

    proposal_run_id = f"prop_api_{uuid.uuid4().hex[:8]}"
    create_run(proposal_run_id, "mock_trips")

    client = TestClient(app)

    # dq_router yeu cau session (mount voi require_role trong src/main.py) va
    # get_session kiem CSRF tren moi request da xac thuc.
    _login = client.post(
        "/api/v1/session", json={"username": "steward", "password": "steward"}
    )
    assert _login.status_code == 200, f"khong dang nhap duoc: {_login.text}"
    client.headers["X-CSRF-Token"] = _login.json()["csrf_token"]

    # 1. Trigger Run 2
    res = client.post(f"/api/v1/dq/runs/{proposal_run_id}/execute-tests")
    assert res.status_code == 200
    data = res.json()
    assert "test_run_id" in data
    assert data["status"] == "QUEUED"

    test_run_id = data["test_run_id"]

    # 2. Poll status
    status_res = client.get(f"/api/v1/dq/test-runs/{test_run_id}")
    assert status_res.status_code == 200
    assert status_res.json()["test_run_id"] == test_run_id

    # 3. Lấy kết quả
    results_res = client.get(f"/api/v1/dq/test-runs/{test_run_id}/results")
    assert results_res.status_code == 200
    assert "results" in results_res.json()
