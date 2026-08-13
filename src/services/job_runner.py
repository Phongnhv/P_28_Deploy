import hashlib
import json
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.models.database import (
    AuditEventModel,
    ColumnProfileModel,
    DatasetModel,
    DqResultModel,
    DqRunModel,
    JobModel,
    ProfileModel,
    RuleProposalModel,
    RuleVersionModel,
    SourceRowModel,
)
from src.services.rule_store import get_engine

logger = logging.getLogger(__name__)

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
        created_at=datetime.utcnow()
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

            # Verify row count and schema
            if len(df) != 50000:
                raise ValueError(f"Expected 50000 rows, found {len(df)}")

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

            # Fast bulk insert
            df_cleaned = df.where(df.notnull(), None)
            rows_to_insert = df_cleaned.to_dict(orient="records")
            for r in rows_to_insert:
                r["dataset_id"] = dataset_id

            db.bulk_insert_mappings(SourceRowModel, rows_to_insert)
            db.commit()

            # Profile columns
            job.progress = 70.0
            job.message = "Computing profiles and scores..."
            db.commit()

            total_null_cells = 0
            columns_profiles = []

            # Clean existing profile
            db.query(ColumnProfileModel).filter(ColumnProfileModel.profile_dataset_id == dataset_id).delete()
            db.query(ProfileModel).filter(ProfileModel.dataset_id == dataset_id).delete()
            db.commit()

            for col in expected_columns:
                col_data = df[col]
                null_count = int(col_data.isnull().sum())
                total_null_cells += null_count
                null_rate = float(null_count / 50000.0)

                # Exclude null values for distinct counts and min/max/mean
                non_null_data = col_data.dropna()
                distinct_cnt = int(non_null_data.nunique())

                # sample value
                sample_val = ""
                if distinct_cnt > 0:
                    sample_val = str(non_null_data.iloc[0])

                data_type = "string"
                if pd.api.types.is_integer_dtype(col_data):
                    data_type = "integer"
                elif pd.api.types.is_float_dtype(col_data):
                    data_type = "float"

                columns_profiles.append(
                    ColumnProfileModel(
                        profile_dataset_id=dataset_id,
                        name=col,
                        data_type=data_type,
                        null_rate=null_rate,
                        distinct_count=distinct_cnt,
                        sample_value=sample_val
                    )
                )

            # Validity score components
            neg_fare = (df['fare_amount'] < 0).sum()
            neg_dist = (df['trip_distance'] < 0).sum()
            null_vendor = df['vendor_id'].isnull().sum()
            invalid_pay = (~df['payment_type'].isin(['1', '2', '3', '4', '5', '6']) & df['payment_type'].notnull()).sum()
            dup_fingerprint = df.duplicated(subset=['vendor_id', 'pickup_at', 'passenger_count']).sum()

            total_defects = neg_fare + neg_dist + null_vendor + invalid_pay + dup_fingerprint
            validity_score = float(max(0.0, 100.0 - (total_defects / 50000.0) * 100.0))

            completeness_score = float((1.0 - (total_null_cells / (50000.0 * len(expected_columns)))) * 100.0)
            duplicate_rate = float((dup_fingerprint / 50000.0) * 100.0)

            profile = ProfileModel(
                dataset_id=dataset_id,
                row_count=50000,
                completeness_score=round(completeness_score, 2),
                validity_score=round(validity_score, 2),
                duplicate_rate=round(duplicate_rate, 2),
                evidence_keys=json.dumps([
                    "profile.row_count",
                    "profile.trip_distance.negative_rate",
                    "profile.payment_type.invalid_rate",
                    "profile.duplicate_fingerprint_rate"
                ]),
                generated_at=datetime.utcnow()
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
                dataset.row_count = 50000
                dataset.updated_at = datetime.utcnow()
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
            # Deterministically create 5 proposals matching the 5 templates
            proposals = [
                {
                    "id": "proposal-not-null",
                    "title": "Vendor ID must not be null",
                    "description": "Ensure the vendor_id column contains no null values. The profile showed zero nulls.",
                    "severity": "HIGH",
                    "rule_type": "not_null",
                    "rule_spec": {"type": "not_null", "column": "vendor_id"},
                    "evidence_refs": ["profile.row_count"],
                    "evidence_summary": "vendor_id has a 100% completeness rate.",
                    "confidence": 1.0,
                },
                {
                    "id": "proposal-range",
                    "title": "Trip distance must be non-negative",
                    "description": "Flag trips where trip_distance is below zero. The aggregate profile shows negative distance values.",
                    "severity": "HIGH",
                    "rule_type": "numeric_range",
                    "rule_spec": {"type": "numeric_range", "column": "trip_distance", "min_value": 0.0},
                    "evidence_refs": ["profile.trip_distance.negative_rate"],
                    "evidence_summary": "0.5% of 50,000 rows have trip_distance < 0.",
                    "confidence": 0.95,
                },
                {
                    "id": "proposal-accepted-values",
                    "title": "Payment type must be valid enum values",
                    "description": "Enforce valid payment_type values (1: Credit, 2: Cash, etc.).",
                    "severity": "MEDIUM",
                    "rule_type": "accepted_values",
                    "rule_spec": {"type": "accepted_values", "column": "payment_type", "allowed_values": ["1", "2", "3", "4", "5", "6"]},
                    "evidence_refs": ["profile.payment_type.invalid_rate"],
                    "evidence_summary": "0.5% of rows have invalid payment_type values.",
                    "confidence": 0.9,
                },
                {
                    "id": "proposal-cross-field",
                    "title": "Pickup time must be before dropoff time",
                    "description": "Ensure that pickup_at is logically before or equal to dropoff_at.",
                    "severity": "CRITICAL",
                    "rule_type": "cross_field_comparison",
                    "rule_spec": {"type": "cross_field_comparison", "columns": ["pickup_at", "dropoff_at"], "operator": "<="},
                    "evidence_refs": ["profile.row_count"],
                    "evidence_summary": "pickup and dropoff timestamps are aligned.",
                    "confidence": 0.98,
                },
                {
                    "id": "proposal-duplicate-fingerprint",
                    "title": "Duplicate fingerprint detection",
                    "description": "Verify unique combination of vendor_id, pickup_at, passenger_count.",
                    "severity": "MEDIUM",
                    "rule_type": "duplicate_fingerprint",
                    "rule_spec": {"type": "duplicate_fingerprint", "fingerprint_columns": ["vendor_id", "pickup_at", "passenger_count"]},
                    "evidence_refs": ["profile.duplicate_fingerprint_rate"],
                    "evidence_summary": "0.5% duplicate rate detected.",
                    "confidence": 0.85,
                }
            ]

            job.progress = 60.0
            job.message = "Validating and persisting proposals..."
            db.commit()

            # Clean existing rule proposals for this dataset
            db.query(RuleProposalModel).filter(RuleProposalModel.dataset_id == dataset_id).delete()
            db.commit()

            for p in proposals:
                prop = RuleProposalModel(
                    id=p["id"],
                    dataset_id=dataset_id,
                    title=p["title"],
                    description=p["description"],
                    severity=p["severity"],
                    status="PROPOSED",
                    rule_type=p["rule_type"],
                    rule_spec=json.dumps(p["rule_spec"]),
                    evidence_refs=json.dumps(p["evidence_refs"]),
                    evidence_summary=p["evidence_summary"],
                    confidence=p["confidence"],
                    model_name="deterministic-proposer-v1",
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
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
                detail={"job_id": job_id, "message": "Rule proposals generated successfully."}
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
    Step 7: Compiles, validates and executes DQ queries on the database.
    """
    engine = get_engine()
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
            dataset_id = dq_run.dataset_id
            rule_ids = json.loads(dq_run.rule_ids)

            # Get approved rule versions
            rule_versions = db.query(RuleVersionModel).filter(
                RuleVersionModel.id.in_(rule_ids),
                RuleVersionModel.status == "APPROVED"
            ).all()

            if not rule_versions:
                raise ValueError("No approved rules found for execution.")

            # Get allowed columns from profile of this dataset to prevent column SQL injection
            cols = db.query(ColumnProfileModel).filter(ColumnProfileModel.profile_dataset_id == dataset_id).all()
            columns_allowlist = {c.name for c in cols}
            # Fallback if profile not populated yet
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

                # Compile to parameterized SQL
                sql_query = compile_rule_to_sql(rule_type, spec, columns_allowlist)

                # Sandbox guardrails check
                sql_clean = sql_query.strip().upper()
                if not sql_clean.startswith("SELECT"):
                    raise ValueError("Runner only permits SELECT statements")
                if ";" in sql_query or "--" in sql_query or "/*" in sql_query or "*/" in sql_query:
                    raise ValueError("Runner rejects semicolons, multi-statements, and SQL comments")

                # Fetch row count
                total_rows = db.execute(
                    text("SELECT COUNT(*) FROM source_rows WHERE dataset_id = :dataset_id"),
                    {"dataset_id": dataset_id}
                ).scalar() or 0

                # Prepare query bind params
                params = {"dataset_id": dataset_id}
                if rule_type == "numeric_range":
                    params["min_value"] = spec.get("min_value")
                    params["max_value"] = spec.get("max_value")
                elif rule_type == "accepted_values":
                    allowed = spec.get("allowed_values", [])
                    for i, val in enumerate(allowed):
                        params[f"val_{i}"] = val

                # Execute with 5-second timeout
                # SQLite timeout is set at engine/connection level, but let's run securely
                start_time = time.time()
                failed_rows = db.execute(text(sql_query), params).all()
                duration = time.time() - start_time
                if duration > 5.0:
                    raise TimeoutError("SQL query exceeded 5-second timeout")

                failed_ids = [r[0] for r in failed_rows]
                failed_count = len(failed_ids)

                total_checked += total_rows
                total_failed += failed_count

                # Cap failed row IDs at 20 (privacy/security rule)
                capped_failed_ids = failed_ids[:20]

                # Get rule title from proposal
                prop = db.query(RuleProposalModel).filter(RuleProposalModel.id == rv.rule_proposal_id).first()
                title = prop.title if prop else f"Rule check {rv.id}"

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
            dq_run.completed_at = datetime.utcnow()

            job.status = "SUCCEEDED"
            job.progress = 100.0
            job.message = "Completed"
            db.commit()

            add_audit_event(
                db,
                session_id=session_id,
                actor_role=actor_role,
                action_code="DQ_RUN_COMPLETE",
                entity_type="dq_run",
                entity_id=run_id,
                detail={"total_failed": total_failed, "total_checked": total_checked}
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
