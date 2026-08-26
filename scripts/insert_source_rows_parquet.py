#!/usr/bin/env python3
"""Insert a semantic Parquet file directly into the source_rows table.

Usage examples:
  python scripts/insert_source_rows_parquet.py
  python scripts/insert_source_rows_parquet.py --dataset-id dataset-nyc-yellow-taxi-50k
  python scripts/insert_source_rows_parquet.py --parquet "data/yellow_tripdata_2025/semantic_data/yellow_tripdata_2025_semantic_50k.parquet"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from src.config import get_settings  # noqa: E402
from src.models.database import DatasetModel, SourceRowModel  # noqa: E402
from src.services.rule_store import get_engine, init_db  # noqa: E402

EXPECTED_COLUMNS = [
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


def resolve_parquet_path(raw_path: str | None) -> Path:
    candidates = []
    if raw_path:
        candidates.append(Path(raw_path))
    candidates.extend(
        [
            Path("data/yellow_tripdata_2025/semantic_data/yellow_tripdata_2025_semantic_50k.parquet"),
            Path("data/yellow_tripdata_2025/semantic_data/"),
        ]
    )
    project_root = Path(__file__).resolve().parent.parent
    for candidate in candidates:
        if candidate.is_absolute():
            if candidate.exists():
                return candidate
        else:
            local = project_root / candidate
            if local.exists():
                return local
    fallback = (
        project_root / "data" / "yellow_tripdata_2025" / "semantic_data" / "yellow_tripdata_2025_semantic_50k.parquet"
    )
    if fallback.exists():
        return fallback
    raise FileNotFoundError(f"Parquet file not found. Tried: {[str(p) for p in candidates + [fallback]]}")


def normalize_row(raw_row: dict, dataset_id: str) -> dict:
    normalized = {}
    for key, value in raw_row.items():
        if key not in EXPECTED_COLUMNS:
            continue
        if pd.isna(value):
            normalized[key] = None
        else:
            normalized[key] = value
    normalized["dataset_id"] = dataset_id
    return normalized


def insert_parquet_to_source_rows(parquet_path: str | Path, dataset_id: str, batch_size: int = 1000) -> int:
    path = resolve_parquet_path(str(parquet_path)) if parquet_path else resolve_parquet_path(None)
    df = pd.read_parquet(path)
    missing = [col for col in EXPECTED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns in parquet: {missing}")

    df = df[EXPECTED_COLUMNS].copy()

    engine = get_engine()
    init_db()

    with Session(engine) as session:
        session.query(SourceRowModel).filter(SourceRowModel.dataset_id == dataset_id).delete(synchronize_session=False)

        rows = []
        for record in df.to_dict(orient="records"):
            rows.append(normalize_row(record, dataset_id))
            if len(rows) >= batch_size:
                session.bulk_insert_mappings(SourceRowModel, rows)
                rows.clear()

        if rows:
            session.bulk_insert_mappings(SourceRowModel, rows)

        dataset = session.query(DatasetModel).filter(DatasetModel.id == dataset_id).first()
        if dataset is None:
            dataset = DatasetModel(
                id=dataset_id,
                name=dataset_id,
                description="Inserted from parquet via script",
                status="REGISTERED",
                row_count=0,
                source_label="semantic",
                manifest_version="manual",
                checksum="manual-import",
            )
            session.add(dataset)

        dataset.row_count = int(len(df))
        dataset.status = "PROFILE_READY"
        session.commit()

    return int(len(df))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load a Parquet file straight into source_rows.")
    parser.add_argument(
        "--dataset-id",
        default="dataset-nyc-yellow-taxi-50k",
        help="Dataset ID to assign to each inserted row.",
    )
    parser.add_argument(
        "--parquet",
        default=None,
        help="Optional path to parquet file. Defaults to the project semantic 50k file.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Batch size for bulk inserts.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    settings = get_settings()
    print(f"Using DB: {settings.database_url}")
    path = resolve_parquet_path(args.parquet)
    print(f"Reading parquet: {path}")
    count = insert_parquet_to_source_rows(path, args.dataset_id, batch_size=args.batch_size)
    print(f"Inserted {count} rows into source_rows for dataset_id={args.dataset_id}")
