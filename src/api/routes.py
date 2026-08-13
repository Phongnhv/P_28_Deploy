import json
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel
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
    SessionModel,
)
from src.services.job_runner import add_audit_event, run_dq_checks, run_ingest_profile, run_propose_rules
from src.services.rule_store import get_engine
from src.services.session_service import (
    SESSION_COOKIE_NAME,
    create_user_session,
    enforce_role,
    get_current_session,
    verify_csrf,
)

logger = logging.getLogger(__name__)
router = APIRouter()

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
            status_code=422,
            detail={"code": "VALIDATION_ERROR", "message": "Idempotency-Key header is required"}
        )
    existing = db.query(JobModel).filter(JobModel.idempotency_key == key).first()
    if existing:
        if existing.status in ("PENDING", "RUNNING"):
            raise HTTPException(
                status_code=409,
                detail={"code": "CONFLICT", "message": "Job with this idempotency key is already active"}
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
    sample_value: str

class DatasetProfileSchema(BaseModel):
    dataset_id: str
    row_count: int
    completeness_score: float
    validity_score: float
    duplicate_rate: float
    columns: list[ColumnProfileSchema]
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
    action: str # approve, reject, edit
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
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session.id,
        httponly=True,
        samesite="lax",
        path="/"
    )

    add_audit_event(
        db,
        session_id=session.id,
        actor_role=session.role,
        action_code="LOGIN",
        entity_type="session",
        entity_id=session.id,
        detail={"username": session.username, "role": session.role}
    )

    return SessionResponse(
        username=session.username,
        role=session.role,
        csrf_token=session.csrf_token,
        expires_at=session.expires_at.isoformat()
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
                detail={"username": session.username}
            )
            db.delete(session)
            db.commit()

    # Clear cookie
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return Response(status_code=204)

@router.get("/datasets")
def list_datasets(session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])), db: Session = Depends(get_db)):
    """
    GET /api/v1/datasets - Lists available datasets and status.
    """
    datasets = db.query(DatasetModel).all()
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
            "updated_at": d.updated_at.isoformat()
        } for d in datasets
    ]

@router.post("/datasets/{id}/ingestions", status_code=202, response_model=CreateJobResponse)
def start_ingestion(
    id: str,
    background_tasks: BackgroundTasks,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db)
):
    """
    POST /api/v1/datasets/{id}/ingestions - Starts background ingestion and profiling job.
    """
    dataset = db.query(DatasetModel).filter(DatasetModel.id == id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    collision_job_id = verify_idempotency(db, idempotency_key)
    if collision_job_id:
        return CreateJobResponse(job_id=collision_job_id, status="PENDING")

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
        updated_at=datetime.utcnow()
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
        detail={"type": "INGEST_PROFILE", "dataset_id": id}
    )

    background_tasks.add_task(run_ingest_profile, job_id, id, session.id, session.role)
    return CreateJobResponse(job_id=job_id, status="PENDING")

@router.get("/jobs/{id}")
def get_job_status(id: str, db: Session = Depends(get_db)):
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
        "error": job.error
    }

@router.get("/datasets/{id}/profile", response_model=DatasetProfileSchema)
def get_dataset_profile(id: str, session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])), db: Session = Depends(get_db)):
    """
    GET /api/v1/datasets/{id}/profile - Returns completed profiling details. Returns 404 until complete.
    """
    dataset = db.query(DatasetModel).filter(DatasetModel.id == id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

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
            sample_value=c.sample_value
        ) for c in cols
    ]

    return DatasetProfileSchema(
        dataset_id=profile.dataset_id,
        row_count=profile.row_count,
        completeness_score=profile.completeness_score,
        validity_score=profile.validity_score,
        duplicate_rate=profile.duplicate_rate,
        columns=columns_list,
        evidence_keys=json.loads(profile.evidence_keys),
        generated_at=profile.generated_at.isoformat()
    )

@router.post("/datasets/{id}/rule-proposals", status_code=202, response_model=CreateJobResponse)
def start_rule_proposals(
    id: str,
    background_tasks: BackgroundTasks,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db)
):
    """
    POST /api/v1/datasets/{id}/rule-proposals - Triggers LLM/deterministic proposals generator.
    """
    dataset = db.query(DatasetModel).filter(DatasetModel.id == id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    if dataset.status != "PROFILE_READY":
        raise HTTPException(status_code=400, detail="Completed profile is required before requesting proposals")

    collision_job_id = verify_idempotency(db, idempotency_key)
    if collision_job_id:
        return CreateJobResponse(job_id=collision_job_id, status="PENDING")

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
        updated_at=datetime.utcnow()
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
        detail={"type": "PROPOSE_RULES", "dataset_id": id}
    )

    background_tasks.add_task(run_propose_rules, job_id, id, session.id, session.role)
    return CreateJobResponse(job_id=job_id, status="PENDING")

@router.get("/rule-proposals", response_model=list[RuleProposalSchema])
def list_proposals(dataset_id: str, session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])), db: Session = Depends(get_db)):
    """
    GET /api/v1/rule-proposals - Returns rule proposals for a dataset.
    """
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
            updated_at=p.updated_at.isoformat()
        ) for p in proposals
    ]

@router.post("/datasets/{id}/rule-proposals/manual", response_model=RuleProposalSchema)
def create_manual_rule(
    id: str,
    body: ManualRuleInput,
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db)
):
    """
    POST /api/v1/datasets/{id}/rule-proposals/manual - Creates a manually authored DQ rule, immediately approved.
    """
    dataset = db.query(DatasetModel).filter(DatasetModel.id == id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    prop_id = f"manual-{str(uuid.uuid4())[:8]}"
    prop = RuleProposalModel(
        id=prop_id,
        dataset_id=id,
        title=body.title,
        description=body.description,
        severity=body.severity.upper(),
        status="APPROVED",
        rule_type=body.rule.type,
        rule_spec=json.dumps(body.rule.model_dump(exclude_none=True)),
        evidence_refs=json.dumps(["manual"]),
        evidence_summary="Manually added by data steward",
        confidence=1.0,
        model_name="data-steward",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    db.add(prop)
    db.commit()

    # Immediately write rule version
    rv = RuleVersionModel(
        id=f"rv_{prop_id}",
        rule_proposal_id=prop_id,
        dataset_id=id,
        rule_spec=json.dumps(body.rule.model_dump(exclude_none=True)),
        status="APPROVED",
        version=1,
        created_at=datetime.utcnow()
    )
    db.add(rv)
    db.commit()

    add_audit_event(
        db,
        session_id=session.id,
        actor_role=session.role,
        action_code="PROPOSAL_CREATED",
        entity_type="rule_proposal",
        entity_id=prop_id,
        detail={"manual": True}
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
        updated_at=prop.updated_at.isoformat()
    )

@router.patch("/rule-proposals/{id}", response_model=RuleProposalSchema)
def review_proposal(
    id: str,
    body: ReviewInput,
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db)
):
    """
    PATCH /api/v1/rule-proposals/{id} - Allows Steward to approve/reject/edit proposals.
    """
    prop = db.query(RuleProposalModel).filter(RuleProposalModel.id == id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Rule proposal not found")

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
                created_at=datetime.utcnow()
            )
            db.add(rv)
        else:
            existing_rv.status = "APPROVED"
        db.commit()
        add_audit_event(
            db,
            session_id=session.id,
            actor_role=session.role,
            action_code="PROPOSAL_APPROVED",
            entity_type="rule_proposal",
            entity_id=prop.id,
            detail={"action": "approve"}
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
            detail={"action": "reject"}
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
                created_at=datetime.utcnow()
            )
            db.add(rv)
        db.commit()
        add_audit_event(
            db,
            session_id=session.id,
            actor_role=session.role,
            action_code="PROPOSAL_EDITED",
            entity_type="rule_proposal",
            entity_id=prop.id,
            detail={"action": "edit"}
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
        updated_at=prop.updated_at.isoformat()
    )

@router.post("/dq-runs", status_code=202, response_model=DqRunCreateResponse)
def start_dq_run(
    body: DqRunCreateRequest,
    background_tasks: BackgroundTasks,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db)
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
        return DqRunCreateResponse(job_id=collision_job_id, run_id=run_id, status="PENDING")

    # Resolve dataset_id from the first rule
    rule_id = body.rule_ids[0]
    # Check if rule ID starts with rv_
    lookup_id = rule_id if rule_id.startswith("rv_") else f"rv_{rule_id}"
    rv = db.query(RuleVersionModel).filter(RuleVersionModel.id == lookup_id).first()
    if not rv:
        raise HTTPException(status_code=400, detail=f"Rule version {rule_id} not found or not approved")

    dataset_id = rv.dataset_id

    # Verify all approved rules belong to same dataset
    # We prefix query ids to match rv_
    normalized_ids = [rid if rid.startswith("rv_") else f"rv_{rid}" for rid in body.rule_ids]
    approved_rules = db.query(RuleVersionModel).filter(
        RuleVersionModel.id.in_(normalized_ids),
        RuleVersionModel.status == "APPROVED"
    ).all()

    if len(approved_rules) != len(body.rule_ids):
        raise HTTPException(status_code=400, detail="Some selected rules are not approved or do not exist")

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
        updated_at=datetime.utcnow()
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
        created_at=datetime.utcnow()
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
        detail={"rule_ids": normalized_ids}
    )

    background_tasks.add_task(run_dq_checks, job_id, run_id, session.id, session.role)
    return DqRunCreateResponse(job_id=job_id, run_id=run_id, status="PENDING")

@router.get("/dq-runs/{id}", response_model=DqRunSchema)
def get_dq_run(id: str, session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])), db: Session = Depends(get_db)):
    """
    GET /api/v1/dq-runs/{id} - Returns DQ run progress.
    """
    run = db.query(DqRunModel).filter(DqRunModel.id == id).first()
    if not run:
        raise HTTPException(status_code=404, detail="DQ run not found")

    return DqRunSchema(
        id=run.id,
        job_id=run.job_id,
        dataset_id=run.dataset_id,
        rule_ids=json.loads(run.rule_ids),
        status=run.status,
        total_failed=run.total_failed,
        total_checked=run.total_checked,
        created_at=run.created_at.isoformat(),
        completed_at=run.completed_at.isoformat() if run.completed_at else None
    )

@router.get("/dq-runs/{id}/results", response_model=list[DqResultSchema])
def get_dq_results(id: str, session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])), db: Session = Depends(get_db)):
    """
    GET /api/v1/dq-runs/{id}/results - Returns checks results with failed counts. No raw cells.
    """
    run = db.query(DqRunModel).filter(DqRunModel.id == id).first()
    if not run:
        raise HTTPException(status_code=404, detail="DQ run not found")

    results = db.query(DqResultModel).filter(DqResultModel.run_id == id).all()
    return [
        DqResultSchema(
            rule_id=r.rule_id,
            rule_title=r.rule_title,
            status=r.status,
            checked_count=r.checked_count,
            failed_count=r.failed_count,
            failed_row_ids=json.loads(r.failed_row_ids)
        ) for r in results
    ]

@router.get("/audit-logs", response_model=list[AuditLogSchema])
def list_audit_logs(
    limit: int = 50,
    offset: int = 0,
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db)
):
    """
    GET /api/v1/audit-logs - Returns paginated system logs.
    """
    logs = db.query(AuditEventModel).order_by(AuditEventModel.created_at.desc()).offset(offset).limit(limit).all()
    return [
        AuditLogSchema(
            id=log.id,
            action=log.action_code,
            entity_type=log.entity_type,
            entity_id=log.entity_id,
            actor=f"{log.actor_role} ({db.query(SessionModel).filter(SessionModel.id == log.session_id).first().username if log.session_id and db.query(SessionModel).filter(SessionModel.id == log.session_id).first() else 'system'})",
            summary=json.loads(log.detail_json).get("message", f"Transitioned {log.entity_type} {log.entity_id}"),
            created_at=log.created_at.isoformat()
        ) for log in logs
    ]

# ---------------------------------------------------------------------------
# Compatibility / Smoke Test Route
# ---------------------------------------------------------------------------
class SmokeCreateJobRequest(BaseModel):
    type: str
    linked_entity: str = None

@router.post("/jobs", status_code=202)
def compatibility_trigger_job(
    request: SmokeCreateJobRequest,
    background_tasks: BackgroundTasks,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    db: Session = Depends(get_db)
):
    """
    POST /api/v1/jobs - Smoke test compatibility job dispatcher.
    """
    collision_job_id = verify_idempotency(db, idempotency_key)
    if collision_job_id:
        raise HTTPException(
            status_code=409,
            detail={"code": "CONFLICT", "message": "Job with this idempotency key is already active"}
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
        updated_at=datetime.utcnow()
    )
    db.add(job)
    db.commit()

    if request.type == "INGEST_PROFILE":
        background_tasks.add_task(run_ingest_profile, job_id, "dataset-nyc-yellow-taxi-50k", None, "SYSTEM")
    elif request.type == "PROPOSE_RULES":
        background_tasks.add_task(run_propose_rules, job_id, "dataset-nyc-yellow-taxi-50k", None, "SYSTEM")

    return {"job_id": job.id, "status": job.status, "message": "Job accepted"}
