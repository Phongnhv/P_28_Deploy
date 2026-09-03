"""Unit tests for Profiler Tool, Profiler Node, and Profile Digest."""

import json

import pytest
from sqlalchemy import Column, Float, Integer, MetaData, String, Table, create_engine

from src.agents.nodes.profiler_node import raw_profiler_node
from src.agents.tools.db_profiler_tool import profile_database
from src.agents.tools.profile_digest import generate_profile_digest


@pytest.fixture
def temp_db_url(tmp_path):
    """Tạo một database SQLite tạm thời để kiểm thử."""
    db_file = tmp_path / "test.db"
    db_url = f"sqlite:///{db_file}"

    # Tạo bảng và insert dữ liệu mẫu
    engine = create_engine(db_url)
    metadata = MetaData()

    test_table = Table(
        "dich_vu_xe_trips",
        metadata,
        Column("trip_id", Integer, primary_key=True),
        Column("fare_amount", Float, nullable=True),
        Column("driver_name", String, nullable=True),
    )

    metadata.create_all(engine)

    with engine.connect() as conn:
        conn.execute(
            test_table.insert(),
            [
                {"trip_id": 1, "fare_amount": 100.0, "driver_name": "Nguyen An"},
                {"trip_id": 2, "fare_amount": 150.0, "driver_name": "Le Binh"},
                {"trip_id": 3, "fare_amount": None, "driver_name": "Nguyen An"},  # 1 null fare, 2 distinct names
                {"trip_id": 4, "fare_amount": 200.0, "driver_name": None},  # 1 null name
            ],
        )
        conn.commit()

    yield db_url

    engine.dispose()


def test_profile_database_tool(temp_db_url):
    """Kiểm thử tính chính xác của tool profiling trên SQLite."""
    res_json_str = profile_database.invoke(
        {"connection_string": temp_db_url, "table_name": "dich_vu_xe_trips", "sampling_rate": 1.0}
    )

    res = json.loads(res_json_str)

    assert "error" not in res
    assert res["table_metadata"]["table_name"] == "dich_vu_xe_trips"
    assert res["table_metadata"]["total_rows"] == 4

    # Kiểm tra số liệu fare_amount (Numeric)
    fare_stats = res["columns"]["fare_amount"]
    assert fare_stats["null_count"] == 1
    assert fare_stats["null_pct"] == 0.25
    assert fare_stats["distinct_in_sample"] == 3
    assert fare_stats["min"] == 100.0
    assert fare_stats["max"] == 200.0
    assert fare_stats["mean"] == 150.0

    # Kiểm tra số liệu driver_name (Text)
    driver_stats = res["columns"]["driver_name"]
    assert driver_stats["null_count"] == 1
    assert driver_stats["distinct_in_sample"] == 2
    assert "top_categories" in driver_stats
    assert len(driver_stats["top_categories"]) > 0
    assert driver_stats["top_categories"][0]["value"] == "Nguyen An"


@pytest.mark.asyncio
async def test_profiler_node_execution(temp_db_url):
    """Explicit table selection profiles only the requested source."""
    state = {"target_tables": ["dich_vu_xe_trips"], "metadata": {"connection_string": temp_db_url, "sampling_rate": 1.0}}

    result = await raw_profiler_node(state)

    # Kiểm định kết quả cập nhật State
    assert "error" not in result
    assert "dataset_profile" in result

    # Verify that the test table was profiled
    profile = result["dataset_profile"]
    assert "dich_vu_xe_trips" in profile

    table_profile = profile["dich_vu_xe_trips"]
    assert table_profile["table_metadata"]["table_name"] == "dich_vu_xe_trips"
    assert table_profile["table_metadata"]["total_rows"] == 4


def test_profile_digest(temp_db_url):
    """Kiểm thử hàm generate_profile_digest chuyển đổi thống kê."""
    res_json_str = profile_database.invoke(
        {"connection_string": temp_db_url, "table_name": "dich_vu_xe_trips", "sampling_rate": 1.0}
    )
    raw_profile = json.loads(res_json_str)

    dataset_profile = {"dich_vu_xe_trips": raw_profile}
    digest = generate_profile_digest(dataset_profile)

    assert "dich_vu_xe_trips" in digest
    table_digest = digest["dich_vu_xe_trips"]
    assert table_digest["table"] == "dich_vu_xe_trips"
    assert table_digest["rows"] == 4
    assert table_digest["sample"]["n"] == 4

    columns = {col["name"]: col for col in table_digest["columns"]}

    # fare_amount stats
    assert "fare_amount" in columns
    assert columns["fare_amount"]["null_pct"] == 25.0
    if columns["fare_amount"]["role"] == "numeric":
        assert columns["fare_amount"]["range"] == [100.0, 200.0]
    else:
        assert columns["fare_amount"]["role"] == "categorical"
        assert len(columns["fare_amount"]["values"]) > 0

    # driver_name should be categorical since distinct <= 50
    assert "driver_name" in columns
    assert columns["driver_name"]["role"] == "categorical"
    assert "values" in columns["driver_name"]
    assert "Nguyen An" in columns["driver_name"]["values"]
