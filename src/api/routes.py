import asyncio
import json
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.config import get_settings
from src.models.database import (
    AuditEventModel,
    ColumnProfileModel,
    DatasetAccessModel,
    DatasetModel,
    DqResultModel,
    DqRunModel,
    JobModel,
    ProfileModel,
    RuleConfigurationModel,
    RuleProposalModel,
    RuleVersionModel,
    SessionModel,
    UserAccountModel,
)
from src.models.schemas import (
    ActiveRuleResponse,
    ActiveRulesListResponse,
    ApprovedRulesResponse,
    BulkReviewRequest,
    BulkReviewResponse,
    ExecuteActiveTestsRequest,
    ExecuteTestsResponse,
    PublishRulesResponse,
    ReviewSummaryResponse,
    RuleReviewResponse,
    RuleUpdateRequest,
    TestResultResponse,
    TestResultsListResponse,
    TestRunStatusResponse,
)
from src.services.job_runner import (
    add_audit_event,
    run_dq_checks,
    run_ingest_profile,
    run_propose_rules,
)
from src.services.rule_store import get_engine
from src.services.session_service import (
    SESSION_COOKIE_NAME,
    create_user_session,
    enforce_role,
    get_current_session,
    hash_password,
    verify_csrf,
)

logger = logging.getLogger(__name__)
router = APIRouter()
dq_router = APIRouter(prefix="/dq", tags=["Data Quality"])


# ---------------------------------------------------------------------------
# DB Dependency
# ---------------------------------------------------------------------------
def get_db():
    with Session(get_engine()) as session:
        yield session


# ---------------------------------------------------------------------------
# Auth Dependency
# ---------------------------------------------------------------------------
async def get_session(request: Request, db: Session = Depends(get_db)) -> SessionModel:
    session = get_current_session(request, db)
    verify_csrf(request, session)
    return session


def require_role(roles: list[str]):
    async def dependency(session: SessionModel = Depends(get_session)):
        enforce_role(session, roles)
        return session

    return dependency


# Helper for idempotency checks
def verify_idempotency(db: Session, key: str | None) -> str:
    if not key:
        raise HTTPException(
            status_code=422, detail={"code": "VALIDATION_ERROR", "message": "Idempotency-Key header is required"}
        )
    existing = db.query(JobModel).filter(JobModel.idempotency_key == key).first()
    if existing:
        if existing.status in ("PENDING", "RUNNING"):
            raise HTTPException(
                status_code=409,
                detail={"code": "CONFLICT", "message": "Job with this idempotency key is already active"},
            )
        else:
            # Re-return existing completed job ID
            return existing.id
    return ""


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    username: str
    password: str


class SessionResponse(BaseModel):
    username: str
    role: str
    csrf_token: str
    expires_at: str


class CreateJobResponse(BaseModel):
    job_id: str
    status: str


class ColumnProfileSchema(BaseModel):
    name: str
    data_type: str
    null_rate: float
    distinct_count: int
    non_null_count: int | None = None
    negative_rate: float | None = None
    quantiles: dict[str, float]
    out_of_domain_rate: float | None = None
    full_distinct_count: int | None = None
    uniqueness_rate: float | None = None
    is_unique_full_table: bool | None = None
    min_value: float | None = None
    max_value: float | None = None
    sample_value: str


class CrossFieldProfileSchema(BaseModel):
    left_column: str
    operator: str
    right_column: str
    checked_count: int
    violation_count: int
    violation_rate: float


class DatasetProfileSchema(BaseModel):
    dataset_id: str
    row_count: int
    completeness_score: float
    validity_score: float
    duplicate_rate: float
    columns: list[ColumnProfileSchema]
    cross_field_metrics: list[CrossFieldProfileSchema]
    evidence_keys: list[str]
    generated_at: str


class RuleSpecSchema(BaseModel):
    type: str
    column: str | None = None
    columns: list[str] | None = None
    min_value: float | None = None
    max_value: float | None = None
    allowed_values: list[str] | None = None
    operator: str | None = None
    fingerprint_columns: list[str] | None = None


class RuleProposalSchema(BaseModel):
    id: str
    dataset_id: str
    title: str
    description: str
    severity: str
    status: str
    rule: RuleSpecSchema
    evidence_refs: list[str]
    evidence_summary: str
    confidence: float
    model_name: str
    created_at: str
    updated_at: str


class ManualRuleInput(BaseModel):
    title: str
    description: str
    severity: str
    rule: RuleSpecSchema


class ReviewInput(BaseModel):
    action: str  # approve, reject, edit
    title: str | None = None
    description: str | None = None
    severity: str | None = None
    rule: RuleSpecSchema | None = None


class DqRunCreateRequest(BaseModel):
    rule_ids: list[str]


class DqRunCreateResponse(BaseModel):
    job_id: str
    run_id: str
    status: str


class DqRunSchema(BaseModel):
    id: str
    job_id: str
    dataset_id: str
    rule_ids: list[str]
    status: str
    total_failed: int
    total_checked: int
    created_at: str
    completed_at: str | None = None


class DqResultSchema(BaseModel):
    rule_id: str
    rule_title: str
    status: str
    checked_count: int
    failed_count: int
    failed_row_ids: list[str]


class AuditLogSchema(BaseModel):
    id: str
    action: str
    entity_type: str
    entity_id: str
    actor: str
    summary: str
    created_at: str


class RuleConfigurationInput(BaseModel):
    execution_status: str
    schedule_frequency: str
    timezone: str


class RuleConfigurationSchema(RuleConfigurationInput):
    rule_id: str
    last_run_at: str | None = None
    next_run_at: str | None = None
    updated_at: str


class UserCreateInput(BaseModel):
    username: str
    display_name: str
    password: str
    role: str


class UserUpdateInput(BaseModel):
    display_name: str | None = None
    password: str | None = None
    role: str | None = None
    status: str | None = None


class UserAccountSchema(BaseModel):
    id: str
    username: str
    display_name: str
    role: str
    status: str
    created_by: str | None = None
    last_login_at: str | None = None
    created_at: str
    updated_at: str


class DatasetAccessInput(BaseModel):
    access_level: str


class DatasetAccessSchema(BaseModel):
    id: str
    dataset_id: str
    username: str
    display_name: str
    role: str
    access_level: str
    granted_by: str
    granted_at: str


def user_to_schema(user: UserAccountModel) -> UserAccountSchema:
    return UserAccountSchema(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        status=user.status,
        created_by=user.created_by,
        last_login_at=user.last_login_at.isoformat() if user.last_login_at else None,
        created_at=user.created_at.isoformat(),
        updated_at=user.updated_at.isoformat(),
    )


def configuration_to_schema(config: RuleConfigurationModel) -> RuleConfigurationSchema:
    return RuleConfigurationSchema(
        rule_id=config.rule_proposal_id,
        execution_status=config.execution_status,
        schedule_frequency=config.schedule_frequency,
        timezone=config.timezone,
        last_run_at=config.last_run_at.isoformat() if config.last_run_at else None,
        next_run_at=config.next_run_at.isoformat() if config.next_run_at else None,
        updated_at=config.updated_at.isoformat(),
    )


def has_dataset_access(db: Session, session: SessionModel, dataset_id: str, manage: bool = False) -> bool:
    if session.role == "ADMIN":
        return True
    access = (
        db.query(DatasetAccessModel)
        .filter(
            DatasetAccessModel.dataset_id == dataset_id,
            DatasetAccessModel.username == session.username,
        )
        .first()
    )
    return bool(access and (not manage or access.access_level == "MANAGE"))


def require_dataset_access(db: Session, session: SessionModel, dataset_id: str, manage: bool = False) -> None:
    if not has_dataset_access(db, session, dataset_id, manage):
        raise HTTPException(
            status_code=403,
            detail={"code": "DATASET_ACCESS_FORBIDDEN", "message": "You do not have access to this dataset."},
        )


# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------


@router.post("/session", response_model=SessionResponse)
def login(request: Request, body: LoginRequest, response: Response, db: Session = Depends(get_db)):
    """
    POST /api/v1/session - Authenticates user and sets session cookie + CSRF token.
    """
    session = create_user_session(body.username, body.password, db)

    # Set HTTP-only cookie
    response.set_cookie(key=SESSION_COOKIE_NAME, value=session.id, httponly=True, samesite="lax", path="/")

    add_audit_event(
        db,
        session_id=session.id,
        actor_role=session.role,
        action_code="LOGIN",
        entity_type="session",
        entity_id=session.id,
        detail={"username": session.username, "role": session.role},
    )

    return SessionResponse(
        username=session.username,
        role=session.role,
        csrf_token=session.csrf_token,
        expires_at=session.expires_at.isoformat(),
    )


@router.delete("/session", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    """
    DELETE /api/v1/session - Invalidates session cookie and database session record.
    """
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id:
        session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
        if session:
            add_audit_event(
                db,
                session_id=session.id,
                actor_role=session.role,
                action_code="LOGOUT",
                entity_type="session",
                entity_id=session.id,
                detail={"username": session.username},
            )
            db.delete(session)
            db.commit()

    # Clear cookie
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return Response(status_code=204)


@router.get("/datasets")
def list_datasets(
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])), db: Session = Depends(get_db)
):
    """
    GET /api/v1/datasets - Lists available datasets and status.
    """
    datasets = db.query(DatasetModel).all()
    if session.role != "ADMIN":
        allowed_ids = {
            access.dataset_id
            for access in db.query(DatasetAccessModel)
            .filter(
                DatasetAccessModel.username == session.username,
            )
            .all()
        }
        datasets = [dataset for dataset in datasets if dataset.id in allowed_ids]
    return [
        {
            "id": d.id,
            "name": d.name,
            "description": d.description,
            "status": d.status,
            "row_count": d.row_count,
            "source_label": d.source_label,
            "manifest_version": d.manifest_version,
            "checksum": d.checksum,
            "updated_at": d.updated_at.isoformat(),
        }
        for d in datasets
    ]


@router.post("/datasets/{id}/ingestions", status_code=202, response_model=CreateJobResponse)
def start_ingestion(
    id: str,
    background_tasks: BackgroundTasks,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """
    POST /api/v1/datasets/{id}/ingestions - Starts background ingestion and profiling job.
    """
    dataset = db.query(DatasetModel).filter(DatasetModel.id == id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    require_dataset_access(db, session, id, manage=True)

    collision_job_id = verify_idempotency(db, idempotency_key)
    if collision_job_id:
        coll_job = db.query(JobModel).filter(JobModel.id == collision_job_id).first()
        status_val = coll_job.status if coll_job else "PENDING"
        return CreateJobResponse(job_id=collision_job_id, status=status_val)

    job_id = str(uuid.uuid4())
    job = JobModel(
        id=job_id,
        type="INGEST_PROFILE",
        status="PENDING",
        progress=0.0,
        message="Queued",
        idempotency_key=idempotency_key,
        linked_entity=id,
        correlation_id=str(uuid.uuid4()),
        attempt_count=1,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(job)
    db.commit()

    add_audit_event(
        db,
        session_id=session.id,
        actor_role=session.role,
        action_code="JOB_STARTED",
        entity_type="job",
        entity_id=job_id,
        detail={"type": "INGEST_PROFILE", "dataset_id": id},
    )

    background_tasks.add_task(run_ingest_profile, job_id, id, session.id, session.role)
    return CreateJobResponse(job_id=job_id, status="PENDING")


@router.get("/jobs/{id}")
def get_job_status(
    id: str, session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])), db: Session = Depends(get_db)
):
    """
    GET /api/v1/jobs/{id} - Returns current status of job.
    """
    job = db.query(JobModel).filter(JobModel.id == id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return {
        "id": job.id,
        "type": job.type,
        "status": job.status,
        "progress": job.progress,
        "message": job.message or "",
        "created_at": job.created_at.isoformat() if job.created_at else "",
        "updated_at": job.updated_at.isoformat() if job.updated_at else "",
        "error": job.error,
    }


@router.get("/datasets/{id}/profile", response_model=DatasetProfileSchema)
def get_dataset_profile(
    id: str, session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])), db: Session = Depends(get_db)
):
    """
    GET /api/v1/datasets/{id}/profile - Returns completed profiling details. Returns 404 until complete.
    """
    dataset = db.query(DatasetModel).filter(DatasetModel.id == id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    require_dataset_access(db, session, id)

    profile = db.query(ProfileModel).filter(ProfileModel.dataset_id == id).first()
    if not profile or dataset.status != "PROFILE_READY":
        raise HTTPException(status_code=404, detail="Profile not generated or dataset not fully profiled yet")

    cols = db.query(ColumnProfileModel).filter(ColumnProfileModel.profile_dataset_id == id).all()

    columns_list = [
        ColumnProfileSchema(
            name=c.name,
            data_type=c.data_type,
            null_rate=c.null_rate,
            distinct_count=c.distinct_count,
            non_null_count=c.non_null_count,
            negative_rate=c.negative_rate,
            quantiles=json.loads(c.quantiles_json or "{}"),
            out_of_domain_rate=c.out_of_domain_rate,
            full_distinct_count=c.full_distinct_count,
            uniqueness_rate=c.uniqueness_rate,
            is_unique_full_table=c.is_unique_full_table,
            min_value=c.min_value,
            max_value=c.max_value,
            sample_value=c.sample_value,
        )
        for c in cols
    ]

    return DatasetProfileSchema(
        dataset_id=profile.dataset_id,
        row_count=profile.row_count,
        completeness_score=profile.completeness_score,
        validity_score=profile.validity_score,
        duplicate_rate=profile.duplicate_rate,
        columns=columns_list,
        cross_field_metrics=json.loads(profile.cross_field_metrics_json or "[]"),
        evidence_keys=json.loads(profile.evidence_keys),
        generated_at=profile.generated_at.isoformat(),
    )


@router.post("/datasets/{id}/rule-proposals", status_code=202, response_model=CreateJobResponse)
def start_rule_proposals(
    id: str,
    background_tasks: BackgroundTasks,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """
    POST /api/v1/datasets/{id}/rule-proposals - Triggers LLM/deterministic proposals generator.
    """
    dataset = db.query(DatasetModel).filter(DatasetModel.id == id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    require_dataset_access(db, session, id, manage=True)
    if dataset.status != "PROFILE_READY":
        raise HTTPException(status_code=400, detail="Completed profile is required before requesting proposals")

    collision_job_id = verify_idempotency(db, idempotency_key)
    if collision_job_id:
        coll_job = db.query(JobModel).filter(JobModel.id == collision_job_id).first()
        status_val = coll_job.status if coll_job else "PENDING"
        return CreateJobResponse(job_id=collision_job_id, status=status_val)

    job_id = str(uuid.uuid4())
    job = JobModel(
        id=job_id,
        type="PROPOSE_RULES",
        status="PENDING",
        progress=0.0,
        message="Queued",
        idempotency_key=idempotency_key,
        linked_entity=id,
        correlation_id=str(uuid.uuid4()),
        attempt_count=1,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(job)
    db.commit()

    add_audit_event(
        db,
        session_id=session.id,
        actor_role=session.role,
        action_code="JOB_STARTED",
        entity_type="job",
        entity_id=job_id,
        detail={"type": "PROPOSE_RULES", "dataset_id": id},
    )

    background_tasks.add_task(run_propose_rules, job_id, id, session.id, session.role)
    return CreateJobResponse(job_id=job_id, status="PENDING")


@router.get("/rule-proposals", response_model=list[RuleProposalSchema])
def list_proposals(
    dataset_id: str,
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """
    GET /api/v1/rule-proposals - Returns rule proposals for a dataset.
    """
    require_dataset_access(db, session, dataset_id)
    proposals = db.query(RuleProposalModel).filter(RuleProposalModel.dataset_id == dataset_id).all()
    return [
        RuleProposalSchema(
            id=p.id,
            dataset_id=p.dataset_id,
            title=p.title,
            description=p.description,
            severity=p.severity,
            status=p.status,
            rule=RuleSpecSchema(**json.loads(p.rule_spec)),
            evidence_refs=json.loads(p.evidence_refs),
            evidence_summary=p.evidence_summary,
            confidence=p.confidence,
            model_name=p.model_name,
            created_at=p.created_at.isoformat(),
            updated_at=p.updated_at.isoformat(),
        )
        for p in proposals
    ]


@router.post("/datasets/{id}/rule-proposals/manual", response_model=RuleProposalSchema)
def create_manual_rule(
    id: str,
    body: ManualRuleInput,
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """
    POST /api/v1/datasets/{id}/rule-proposals/manual - Creates a manually authored DQ proposal for review.
    """
    dataset = db.query(DatasetModel).filter(DatasetModel.id == id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    require_dataset_access(db, session, id, manage=True)

    prop_id = f"manual-{str(uuid.uuid4())[:8]}"
    prop = RuleProposalModel(
        id=prop_id,
        dataset_id=id,
        title=body.title,
        description=body.description,
        severity=body.severity.upper(),
        status="PROPOSED",
        rule_type=body.rule.type,
        rule_spec=json.dumps(body.rule.model_dump(exclude_none=True)),
        evidence_refs=json.dumps(["manual"]),
        evidence_summary="Manually added by data steward",
        confidence=1.0,
        model_name="data-steward",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(prop)
    db.commit()

    add_audit_event(
        db,
        session_id=session.id,
        actor_role=session.role,
        action_code="PROPOSAL_CREATED",
        entity_type="rule_proposal",
        entity_id=prop_id,
        detail={"manual": True},
    )

    return RuleProposalSchema(
        id=prop.id,
        dataset_id=prop.dataset_id,
        title=prop.title,
        description=prop.description,
        severity=prop.severity,
        status=prop.status,
        rule=body.rule,
        evidence_refs=["manual"],
        evidence_summary=prop.evidence_summary,
        confidence=prop.confidence,
        model_name=prop.model_name,
        created_at=prop.created_at.isoformat(),
        updated_at=prop.updated_at.isoformat(),
    )


@router.patch("/rule-proposals/{id}", response_model=RuleProposalSchema)
def review_proposal(
    id: str,
    body: ReviewInput,
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """
    PATCH /api/v1/rule-proposals/{id} - Allows Steward to approve/reject/edit proposals.
    """
    prop = db.query(RuleProposalModel).filter(RuleProposalModel.id == id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Rule proposal not found")
    require_dataset_access(db, session, prop.dataset_id, manage=True)

    action = body.action

    if action == "approve":
        prop.status = "APPROVED"
        # Write to rule_versions
        rv_id = f"rv_{prop.id}"
        existing_rv = db.query(RuleVersionModel).filter(RuleVersionModel.id == rv_id).first()
        if not existing_rv:
            rv = RuleVersionModel(
                id=rv_id,
                rule_proposal_id=prop.id,
                dataset_id=prop.dataset_id,
                rule_spec=prop.rule_spec,
                status="APPROVED",
                version=1,
                created_at=datetime.utcnow(),
            )
            db.add(rv)
        else:
            existing_rv.status = "APPROVED"
        configuration = (
            db.query(RuleConfigurationModel)
            .filter(
                RuleConfigurationModel.rule_proposal_id == prop.id,
            )
            .first()
        )
        if not configuration:
            db.add(
                RuleConfigurationModel(
                    rule_proposal_id=prop.id, execution_status="ACTIVE", schedule_frequency="MANUAL", timezone="UTC"
                )
            )
        db.commit()
        add_audit_event(
            db,
            session_id=session.id,
            actor_role=session.role,
            action_code="PROPOSAL_APPROVED",
            entity_type="rule_proposal",
            entity_id=prop.id,
            detail={"action": "approve"},
        )

    elif action == "reject":
        prop.status = "REJECTED"
        # De-authorize existing version if any
        rv_id = f"rv_{prop.id}"
        existing_rv = db.query(RuleVersionModel).filter(RuleVersionModel.id == rv_id).first()
        if existing_rv:
            db.delete(existing_rv)
        db.commit()
        add_audit_event(
            db,
            session_id=session.id,
            actor_role=session.role,
            action_code="PROPOSAL_REJECTED",
            entity_type="rule_proposal",
            entity_id=prop.id,
            detail={"action": "reject"},
        )

    elif action == "edit":
        prop.status = "APPROVED"
        # Update spec or metadata
        if body.title:
            prop.title = body.title
        if body.description:
            prop.description = body.description
        if body.severity:
            prop.severity = body.severity.upper()
        if body.rule:
            prop.rule_spec = json.dumps(body.rule.model_dump(exclude_none=True))

        rv_id = f"rv_{prop.id}"
        existing_rv = db.query(RuleVersionModel).filter(RuleVersionModel.id == rv_id).first()
        if existing_rv:
            existing_rv.rule_spec = prop.rule_spec
            existing_rv.created_at = datetime.utcnow()
        else:
            rv = RuleVersionModel(
                id=rv_id,
                rule_proposal_id=prop.id,
                dataset_id=prop.dataset_id,
                rule_spec=prop.rule_spec,
                status="APPROVED",
                version=1,
                created_at=datetime.utcnow(),
            )
            db.add(rv)
        configuration = (
            db.query(RuleConfigurationModel)
            .filter(
                RuleConfigurationModel.rule_proposal_id == prop.id,
            )
            .first()
        )
        if not configuration:
            db.add(
                RuleConfigurationModel(
                    rule_proposal_id=prop.id, execution_status="ACTIVE", schedule_frequency="MANUAL", timezone="UTC"
                )
            )
        db.commit()
        add_audit_event(
            db,
            session_id=session.id,
            actor_role=session.role,
            action_code="PROPOSAL_EDITED",
            entity_type="rule_proposal",
            entity_id=prop.id,
            detail={"action": "edit"},
        )

    else:
        raise HTTPException(status_code=400, detail="Invalid review action")

    prop.updated_at = datetime.utcnow()
    db.commit()

    return RuleProposalSchema(
        id=prop.id,
        dataset_id=prop.dataset_id,
        title=prop.title,
        description=prop.description,
        severity=prop.severity,
        status=prop.status,
        rule=RuleSpecSchema(**json.loads(prop.rule_spec)),
        evidence_refs=json.loads(prop.evidence_refs),
        evidence_summary=prop.evidence_summary,
        confidence=prop.confidence,
        model_name=prop.model_name,
        created_at=prop.created_at.isoformat(),
        updated_at=prop.updated_at.isoformat(),
    )


@router.delete("/rule-proposals/{id}", status_code=204)
def delete_proposal(
    id: str,
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """Remove a non-approved proposal while retaining its audit history."""
    proposal = db.query(RuleProposalModel).filter(RuleProposalModel.id == id).first()
    if not proposal:
        raise HTTPException(
            status_code=404, detail={"code": "PROPOSAL_NOT_FOUND", "message": "Rule proposal not found."}
        )
    require_dataset_access(db, session, proposal.dataset_id, manage=True)
    if proposal.status == "APPROVED":
        raise HTTPException(
            status_code=422,
            detail={
                "code": "APPROVED_PROPOSAL_DELETE_FORBIDDEN",
                "message": "Reject an approved proposal before deleting it.",
            },
        )

    db.query(RuleConfigurationModel).filter(RuleConfigurationModel.rule_proposal_id == proposal.id).delete()
    db.query(RuleVersionModel).filter(RuleVersionModel.rule_proposal_id == proposal.id).delete()
    db.delete(proposal)
    db.commit()
    add_audit_event(
        db, session.id, session.role, "PROPOSAL_DELETED", "rule_proposal", id, {"message": "Rule proposal deleted."}
    )
    return Response(status_code=204)


@router.get("/rule-configurations", response_model=list[RuleConfigurationSchema])
def list_rule_configurations(
    dataset_id: str,
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    require_dataset_access(db, session, dataset_id)
    configurations = (
        db.query(RuleConfigurationModel)
        .join(
            RuleProposalModel,
            RuleProposalModel.id == RuleConfigurationModel.rule_proposal_id,
        )
        .filter(RuleProposalModel.dataset_id == dataset_id)
        .all()
    )
    return [configuration_to_schema(configuration) for configuration in configurations]


@router.patch("/rule-proposals/{id}/configuration", response_model=RuleConfigurationSchema)
def update_rule_configuration(
    id: str,
    body: RuleConfigurationInput,
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    if body.execution_status not in {"ACTIVE", "PAUSED"} or body.schedule_frequency not in {
        "MANUAL",
        "HOURLY",
        "DAILY",
    }:
        raise HTTPException(
            status_code=422, detail={"code": "VALIDATION_ERROR", "message": "Invalid execution configuration."}
        )
    proposal = db.query(RuleProposalModel).filter(RuleProposalModel.id == id).first()
    if not proposal:
        raise HTTPException(
            status_code=404, detail={"code": "PROPOSAL_NOT_FOUND", "message": "Rule proposal not found."}
        )
    require_dataset_access(db, session, proposal.dataset_id, manage=True)
    if proposal.status != "APPROVED":
        raise HTTPException(
            status_code=422,
            detail={"code": "APPROVED_RULE_REQUIRED", "message": "Only approved rules can be configured."},
        )
    configuration = db.query(RuleConfigurationModel).filter(RuleConfigurationModel.rule_proposal_id == id).first()
    if not configuration:
        configuration = RuleConfigurationModel(rule_proposal_id=id)
        db.add(configuration)
    configuration.execution_status = body.execution_status
    configuration.schedule_frequency = body.schedule_frequency
    configuration.timezone = body.timezone
    configuration.next_run_at = None
    db.commit()
    add_audit_event(
        db,
        session.id,
        session.role,
        "RULE_CONFIGURATION_UPDATED",
        "rule_configuration",
        id,
        {"message": "Rule execution configuration updated."},
    )
    db.refresh(configuration)
    return configuration_to_schema(configuration)


@router.post("/dq-runs", status_code=202, response_model=DqRunCreateResponse)
def start_dq_run(
    body: DqRunCreateRequest,
    background_tasks: BackgroundTasks,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """
    POST /api/v1/dq-runs - Launches data quality run. Accepts allowed list of rule IDs.
    """
    if not body.rule_ids:
        raise HTTPException(status_code=400, detail="No rule IDs provided")

    collision_job_id = verify_idempotency(db, idempotency_key)
    if collision_job_id:
        # Find matching run id
        existing_run = db.query(DqRunModel).filter(DqRunModel.job_id == collision_job_id).first()
        run_id = existing_run.id if existing_run else ""
        coll_job = db.query(JobModel).filter(JobModel.id == collision_job_id).first()
        status_val = coll_job.status if coll_job else "PENDING"
        return DqRunCreateResponse(job_id=collision_job_id, run_id=run_id, status=status_val)

    # Resolve dataset_id from the first rule
    rule_id = body.rule_ids[0]
    # Check if rule ID starts with rv_
    lookup_id = rule_id if rule_id.startswith("rv_") else f"rv_{rule_id}"
    rv = db.query(RuleVersionModel).filter(RuleVersionModel.id == lookup_id).first()
    if not rv:
        raise HTTPException(status_code=400, detail=f"Rule version {rule_id} not found or not approved")

    dataset_id = rv.dataset_id
    require_dataset_access(db, session, dataset_id, manage=True)

    # Verify all approved rules belong to same dataset
    # We prefix query ids to match rv_
    normalized_ids = [rid if rid.startswith("rv_") else f"rv_{rid}" for rid in body.rule_ids]
    approved_rules = (
        db.query(RuleVersionModel)
        .filter(RuleVersionModel.id.in_(normalized_ids), RuleVersionModel.status == "APPROVED")
        .all()
    )

    if len(approved_rules) != len(body.rule_ids):
        raise HTTPException(status_code=400, detail="Some selected rules are not approved or do not exist")
    if {rule.dataset_id for rule in approved_rules} != {dataset_id}:
        raise HTTPException(
            status_code=422,
            detail={"code": "MIXED_DATASET_RULES", "message": "All selected rules must belong to the same dataset."},
        )

    selected_proposal_ids = [rule.rule_proposal_id for rule in approved_rules]
    paused_count = (
        db.query(RuleConfigurationModel)
        .filter(
            RuleConfigurationModel.rule_proposal_id.in_(selected_proposal_ids),
            RuleConfigurationModel.execution_status == "PAUSED",
        )
        .count()
    )
    if paused_count:
        raise HTTPException(
            status_code=422,
            detail={"code": "ACTIVE_RULES_REQUIRED", "message": "Paused rules cannot be included in a DQ run."},
        )

    job_id = str(uuid.uuid4())
    run_id = f"run_{str(uuid.uuid4())[:8]}"

    job = JobModel(
        id=job_id,
        type="RUN_DQ",
        status="PENDING",
        progress=0.0,
        message="Queued",
        idempotency_key=idempotency_key,
        linked_entity=run_id,
        correlation_id=str(uuid.uuid4()),
        attempt_count=1,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(job)

    dq_run = DqRunModel(
        id=run_id,
        job_id=job_id,
        dataset_id=dataset_id,
        rule_ids=json.dumps(normalized_ids),
        status="PENDING",
        total_failed=0,
        total_checked=0,
        created_at=datetime.utcnow(),
    )
    db.add(dq_run)
    db.commit()

    add_audit_event(
        db,
        session_id=session.id,
        actor_role=session.role,
        action_code="DQ_RUN_START",
        entity_type="dq_run",
        entity_id=run_id,
        detail={"rule_ids": normalized_ids},
    )

    background_tasks.add_task(run_dq_checks, job_id, run_id, session.id, session.role)
    return DqRunCreateResponse(job_id=job_id, run_id=run_id, status="PENDING")


@router.get("/dq-runs/{id}", response_model=DqRunSchema)
def get_dq_run(
    id: str, session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])), db: Session = Depends(get_db)
):
    """
    GET /api/v1/dq-runs/{id} - Returns DQ run progress.
    """
    run = db.query(DqRunModel).filter(DqRunModel.id == id).first()
    if not run:
        raise HTTPException(status_code=404, detail="DQ run not found")
    require_dataset_access(db, session, run.dataset_id)

    return DqRunSchema(
        id=run.id,
        job_id=run.job_id,
        dataset_id=run.dataset_id,
        rule_ids=json.loads(run.rule_ids),
        status=run.status,
        total_failed=run.total_failed,
        total_checked=run.total_checked,
        created_at=run.created_at.isoformat(),
        completed_at=run.completed_at.isoformat() if run.completed_at else None,
    )


@router.get("/dq-runs/{id}/results", response_model=list[DqResultSchema])
def get_dq_results(
    id: str, session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])), db: Session = Depends(get_db)
):
    """
    GET /api/v1/dq-runs/{id}/results - Returns checks results with failed counts. No raw cells.
    """
    run = db.query(DqRunModel).filter(DqRunModel.id == id).first()
    if not run:
        raise HTTPException(status_code=404, detail="DQ run not found")
    require_dataset_access(db, session, run.dataset_id)

    results = db.query(DqResultModel).filter(DqResultModel.run_id == id).all()
    return [
        DqResultSchema(
            rule_id=r.rule_id,
            rule_title=r.rule_title,
            status=r.status,
            checked_count=r.checked_count,
            failed_count=r.failed_count,
            failed_row_ids=json.loads(r.failed_row_ids),
        )
        for r in results
    ]


@router.get("/admin/users", response_model=list[UserAccountSchema])
def list_users(
    session: SessionModel = Depends(require_role(["ADMIN"])),
    db: Session = Depends(get_db),
):
    return [user_to_schema(user) for user in db.query(UserAccountModel).order_by(UserAccountModel.username).all()]


@router.post("/admin/users", status_code=201, response_model=UserAccountSchema)
def create_user(
    body: UserCreateInput,
    session: SessionModel = Depends(require_role(["ADMIN"])),
    db: Session = Depends(get_db),
):
    username = body.username.strip().lower()
    if (
        not username
        or len(username) < 3
        or not all(character.isalnum() or character in "_.-" for character in username)
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "VALIDATION_ERROR",
                "message": "Username must contain only letters, digits, dot, dash, or underscore.",
            },
        )
    if body.role not in {"USER", "STEWARD", "ADMIN"} or len(body.password) < 8 or not body.display_name.strip():
        raise HTTPException(
            status_code=422, detail={"code": "VALIDATION_ERROR", "message": "Account details are invalid."}
        )
    if db.query(UserAccountModel).filter(UserAccountModel.username == username).first():
        raise HTTPException(
            status_code=409,
            detail={"code": "USERNAME_EXISTS", "message": "An account with this username already exists."},
        )
    account = UserAccountModel(
        id=f"user-{uuid.uuid4().hex}",
        username=username,
        display_name=body.display_name.strip(),
        password_hash=hash_password(body.password),
        role=body.role,
        status="ACTIVE",
        created_by=session.username,
    )
    db.add(account)
    db.commit()
    add_audit_event(
        db, session.id, session.role, "USER_CREATED", "user", account.id, {"message": f"Created account '{username}'."}
    )
    return user_to_schema(account)


@router.patch("/admin/users/{username}", response_model=UserAccountSchema)
def update_user(
    username: str,
    body: UserUpdateInput,
    session: SessionModel = Depends(require_role(["ADMIN"])),
    db: Session = Depends(get_db),
):
    account = db.query(UserAccountModel).filter(UserAccountModel.username == username.lower()).first()
    if not account:
        raise HTTPException(status_code=404, detail={"code": "USER_NOT_FOUND", "message": "User not found."})
    if account.username == session.username and (
        body.status in {"SUSPENDED", "DISABLED"} or body.role not in {None, "ADMIN"}
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "SELF_ADMIN_CHANGE_FORBIDDEN",
                "message": "An admin cannot remove their own active admin access.",
            },
        )
    if body.display_name is not None:
        if not body.display_name.strip():
            raise HTTPException(
                status_code=422, detail={"code": "VALIDATION_ERROR", "message": "Display name is required."}
            )
        account.display_name = body.display_name.strip()
    if body.password is not None:
        if len(body.password) < 8:
            raise HTTPException(
                status_code=422,
                detail={"code": "VALIDATION_ERROR", "message": "Password must be at least eight characters."},
            )
        account.password_hash = hash_password(body.password)
    if body.role is not None:
        if body.role not in {"USER", "STEWARD", "ADMIN"}:
            raise HTTPException(status_code=422, detail={"code": "VALIDATION_ERROR", "message": "Role is invalid."})
        account.role = body.role
    if body.status is not None:
        if body.status not in {"ACTIVE", "SUSPENDED", "DISABLED"}:
            raise HTTPException(status_code=422, detail={"code": "VALIDATION_ERROR", "message": "Status is invalid."})
        account.status = body.status
    db.commit()
    add_audit_event(
        db,
        session.id,
        session.role,
        "USER_UPDATED",
        "user",
        account.id,
        {"message": f"Updated account '{account.username}'."},
    )
    return user_to_schema(account)


def access_to_schema(access: DatasetAccessModel, account: UserAccountModel) -> DatasetAccessSchema:
    return DatasetAccessSchema(
        id=access.id,
        dataset_id=access.dataset_id,
        username=account.username,
        display_name=account.display_name,
        role=account.role,
        access_level=access.access_level,
        granted_by=access.granted_by,
        granted_at=access.granted_at.isoformat(),
    )


@router.get("/admin/datasets/{dataset_id}/access", response_model=list[DatasetAccessSchema])
def list_dataset_access(
    dataset_id: str,
    session: SessionModel = Depends(require_role(["ADMIN"])),
    db: Session = Depends(get_db),
):
    if not db.query(DatasetModel).filter(DatasetModel.id == dataset_id).first():
        raise HTTPException(status_code=404, detail={"code": "DATASET_NOT_FOUND", "message": "Dataset not found."})
    access_rows = db.query(DatasetAccessModel).filter(DatasetAccessModel.dataset_id == dataset_id).all()
    accounts = {
        account.username: account
        for account in db.query(UserAccountModel)
        .filter(UserAccountModel.username.in_([row.username for row in access_rows]))
        .all()
    }
    return [access_to_schema(row, accounts[row.username]) for row in access_rows if row.username in accounts]


@router.put("/admin/datasets/{dataset_id}/access/{username}", response_model=DatasetAccessSchema)
def grant_dataset_access(
    dataset_id: str,
    username: str,
    body: DatasetAccessInput,
    session: SessionModel = Depends(require_role(["ADMIN"])),
    db: Session = Depends(get_db),
):
    if body.access_level not in {"READ", "MANAGE"}:
        raise HTTPException(status_code=422, detail={"code": "VALIDATION_ERROR", "message": "Access level is invalid."})
    if not db.query(DatasetModel).filter(DatasetModel.id == dataset_id).first():
        raise HTTPException(status_code=404, detail={"code": "DATASET_NOT_FOUND", "message": "Dataset not found."})
    account = db.query(UserAccountModel).filter(UserAccountModel.username == username.lower()).first()
    if not account:
        raise HTTPException(status_code=404, detail={"code": "USER_NOT_FOUND", "message": "User not found."})
    access = (
        db.query(DatasetAccessModel)
        .filter(
            DatasetAccessModel.dataset_id == dataset_id,
            DatasetAccessModel.username == account.username,
        )
        .first()
    )
    if not access:
        access = DatasetAccessModel(
            id=f"access-{uuid.uuid4().hex}",
            dataset_id=dataset_id,
            username=account.username,
            access_level=body.access_level,
            granted_by=session.username,
        )
        db.add(access)
    else:
        access.access_level = body.access_level
        access.granted_by = session.username
        access.granted_at = datetime.utcnow()
    db.commit()
    add_audit_event(
        db,
        session.id,
        session.role,
        "DATASET_ACCESS_GRANTED",
        "dataset_access",
        access.id,
        {"message": f"Updated access for '{account.username}'."},
    )
    return access_to_schema(access, account)


@router.delete("/admin/datasets/{dataset_id}/access/{username}", status_code=204)
def revoke_dataset_access(
    dataset_id: str,
    username: str,
    session: SessionModel = Depends(require_role(["ADMIN"])),
    db: Session = Depends(get_db),
):
    access = (
        db.query(DatasetAccessModel)
        .filter(
            DatasetAccessModel.dataset_id == dataset_id,
            DatasetAccessModel.username == username.lower(),
        )
        .first()
    )
    if not access:
        raise HTTPException(
            status_code=404, detail={"code": "DATASET_ACCESS_NOT_FOUND", "message": "Dataset access grant not found."}
        )
    access_id = access.id
    db.delete(access)
    db.commit()
    add_audit_event(
        db,
        session.id,
        session.role,
        "DATASET_ACCESS_REVOKED",
        "dataset_access",
        access_id,
        {"message": f"Revoked access for '{username.lower()}'."},
    )
    return Response(status_code=204)


@router.get("/audit-logs", response_model=list[AuditLogSchema])
def list_audit_logs(
    limit: int = 50,
    offset: int = 0,
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """
    GET /api/v1/audit-logs - Returns paginated system logs.
    """
    logs = db.query(AuditEventModel).order_by(AuditEventModel.created_at.desc()).offset(offset).limit(limit).all()
    session_ids = {log.session_id for log in logs if log.session_id}
    sessions = {}
    if session_ids:
        session_objs = db.query(SessionModel).filter(SessionModel.id.in_(list(session_ids))).all()
        sessions = {s.id: s.username for s in session_objs}

    return [
        AuditLogSchema(
            id=log.id,
            action=log.action_code,
            entity_type=log.entity_type,
            entity_id=log.entity_id,
            actor=f"{log.actor_role} ({sessions.get(log.session_id, 'system')})",
            summary=json.loads(log.detail_json).get("message", f"Transitioned {log.entity_type} {log.entity_id}"),
            created_at=log.created_at.isoformat(),
        )
        for log in logs
    ]


# ---------------------------------------------------------------------------
# Agent Chat & Status Routes
# ---------------------------------------------------------------------------
@router.get("/status")
async def status_endpoint():
    return {"status": "healthy", "agent_mode": get_settings().agent_mode}


# ---------------------------------------------------------------------------
# Compatibility / Smoke Test Route
# ---------------------------------------------------------------------------
class SmokeCreateJobRequest(BaseModel):
    type: str
    linked_entity: str | None = None


@router.post("/jobs", status_code=202)
def compatibility_trigger_job(
    request: SmokeCreateJobRequest,
    background_tasks: BackgroundTasks,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    """
    POST /api/v1/jobs - Smoke test compatibility job dispatcher.
    """
    collision_job_id = verify_idempotency(db, idempotency_key)
    if collision_job_id:
        raise HTTPException(
            status_code=409, detail={"code": "CONFLICT", "message": "Job with this idempotency key is already active"}
        )

    job_id = str(uuid.uuid4())
    job = JobModel(
        id=job_id,
        type=request.type,
        status="PENDING",
        progress=0.0,
        message="Queued",
        idempotency_key=idempotency_key,
        linked_entity=request.linked_entity,
        correlation_id=str(uuid.uuid4()),
        attempt_count=1,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(job)
    db.commit()

    if request.type == "INGEST_PROFILE":
        background_tasks.add_task(run_ingest_profile, job_id, "dataset-nyc-yellow-taxi-50k", None, "SYSTEM")
    elif request.type == "PROPOSE_RULES":
        background_tasks.add_task(run_propose_rules, job_id, "dataset-nyc-yellow-taxi-50k", None, "SYSTEM")

    return {"job_id": job_id, "status": "PENDING"}


async def _run_execution_pipeline(
    test_run_id: str,
    proposal_run_id: str,
    dataset_id: str,
) -> None:
    """Background task: chạy Run 2 (Test Execution Graph) và cập nhật status vào DB."""
    from src.agents.graph import build_execution_graph
    from src.services.rule_store import get_approved_rules as store_get_approved
    from src.services.rule_store import update_test_run_status

    try:
        update_test_run_status(test_run_id, "RUNNING")
        approved_rules = store_get_approved(proposal_run_id)

        execution_graph = build_execution_graph()
        state = {
            "test_run_id": test_run_id,
            "rule_run_id": proposal_run_id,
            "dataset_id": dataset_id,
            "approved_rules": approved_rules,
        }
        await execution_graph.ainvoke(state)
        logger.info("Run 2 hoàn thành: test_run_id=%s", test_run_id)

    except Exception as exc:
        logger.error("Run 2 thất bại test_run_id=%s: %s", test_run_id, exc, exc_info=True)
        update_test_run_status(test_run_id, "FAILED", error=str(exc))


@dq_router.post(
    "/runs/{run_id}/generate-tests",
    response_model=ExecuteTestsResponse,
)
@dq_router.post(
    "/runs/{run_id}/execute-tests",
    response_model=ExecuteTestsResponse,
)
async def execute_tests(
    run_id: str,
    background_tasks: BackgroundTasks,
) -> ExecuteTestsResponse:
    """Kích hoạt Run 2: load approved rules → test_generator → validate → repair → run → anomaly.

    Trả về test_run_id ngay lập tức. Client poll GET /dq/test-runs/{test_run_id}
    để kiểm tra trạng thái và kết quả.
    """
    from src.services.rule_store import create_test_run, get_run

    proposal_run = await asyncio.to_thread(get_run, run_id)
    if not proposal_run:
        raise HTTPException(status_code=404, detail=f"proposal run_id={run_id!r} không tồn tại")

    dataset_id = proposal_run.get("dataset_id", "unknown")
    test_run_id = uuid.uuid4().hex
    create_test_run(test_run_id, dataset_id)

    background_tasks.add_task(
        _run_execution_pipeline,
        test_run_id=test_run_id,
        proposal_run_id=run_id,
        dataset_id=dataset_id,
    )
    return ExecuteTestsResponse(test_run_id=test_run_id, status="QUEUED")


@dq_router.get(
    "/test-runs/{test_run_id}",
    response_model=TestRunStatusResponse,
)
async def get_test_run_status(test_run_id: str) -> TestRunStatusResponse:
    """Poll trạng thái của một test run."""
    from src.services.rule_store import get_test_run as store_get_test_run

    run = await asyncio.to_thread(store_get_test_run, test_run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"test_run_id={test_run_id!r} không tồn tại")
    return TestRunStatusResponse(**run)


@dq_router.get(
    "/test-runs/{test_run_id}/results",
    response_model=TestResultsListResponse,
)
async def get_test_run_results(
    test_run_id: str,
    status: str | None = None,
) -> TestResultsListResponse:
    """Lấy danh sách kết quả kiểm thử của từng rule trong test run."""
    from src.services.rule_store import (
        get_test_results as store_get_results,
    )
    from src.services.rule_store import (
        get_test_run as store_get_test_run,
    )

    run = await asyncio.to_thread(store_get_test_run, test_run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"test_run_id={test_run_id!r} không tồn tại")

    rows = await asyncio.to_thread(store_get_results, test_run_id, status)
    return TestResultsListResponse(
        test_run_id=test_run_id,
        count=len(rows),
        results=[TestResultResponse(**r) for r in rows],
    )


# ---------------------------------------------------------------------------
# Publish & Active Rules Registry Endpoints
# ---------------------------------------------------------------------------


@dq_router.post(
    "/runs/{run_id}/publish",
    response_model=PublishRulesResponse,
)
async def publish_run_rules(run_id: str) -> PublishRulesResponse:
    """Xuất bản (Publish/Merge) các rules đã APPROVED từ proposal run vào Active Ruleset chính thức."""
    from src.services.rule_store import get_run, publish_approved_rules

    proposal_run = await asyncio.to_thread(get_run, run_id)
    if not proposal_run:
        raise HTTPException(status_code=404, detail=f"proposal run_id={run_id!r} không tồn tại")

    count = await asyncio.to_thread(publish_approved_rules, run_id)
    return PublishRulesResponse(
        run_id=run_id,
        published_count=count,
        message=f"Đã xuất bản thành công {count} rules vào Active Ruleset.",
    )


@dq_router.get(
    "/active-rules",
    response_model=ActiveRulesListResponse,
)
async def list_active_rules(
    dataset_id: str | None = None,
    table_name: str | None = None,
) -> ActiveRulesListResponse:
    """Lấy danh sách các rules đang hoạt động (Active Ruleset)."""
    from src.services.rule_store import get_active_rules as store_get_active_rules

    rules = await asyncio.to_thread(store_get_active_rules, dataset_id, table_name)
    return ActiveRulesListResponse(
        total_rules=len(rules),
        rules=[ActiveRuleResponse(**r) for r in rules],
    )


@dq_router.patch(
    "/active-rules/{rule_id}/deactivate",
)
async def deactivate_active_rule(rule_id: str) -> dict:
    """Vô hiệu hoá một active rule."""
    from src.services.rule_store import deactivate_rule as store_deactivate_rule

    success = await asyncio.to_thread(store_deactivate_rule, rule_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"rule_id={rule_id!r} không tồn tại hoặc đã bị vô hiệu hóa")
    return {"message": f"Rule {rule_id} đã được chuyển sang INACTIVE.", "status": "INACTIVE"}


@dq_router.post(
    "/execute-active-tests",
    response_model=ExecuteTestsResponse,
)
async def execute_active_tests(
    request: ExecuteActiveTestsRequest,
    background_tasks: BackgroundTasks,
) -> ExecuteTestsResponse:
    """Kích hoạt chạy test trên bộ Active Ruleset chính thức."""
    from src.services.rule_store import create_test_run, get_active_rules

    test_run_id = uuid.uuid4().hex
    dataset_id = request.dataset_id or "all"
    create_test_run(test_run_id, dataset_id)

    async def _run_active_execution(test_run_id: str, dataset_id: str, table_name: str | None) -> None:
        from src.agents.graph import build_execution_graph
        from src.services.rule_store import update_test_run_status

        try:
            update_test_run_status(test_run_id, "RUNNING")
            active_rules = get_active_rules(
                dataset_id=None if dataset_id == "all" else dataset_id,
                table_name=table_name,
            )

            execution_graph = build_execution_graph()
            state = {
                "test_run_id": test_run_id,
                "dataset_id": dataset_id,
                "approved_rules": active_rules,
            }
            await execution_graph.ainvoke(state)
            logger.info("Chạy test trên Active Ruleset hoàn thành: test_run_id=%s", test_run_id)
        except Exception as exc:
            logger.error("Chạy test trên Active Ruleset thất bại test_run_id=%s: %s", test_run_id, exc, exc_info=True)
            update_test_run_status(test_run_id, "FAILED", error=str(exc))

    background_tasks.add_task(
        _run_active_execution,
        test_run_id=test_run_id,
        dataset_id=dataset_id,
        table_name=request.table_name,
    )
    return ExecuteTestsResponse(test_run_id=test_run_id, status="QUEUED")


# ---------------------------------------------------------------------------
# HITL REST API Endpoints
# ---------------------------------------------------------------------------


@dq_router.get(
    "/runs/{run_id}/rules",
    response_model=list[RuleReviewResponse],
)
async def list_proposal_rules(
    run_id: str,
    status: str | None = None,
    dimension: str | None = None,
) -> list[RuleReviewResponse]:
    from src.services.rule_store import get_run, list_rules
    run = await asyncio.to_thread(get_run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"run_id={run_id!r} không tồn tại")

    rules = await asyncio.to_thread(list_rules, run_id=run_id, status=status, dimension=dimension)
    return [RuleReviewResponse(**r) for r in rules]


@dq_router.patch(
    "/runs/{run_id}/rules/{rule_id}",
    response_model=RuleReviewResponse,
)
async def review_proposal_rule(
    run_id: str,
    rule_id: str,
    body: RuleUpdateRequest,
) -> RuleReviewResponse:
    from src.services.rule_store import get_run, review_rule
    run = await asyncio.to_thread(get_run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"run_id={run_id!r} không tồn tại")

    res = await asyncio.to_thread(
        review_rule,
        run_id=run_id,
        rule_id=rule_id,
        status=body.status.value if hasattr(body.status, "value") else body.status,
        edited_parameters=body.edited_parameters,
        severity=body.severity,
        reviewer=body.reviewer,
        review_note=body.review_note,
    )
    if not res:
        raise HTTPException(status_code=404, detail=f"rule_id={rule_id!r} không tồn tại trong run_id={run_id!r}")
    return RuleReviewResponse(**res)


@dq_router.post(
    "/runs/{run_id}/rules/bulk-review",
    response_model=BulkReviewResponse,
)
async def bulk_review_proposal_rules(
    run_id: str,
    body: BulkReviewRequest,
) -> BulkReviewResponse:
    from src.services.rule_store import bulk_review, get_run

    run = await asyncio.to_thread(get_run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"run_id={run_id!r} không tồn tại")

    decisions_dict = [
        {
            "rule_id": d.rule_id,
            "status": d.status.value if hasattr(d.status, "value") else d.status,
            "edited_parameters": d.edited_parameters,
            "severity": d.severity,
            "reviewer": d.reviewer,
            "review_note": d.review_note,
        }
        for d in body.decisions
    ]
    updated, not_found = await asyncio.to_thread(bulk_review, run_id, decisions_dict)
    return BulkReviewResponse(
        updated_count=len(updated), rules=[RuleReviewResponse(**r) for r in updated], not_found=not_found
    )


@dq_router.get(
    "/runs/{run_id}/review-summary",
    response_model=ReviewSummaryResponse,
)
async def get_run_review_summary(run_id: str) -> ReviewSummaryResponse:
    from src.services.rule_store import get_review_summary, get_run

    run = await asyncio.to_thread(get_run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"run_id={run_id!r} không tồn tại")

    res = await asyncio.to_thread(get_review_summary, run_id)
    return ReviewSummaryResponse(**res)


@dq_router.get(
    "/runs/{run_id}/approved-rules",
    response_model=ApprovedRulesResponse,
)
async def get_run_approved_rules(run_id: str) -> ApprovedRulesResponse:
    from src.services.rule_store import get_approved_rules, get_run

    run = await asyncio.to_thread(get_run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"run_id={run_id!r} không tồn tại")

    rules = await asyncio.to_thread(get_approved_rules, run_id)
    return ApprovedRulesResponse(run_id=run_id, count=len(rules), rules=[RuleReviewResponse(**r) for r in rules])
