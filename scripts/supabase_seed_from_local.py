"""Safely copy a bounded local sample to Supabase's canonical raw contract."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.supabase_dataset import (  # noqa: E402
    CANONICAL_COLUMNS,
    DATASET_ID,
    create_supabase_engine,
)

RAW_VALUE_COLUMNS = tuple(sorted(CANONICAL_COLUMNS - {"source_row_id", "dataset_id"}))


def _verify_remote_contract(connection) -> None:
    raw_columns = set(
        connection.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'trips_raw'"
            )
        ).scalars()
    )
    if raw_columns != {"source_row_id", "dataset_id", "values"}:
        raise RuntimeError(f"Unexpected public.trips_raw contract: {sorted(raw_columns)}")

    canonical_columns = set(
        connection.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'trips_canonical'"
            )
        ).scalars()
    )
    if not CANONICAL_COLUMNS.issubset(canonical_columns):
        missing = sorted(CANONICAL_COLUMNS - canonical_columns)
        raise RuntimeError(f"trips_canonical is missing expected fields: {missing}")


def _load_local_rows(source_url: str, dataset_id: str, limit: int) -> list[dict[str, Any]]:
    engine = create_engine(source_url)
    columns = ", ".join(("source_row_id", *RAW_VALUE_COLUMNS))
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"SELECT {columns} FROM source_rows "
                    "WHERE dataset_id = :dataset_id ORDER BY source_row_id LIMIT :limit"
                ),
                {"dataset_id": dataset_id, "limit": limit},
            ).mappings()
            return [
                {
                    "source_row_id": str(row["source_row_id"]),
                    "dataset_id": dataset_id,
                    "values": json.dumps({column: row[column] for column in RAW_VALUE_COLUMNS}),
                }
                for row in rows
            ]
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-id", default=DATASET_ID)
    parser.add_argument("--source-url", default="sqlite:///ui_local_mvp.db")
    parser.add_argument("--target-row-count", type=int, default=5_000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.target_row_count < 1:
        raise SystemExit("--target-row-count must be positive")

    load_dotenv(ROOT / ".env")
    database_url = os.getenv("DATABASE_URL")
    if not database_url or "postgres" not in database_url:
        raise SystemExit("DATABASE_URL must point to PostgreSQL/Supabase")

    remote = create_supabase_engine(database_url)
    try:
        with remote.begin() as connection:
            _verify_remote_contract(connection)
            current_count = int(
                connection.execute(
                    text("SELECT COUNT(*) FROM public.trips_raw WHERE dataset_id = :dataset_id"),
                    {"dataset_id": args.dataset_id},
                ).scalar_one()
            )
            needed = max(0, args.target_row_count - current_count)
            print(f"remote_rows={current_count} target_rows={args.target_row_count} needed={needed}")
            if not needed:
                return 0

            # Fetch a small buffer in case a globally unique source_row_id already
            # belongs to another dataset in the remote raw table.
            source_rows = _load_local_rows(args.source_url, args.dataset_id, needed + 500)
            if len(source_rows) < needed:
                raise RuntimeError(f"Local source has only {len(source_rows)} usable rows; need {needed}")
            batch = source_rows[:needed]
            if args.dry_run:
                print(f"validated_contract=true planned_insert={len(batch)}")
                return 0

            insert = text(
                "INSERT INTO public.trips_raw (source_row_id, dataset_id, values) "
                "VALUES (:source_row_id, :dataset_id, CAST(:values AS json)) "
                "ON CONFLICT (source_row_id) DO NOTHING"
            )
            for start in range(0, len(batch), 250):
                connection.execute(insert, batch[start : start + 250])

            final_count = int(
                connection.execute(
                    text("SELECT COUNT(*) FROM public.trips_raw WHERE dataset_id = :dataset_id"),
                    {"dataset_id": args.dataset_id},
                ).scalar_one()
            )
            if final_count != args.target_row_count:
                raise RuntimeError(
                    f"Inserted rows did not reach target: expected {args.target_row_count}, got {final_count}."
                )
            connection.execute(
                text(
                    "UPDATE public.datasets SET row_count = :row_count, status = 'PROFILE_READY', "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = :dataset_id"
                ),
                {"dataset_id": args.dataset_id, "row_count": final_count},
            )
            print(f"inserted={final_count - current_count} final_rows={final_count}")
    finally:
        remote.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
