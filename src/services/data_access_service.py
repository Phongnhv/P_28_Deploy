"""Workspace-scoped authorization and local PostgreSQL dashboard queries.

This module deliberately sits outside agent prompts and graph behavior. It
resolves immutable dataset lineage and effective grants before exposing profile,
rule, report, artifact, overview, or audit data.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from src.models.database import (
    AnalysisSummaryModel,
    DataGroupMembershipModel,
    DatasetGovernanceModel,
    DatasetGrantModel,
    DatasetModel,
    DatasetStewardModel,
    DatasetVersionModel,
    GovernanceAuditEventModel,
    GovernedArtifactModel,
    ProfileRunSnapshotModel,
    RuleReviewSnapshotModel,
    WorkspaceMembershipModel,
)
from src.time_utils import utc_now
from src.services.versioned_dataset import materialize_source_artifact, read_verified_frame

PERMISSIONS = {
    "DISCOVER",
    "VIEW_PROFILE",
    "VIEW_REPORTS",
    "VIEW_ROWS",
    "RUN_ANALYSIS",
    "MANAGE",
}
PERMISSION_CLOSURE = {
    "DISCOVER": {"DISCOVER"},
    "VIEW_PROFILE": {"DISCOVER", "VIEW_PROFILE"},
    "VIEW_REPORTS": {"DISCOVER", "VIEW_REPORTS"},
    "VIEW_ROWS": {"DISCOVER", "VIEW_ROWS"},
    "RUN_ANALYSIS": {"DISCOVER", "RUN_ANALYSIS"},
    "MANAGE": set(PERMISSIONS),
}
REDACTED_KEYS = {
    "authorization",
    "cookie",
    "email",
    "password",
    "phone",
    "secret",
    "token",
}


class AccessDeniedError(Exception):
    """The principal knows the resource exists but lacks an operation permission."""


class ResourceNotFoundError(Exception):
    """The resource is absent or intentionally hidden from the principal."""


@dataclass(frozen=True)
class AccessContext:
    user_id: str
    workspace_id: str


def _active_membership(db: Session, ctx: AccessContext) -> WorkspaceMembershipModel:
    membership = (
        db.query(WorkspaceMembershipModel)
        .filter_by(workspace_id=ctx.workspace_id, user_id=ctx.user_id, status="ACTIVE")
        .first()
    )
    if not membership:
        raise ResourceNotFoundError("Workspace not found")
    return membership


def _dataset_governance(db: Session, ctx: AccessContext, dataset_id: str) -> DatasetGovernanceModel:
    governance = (
        db.query(DatasetGovernanceModel).filter_by(workspace_id=ctx.workspace_id, dataset_id=dataset_id).first()
    )
    if not governance:
        raise ResourceNotFoundError("Dataset not found")
    return governance


def _dataset_version(
    db: Session,
    ctx: AccessContext,
    dataset_id: str,
    dataset_version_id: str,
) -> DatasetVersionModel:
    version = (
        db.query(DatasetVersionModel)
        .filter_by(
            id=dataset_version_id,
            workspace_id=ctx.workspace_id,
            dataset_id=dataset_id,
        )
        .first()
    )
    if not version:
        raise ResourceNotFoundError("Dataset version not found")
    return version


def _active_grant(grant: DatasetGrantModel, now: datetime) -> bool:
    if grant.revoked_at is not None:
        return False
    if grant.expires_at is None:
        return True
    expires_at = grant.expires_at
    comparable_now = now
    if expires_at.tzinfo is None and comparable_now.tzinfo is not None:
        comparable_now = comparable_now.replace(tzinfo=None)
    return expires_at > comparable_now


def effective_permissions(
    db: Session,
    ctx: AccessContext,
    dataset_id: str,
    dataset_version_id: str | None = None,
) -> set[str]:
    membership = _active_membership(db, ctx)
    governance = _dataset_governance(db, ctx, dataset_id)
    if dataset_version_id is not None:
        _dataset_version(db, ctx, dataset_id, dataset_version_id)

    if membership.role == "ADMIN" or governance.owner_user_id == ctx.user_id:
        return set(PERMISSIONS)

    steward = (
        db.query(DatasetStewardModel.id).filter_by(dataset_id=dataset_id, user_id=ctx.user_id, revoked_at=None).first()
    )
    if steward:
        return set(PERMISSIONS)

    group_ids = [
        row[0]
        for row in db.query(DataGroupMembershipModel.group_id).filter_by(user_id=ctx.user_id, status="ACTIVE").all()
    ]
    principal_clauses = [
        and_(DatasetGrantModel.grantee_type == "USER", DatasetGrantModel.grantee_id == ctx.user_id),
        and_(
            DatasetGrantModel.grantee_type == "WORKSPACE",
            DatasetGrantModel.grantee_id == ctx.workspace_id,
        ),
    ]
    if group_ids:
        principal_clauses.append(
            and_(
                DatasetGrantModel.grantee_type == "GROUP",
                DatasetGrantModel.grantee_id.in_(group_ids),
            )
        )

    query = db.query(DatasetGrantModel).filter(
        DatasetGrantModel.workspace_id == ctx.workspace_id,
        DatasetGrantModel.dataset_id == dataset_id,
        or_(*principal_clauses),
    )
    if dataset_version_id is not None:
        query = query.filter(
            or_(
                DatasetGrantModel.dataset_version_id.is_(None),
                DatasetGrantModel.dataset_version_id == dataset_version_id,
            )
        )

    now = utc_now()
    permissions: set[str] = set()
    for grant in query.all():
        if _active_grant(grant, now):
            permissions.update(PERMISSION_CLOSURE.get(grant.permission, set()))
    return permissions


def require_permission(
    db: Session,
    ctx: AccessContext,
    dataset_id: str,
    permission: str,
    dataset_version_id: str | None = None,
) -> set[str]:
    if permission not in PERMISSIONS:
        raise ValueError(f"Unknown dataset permission: {permission}")
    permissions = effective_permissions(db, ctx, dataset_id, dataset_version_id)
    if permission in permissions:
        return permissions
    if "DISCOVER" not in permissions:
        raise ResourceNotFoundError("Dataset not found")
    raise AccessDeniedError(f"Missing permission: {permission}")


def _sanitize_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower() in REDACTED_KEYS else _sanitize_json(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_json(item) for item in value]
    return value


def append_audit_event(
    db: Session,
    ctx: AccessContext,
    *,
    actor_role: str,
    action: str,
    entity_type: str,
    entity_id: str,
    dataset_id: str | None = None,
    dataset_version_id: str | None = None,
    run_id: str | None = None,
    correlation_id: str | None = None,
    source: str = "API",
    request_metadata: dict[str, Any] | None = None,
    detail: dict[str, Any] | None = None,
) -> GovernanceAuditEventModel:
    event = GovernanceAuditEventModel(
        id=f"gaudit-{uuid.uuid4().hex}",
        workspace_id=ctx.workspace_id,
        actor_id=ctx.user_id,
        actor_role=actor_role,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        dataset_id=dataset_id,
        dataset_version_id=dataset_version_id,
        run_id=run_id,
        correlation_id=correlation_id or str(uuid.uuid4()),
        request_metadata_json=json.dumps(_sanitize_json(request_metadata or {}), ensure_ascii=False),
        detail_json=json.dumps(_sanitize_json(detail or {}), ensure_ascii=False),
        source=source,
        occurred_at=utc_now(),
    )
    db.add(event)
    return event


def grant_dataset_permissions(
    db: Session,
    ctx: AccessContext,
    *,
    dataset_id: str,
    grantee_type: str,
    grantee_id: str,
    permissions: Iterable[str],
    dataset_version_id: str | None = None,
    expires_at: datetime | None = None,
    grant_set_id: str | None = None,
) -> str:
    membership = _active_membership(db, ctx)
    require_permission(db, ctx, dataset_id, "MANAGE", dataset_version_id)
    if grantee_type not in {"USER", "GROUP", "WORKSPACE"}:
        raise ValueError("Invalid grantee type")
    normalized = set(permissions)
    if not normalized or not normalized.issubset(PERMISSIONS):
        raise ValueError("Invalid permissions")
    if dataset_version_id is not None:
        _dataset_version(db, ctx, dataset_id, dataset_version_id)

    next_grant_set_id = grant_set_id or f"grant-set-{uuid.uuid4().hex}"
    for permission in sorted(normalized):
        db.add(
            DatasetGrantModel(
                id=f"grant-{uuid.uuid4().hex}",
                grant_set_id=next_grant_set_id,
                workspace_id=ctx.workspace_id,
                dataset_id=dataset_id,
                dataset_version_id=dataset_version_id,
                grantee_type=grantee_type,
                grantee_id=grantee_id,
                permission=permission,
                granted_by=ctx.user_id,
                expires_at=expires_at,
            )
        )
    append_audit_event(
        db,
        ctx,
        actor_role=membership.role,
        action="DATASET_SHARED",
        entity_type="dataset_grant_set",
        entity_id=next_grant_set_id,
        dataset_id=dataset_id,
        dataset_version_id=dataset_version_id,
        detail={"grantee_type": grantee_type, "permissions": sorted(normalized)},
    )
    db.commit()
    return next_grant_set_id


def revoke_grant_set(db: Session, ctx: AccessContext, grant_set_id: str) -> int:
    membership = _active_membership(db, ctx)
    grants = (
        db.query(DatasetGrantModel)
        .filter_by(workspace_id=ctx.workspace_id, grant_set_id=grant_set_id, revoked_at=None)
        .all()
    )
    if not grants:
        raise ResourceNotFoundError("Grant set not found")
    dataset_id = grants[0].dataset_id
    require_permission(db, ctx, dataset_id, "MANAGE", grants[0].dataset_version_id)
    revoked_at = utc_now()
    for grant in grants:
        grant.revoked_at = revoked_at
    append_audit_event(
        db,
        ctx,
        actor_role=membership.role,
        action="DATASET_ACCESS_REVOKED",
        entity_type="dataset_grant_set",
        entity_id=grant_set_id,
        dataset_id=dataset_id,
        dataset_version_id=grants[0].dataset_version_id,
        detail={"revoked_count": len(grants)},
    )
    db.commit()
    return len(grants)


def _empty_metrics() -> dict[str, Any]:
    return {
        "dataset_count": 0,
        "version_count": 0,
        "profiling_runs": 0,
        "completed_profiling_runs": 0,
        "completion_rate": None,
        "quality_score": None,
        "rules": {"approved": 0, "rejected": 0},
        "tests": {"pass": 0, "fail": 0},
        "anomaly_count": 0,
    }


def get_overview_metrics(db: Session, ctx: AccessContext) -> dict[str, Any]:
    """Compute authorization-filtered metrics from the local control-plane DB."""
    _active_membership(db, ctx)
    buckets = {"owned": _empty_metrics(), "shared": _empty_metrics()}
    quality_values: dict[str, list[float]] = {"owned": [], "shared": []}
    recent_runs: list[dict[str, Any]] = []

    governed_datasets = db.query(DatasetGovernanceModel).filter_by(workspace_id=ctx.workspace_id).all()
    for governance in governed_datasets:
        dataset_permissions = effective_permissions(db, ctx, governance.dataset_id)
        if "DISCOVER" not in dataset_permissions:
            continue
        bucket_name = "owned" if governance.owner_user_id == ctx.user_id else "shared"
        bucket = buckets[bucket_name]
        bucket["dataset_count"] += 1

        versions = (
            db.query(DatasetVersionModel)
            .filter_by(workspace_id=ctx.workspace_id, dataset_id=governance.dataset_id)
            .all()
        )
        for version in versions:
            version_permissions = effective_permissions(db, ctx, governance.dataset_id, version.id)
            if "DISCOVER" not in version_permissions:
                continue
            bucket["version_count"] += 1

            if "VIEW_PROFILE" in version_permissions:
                profiles = (
                    db.query(ProfileRunSnapshotModel)
                    .filter_by(
                        workspace_id=ctx.workspace_id,
                        dataset_id=governance.dataset_id,
                        dataset_version_id=version.id,
                    )
                    .all()
                )
                bucket["profiling_runs"] += len(profiles)
                completed = [profile for profile in profiles if profile.status == "COMPLETED"]
                bucket["completed_profiling_runs"] += len(completed)
                quality_values[bucket_name].extend(
                    profile.quality_score for profile in completed if profile.quality_score is not None
                )
                recent_runs.extend(
                    {
                        "run_id": profile.id,
                        "run_type": "PROFILE",
                        "dataset_id": governance.dataset_id,
                        "dataset_version_id": version.id,
                        "status": profile.status,
                        "created_at": profile.created_at.isoformat(),
                    }
                    for profile in profiles
                )

            if "VIEW_REPORTS" in version_permissions:
                reviews = (
                    db.query(RuleReviewSnapshotModel)
                    .filter_by(dataset_version_id=version.id, workspace_id=ctx.workspace_id)
                    .all()
                )
                bucket["rules"]["approved"] += sum(row.status == "APPROVED" for row in reviews)
                bucket["rules"]["rejected"] += sum(row.status == "REJECTED" for row in reviews)
                summaries = (
                    db.query(AnalysisSummaryModel)
                    .filter_by(dataset_version_id=version.id, workspace_id=ctx.workspace_id)
                    .all()
                )
                bucket["tests"]["pass"] += sum(row.tests_passed for row in summaries)
                bucket["tests"]["fail"] += sum(row.tests_failed for row in summaries)
                bucket["anomaly_count"] += sum(row.anomaly_count for row in summaries)
                recent_runs.extend(
                    {
                        "run_id": row.id,
                        "run_type": "ANALYSIS",
                        "dataset_id": governance.dataset_id,
                        "dataset_version_id": version.id,
                        "status": row.status,
                        "created_at": row.created_at.isoformat(),
                    }
                    for row in summaries
                )

    for bucket_name, bucket in buckets.items():
        if bucket["profiling_runs"]:
            bucket["completion_rate"] = round(bucket["completed_profiling_runs"] / bucket["profiling_runs"], 4)
        if quality_values[bucket_name]:
            bucket["quality_score"] = round(sum(quality_values[bucket_name]) / len(quality_values[bucket_name]), 2)

    recent_runs.sort(key=lambda row: row["created_at"], reverse=True)
    return {
        "workspace_id": ctx.workspace_id,
        "as_of": utc_now().isoformat(),
        "shared": buckets["shared"],
        "owned": buckets["owned"],
        "recent_runs": recent_runs[:20],
    }


def list_accessible_datasets(db: Session, ctx: AccessContext) -> list[dict[str, Any]]:
    _active_membership(db, ctx)
    rows: list[dict[str, Any]] = []
    total_rows = version.row_count
    for governance, dataset in (
        db.query(DatasetGovernanceModel, DatasetModel)
        .join(DatasetModel, DatasetModel.id == DatasetGovernanceModel.dataset_id)
        .filter(DatasetGovernanceModel.workspace_id == ctx.workspace_id)
        .all()
    ):
        permissions = effective_permissions(db, ctx, dataset.id)
        if "DISCOVER" in permissions:
            rows.append(
                {
                    "dataset_id": dataset.id,
                    "name": dataset.name,
                    "owner_user_id": governance.owner_user_id,
                    "permissions": sorted(permissions),
                }
            )
    return rows


def get_data_explorer(
    db: Session,
    ctx: AccessContext,
    *,
    dataset_id: str,
    dataset_version_id: str,
    profile_run_id: str | None = None,
    include_rows: bool = False,
    selected_columns: list[str] | None = None,
    filters: dict[str, Any] | None = None,
    sort_by: str | None = None,
    sort_direction: str = "asc",
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    permissions = require_permission(db, ctx, dataset_id, "DISCOVER", dataset_version_id)
    version = _dataset_version(db, ctx, dataset_id, dataset_version_id)
    dataset = db.get(DatasetModel, dataset_id)
    if not dataset:
        raise ResourceNotFoundError("Dataset not found")

    profile_history: list[ProfileRunSnapshotModel] = []
    selected_profile: ProfileRunSnapshotModel | None = None
    if "VIEW_PROFILE" in permissions:
        profile_history = (
            db.query(ProfileRunSnapshotModel)
            .filter_by(
                workspace_id=ctx.workspace_id,
                dataset_id=dataset_id,
                dataset_version_id=dataset_version_id,
            )
            .order_by(ProfileRunSnapshotModel.created_at.desc())
            .all()
        )
    if profile_run_id is not None:
        require_permission(db, ctx, dataset_id, "VIEW_PROFILE", dataset_version_id)
        selected_profile = (
            db.query(ProfileRunSnapshotModel)
            .filter_by(
                id=profile_run_id,
                workspace_id=ctx.workspace_id,
                dataset_id=dataset_id,
                dataset_version_id=dataset_version_id,
            )
            .first()
        )
        if selected_profile is None:
            raise ResourceNotFoundError("Profile run not found")

    rules: list[RuleReviewSnapshotModel] = []
    reports: list[AnalysisSummaryModel] = []
    artifacts: list[GovernedArtifactModel] = []
    if "VIEW_REPORTS" in permissions:
        rules = db.query(RuleReviewSnapshotModel).filter_by(dataset_version_id=version.id).all()
        reports = db.query(AnalysisSummaryModel).filter_by(dataset_version_id=version.id).all()
    artifact_permission = {
        "SOURCE_DATASET": "DISCOVER",
        "PROFILE_SNAPSHOT": "VIEW_PROFILE",
        "ROW_SAMPLE": "VIEW_ROWS",
    }
    all_artifacts = db.query(GovernedArtifactModel).filter_by(dataset_version_id=version.id).all()
    artifacts = [
        artifact for artifact in all_artifacts
        if artifact_permission.get(artifact.artifact_type, "VIEW_REPORTS") in permissions
    ]

    try:
        version_metadata = json.loads(version.source_metadata_json or "{}")
    except ValueError:
        version_metadata = {}
    version_schema = version_metadata.get("schema") if isinstance(version_metadata.get("schema"), list) else []
    rows: list[dict[str, Any]] = []
    rows_authorized = "VIEW_ROWS" in permissions
    if include_rows:
        require_permission(db, ctx, dataset_id, "VIEW_ROWS", dataset_version_id)
        if sort_direction not in {"asc", "desc"} or limit < 1 or limit > 100 or offset < 0:
            raise ValueError("Invalid bounded explorer query")
        allowed_columns = {str(item.get("name")) for item in version_schema if isinstance(item, dict) and item.get("name")}
        selected = selected_columns or [str(item.get("name")) for item in version_schema if isinstance(item, dict) and item.get("name")]
        if any(column not in allowed_columns for column in selected):
            raise ValueError("Explorer query references a column outside the immutable schema")
        if sort_by and sort_by not in allowed_columns:
            raise ValueError("Explorer sorting references a column outside the immutable schema")
        filter_values = filters or {}
        if any(column not in allowed_columns for column in filter_values):
            raise ValueError("Explorer filter references a column outside the immutable schema")
        artifact = (
            db.query(GovernedArtifactModel)
            .filter_by(
                workspace_id=ctx.workspace_id, dataset_id=dataset_id,
                dataset_version_id=dataset_version_id, artifact_type="SOURCE_DATASET",
            )
            .first()
        )
        if not artifact or artifact.checksum != version.checksum:
            raise ResourceNotFoundError("Verified source artifact not found")
        source_ref = {
            "bucket": version_metadata.get("bucket"),
            "object_key": version_metadata.get("object_key") or artifact.storage_locator,
            "checksum": version.checksum,
            "size_bytes": int(version_metadata.get("size_bytes") or 0),
            "format": version_metadata.get("format") or "csv",
            "filename": version_metadata.get("filename") or "dataset.csv",
            "storage_locator": artifact.storage_locator,
        }
        path = materialize_source_artifact(source_ref)
        temporary = source_ref["storage_locator"].startswith("object://")
        try:
            frame = read_verified_frame(path, checksum=version.checksum, size_bytes=source_ref["size_bytes"], schema=version_schema)
            for column, expected in filter_values.items():
                frame = frame[frame[column].astype(str) == str(expected)]
            total_rows = int(len(frame))
            if sort_by:
                frame = frame.sort_values(sort_by, ascending=sort_direction == "asc", kind="stable")
            frame = frame.iloc[offset:offset + limit]
            sensitive = {
                str(item.get("name")) for item in version_schema
                if isinstance(item, dict) and (item.get("sensitivity") in {"PII", "SENSITIVE", "SECRET"} or item.get("masking") not in {None, "NONE"})
            }
            for _, raw in frame[selected].iterrows():
                item: dict[str, Any] = {}
                for column in selected:
                    value = raw[column]
                    if column in sensitive and value == value:
                        item[column] = "[MASKED]"
                    elif value != value:
                        item[column] = None
                    elif hasattr(value, "isoformat"):
                        item[column] = value.isoformat()
                    else:
                        item[column] = value.item() if hasattr(value, "item") else value
                rows.append(item)
            append_audit_event(
                db, ctx, actor_role=_active_membership(db, ctx).role,
                action="ROWS_ACCESSED", entity_type="dataset_rows", entity_id=dataset_version_id,
                dataset_id=dataset_id, dataset_version_id=dataset_version_id,
                detail={"columns": selected, "limit": limit, "offset": offset},
            )
            db.commit()
        finally:
            if temporary:
                path.unlink(missing_ok=True)

    def profile_payload(profile: ProfileRunSnapshotModel) -> dict[str, Any]:
        return {
            "profile_run_id": profile.id,
            "status": profile.status,
            "row_count": profile.row_count,
            "quality_score": profile.quality_score,
            "schema": json.loads(profile.schema_json or "[]"),
            "metrics": json.loads(profile.metrics_json or "{}"),
            "sanitized_samples": json.loads(profile.sanitized_samples_json or "[]"),
            "created_at": profile.created_at.isoformat(),
        }

    return {
        "workspace_id": ctx.workspace_id,
        "dataset": {"id": dataset.id, "name": dataset.name},
        "dataset_version": {
            "id": version.id,
            "version_number": version.version_number,
            "row_count": version.row_count,
            "schema_hash": version.schema_hash,
            "schema": version_schema,
        },
        "permissions": sorted(permissions),
        "profile_history": [profile_payload(profile) for profile in profile_history],
        "selected_profile": profile_payload(selected_profile) if selected_profile else None,
        "ruleset_history": [{"id": row.id, "status": row.status} for row in rules],
        "reports": [
            {
                "id": row.id,
                "status": row.status,
                "tests_passed": row.tests_passed,
                "tests_failed": row.tests_failed,
                "anomaly_count": row.anomaly_count,
            }
            for row in reports
        ],
        "artifacts": [{"id": row.id, "artifact_type": row.artifact_type} for row in artifacts],
        "rows_authorized": rows_authorized,
        "rows": rows,
        "total_rows": total_rows,
        "row_limit": limit if include_rows else 0,
    }


def get_governed_artifact(
    db: Session,
    ctx: AccessContext,
    *,
    dataset_id: str,
    dataset_version_id: str,
    artifact_id: str,
) -> GovernedArtifactModel:
    artifact = (
        db.query(GovernedArtifactModel)
        .filter_by(
            id=artifact_id,
            workspace_id=ctx.workspace_id,
            dataset_id=dataset_id,
            dataset_version_id=dataset_version_id,
        )
        .first()
    )
    if not artifact:
        raise ResourceNotFoundError("Artifact not found")
    required_permission = {
        "SOURCE_DATASET": "DISCOVER",
        "PROFILE_SNAPSHOT": "VIEW_PROFILE",
        "ROW_SAMPLE": "VIEW_ROWS",
    }.get(artifact.artifact_type, "VIEW_REPORTS")
    require_permission(db, ctx, dataset_id, required_permission, dataset_version_id)
    return artifact


def list_audit_events(
    db: Session,
    ctx: AccessContext,
    *,
    dataset_id: str | None = None,
    actor_id: str | None = None,
    action: str | None = None,
    run_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    membership = _active_membership(db, ctx)
    query = db.query(GovernanceAuditEventModel).filter_by(workspace_id=ctx.workspace_id)
    if membership.role != "ADMIN":
        visible_dataset_ids = [row["dataset_id"] for row in list_accessible_datasets(db, ctx)]
        query = query.filter(
            or_(
                GovernanceAuditEventModel.dataset_id.in_(visible_dataset_ids),
                and_(
                    GovernanceAuditEventModel.dataset_id.is_(None),
                    GovernanceAuditEventModel.actor_id == ctx.user_id,
                ),
            )
        )
    if dataset_id:
        query = query.filter(GovernanceAuditEventModel.dataset_id == dataset_id)
    if actor_id:
        query = query.filter(GovernanceAuditEventModel.actor_id == actor_id)
    if action:
        query = query.filter(GovernanceAuditEventModel.action == action)
    if run_id:
        query = query.filter(GovernanceAuditEventModel.run_id == run_id)
    events = query.order_by(GovernanceAuditEventModel.occurred_at.desc()).limit(min(max(limit, 1), 200)).all()
    return [
        {
            "id": event.id,
            "workspace_id": event.workspace_id,
            "actor_id": event.actor_id,
            "actor_role": event.actor_role,
            "action": event.action,
            "entity_type": event.entity_type,
            "entity_id": event.entity_id,
            "dataset_id": event.dataset_id,
            "dataset_version_id": event.dataset_version_id,
            "run_id": event.run_id,
            "correlation_id": event.correlation_id,
            "source": event.source,
            "detail": json.loads(event.detail_json or "{}"),
            "occurred_at": event.occurred_at.isoformat(),
        }
        for event in events
    ]
