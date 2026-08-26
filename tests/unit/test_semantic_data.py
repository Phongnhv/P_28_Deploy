import hashlib
from pathlib import Path

import pandas as pd

from src.services.dataset_loader import load_manifest


def _get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def test_semantic_dataset_file_exists():
    """Kiểm tra tệp dữ liệu semantic Parquet và CSV tồn tại tại data/yellow_tripdata_2025/semantic_data/."""
    project_root = _get_project_root()
    parquet_path = (
        project_root / "data" / "yellow_tripdata_2025" / "semantic_data" / "yellow_tripdata_2025_semantic_50k.parquet"
    )
    csv_path = (
        project_root / "data" / "yellow_tripdata_2025" / "semantic_data" / "yellow_tripdata_2025_semantic_50k.csv"
    )
    manifest_path = project_root / "data" / "yellow_tripdata_2025" / "semantic_data" / "manifest.json"

    assert parquet_path.exists(), f"File {parquet_path} không tồn tại."
    assert csv_path.exists(), f"File {csv_path} không tồn tại."
    assert manifest_path.exists(), f"File {manifest_path} không tồn tại."


def test_semantic_strict_21_columns():
    """Kiểm tra tệp Parquet Semantic chỉ chứa ĐÚNG 21 CỘT tiêu chuẩn, không thêm cột thừa."""
    project_root = _get_project_root()
    parquet_path = (
        project_root / "data" / "yellow_tripdata_2025" / "semantic_data" / "yellow_tripdata_2025_semantic_50k.parquet"
    )
    df = pd.read_parquet(parquet_path)

    assert len(df.columns) == 21, f"Kỳ vọng 21 cột, nhưng có {len(df.columns)} cột."

    expected_21_cols = [
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
    for col in expected_21_cols:
        assert col in df.columns, f"Cột {col} thiếu trong tệp Parquet."


def test_semantic_direct_value_replacements_in_place():
    """Kiểm tra Phương án 1: Mã số VendorID, RatecodeID, payment_type, PULocationID, DOLocationID được thay thế TRỰC TIẾP trong 21 cột."""
    project_root = _get_project_root()
    parquet_path = (
        project_root / "data" / "yellow_tripdata_2025" / "semantic_data" / "yellow_tripdata_2025_semantic_50k.parquet"
    )
    df = pd.read_parquet(parquet_path)

    # 1. vendor_id thay bằng tên chữ
    valid_vendors = [
        "Creative Mobile Technologies, LLC",
        "Curb Mobility, LLC",
        "Myle Technologies Inc",
        "Helix",
        "Unknown Vendor",
    ]
    assert df["vendor_id"].isin(valid_vendors).all()

    # 2. rate_code_id thay bằng tên biểu giá
    valid_ratecodes = [
        "Standard rate",
        "JFK",
        "Newark",
        "Nassau or Westchester",
        "Negotiated fare",
        "Group ride",
        "Null/Unknown",
        "Unknown Ratecode",
    ]
    assert df["rate_code_id"].isin(valid_ratecodes).all()

    # 3. payment_type thay bằng tên phương thức thanh toán
    valid_payments = [
        "Flex Fare trip",
        "Credit card",
        "Cash",
        "No charge",
        "Dispute",
        "Unknown",
        "Voided trip",
        "Invalid Payment (Dispute/Test)",
        "Unknown Payment",
    ]
    assert df["payment_type"].isin(valid_payments).all()

    # 4. Location IDs thay bằng nhãn chữ Borough (Zone)
    assert df["pickup_location_id"].str.contains(r"\(").all()
    assert df["dropoff_location_id"].str.contains(r"\(").all()


def test_semantic_sha256_checksum():
    """Kiểm tra SHA-256 hash của tệp semantic parquet khớp 100% với manifest."""
    project_root = _get_project_root()
    parquet_path = (
        project_root / "data" / "yellow_tripdata_2025" / "semantic_data" / "yellow_tripdata_2025_semantic_50k.parquet"
    )
    manifest = load_manifest("nyc-yellow-50k-v1")

    sha256 = hashlib.sha256()
    with open(parquet_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    calculated_hash = sha256.hexdigest()

    assert calculated_hash == manifest["file_sha256"]
