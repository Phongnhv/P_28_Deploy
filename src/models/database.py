from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class SessionModel(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(256), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


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
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
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
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class DatasetAccessModel(Base):
    __tablename__ = "dataset_access"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(256), ForeignKey("datasets.id"), nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(100), ForeignKey("user_accounts.username"), nullable=False, index=True)
    access_level: Mapped[str] = mapped_column(String(16), nullable=False)
    granted_by: Mapped[str] = mapped_column(String(100), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


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
    message: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(256), unique=True, index=True, nullable=False)
    linked_entity: Mapped[str | None] = mapped_column(String(256))
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class ProfileModel(Base):
    __tablename__ = "profiles"

    dataset_id: Mapped[str] = mapped_column(String(256), ForeignKey("datasets.id"), primary_key=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    completeness_score: Mapped[float] = mapped_column(Float, nullable=False)
    validity_score: Mapped[float] = mapped_column(Float, nullable=False)
    duplicate_rate: Mapped[float] = mapped_column(Float, nullable=False)
    evidence_keys: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list
    generated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    columns: Mapped[list["ColumnProfileModel"]] = relationship(back_populates="profile", cascade="all, delete-orphan")


class ColumnProfileModel(Base):
    __tablename__ = "column_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_dataset_id: Mapped[str] = mapped_column(String(256), ForeignKey("profiles.dataset_id"), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    data_type: Mapped[str] = mapped_column(String(64), nullable=False)
    null_rate: Mapped[float] = mapped_column(Float, nullable=False)
    distinct_count: Mapped[int] = mapped_column(Integer, nullable=False)
    min_value: Mapped[float | None] = mapped_column(Float)
    max_value: Mapped[float | None] = mapped_column(Float)
    sample_value: Mapped[str] = mapped_column(Text, nullable=False)

    profile: Mapped["ProfileModel"] = relationship(back_populates="columns")


class RuleProposalModel(Base):
    __tablename__ = "rule_proposals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(256), ForeignKey("datasets.id"), nullable=False, index=True)
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
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class RuleVersionModel(Base):
    __tablename__ = "rule_versions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    rule_proposal_id: Mapped[str] = mapped_column(String(64), ForeignKey("rule_proposals.id"), nullable=False)
    dataset_id: Mapped[str] = mapped_column(String(256), nullable=False)
    rule_spec: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="APPROVED")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


class RuleConfigurationModel(Base):
    __tablename__ = "rule_configurations"

    rule_proposal_id: Mapped[str] = mapped_column(String(64), ForeignKey("rule_proposals.id"), primary_key=True)
    execution_status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    schedule_frequency: Mapped[str] = mapped_column(String(16), nullable=False, default="MANUAL")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
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
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class DqResultModel(Base):
    __tablename__ = "dq_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), ForeignKey("dq_runs.id"), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_title: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # PASS, FAIL
    checked_count: Mapped[int] = mapped_column(Integer, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False)
    failed_row_ids: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list


class AuditEventModel(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str | None] = mapped_column(String(64))
    actor_role: Mapped[str] = mapped_column(String(64), nullable=False)
    action_code: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(256), nullable=False)
    detail_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
