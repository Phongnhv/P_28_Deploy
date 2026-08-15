import hashlib
import json
import logging
import time
import uuid
from pathlib import Path

import pandas as pd
from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from src.config import get_settings
from src.models.database import (
    AuditEventModel,
    ColumnProfileModel,
    DatasetModel,
    DqResultModel,
    DqRunModel,
    JobModel,
    ProfileModel,
    RuleConfigurationModel,
    RuleProposalModel,
    RuleVersionModel,
    SourceRowModel,
)
from src.services.dashboard_agent_workflow import generate_dashboard_proposals, get_dataset_rule_policy
from src.services.rule_store import get_engine
from src.services.supabase_dataset import (
    NUMERIC_COLUMNS,
    create_supabase_engine,
    is_postgres_database_url,
)
from src.services.supabase_dataset import (
    execute_rule as execute_supabase_rule,
)
from src.services.supabase_dataset import (
    persist_profile as persist_supabase_profile,
)
from src.services.supabase_dataset import (
    profile_dataset as profile_supabase_dataset,
)
from src.time_utils import utc_now

logger = logging.getLogger(__name__)


def _rate(count: int, denominator: int) -> float:
    return float(count / denominator) if denominator > 0 else 0.0


def _supabase_source_url() -> str | None:
    """Select Supabase for a PostgreSQL source unless local mode is explicit."""
    settings = get_settings()
    if settings.dq_execution_backend == "local":
        return None
    source_url = settings.supabase_database_url or settings.database_url
    if settings.dq_execution_backend == "supabase":
        if not is_postgres_database_url(source_url):
            raise ValueError("Supabase execution requires a PostgreSQL SUPABASE_DATABASE_URL or DATABASE_URL")
        return source_url
    return source_url if is_postgres_database_url(source_url) else None


def _profile_supabase_into_dashboard(db: Session, dataset_id: str) -> dict:
    """Profile canonical Supabase rows and persist only aggregate dashboard evidence."""
    policy = get_dataset_rule_policy(dataset_id)
    governed_values = policy.governed_value_sets if policy else {}
    source_engine = create_supabase_engine(_supabase_source_url() or "")
    try:
        with source_engine.begin() as connection:
            profile_payload = profile_supabase_dataset(connection, dataset_id, governed_values)
            persist_supabase_profile(connection, profile_payload)
    finally:
        source_engine.dispose()

    db.query(ColumnProfileModel).filter(ColumnProfileModel.profile_dataset_id == dataset_id).delete()
    db.query(ProfileModel).filter(ProfileModel.dataset_id == dataset_id).delete()
    db.flush()

    columns = []
    evidence_keys = [
        "profile.row_count", "profile.completeness_score", "profile.validity_score", "profile.duplicate_rate",
    ]
    for item in profile_payload["columns"]:
        name = item["name"]
        quantiles = item.get("quantiles", {})
        data_type = "float" if name in NUMERIC_COLUMNS else "timestamp" if name.endswith("_at") else "string"
        columns.append(
            ColumnProfileModel(
                profile_dataset_id=dataset_id,
                name=name,
                data_type=data_type,
                null_rate=float(item["null_rate"]),
                distinct_count=int(item["full_distinct_count"]),
                non_null_count=int(item["non_null_count"]),
                negative_rate=float(item["negative_rate"]) if item.get("negative_rate") is not None else None,
                quantiles_json=json.dumps(quantiles),
                out_of_domain_rate=(
                    float(item["out_of_domain_rate"]) if item.get("out_of_domain_rate") is not None else None
                ),
                full_distinct_count=int(item["full_distinct_count"]),
                uniqueness_rate=float(item["uniqueness_rate"]),
                is_unique_full_table=bool(item["is_unique_full_table"]),
                min_value=float(item["min_value"]) if item.get("min_value") is not None else None,
                max_value=float(item["max_value"]) if item.get("max_value") is not None else None,
                sample_value="Aggregate profile only",
            )
        )
        prefix = f"profile.column.{name}"
        evidence_keys.extend([
            f"{prefix}.non_null_count", f"{prefix}.full_distinct_count", f"{prefix}.uniqueness_rate",
            f"{prefix}.is_unique_full_table",
        ])
        if item.get("negative_rate") is not None:
            evidence_keys.append(f"{prefix}.negative_rate")
            evidence_keys.extend(f"{prefix}.quantile.{key}" for key in quantiles)
        if item.get("out_of_domain_rate") is not None:
            evidence_keys.append(f"{prefix}.out_of_domain_rate")

    cross_field_metrics = profile_payload["cross_field_metrics"]
    evidence_keys.extend(
        f"profile.cross_field.{metric['left_column']}.{metric['operator']}.{metric['right_column']}.violation_rate"
        for metric in cross_field_metrics
    )
    db.add(ProfileModel(
        dataset_id=dataset_id,
        row_count=int(profile_payload["row_count"]),
        completeness_score=float(profile_payload["completeness_score"]),
        validity_score=float(profile_payload["validity_score"]),
        duplicate_rate=float(profile_payload["duplicate_rate"]),
        cross_field_metrics_json=json.dumps(cross_field_metrics),
        evidence_keys=json.dumps(evidence_keys),
        generated_at=utc_now(),
    ))
    db.add_all(columns)
    dataset = db.query(DatasetModel).filter(DatasetModel.id == dataset_id).first()
    if dataset:
        dataset.status = "PROFILE_READY"
        dataset.row_count = int(profile_payload["row_count"])
        dataset.updated_at = utc_now()
    db.commit()
    return profile_payload


def _numeric_quantiles(values: pd.Series) -> dict[str, float]:
    """Return deterministic full-table quantiles for a non-null numeric series."""
    if values.empty:
        return {}
    quantiles = values.quantile([0.05, 0.25, 0.5, 0.75, 0.95])
    return {
        "p05": float(quantiles.loc[0.05]),
        "p25": float(quantiles.loc[0.25]),
        "p50": float(quantiles.loc[0.5]),
        "p75": float(quantiles.loc[0.75]),
        "p95": float(quantiles.loc[0.95]),
    }


def _cross_field_metrics(df: pd.DataFrame, dataset_id: str) -> list[dict]:
    """Evaluate configured cross-field relationships without exposing row values."""
    policy = get_dataset_rule_policy(dataset_id)
    if not policy:
        return []
    operators = {
        "<": lambda left, right: left < right,
        "<=": lambda left, right: left <= right,
        ">": lambda left, right: left > right,
        ">=": lambda left, right: left >= right,
        "==": lambda left, right: left == right,
        "!=": lambda left, right: left != right,
    }
    metrics: list[dict] = []
    for rule in policy.cross_field_rules:
        if rule.left_column not in df.columns or rule.right_column not in df.columns:
            continue
        comparable = df[[rule.left_column, rule.right_column]].dropna()
        checked_count = int(len(comparable))
        if checked_count:
            passes = operators[rule.operator](comparable[rule.left_column], comparable[rule.right_column])
            violation_count = int((~passes).sum())
        else:
            violation_count = 0
        metrics.append(
            {
                "left_column": rule.left_column,
                "operator": rule.operator,
                "right_column": rule.right_column,
                "checked_count": checked_count,
                "violation_count": violation_count,
                "violation_rate": _rate(violation_count, checked_count),
            }
        )
    return metrics

def add_audit_event(db: Session, session_id: str | None, actor_role: str, action_code: str, entity_type: str, entity_id: str, detail: dict):
    """
    Utility function to write an audit trail row.
    """
    event_id = f"evt_{uuid.uuid4().hex}"
    evt = AuditEventModel(
        id=event_id,
        session_id=session_id,
        actor_role=actor_role,
        action_code=action_code,
        entity_type=entity_type,
        entity_id=entity_id,
        detail_json=json.dumps(detail, ensure_ascii=False),
        created_at=utc_now()
    )
    db.add(evt)
    db.commit()

def run_ingest_profile(job_id: str, dataset_id: str, session_id: str | None = None, actor_role: str = "STEWARD"):
    """
    Step 5: Background ingestion and profiling of NYC Yellow Taxi Parquet.
    """
    engine = get_engine()
    with Session(engine) as db:
        job = db.query(JobModel).filter(JobModel.id == job_id).first()
        if not job:
            return

        job.status = "RUNNING"
        job.progress = 10.0
        job.message = "Verifying manifest and Parquet file checksum..."
        db.commit()

        try:
            if _supabase_source_url():
                job.progress = 35.0
                job.message = "Profiling canonical Supabase rows..."
                db.commit()
                profile_payload = _profile_supabase_into_dashboard(db, dataset_id)
                job.status = "SUCCEEDED"
                job.progress = 100.0
                job.message = "Completed from Supabase canonical dataset"
                db.commit()
                add_audit_event(
                    db,
                    session_id=session_id,
                    actor_role=actor_role,
                    action_code="PROFILE_CREATED",
                    entity_type="dataset",
                    entity_id=dataset_id,
                    detail={
                        "job_id": job_id,
                        "message": "Supabase canonical profile completed successfully.",
                        "row_count": profile_payload["row_count"],
                        "source": "supabase-canonical-v1",
                    },
                )
                return

            # Check manifest and path
            parquet_path = Path("data/yellow_tripdata_2025/semantic_data/yellow_tripdata_2025_semantic_50k.parquet")
            if not parquet_path.exists():
                parquet_path = Path("c:/DATA/P-028") / parquet_path

            if not parquet_path.exists():
                raise FileNotFoundError(f"Parquet file not found at {parquet_path}")

            # Verify checksum
            expected_sha = "b1549ceb43dee8e083e34d81b22db37c3afa401737e831c7ed63fb83a5baeff7"
            sha256 = hashlib.sha256()
            with open(parquet_path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    sha256.update(chunk)
            calculated_sha = sha256.hexdigest().lower()
            if calculated_sha != expected_sha:
                raise ValueError(f"Checksum mismatch. Calculated: {calculated_sha}, Expected: {expected_sha}")

            # Load parquet data
            job.progress = 30.0
            job.message = "Loading immutable raw rows..."
            db.commit()

            df = pd.read_parquet(parquet_path)
            row_count = int(len(df))

            # Verify row count and schema
            if row_count != 50000:
                raise ValueError(f"Expected 50000 rows, found {row_count}")

            expected_columns = [
                'source_row_id', 'vendor_id', 'pickup_at', 'dropoff_at', 'passenger_count',
                'trip_distance', 'rate_code_id', 'store_and_fwd_flag', 'pickup_location_id',
                'dropoff_location_id', 'payment_type', 'fare_amount', 'extra', 'mta_tax',
                'tip_amount', 'tolls_amount', 'improvement_surcharge', 'total_amount',
                'congestion_surcharge', 'airport_fee', 'cbd_congestion_fee'
            ]
            for col in expected_columns:
                if col not in df.columns:
                    raise ValueError(f"Missing required column in Parquet schema: {col}")

            # Ingest immutable rows
            job.progress = 50.0
            job.message = "Bulk inserting raw source rows..."
            db.commit()

            # Clean existing source rows for this dataset to remain idempotent
            db.query(SourceRowModel).filter(SourceRowModel.dataset_id == dataset_id).delete()

            # Fast bulk insert using vectorized pandas where mapping (object type conversion prevents float coercion of None)
            df["dataset_id"] = dataset_id
            insert_df = df.astype(object).where(pd.notnull(df), None)
            rows_to_insert = insert_df.to_dict(orient="records")

            db.bulk_insert_mappings(SourceRowModel, rows_to_insert)
            db.commit()

            # Profile columns
            job.progress = 70.0
            job.message = "Computing profiles and scores..."
            db.commit()

            total_null_cells = 0
            columns_profiles = []
            policy = get_dataset_rule_policy(dataset_id)
            governed_value_sets = policy.governed_value_sets if policy else {}

            # Clean existing profile
            db.query(ColumnProfileModel).filter(ColumnProfileModel.profile_dataset_id == dataset_id).delete()
            db.query(ProfileModel).filter(ProfileModel.dataset_id == dataset_id).delete()
            db.commit()

            for col in expected_columns:
                col_data = df[col]
                null_count = int(col_data.isnull().sum())
                total_null_cells += null_count
                null_rate = _rate(null_count, row_count)

                # All persisted dashboard aggregates are computed over the full table.
                non_null_data = col_data.dropna()
                non_null_count = int(len(non_null_data))
                distinct_cnt = int(non_null_data.nunique())
                uniqueness_rate = _rate(distinct_cnt, non_null_count)
                is_unique_full_table = bool(row_count > 0 and null_count == 0 and distinct_cnt == row_count)

                # sample value
                sample_val = ""
                if distinct_cnt > 0:
                    sample_val = str(non_null_data.iloc[0])

                data_type = "string"
                min_value = None
                max_value = None
                negative_rate = None
                quantiles: dict[str, float] = {}
                if pd.api.types.is_integer_dtype(col_data):
                    data_type = "integer"
                elif pd.api.types.is_float_dtype(col_data):
                    data_type = "float"
                if pd.api.types.is_numeric_dtype(col_data) and not non_null_data.empty:
                    min_value = float(non_null_data.min())
                    max_value = float(non_null_data.max())
                    negative_rate = _rate(int((non_null_data < 0).sum()), non_null_count)
                    quantiles = _numeric_quantiles(non_null_data)

                out_of_domain_rate = None
                allowed_values = governed_value_sets.get(col)
                if allowed_values is not None:
                    normalized = non_null_data.astype(str)
                    invalid_count = int((~normalized.isin(allowed_values)).sum())
                    out_of_domain_rate = _rate(invalid_count, non_null_count)

                columns_profiles.append(
                    ColumnProfileModel(
                        profile_dataset_id=dataset_id,
                        name=col,
                        data_type=data_type,
                        null_rate=null_rate,
                        distinct_count=distinct_cnt,
                        non_null_count=non_null_count,
                        negative_rate=negative_rate,
                        quantiles_json=json.dumps(quantiles),
                        out_of_domain_rate=out_of_domain_rate,
                        full_distinct_count=distinct_cnt,
                        uniqueness_rate=uniqueness_rate,
                        is_unique_full_table=is_unique_full_table,
                        min_value=min_value,
                        max_value=max_value,
                        sample_value=sample_val
                    )
                )

            # Validity score uses the same versioned policy as profiling and proposals.
            cross_field_metrics = _cross_field_metrics(df, dataset_id)
            fingerprint_columns = policy.duplicate_fingerprint_columns if policy else []
            dup_fingerprint = (
                int(df.duplicated(subset=fingerprint_columns).sum())
                if fingerprint_columns and set(fingerprint_columns).issubset(df.columns)
                else 0
            )
            total_defects = dup_fingerprint
            if policy:
                total_defects += sum(
                    int(df[column].isnull().sum())
                    for column in policy.required_identifiers
                    if column in df.columns
                )
                total_defects += sum(
                    int((df[column].dropna() < 0).sum())
                    for column in policy.nonnegative_columns
                    if column in df.columns and pd.api.types.is_numeric_dtype(df[column])
                )
                for column, allowed_values in policy.governed_value_sets.items():
                    if column not in df.columns:
                        continue
                    non_null_values = df[column].dropna().astype(str)
                    total_defects += int((~non_null_values.isin(allowed_values)).sum())
            total_defects += sum(metric["violation_count"] for metric in cross_field_metrics)
            validity_score = float(max(0.0, 100.0 - _rate(int(total_defects), row_count) * 100.0))

            completeness_score = float(
                (1.0 - _rate(total_null_cells, row_count * len(expected_columns))) * 100.0
            )
            duplicate_rate = float(_rate(int(dup_fingerprint), row_count) * 100.0)
            evidence_keys = [
                "profile.row_count",
                "profile.completeness_score",
                "profile.validity_score",
                "profile.duplicate_rate",
            ]
            for column in columns_profiles:
                prefix = f"profile.column.{column.name}"
                evidence_keys.extend(
                    [
                        f"{prefix}.non_null_count",
                        f"{prefix}.full_distinct_count",
                        f"{prefix}.uniqueness_rate",
                        f"{prefix}.is_unique_full_table",
                    ]
                )
                if column.negative_rate is not None:
                    evidence_keys.append(f"{prefix}.negative_rate")
                    evidence_keys.extend(f"{prefix}.quantile.{name}" for name in json.loads(column.quantiles_json or "{}"))
                if column.out_of_domain_rate is not None:
                    evidence_keys.append(f"{prefix}.out_of_domain_rate")
            evidence_keys.extend(
                f"profile.cross_field.{metric['left_column']}.{metric['operator']}.{metric['right_column']}.violation_rate"
                for metric in cross_field_metrics
            )

            profile = ProfileModel(
                dataset_id=dataset_id,
                row_count=row_count,
                completeness_score=round(completeness_score, 2),
                validity_score=round(validity_score, 2),
                duplicate_rate=round(duplicate_rate, 2),
                cross_field_metrics_json=json.dumps(cross_field_metrics),
                evidence_keys=json.dumps(evidence_keys),
                generated_at=utc_now()
            )
            db.add(profile)
            db.commit()

            for cp in columns_profiles:
                db.add(cp)
            db.commit()

            # Update dataset status
            dataset = db.query(DatasetModel).filter(DatasetModel.id == dataset_id).first()
            if dataset:
                dataset.status = "PROFILE_READY"
                dataset.row_count = row_count
                dataset.updated_at = utc_now()
                db.commit()

            job.status = "SUCCEEDED"
            job.progress = 100.0
            job.message = "Completed"
            db.commit()

            add_audit_event(
                db,
                session_id=session_id,
                actor_role=actor_role,
                action_code="PROFILE_CREATED",
                entity_type="dataset",
                entity_id=dataset_id,
                detail={"job_id": job_id, "message": "Dataset ingestion and profiling completed successfully."}
            )

        except Exception as e:
            logger.error("Job INGEST_PROFILE failed: %s", str(e), exc_info=True)
            job.status = "FAILED"
            job.error = "Ingestion failed" # Safe error message
            db.commit()
            add_audit_event(
                db,
                session_id=session_id,
                actor_role=actor_role,
                action_code="JOB_FAILED",
                entity_type="job",
                entity_id=job_id,
                detail={"error": "Dataset ingestion failed."}
            )

def run_propose_rules(job_id: str, dataset_id: str, session_id: str | None = None, actor_role: str = "STEWARD"):
    """
    Step 6: Background rule proposals generation.
    """
    engine = get_engine()
    with Session(engine) as db:
        job = db.query(JobModel).filter(JobModel.id == job_id).first()
        if not job:
            return

        job.status = "RUNNING"
        job.progress = 20.0
        job.message = "Preparing allow-listed evidence..."
        db.commit()

        try:
            proposals = generate_dashboard_proposals(db, dataset_id)

            job.progress = 60.0
            job.message = "Validating and persisting proposals..."
            db.commit()

            # Replace only previous agent drafts. Manual and approved stewardship
            # decisions remain immutable dashboard records.
            db.query(RuleProposalModel).filter(
                RuleProposalModel.dataset_id == dataset_id,
                or_(
                    RuleProposalModel.model_name.like("agent-%"),
                    RuleProposalModel.model_name.like("langgraph-%"),
                ),
                RuleProposalModel.status.in_(["PROPOSED", "REJECTED"]),
            ).delete(synchronize_session=False)
            db.commit()

            for p in proposals:
                prop = RuleProposalModel(
                    id=p.id,
                    dataset_id=dataset_id,
                    title=p.title,
                    description=p.description,
                    severity=p.severity,
                    status="PROPOSED",
                    rule_type=p.rule_type,
                    rule_spec=json.dumps(p.rule_spec),
                    evidence_refs=json.dumps(p.evidence_refs),
                    evidence_summary=p.evidence_summary,
                    confidence=p.confidence,
                    model_name=p.model_name,
                    created_at=utc_now(),
                    updated_at=utc_now()
                )
                db.add(prop)

            job.status = "SUCCEEDED"
            job.progress = 100.0
            job.message = "Completed"
            db.commit()

            add_audit_event(
                db,
                session_id=session_id,
                actor_role=actor_role,
                action_code="PROPOSALS_CREATED",
                entity_type="dataset",
                entity_id=dataset_id,
                detail={"job_id": job_id, "message": "Rule proposals generated successfully.", "agent_mode": get_settings().agent_mode}
            )

        except Exception as e:
            logger.error("Job PROPOSE_RULES failed: %s", str(e), exc_info=True)
            job.status = "FAILED"
            job.error = "Proposals generation failed"
            db.commit()
            add_audit_event(
                db,
                session_id=session_id,
                actor_role=actor_role,
                action_code="JOB_FAILED",
                entity_type="job",
                entity_id=job_id,
                detail={"error": "Rule proposals job failed."}
            )

def compile_rule_to_sql(rule_type: str, spec: dict, columns_allowlist: set[str]) -> str:
    """
    Step 7: DQ execution rule compiler.
    Resolves column names from allowed metadata list. Rejects injection characters.
    """
    def validate_col(c: str):
        if c not in columns_allowlist:
            raise ValueError(f"Unauthorized column access: {c}")
        # Enforce basic injection guardrails on identifier
        if any(char in c for char in (";", "--", "/*", "*/", "'", '"', "`", "\n")):
            raise ValueError(f"Malicious characters in column: {c}")
        return f'"{c}"'

    if rule_type == "not_null":
        col = validate_col(spec.get("column", ""))
        return f'SELECT "source_row_id" FROM "source_rows" WHERE "dataset_id" = :dataset_id AND {col} IS NULL'

    elif rule_type == "numeric_range":
        col = validate_col(spec.get("column", ""))
        min_v = spec.get("min_value")
        max_v = spec.get("max_value")
        if min_v is not None and max_v is not None:
            return f'SELECT "source_row_id" FROM "source_rows" WHERE "dataset_id" = :dataset_id AND ({col} < :min_value OR {col} > :max_value)'
        elif min_v is not None:
            return f'SELECT "source_row_id" FROM "source_rows" WHERE "dataset_id" = :dataset_id AND {col} < :min_value'
        elif max_v is not None:
            return f'SELECT "source_row_id" FROM "source_rows" WHERE "dataset_id" = :dataset_id AND {col} > :max_value'
        else:
            raise ValueError("Numeric range rule requires at least one of min_value or max_value.")

    elif rule_type == "accepted_values":
        col = validate_col(spec.get("column", ""))
        allowed = spec.get("allowed_values", [])
        if not allowed:
            raise ValueError("Accepted values rule requires allowed_values list.")

        # Verify allowed values are strictly strings and contain no injection chars
        for val in allowed:
            if not isinstance(val, str) or any(char in val for char in (";", "--", "/*", "*/", "'", '"', "`")):
                raise ValueError("Malicious value in accepted values list")

        # Build query safely. The values are bind parameters
        placeholders = [f":val_{i}" for i in range(len(allowed))]
        placeholders_str = ", ".join(placeholders)
        return f'SELECT "source_row_id" FROM "source_rows" WHERE "dataset_id" = :dataset_id AND {col} IS NOT NULL AND {col} NOT IN ({placeholders_str})'

    elif rule_type == "cross_field_comparison":
        cols = spec.get("columns", [])
        if len(cols) != 2:
            raise ValueError("Cross field comparison requires exactly 2 columns.")
        col1 = validate_col(cols[0])
        col2 = validate_col(cols[1])
        op = spec.get("operator", "")
        if op not in ("<", "<=", ">", ">=", "==", "!="):
            raise ValueError(f"Unauthorized comparison operator: {op}")

        return f'SELECT "source_row_id" FROM "source_rows" WHERE "dataset_id" = :dataset_id AND NOT ({col1} {op} {col2})'

    elif rule_type == "duplicate_fingerprint":
        cols = spec.get("fingerprint_columns", [])
        if not cols:
            raise ValueError("Duplicate fingerprint rule requires fingerprint_columns.")
        validated_cols = [validate_col(c) for c in cols]
        cols_expr = ", ".join(validated_cols)

        # SQLite duplicate check
        return (
            f'SELECT "source_row_id" FROM "source_rows" WHERE "dataset_id" = :dataset_id '
            f'AND ({cols_expr}) IN ('
            f'SELECT {cols_expr} FROM "source_rows" WHERE "dataset_id" = :dataset_id '
            f'GROUP BY {cols_expr} HAVING COUNT(*) > 1)'
        )

    else:
        raise ValueError(f"Unsupported rule template: {rule_type}")

def run_dq_checks(job_id: str, run_id: str, session_id: str | None = None, actor_role: str = "STEWARD"):
    """
    Dashboard execution adapter.

    Approved dashboard rule versions are the only input. The SQL comes from fixed
    ``compile_rule_to_sql`` templates; the legacy execution graph and its LLM repair
    loop are intentionally not a source of executable SQL in this product flow.
    """
    engine = get_engine()
    source_engine = None
    source_connection = None
    with Session(engine) as db:
        job = db.query(JobModel).filter(JobModel.id == job_id).first()
        if not job:
            return

        dq_run = db.query(DqRunModel).filter(DqRunModel.id == run_id).first()
        if not dq_run:
            return

        job.status = "RUNNING"
        job.progress = 10.0
        job.message = "Claiming approved rule set..."
        dq_run.status = "RUNNING"
        db.commit()

        try:
            source_url = _supabase_source_url()
            if source_url:
                source_engine = create_supabase_engine(source_url)
                source_connection = source_engine.connect()
            dataset_id = dq_run.dataset_id
            rule_ids = json.loads(dq_run.rule_ids)

            # Get approved rule versions
            rule_versions = db.query(RuleVersionModel).filter(
                RuleVersionModel.id.in_(rule_ids),
                RuleVersionModel.status == "APPROVED"
            ).all()

            if not rule_versions:
                raise ValueError("No approved rules found for execution.")

            # The local fallback needs a profile-derived allowlist. The Supabase
            # adapter carries its own canonical-column allowlist.
            cols = db.query(ColumnProfileModel).filter(ColumnProfileModel.profile_dataset_id == dataset_id).all()
            columns_allowlist = {c.name for c in cols}
            if not columns_allowlist:
                columns_allowlist = {
                    'source_row_id', 'vendor_id', 'pickup_at', 'dropoff_at', 'passenger_count',
                    'trip_distance', 'rate_code_id', 'store_and_fwd_flag', 'pickup_location_id',
                    'dropoff_location_id', 'payment_type', 'fare_amount', 'extra', 'mta_tax',
                    'tip_amount', 'tolls_amount', 'improvement_surcharge', 'total_amount',
                    'congestion_surcharge', 'airport_fee', 'cbd_congestion_fee'
                }

            # Delete any existing results for this run
            db.query(DqResultModel).filter(DqResultModel.run_id == run_id).delete()
            db.commit()

            total_checked = 0
            total_failed = 0

            # Execute each rule
            for idx, rv in enumerate(rule_versions):
                spec = json.loads(rv.rule_spec)
                rule_type = spec.get("type", "")

                prop = db.query(RuleProposalModel).filter(RuleProposalModel.id == rv.rule_proposal_id).first()
                title = prop.title if prop else f"Rule check {rv.id}"
                if source_connection is not None:
                    outcome = execute_supabase_rule(source_connection, dataset_id, title, spec)
                    total_rows = outcome.checked_count
                    failed_ids = outcome.failed_row_ids
                    failed_count = outcome.failed_count
                else:
                    sql_query = compile_rule_to_sql(rule_type, spec, columns_allowlist)
                    sql_clean = sql_query.strip().upper()
                    if not sql_clean.startswith("SELECT"):
                        raise ValueError("Runner only permits SELECT statements")
                    if ";" in sql_query or "--" in sql_query or "/*" in sql_query or "*/" in sql_query:
                        raise ValueError("Runner rejects semicolons, multi-statements, and SQL comments")
                    total_rows = db.execute(
                        text("SELECT COUNT(*) FROM source_rows WHERE dataset_id = :dataset_id"),
                        {"dataset_id": dataset_id},
                    ).scalar() or 0
                    params = {"dataset_id": dataset_id}
                    if rule_type == "numeric_range":
                        params["min_value"] = spec.get("min_value")
                        params["max_value"] = spec.get("max_value")
                    elif rule_type == "accepted_values":
                        for i, value in enumerate(spec.get("allowed_values", [])):
                            params[f"val_{i}"] = value
                    start_time = time.time()
                    failed_rows = db.execute(text(sql_query), params).all()
                    if time.time() - start_time > 5.0:
                        raise TimeoutError("SQL query exceeded 5-second timeout")
                    failed_ids = [row[0] for row in failed_rows]
                    failed_count = len(failed_ids)

                total_checked += total_rows
                total_failed += failed_count

                # Cap failed row IDs at 20 (privacy/security rule)
                capped_failed_ids = failed_ids[:20]

                res = DqResultModel(
                    run_id=run_id,
                    rule_id=rv.id,
                    rule_title=title,
                    status="FAIL" if failed_count > 0 else "PASS",
                    checked_count=total_rows,
                    failed_count=failed_count,
                    failed_row_ids=json.dumps(capped_failed_ids)
                )
                db.add(res)

                # Update progress
                job.progress = 10.0 + (80.0 * (idx + 1) / len(rule_versions))
                job.message = f"Executed {idx+1}/{len(rule_versions)} rule checks..."
                db.commit()

            # Finalize run
            dq_run.status = "SUCCEEDED"
            dq_run.total_failed = total_failed
            dq_run.total_checked = total_checked
            dq_run.completed_at = utc_now()

            job.status = "SUCCEEDED"
            job.progress = 100.0
            job.message = "Completed"
            completed_at = utc_now()
            db.query(RuleConfigurationModel).filter(
                RuleConfigurationModel.rule_proposal_id.in_([rule.rule_proposal_id for rule in rule_versions])
            ).update({RuleConfigurationModel.last_run_at: completed_at}, synchronize_session=False)
            db.commit()

            add_audit_event(
                db,
                session_id=session_id,
                actor_role=actor_role,
                action_code="DQ_RUN_COMPLETE",
                entity_type="dq_run",
                entity_id=run_id,
                detail={
                    "total_failed": total_failed,
                    "total_checked": total_checked,
                    "execution_engine": "supabase-canonical-v1" if source_connection is not None else "typed-compiler-v1",
                }
            )

        except Exception as e:
            logger.error("DQ Checks failed: %s", str(e), exc_info=True)
            dq_run.status = "FAILED"
            job.status = "FAILED"
            job.error = "Data quality run failed"
            db.commit()
            add_audit_event(
                db,
                session_id=session_id,
                actor_role=actor_role,
                action_code="JOB_FAILED",
                entity_type="job",
                entity_id=job_id,
                detail={"error": "DQ run failed."}
            )
        finally:
            if source_connection is not None:
                source_connection.close()
            if source_engine is not None:
                source_engine.dispose()
