import hashlib
import json
import logging
import sys
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
    DatasetVersionModel,
    DqResultModel,
    DqRunModel,
    GovernanceAuditEventModel,
    GovernedArtifactModel,
    JobModel,
    ProfileModel,
    ProfileRunSnapshotModel,
    RuleConfigurationModel,
    RuleProposalModel,
    RuleVersionModel,
    SourceRowModel,
)
from src.services.dashboard_agent_workflow import generate_dashboard_proposals, get_dataset_rule_policy
from src.services.node_telemetry import record_stage, start_graph_run
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
from src.services.versioned_dataset import (
    SourceArtifactRef,
    materialize_source_artifact,
    profile_frame,
    read_verified_frame,
    schema_hash,
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


DEMO_TAXI_DATASET_ID = "dataset-nyc-yellow-taxi-50k"


def _completed_versioned_profile(db: Session, dataset_id: str):
    """The profile snapshot a versioned import already produced, if any."""
    from src.models.database import DatasetVersionModel, ProfileRunSnapshotModel

    latest = (
        db.query(DatasetVersionModel)
        .filter_by(dataset_id=dataset_id, status="READY")
        .order_by(DatasetVersionModel.version_number.desc())
        .first()
    )
    if not latest:
        return None
    return (
        db.query(ProfileRunSnapshotModel)
        .filter_by(dataset_version_id=latest.id, status="COMPLETED")
        .order_by(ProfileRunSnapshotModel.completed_at.desc())
        .first()
    )


def _uploaded_dataset_path(dataset_id: str) -> Path | None:
    for suffix in (".parquet", ".csv"):
        candidate = Path(get_settings().upload_dir) / f"{dataset_id}{suffix}"
        if candidate.exists():
            return candidate
    return None


def _materialize_versioned_dataset_path(db: Session, dataset_id: str) -> tuple[Path | None, bool]:
    """Resolve the latest immutable source artifact for a versioned dataset.

    Canonical imports are profiled from ``dataset_versions`` and are not copied
    into the legacy ``source_rows`` table. DQ execution must therefore use the
    verified source artifact even when Supabase is configured as the default
    backend; otherwise it evaluates the wrong canonical table and skips every
    user-uploaded column.
    """
    version = (
        db.query(DatasetVersionModel)
        .filter_by(dataset_id=dataset_id, status="READY")
        .order_by(DatasetVersionModel.version_number.desc())
        .first()
    )
    if not version:
        return None, False
    artifact = (
        db.query(GovernedArtifactModel)
        .filter_by(
            dataset_id=dataset_id,
            dataset_version_id=version.id,
            artifact_type="SOURCE_DATASET",
        )
        .first()
    )
    if not artifact or artifact.checksum != version.checksum:
        return None, False
    try:
        metadata = json.loads(version.source_metadata_json or "{}")
    except (TypeError, ValueError):
        metadata = {}
    storage_locator = artifact.storage_locator
    source_ref = SourceArtifactRef(
        bucket=metadata.get("bucket"),
        object_key=metadata.get("object_key") or storage_locator,
        checksum=version.checksum,
        size_bytes=int(metadata.get("size_bytes") or 0),
        format=metadata.get("format") or "csv",
        filename=metadata.get("filename") or "dataset.csv",
        storage_locator=storage_locator,
        created_by_request=False,
        version_id=metadata.get("version_id"),
    )
    return materialize_source_artifact(source_ref), storage_locator.startswith("object://")


def _profile_uploaded_dataset(db: Session, dataset_id: str, path: Path) -> dict:
    """Profile an imported CSV/Parquet without exposing its source rows to agents."""
    from src.services.versioned_dataset import inspect_upload_path
    dataset_meta = db.get(DatasetModel, dataset_id)
    inspect_upload_path(path, path.name, checksum=(dataset_meta.checksum if dataset_meta else None))
    existing_profile = db.query(ProfileModel).filter_by(dataset_id=dataset_id).first()
    if existing_profile:
        dataset = db.get(DatasetModel, dataset_id)
        if dataset and dataset.status != "PROFILE_READY":
            dataset.status = "PROFILE_READY"
            db.commit()
        return {
            "row_count": existing_profile.row_count,
            "completeness_score": existing_profile.completeness_score,
            "validity_score": existing_profile.validity_score,
            "duplicate_rate": existing_profile.duplicate_rate,
        }

    df = pd.read_parquet(path) if path.suffix.lower() == ".parquet" else pd.read_csv(path)
    if df.empty:
        raise ValueError("The imported dataset has no rows.")
    if len(df.columns) > 128:
        raise ValueError("The imported dataset has too many columns.")
    df.columns = [str(column).strip()[:128] for column in df.columns]
    if any(not column for column in df.columns) or len(set(df.columns)) != len(df.columns):
        raise ValueError("Column names must be non-empty and unique.")
    row_count = int(len(df))
    db.query(ColumnProfileModel).filter_by(profile_dataset_id=dataset_id).delete(synchronize_session=False)
    db.query(ProfileModel).filter_by(dataset_id=dataset_id).delete(synchronize_session=False)
    db.commit()
    columns = []
    total_nulls = 0
    for name in df.columns:
        series = df[name]
        non_null = series.dropna()
        null_count, non_null_count = int(series.isnull().sum()), int(len(non_null))
        total_nulls += null_count
        numeric = pd.api.types.is_numeric_dtype(series)
        data_type = "numeric" if numeric else "timestamp" if pd.api.types.is_datetime64_any_dtype(series) else "string"
        quantiles = _numeric_quantiles(non_null) if numeric and non_null_count else {}
        columns.append(
            ColumnProfileModel(
                profile_dataset_id=dataset_id,
                name=name,
                data_type=data_type,
                null_rate=_rate(null_count, row_count),
                distinct_count=int(non_null.nunique()),
                non_null_count=non_null_count,
                negative_rate=_rate(int((non_null < 0).sum()), non_null_count) if numeric and non_null_count else None,
                quantiles_json=json.dumps(quantiles),
                out_of_domain_rate=None,
                full_distinct_count=int(non_null.nunique()),
                uniqueness_rate=_rate(int(non_null.nunique()), non_null_count),
                is_unique_full_table=bool(
                    null_count == 0 and non_null_count == row_count and non_null.nunique() == row_count
                ),
                min_value=float(non_null.min()) if numeric and non_null_count else None,
                max_value=float(non_null.max()) if numeric and non_null_count else None,
                sample_value=str(non_null.iloc[0])[:256] if non_null_count else "",
            )
        )
    duplicate_rate = _rate(int(df.duplicated().sum()), row_count) * 100.0
    completeness = (1.0 - _rate(total_nulls, row_count * len(df.columns))) * 100.0
    evidence_keys = ["profile.row_count", "profile.completeness_score", "profile.duplicate_rate"]
    evidence_keys.extend(f"profile.column.{column.name}.null_rate" for column in columns)
    try:
        db.add(
            ProfileModel(
                dataset_id=dataset_id,
                row_count=row_count,
                completeness_score=round(completeness, 2),
                validity_score=100.0,
                duplicate_rate=round(duplicate_rate, 2),
                cross_field_metrics_json="[]",
                evidence_keys=json.dumps(evidence_keys),
                generated_at=utc_now(),
            )
        )
        db.add_all(columns)
        dataset = db.get(DatasetModel, dataset_id)
        if dataset:
            dataset.status, dataset.row_count, dataset.updated_at = "PROFILE_READY", row_count, utc_now()
        db.commit()
    except Exception:
        db.rollback()
        existing = db.query(ProfileModel).filter_by(dataset_id=dataset_id).first()
        if existing:
            return {
                "row_count": existing.row_count,
                "completeness_score": existing.completeness_score,
                "validity_score": existing.validity_score,
                "duplicate_rate": existing.duplicate_rate,
            }
    return {
        "row_count": row_count,
        "completeness_score": completeness,
        "validity_score": 100.0,
        "duplicate_rate": duplicate_rate,
    }


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

    db.query(ColumnProfileModel).filter(ColumnProfileModel.profile_dataset_id == dataset_id).delete(synchronize_session=False)
    db.query(ProfileModel).filter(ProfileModel.dataset_id == dataset_id).delete(synchronize_session=False)
    db.commit()

    columns = []
    evidence_keys = [
        "profile.row_count",
        "profile.completeness_score",
        "profile.validity_score",
        "profile.duplicate_rate",
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
        evidence_keys.extend(
            [
                f"{prefix}.non_null_count",
                f"{prefix}.full_distinct_count",
                f"{prefix}.uniqueness_rate",
                f"{prefix}.is_unique_full_table",
            ]
        )
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
    db.add(
        ProfileModel(
            dataset_id=dataset_id,
            row_count=int(profile_payload["row_count"]),
            completeness_score=float(profile_payload["completeness_score"]),
            validity_score=float(profile_payload["validity_score"]),
            duplicate_rate=float(profile_payload["duplicate_rate"]),
            cross_field_metrics_json=json.dumps(cross_field_metrics),
            evidence_keys=json.dumps(evidence_keys),
            generated_at=utc_now(),
        )
    )
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


def add_audit_event(
    db: Session,
    session_id: str | None,
    actor_role: str,
    action_code: str,
    entity_type: str,
    entity_id: str,
    detail: dict,
):
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
        created_at=utc_now(),
    )
    db.add(evt)
    db.commit()

def _versioned_profile_run(
    db: Session,
    job: JobModel,
    dataset_version_id: str,
    *,
    session_id: str | None,
    actor_role: str,
) -> str:
    """Profile one verified immutable source artifact and never overwrite history."""
    version = db.get(DatasetVersionModel, dataset_version_id)
    if not version or version.status != "READY":
        raise ValueError("Dataset version is not READY")
    metadata = json.loads(version.source_metadata_json or "{}")
    artifact_id = metadata.get("source_artifact_id")
    artifact = db.query(GovernedArtifactModel).filter_by(
        id=artifact_id,
        workspace_id=version.workspace_id,
        dataset_id=version.dataset_id,
        dataset_version_id=version.id,
        artifact_type="SOURCE_DATASET",
    ).first() if artifact_id else None
    if not artifact or artifact.checksum != version.checksum:
        raise ValueError("READY dataset version has no matching SOURCE_DATASET artifact")
    profile_id = f"profile-{version.id}"
    existing = db.get(ProfileRunSnapshotModel, profile_id)
    if existing and existing.status == "COMPLETED":
        return profile_id
    if existing is None:
        existing = ProfileRunSnapshotModel(
            id=profile_id,
            workspace_id=version.workspace_id,
            dataset_id=version.dataset_id,
            dataset_version_id=version.id,
            status="RUNNING",
            triggered_by=version.created_by,
            profiler_version="versioned-profiler-v1",
        )
        db.add(existing)
    else:
        existing.status = "RUNNING"
    db.add(GovernanceAuditEventModel(
        id=f"gaudit-{uuid.uuid4().hex}", workspace_id=version.workspace_id, actor_id=version.created_by,
        actor_role=actor_role, action="PROFILE_STARTED", entity_type="profile_run", entity_id=profile_id,
        dataset_id=version.dataset_id, dataset_version_id=version.id, run_id=job.id,
        correlation_id=job.correlation_id or str(uuid.uuid4()), request_metadata_json="{}",
        detail_json=json.dumps({"schema_hash": version.schema_hash}, ensure_ascii=False),
        source="WORKER", occurred_at=utc_now(),
    ))
    db.commit()
    source_ref = {
        "bucket": metadata.get("bucket"),
        "object_key": metadata.get("object_key") or artifact.storage_locator,
        "checksum": version.checksum,
        "size_bytes": int(metadata.get("size_bytes") or 0),
        "format": metadata.get("format") or "csv",
        "filename": metadata.get("filename") or "dataset.csv",
        "storage_locator": artifact.storage_locator,
        "version_id": metadata.get("version_id"),
    }
    path = None
    temporary = source_ref["storage_locator"].startswith("object://")
    try:
        path = materialize_source_artifact(source_ref)
        frame = read_verified_frame(path, checksum=version.checksum, size_bytes=source_ref["size_bytes"], schema=metadata.get("schema"))
        metrics = profile_frame(frame, schema=metadata.get("schema"))
        if int(metrics["row_count"]) != int(version.row_count) or schema_hash(metadata.get("schema") or []) != version.schema_hash:
            raise ValueError("Profile evidence does not match the immutable version contract")
        existing.row_count = version.row_count
        existing.completeness_score = metrics.get("completeness_score")
        existing.validity_score = metrics.get("validity_score")
        existing.uniqueness_score = metrics.get("uniqueness_score")
        existing.duplicate_rate = metrics.get("duplicate_rate")
        existing.quality_score = metrics.get("quality_score")
        existing.schema_json = json.dumps(metadata.get("schema") or [], ensure_ascii=False, sort_keys=True)
        existing.metrics_json = json.dumps(metrics, ensure_ascii=False, default=str)
        existing.sanitized_samples_json = "[]"
        existing.status = "COMPLETED"
        existing.completed_at = utc_now()
        db.query(DatasetModel).filter_by(id=version.dataset_id).update({"status": "PROFILE_READY", "row_count": version.row_count, "updated_at": utc_now()})
        db.add(GovernanceAuditEventModel(
            id=f"gaudit-{uuid.uuid4().hex}", workspace_id=version.workspace_id, actor_id=version.created_by,
            actor_role=actor_role, action="PROFILE_COMPLETED", entity_type="profile_run", entity_id=profile_id,
            dataset_id=version.dataset_id, dataset_version_id=version.id, run_id=job.id,
            correlation_id=job.correlation_id or str(uuid.uuid4()), request_metadata_json="{}",
            detail_json=json.dumps({"row_count": version.row_count, "schema_hash": version.schema_hash}, ensure_ascii=False),
            source="WORKER", occurred_at=utc_now(),
        ))
        db.commit()
        return profile_id
    except Exception as exc:
        db.rollback()
        failed = db.get(ProfileRunSnapshotModel, profile_id)
        if failed:
            failed.status = "FAILED"
            failed.completed_at = utc_now()
        db.add(GovernanceAuditEventModel(
            id=f"gaudit-{uuid.uuid4().hex}", workspace_id=version.workspace_id, actor_id=version.created_by,
            actor_role=actor_role, action="PROFILE_FAILED", entity_type="profile_run", entity_id=profile_id,
            dataset_id=version.dataset_id, dataset_version_id=version.id, run_id=job.id,
            correlation_id=job.correlation_id or str(uuid.uuid4()), request_metadata_json="{}",
            detail_json=json.dumps({"error": str(exc)[:500]}, ensure_ascii=False),
            source="WORKER", occurred_at=utc_now(),
        ))
        db.commit()
        raise
    finally:
        if temporary and path is not None:
            path.unlink(missing_ok=True)


def run_ingest_profile(
    job_id: str,
    dataset_id: str,
    session_id: str | None = None,
    actor_role: str = "STEWARD",
    dataset_version_id: str | None = None,
):
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
            if dataset_version_id:
                job.message = "Verifying immutable source artifact and creating versioned profile..."
                db.commit()
                try:
                    profile_id = _versioned_profile_run(
                        db, job, dataset_version_id, session_id=session_id, actor_role=actor_role
                    )
                except Exception as exc:
                    job.status = "FAILED"
                    job.error = str(exc)[:2000]
                    job.message = "Versioned profile failed"
                    db.commit()
                    raise
                job.status = "SUCCEEDED"
                job.progress = 100.0
                job.message = "Versioned profile completed"
                job.linked_entity = dataset_version_id
                db.commit()
                return profile_id
            uploaded_path = _uploaded_dataset_path(dataset_id)
            if uploaded_path:
                job.progress = 35.0
                job.message = "Reading imported dataset..."
                db.commit()
                profile_payload = _profile_uploaded_dataset(db, dataset_id, uploaded_path)
                job.status, job.progress, job.message = "SUCCEEDED", 100.0, "Imported dataset profiled"
                db.commit()
                add_audit_event(
                    db,
                    session_id=session_id,
                    actor_role=actor_role,
                    action_code="PROFILE_CREATED",
                    entity_type="dataset",
                    entity_id=dataset_id,
                    detail={"job_id": job_id, "row_count": profile_payload["row_count"], "source": "uploaded-file"},
                )
                return
            if dataset_id == DEMO_TAXI_DATASET_ID and _supabase_source_url():
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

            # Everything below seeds the NYC taxi demo fixture. That is the
            # right source for the demo dataset and for nothing else: a dataset
            # imported through the versioned route keeps no file in upload_dir,
            # so it reached here and had taxi rows written under its own name --
            # profiling data the user never uploaded and rules then measured it.
            if dataset_id != DEMO_TAXI_DATASET_ID:
                versioned = _completed_versioned_profile(db, dataset_id)
                if versioned is not None:
                    dataset = db.get(DatasetModel, dataset_id)
                    if dataset:
                        dataset.status = "PROFILE_READY"
                        dataset.row_count = versioned.row_count
                        dataset.updated_at = utc_now()
                    job.status = "SUCCEEDED"
                    job.progress = 100.0
                    job.message = "Versioned profile is already current"
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
                            "message": "Reused the versioned profile snapshot.",
                            "row_count": versioned.row_count,
                            "source": "versioned-profile-snapshot",
                        },
                    )
                    return
                raise FileNotFoundError(
                    f"No uploaded file or versioned profile for dataset {dataset_id!r}; "
                    "refusing to profile the demo fixture in its place."
                )

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
                        sample_value=sample_val,
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
                    int(df[column].isnull().sum()) for column in policy.required_identifiers if column in df.columns
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

            completeness_score = float((1.0 - _rate(total_null_cells, row_count * len(expected_columns))) * 100.0)
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
                    evidence_keys.extend(
                        f"{prefix}.quantile.{name}" for name in json.loads(column.quantiles_json or "{}")
                    )
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
                generated_at=utc_now(),
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
                detail={"job_id": job_id, "message": "Dataset ingestion and profiling completed successfully."},
            )

        except Exception as e:
            logger.error("Job INGEST_PROFILE failed: %s", str(e), exc_info=True)
            job.status = "FAILED"
            job.error = "Ingestion failed"  # Safe error message
            db.commit()
            add_audit_event(
                db,
                session_id=session_id,
                actor_role=actor_role,
                action_code="JOB_FAILED",
                entity_type="job",
                entity_id=job_id,
                detail={"error": "Dataset ingestion failed."},
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

            try:
                db.query(RuleProposalModel).filter(
                    RuleProposalModel.dataset_id == dataset_id,
                    or_(
                        RuleProposalModel.model_name.like("agent-%"),
                        RuleProposalModel.model_name.like("langgraph-%"),
                    ),
                ).delete(synchronize_session=False)
                db.commit()
            except Exception as del_err:
                logger.warning("Could not delete previous agent proposals: %s", del_err)
                db.rollback()

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
                    rule_name=p.rule_name,
                    business_rationale=p.business_rationale,
                    proposal_basis=p.proposal_basis,
                    evidence=json.dumps(p.evidence, ensure_ascii=False),
                    confidence_breakdown=json.dumps(p.confidence_breakdown, ensure_ascii=False),
                    created_at=utc_now(),
                    updated_at=utc_now(),
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
                detail={
                    "job_id": job_id,
                    "message": "Rule proposals generated successfully.",
                    "agent_mode": get_settings().agent_mode,
                },
            )

        except Exception as e:
            logger.error("Job PROPOSE_RULES failed: %s", str(e), exc_info=True)
            db.rollback()
            try:
                failed_job = db.query(JobModel).filter(JobModel.id == job_id).first()
                if failed_job:
                    failed_job.status = "FAILED"
                    failed_job.error = str(e) or "Proposals generation failed"
                    db.commit()
                    add_audit_event(
                        db,
                        session_id=session_id,
                        actor_role=actor_role,
                        action_code="JOB_FAILED",
                        entity_type="job",
                        entity_id=job_id,
                        detail={"error": str(e) or "Rule proposals job failed."}
                    )
            except Exception as rollback_err:
                logger.error("Failed to set job status to FAILED: %s", rollback_err)


# Graph 1B writes rule types in upper snake case ("RANGE", "NOT_NULL"), while
# this compiler was written against the earlier lower-case names. Nothing
# translated between them, so every rule the current agent proposes failed with
# "Unsupported rule template" and took the whole run down with it.
RULE_TYPE_ALIASES = {
    "range": "numeric_range",
    "numeric_range": "numeric_range",
    "not_null": "not_null",
    "unique": "unique",
    "accepted_values": "accepted_values",
    "allowed_values": "accepted_values",
    "cross_field_comparison": "cross_field_comparison",
    "duplicate_fingerprint": "duplicate_fingerprint",
    "duplicate": "duplicate_fingerprint",
}


def normalize_rule_type(rule_type: str) -> str:
    """Map whatever spelling a rule carries onto one compiler template."""
    return RULE_TYPE_ALIASES.get(str(rule_type or "").strip().lower(), str(rule_type or "").strip().lower())


# Rules whose subject is the dataset, not a row. They cannot be expressed as a
# SELECT of failing row ids, which is why the compiler used to refuse them and
# — before per-rule isolation — took the whole run down with them.
AGGREGATE_RULE_TYPES = {"null_rate", "row_count"}


def evaluate_aggregate_rule(
    db, dataset_id: str, rule_type: str, spec: dict, columns_allowlist: set[str], total_rows: int
) -> tuple[int, float, str]:
    """Return (failed_count, violation_rate, status) for a dataset-level rule.

    These rules carry no threshold in the specs the agent currently emits, so
    without one the check reports the measured value and passes rather than
    inventing a policy nobody set. Supply ``max_null_rate`` / ``min_row_count``
    / ``max_row_count`` and it is enforced.
    """
    rule_type = normalize_rule_type(rule_type)

    if rule_type == "row_count":
        minimum = spec.get("min_row_count")
        maximum = spec.get("max_row_count")
        # A dataset with no rows fails on any reading of a row-count rule.
        if minimum is None and maximum is None:
            return (0, 0.0, "PASS" if total_rows > 0 else "FAIL")
        below = minimum is not None and total_rows < float(minimum)
        above = maximum is not None and total_rows > float(maximum)
        return (0, 0.0, "FAIL" if (below or above) else "PASS")

    column = spec.get("column", "")
    if column not in columns_allowlist:
        raise ValueError(f"Unauthorized column access: {column}")
    null_count = (
        db.execute(
            text(
                f'SELECT COUNT(*) FROM "source_rows" WHERE "dataset_id" = :dataset_id AND "{column}" IS NULL'
            ),
            {"dataset_id": dataset_id},
        ).scalar()
        or 0
    )
    rate = (null_count / total_rows * 100) if total_rows else 0.0
    threshold = spec.get("max_null_rate")
    status = "PASS" if threshold is None else ("FAIL" if rate > float(threshold) else "PASS")
    return (null_count, rate, status)


def compile_rule_to_sql(rule_type: str, spec: dict, columns_allowlist: set[str]) -> str:
    """
    Step 7: DQ execution rule compiler.
    Resolves column names from allowed metadata list. Rejects injection characters.
    """

    rule_type = normalize_rule_type(rule_type)

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

        return (
            f'SELECT "source_row_id" FROM "source_rows" WHERE "dataset_id" = :dataset_id AND NOT ({col1} {op} {col2})'
        )

    elif rule_type == "unique":
        col = validate_col(spec.get("column", ""))
        return (
            f'SELECT "source_row_id" FROM "source_rows" WHERE "dataset_id" = :dataset_id '
            f"AND {col} IS NOT NULL AND {col} IN ("
            f'SELECT {col} FROM "source_rows" WHERE "dataset_id" = :dataset_id '
            f"AND {col} IS NOT NULL GROUP BY {col} HAVING COUNT(*) > 1)"
        )

    elif rule_type == "duplicate_fingerprint":
        cols = spec.get("fingerprint_columns", [])
        if not cols:
            raise ValueError("Duplicate fingerprint rule requires fingerprint_columns.")
        validated_cols = [validate_col(c) for c in cols]
        cols_expr = ", ".join(validated_cols)

        # SQLite duplicate check
        return (
            f'SELECT "source_row_id" FROM "source_rows" WHERE "dataset_id" = :dataset_id '
            f"AND ({cols_expr}) IN ("
            f'SELECT {cols_expr} FROM "source_rows" WHERE "dataset_id" = :dataset_id '
            f"GROUP BY {cols_expr} HAVING COUNT(*) > 1)"
        )

    else:
        raise ValueError(f"Unsupported rule template: {rule_type}")


def execute_uploaded_rule(uploaded_path: Path, rule_type: str, spec: dict) -> tuple[int, list[str], int]:
    """Execute a data quality rule on an uploaded CSV/Parquet dataset via pandas."""
    from src.services.versioned_dataset import inspect_upload_path
    inspect_upload_path(uploaded_path, uploaded_path.name)
    df = pd.read_parquet(uploaded_path) if uploaded_path.suffix.lower() == ".parquet" else pd.read_csv(uploaded_path)
    total_rows = len(df)

    if "source_row_id" in df.columns:
        row_ids = df["source_row_id"].astype(str).tolist()
    else:
        row_ids = [str(i + 1) for i in range(total_rows)]

    failed_indices = []

    if rule_type == "not_null":
        col = spec.get("column", "")
        if col in df.columns:
            failed_indices = df.index[df[col].isna()].tolist()

    elif rule_type == "numeric_range":
        col = spec.get("column", "")
        if col in df.columns:
            series = pd.to_numeric(df[col], errors="coerce")
            min_v = spec.get("min_value")
            max_v = spec.get("max_value")
            cond = pd.Series(False, index=df.index)
            if min_v is not None:
                cond = cond | (series < min_v)
            if max_v is not None:
                cond = cond | (series > max_v)
            failed_indices = df.index[cond | series.isna()].tolist()

    elif rule_type == "accepted_values":
        col = spec.get("column", "")
        allowed = [str(v) for v in spec.get("allowed_values", [])]
        if col in df.columns:
            series = df[col].astype(str)
            failed_indices = df.index[df[col].notna() & (~series.isin(allowed))].tolist()

    elif rule_type == "cross_field_comparison":
        cols = spec.get("columns", [])
        op = spec.get("operator", "")
        if len(cols) == 2 and cols[0] in df.columns and cols[1] in df.columns:
            s1 = pd.to_numeric(df[cols[0]], errors="coerce")
            s2 = pd.to_numeric(df[cols[1]], errors="coerce")
            if op == "<":
                valid = s1 < s2
            elif op == "<=":
                valid = s1 <= s2
            elif op == ">":
                valid = s1 > s2
            elif op == ">=":
                valid = s1 >= s2
            elif op == "==":
                valid = s1 == s2
            elif op == "!=":
                valid = s1 != s2
            else:
                valid = pd.Series(True, index=df.index)
            failed_indices = df.index[~valid].tolist()

    elif rule_type == "duplicate_fingerprint":
        cols = spec.get("fingerprint_columns", [])
        valid_cols = [c for c in cols if c in df.columns]
        if valid_cols:
            failed_indices = df.index[df.duplicated(subset=valid_cols, keep=False)].tolist()

    failed_row_ids = [row_ids[i] for i in failed_indices if i < len(row_ids)]
    return total_rows, failed_row_ids, len(failed_row_ids)


def run_dq_checks(
    job_id: str,
    run_id: str,
    session_id: str | None = None,
    actor_role: str = "STEWARD",
    *,
    trigger_anomaly: bool = True,
    finalize_job: bool = True,
    workflow_run_id: str | None = None,
):
    """
    Dashboard execution adapter.

    Approved dashboard rule versions are the only input. The SQL comes from fixed
    ``compile_rule_to_sql`` templates; the legacy execution graph and its LLM repair
    loop are intentionally not a source of executable SQL in this product flow.
    """
    engine = get_engine()
    source_engine = None
    source_connection = None
    versioned_path: Path | None = None
    versioned_temporary = False
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
            versioned_path, versioned_temporary = _materialize_versioned_dataset_path(db, dataset_id)
            if versioned_path is None and dataset_id == DEMO_TAXI_DATASET_ID:
                source_url = _supabase_source_url()
                if source_url:
                    source_engine = create_supabase_engine(source_url)
                    source_connection = source_engine.connect()
            rule_ids = json.loads(dq_run.rule_ids)

            # Get approved rule versions
            rule_versions = (
                db.query(RuleVersionModel)
                .filter(RuleVersionModel.id.in_(rule_ids), RuleVersionModel.status == "APPROVED")
                .all()
            )

            if not rule_versions:
                raise ValueError("No approved rules found for execution.")

            # The local fallback needs a profile-derived allowlist. The Supabase
            # adapter carries its own canonical-column allowlist.
            cols = db.query(ColumnProfileModel).filter(ColumnProfileModel.profile_dataset_id == dataset_id).all()
            columns_allowlist = {c.name for c in cols}
            if not columns_allowlist:
                columns_allowlist = {
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
                }

            # Delete any existing results for this run
            db.query(DqResultModel).filter(DqResultModel.run_id == run_id).delete()
            db.commit()

            total_checked = 0
            total_failed = 0

            uploaded_path = versioned_path or _uploaded_dataset_path(dataset_id)

            # Report the stages this executor really performs, so the Graph 2
            # panel reflects the run instead of showing five dbt nodes that were
            # never part of this path.
            start_graph_run(
                workflow_run_id=workflow_run_id,
                dataset_id=dataset_id,
                dq_run_id=run_id,
            )
            with record_stage(
                "G2_DIRECT", "compile_rules", "DETERMINISTIC", {"rules": len(rule_versions)}
            ) as compile_summary:
                compile_summary["rules"] = len(rule_versions)
            with record_stage("G2_DIRECT", "validate_sql", "DETERMINISTIC") as validate_summary:
                validate_summary["policy"] = "single SELECT, no comments or multi-statements"
            execute_stage = record_stage(
                "G2_DIRECT", "execute_checks", "DETERMINISTIC", {"rules": len(rule_versions)}
            )
            execute_summary = execute_stage.__enter__()

            # Execute each rule
            for idx, rv in enumerate(rule_versions):
                spec = json.loads(rv.rule_spec)
                rule_type = spec.get("type", "")
                aggregate_status: str | None = None
                aggregate_rate: float | None = None

                prop = db.query(RuleProposalModel).filter(RuleProposalModel.id == rv.rule_proposal_id).first()
                title = prop.title if prop else f"Rule check {rv.id}"
                try:
                  if source_connection is not None:
                    outcome = execute_supabase_rule(source_connection, dataset_id, title, spec)
                    total_rows = outcome.checked_count
                    failed_ids = outcome.failed_row_ids
                    failed_count = outcome.failed_count
                  elif uploaded_path is not None:
                    total_rows, failed_ids, failed_count = execute_uploaded_rule(uploaded_path, rule_type, spec)
                  elif normalize_rule_type(rule_type) in AGGREGATE_RULE_TYPES:
                    total_rows = (
                        db.execute(
                            text("SELECT COUNT(*) FROM source_rows WHERE dataset_id = :dataset_id"),
                            {"dataset_id": dataset_id},
                        ).scalar()
                        or 0
                    )
                    failed_count, aggregate_rate, aggregate_status = evaluate_aggregate_rule(
                        db, dataset_id, rule_type, spec, columns_allowlist, total_rows
                    )
                    # A dataset-level rule has no offending rows to point at.
                    failed_ids = []
                  else:
                    sql_query = compile_rule_to_sql(rule_type, spec, columns_allowlist)
                    sql_clean = sql_query.strip().upper()
                    if not sql_clean.startswith("SELECT"):
                        raise ValueError("Runner only permits SELECT statements")
                    if ";" in sql_query or "--" in sql_query or "/*" in sql_query or "*/" in sql_query:
                        raise ValueError("Runner rejects semicolons, multi-statements, and SQL comments")
                    total_rows = (
                        db.execute(
                            text("SELECT COUNT(*) FROM source_rows WHERE dataset_id = :dataset_id"),
                            {"dataset_id": dataset_id},
                        ).scalar()
                        or 0
                    )
                    params = {"dataset_id": dataset_id}
                    normalized_type = normalize_rule_type(rule_type)
                    if normalized_type == "numeric_range":
                        params["min_value"] = spec.get("min_value")
                        params["max_value"] = spec.get("max_value")
                    elif normalized_type == "accepted_values":
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
                      # A dataset-level rule decides its own outcome; a row-level
                      # one fails when it found offending rows.
                      status=aggregate_status or ("FAIL" if failed_count > 0 else "PASS"),
                      checked_count=total_rows,
                      failed_count=failed_count,
                      failed_row_ids=json.dumps(capped_failed_ids),
                      violation_rate=aggregate_rate,
                  )
                  db.add(res)
                except Exception as rule_error:
                    # One rule the compiler cannot express must not take the
                    # other thirty-nine down with it. Record why it was skipped
                    # and carry on: a partial result set is far more useful than
                    # a failed run with nothing in it.
                    logger.warning("Rule %s could not be executed: %s", rv.id, rule_error)
                    db.rollback()
                    db.add(
                        DqResultModel(
                            run_id=run_id,
                            rule_id=rv.id,
                            rule_title=title,
                            status="SKIPPED",
                            checked_count=0,
                            failed_count=0,
                            failed_row_ids=json.dumps([]),
                            error_message=str(rule_error)[:1000],
                        )
                    )

                # Update progress
                job.progress = 10.0 + (80.0 * (idx + 1) / len(rule_versions))
                job.message = f"Executed {idx + 1}/{len(rule_versions)} rule checks..."
                db.commit()

            execute_summary["checked"] = total_checked
            execute_summary["failed"] = total_failed
            execute_stage.__exit__(None, None, None)

            with record_stage(
                "G2_DIRECT", "persist_results", "DETERMINISTIC", {"checked": total_checked}
            ) as persist_summary:
                persist_summary["results"] = len(rule_versions)
                persist_summary["failed"] = total_failed

            # Finalize run
            dq_run.status = "SUCCEEDED"
            dq_run.total_failed = total_failed
            dq_run.total_checked = total_checked
            dq_run.completed_at = utc_now()

            # The steward workflow owns the subsequent anomaly analysis.  Its
            # single visible job must remain running until the report artifact
            # is durable, otherwise the UI stops polling too early.
            job.status = "SUCCEEDED" if finalize_job else "RUNNING"
            job.progress = 100.0 if finalize_job else 90.0
            job.message = "Completed" if finalize_job else "Checks completed; preparing analysis report..."
            completed_at = utc_now()
            db.query(RuleConfigurationModel).filter(
                RuleConfigurationModel.rule_proposal_id.in_([rule.rule_proposal_id for rule in rule_versions])
            ).update({RuleConfigurationModel.last_run_at: completed_at}, synchronize_session=False)
            db.commit()

            # Legacy callers get Graph 3 in the background.  The steward workflow
            # invokes it in its own worker so it can persist a linked artifact.
            if trigger_anomaly and "pytest" not in sys.modules:
                try:
                    import asyncio
                    import threading

                    from src.agents.graph import run_anomaly_graph

                    logger.info("Triggering Graph 3 Anomaly Analysis asynchronously for run_id=%s", run_id)

                    def _trigger_anomaly():
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            loop.run_until_complete(run_anomaly_graph(execution_run_id=run_id, dataset_id=dataset_id))
                        except Exception as ex:
                            logger.error("Failed executing Graph 3 from background thread: %s", ex, exc_info=True)
                        finally:
                            loop.close()

                    threading.Thread(target=_trigger_anomaly, daemon=True).start()
                except Exception as ae:
                    logger.error("Failed to trigger Graph 3 Anomaly Analysis: %s", ae, exc_info=True)

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
                    "execution_engine": "supabase-canonical-v1"
                    if source_connection is not None
                    else "typed-compiler-v1",
                },
            )

        except Exception as e:
            logger.error("DQ Checks failed: %s", str(e), exc_info=True)
            db.rollback()
            try:
                failed_job = db.query(JobModel).filter(JobModel.id == job_id).first()
                failed_dq_run = db.query(DqRunModel).filter(DqRunModel.id == run_id).first()
                if failed_dq_run:
                    failed_dq_run.status = "FAILED"
                if failed_job:
                    failed_job.status = "FAILED"
                    failed_job.error = str(e) or "Data quality run failed"
                db.commit()
                add_audit_event(
                    db,
                    session_id=session_id,
                    actor_role=actor_role,
                    action_code="JOB_FAILED",
                    entity_type="job",
                    entity_id=job_id,
                    detail={"error": str(e)}
                )
            except Exception as inner_ex:
                logger.error("Failed recording job error to DB: %s", str(inner_ex), exc_info=True)
            add_audit_event(
                db,
                session_id=session_id,
                actor_role=actor_role,
                action_code="JOB_FAILED",
                entity_type="job",
                entity_id=job_id,
                detail={"error": "DQ run failed."},
            )
        finally:
            if source_connection is not None:
                source_connection.close()
            if source_engine is not None:
                source_engine.dispose()
            if versioned_temporary and versioned_path is not None:
                versioned_path.unlink(missing_ok=True)
