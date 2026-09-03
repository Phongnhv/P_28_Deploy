import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.config import get_settings
from src.models.api_schemas import (
    AnomalyFeedbackRequest,
    AnomalySignalDTO,
    CombinedRunStatusResponse,
    ExecutionRequest,
    PublishRulesetRequest,
    PublishRulesetResponse,
)
from src.models.database import (
    AnalysisNodeExecutionModel,
    AnalysisRunModel,
    AnomalyFeedbackModel,
    AnomalyHypothesisModel,
    AnomalyRunModel,
    AnomalySignalModel,
    AuditEventModel,
    ColumnProfileModel,
    DatasetAccessModel,
    DatasetGovernanceModel,
    DatasetModel,
    DatasetVersionModel,
    DqResultModel,
    DqRunModel,
    GovernanceAuditEventModel,
    GovernedArtifactModel,
    Graph1NodeExecutionModel,
    Graph1RunModel,
    GraphNodeRunModel,
    JobModel,
    ProfileModel,
    ProfileRunSnapshotModel,
    RuleConfigurationModel,
    RuleProposalModel,
    RulesetVersionModel,
    RuleVersionModel,
    SessionModel,
    SourceRowModel,
    UserAccountModel,
    WorkflowArtifactModel,
    WorkflowRunModel,
    WorkspaceMembershipModel,
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
from src.services.data_dictionary_store import (
    DataDictionaryError,
    delete_data_dictionary,
    get_data_dictionary,
    parse_data_dictionary,
    save_data_dictionary,
    serialize_data_dictionary,
)
from src.services.demo_quota import enforce_demo_quota
from src.services.job_dispatch import (
    create_persisted_job,
    dispatch_or_mark_failed,
    dispatch_persisted_job,
    job_checksum,
)
from src.services.job_runner import (
    DEMO_TAXI_DATASET_ID,
    _supabase_source_url,
    add_audit_event,
    run_dq_checks,
    run_ingest_profile,
    run_propose_rules,
)
from src.services.rule_proposer_workflow import (
    WorkflowError,
    complete_rule_review,
    get_or_create_run,
    navigate_forward,
    queue_check_run,
    run_workflow_stage_job,
    serialize_artifact,
    serialize_run,
)
from src.services.rule_proposer_workflow import (
    confirm_semantic_contract as confirm_workflow_semantic_contract,
)
from src.services.rule_proposer_workflow import (
    rewind as rewind_workflow,
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
from src.services.supabase_dataset import create_supabase_engine
from src.services.supabase_dataset import query_dataset_rows as query_supabase_dataset_rows
from src.services.versioned_dataset import (
    DatasetContractError,
    SourceIntegrityError,
    UploadTooLargeError,
    canonical_schema_manifest,
    delete_source_artifact,
    inspect_upload_path,
    schema_hash,
    spool_upload,
    store_source_artifact_path,
)
from src.time_utils import utc_now

logger = logging.getLogger(__name__)


class SemanticContractConfirmInput(BaseModel):
    artifact_id: str
    expected_version: int
    contract: dict[str, Any]
    review_note: str | None = None


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
def get_session(request: Request, db: Session = Depends(get_db)) -> SessionModel:
    """Authenticate in FastAPI's worker thread, not on the event loop.

    The checks below are synchronous SQLAlchemy work.  Keeping this dependency
    async made concurrent requests perform pool checkout/query work on the
    event loop; once the bounded Supabase pool was busy, even unrelated async
    endpoints such as ``/health`` stopped making progress.
    """
    session = get_current_session(request, db)
    verify_csrf(request, session)
    enforce_demo_quota(db, request, session)
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
    validity_score: float | None
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
    regex: str | None = None
    operator: str | None = None
    fingerprint_columns: list[str] | None = None
    max_null_pct: float | None = Field(default=None, ge=0, le=100)
    max_age_hours: float | None = Field(default=None, ge=0)
    min_row_count: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_regex_rule(self):
        if self.type.upper() == "REGEX_FORMAT":
            from src.services.safe_regex import validate_regex

            validate_regex(self.regex or "")
        return self


class RuleProposalSchema(BaseModel):
    id: str
    dataset_id: str
    workflow_run_id: str | None = None
    title: str
    description: str
    severity: str
    status: str
    rule: RuleSpecSchema
    evidence_refs: list[str]
    evidence_summary: str
    confidence: float
    model_name: str
    rule_name: str
    business_rationale: str
    proposal_basis: str
    evidence: dict
    parameter_provenance: list[dict] = []
    assumptions: list[str] = []
    confidence_breakdown: dict
    created_at: str
    updated_at: str


class ManualRuleInput(BaseModel):
    title: str
    description: str
    severity: str
    rule: RuleSpecSchema
    workflow_run_id: str | None = None


class ReviewInput(BaseModel):
    action: str  # approve, reject, edit
    title: str | None = None
    description: str | None = None
    severity: str | None = None
    rule: RuleSpecSchema | None = None
    workflow_run_id: str | None = None


class WorkflowRewindInput(BaseModel):
    target_step: str


class ArtifactReviewInput(BaseModel):
    action: str
    comment: str | None = None


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
    # Dataset-level rules report a measured rate rather than offending rows; it
    # was persisted but never returned, so the UI had nothing to show for them.
    violation_rate: float | None = None
    error_message: str | None = None


class DqAnomalySchema(BaseModel):
    rule_id: str
    rule_title: str
    anomaly_type: str
    current_rate: float
    historical_mean: float | None
    z_score: float | None
    history_size: int
    detection_mode: str
    checked_count: int
    failed_count: int
    reason: str
    columns: list[str] = []


class DatasetRowSchema(BaseModel):
    source_row_id: str
    vendor_id: str | None
    pickup_at: str | None
    dropoff_at: str | None
    passenger_count: int | None
    trip_distance: float | None
    payment_type: str | None
    fare_amount: float | None
    total_amount: float | None


class DatasetRowsResponse(BaseModel):
    dataset_id: str
    dataset_version_id: str | None = None
    total: int
    limit: int
    offset: int
    rows: list[dict[str, Any]]
    schema: list[dict[str, Any]] | None = None


class QualityTrendPointSchema(BaseModel):
    run_id: str
    created_at: str
    quality_score: float
    failure_rate: float
    total_checked: int
    total_failed: int
    rule_count: int


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


class Graph1SemanticReviewInput(BaseModel):
    contract: dict[str, Any]


class Graph1RuleDecisionInput(BaseModel):
    rule_id: str
    action: str
    rule: dict[str, Any] | None = None


class Graph1RuleReviewInput(BaseModel):
    decisions: list[Graph1RuleDecisionInput]


class AnalysisRunSchema(BaseModel):
    id: str
    graph1_run_id: str
    dataset_id: str
    status: str
    phase: str
    current_node: str | None = None
    test_run_id: str | None = None
    anomaly_run_id: str | None = None
    report_available: bool
    job_id: str | None = None
    report_artifact_status: str = "NOT_AVAILABLE"
    report_artifact_locator: str | None = None
    error: str | None = None
    created_by: str
    created_at: str
    updated_at: str
    completed_at: str | None = None


class AnalysisNodeSchema(BaseModel):
    graph_name: str
    node_key: str
    position: int
    status: str
    output: dict[str, Any]
    error: str | None = None
    sequence: int
    started_at: str | None = None
    completed_at: str | None = None
    duration_ms: float | None = None


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
        # Một lần từ chối là bình thường; một loạt từ chối từ cùng một tài khoản
        # là dấu hiệu dò tìm ngang quyền. Không ghi lại thì không phân biệt được.
        from src.services.session_service import audit_security_event

        audit_security_event(
            "ACCESS_DENIED",
            dataset_id,
            entity_type="dataset",
            actor_role=session.role,
            detail={"username": session.username, "manage": manage},
        )
        raise HTTPException(
            status_code=403,
            detail={"code": "DATASET_ACCESS_FORBIDDEN", "message": "You do not have access to this dataset."},
        )


def require_compat_dataset_access(
    db: Session, session: SessionModel, dataset_id: str | None, *, manage: bool = False
) -> str:
    """Authorize a compatibility object through its persisted dataset identity."""
    resolved = str(dataset_id or "").strip()
    if not resolved or resolved == "unknown":
        raise HTTPException(status_code=404, detail="Dataset not found")
    require_dataset_access(db, session, resolved, manage=manage)
    return resolved


def require_proposal_run_access(
    db: Session, session: SessionModel, run_id: str, *, manage: bool = False
) -> dict[str, Any]:
    from src.services.rule_store import get_run

    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"proposal run_id={run_id!r} does not exist")
    # Dereference before the access check: a version id never matches a
    # dataset_access row, so checking the raw value refuses the rightful owner.
    require_compat_dataset_access(
        db, session, _resolve_dataset_id(db, run.get("dataset_id")), manage=manage
    )
    return run


def require_test_run_access(
    db: Session, session: SessionModel, test_run_id: str, *, manage: bool = False
) -> dict[str, Any]:
    from src.services.rule_store import get_test_run

    run = get_test_run(test_run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"test_run_id={test_run_id!r} does not exist")
    require_compat_dataset_access(db, session, run.get("dataset_id"), manage=manage)
    return run


def require_anomaly_run_access(
    db: Session, session: SessionModel, run_id: str, *, manage: bool = False
) -> AnomalyRunModel:
    anomaly_run = db.get(AnomalyRunModel, run_id)
    if not anomaly_run:
        anomaly_run = db.query(AnomalyRunModel).filter(AnomalyRunModel.execution_run_id == run_id).first()
    if not anomaly_run:
        raise HTTPException(status_code=404, detail=f"Anomaly run {run_id} not found")
    dq_run = db.get(DqRunModel, anomaly_run.execution_run_id)
    if not dq_run:
        raise HTTPException(status_code=404, detail=f"Execution run {anomaly_run.execution_run_id} not found")
    require_compat_dataset_access(db, session, dq_run.dataset_id, manage=manage)
    return anomaly_run


def _resolve_dataset_id(db: Session, linked_entity: str | None) -> str | None:
    """Resolve the dataset a proposal run belongs to.

    ``JobModel.linked_entity`` holds either a dataset id or an immutable dataset
    version id, so the version has to be dereferenced before tenancy can be asked.
    Without this step a run linked to a version id can never match any row in
    ``dataset_access`` and its rightful owner is refused.
    """
    if not linked_entity:
        return None
    if linked_entity.startswith("dv-"):
        version = db.get(DatasetVersionModel, linked_entity)
        return version.dataset_id if version else None
    return linked_entity


def require_run_access(*, manage: bool = False, param: str = "run_id"):
    """Tenancy for the ``/dq`` run endpoints, attached at the decorator.

    ``dq_router`` is mounted with a role dependency only, so a caller holding the
    right role could read, review and publish another tenant's proposal run. The
    check lives in a dependency rather than in each handler because that is how
    those gaps arise -- every one of them simply omits the call, and a route added
    tomorrow would inherit the same gap by doing nothing at all.
    """

    def _dep(
        request: Request,
        session: SessionModel = Depends(get_session),
        db: Session = Depends(get_db),
    ) -> str:
        run_id = str(request.path_params.get(param))
        run = require_proposal_run_access(db, session, run_id, manage=manage)
        return _resolve_dataset_id(db, run.get("dataset_id")) or ""

    return _dep


# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------


@router.post("/session", response_model=SessionResponse)
def login(request: Request, body: LoginRequest, response: Response, db: Session = Depends(get_db)):
    """
    POST /api/v1/session - Authenticates user and sets session cookie + CSRF token.
    """
    session = create_user_session(request, body.username, body.password, db)

    # The Vercel frontend and Cloud Run API are separate sites in production.
    # Cross-site requests therefore require a Secure SameSite=None session
    # cookie; local development remains compatible with HTTP and localhost.
    production = get_settings().app_env == "production"
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session.id,
        httponly=True,
        secure=production,
        samesite="none" if production else "lax",
        path="/",
    )

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
    response = []
    for d in datasets:
        from src.services.source_binding import dataset_source_version
        try:
            latest_version = dataset_source_version(db, d.id)
        except SourceIntegrityError:
            latest_version = None
        latest_profile = (
            db.query(ProfileRunSnapshotModel)
            .filter_by(dataset_version_id=latest_version.id, status="COMPLETED")
            .order_by(ProfileRunSnapshotModel.completed_at.desc())
            .first()
            if latest_version
            else None
        )
        has_local_source = any((Path("data/uploads") / f"{d.id}{suffix}").exists() for suffix in (".parquet", ".csv"))
        response.append({
            "id": d.id,
            "name": d.name,
            "description": d.description,
            "status": d.status,
            "row_count": d.row_count,
            "source_label": d.source_label,
            "manifest_version": d.manifest_version,
            "checksum": d.checksum,
            "updated_at": d.updated_at.isoformat(),
            "data_explorer_available": bool(latest_version or has_local_source),
            "dataset_version_id": latest_version.id if latest_version else None,
            "version_number": latest_version.version_number if latest_version else None,
            "profile_run_id": latest_profile.id if latest_profile else None,
        })
    return response


@router.post("/datasets/import")
async def import_dataset(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """Legacy dashboard import; canonical arbitrary imports use the workspace route below.

    This compatibility endpoint retains the historical local dashboard profile
    contract. It is deliberately not used by versioned Graph 1/2/3 flows.
    """
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".csv", ".parquet"}:
        raise HTTPException(status_code=415, detail="Only CSV and Parquet files are supported.")
    try:
        temp_path, upload_size, upload_checksum = await spool_upload(file, file.filename or f"imported{suffix}")
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except DatasetContractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    dataset_id = f"dataset-import-{uuid.uuid4().hex[:20]}"
    upload_dir = Path(get_settings().upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    upload_path = upload_dir / f"{dataset_id}{suffix}"
    try:
        inspected = inspect_upload_path(temp_path, file.filename or f"imported{suffix}", file.content_type, checksum=upload_checksum, size_bytes=upload_size)
    except UploadTooLargeError as exc:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except DatasetContractError as exc:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    upload_path.parent.mkdir(parents=True, exist_ok=True)
    upload_path.unlink(missing_ok=True)
    import shutil
    shutil.move(str(temp_path), str(upload_path))
    if get_settings().object_storage_enabled:
        try:
            from src.services.dbt_artifact_store import get_dbt_artifact_store
            get_dbt_artifact_store().upload_dataset_path(dataset_id, upload_path)
        except Exception as exc:
            logger.warning("Failed to upload dataset to MinIO: %s", exc)
    display_name = Path(file.filename or "Imported dataset").stem.replace("_", " ").strip() or "Imported dataset"
    dataset = DatasetModel(
        id=dataset_id,
        name=display_name[:256],
        # No progress claim here. ``status`` already reports REGISTERED ->
        # PROFILE_READY, and a sentence frozen at import time kept telling the
        # catalogue that profiling was running long after it had finished.
        description="Imported CSV/Parquet dataset.",
        status="REGISTERED",
        row_count=0,
        source_label=file.filename or f"imported{suffix}",
        manifest_version="import-v1",
        checksum=inspected.checksum,
    )
    job_id = str(uuid.uuid4())
    job = JobModel(
        id=job_id,
        type="INGEST_PROFILE",
        status="PENDING",
        progress=0.0,
        message="Queued for profiling",
        idempotency_key=f"import-{dataset_id}",
        linked_entity=dataset_id,
        correlation_id=str(uuid.uuid4()),
        attempt_count=1,
    )
    db.add(dataset)
    db.flush()
    db.add(job)
    db.add(
        DatasetAccessModel(
            id=str(uuid.uuid4()),
            dataset_id=dataset_id,
            username=session.username,
            access_level="MANAGE",
            granted_by=session.username,
        )
    )
    db.commit()
    add_audit_event(
        db,
        session_id=session.id,
        actor_role=session.role,
        action_code="DATASET_IMPORTED",
        entity_type="dataset",
        entity_id=dataset_id,
        detail={"filename": file.filename, "job_id": job_id},
    )
    dispatch_or_mark_failed(db, job)
    return {
        "dataset": {
            "id": dataset.id,
            "name": dataset.name,
            "description": dataset.description,
            "status": dataset.status,
            "row_count": dataset.row_count,
            "source_label": dataset.source_label,
            "manifest_version": dataset.manifest_version,
            "checksum": dataset.checksum,
            "updated_at": dataset.updated_at.isoformat(),
        },
        "job": {"job_id": job_id, "status": "PENDING"},
    }


@router.post("/workspaces/{workspace_id}/datasets/import", status_code=202)
async def import_versioned_dataset(
    workspace_id: str,
    file: UploadFile = File(...),
    dataset_id: str | None = Form(None),
    dataset_name: str | None = Form(None),
    client_sha256: str | None = Form(None),
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """Canonical arbitrary CSV/Parquet import with immutable version lineage.

    The job row is reserved before object storage is touched.  That reservation
    is the concurrency gate for the canonical idempotency key and lets a second
    request replay the same version without creating an object, audit event or
    artifact of its own.
    """
    account = db.query(UserAccountModel).filter_by(username=session.username).first()
    membership = db.query(WorkspaceMembershipModel).filter_by(
        workspace_id=workspace_id,
        user_id=account.id if account else "",
        status="ACTIVE",
    ).first()
    if not account or not membership:
        raise HTTPException(status_code=404, detail={"code": "WORKSPACE_NOT_FOUND", "message": "Workspace not found"})
    if membership.role not in {"ADMIN", "STEWARD"} and session.role not in {"ADMIN", "STEWARD"}:
        raise HTTPException(status_code=403, detail={"code": "WORKSPACE_MANAGE_FORBIDDEN", "message": "Workspace management permission is required"})
    try:
        temp_path, upload_size, upload_checksum = await spool_upload(file, file.filename or "dataset.csv")
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except DatasetContractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        inspected = inspect_upload_path(temp_path, file.filename or "dataset.csv", file.content_type, checksum=upload_checksum, size_bytes=upload_size)
    except UploadTooLargeError as exc:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except DatasetContractError as exc:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail={"code": "INVALID_DATASET", "message": str(exc)}) from exc
    if client_sha256 and client_sha256.lower() != inspected.checksum:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail={"code": "CHECKSUM_MISMATCH", "message": "Uploaded checksum does not match content"})

    idempotency_key = idempotency_key.strip()
    if not idempotency_key:
        raise HTTPException(status_code=422, detail={"code": "IDEMPOTENCY_KEY_REQUIRED", "message": "Idempotency-Key must not be empty"})
    # The database keeps one globally unique idempotency key.  Include the
    # workspace so the same client key can safely be reused in another
    # workspace without colliding with this import reservation.
    canonical_key = f"versioned-import-{workspace_id}-{idempotency_key}"

    def replay_response(version: DatasetVersionModel | None, job: JobModel | None, *, replay: bool = True) -> dict[str, Any]:
        if version:
            replay_dataset_id = version.dataset_id
            replay_name = (db.get(DatasetModel, replay_dataset_id) or DatasetModel(id=replay_dataset_id, name=replay_dataset_id)).name
            return {
                "dataset": {"id": replay_dataset_id, "name": replay_name, "status": (db.get(DatasetModel, replay_dataset_id).status if db.get(DatasetModel, replay_dataset_id) else "REGISTERED")},
                "version": {"id": version.id, "version_number": version.version_number, "status": version.status, "checksum": version.checksum, "schema_hash": version.schema_hash, "row_count": version.row_count},
                "profile_run_id": f"profile-{version.id}",
                "job": {"job_id": job.id if job else None, "status": job.status if job else "COMPLETED"},
                "idempotent_replay": replay,
            }
        reservation = {}
        if job:
            try:
                reservation = json.loads(job.message or "{}")
            except (TypeError, ValueError):
                reservation = {}
        return {
            "dataset": {"id": reservation.get("dataset_id", dataset_id or ""), "name": reservation.get("dataset_name") or dataset_name or "Imported dataset", "status": "REGISTERED"},
            "version": {"id": reservation.get("version_id"), "version_number": reservation.get("version_number", 1), "status": "PENDING", "checksum": reservation.get("checksum", inspected.checksum), "schema_hash": reservation.get("schema_hash"), "row_count": reservation.get("row_count", inspected.row_count)},
            "profile_run_id": f"profile-{reservation.get('version_id')}" if reservation.get("version_id") else None,
            "job": {"job_id": job.id if job else None, "status": job.status if job else "PENDING"},
            "idempotent_replay": replay,
        }

    # Canonical key lookup happens before dataset creation, object storage, or
    # audit/artifact writes.  A reused key is allowed only for the same bytes.
    existing_job = db.query(JobModel).filter(JobModel.idempotency_key == canonical_key).first()
    if existing_job:
        existing_version = db.get(DatasetVersionModel, existing_job.linked_entity) if existing_job.linked_entity else None
        known_checksum = existing_version.checksum if existing_version else job_checksum(existing_job)
        if known_checksum and known_checksum != inspected.checksum:
            raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_KEY_REUSED", "message": "Idempotency-Key is already bound to a different payload"})
        temp_path.unlink(missing_ok=True)
        return replay_response(existing_version, existing_job)

    # For a request that does not supply a dataset id, checksum identity is
    # workspace-scoped.  This is what makes two UI retries with different
    # generated names converge on one immutable version.
    checksum_query = db.query(DatasetVersionModel).filter(
        DatasetVersionModel.workspace_id == workspace_id,
        DatasetVersionModel.checksum == inspected.checksum,
    )
    if dataset_id:
        checksum_query = checksum_query.filter(DatasetVersionModel.dataset_id == dataset_id)
    checksum_version = checksum_query.order_by(DatasetVersionModel.created_at.asc()).first()
    if checksum_version:
        checksum_job = db.query(JobModel).filter(JobModel.linked_entity == checksum_version.id).order_by(JobModel.created_at.desc()).first()
        temp_path.unlink(missing_ok=True)
        return replay_response(checksum_version, checksum_job)

    logical_id = dataset_id or f"dataset-import-{uuid.uuid4().hex[:20]}"
    existing_dataset = db.get(DatasetModel, logical_id)
    if existing_dataset:
        governance = db.query(DatasetGovernanceModel).filter_by(dataset_id=logical_id, workspace_id=workspace_id).first()
        if not governance:
            raise HTTPException(status_code=404, detail={"code": "DATASET_NOT_FOUND", "message": "Dataset not found in workspace"})
        # Different bytes are a separate dataset. Keep existing source/history
        # intact and never create another selectable source under the same ID.
        logical_id = f"dataset-import-{uuid.uuid4().hex[:20]}"
        existing_dataset = None
        governance = None
    else:
        governance = None

    version_number = 1
    parent = None
    version_id = f"dv-{uuid.uuid4().hex[:24]}"
    reservation_message = json.dumps({
        "kind": "VERSIONED_IMPORT_RESERVATION",
        "checksum": inspected.checksum,
        "schema_hash": schema_hash(inspected.schema),
        "dataset_id": logical_id,
        "dataset_name": dataset_name or Path(inspected.filename).stem or logical_id,
        "version_id": version_id,
        "version_number": version_number,
        "row_count": inspected.row_count,
    }, ensure_ascii=False, sort_keys=True)
    try:
        job, created = create_persisted_job(
            db,
            job_type="INGEST_PROFILE",
            linked_entity=version_id,
            idempotency_key=canonical_key,
            message=reservation_message,
        )
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_RESERVATION_CONFLICT", "message": "The import reservation could not be claimed safely"}) from exc
    if not created:
        existing_version = db.get(DatasetVersionModel, job.linked_entity) if job.linked_entity else None
        known_checksum = existing_version.checksum if existing_version else job_checksum(job)
        if known_checksum and known_checksum != inspected.checksum:
            raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_KEY_REUSED", "message": "Idempotency-Key is already bound to a different payload"})
        temp_path.unlink(missing_ok=True)
        return replay_response(existing_version, job)

    artifact_ref = None
    try:
        artifact_ref = store_source_artifact_path(temp_path, inspected, workspace_id=workspace_id, dataset_id=logical_id, dataset_version_id=version_id)
    except SourceIntegrityError as exc:
        db.rollback()
        try:
            db.query(JobModel).filter(JobModel.id == job.id).delete(synchronize_session=False)
            db.commit()
        except Exception:
            # If the cleanup transaction is itself unavailable, leave an
            # explicit retryable reservation rather than a misleading PENDING
            # job that can never be diagnosed or retried safely.
            db.rollback()
            try:
                reserved = db.get(JobModel, job.id)
                if reserved:
                    reserved.status = "FAILED_RETRYABLE"
                    reserved.error = "Source storage failed and reservation cleanup could not be completed"
                    reserved.message = "Import reservation requires retry"
                    db.commit()
            except Exception:
                db.rollback()
        raise HTTPException(status_code=502, detail={"code": "SOURCE_STORAGE_FAILED", "message": str(exc)}) from exc
    finally:
        temp_path.unlink(missing_ok=True)
    artifact_id = f"artifact-{uuid.uuid4().hex}"
    metadata = {
        "filename": inspected.filename,
        "format": inspected.format,
        "content_type": file.content_type,
        "size_bytes": inspected.size_bytes,
        "checksum": inspected.checksum,
        "schema": inspected.schema,
        "schema_hash": schema_hash(inspected.schema),
        "source_artifact_id": artifact_id,
        "bucket": artifact_ref.bucket,
        "object_key": artifact_ref.object_key,
        "version_id": artifact_ref.version_id,
        "idempotency_key": idempotency_key,
    }
    if not existing_dataset:
        existing_dataset = DatasetModel(
            id=logical_id,
            name=(dataset_name or Path(inspected.filename).stem or logical_id)[:256],
            description="Generic versioned CSV/Parquet dataset",
            status="REGISTERED",
            row_count=inspected.row_count,
            source_label=inspected.filename,
            manifest_version="versioned-v1",
            checksum=inspected.checksum,
        )
        db.add(existing_dataset)
        db.flush()
        governance = DatasetGovernanceModel(dataset_id=logical_id, workspace_id=workspace_id, owner_user_id=account.id)
        db.add(governance)
    else:
        existing_dataset.checksum = inspected.checksum
        existing_dataset.source_label = inspected.filename
        existing_dataset.row_count = inspected.row_count
    version = DatasetVersionModel(
        id=version_id,
        workspace_id=workspace_id,
        dataset_id=logical_id,
        version_number=version_number,
        parent_version_id=parent.id if parent else None,
        status="READY",
        checksum=inspected.checksum,
        schema_hash=metadata["schema_hash"],
        row_count=inspected.row_count,
        source_metadata_json=json.dumps(metadata, ensure_ascii=False, sort_keys=True),
        created_by=account.id,
    )
    job.message = "Queued for immutable version profiling"
    db.add_all([version, GovernedArtifactModel(
        id=artifact_id, workspace_id=workspace_id, dataset_id=logical_id, dataset_version_id=version_id,
        artifact_type="SOURCE_DATASET", storage_locator=artifact_ref.storage_locator,
        checksum=inspected.checksum, created_by=account.id,
    ), job, DatasetAccessModel(
        id=str(uuid.uuid4()), dataset_id=logical_id, username=session.username,
        access_level="MANAGE", granted_by=session.username,
    )])
    audit_common = {
        "workspace_id": workspace_id,
        "actor_id": account.id,
        "actor_role": membership.role,
        "dataset_id": logical_id,
        "dataset_version_id": version_id,
        "run_id": job.id,
        "correlation_id": job.correlation_id,
        "request_metadata_json": json.dumps({"filename": inspected.filename, "content_type": file.content_type}, ensure_ascii=False),
        "source": "API",
        "occurred_at": utc_now(),
    }
    db.add_all([
        GovernanceAuditEventModel(
            id=f"gaudit-{uuid.uuid4().hex}", action="DATASET_UPLOAD_ACCEPTED",
            entity_type="dataset", entity_id=logical_id,
            detail_json=json.dumps({"size_bytes": inspected.size_bytes, "format": inspected.format}, ensure_ascii=False),
            **audit_common,
        ),
        GovernanceAuditEventModel(
            id=f"gaudit-{uuid.uuid4().hex}", action="SOURCE_ARTIFACT_REGISTERED",
            entity_type="governed_artifact", entity_id=artifact_id,
            detail_json=json.dumps({"artifact_type": "SOURCE_DATASET", "checksum": inspected.checksum}, ensure_ascii=False),
            **audit_common,
        ),
        GovernanceAuditEventModel(
            id=f"gaudit-{uuid.uuid4().hex}", action="DATASET_VERSION_CREATED",
            entity_type="dataset_version", entity_id=version_id,
            detail_json=json.dumps({"checksum": inspected.checksum, "schema_hash": metadata["schema_hash"], "row_count": inspected.row_count}, ensure_ascii=False),
            **audit_common,
        ),
    ])
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        try:
            delete_source_artifact(artifact_ref)
        except Exception:
            logger.exception("Failed to compensate source artifact for import %s", version_id)
        # The reservation is ours; remove it so a subsequent retry can safely
        # claim the canonical key.  This is deliberately best effort because a
        # database outage is the original failure being reported.
        try:
            db.query(JobModel).filter(JobModel.id == job.id).delete(synchronize_session=False)
            db.commit()
        except Exception:
            db.rollback()
        if isinstance(exc, IntegrityError):
            raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_COMMIT_CONFLICT", "message": "The import collided with another committed request"}) from exc
        raise HTTPException(status_code=503, detail={"code": "IMPORT_COMMIT_FAILED", "message": "Dataset import could not be committed; no source object was retained"}) from exc

    dispatch_or_mark_failed(db, job)
    db.refresh(job)
    db.refresh(version)
    return {
        "dataset": {"id": logical_id, "name": existing_dataset.name, "status": existing_dataset.status},
        "version": {"id": version.id, "version_number": version.version_number, "status": version.status, "checksum": version.checksum, "schema_hash": version.schema_hash, "row_count": version.row_count},
        "profile_run_id": f"profile-{version.id}",
        "job": {"job_id": job.id, "status": job.status},
        "idempotent_replay": False,
    }


# ---------------------------------------------------------------------------
# Canonical Graph 1 API (real agent execution; never falls back to fixtures)
# ---------------------------------------------------------------------------

def _require_graph_provider() -> None:
    settings = get_settings()
    if settings.agent_mode != "graph":
        raise HTTPException(status_code=503, detail={"code": "GRAPH_MODE_REQUIRED", "message": "Set AGENT_MODE=graph to run Graph 1."})
    keys = {
        "openai": settings.openai_api_key,
        "anthropic": settings.anthropic_api_key,
        "mistral": settings.mistral_api_key,
        "google": settings.google_api_key,
    }
    if not keys.get(settings.llm_provider):
        raise HTTPException(status_code=503, detail={"code": "LLM_NOT_CONFIGURED", "message": f"Missing API key for provider '{settings.llm_provider}'."})


@router.post("/datasets/{dataset_id}/graph1-runs", status_code=202)
def start_graph1_run(
    dataset_id: str,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    dataset_version_id: str | None = Query(None),
    profile_run_id: str | None = Query(None),
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    from src.services.graph1_workflow import create_graph1_run, serialize_run

    _require_graph_provider()
    dataset = db.get(DatasetModel, dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    require_dataset_access(db, session, dataset_id, manage=True)
    if dataset_version_id:
        selected_version = db.query(DatasetVersionModel).filter_by(
            id=dataset_version_id, dataset_id=dataset_id, status="READY"
        ).first()
        if not selected_version:
            raise HTTPException(status_code=404, detail={"code": "DATASET_VERSION_NOT_FOUND", "message": "Dataset version not found"})
    try:
        run = create_graph1_run(
            db, dataset_id, session.username, idempotency_key,
            workspace_id=selected_version.workspace_id if dataset_version_id else None,
            dataset_version_id=dataset_version_id,
            profile_run_id=profile_run_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": "DATASET_NOT_READY", "message": str(exc)})
    workflow_job = db.query(JobModel).filter(
        JobModel.linked_entity == run.id,
        JobModel.type.in_(["GRAPH1_EXECUTION", "GRAPH1_CONTINUATION"]),
    ).order_by(JobModel.created_at.desc()).first()
    if run.status == "PENDING":
        job, job_created = create_persisted_job(
            db,
            job_type="GRAPH1_EXECUTION",
            linked_entity=run.id,
            idempotency_key=f"graph1-execution-{run.id}",
            message="Queued Graph 1 execution",
        )
        if job_created or job.status == "FAILED_RETRYABLE":
            dispatch_or_mark_failed(db, job)
        workflow_job = db.get(JobModel, job.id)
        db.refresh(run)
    add_audit_event(db, session.id, session.role, "GRAPH1_STARTED", "graph1_run", run.id,
                    {"message": "Canonical Graph 1 started.", "dataset_id": dataset_id, "provider": get_settings().llm_provider})
    response_payload = serialize_run(run)
    response_payload["job_id"] = workflow_job.id if workflow_job else None
    return response_payload


@router.get("/datasets/{dataset_id}/graph1-runs/latest")
def get_latest_graph1_run(
    dataset_id: str,
    dataset_version_id: str | None = Query(None),
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """Recover the latest durable Graph 1 snapshot for a dataset version."""
    dataset = db.get(DatasetModel, dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    require_dataset_access(db, session, dataset_id)
    query = db.query(Graph1RunModel).filter(Graph1RunModel.dataset_id == dataset_id)
    if dataset_version_id:
        query = query.filter(Graph1RunModel.dataset_version_id == dataset_version_id)
    run = query.order_by(Graph1RunModel.updated_at.desc(), Graph1RunModel.created_at.desc()).first()
    if not run:
        return None
    from src.services.graph1_workflow import serialize_run

    response_payload = serialize_run(run)
    latest_job = db.query(JobModel).filter(
        JobModel.linked_entity == run.id,
        JobModel.type.in_(["GRAPH1_EXECUTION", "GRAPH1_CONTINUATION"]),
    ).order_by(JobModel.created_at.desc()).first()
    response_payload["job_id"] = latest_job.id if latest_job else None
    return response_payload


@router.get("/graph1-runs/{run_id}")
def get_graph1_run(
    run_id: str,
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    from src.services.graph1_workflow import serialize_run
    run = db.get(Graph1RunModel, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Graph 1 run not found")
    require_dataset_access(db, session, run.dataset_id)
    return serialize_run(run)


@router.get("/graph1-runs/{run_id}/nodes")
def get_graph1_nodes(
    run_id: str,
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    from src.services.graph1_workflow import list_nodes
    run = db.get(Graph1RunModel, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Graph 1 run not found")
    require_dataset_access(db, session, run.dataset_id)
    return list_nodes(db, run_id)


@router.get("/graph1-runs/{run_id}/stream")
async def stream_graph1_run(
    run_id: str,
    request: Request,
    after_sequence: int = Query(0, ge=0),
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    from src.services.graph1_workflow import list_nodes, serialize_run
    run = db.get(Graph1RunModel, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Graph 1 run not found")
    require_dataset_access(db, session, run.dataset_id)
    dataset_id = run.dataset_id
    # A streaming response can remain open for minutes. Release the request
    # dependency's checked-out connection before starting the SSE loop; each
    # snapshot below deliberately uses its own short-lived session.
    db.close()

    async def events():
        last_signature = ""
        last_sequence = after_sequence
        while not await request.is_disconnected():
            with Session(get_engine()) as event_db:
                current = event_db.get(Graph1RunModel, run_id)
                if not current:
                    yield "event: error\ndata: {\"message\":\"Run removed\"}\n\n"
                    return
                nodes = list_nodes(event_db, run_id)
                max_sequence = max((node["sequence"] for node in nodes), default=0)
                signature = f"{current.status}:{current.current_node}:{current.updated_at.isoformat()}:{max_sequence}"
                if signature != last_signature and max_sequence >= last_sequence:
                    payload = {"run": serialize_run(current), "nodes": nodes, "dataset_id": dataset_id, "sequence": max_sequence}
                    yield f"id: {max_sequence}\nevent: snapshot\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    last_signature, last_sequence = signature, max_sequence
                if current.status in {"COMPLETED", "FAILED", "AWAITING_SEMANTIC_REVIEW", "AWAITING_RULE_REVIEW"}:
                    return
            yield ": heartbeat\n\n"
            await asyncio.sleep(0.75)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/graph1-runs/{run_id}/semantic-review", status_code=202)
def review_graph1_semantics(
    run_id: str,
    body: Graph1SemanticReviewInput,
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    from src.services.graph1_workflow import confirm_semantic_review, serialize_run
    run = db.get(Graph1RunModel, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Graph 1 run not found")
    require_dataset_access(db, session, run.dataset_id, manage=True)
    try:
        confirm_semantic_review(db, run, body.contract)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": "GRAPH1_STATE", "message": str(exc)})
    job, job_created = create_persisted_job(
        db,
        job_type="GRAPH1_CONTINUATION",
        linked_entity=run.id,
        idempotency_key=f"graph1-continuation-{run.id}-semantic",
        message="Queued Graph 1 continuation after semantic review",
    )
    if job_created or job.status == "FAILED_RETRYABLE":
        dispatch_or_mark_failed(db, job)
    db.refresh(run)
    add_audit_event(db, session.id, session.role, "GRAPH1_SEMANTIC_APPROVED", "graph1_run", run.id,
                    {"message": "Semantic Contract approved; Graph 1 resumed."})
    response_payload = serialize_run(run)
    response_payload["job_id"] = job.id
    return response_payload


@router.post("/graph1-runs/{run_id}/rule-review")
def review_graph1_rules(
    run_id: str,
    body: Graph1RuleReviewInput,
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    from src.services.graph1_workflow import review_rules, serialize_run
    run = db.get(Graph1RunModel, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Graph 1 run not found")
    require_dataset_access(db, session, run.dataset_id, manage=True)
    try:
        review_rules(db, run, [item.model_dump() for item in body.decisions], session.username)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": "GRAPH1_STATE", "message": str(exc)})
    add_audit_event(db, session.id, session.role, "GRAPH1_RULES_REVIEWED", "graph1_run", run.id,
                    {"message": "All Graph 1 rule decisions were recorded."})
    return serialize_run(run)


@router.post(
    "/graph1-runs/{run_id}/analysis-runs",
    response_model=AnalysisRunSchema,
    status_code=202,
)
def start_analysis_run(
    run_id: str,
    response: Response,
    rerun: bool = Query(False),
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    from src.services.analysis_workflow import (
        create_analysis_run,
        serialize_analysis_run,
    )

    graph1_run = db.get(Graph1RunModel, run_id)
    if not graph1_run:
        raise HTTPException(status_code=404, detail="Graph 1 run not found")
    require_dataset_access(db, session, graph1_run.dataset_id, manage=True)
    try:
        analysis_run, created = create_analysis_run(
            db,
            graph1_run,
            session.username,
            idempotency_key,
            force_rerun=rerun,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": "ANALYSIS_NOT_READY", "message": str(exc)})
    if created:
        job, _ = create_persisted_job(
            db,
            job_type="ANALYSIS_GRAPH2_GRAPH3",
            linked_entity=analysis_run.id,
            idempotency_key=f"analysis-graph2-graph3-{analysis_run.id}",
            message="Queued Graph 2 and Graph 3 analysis",
        )
        dispatch_or_mark_failed(db, job)
        db.refresh(analysis_run)
        add_audit_event(
            db,
            session.id,
            session.role,
            "ANALYSIS_STARTED",
            "analysis_run",
            analysis_run.id,
            {"message": "Graph 2 and Graph 3 analysis started.", "graph1_run_id": run_id},
        )
    else:
        response.status_code = 200
    analysis_job = db.query(JobModel).filter(
        JobModel.type == "ANALYSIS_GRAPH2_GRAPH3",
        JobModel.linked_entity == analysis_run.id,
    ).order_by(JobModel.created_at.desc()).first()
    payload = serialize_analysis_run(analysis_run)
    payload["job_id"] = analysis_job.id if analysis_job else None
    return payload


@router.get("/analysis-runs/{analysis_run_id}", response_model=AnalysisRunSchema)
def get_analysis_run(
    analysis_run_id: str,
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    from src.services.analysis_workflow import serialize_analysis_run

    run = db.get(AnalysisRunModel, analysis_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Analysis run not found")
    require_dataset_access(db, session, run.dataset_id)
    return serialize_analysis_run(run)


@router.get("/analysis-runs/{analysis_run_id}/nodes", response_model=list[AnalysisNodeSchema])
def get_analysis_nodes(
    analysis_run_id: str,
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    from src.services.analysis_workflow import list_analysis_nodes

    run = db.get(AnalysisRunModel, analysis_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Analysis run not found")
    require_dataset_access(db, session, run.dataset_id)
    return list_analysis_nodes(db, analysis_run_id)


@router.get("/analysis-runs/{analysis_run_id}/result")
def get_analysis_result(
    analysis_run_id: str,
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    from src.services.analysis_workflow import build_analysis_result

    run = db.get(AnalysisRunModel, analysis_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Analysis run not found")
    require_dataset_access(db, session, run.dataset_id)
    return build_analysis_result(db, run)


@router.get("/analysis-runs/{analysis_run_id}/report")
def get_analysis_report(
    analysis_run_id: str,
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """Return the report only after the same dataset authorization check."""
    run = db.get(AnalysisRunModel, analysis_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Analysis run not found")
    require_dataset_access(db, session, run.dataset_id)
    artifact = db.query(GovernedArtifactModel).filter_by(
        run_id=run.id,
        artifact_type="STEWARD_REPORT_MARKDOWN",
    ).first()
    return {
        "analysis_run_id": run.id,
        "dataset_id": run.dataset_id,
        "dataset_version_id": run.dataset_version_id,
        "artifact": {
            "id": artifact.id,
            "artifact_type": artifact.artifact_type,
            "storage_locator": artifact.storage_locator,
            "checksum": artifact.checksum,
        } if artifact else None,
        "markdown": run.report_markdown or "",
        "status": "REGISTERED" if artifact else "NOT_AVAILABLE",
    }


@router.get("/analysis-runs/{analysis_run_id}/stream")
async def stream_analysis_run(
    analysis_run_id: str,
    request: Request,
    after_sequence: int = Query(0, ge=0),
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    from src.services.analysis_workflow import list_analysis_nodes, serialize_analysis_run

    run = db.get(AnalysisRunModel, analysis_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Analysis run not found")
    require_dataset_access(db, session, run.dataset_id)
    # Do not pin one pool connection for the lifetime of the SSE stream.
    db.close()

    async def events():
        last_signature = ""
        last_sequence = after_sequence
        while not await request.is_disconnected():
            with Session(get_engine()) as event_db:
                current = event_db.get(AnalysisRunModel, analysis_run_id)
                if not current:
                    yield "event: error\ndata: {\"message\":\"Run removed\"}\n\n"
                    return
                nodes = list_analysis_nodes(event_db, analysis_run_id)
                max_sequence = max((node["sequence"] for node in nodes), default=0)
                signature = f"{current.status}:{current.current_node}:{current.updated_at.isoformat()}:{max_sequence}"
                if signature != last_signature and max_sequence >= last_sequence:
                    payload = {
                        "run": serialize_analysis_run(current),
                        "nodes": nodes,
                        "sequence": max_sequence,
                    }
                    yield f"id: {max_sequence}\nevent: snapshot\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    last_signature, last_sequence = signature, max_sequence
                if current.status in {"COMPLETED", "PARTIAL", "FAILED"}:
                    return
            yield ": heartbeat\n\n"
            await asyncio.sleep(0.75)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/datasets/{id}", status_code=204)
def delete_dataset(
    id: str,
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """DELETE /api/v1/datasets/{id} - Deletes a registered dataset and its metadata."""
    dataset = db.query(DatasetModel).filter(DatasetModel.id == id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    require_dataset_access(db, session, id, manage=True)

    from src.models.database import (
        AnomalyFeedbackModel,
        AnomalyHypothesisModel,
        AnomalyRunModel,
        AnomalySignalModel,
        ColumnProfileModel,
        DatasetAccessModel,
        DqResultModel,
        DqRunModel,
        JobModel,
        ProfileModel,
        RuleConfigurationModel,
        RuleProposalModel,
        RulesetVersionModel,
        RuleVersionModel,
        SemanticContractModel,
        SourceRowModel,
        WorkflowArtifactModel,
        WorkflowRunModel,
    )

    try:
        # 1. DQ Results & Runs (referencing dq_runs.id)
        dq_runs = db.query(DqRunModel.id).filter(DqRunModel.dataset_id == id).all()
        dq_run_ids = [r[0] for r in dq_runs]
        if dq_run_ids:
            anom_runs = db.query(AnomalyRunModel.id).filter(AnomalyRunModel.execution_run_id.in_(dq_run_ids)).all()
            anom_ids = [a[0] for a in anom_runs]
            if anom_ids:
                db.query(AnomalyFeedbackModel).filter(AnomalyFeedbackModel.anomaly_run_id.in_(anom_ids)).delete(synchronize_session=False)
                db.query(AnomalyHypothesisModel).filter(AnomalyHypothesisModel.anomaly_run_id.in_(anom_ids)).delete(synchronize_session=False)
                db.query(AnomalySignalModel).filter(AnomalySignalModel.anomaly_run_id.in_(anom_ids)).delete(synchronize_session=False)
                db.query(AnomalyRunModel).filter(AnomalyRunModel.id.in_(anom_ids)).delete(synchronize_session=False)
            db.query(DqResultModel).filter(DqResultModel.run_id.in_(dq_run_ids)).delete(synchronize_session=False)
            db.query(DqRunModel).filter(DqRunModel.dataset_id == id).delete(synchronize_session=False)

        # 2. Rule versions, configurations, proposals
        proposals = db.query(RuleProposalModel.id).filter(RuleProposalModel.dataset_id == id).all()
        proposal_ids = [p[0] for p in proposals]
        if proposal_ids:
            db.query(RuleConfigurationModel).filter(RuleConfigurationModel.rule_proposal_id.in_(proposal_ids)).delete(synchronize_session=False)
            db.query(RuleVersionModel).filter(RuleVersionModel.rule_proposal_id.in_(proposal_ids)).delete(synchronize_session=False)
            db.query(RuleProposalModel).filter(RuleProposalModel.dataset_id == id).delete(synchronize_session=False)

        # 3. Ruleset versions & Semantic contracts
        db.query(RulesetVersionModel).filter(RulesetVersionModel.dataset_id == id).delete(synchronize_session=False)
        db.query(SemanticContractModel).filter(SemanticContractModel.dataset_id == id).delete(synchronize_session=False)

        # 4. Workflow artifacts & runs
        workflow_runs = db.query(WorkflowRunModel.id).filter(WorkflowRunModel.dataset_id == id).all()
        wf_ids = [w[0] for w in workflow_runs]
        if wf_ids:
            db.query(WorkflowArtifactModel).filter(WorkflowArtifactModel.workflow_run_id.in_(wf_ids)).delete(synchronize_session=False)
            db.query(WorkflowRunModel).filter(WorkflowRunModel.dataset_id == id).delete(synchronize_session=False)

        graph1_run_ids = [row[0] for row in db.query(Graph1RunModel.id).filter(Graph1RunModel.dataset_id == id).all()]
        if graph1_run_ids:
            analysis_run_ids = [
                row[0]
                for row in db.query(AnalysisRunModel.id).filter(AnalysisRunModel.graph1_run_id.in_(graph1_run_ids)).all()
            ]
            if analysis_run_ids:
                db.query(AnalysisNodeExecutionModel).filter(
                    AnalysisNodeExecutionModel.run_id.in_(analysis_run_ids)
                ).delete(synchronize_session=False)
                db.query(AnalysisRunModel).filter(AnalysisRunModel.id.in_(analysis_run_ids)).delete(synchronize_session=False)
            db.query(Graph1NodeExecutionModel).filter(Graph1NodeExecutionModel.run_id.in_(graph1_run_ids)).delete(synchronize_session=False)
            db.query(Graph1RunModel).filter(Graph1RunModel.id.in_(graph1_run_ids)).delete(synchronize_session=False)

        # 6. Column Profiles & Profiles
        profiles = db.query(ProfileModel.dataset_id).filter(ProfileModel.dataset_id == id).all()
        if profiles:
            db.query(ColumnProfileModel).filter(ColumnProfileModel.profile_dataset_id == id).delete(synchronize_session=False)
            db.query(ProfileModel).filter(ProfileModel.dataset_id == id).delete(synchronize_session=False)

        # 7. Source rows, Dataset Access, Jobs
        db.query(SourceRowModel).filter(SourceRowModel.dataset_id == id).delete(synchronize_session=False)
        db.query(DatasetAccessModel).filter(DatasetAccessModel.dataset_id == id).delete(synchronize_session=False)
        db.query(JobModel).filter(JobModel.linked_entity == id).delete(synchronize_session=False)

        # 8. Delete dataset
        db.delete(dataset)
        db.commit()
    except Exception:
        db.rollback()
        # Fallback raw query if needed
        db.execute(text("DELETE FROM datasets WHERE id = :id"), {"id": id})
        db.commit()

    add_audit_event(
        db,
        session_id=session.id,
        actor_role=session.role,
        action_code="DATASET_DELETED",
        entity_type="dataset",
        entity_id=id,
        detail={"dataset_name": dataset.name},
    )

    for suffix in (".csv", ".parquet"):
        p = Path(get_settings().upload_dir) / f"{id}{suffix}"
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass

    return Response(status_code=204)


@router.post("/datasets/{id}/ingestions", status_code=202, response_model=CreateJobResponse)
def start_ingestion(
    id: str,
    background_tasks: BackgroundTasks,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """Legacy taxi/dashboard ingestion endpoint.

    Versioned arbitrary datasets use the durable ``INGEST_PROFILE`` reservation
    created by ``/workspaces/{workspace_id}/datasets/import`` instead.
    """
    dataset = db.query(DatasetModel).filter(DatasetModel.id == id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    require_dataset_access(db, session, id, manage=True)
    if dataset.manifest_version == "versioned-v1":
        raise HTTPException(status_code=409, detail={"code": "VERSIONED_PROFILE_REQUIRED", "message": "Prepare a workflow with an explicit dataset version to create a fresh profile"})

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
        created_at=utc_now(),
        updated_at=utc_now(),
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
    id: str, session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])), db: Session = Depends(get_db),
    dataset_version_id: str | None = Query(None), profile_run_id: str | None = Query(None),
):
    """
    GET /api/v1/datasets/{id}/profile - Returns completed profiling details. Returns 404 until complete.
    """
    dataset = db.query(DatasetModel).filter(DatasetModel.id == id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    require_dataset_access(db, session, id)

    profile = db.query(ProfileModel).filter(ProfileModel.dataset_id == id).first()
    if profile and dataset.status == "PROFILE_READY" and dataset.manifest_version != "versioned-v1" and not dataset_version_id and not profile_run_id:
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

    # Canonical versioned profiles are immutable snapshots rather than legacy
    # ProfileModel rows. Adapt the aggregate snapshot to the existing
    # dashboard contract so the UI does not make a second, domain-specific
    # profiling request after a successful versioned import.
    from src.services.source_binding import dataset_source_version
    try:
        latest_version = dataset_source_version(db, id, dataset_version_id)
    except SourceIntegrityError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    version_profile = (
        db.query(ProfileRunSnapshotModel)
        .filter_by(dataset_id=id, dataset_version_id=latest_version.id, status="COMPLETED")
        .filter(ProfileRunSnapshotModel.id == profile_run_id if profile_run_id else True)
        .order_by(ProfileRunSnapshotModel.completed_at.desc())
        .first()
        if latest_version
        else None
    )
    if not version_profile or dataset.status != "PROFILE_READY":
        raise HTTPException(status_code=404, detail="Profile not generated or dataset not fully profiled yet")

    metrics = json.loads(version_profile.metrics_json or "{}")
    version_columns = metrics.get("columns") or json.loads(version_profile.schema_json or "[]")
    columns_list = [
        ColumnProfileSchema(
            name=str(column.get("name")),
            data_type=str(column.get("logical_type") or column.get("physical_type") or "string"),
            null_rate=float(column.get("null_rate") or 0.0),
            distinct_count=int(column.get("distinct_count") or 0),
            non_null_count=int(column.get("non_null_count") or 0),
            quantiles={},
            full_distinct_count=int(column.get("distinct_count") or 0),
            uniqueness_rate=float(column.get("uniqueness_rate") or 0.0),
            is_unique_full_table=column.get("is_unique_full_table"),
            sample_value="Aggregate profile only",
        )
        for column in version_columns
        if isinstance(column, dict) and column.get("name")
    ]
    evidence_keys = ["profile.row_count", "profile.completeness_score", "profile.validity_score", "profile.duplicate_rate"]
    evidence_keys.extend(f"profile.column.{column.name}.null_rate" for column in columns_list)
    return DatasetProfileSchema(
        dataset_id=id,
        row_count=version_profile.row_count,
        completeness_score=float(version_profile.completeness_score or metrics.get("completeness_score") or 0.0),
        validity_score=version_profile.validity_score if version_profile.validity_score is not None else metrics.get("validity_score"),
        duplicate_rate=float(version_profile.duplicate_rate or metrics.get("duplicate_rate") or 0.0),
        columns=columns_list,
        cross_field_metrics=[],
        evidence_keys=evidence_keys,
        generated_at=(version_profile.completed_at or version_profile.created_at).isoformat(),
    )


@router.post("/datasets/{id}/workflows")
def create_workflow(
    id: str,
    fresh: bool = Query(False),
    fresh_profile: bool = Query(False),
    dataset_version_id: str | None = Query(None),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    dataset = db.get(DatasetModel, id)
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    require_dataset_access(db, session, id, manage=True)
    request_run_id = None
    if fresh_profile:
        if not idempotency_key:
            raise HTTPException(status_code=422, detail={"code": "SOURCE_BINDING_REQUIRED", "message": "Fresh profiling requires Idempotency-Key"})
        from src.services.source_binding import dataset_source_version
        try:
            dataset_version_id = dataset_source_version(db, id, dataset_version_id).id
        except SourceIntegrityError as exc:
            raise HTTPException(status_code=409, detail={"code": "SOURCE_BINDING_INVALID", "message": str(exc)}) from exc
        import hashlib
        request_run_id = "workflow-" + hashlib.sha256(f"{session.id}:{id}:{idempotency_key}".encode()).hexdigest()[:32]
    try:
        run = get_or_create_run(db, dataset, force_new=fresh, dataset_version_id=dataset_version_id, fresh_profile=fresh_profile, request_run_id=request_run_id)
    except IntegrityError:
        db.rollback()
        if not request_run_id:
            raise
        run = db.get(WorkflowRunModel, request_run_id)
        if not run or run.dataset_id != id:
            raise
        from src.services.source_binding import workflow_binding
        existing_binding = workflow_binding(db, run, require_profile=False)
        if not existing_binding or existing_binding["dataset_version_id"] != dataset_version_id:
            raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_CONFLICT", "message": "Key belongs to another dataset version"})
    except (SourceIntegrityError, WorkflowError) as exc:
        raise HTTPException(status_code=409, detail={"code": "SOURCE_BINDING_INVALID", "message": str(exc)}) from exc
    db.commit()
    return serialize_run(run)


@router.get("/datasets/{id}/workflows/latest")
def get_latest_workflow(
    id: str,
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """Restore the latest persisted workflow without creating a new run."""
    dataset = db.get(DatasetModel, id)
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    require_dataset_access(db, session, id)
    run = (
        db.query(WorkflowRunModel)
        .filter_by(dataset_id=id)
        .order_by(WorkflowRunModel.created_at.desc())
        .first()
    )
    return serialize_run(run) if run else None


@router.get("/workflows/{workflow_run_id}")
def get_workflow(
    workflow_run_id: str,
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    run = db.get(WorkflowRunModel, workflow_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    require_dataset_access(db, session, run.dataset_id)
    return serialize_run(run)


@router.get("/workflows/{workflow_run_id}/artifacts")
def list_workflow_artifacts(
    workflow_run_id: str,
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    run = db.get(WorkflowRunModel, workflow_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    require_dataset_access(db, session, run.dataset_id)
    artifacts = (
        db.query(WorkflowArtifactModel)
        .filter_by(workflow_run_id=run.id)
        .order_by(WorkflowArtifactModel.created_at)
        .all()
    )
    return [serialize_artifact(artifact) for artifact in artifacts]


@router.post("/workflows/{workflow_run_id}/steps/{step}", response_model=CreateJobResponse)
def run_workflow_step(
    workflow_run_id: str,
    step: str,
    background_tasks: BackgroundTasks,
    dataset_id: str | None = Query(None),
    dataset_version_id: str | None = Query(None),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    run = db.get(WorkflowRunModel, workflow_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    require_dataset_access(db, session, run.dataset_id, manage=True)
    from src.services.source_binding import workflow_binding
    try:
        binding = workflow_binding(db, run, require_profile=step != "UPLOAD_PROFILE")
        if dataset_id and dataset_id != run.dataset_id:
            raise SourceIntegrityError("Requested dataset does not match the workflow")
        if dataset_version_id and (not binding or binding["dataset_version_id"] != dataset_version_id):
            raise SourceIntegrityError("Requested version does not match the workflow")
    except SourceIntegrityError as exc:
        raise HTTPException(status_code=409, detail={"code": "SOURCE_BINDING_INVALID", "message": str(exc)}) from exc
    workflow_job_type = (
        "WORKFLOW_PROFILE"
        if step == "UPLOAD_PROFILE"
        else
        "UNDERSTAND_DATA"
        if step == "UNDERSTAND_DATA"
        else "WORKFLOW_PROPOSE_RULES"
        if step == "PROPOSE_RULES"
        else "PROPOSE_RULES"
        if step in {"PROPOSE_RULES", "PUBLISH_RULESET"}
        # These two stages can take longer than an HTTP request/container
        # lifecycle. Keep their durable job type distinct from the legacy
        # compatibility handlers so the worker can execute workflow-owned
        # state with Cloud Run Jobs.
        else "WORKFLOW_RUN_CHECKS"
        if step == "RUN_CHECKS"
        else "WORKFLOW_ANALYZE_REPORT"
        if step == "ANALYZE_REPORT"
        else "PROPOSE_RULES"
    )
    collision = verify_idempotency(db, idempotency_key)
    if collision:
        existing = db.get(JobModel, collision)
        if not existing or existing.linked_entity != run.id or existing.type != workflow_job_type:
            raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_CONFLICT", "message": "Key belongs to another workflow or step"})
        return CreateJobResponse(job_id=collision, status=existing.status)
    active_stage_types = {
        "RUN_CHECKS": {"RUN_DQ", "WORKFLOW_RUN_CHECKS"},
        "ANALYZE_REPORT": {"ANALYSIS_GRAPH2_GRAPH3", "WORKFLOW_ANALYZE_REPORT"},
    }.get(step, {workflow_job_type})
    active_stage_job = (
        db.query(JobModel)
        .filter(
            JobModel.correlation_id == run.id,
            JobModel.type.in_(active_stage_types),
            JobModel.status.in_(["PENDING", "RUNNING"]),
        )
        .first()
    )
    if active_stage_job:
        # A pre-worker revision could leave a background task RUNNING without
        # ever creating a worker lease. Do not let that historical record brick
        # the stage forever: after a conservative grace period, make it
        # retryable and let the normal dispatch path create a fresh execution.
        stale_without_worker_lease = (
            step in {"RUN_CHECKS", "ANALYZE_REPORT"}
            and not active_stage_job.lease_expires_at
            and active_stage_job.updated_at < utc_now() - timedelta(minutes=10)
        )
        if stale_without_worker_lease:
            active_stage_job.status = "FAILED_RETRYABLE"
            active_stage_job.error = "Previous workflow execution did not report back; retrying is safe."
            active_stage_job.message = "Previous workflow execution expired"
            steps = json.loads(run.steps_json or "[]")
            current = next((item for item in steps if item.get("key") == step), None)
            if current and current.get("status") == "RUNNING":
                current["status"] = "FAILED"
                run.status = "ACTIVE"
                run.steps_json = json.dumps(steps, ensure_ascii=False)
            db.commit()
        else:
            raise HTTPException(
                status_code=409,
                detail={"code": "CONFLICT", "message": "This workflow stage already has an active execution"},
            )
    job = JobModel(
        id=str(uuid.uuid4()),
        type=workflow_job_type,
        status="PENDING",
        progress=0.0,
        message=f"Running {step}",
        idempotency_key=idempotency_key or "",
        linked_entity=run.id,
        correlation_id=run.id,
        attempt_count=1,
    )
    db.add(job)
    queued_job_id, queued_workflow_id = job.id, run.id
    try:
        if step == "RUN_CHECKS":
            queue_check_run(db, run, job)
            # queue_check_run also serves the legacy /dq compatibility path and
            # writes the old RUN_DQ envelope. Restore the workflow envelope
            # before committing so the worker can reload this exact workflow
            # and its child DQ run by job_id.
            job.type = workflow_job_type
            job.linked_entity = run.id
            job.message = "Queued workflow quality checks"
            db.commit()
            background_tasks.add_task(dispatch_persisted_job, queued_job_id)
            return CreateJobResponse(job_id=queued_job_id, status="PENDING")
        if step == "ANALYZE_REPORT":
            if run.current_step != "ANALYZE_REPORT":
                raise WorkflowError("Complete Graph 2 before starting Graph 3 analysis.")
            db.commit()
            background_tasks.add_task(dispatch_persisted_job, queued_job_id)
            return CreateJobResponse(job_id=queued_job_id, status="PENDING")
        if step == "PROPOSE_RULES":
            db.commit()
            background_tasks.add_task(dispatch_persisted_job, queued_job_id)
            return CreateJobResponse(job_id=queued_job_id, status="PENDING")
        steps = json.loads(run.steps_json or "[]")
        current = next((item for item in steps if item.get("key") == step), None)
        # WAITING_APPROVAL is a re-run, not a skip: the stage already produced an
        # artifact and the Steward is asking for a new one. Excluding it meant
        # "regenerate rules" answered 409 for every dataset that had ever had
        # rules proposed. LOCKED stays blocked, so ordering is still enforced.
        if not current or current.get("status") not in {"READY", "FAILED", "COMPLETED", "WAITING_APPROVAL"}:
            raise WorkflowError("This workflow step is not ready to run.")
        current["status"] = "RUNNING"
        run.steps_json = json.dumps(steps, ensure_ascii=False)
        job.status, job.progress = "PENDING", 0.0
        db.commit()
        if step == "UPLOAD_PROFILE":
            background_tasks.add_task(dispatch_persisted_job, queued_job_id)
        else:
            background_tasks.add_task(run_workflow_stage_job, queued_workflow_id, step, queued_job_id)
        return CreateJobResponse(job_id=queued_job_id, status="PENDING")
    except WorkflowError as exc:
        job.status, job.error, job.message = "FAILED", str(exc), "Workflow step failed"
        db.commit()
        raise HTTPException(status_code=409, detail={"code": "WORKFLOW_STATE", "message": str(exc)})
    except Exception:
        # A failed flush leaves the SQLAlchemy session unusable until rollback.
        # Persist a terminal job after recovery so clients never poll a stale
        # RUNNING record when a workflow write fails.
        db.rollback()
        failed_job = db.get(JobModel, job.id)
        if not failed_job:
            failed_job = JobModel(
                id=job.id,
                type=job.type,
                status="FAILED",
                progress=0.0,
                message="Workflow step failed",
                error="Workflow execution failed",
                idempotency_key=job.idempotency_key,
                linked_entity=run.dataset_id,
                correlation_id=run.id,
                attempt_count=1,
            )
            db.add(failed_job)
        else:
            failed_job.status = "FAILED"
            failed_job.error = "Workflow execution failed"
            failed_job.message = "Workflow step failed"
        db.commit()
        raise
    return CreateJobResponse(job_id=job.id, status="PENDING")


def authorize_workflow_stream(workflow_run_id: str, request: Request) -> None:
    """Authorize an SSE stream before opening the response body.

    This dependency deliberately owns a short-lived session instead of using
    ``get_session``/``get_db``.  FastAPI keeps yield-based dependencies alive
    until a ``StreamingResponse`` finishes, so attaching the regular database
    dependency here would reserve a Supabase connection for the whole lifetime
    of an EventSource (including idle keep-alive periods).
    """
    with Session(get_engine()) as db:
        session = get_current_session(request, db)
        verify_csrf(request, session)
        enforce_demo_quota(db, request, session)
        enforce_role(session, ["USER", "STEWARD", "ADMIN"])
        run = db.get(WorkflowRunModel, workflow_run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Workflow run not found")
        require_dataset_access(db, session, run.dataset_id)


@router.get("/workflows/{workflow_run_id}/stream")
async def stream_workflow_nodes(
    workflow_run_id: str,
    request: Request,
    _authorized: None = Depends(authorize_workflow_stream),
):
    """SSE: phát output của TỪNG node trong graph theo thời gian thực.

    Client mở EventSource tới endpoint này *đồng thời* với khi trigger một step
    (vd. POST /workflows/{id}/steps/ANALYZE_REPORT). Graph chạy nền publish event
    theo ``workflow_run_id`` và được fan-out ở đây. GET nên CSRF được bỏ qua;
    xác thực bằng session cookie như mọi GET khác.
    """
    from src.services.node_event_stream import broker

    async def event_gen():
        sub, queue, backlog = broker.subscribe(workflow_run_id)
        try:
            yield ": connected\n\n"  # SSE comment to open the stream promptly
            for event in backlog:
                yield f"event: {event.get('type', 'node')}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("type") == "done":
                    return
            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    yield ": keep-alive\n\n"  # heartbeat to defeat idle proxies
                    continue
                yield f"event: {event.get('type', 'node')}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("type") == "done":
                    return
        finally:
            broker.unsubscribe(workflow_run_id, sub)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.post("/workflows/{workflow_run_id}/rewind")
def rewind_workflow_stage(
    workflow_run_id: str,
    body: WorkflowRewindInput,
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    run = db.get(WorkflowRunModel, workflow_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    require_dataset_access(db, session, run.dataset_id, manage=True)
    try:
        rewind_workflow(db, run, body.target_step)
        db.commit()
    except WorkflowError as exc:
        raise HTTPException(status_code=409, detail={"code": "WORKFLOW_STATE", "message": str(exc)})
    return serialize_run(run)


@router.post("/workflow-artifacts/{artifact_id}/review")
def review_workflow_artifact(
    artifact_id: str,
    body: ArtifactReviewInput,
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    artifact = db.get(WorkflowArtifactModel, artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Workflow artifact not found")
    run = db.get(WorkflowRunModel, artifact.workflow_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    require_dataset_access(db, session, run.dataset_id, manage=True)
    if body.action != "approve" or artifact.artifact_type not in {"SEMANTIC_CONTRACT", "RULE_SET"}:
        raise HTTPException(status_code=422, detail="Only the current semantic contract or rule set can be confirmed here")
    if artifact.artifact_type == "SEMANTIC_CONTRACT":
        if run.current_step != "UNDERSTAND_DATA" or artifact.stale:
            raise HTTPException(
                status_code=409,
                detail={"code": "WORKFLOW_STATE", "message": "The semantic contract is not current."},
            )
        artifact.status = "APPROVED"
        try:
            navigate_forward(run)
            add_audit_event(
                db,
                session_id=session.id,
                actor_role=session.role,
                action_code="SEMANTIC_CONTRACT_APPROVED",
                entity_type="workflow_artifact",
                entity_id=artifact.id,
                detail={"workflow_run_id": run.id},
            )
            db.commit()
        except WorkflowError as exc:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail={"code": "WORKFLOW_STATE", "message": str(exc)},
            )
        return serialize_artifact(artifact)
    try:
        reviewed = complete_rule_review(db, run)
        db.commit()
    except WorkflowError as exc:
        raise HTTPException(status_code=409, detail={"code": "WORKFLOW_STATE", "message": str(exc)})
    return serialize_artifact(reviewed)


@router.post("/workflows/{workflow_run_id}/semantic-contract/confirm")
def confirm_workflow_contract(workflow_run_id: str, body: SemanticContractConfirmInput, session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])), db: Session = Depends(get_db)):
    run = db.get(WorkflowRunModel, workflow_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    require_dataset_access(db, session, run.dataset_id, manage=True)
    try:
        artifact = confirm_workflow_semantic_contract(db, run, artifact_id=body.artifact_id, expected_version=body.expected_version, contract=body.contract, review_note=body.review_note)
        db.commit()
    except WorkflowError as exc:
        raise HTTPException(status_code=409, detail={"code": "WORKFLOW_STATE", "message": str(exc)})
    return {"workflow": serialize_run(run), "artifact": serialize_artifact(artifact)}


@router.post("/workflows/{workflow_run_id}/advance")
def advance_workflow_stage(
    workflow_run_id: str,
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    run = db.get(WorkflowRunModel, workflow_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    require_dataset_access(db, session, run.dataset_id, manage=True)
    try:
        navigate_forward(run)
        db.commit()
    except WorkflowError as exc:
        raise HTTPException(status_code=409, detail={"code": "WORKFLOW_STATE", "message": str(exc)})
    return serialize_run(run)


@router.get("/datasets/{id}/data-dictionary")
def get_dataset_data_dictionary(
    id: str,
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """GET /api/v1/datasets/{id}/data-dictionary - Returns the supplied dictionary.

    Absence is the normal case, not an error: it is what tells Graph 1A to infer
    the dictionary instead. The route answers 200 with ``null`` so the UI can
    render "the agent will generate this" without treating it as a failure.
    """
    dataset = db.query(DatasetModel).filter(DatasetModel.id == id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    require_dataset_access(db, session, id)

    record = get_data_dictionary(db, id)
    return serialize_data_dictionary(record) if record else None


@router.post("/datasets/{id}/data-dictionary", status_code=201)
async def upload_dataset_data_dictionary(
    id: str,
    file: UploadFile = File(...),
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """POST /api/v1/datasets/{id}/data-dictionary - Stores a Steward's dictionary.

    Uploading replaces any earlier upload for the dataset, so re-uploading a
    corrected sheet is the fix for a bad one rather than an error.
    """
    dataset = db.query(DatasetModel).filter(DatasetModel.id == id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    require_dataset_access(db, session, id)

    payload = await file.read()
    if len(payload) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail={"code": "DICTIONARY_TOO_LARGE", "message": "The dictionary exceeds the 5 MB limit."})
    try:
        document = parse_data_dictionary(payload, file.filename or "dictionary.csv", dataset.name or id)
    except DataDictionaryError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_DATA_DICTIONARY", "message": str(exc)}) from exc

    from src.services.source_binding import dataset_source_version
    latest_version = dataset_source_version(db, id) if dataset.manifest_version == "versioned-v1" else None
    record = save_data_dictionary(
        db,
        dataset_id=id,
        dataset_version_id=latest_version.id if latest_version else None,
        payload=document,
        source_filename=file.filename,
        uploaded_by=session.username,
    )
    return serialize_data_dictionary(record)


@router.delete("/datasets/{id}/data-dictionary", status_code=204)
def delete_dataset_data_dictionary(
    id: str,
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """DELETE /api/v1/datasets/{id}/data-dictionary - Hands the job back to the agent."""
    dataset = db.query(DatasetModel).filter(DatasetModel.id == id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    require_dataset_access(db, session, id)
    if not delete_data_dictionary(db, id):
        raise HTTPException(status_code=404, detail="No supplied data dictionary for this dataset")
    return None


@router.get("/datasets/{id}/semantic-contract")
def get_semantic_contract(
    id: str, session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])), db: Session = Depends(get_db)
):
    """
    GET /api/v1/datasets/{id}/semantic-contract - Returns the latest semantic contract draft or confirmed version.
    """
    dataset = db.query(DatasetModel).filter(DatasetModel.id == id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    require_dataset_access(db, session, id)

    run = db.query(WorkflowRunModel).filter_by(dataset_id=id, status="ACTIVE").order_by(WorkflowRunModel.updated_at.desc()).first()
    if not run:
        raise HTTPException(status_code=404, detail="No workflow run found for this dataset")
    artifact = db.query(WorkflowArtifactModel).filter_by(workflow_run_id=run.id, step_key="UNDERSTAND_DATA", artifact_type="SEMANTIC_CONTRACT", stale=False).order_by(WorkflowArtifactModel.version.desc()).first()
    if not artifact:
        raise HTTPException(status_code=404, detail="Semantic contract not generated yet")
    return json.loads(artifact.payload_json or "{}")


@router.post("/datasets/{id}/semantic-contract/confirm")
def confirm_semantic_contract(
    id: str,
    body: dict,
    background_tasks: BackgroundTasks,
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """
    POST /api/v1/datasets/{id}/semantic-contract/confirm - Allows Steward to confirm/update the semantic contract.
    Resumes rule proposal graph execution in background.
    """
    dataset = db.query(DatasetModel).filter(DatasetModel.id == id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    require_dataset_access(db, session, id, manage=True)

    run = db.query(WorkflowRunModel).filter_by(dataset_id=id, status="ACTIVE").order_by(WorkflowRunModel.updated_at.desc()).first()
    if not run:
        raise HTTPException(status_code=404, detail="No workflow run found for this dataset")
    artifact = db.query(WorkflowArtifactModel).filter_by(workflow_run_id=run.id, step_key="UNDERSTAND_DATA", artifact_type="SEMANTIC_CONTRACT", stale=False).order_by(WorkflowArtifactModel.version.desc()).first()
    if not artifact:
        raise HTTPException(status_code=404, detail="Semantic contract not generated yet")
    try:
        confirmed = confirm_workflow_semantic_contract(db, run, artifact_id=artifact.id, expected_version=artifact.version, contract=body)
        db.commit()
    except WorkflowError as exc:
        raise HTTPException(status_code=409, detail={"code": "WORKFLOW_STATE", "message": str(exc)})
    return {"message": "Semantic contract confirmed successfully.", "workflow": serialize_run(run), "artifact": serialize_artifact(confirmed)}


@router.get("/datasets/{id}/rows")
def query_dataset_rows(
    id: str,
    dataset_version_id: str | None = Query(None, max_length=64),
    vendor_id: str | None = None,
    payment_type: str | None = None,
    min_distance: float | None = None,
    max_distance: float | None = None,
    quality_status: str = Query("ALL", pattern="^(ALL|VALID|ISSUE)$"),
    # Empty by default: the caller's dataset decides what is sortable, and each
    # branch below falls back on its own when the column does not apply.
    sort_by: str = Query("", min_length=0, max_length=128),
    sort_direction: str = Query("desc", pattern="^(asc|desc)$"),
    filter_column: str | None = Query(None, max_length=128),
    filter_value: str | None = Query(None, max_length=512),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """Query a bounded, allow-listed projection of the registered dataset."""
    from src.services.dashboard_agent_workflow import get_dataset_rule_policy

    dataset = db.query(DatasetModel).filter(DatasetModel.id == id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    # Versioned uploads are served through the workspace authorization and
    # schema-driven source adapter. The legacy taxi projection below remains
    # available only for datasets without a canonical version artifact.
    from src.services.source_binding import dataset_source_version
    try:
        latest_version = dataset_source_version(db, id, dataset_version_id) if dataset.manifest_version == "versioned-v1" else None
    except SourceIntegrityError as exc:
        raise HTTPException(status_code=409, detail={"code": "SOURCE_BINDING_INVALID", "message": str(exc)}) from exc
    governance = db.query(DatasetGovernanceModel).filter_by(dataset_id=id).first() if latest_version else None
    account = db.query(UserAccountModel).filter_by(username=session.username).first() if latest_version else None
    if latest_version and governance and account:
        from src.services.data_access_service import AccessContext, get_data_explorer
        try:
            version_metadata = json.loads(latest_version.source_metadata_json or "{}")
            version_columns = {str(item.get("name")) for item in version_metadata.get("schema", []) if isinstance(item, dict) and item.get("name")}
            explorer = get_data_explorer(
                db,
                AccessContext(user_id=account.id, workspace_id=governance.workspace_id),
                dataset_id=id,
                dataset_version_id=latest_version.id,
                include_rows=True,
                filters={filter_column: filter_value} if filter_column and filter_value is not None else None,
                sort_by=sort_by if sort_by in version_columns else None,
                sort_direction=sort_direction,
                limit=limit,
                offset=offset,
            )
        except Exception as exc:
            from src.services.data_access_service import AccessDeniedError, ResourceNotFoundError
            if isinstance(exc, ResourceNotFoundError):
                raise HTTPException(status_code=404, detail="Dataset not found") from exc
            if isinstance(exc, AccessDeniedError):
                raise HTTPException(status_code=403, detail="Rows access is not granted") from exc
            if isinstance(exc, SourceIntegrityError):
                raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "dataset_id": id,
            "dataset_version_id": latest_version.id,
            "total": explorer["total_rows"],
            "limit": limit,
            "offset": offset,
            "schema": explorer["dataset_version"].get("schema", []),
            "rows": explorer["rows"],
        }

    # Only the explicitly legacy projection uses the old DatasetAccess table.
    # A shared versioned dataset may be authorized solely through workspace
    # grants and must not be blocked by this compatibility check.
    require_dataset_access(db, session, id)

    policy = get_dataset_rule_policy(id)
    allowed_payments = policy.governed_value_sets.get("payment_type", []) if policy else []
    uploaded_path = None
    for suffix in (".parquet", ".csv"):
        candidate = Path("data/uploads") / f"{id}{suffix}"
        if candidate.exists():
            uploaded_path = candidate
            break

    if uploaded_path:
        import pandas as pd
        df = pd.read_parquet(uploaded_path) if uploaded_path.suffix.lower() == ".parquet" else pd.read_csv(uploaded_path)
        schema = canonical_schema_manifest(df)
        columns = [item["name"] for item in schema]
        if filter_column:
            if filter_column not in columns:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "INVALID_DATASET_FILTER", "message": f"Unknown dataset column: {filter_column}"},
                )
            df = df[df[filter_column].astype(str) == str(filter_value or "")]
        if sort_by and sort_by in columns:
            df = df.sort_values(sort_by, ascending=sort_direction == "asc", kind="stable")
        total = len(df)
        if dataset.row_count == 0 and total > 0:
            dataset.row_count = total
            db.commit()

        sub_df = df.iloc[offset : offset + limit]
        def json_value(value: Any) -> Any:
            if value is None:
                return None
            try:
                if bool(pd.isna(value)):
                    return None
            except (TypeError, ValueError):
                pass
            if hasattr(value, "item"):
                value = value.item()
            if hasattr(value, "isoformat"):
                return value.isoformat()
            return value

        rows_list = [
            {column: json_value(row[column]) for column in columns}
            for _, row in sub_df.iterrows()
        ]
        return DatasetRowsResponse(
            dataset_id=id,
            total=total,
            limit=limit,
            offset=offset,
            rows=rows_list,
            schema=schema,
        )

    # The Supabase source adapter is a compatibility path for the original
    # demo dataset only. Generic imports without a local compatibility file
    # must have a canonical version artifact instead of guessing a domain
    # table name such as trips_canonical.
    if dataset.manifest_version not in {"v1", "1.0.0"}:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "VERSIONED_SOURCE_REQUIRED",
                "message": "This dataset has no queryable versioned source artifact.",
            },
        )

    if id != DEMO_TAXI_DATASET_ID:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "VERSIONED_SOURCE_REQUIRED",
                "message": "Only the explicitly configured legacy demo dataset may use the taxi compatibility source.",
            },
        )

    source_url = _supabase_source_url()
    if source_url:
        source_engine = create_supabase_engine(source_url)
        try:
            with source_engine.connect() as connection:
                total, rows = query_supabase_dataset_rows(
                    connection,
                    id,
                    vendor_id=vendor_id,
                    payment_type=payment_type,
                    min_distance=min_distance,
                    max_distance=max_distance,
                    quality_status=quality_status,
                    sort_by=sort_by,
                    sort_direction=sort_direction,
                    limit=limit,
                    offset=offset,
                    allowed_payments=allowed_payments,
                )
        finally:
            source_engine.dispose()
        return DatasetRowsResponse(
            dataset_id=id,
            total=total,
            limit=limit,
            offset=offset,
            rows=[dict(row) for row in rows],
        )

    query = db.query(SourceRowModel).filter(SourceRowModel.dataset_id == id)
    if vendor_id:
        query = query.filter(SourceRowModel.vendor_id == vendor_id)
    if payment_type:
        query = query.filter(SourceRowModel.payment_type == payment_type)
    if min_distance is not None:
        query = query.filter(SourceRowModel.trip_distance >= min_distance)
    if max_distance is not None:
        query = query.filter(SourceRowModel.trip_distance <= max_distance)

    issue_predicate = or_(
        SourceRowModel.vendor_id.is_(None),
        SourceRowModel.trip_distance < 0,
        SourceRowModel.fare_amount < 0,
        (
            SourceRowModel.pickup_at.is_not(None)
            & SourceRowModel.dropoff_at.is_not(None)
            & (SourceRowModel.pickup_at > SourceRowModel.dropoff_at)
        ),
        (SourceRowModel.payment_type.is_not(None) & SourceRowModel.payment_type.notin_(allowed_payments))
        if allowed_payments
        else SourceRowModel.payment_type.is_(None),
    )
    if quality_status == "ISSUE":
        query = query.filter(issue_predicate)
    elif quality_status == "VALID":
        query = query.filter(~issue_predicate)

    total = query.count()
    sort_columns = {
        "pickup_at": SourceRowModel.pickup_at,
        "trip_distance": SourceRowModel.trip_distance,
        "fare_amount": SourceRowModel.fare_amount,
        "total_amount": SourceRowModel.total_amount,
    }
    # An unguarded lookup here raised KeyError -> 500 for any sort column
    # outside this taxi-shaped set, and `sort_by` accepts any string (it even
    # allows the empty one). The versioned and file-backed branches above both
    # fall back when the column is unknown; this one did not.
    sort_column = sort_columns.get(sort_by, SourceRowModel.source_row_id)
    ordering = sort_column.asc() if sort_direction == "asc" else sort_column.desc()
    rows = query.order_by(ordering, SourceRowModel.source_row_id.asc()).offset(offset).limit(limit).all()
    return DatasetRowsResponse(
        dataset_id=id,
        total=total,
        limit=limit,
        offset=offset,
        rows=[
            DatasetRowSchema(
                source_row_id=row.source_row_id,
                vendor_id=row.vendor_id,
                pickup_at=row.pickup_at,
                dropoff_at=row.dropoff_at,
                passenger_count=row.passenger_count,
                trip_distance=row.trip_distance,
                payment_type=row.payment_type,
                fare_amount=row.fare_amount,
                total_amount=row.total_amount,
            ).model_dump()
            for row in rows
        ],
    )


@router.get("/datasets/{id}/dq-runs/latest", response_model=DqRunSchema | None)
def get_latest_dq_run(
    id: str,
    workflow_run_id: str | None = Query(None),
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    require_dataset_access(db, session, id)
    query = db.query(DqRunModel).filter(DqRunModel.dataset_id == id)
    if workflow_run_id:
        query = query.filter(DqRunModel.workflow_run_id == workflow_run_id, DqRunModel.stale.is_(False))
    run = query.order_by(DqRunModel.created_at.desc()).first()
    if not run:
        return None
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


@router.get("/datasets/{id}/quality-trends", response_model=list[QualityTrendPointSchema])
def get_quality_trends(
    id: str,
    limit: int = Query(12, ge=1, le=30),
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    require_dataset_access(db, session, id)
    runs = (
        db.query(DqRunModel)
        .filter(DqRunModel.dataset_id == id, DqRunModel.status == "SUCCEEDED")
        .order_by(DqRunModel.created_at.desc())
        .limit(limit)
        .all()
    )
    points = []
    for run in reversed(runs):
        failure_rate = run.total_failed / run.total_checked if run.total_checked else 0.0
        points.append(
            QualityTrendPointSchema(
                run_id=run.id,
                created_at=run.created_at.isoformat(),
                quality_score=round(max(0.0, 100.0 * (1.0 - failure_rate)), 2),
                failure_rate=round(failure_rate, 6),
                total_checked=run.total_checked,
                total_failed=run.total_failed,
                rule_count=len(json.loads(run.rule_ids)),
            )
        )
    return points


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
    if dataset.status != "PROFILE_READY":
        try:
            from src.services.job_runner import _profile_uploaded_dataset, _uploaded_dataset_path
            path = _uploaded_dataset_path(id)
            if path:
                _profile_uploaded_dataset(db, id, path)
                db.commit()
                dataset = db.query(DatasetModel).filter(DatasetModel.id == id).first()
        except Exception as err:
            db.rollback()
            logger.warning("Auto-profiling dataset %s failed: %s", id, err)
            dataset = db.query(DatasetModel).filter(DatasetModel.id == id).first()

    if dataset:
        dataset.status = "PROFILE_READY"
        db.commit()

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
        created_at=utc_now(),
        updated_at=utc_now(),
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


def _serialize_proposal(p: RuleProposalModel) -> RuleProposalSchema:
    """The single wire shape for a proposal, shared by every route returning one."""
    return RuleProposalSchema(
        id=p.id,
        dataset_id=p.dataset_id,
        workflow_run_id=p.workflow_run_id,
        title=p.title,
        description=p.description,
        severity=p.severity,
        status=p.status,
        rule=RuleSpecSchema(**json.loads(p.rule_spec)),
        evidence_refs=json.loads(p.evidence_refs),
        evidence_summary=p.evidence_summary,
        confidence=p.confidence,
        model_name=p.model_name,
        rule_name=p.rule_name,
        business_rationale=p.business_rationale,
        proposal_basis=p.proposal_basis,
        evidence=json.loads(p.evidence or "{}"),
        parameter_provenance=json.loads(p.parameter_provenance or "[]"),
        assumptions=json.loads(p.assumptions or "[]"),
        confidence_breakdown=json.loads(p.confidence_breakdown or "{}"),
        created_at=p.created_at.isoformat(),
        updated_at=p.updated_at.isoformat(),
    )


@router.get("/rule-proposals", response_model=list[RuleProposalSchema])
def list_proposals(
    dataset_id: str,
    workflow_run_id: str | None = None,
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """
    GET /api/v1/rule-proposals - Returns rule proposals for a dataset.
    """
    require_dataset_access(db, session, dataset_id)
    query = _proposal_scope_query(db, dataset_id, workflow_run_id)
    proposals = query.all()
    return [_serialize_proposal(p) for p in proposals]


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

    if body.workflow_run_id:
        workflow = db.get(WorkflowRunModel, body.workflow_run_id)
        if not workflow or workflow.dataset_id != id:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "WORKFLOW_SCOPE",
                    "message": "The workflow does not belong to this dataset.",
                },
            )

    prop_id = f"manual-{str(uuid.uuid4())[:8]}"
    prop = RuleProposalModel(
        id=prop_id,
        dataset_id=id,
        workflow_run_id=body.workflow_run_id,
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
        rule_name=body.title,
        business_rationale=body.description,
        proposal_basis="POLICY",
        evidence=json.dumps({"source_refs": ["manual"]}),
        parameter_provenance="[]",
        assumptions="[]",
        confidence_breakdown=json.dumps(
            {
                "overall": 1.0,
                "evidence_strength": 1.0,
                "business_support": 1.0,
                "sample_representativeness": 1.0,
                "explanation": "Manually authored by data steward",
            }
        ),
        created_at=utc_now(),
        updated_at=utc_now(),
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
        detail={"manual": True, "workflow_run_id": body.workflow_run_id},
    )

    return _serialize_proposal(prop)


class BulkProposalReviewInput(BaseModel):
    dataset_id: str
    workflow_run_id: str | None = None
    action: str  # approve | reject
    # Default keeps a bulk action from silently overturning decisions the
    # Steward already made one by one; pass false to re-decide everything.
    pending_only: bool = True


def _proposal_scope_query(db: Session, dataset_id: str, workflow_run_id: str | None = None):
    """Return proposals belonging to one explicit review queue.

    Dataset-wide callers retain the legacy scope. Workflow callers must never
    absorb historical or unowned rows from the same dataset.
    """
    query = db.query(RuleProposalModel).filter(RuleProposalModel.dataset_id == dataset_id)
    if workflow_run_id:
        query = query.filter(
            RuleProposalModel.workflow_run_id == workflow_run_id,
            RuleProposalModel.status != "STALE",
        )
    return query


def _apply_proposal_approval(db: Session, prop: RuleProposalModel) -> None:
    """Promote a proposal to APPROVED and make its rule version executable."""
    prop.status = "APPROVED"
    rv_id = f"rv_{prop.id}"
    existing_rv = db.query(RuleVersionModel).filter(RuleVersionModel.id == rv_id).first()
    if not existing_rv:
        db.add(
            RuleVersionModel(
                id=rv_id,
                rule_proposal_id=prop.id,
                dataset_id=prop.dataset_id,
                rule_spec=prop.rule_spec,
                status="APPROVED",
                version=1,
                created_at=utc_now(),
            )
        )
    else:
        existing_rv.status = "APPROVED"
    configuration = (
        db.query(RuleConfigurationModel)
        .filter(RuleConfigurationModel.rule_proposal_id == prop.id)
        .first()
    )
    if not configuration:
        db.add(
            RuleConfigurationModel(
                rule_proposal_id=prop.id, execution_status="ACTIVE", schedule_frequency="MANUAL", timezone="UTC"
            )
        )


def _apply_proposal_rejection(db: Session, prop: RuleProposalModel) -> None:
    """Reject a proposal and withdraw any rule version it had authorised."""
    prop.status = "REJECTED"
    existing_rv = db.query(RuleVersionModel).filter(RuleVersionModel.id == f"rv_{prop.id}").first()
    if existing_rv:
        db.delete(existing_rv)


@router.post("/rule-proposals/bulk-review", response_model=list[RuleProposalSchema])
def bulk_review_proposals(
    body: BulkProposalReviewInput,
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """POST /api/v1/rule-proposals/bulk-review - Decide a dataset's proposals at once.

    Reviewing 40-odd rules one request at a time is both slow and non-atomic:
    a failure halfway leaves the queue in a state nobody chose. This applies the
    whole decision in one transaction and returns the resulting proposals.
    """
    if body.action not in {"approve", "reject"}:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_BULK_ACTION", "message": "action must be 'approve' or 'reject'."},
        )
    dataset = db.query(DatasetModel).filter(DatasetModel.id == body.dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    require_dataset_access(db, session, body.dataset_id, manage=True)

    query = _proposal_scope_query(db, body.dataset_id, body.workflow_run_id)
    if body.pending_only:
        query = query.filter(RuleProposalModel.status.in_(("PROPOSED", "EDITED")))
    targets = query.all()

    for prop in targets:
        if body.action == "approve":
            _apply_proposal_approval(db, prop)
        else:
            _apply_proposal_rejection(db, prop)
    db.commit()

    add_audit_event(
        db,
        session_id=session.id,
        actor_role=session.role,
        action_code="PROPOSAL_BULK_APPROVED" if body.action == "approve" else "PROPOSAL_BULK_REJECTED",
        entity_type="dataset",
        entity_id=body.dataset_id,
        detail={
            "action": body.action,
            "count": len(targets),
            "pending_only": body.pending_only,
            "workflow_run_id": body.workflow_run_id,
        },
    )

    return [
        _serialize_proposal(prop)
        for prop in _proposal_scope_query(db, body.dataset_id, body.workflow_run_id).all()
    ]


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

    if body.workflow_run_id:
        workflow = db.get(WorkflowRunModel, body.workflow_run_id)
        if (
            not workflow
            or workflow.dataset_id != prop.dataset_id
            or prop.workflow_run_id != body.workflow_run_id
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "WORKFLOW_SCOPE",
                    "message": "The proposal does not belong to this workflow.",
                },
            )

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
                created_at=utc_now(),
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
            detail={"action": "approve", "workflow_run_id": body.workflow_run_id},
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
            detail={"action": "reject", "workflow_run_id": body.workflow_run_id},
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
            existing_rv.created_at = utc_now()
        else:
            rv = RuleVersionModel(
                id=rv_id,
                rule_proposal_id=prop.id,
                dataset_id=prop.dataset_id,
                rule_spec=prop.rule_spec,
                status="APPROVED",
                version=1,
                created_at=utc_now(),
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
            detail={"action": "edit", "workflow_run_id": body.workflow_run_id},
        )

    else:
        raise HTTPException(status_code=400, detail="Invalid review action")

    prop.updated_at = utc_now()
    db.commit()

    return _serialize_proposal(prop)


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

    if proposal.workflow_run_id:
        # A workflow decision is an auditable state transition, not a physical
        # erase.  The current batch view hides stale rows while the artifact
        # snapshot remains available after navigating backwards.
        proposal.status = "STALE"
    else:
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
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(job)
    db.flush()

    dq_run = DqRunModel(
        id=run_id,
        job_id=job_id,
        dataset_id=dataset_id,
        rule_ids=json.dumps(normalized_ids),
        status="PENDING",
        total_failed=0,
        total_checked=0,
        created_at=utc_now(),
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
            violation_rate=r.violation_rate,
            error_message=r.error_message,
        )
        for r in results
    ]


@router.get("/dq-runs/{id}/anomalies", response_model=list[DqAnomalySchema])
def get_dq_anomalies(
    id: str,
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """Return aggregate anomaly signals without exposing raw dataset values."""
    from src.services.dashboard_anomaly import detect_dashboard_anomalies

    run = db.query(DqRunModel).filter(DqRunModel.id == id).first()
    if not run:
        raise HTTPException(status_code=404, detail="DQ run not found")
    require_dataset_access(db, session, run.dataset_id)
    return [DqAnomalySchema(**anomaly.__dict__) for anomaly in detect_dashboard_anomalies(db, id)]


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
        access.granted_at = utc_now()
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
    logs = (
        db.query(AuditEventModel)
        .filter(AuditEventModel.entity_type != "demo_quota")
        .order_by(AuditEventModel.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
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
# Graph observability
#
# The wizard shows a step running and then an artifact appearing.  Everything in
# between -- which nodes ran, in what order, how long each took, which one failed
# -- had no route out of the backend.  These four endpoints are that route.
# ---------------------------------------------------------------------------
@router.get("/graph/catalog", tags=["Graph"])
def get_graph_catalog(
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
):
    """Static topology of every graph, so the UI can draw one before it runs."""
    from src.agents.graph_catalog import get_catalog

    return get_catalog()


@router.get("/graph/node-runs", tags=["Graph"])
def list_graph_node_runs(
    workflow_run_id: str | None = None,
    dataset_id: str | None = None,
    dq_run_id: str | None = None,
    anomaly_run_id: str | None = None,
    graph_key: str | None = None,
    graph_run_id: str | None = None,
    limit: int = 200,
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """Node executions, newest run first, filtered by whichever context is known."""
    from src.services.node_telemetry import recover_stale_node_runs, serialize_node_run

    recover_stale_node_runs(db, workflow_run_id=workflow_run_id, graph_key=graph_key)

    query = db.query(GraphNodeRunModel)
    if workflow_run_id:
        query = query.filter(GraphNodeRunModel.workflow_run_id == workflow_run_id)
    if dataset_id:
        query = query.filter(GraphNodeRunModel.dataset_id == dataset_id)
    if dq_run_id:
        query = query.filter(GraphNodeRunModel.dq_run_id == dq_run_id)
    if anomaly_run_id:
        query = query.filter(GraphNodeRunModel.anomaly_run_id == anomaly_run_id)
    if graph_key:
        query = query.filter(GraphNodeRunModel.graph_key == graph_key)
    if graph_run_id:
        query = query.filter(GraphNodeRunModel.graph_run_id == graph_run_id)

    rows = (
        query.order_by(GraphNodeRunModel.started_at.desc(), GraphNodeRunModel.sequence.desc())
        .limit(max(1, min(limit, 1000)))
        .all()
    )
    return [serialize_node_run(row) for row in rows]


@router.get("/graph/node-runs/{node_run_id}", tags=["Graph"])
def get_graph_node_run(
    node_run_id: str,
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """One node execution including its redacted input/output summaries."""
    from src.services.node_telemetry import serialize_node_run

    row = db.get(GraphNodeRunModel, node_run_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail={"code": "NOT_FOUND", "message": "Node run not found"}
        )
    return serialize_node_run(row, include_payload=True)


@router.get("/dq-runs/{run_id}/steward-report", tags=["Graph"])
def get_steward_report(
    run_id: str,
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """Serve the Markdown report written by ``report_writer_node``.

    Graph 3's last node writes a steward report to ``output/steward_reports/``
    and nothing ever read it back, so the report never reached a user.  Files are
    matched on the execution-run id embedded in the filename; the newest wins.
    """
    settings = get_settings()
    base_dir = getattr(settings, "output_dir", None) or "./output"
    report_dir = Path(base_dir) / "steward_reports"
    matches = sorted(report_dir.glob(f"steward_report_*_{run_id}.md")) if report_dir.exists() else []
    if not matches:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "No steward report has been written for this run"},
        )
    newest = matches[-1]
    return {
        "run_id": run_id,
        "filename": newest.name,
        "generated_at": datetime.fromtimestamp(newest.stat().st_mtime, tz=UTC).isoformat(),
        "content": newest.read_text(encoding="utf-8"),
    }


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


@router.post(
    "/jobs",
    status_code=202,
    # Being a smoke-test convenience does not make it harmless: this dispatches the
    # same INGEST_PROFILE work as the audited endpoint, and without a session it
    # accepted anonymous requests with 202 on a public URL.
    dependencies=[Depends(require_role(["STEWARD", "ADMIN"]))],
)
def compatibility_trigger_job(
    request: SmokeCreateJobRequest,
    background_tasks: BackgroundTasks,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """Legacy compatibility dispatcher for taxi/dashboard smoke tests.

    Canonical versioned workflows are dispatched by their typed workflow
    routes and ``src.services.job_dispatch``.
    """
    if request.type not in {"INGEST_PROFILE", "PROPOSE_RULES"}:
        raise HTTPException(
            status_code=422,
            detail={"code": "VALIDATION_ERROR", "message": "Unsupported compatibility job type."},
        )
    target_dataset_id = request.linked_entity or "dataset-nyc-yellow-taxi-50k"
    require_dataset_access(db, session, target_dataset_id, manage=True)
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
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(job)
    db.commit()

    if request.type == "INGEST_PROFILE":
        background_tasks.add_task(
            run_ingest_profile,
            job_id,
            target_dataset_id,
            session.id,
            session.role,
        )
    elif request.type == "PROPOSE_RULES":
        background_tasks.add_task(
            run_propose_rules,
            job_id,
            target_dataset_id,
            session.id,
            session.role,
        )

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
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
) -> ExecuteTestsResponse:
    """Kích hoạt Run 2: load approved rules → test_generator → validate → repair → run → anomaly.

    Trả về test_run_id ngay lập tức. Client poll GET /dq/test-runs/{test_run_id}
    để kiểm tra trạng thái và kết quả.
    """
    from src.services.rule_store import create_test_run

    proposal_run = require_proposal_run_access(db, session, run_id, manage=True)

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
async def get_test_run_status(
    test_run_id: str,
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
) -> TestRunStatusResponse:
    """Poll trạng thái của một test run."""

    run = require_test_run_access(db, session, test_run_id)
    return TestRunStatusResponse(**run)


@dq_router.get(
    "/test-runs/{test_run_id}/results",
    response_model=TestResultsListResponse,
)
async def get_test_run_results(
    test_run_id: str,
    status: str | None = None,
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
) -> TestResultsListResponse:
    """Lấy danh sách kết quả kiểm thử của từng rule trong test run."""
    from src.services.rule_store import (
        get_test_results as store_get_results,
    )

    require_test_run_access(db, session, test_run_id)

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
    # Publishing puts rules into the active ruleset, where they compile and run.
    # Safety rule 3 makes that a steward decision, not a reader's -- and being a
    # steward somewhere is not the same as being one on this run's dataset.
    dependencies=[
        Depends(require_role(["STEWARD", "ADMIN"])),
        Depends(require_run_access(manage=True)),
    ],
)
async def publish_run_rules(
    run_id: str,
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
) -> PublishRulesResponse:
    """Xuất bản (Publish/Merge) các rules đã APPROVED từ proposal run vào Active Ruleset chính thức."""
    from src.services.rule_store import publish_approved_rules

    require_proposal_run_access(db, session, run_id, manage=True)

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
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
) -> ActiveRulesListResponse:
    """Lấy danh sách các rules đang hoạt động (Active Ruleset)."""
    from src.services.rule_store import get_active_rules as store_get_active_rules

    if not dataset_id or dataset_id == "all":
        if session.role != "ADMIN":
            raise HTTPException(status_code=403, detail={"code": "ROLE_FORBIDDEN", "message": "Only administrators may list rules across all datasets."})
    else:
        require_compat_dataset_access(db, session, dataset_id)
    rules = await asyncio.to_thread(store_get_active_rules, dataset_id if dataset_id != "all" else None, table_name)
    return ActiveRulesListResponse(
        total_rules=len(rules),
        rules=[ActiveRuleResponse(**r) for r in rules],
    )


@dq_router.patch(
    "/active-rules/{rule_id}/deactivate",
    dependencies=[Depends(require_role(["STEWARD", "ADMIN"]))],
)
async def deactivate_active_rule(
    rule_id: str,
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
) -> dict:
    """Vô hiệu hoá một active rule."""
    from src.services.rule_store import ActiveRuleModel
    from src.services.rule_store import deactivate_rule as store_deactivate_rule

    rule = db.get(ActiveRuleModel, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail=f"rule_id={rule_id!r} does not exist")
    require_compat_dataset_access(db, session, rule.dataset_id, manage=True)
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
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
) -> ExecuteTestsResponse:
    """Kích hoạt chạy test trên bộ Active Ruleset chính thức."""
    from src.services.rule_store import create_test_run, get_active_rules

    test_run_id = uuid.uuid4().hex
    dataset_id = request.dataset_id or "all"
    if dataset_id == "all":
        if session.role != "ADMIN":
            raise HTTPException(status_code=403, detail={"code": "ROLE_FORBIDDEN", "message": "Only administrators may execute all datasets."})
    else:
        require_compat_dataset_access(db, session, dataset_id, manage=True)
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
    dependencies=[Depends(require_run_access())],
)
async def list_proposal_rules(
    run_id: str,
    status: str | None = None,
    dimension: str | None = None,
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
) -> list[RuleReviewResponse]:
    from src.services.rule_store import list_rules

    require_proposal_run_access(db, session, run_id)

    rules = await asyncio.to_thread(list_rules, run_id=run_id, status=status, dimension=dimension)
    return [RuleReviewResponse(**r) for r in rules]


@dq_router.patch(
    "/runs/{run_id}/rules/{rule_id}",
    response_model=RuleReviewResponse,
    dependencies=[
        Depends(require_role(["STEWARD", "ADMIN"])),
        Depends(require_run_access(manage=True)),
    ],
)
async def review_proposal_rule(
    run_id: str,
    rule_id: str,
    body: RuleUpdateRequest,
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
) -> RuleReviewResponse:
    from src.services.rule_store import review_rule

    run = require_proposal_run_access(db, session, run_id, manage=True)
    prop = db.get(RuleProposalModel, rule_id)
    if not prop or prop.dataset_id != run["dataset_id"]:
        raise HTTPException(status_code=404, detail=f"rule_id={rule_id!r} does not exist in run_id={run_id!r}")

    res = await asyncio.to_thread(
        review_rule,
        run_id=run_id,
        rule_id=rule_id,
        status=body.status.value if hasattr(body.status, "value") else body.status,
        edited_parameters=body.edited_parameters,
        severity=body.severity,
        reviewer=session.username,
        review_note=body.review_note,
    )
    if not res:
        raise HTTPException(status_code=404, detail=f"rule_id={rule_id!r} không tồn tại trong run_id={run_id!r}")
    return RuleReviewResponse(**res)


@dq_router.post(
    "/runs/{run_id}/rules/bulk-review",
    response_model=BulkReviewResponse,
    dependencies=[
        Depends(require_role(["STEWARD", "ADMIN"])),
        Depends(require_run_access(manage=True)),
    ],
)
async def bulk_review_proposal_rules(
    run_id: str,
    body: BulkReviewRequest,
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
) -> BulkReviewResponse:
    from src.services.rule_store import bulk_review

    run = require_proposal_run_access(db, session, run_id, manage=True)

    decisions_dict = [
        {
            "rule_id": d.rule_id,
            "status": d.status.value if hasattr(d.status, "value") else d.status,
            "edited_parameters": d.edited_parameters,
            "severity": d.severity,
            "reviewer": session.username,
            "review_note": d.review_note,
        }
        for d in body.decisions
    ]
    requested_ids = {item["rule_id"] for item in decisions_dict}
    forbidden_ids = {
        row[0]
        for row in db.query(RuleProposalModel.id).filter(
            RuleProposalModel.id.in_(requested_ids),
            RuleProposalModel.dataset_id != run["dataset_id"],
        ).all()
    }
    if forbidden_ids:
        raise HTTPException(
            status_code=403,
            detail={"code": "DATASET_ACCESS_FORBIDDEN", "message": "Cross-dataset rule review is forbidden"},
        )
    updated, not_found = await asyncio.to_thread(bulk_review, run_id, decisions_dict)
    return BulkReviewResponse(
        updated_count=len(updated), rules=[RuleReviewResponse(**r) for r in updated], not_found=not_found
    )


@dq_router.get(
    "/runs/{run_id}/review-summary",
    response_model=ReviewSummaryResponse,
    dependencies=[Depends(require_run_access())],
)
async def get_run_review_summary(
    run_id: str,
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
) -> ReviewSummaryResponse:
    from src.services.rule_store import get_review_summary

    require_proposal_run_access(db, session, run_id)

    res = await asyncio.to_thread(get_review_summary, run_id)
    return ReviewSummaryResponse(**res)


@dq_router.get(
    "/runs/{run_id}/approved-rules",
    response_model=ApprovedRulesResponse,
    dependencies=[Depends(require_run_access())],
)
async def get_run_approved_rules(
    run_id: str,
    session: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
) -> ApprovedRulesResponse:
    from src.services.rule_store import get_approved_rules

    require_proposal_run_access(db, session, run_id)

    rules = await asyncio.to_thread(get_approved_rules, run_id)
    return ApprovedRulesResponse(run_id=run_id, count=len(rules), rules=[RuleReviewResponse(**r) for r in rules])


# ---------------------------------------------------------------------------
# Specialized API v1 Endpoints (Execution & Anomaly Separation)
# ---------------------------------------------------------------------------


@dq_router.post(
    "/rule-runs/{proposal_run_id}/publish",
    response_model=PublishRulesetResponse,
    dependencies=[
        Depends(require_role(["STEWARD", "ADMIN"])),
        # Same run, different path parameter name.
        Depends(require_run_access(manage=True, param="proposal_run_id")),
    ],
)
async def publish_ruleset_endpoint(
    proposal_run_id: str,
    body: PublishRulesetRequest,
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
) -> PublishRulesetResponse:
    """Publishes approved rules into an active immutable RulesetVersion."""
    from src.services.rule_store import publish_approved_rules

    proposal_run = require_proposal_run_access(db, session, proposal_run_id, manage=True)
    if body.dataset_id != proposal_run["dataset_id"]:
        raise HTTPException(status_code=422, detail="dataset_id does not match the proposal run")
    # Publishing opens its own worker session. Release the request/auth
    # session first so a small Supabase pool cannot be exhausted by the
    # worker plus this request.
    db.close()
    ruleset_ver_id = await asyncio.to_thread(
        publish_approved_rules,
        proposal_run_id=proposal_run_id,
        created_by=session.username,
    )
    if not ruleset_ver_id:
        raise HTTPException(status_code=400, detail="No approved rules found or failed to publish ruleset.")

    # ``db`` is already held by the auth dependency. Opening a second session
    # here can deadlock a small Supabase pool while the publish worker is
    # finishing. Reuse the request session for the short read-back instead.
    ruleset = db.query(RulesetVersionModel).filter(RulesetVersionModel.id == ruleset_ver_id).first()
    rules_list = json.loads(ruleset.normalized_rules) if ruleset else []
    return PublishRulesetResponse(
        ruleset_version_id=ruleset_ver_id,
        ruleset_hash=ruleset.ruleset_hash if ruleset else "",
        dataset_id=body.dataset_id,
        status="PUBLISHED",
        rule_count=len(rules_list),
    )


@dq_router.post(
    "/execution-runs",
    response_model=CombinedRunStatusResponse,
)
async def trigger_execution_run_endpoint(
    body: ExecutionRequest,
    session: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
) -> CombinedRunStatusResponse:
    """Triggers Graph 2 (Execution) and Graph 3 (Anomaly) returning combined 3-status payload."""
    from src.agents.graph import run_execution_graph

    require_compat_dataset_access(db, session, body.dataset_id, manage=True)
    if body.ruleset_version_id:
        ruleset = db.get(RulesetVersionModel, body.ruleset_version_id)
        if not ruleset or ruleset.dataset_id != body.dataset_id:
            raise HTTPException(status_code=422, detail="ruleset_version_id does not belong to dataset_id")
    existing_run = db.get(DqRunModel, body.execution_run_id)
    if existing_run and existing_run.dataset_id != body.dataset_id:
        raise HTTPException(status_code=409, detail="execution_run_id is already bound to another dataset")
    # The compatibility graph owns its database sessions for the duration of
    # the run; the request session is no longer needed after validation.
    db.close()
    res = await run_execution_graph(
        dataset_id=body.dataset_id,
        test_run_id=body.execution_run_id,
        ruleset_version_id=body.ruleset_version_id,
        trigger_type=body.trigger_type,
    )

    return CombinedRunStatusResponse(
        execution_run_id=body.execution_run_id,
        dataset_id=body.dataset_id,
        execution_status=res.get("execution_status", "DONE"),
        anomaly_status=res.get("anomaly_status", "DONE"),
        hypothesis_status=res.get("hypothesis_status", "NOT_RUN"),
        execution_details={"total_rules": len(res.get("results", []))},
        anomaly_decision=res.get("anomaly_decision", {}).get("decision"),
        anomaly_score=res.get("anomaly_decision", {}).get("score"),
    )


@dq_router.get(
    "/execution-runs/{id}/results",
    response_model=CombinedRunStatusResponse,
)
async def get_execution_run_results_endpoint(
    id: str,
    auth: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
) -> CombinedRunStatusResponse:
    """Fetches combined execution status, anomaly status, and hypothesis status."""
    # The auth dependency already owns ``db`` for this request. Reusing it is
    # important when the deployment deliberately runs with a two-connection
    # Supabase pool; a nested session here could wait on itself indefinitely.
    dq_run = db.query(DqRunModel).filter(DqRunModel.id == id).first()
    if not dq_run:
        raise HTTPException(status_code=404, detail=f"Execution run {id} not found")
    require_compat_dataset_access(db, auth, dq_run.dataset_id)

    anomaly_run = db.query(AnomalyRunModel).filter(AnomalyRunModel.execution_run_id == id).first()
    signals = (
        db.query(AnomalySignalModel).filter(AnomalySignalModel.anomaly_run_id == anomaly_run.id).all()
        if anomaly_run
        else []
    )
    hypotheses = (
        db.query(AnomalyHypothesisModel).filter(AnomalyHypothesisModel.anomaly_run_id == anomaly_run.id).all()
        if anomaly_run
        else []
    )

    return CombinedRunStatusResponse(
        execution_run_id=id,
        dataset_id=dq_run.dataset_id,
        execution_status=dq_run.status,
        anomaly_status=anomaly_run.status if anomaly_run else "NOT_RUN",
        hypothesis_status="SUCCEEDED" if hypotheses else ("FALLBACK_USED" if anomaly_run else "NOT_RUN"),
        execution_details={"total_failed": dq_run.total_failed, "total_checked": dq_run.total_checked},
        anomaly_decision=anomaly_run.decision if anomaly_run else None,
        anomaly_score=anomaly_run.score if anomaly_run else None,
        signals_count=len(signals),
        hypotheses_count=len(hypotheses),
    )


@dq_router.get(
    "/anomaly-runs/{id}/signals",
    response_model=list[AnomalySignalDTO],
)
async def get_anomaly_signals_endpoint(
    id: str,
    auth: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
) -> list[AnomalySignalDTO]:
    """Fetches specialized signals for an anomaly run."""
    anomaly = require_anomaly_run_access(db, auth, id)
    signals = db.query(AnomalySignalModel).filter(AnomalySignalModel.anomaly_run_id == anomaly.id).all()

    result = []
    for s in signals:
        baseline_dict = json.loads(s.baseline) if s.baseline else None
        refs = json.loads(s.evidence_refs) if s.evidence_refs else []
        result.append(
            AnomalySignalDTO(
                signal_id=s.id,
                family=s.family,
                target_type=s.target_type,
                target_id=s.target_id,
                score=s.score,
                reliability=s.reliability,
                observed_value=s.observed_value,
                baseline=baseline_dict,
                explanation_code=s.explanation_code,
                evidence_refs=refs,
            )
        )
    return result


@dq_router.get(
    "/anomaly-runs/{id}/hypotheses",
    response_model=list[dict[str, Any]],
)
async def get_anomaly_hypotheses_endpoint(
    id: str,
    auth: SessionModel = Depends(require_role(["USER", "STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """Fetches detailed hypotheses for an anomaly run."""
    anomaly = require_anomaly_run_access(db, auth, id)
    hyps = db.query(AnomalyHypothesisModel).filter(AnomalyHypothesisModel.anomaly_run_id == anomaly.id).all()

    return [
        {
            "id": h.id,
            "hypothesis_type": h.hypothesis_type,
            "summary": h.summary,
            "confidence": h.confidence,
            "supporting_signal_ids": json.loads(h.supporting_signal_ids) if h.supporting_signal_ids else [],
            "contradicting_signal_ids": json.loads(h.contradicting_signal_ids)
            if h.contradicting_signal_ids
            else [],
            "evidence_refs": json.loads(h.evidence_refs) if h.evidence_refs else [],
            "recommended_checks": json.loads(h.recommended_checks) if h.recommended_checks else [],
            "missing_evidence": h.missing_evidence,
            "limitations": h.limitations,
            "fallback_used": h.fallback_used,
        }
        for h in hyps
    ]


@dq_router.post(
    "/anomaly-runs/{id}/feedback",
)
async def submit_anomaly_feedback_endpoint(
    id: str,
    body: AnomalyFeedbackRequest,
    auth: SessionModel = Depends(require_role(["STEWARD", "ADMIN"])),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Submits steward feedback for an anomaly run."""
    anom_run = require_anomaly_run_access(db, auth, id, manage=True)
    feedback_id = f"fb-{uuid.uuid4().hex[:12]}"
    fb = AnomalyFeedbackModel(
        id=feedback_id,
        anomaly_run_id=anom_run.id,
        username=auth.username,
        feedback_label=body.feedback_label,
        comment=body.comment,
        created_at=utc_now(),
    )
    db.add(fb)
    db.commit()
    return {"status": "SUCCESS", "feedback_id": feedback_id, "anomaly_run_id": anom_run.id}
