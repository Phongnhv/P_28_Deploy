import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from src.time_utils import utc_now


class TriggerTypeEnum(enum.StrEnum):
    MANUAL = "MANUAL"
    PUBLISH_AND_RUN = "PUBLISH_AND_RUN"
    SCHEDULED = "SCHEDULED"


class ExecutionRunStatusEnum(enum.StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    FAILED_TO_START = "FAILED_TO_START"


class DqResultStatusEnum(enum.StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"
    RESULT_MISMATCH = "RESULT_MISMATCH"


class AnomalyFeedbackEnum(enum.StrEnum):
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
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)


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
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)


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
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)


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
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)


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
    workspace_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("workspaces.id"), nullable=True, index=True)
    dataset_version_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("dataset_versions.id"), nullable=True, index=True)
    profile_run_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("profile_runs.id"), nullable=True, index=True)
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
        String(64), ForeignKey("graph1_runs.id"), nullable=False, index=True
    )
    dataset_id: Mapped[str] = mapped_column(String(256), ForeignKey("datasets.id"), nullable=False, index=True)
    workspace_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("workspaces.id"), nullable=True, index=True)
    dataset_version_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("dataset_versions.id"), nullable=True, index=True)
    profile_run_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("profile_runs.id"), nullable=True, index=True)
    rule_review_snapshot_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("rule_review_snapshots.id"), nullable=True)
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
    dataset_version_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("dataset_versions.id"), nullable=True, index=True)
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
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)


class DqRunModel(Base):
    __tablename__ = "dq_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(64), ForeignKey("jobs.id"), nullable=False)
    dataset_id: Mapped[str] = mapped_column(String(256), ForeignKey("datasets.id"), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("workspaces.id"), nullable=True, index=True)
    dataset_version_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("dataset_versions.id"), nullable=True, index=True)
    profile_run_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("profile_runs.id"), nullable=True, index=True)
    rule_review_snapshot_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("rule_review_snapshots.id"), nullable=True)
    source_checksum: Mapped[str | None] = mapped_column(String(256), nullable=True)
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
    workflow_run_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("workflow_runs.id"), nullable=True, index=True
    )
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
    workflow_run_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("workflow_runs.id"), nullable=True, index=True
    )
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
    decision: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # NORMAL, WATCH, ANOMALY, CRITICAL, INSUFFICIENT_HISTORY
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
    feedback_label: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # TRUE_ANOMALY, FALSE_POSITIVE, EXPECTED_CHANGE, RULE_MISCONFIGURATION, UNKNOWN
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)


# ---------------------------------------------------------------------------
# Workspace-scoped data access contract (v2)
# ---------------------------------------------------------------------------


class WorkspaceModel(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    created_by: Mapped[str] = mapped_column(String(64), ForeignKey("user_accounts.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class WorkspaceMembershipModel(Base):
    __tablename__ = "workspace_memberships"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_membership_user"),
        Index("ix_workspace_membership_user_status", "user_id", "workspace_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspaces.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("user_accounts.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="USER")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class DataGroupModel(Base):
    __tablename__ = "data_groups"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_data_group_name"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspaces.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), ForeignKey("user_accounts.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class DataGroupMembershipModel(Base):
    __tablename__ = "data_group_memberships"
    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_data_group_membership_user"),
        Index("ix_data_group_membership_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    group_id: Mapped[str] = mapped_column(String(64), ForeignKey("data_groups.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("user_accounts.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class DatasetGovernanceModel(Base):
    """Workspace and ownership sidecar for the legacy logical datasets table."""

    __tablename__ = "dataset_governance"
    __table_args__ = (
        Index("ix_dataset_governance_workspace_owner", "workspace_id", "owner_user_id"),
    )

    dataset_id: Mapped[str] = mapped_column(String(256), ForeignKey("datasets.id"), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspaces.id"), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(64), ForeignKey("user_accounts.id"), nullable=False)
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, default="PRIVATE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class DatasetStewardModel(Base):
    __tablename__ = "dataset_stewards"
    __table_args__ = (UniqueConstraint("dataset_id", "user_id", name="uq_dataset_steward_user"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(256), ForeignKey("datasets.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("user_accounts.id"), nullable=False)
    assigned_by: Mapped[str] = mapped_column(String(64), ForeignKey("user_accounts.id"), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DatasetVersionModel(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (
        UniqueConstraint("dataset_id", "version_number", name="uq_dataset_version_number"),
        Index("ix_dataset_version_history", "dataset_id", "created_at"),
        Index("ix_dataset_version_workspace", "workspace_id", "dataset_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspaces.id"), nullable=False)
    dataset_id: Mapped[str] = mapped_column(String(256), ForeignKey("datasets.id"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_version_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("dataset_versions.id"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="READY")
    checksum: Mapped[str] = mapped_column(String(256), nullable=False)
    schema_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_by: Mapped[str] = mapped_column(String(64), ForeignKey("user_accounts.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class DatasetGrantModel(Base):
    __tablename__ = "dataset_grants"
    __table_args__ = (
        Index(
            "ix_dataset_grant_principal",
            "workspace_id",
            "grantee_type",
            "grantee_id",
            "permission",
        ),
        Index("ix_dataset_grant_resource", "dataset_id", "dataset_version_id", "revoked_at"),
        Index("ix_dataset_grant_set", "grant_set_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    grant_set_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspaces.id"), nullable=False)
    dataset_id: Mapped[str] = mapped_column(String(256), ForeignKey("datasets.id"), nullable=False)
    dataset_version_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("dataset_versions.id"))
    grantee_type: Mapped[str] = mapped_column(String(16), nullable=False)
    grantee_id: Mapped[str] = mapped_column(String(64), nullable=False)
    permission: Mapped[str] = mapped_column(String(32), nullable=False)
    granted_by: Mapped[str] = mapped_column(String(64), ForeignKey("user_accounts.id"), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class ProfileRunSnapshotModel(Base):
    __tablename__ = "profile_runs"
    __table_args__ = (
        Index("ix_profile_run_version_history", "dataset_version_id", "completed_at"),
        Index("ix_profile_run_dataset_history", "workspace_id", "dataset_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspaces.id"), nullable=False)
    dataset_id: Mapped[str] = mapped_column(String(256), ForeignKey("datasets.id"), nullable=False)
    dataset_version_id: Mapped[str] = mapped_column(String(64), ForeignKey("dataset_versions.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    triggered_by: Mapped[str] = mapped_column(String(64), ForeignKey("user_accounts.id"), nullable=False)
    profiler_version: Mapped[str] = mapped_column(String(64), nullable=False, default="local-v1")
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completeness_score: Mapped[float | None] = mapped_column(Float)
    validity_score: Mapped[float | None] = mapped_column(Float)
    uniqueness_score: Mapped[float | None] = mapped_column(Float)
    duplicate_rate: Mapped[float | None] = mapped_column(Float)
    quality_score: Mapped[float | None] = mapped_column(Float)
    schema_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    metrics_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    sanitized_samples_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RuleReviewSnapshotModel(Base):
    __tablename__ = "rule_review_snapshots"
    __table_args__ = (Index("ix_rule_review_version_status", "dataset_version_id", "status"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspaces.id"), nullable=False)
    dataset_id: Mapped[str] = mapped_column(String(256), ForeignKey("datasets.id"), nullable=False)
    dataset_version_id: Mapped[str] = mapped_column(String(64), ForeignKey("dataset_versions.id"), nullable=False)
    profile_run_id: Mapped[str] = mapped_column(String(64), ForeignKey("profile_runs.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class AnalysisSummaryModel(Base):
    __tablename__ = "analysis_summaries"
    __table_args__ = (Index("ix_analysis_summary_version_history", "dataset_version_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspaces.id"), nullable=False)
    dataset_id: Mapped[str] = mapped_column(String(256), ForeignKey("datasets.id"), nullable=False)
    dataset_version_id: Mapped[str] = mapped_column(String(64), ForeignKey("dataset_versions.id"), nullable=False)
    profile_run_id: Mapped[str] = mapped_column(String(64), ForeignKey("profile_runs.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    tests_passed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tests_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    anomaly_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    report_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_by: Mapped[str] = mapped_column(String(64), ForeignKey("user_accounts.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GovernedArtifactModel(Base):
    __tablename__ = "governed_artifacts"
    __table_args__ = (Index("ix_governed_artifact_version", "dataset_version_id", "artifact_type"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspaces.id"), nullable=False)
    dataset_id: Mapped[str] = mapped_column(String(256), ForeignKey("datasets.id"), nullable=False)
    dataset_version_id: Mapped[str] = mapped_column(String(64), ForeignKey("dataset_versions.id"), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(64))
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_locator: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str] = mapped_column(String(256), nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), ForeignKey("user_accounts.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class GovernanceAuditEventModel(Base):
    __tablename__ = "governance_audit_events"
    __table_args__ = (
        Index("ix_governance_audit_workspace_time", "workspace_id", "occurred_at"),
        Index("ix_governance_audit_dataset_time", "dataset_id", "occurred_at"),
        Index("ix_governance_audit_actor_time", "actor_id", "occurred_at"),
        Index("ix_governance_audit_action_time", "action", "occurred_at"),
        Index("ix_governance_audit_run_time", "run_id", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspaces.id"), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("user_accounts.id"))
    actor_role: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(256), nullable=False)
    dataset_id: Mapped[str | None] = mapped_column(String(256), ForeignKey("datasets.id"))
    dataset_version_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("dataset_versions.id"))
    run_id: Mapped[str | None] = mapped_column(String(64))
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    request_metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    detail_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="API")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class GraphNodeRunModel(Base):
    """One execution of one LangGraph node.

    The graph builders in ``src/agents/graph.py`` wrap every node so a row lands
    here on entry and is completed on exit.  This is the only place node-level
    timing and failure detail is durable: LangGraph itself keeps nothing once
    ``ainvoke`` returns, and workflow artifacts are recorded per *step*, which is
    coarser than a node.

    ``input_summary_json`` / ``output_summary_json`` hold a redacted summary
    produced by ``src.services.node_telemetry.summarize`` -- key names, counts
    and short scalars only.  Raw source rows must never reach this table: the
    platform's central privacy claim is that row values stay out of the agent
    tier, and a telemetry table is exactly the sort of place that claim quietly
    breaks.
    """

    __tablename__ = "graph_node_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    graph_run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    graph_key: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    node_name: Mapped[str] = mapped_column(String(64), nullable=False)
    node_kind: Mapped[str] = mapped_column(String(32), nullable=False)  # LLM, DETERMINISTIC, GATE
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="RUNNING")
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_summary_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    output_summary_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Correlation back to the business context the run belongs to.  All optional:
    # a graph may be driven from the CLI with no workflow or job attached.
    workflow_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    dataset_id: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    dq_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    anomaly_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
