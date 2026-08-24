import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from src.time_utils import utc_now


class TriggerTypeEnum(str, enum.Enum):
    MANUAL = "MANUAL"
    PUBLISH_AND_RUN = "PUBLISH_AND_RUN"
    SCHEDULED = "SCHEDULED"


class ExecutionRunStatusEnum(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    FAILED_TO_START = "FAILED_TO_START"


class DqResultStatusEnum(str, enum.Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"
    RESULT_MISMATCH = "RESULT_MISMATCH"


class AnomalyFeedbackEnum(str, enum.Enum):
    TRUE_ANOMALY = "TRUE_ANOMALY"
    FALSE_POSITIVE = "FALSE_POSITIVE"
    EXPECTED_CHANGE = "EXPECTED_CHANGE"
    RULE_MISCONFIGURATION = "RULE_MISCONFIGURATION"
    UNKNOWN = "UNKNOWN"


class Base(DeclarativeBase):
    pass


class SessionModel(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(256), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)


class UserAccountModel(Base):
    __tablename__ = "user_accounts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    created_by: Mapped[str | None] = mapped_column(String(100))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )


class DatasetModel(Base):
    __tablename__ = "datasets"

    id: Mapped[str] = mapped_column(String(256), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(64), nullable=False, default="REGISTERED"
    )  # REGISTERED, INGESTED, PROFILE_READY
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_label: Mapped[str] = mapped_column(String(256), nullable=False)
    manifest_version: Mapped[str] = mapped_column(String(64), nullable=False)
    checksum: Mapped[str] = mapped_column(String(256), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )


class DatasetAccessModel(Base):
    __tablename__ = "dataset_access"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(256), ForeignKey("datasets.id"), nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(100), ForeignKey("user_accounts.username"), nullable=False, index=True)
    access_level: Mapped[str] = mapped_column(String(16), nullable=False)
    granted_by: Mapped[str] = mapped_column(String(100), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)


class SourceRowModel(Base):
    __tablename__ = "source_rows"

    source_row_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(256), ForeignKey("datasets.id"), nullable=False, index=True)

    vendor_id: Mapped[str | None] = mapped_column(String(64))
    pickup_at: Mapped[str | None] = mapped_column(String(64))
    dropoff_at: Mapped[str | None] = mapped_column(String(64))
    passenger_count: Mapped[int | None] = mapped_column(Integer)
    trip_distance: Mapped[float | None] = mapped_column(Float)
    rate_code_id: Mapped[str | None] = mapped_column(String(64))
    store_and_fwd_flag: Mapped[str | None] = mapped_column(String(64))
    pickup_location_id: Mapped[str | None] = mapped_column(String(64))
    dropoff_location_id: Mapped[str | None] = mapped_column(String(64))
    payment_type: Mapped[str | None] = mapped_column(String(64))
    fare_amount: Mapped[float | None] = mapped_column(Float)
    extra: Mapped[float | None] = mapped_column(Float)
    mta_tax: Mapped[float | None] = mapped_column(Float)
    tip_amount: Mapped[float | None] = mapped_column(Float)
    tolls_amount: Mapped[float | None] = mapped_column(Float)
    improvement_surcharge: Mapped[float | None] = mapped_column(Float)
    total_amount: Mapped[float | None] = mapped_column(Float)
    congestion_surcharge: Mapped[float | None] = mapped_column(Float)
    airport_fee: Mapped[float | None] = mapped_column(Float)
    cbd_congestion_fee: Mapped[float | None] = mapped_column(Float)


class JobModel(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)  # INGEST_PROFILE, PROPOSE_RULES, RUN_DQ
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING", index=True)
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # The deployed Supabase schema enforces NOT NULL.  Keep the ORM contract in
    # sync so every job creator (including Graph 1/2 compatibility jobs) gets
    # a safe value even when it does not provide a more specific message.
    message: Mapped[str] = mapped_column(Text, nullable=False, default="Queued")
    error: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(256), unique=True, index=True, nullable=False)
    linked_entity: Mapped[str | None] = mapped_column(String(256))
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )


class ProfileModel(Base):
    __tablename__ = "profiles"

    dataset_id: Mapped[str] = mapped_column(String(256), ForeignKey("datasets.id"), primary_key=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    completeness_score: Mapped[float] = mapped_column(Float, nullable=False)
    validity_score: Mapped[float] = mapped_column(Float, nullable=False)
    duplicate_rate: Mapped[float] = mapped_column(Float, nullable=False)
    cross_field_metrics_json: Mapped[str | None] = mapped_column(Text)
    evidence_keys: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list
    generated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)

    columns: Mapped[list["ColumnProfileModel"]] = relationship(back_populates="profile", cascade="all, delete-orphan")


class ColumnProfileModel(Base):
    __tablename__ = "column_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_dataset_id: Mapped[str] = mapped_column(String(256), ForeignKey("profiles.dataset_id"), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    data_type: Mapped[str] = mapped_column(String(64), nullable=False)
    null_rate: Mapped[float] = mapped_column(Float, nullable=False)
    distinct_count: Mapped[int] = mapped_column(Integer, nullable=False)
    non_null_count: Mapped[int | None] = mapped_column(Integer)
    negative_rate: Mapped[float | None] = mapped_column(Float)
    quantiles_json: Mapped[str | None] = mapped_column(Text)
    out_of_domain_rate: Mapped[float | None] = mapped_column(Float)
    full_distinct_count: Mapped[int | None] = mapped_column(Integer)
    uniqueness_rate: Mapped[float | None] = mapped_column(Float)
    is_unique_full_table: Mapped[bool | None] = mapped_column(Boolean)
    min_value: Mapped[float | None] = mapped_column(Float)
    max_value: Mapped[float | None] = mapped_column(Float)
    sample_value: Mapped[str] = mapped_column(Text, nullable=False)

    profile: Mapped["ProfileModel"] = relationship(back_populates="columns")


class RuleProposalModel(Base):
    __tablename__ = "rule_proposals"

    # Canonical IDs include table/column/rule semantics and can be much longer
    # than UUID-sized legacy IDs (for example cross-field comparisons).
    id: Mapped[str] = mapped_column(String(512), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(256), ForeignKey("datasets.id"), nullable=False, index=True)
    # Proposal batches belong to a concrete steward workflow.  Keeping this nullable
    # preserves legacy dashboard rows while new workflow rows remain isolated.
    workflow_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)  # LOW, MEDIUM, HIGH
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PROPOSED", index=True
    )  # PROPOSED, APPROVED, EDITED, REJECTED
    rule_type: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_spec: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    evidence_refs: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    evidence_summary: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    rule_name: Mapped[str] = mapped_column(String(256), nullable=False, default="Rule proposal")
    business_rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    proposal_basis: Mapped[str] = mapped_column(String(32), nullable=False, default="DATA_PROFILE")
    evidence: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # Hai cột dưới đây được `_migrate_local_proposal_columns` tạo trong bảng vật lý và được
    # routes.py đọc/ghi (parameter_provenance, assumptions), nhưng trước đây không được khai
    # báo trong ORM model — mọi truy cập `prop.parameter_provenance` đều ném AttributeError.
    parameter_provenance: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    assumptions: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    confidence_breakdown: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )


class WorkflowRunModel(Base):
    """Durable state for the four-stage Rule Proposer workflow.

    The UI may navigate backwards without changing this state.  Artifacts are
    marked stale only when a stage is actually re-run or its review decisions
    change.
    """

    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(256), ForeignKey("datasets.id"), nullable=False, index=True)
    current_step: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    steps_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)


class WorkflowArtifactModel(Base):
    __tablename__ = "workflow_artifacts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workflow_run_id: Mapped[str] = mapped_column(String(64), ForeignKey("workflow_runs.id"), nullable=False, index=True)
    step_key: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="VALIDATED")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    input_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)


class Graph1RunModel(Base):
    """Durable execution state for the canonical nine-node proposal graph."""

    __tablename__ = "graph1_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(256), ForeignKey("datasets.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="PENDING", index=True)
    current_node: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(256), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False, unique=True, index=True)
    state_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)


class Graph1NodeExecutionModel(Base):
    """Latest observable output for one node in a Graph 1 run."""

    __tablename__ = "graph1_node_executions"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), ForeignKey("graph1_runs.id"), nullable=False, index=True)
    node_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="PENDING")
    output_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)


class AnalysisRunModel(Base):
    """Durable orchestration state for Graph 2, Graph 3, and the steward report."""

    __tablename__ = "analysis_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    graph1_run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("graph1_runs.id"), nullable=False, unique=True, index=True
    )
    dataset_id: Mapped[str] = mapped_column(String(256), ForeignKey("datasets.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING", index=True)
    phase: Mapped[str] = mapped_column(String(32), nullable=False, default="PREPARING")
    current_node: Mapped[str | None] = mapped_column(String(64), nullable=True)
    test_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    anomaly_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    report_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    report_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(256), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AnalysisNodeExecutionModel(Base):
    """Latest observable status and safe output summary for one analysis node."""

    __tablename__ = "analysis_node_executions"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), ForeignKey("analysis_runs.id"), nullable=False, index=True)
    graph_name: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    node_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    output_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)


class RuleVersionModel(Base):
    __tablename__ = "rule_versions"

    id: Mapped[str] = mapped_column(String(640), primary_key=True)
    rule_proposal_id: Mapped[str] = mapped_column(String(512), ForeignKey("rule_proposals.id"), nullable=False)
    dataset_id: Mapped[str] = mapped_column(String(256), nullable=False)
    rule_spec: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="APPROVED")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)


class RuleConfigurationModel(Base):
    __tablename__ = "rule_configurations"

    # Keep the public/domain attribute while mapping to the legacy physical
    # primary-key name used by the original Supabase schema.
    rule_proposal_id: Mapped[str] = mapped_column(
        "rule_id", String(512), ForeignKey("rule_proposals.id"), primary_key=True
    )
    execution_status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    schedule_frequency: Mapped[str] = mapped_column(String(16), nullable=False, default="MANUAL")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Cột NOT NULL nhưng không call site nào truyền giá trị (routes.py:1108, 1178, 1302) và
    # RuleConfigurationSchema cũng không phơi ra — mọi lần tạo cấu hình đều ném IntegrityError.
    # Đặt default để insert hợp lệ trên cả schema cũ (đã NOT NULL) lẫn schema mới.
    model_name: Mapped[str] = mapped_column(String(128), nullable=False, default="unspecified")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )




class DqRunModel(Base):
    __tablename__ = "dq_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(64), ForeignKey("jobs.id"), nullable=False)
    dataset_id: Mapped[str] = mapped_column(String(256), ForeignKey("datasets.id"), nullable=False)
    rule_ids: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    total_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_checked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False, default=TriggerTypeEnum.MANUAL.value)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    # Graph 2 separation extension columns
    ruleset_version_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("ruleset_versions.id"), nullable=True)
    ruleset_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    compiler_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    artifact_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    retry_history_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    dbt_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    metrics_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Retain execution evidence when an upstream workflow revision supersedes it.
    workflow_run_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("workflow_runs.id"), nullable=True, index=True)
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class SemanticContractModel(Base):
    __tablename__ = "semantic_contracts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(256), ForeignKey("datasets.id"), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    contract_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)



class DqResultModel(Base):
    __tablename__ = "dq_results"

    # The deployed Supabase schema stores this key as VARCHAR(36) without a
    # server-side default.  Always generate it in the application so inserts
    # work against both fresh SQLite databases and the legacy cloud schema.
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    run_id: Mapped[str] = mapped_column(String(64), ForeignKey("dq_runs.id"), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(512), nullable=False)
    rule_title: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # PASS, FAIL, ERROR, SKIPPED, RESULT_MISMATCH
    checked_count: Mapped[int] = mapped_column(Integer, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False)
    failed_row_ids: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list

    # Graph 2 separation extension columns
    violation_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    dbt_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    metrics_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuditEventModel(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str | None] = mapped_column(String(64))
    actor_role: Mapped[str] = mapped_column(String(64), nullable=False)
    action_code: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(256), nullable=False)
    detail_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)


class RulesetVersionModel(Base):
    __tablename__ = "ruleset_versions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(256), ForeignKey("datasets.id"), nullable=False, index=True)
    dataset_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    proposal_run_id: Mapped[str | None] = mapped_column(String(512), ForeignKey("rule_proposals.id"), nullable=True)
    # New dashboard rulesets are owned by a workflow batch, not one legacy proposal row.
    workflow_run_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("workflow_runs.id"), nullable=True, index=True)
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    semantic_contract_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ruleset_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    normalized_rules: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)


class AnomalyRunModel(Base):
    __tablename__ = "anomaly_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    execution_run_id: Mapped[str] = mapped_column(String(64), ForeignKey("dq_runs.id"), nullable=False, index=True)
    detector_config_version: Mapped[str] = mapped_column(String(64), nullable=False, default="anomaly-v1")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    decision: Mapped[str] = mapped_column(String(32), nullable=False)  # NORMAL, WATCH, ANOMALY, CRITICAL, INSUFFICIENT_HISTORY
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    severity: Mapped[str] = mapped_column(String(32), nullable=False, default="LOW")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AnomalySignalModel(Base):
    __tablename__ = "anomaly_signals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    anomaly_run_id: Mapped[str] = mapped_column(String(64), ForeignKey("anomaly_runs.id"), nullable=False, index=True)
    family: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)  # DATASET, TABLE, COLUMN, RULE
    target_id: Mapped[str] = mapped_column(String(256), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    reliability: Mapped[float] = mapped_column(Float, nullable=False)
    observed_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    baseline: Mapped[str | None] = mapped_column(Text, nullable=True)
    sufficient_history: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    detector_name: Mapped[str] = mapped_column(String(128), nullable=False)
    detector_version: Mapped[str] = mapped_column(String(32), nullable=False)
    explanation_code: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)


class AnomalyHypothesisModel(Base):
    __tablename__ = "anomaly_hypotheses"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    anomaly_run_id: Mapped[str] = mapped_column(String(64), ForeignKey("anomaly_runs.id"), nullable=False, index=True)
    hypothesis_type: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    supporting_signal_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    contradicting_signal_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    evidence_refs: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    recommended_checks: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    missing_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    limitations: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fallback_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)


class AnomalyFeedbackModel(Base):
    __tablename__ = "anomaly_feedback"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    anomaly_run_id: Mapped[str] = mapped_column(String(64), ForeignKey("anomaly_runs.id"), nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(100), ForeignKey("user_accounts.username"), nullable=False)
    feedback_label: Mapped[str] = mapped_column(String(64), nullable=False)  # TRUE_ANOMALY, FALSE_POSITIVE, EXPECTED_CHANGE, RULE_MISCONFIGURATION, UNKNOWN
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
