"""API v2 contracts for Overview, Data Explorer, sharing, and Audit Logs."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.models.database import (
    DatasetVersionModel,
    SessionModel,
    UserAccountModel,
)
from src.services.data_access_service import (
    AccessContext,
    AccessDeniedError,
    ResourceNotFoundError,
    effective_permissions,
    get_data_explorer,
    get_overview_metrics,
    grant_dataset_permissions,
    list_accessible_datasets,
    list_audit_events,
    revoke_grant_set,
)
from src.services.rule_store import get_engine
from src.services.session_service import get_current_session, verify_csrf

router = APIRouter(prefix="/api/v2", tags=["data-access-v2"])


class DatasetGrantInput(BaseModel):
    grantee_type: str = Field(pattern="^(USER|GROUP|WORKSPACE)$")
    grantee_id: str = Field(min_length=1, max_length=64)
    permissions: set[str] = Field(min_length=1)
    dataset_version_id: str | None = None
    expires_at: datetime | None = None


def get_db():
    db = Session(get_engine())
    try:
        yield db
    finally:
        db.close()


def get_session(request: Request, db: Session = Depends(get_db)) -> SessionModel:
    session = get_current_session(request, db)
    verify_csrf(request, session)
    return session


def _context(db: Session, session: SessionModel, workspace_id: str) -> AccessContext:
    account = db.query(UserAccountModel).filter_by(username=session.username).first()
    if not account:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "User account not found"})
    return AccessContext(user_id=account.id, workspace_id=workspace_id)


def _translate_access_error(exc: Exception) -> None:
    if isinstance(exc, ResourceNotFoundError):
        raise HTTPException(status_code=404, detail={"code": "RESOURCE_NOT_FOUND", "message": str(exc)}) from exc
    if isinstance(exc, AccessDeniedError):
        raise HTTPException(status_code=403, detail={"code": "DATASET_ACCESS_FORBIDDEN", "message": str(exc)}) from exc
    raise exc


@router.get("/workspaces/{workspace_id}/overview")
def overview(
    workspace_id: str,
    session: SessionModel = Depends(get_session),
    db: Session = Depends(get_db),
):
    try:
        return get_overview_metrics(db, _context(db, session, workspace_id))
    except (ResourceNotFoundError, AccessDeniedError) as exc:
        _translate_access_error(exc)


@router.get("/workspaces/{workspace_id}/datasets")
def datasets(
    workspace_id: str,
    session: SessionModel = Depends(get_session),
    db: Session = Depends(get_db),
):
    try:
        return list_accessible_datasets(db, _context(db, session, workspace_id))
    except (ResourceNotFoundError, AccessDeniedError) as exc:
        _translate_access_error(exc)


@router.get("/workspaces/{workspace_id}/datasets/{dataset_id}/versions")
def dataset_versions(
    workspace_id: str,
    dataset_id: str,
    session: SessionModel = Depends(get_session),
    db: Session = Depends(get_db),
):
    ctx = _context(db, session, workspace_id)
    try:
        versions = (
            db.query(DatasetVersionModel)
            .filter_by(workspace_id=workspace_id, dataset_id=dataset_id)
            .order_by(DatasetVersionModel.version_number.desc())
            .all()
        )
        visible = []
        for version in versions:
            permissions = effective_permissions(db, ctx, dataset_id, version.id)
            if "DISCOVER" in permissions:
                visible.append(
                    {
                        "id": version.id,
                        "version_number": version.version_number,
                        "status": version.status,
                        "row_count": version.row_count,
                        "schema_hash": version.schema_hash,
                        "created_at": version.created_at.isoformat(),
                        "permissions": sorted(permissions),
                    }
                )
        if not visible and "DISCOVER" not in effective_permissions(db, ctx, dataset_id):
            raise ResourceNotFoundError("Dataset not found")
        return visible
    except (ResourceNotFoundError, AccessDeniedError) as exc:
        _translate_access_error(exc)


@router.get("/workspaces/{workspace_id}/datasets/{dataset_id}/versions/{dataset_version_id}/explorer")
def data_explorer(
    workspace_id: str,
    dataset_id: str,
    dataset_version_id: str,
    profile_run_id: str | None = None,
    session: SessionModel = Depends(get_session),
    db: Session = Depends(get_db),
):
    try:
        return get_data_explorer(
            db,
            _context(db, session, workspace_id),
            dataset_id=dataset_id,
            dataset_version_id=dataset_version_id,
            profile_run_id=profile_run_id,
        )
    except (ResourceNotFoundError, AccessDeniedError) as exc:
        _translate_access_error(exc)


@router.post("/workspaces/{workspace_id}/datasets/{dataset_id}/grants", status_code=201)
def create_dataset_grant(
    workspace_id: str,
    dataset_id: str,
    body: DatasetGrantInput,
    session: SessionModel = Depends(get_session),
    db: Session = Depends(get_db),
):
    try:
        grant_set_id = grant_dataset_permissions(
            db,
            _context(db, session, workspace_id),
            dataset_id=dataset_id,
            dataset_version_id=body.dataset_version_id,
            grantee_type=body.grantee_type,
            grantee_id=body.grantee_id,
            permissions=body.permissions,
            expires_at=body.expires_at,
        )
        return {"grant_set_id": grant_set_id, "status": "ACTIVE"}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "VALIDATION_ERROR", "message": str(exc)}) from exc
    except (ResourceNotFoundError, AccessDeniedError) as exc:
        _translate_access_error(exc)


@router.delete("/workspaces/{workspace_id}/grant-sets/{grant_set_id}")
def revoke_dataset_grant_set(
    workspace_id: str,
    grant_set_id: str,
    session: SessionModel = Depends(get_session),
    db: Session = Depends(get_db),
):
    try:
        revoked_count = revoke_grant_set(db, _context(db, session, workspace_id), grant_set_id)
        return {"grant_set_id": grant_set_id, "status": "REVOKED", "revoked_count": revoked_count}
    except (ResourceNotFoundError, AccessDeniedError) as exc:
        _translate_access_error(exc)


@router.get("/workspaces/{workspace_id}/audit-events")
def audit_events(
    workspace_id: str,
    dataset_id: str | None = None,
    actor_id: str | None = None,
    action: str | None = None,
    run_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    session: SessionModel = Depends(get_session),
    db: Session = Depends(get_db),
):
    try:
        return list_audit_events(
            db,
            _context(db, session, workspace_id),
            dataset_id=dataset_id,
            actor_id=actor_id,
            action=action,
            run_id=run_id,
            limit=limit,
        )
    except (ResourceNotFoundError, AccessDeniedError) as exc:
        _translate_access_error(exc)
