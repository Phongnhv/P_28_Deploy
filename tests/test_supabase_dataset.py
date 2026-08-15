import json

import pytest

from src.services.supabase_dataset import compile_supabase_rule, normalize_postgres_url


def test_normalize_postgres_url_selects_explicit_driver():
    normalized = normalize_postgres_url("postgresql://example.invalid/db")
    assert normalized.startswith(("postgresql+psycopg://", "postgresql+psycopg2://"))
    assert normalized.endswith("example.invalid/db")


@pytest.mark.parametrize(
    ("spec", "fragment"),
    [
        ({"type": "not_null", "column": "vendor_id"}, '"vendor_id" IS NULL'),
        (
            {"type": "numeric_range", "column": "trip_distance", "min_value": 0},
            '"trip_distance" < :min_value',
        ),
        (
            {
                "type": "cross_field_comparison",
                "columns": ["pickup_at", "dropoff_at"],
                "operator": "<=",
            },
            '"pickup_at" <= "dropoff_at"',
        ),
        (
            {
                "type": "duplicate_fingerprint",
                "fingerprint_columns": ["vendor_id", "pickup_at"],
            },
            "HAVING COUNT(*) > 1",
        ),
    ],
)
def test_compile_supabase_rule_uses_fixed_templates(spec, fragment):
    sql, _ = compile_supabase_rule(spec)
    assert fragment in sql
    assert "public.trips_canonical" in sql


def test_compile_accepted_values_binds_json_instead_of_interpolating_values():
    sql, params = compile_supabase_rule(
        {"type": "accepted_values", "column": "payment_type", "allowed_values": ["Cash", "Credit card"]}
    )
    assert "Cash" not in sql
    assert json.loads(params["allowed_values"]) == ["Cash", "Credit card"]


def test_compile_supabase_rule_rejects_unknown_columns_and_operators():
    with pytest.raises(ValueError, match="allowlist"):
        compile_supabase_rule({"type": "not_null", "column": "vendor_id; DROP TABLE trips_raw"})
    with pytest.raises(ValueError, match="operator"):
        compile_supabase_rule(
            {
                "type": "cross_field_comparison",
                "columns": ["pickup_at", "dropoff_at"],
                "operator": "<=; DROP TABLE trips_raw",
            }
        )
