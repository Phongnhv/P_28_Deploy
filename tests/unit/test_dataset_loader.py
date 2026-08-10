import pytest

from src.services.dataset_loader import (
    load_dataset_rows,
    load_manifest,
    verify_checksum,
)


def test_load_manifest_valid():
    """Kiểm tra đọc thành công tệp manifest đăng ký hợp lệ."""
    manifest = load_manifest("nyc-yellow-demo-v1")
    assert isinstance(manifest, dict)
    assert manifest["manifest_name"] == "nyc-yellow-demo-v1"
    assert manifest["total_rows"] == 73
    assert manifest["defect_rows"] == 5
    assert "columns" in manifest


def test_load_manifest_invalid_name():
    """Kiểm tra ném lỗi ValueError khi manifest_name không nằm trong allow-list."""
    with pytest.raises(ValueError, match="không nằm trong danh sách allow-list"):
        load_manifest("invalid-manifest-name")


def test_load_manifest_path_traversal():
    """Kiểm tra bảo vệ Path Traversal khi truyền ký tự lạ hoặc đường dẫn tùy ý."""
    with pytest.raises(ValueError, match="Phát hiện ký tự Path Traversal"):
        load_manifest("../src/resources/manifest.json")

    with pytest.raises(ValueError, match="Phát hiện ký tự Path Traversal"):
        load_manifest("..\\src\\resources\\manifest.json")


def test_verify_checksum_success():
    """Kiểm tra verify_checksum thành công với SHA-256 thực tế."""
    manifest = load_manifest("nyc-yellow-demo-v1")
    sha256 = manifest["file_sha256"]
    csv_path = manifest["local_path"]
    assert verify_checksum(csv_path, sha256) is True


def test_verify_checksum_mismatch():
    """Kiểm tra verify_checksum ném lỗi ValueError khi mã hash không khớp."""
    manifest = load_manifest("nyc-yellow-demo-v1")
    csv_path = manifest["local_path"]
    fake_sha256 = "0000000000000000000000000000000000000000000000000000000000000000"

    with pytest.raises(ValueError, match="Checksum SHA-256 không khớp"):
        verify_checksum(csv_path, fake_sha256)


def test_load_dataset_rows_success():
    """Kiểm tra đọc dữ liệu dataset hoàn chỉnh trả về 73 bản ghi và 21 cột."""
    rows = load_dataset_rows("nyc-yellow-demo-v1")
    assert isinstance(rows, list)
    assert len(rows) == 73

    # Kiểm tra bản ghi đầu tiên
    first_row = rows[0]
    assert first_row["source_row_id"] == "row-001"
    assert "vendor_id" in first_row
    assert "pickup_at" in first_row
    assert "fare_amount" in first_row
    assert len(first_row.keys()) == 21

    # Kiểm tra bản ghi lỗi synthetic
    row_71 = next(r for r in rows if r["source_row_id"] == "row-071")
    assert row_71["trip_distance"] == "-2.5"
    assert row_71["fare_amount"] == "-15.0"
