"""Direct PostgreSQL/Supabase adapter for the canonical dataset contract.

This module deliberately uses SQLAlchemy Core instead of the local ORM.  The
deployed Supabase schema predates the local SQLite ORM and stores immutable raw
rows as JSON.  ``trips_canonical`` is the only execution surface used here.
"""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, create_engine, text

DATASET_ID = "dataset-nyc-yellow-taxi-50k"
CANONICAL_COLUMNS = {
    "source_row_id",
    "dataset_id",
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
}
NUMERIC_COLUMNS = {
    "passenger_count",
    "trip_distance",
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
}


@dataclass(frozen=True)
class RuleOutcome:
    rule_type: str
    title: str
    checked_count: int
    failed_count: int
    failed_row_ids: list[str]

    @property
    def status(self) -> str:
        return "FAIL" if self.failed_count else "PASS"


def normalize_postgres_url(database_url: str) -> str:
    """Select an installed PostgreSQL DBAPI without exposing connection details."""
    if not database_url.startswith(("postgres://", "postgresql://")):
        return database_url
    prefix = "postgresql+psycopg" if importlib.util.find_spec("psycopg") else "postgresql+psycopg2"
    return prefix + "://" + database_url.split("://", 1)[1]


def create_supabase_engine(database_url: str) -> Engine:
    return create_engine(
        normalize_postgres_url(database_url),
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=0,
    )


def _identifier(column: str) -> str:
    if column not in CANONICAL_COLUMNS:
        raise ValueError(f"Column is not in the canonical allowlist: {column}")
    return f'"{column}"'


def compile_supabase_rule(rule_spec: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Compile one approved rule into a SELECT of violating row IDs."""
    rule_type = str(rule_spec.get("type", ""))
    params: dict[str, Any] = {}
    base = 'SELECT "source_row_id" FROM public.trips_canonical WHERE "dataset_id" = :dataset_id AND '

    if rule_type == "not_null":
        return base + f'{_identifier(str(rule_spec.get("column", "")))} IS NULL', params

    if rule_type == "numeric_range":
        column = _identifier(str(rule_spec.get("column", "")))
        clauses = []
        if rule_spec.get("min_value") is not None:
            clauses.append(f"{column} < :min_value")
            params["min_value"] = float(rule_spec["min_value"])
        if rule_spec.get("max_value") is not None:
            clauses.append(f"{column} > :max_value")
            params["max_value"] = float(rule_spec["max_value"])
        if not clauses:
            raise ValueError("numeric_range requires min_value or max_value")
        return base + "(" + " OR ".join(clauses) + ")", params

    if rule_type == "accepted_values":
        column = _identifier(str(rule_spec.get("column", "")))
        allowed = rule_spec.get("allowed_values")
        if not isinstance(allowed, list) or not allowed or not all(isinstance(value, str) for value in allowed):
            raise ValueError("accepted_values requires a non-empty string list")
        params["allowed_values"] = json.dumps(allowed)
        return (
            base
            + f"{column} IS NOT NULL AND {column} NOT IN "
            "(SELECT jsonb_array_elements_text(CAST(:allowed_values AS jsonb)))",
            params,
        )

    if rule_type == "cross_field_comparison":
        columns = rule_spec.get("columns", [])
        operator = rule_spec.get("operator")
        if not isinstance(columns, list) or len(columns) != 2:
            raise ValueError("cross_field_comparison requires two columns")
        if operator not in {"<", "<=", ">", ">=", "=", "==", "!=", "<>"}:
            raise ValueError("Unsupported comparison operator")
        sql_operator = "=" if operator == "==" else operator
        left, right = _identifier(str(columns[0])), _identifier(str(columns[1]))
        return base + f"{left} IS NOT NULL AND {right} IS NOT NULL AND NOT ({left} {sql_operator} {right})", params

    if rule_type == "duplicate_fingerprint":
        columns = rule_spec.get("fingerprint_columns", [])
        if not isinstance(columns, list) or not columns:
            raise ValueError("duplicate_fingerprint requires fingerprint_columns")
        identifiers = ", ".join(_identifier(str(column)) for column in columns)
        return (
            'SELECT row."source_row_id" FROM public.trips_canonical AS row '
            'JOIN (SELECT "dataset_id", '
            + identifiers
            + ', MIN("source_row_id") AS keeper FROM public.trips_canonical '
            'WHERE "dataset_id" = :dataset_id GROUP BY "dataset_id", '
            + identifiers
            + ' HAVING COUNT(*) > 1) AS duplicates USING ("dataset_id", '
            + identifiers
            + ') WHERE row."dataset_id" = :dataset_id AND row."source_row_id" <> duplicates.keeper',
            params,
        )

    raise ValueError(f"Unsupported rule type: {rule_type}")


def execute_rule(
    connection,
    dataset_id: str,
    title: str,
    rule_spec: dict[str, Any],
    failed_id_limit: int = 20,
) -> RuleOutcome:
    violation_sql, params = compile_supabase_rule(rule_spec)
    params["dataset_id"] = dataset_id
    params["failed_id_limit"] = failed_id_limit
    aggregate_sql = (
        "WITH violations AS (" + violation_sql + "), capped AS ("
        "SELECT source_row_id FROM violations ORDER BY source_row_id LIMIT :failed_id_limit) "
        "SELECT (SELECT COUNT(*) FROM public.trips_canonical WHERE dataset_id = :dataset_id), "
        "(SELECT COUNT(*) FROM violations), "
        "COALESCE((SELECT json_agg(source_row_id) FROM capped), '[]'::json)"
    )
    row = connection.execute(text(aggregate_sql), params).one()
    return RuleOutcome(
        rule_type=str(rule_spec["type"]),
        title=title,
        checked_count=int(row[0]),
        failed_count=int(row[1]),
        failed_row_ids=list(row[2]),
    )


def profile_dataset(connection, dataset_id: str, governed_values: dict[str, list[str]]) -> dict[str, Any]:
    """Compute full-table aggregates without returning raw values to the caller."""
    row_count = int(
        connection.execute(
            text("SELECT COUNT(*) FROM public.trips_canonical WHERE dataset_id = :dataset_id"),
            {"dataset_id": dataset_id},
        ).scalar_one()
    )
    columns: list[dict[str, Any]] = []
    total_null_cells = 0
    for name in sorted(CANONICAL_COLUMNS - {"dataset_id"}):
        identifier = _identifier(name)
        row = connection.execute(
            text(
                f"SELECT COUNT(*) FILTER (WHERE {identifier} IS NULL), "
                f"COUNT(DISTINCT {identifier}), COUNT({identifier}) "
                "FROM public.trips_canonical WHERE dataset_id = :dataset_id"
            ),
            {"dataset_id": dataset_id},
        ).one()
        null_count, distinct_count, non_null_count = map(int, row)
        total_null_cells += null_count
        item: dict[str, Any] = {
            "name": name,
            "null_rate": null_count / row_count if row_count else 0.0,
            "non_null_count": non_null_count,
            "full_distinct_count": distinct_count,
            "uniqueness_rate": distinct_count / non_null_count if non_null_count else 0.0,
            "is_unique_full_table": bool(row_count and null_count == 0 and distinct_count == row_count),
        }
        if name in NUMERIC_COLUMNS:
            numeric = connection.execute(
                text(
                    f"SELECT MIN({identifier}), MAX({identifier}), "
                    f"COUNT(*) FILTER (WHERE {identifier} < 0), "
                    f"percentile_cont(ARRAY[0.05,0.25,0.5,0.75,0.95]) WITHIN GROUP (ORDER BY {identifier}) "
                    "FROM public.trips_canonical WHERE dataset_id = :dataset_id AND "
                    f"{identifier} IS NOT NULL"
                ),
                {"dataset_id": dataset_id},
            ).one()
            item.update(
                min_value=numeric[0],
                max_value=numeric[1],
                negative_rate=int(numeric[2]) / non_null_count if non_null_count else 0.0,
                quantiles=dict(zip(("p05", "p25", "p50", "p75", "p95"), numeric[3] or [], strict=False)),
            )
        if name in governed_values:
            invalid_count = int(
                connection.execute(
                    text(
                        f"SELECT COUNT(*) FROM public.trips_canonical WHERE dataset_id = :dataset_id "
                        f"AND {identifier} IS NOT NULL AND {identifier} NOT IN "
                        "(SELECT jsonb_array_elements_text(CAST(:allowed_values AS jsonb)))"
                    ),
                    {"dataset_id": dataset_id, "allowed_values": json.dumps(governed_values[name])},
                ).scalar_one()
            )
            item["out_of_domain_rate"] = invalid_count / non_null_count if non_null_count else 0.0
        columns.append(item)

    cross_violations = int(
        connection.execute(
            text(
                "SELECT COUNT(*) FROM public.trips_canonical WHERE dataset_id = :dataset_id "
                "AND pickup_at IS NOT NULL AND dropoff_at IS NOT NULL AND pickup_at > dropoff_at"
            ),
            {"dataset_id": dataset_id},
        ).scalar_one()
    )
    duplicate_count = int(
        connection.execute(
            text(
                "SELECT COALESCE(SUM(group_count - 1), 0) FROM (SELECT COUNT(*) AS group_count "
                "FROM public.trips_canonical WHERE dataset_id = :dataset_id "
                "GROUP BY vendor_id, pickup_at, passenger_count HAVING COUNT(*) > 1) grouped"
            ),
            {"dataset_id": dataset_id},
        ).scalar_one()
    )
    required_nulls = next(item for item in columns if item["name"] == "vendor_id")["null_rate"] * row_count
    negative_defects = sum(
        next(item for item in columns if item["name"] == name).get("negative_rate", 0.0)
        * next(item for item in columns if item["name"] == name)["non_null_count"]
        for name in ("trip_distance", "fare_amount")
    )
    domain_defects = sum(
        item.get("out_of_domain_rate", 0.0) * item["non_null_count"] for item in columns
    )
    total_defects = int(required_nulls + negative_defects + domain_defects + cross_violations + duplicate_count)
    completeness = 100.0 * (1.0 - total_null_cells / (row_count * len(columns))) if row_count else 0.0
    validity = max(0.0, 100.0 * (1.0 - total_defects / row_count)) if row_count else 0.0
    return {
        "dataset_id": dataset_id,
        "row_count": row_count,
        "completeness_score": round(completeness, 2),
        "validity_score": round(validity, 2),
        "duplicate_rate": round(100.0 * duplicate_count / row_count, 2) if row_count else 0.0,
        "columns": columns,
        "cross_field_metrics": [{
            "left_column": "pickup_at",
            "operator": "<=",
            "right_column": "dropoff_at",
            "violation_count": cross_violations,
            "violation_rate": cross_violations / row_count if row_count else 0.0,
        }],
        "generated_at": datetime.now(UTC).isoformat(),
    }


def persist_profile(connection, profile: dict[str, Any]) -> None:
    evidence_keys = [
        "profile.row_count",
        "profile.completeness_score",
        "profile.validity_score",
        "profile.duplicate_rate",
    ]
    connection.execute(
        text(
            "INSERT INTO public.dataset_profiles "
            "(dataset_id, row_count, completeness_score, validity_score, duplicate_rate, columns, evidence_keys, generated_at) "
            "VALUES (:dataset_id, :row_count, :completeness_score, :validity_score, :duplicate_rate, "
            "CAST(:columns AS json), CAST(:evidence_keys AS json), CURRENT_TIMESTAMP) "
            "ON CONFLICT (dataset_id) DO UPDATE SET row_count = EXCLUDED.row_count, "
            "completeness_score = EXCLUDED.completeness_score, validity_score = EXCLUDED.validity_score, "
            "duplicate_rate = EXCLUDED.duplicate_rate, columns = EXCLUDED.columns, "
            "evidence_keys = EXCLUDED.evidence_keys, generated_at = EXCLUDED.generated_at"
        ),
        {
            **{key: profile[key] for key in (
                "dataset_id", "row_count", "completeness_score", "validity_score", "duplicate_rate"
            )},
            "columns": json.dumps(profile["columns"]),
            "evidence_keys": json.dumps(evidence_keys),
        },
    )
    connection.execute(
        text(
            "UPDATE public.datasets SET row_count = :row_count, status = 'PROFILE_READY', "
            "updated_at = CURRENT_TIMESTAMP WHERE id = :dataset_id"
        ),
        {"dataset_id": profile["dataset_id"], "row_count": profile["row_count"]},
    )
