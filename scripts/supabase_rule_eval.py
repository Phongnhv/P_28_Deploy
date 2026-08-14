"""Evaluate the canonical MVP rules directly against Supabase."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.dashboard_agent_workflow import get_dataset_rule_policy  # noqa: E402
from src.services.supabase_dataset import (  # noqa: E402
    DATASET_ID,
    create_supabase_engine,
    execute_rule,
    persist_profile,
    profile_dataset,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-id", default=DATASET_ID)
    parser.add_argument("--persist-profile", action="store_true")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    database_url = os.getenv("DATABASE_URL")
    if not database_url or "postgres" not in database_url:
        raise SystemExit("DATABASE_URL must point to PostgreSQL/Supabase")

    policy = get_dataset_rule_policy(args.dataset_id)
    if policy is None:
        raise SystemExit(f"No versioned policy for {args.dataset_id}")
    rules = [
        ("vendor_id must be populated", {"type": "not_null", "column": "vendor_id"}),
        ("trip_distance must be non-negative", {
            "type": "numeric_range", "column": "trip_distance", "min_value": 0.0
        }),
        ("fare_amount must be non-negative", {
            "type": "numeric_range", "column": "fare_amount", "min_value": 0.0
        }),
        ("payment_type must use governed values", {
            "type": "accepted_values", "column": "payment_type",
            "allowed_values": policy.governed_value_sets["payment_type"],
        }),
        ("pickup_at must not follow dropoff_at", {
            "type": "cross_field_comparison", "columns": ["pickup_at", "dropoff_at"], "operator": "<=",
        }),
        ("trip fingerprint must not be duplicated", {
            "type": "duplicate_fingerprint",
            "fingerprint_columns": policy.duplicate_fingerprint_columns,
        }),
    ]

    engine = create_supabase_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL statement_timeout = '15s'"))
            profile = profile_dataset(connection, args.dataset_id, policy.governed_value_sets)
            if args.persist_profile:
                persist_profile(connection, profile)
            print(
                f"profile rows={profile['row_count']} completeness={profile['completeness_score']} "
                f"validity={profile['validity_score']} duplicate_rate={profile['duplicate_rate']}"
            )
            for title, rule in rules:
                outcome = execute_rule(connection, args.dataset_id, title, rule)
                print(
                    f"{outcome.status:4} checked={outcome.checked_count:6} "
                    f"failed={outcome.failed_count:6} rule={outcome.title} ids={outcome.failed_row_ids}"
                )
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
