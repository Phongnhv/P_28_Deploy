import pytest

from src.services.dataset_loader import (
    load_dataset_rows,
    load_manifest,
    verify_checksum,
)


def test_fixture_row_count():
    """Kịch bản 1: Kiểm thử đếm đúng tổng số bản ghi trong tệp dataset."""
    manifest = load_manifest("nyc-yellow-demo-v1")
    rows = load_dataset_rows("nyc-yellow-demo-v1")
    assert len(rows) == manifest["total_rows"]
    assert len(rows) == 73


def test_fixture_columns_schema():
    """Kịch bản 2: Kiểm thử dữ liệu có đủ tất cả 21 cột tiêu chuẩn."""
    rows = load_dataset_rows("nyc-yellow-demo-v1")
    expected_columns = [
        "source_row_id",
        "vendor_id",
        "pickup_at",
        "dropoff_at",
        "passenger_count",
        "trip_distance",
        "rate_code_id",
        "store_and_fwd_flag",
        "pickup_location_id",
        "dropoff_location_id",
        "payment_type",
        "fare_amount",
        "extra",
        "mta_tax",
        "tip_amount",
        "tolls_amount",
        "improvement_surcharge",
        "total_amount",
        "congestion_surcharge",
        "airport_fee",
        "cbd_congestion_fee",
    ]
    first_row = rows[0]
    for col in expected_columns:
        assert col in first_row, f"Cột '{col}' không tồn tại trong dataset fixture"
    assert len(first_row.keys()) == 21


def test_fixture_sha256_checksum():
    """Kịch bản 3: Kiểm thử mã hash SHA-256 thực tế khớp 100% với manifest."""
    manifest = load_manifest("nyc-yellow-demo-v1")
    expected_sha256 = manifest["file_sha256"]
    csv_path = manifest["local_path"]
    assert verify_checksum(csv_path, expected_sha256) is True


def test_fixture_synthetic_defects():
    """Kịch bản 4: Xác nhận các dòng lỗi synthetic được chèn đúng điều kiện giả định."""
    rows = load_dataset_rows("nyc-yellow-demo-v1")
    row_map = {r["source_row_id"]: r for r in rows}

    # 1. Duplicate Fingerprint: row-069 trùng lặp hành trình với row-001
    row_001 = row_map["row-001"]
    row_069 = row_map["row-069"]
    assert row_069["vendor_id"] == row_001["vendor_id"]
    assert row_069["pickup_at"] == row_001["pickup_at"]
    assert row_069["dropoff_at"] == row_001["dropoff_at"]
    assert row_069["trip_distance"] == row_001["trip_distance"]

    # 2. Null Vendor ID: row-070 có vendor_id bị trống/NULL
    row_070 = row_map["row-070"]
    assert row_070["vendor_id"] == ""

    # 3. Negative Distance & Fare: row-071 có khoảng cách và cước âm
    row_071 = row_map["row-071"]
    assert float(row_071["trip_distance"]) < 0
    assert float(row_071["fare_amount"]) < 0

    # 4. Zero Distance High Fare: row-072 có khoảng cách 0 nhưng cước cao
    row_072 = row_map["row-072"]
    assert float(row_072["trip_distance"]) == 0.0
    assert float(row_072["fare_amount"]) > 100.0

    # 5. Invalid Payment Type: row-073 có payment_type = 99
    row_073 = row_map["row-073"]
    assert row_073["payment_type"] == "99"


def test_fixture_manifest_allow_list_security():
    """Kịch bản 5: Kiểm thử bảo mật allow-list manifest và ném lỗi khi chứa path traversal."""
    with pytest.raises(ValueError, match="không nằm trong danh sách allow-list"):
        load_manifest("unauthorized-manifest")

    with pytest.raises(ValueError, match="Phát hiện ký tự Path Traversal"):
        load_manifest("../src/resources/manifest.json")
