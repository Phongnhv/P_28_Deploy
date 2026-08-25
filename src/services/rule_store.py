import json
import logging
import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    event,
    inspect,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from src.config import get_settings
from src.models.database import (
    AuditEventModel,
    Base,
    DatasetAccessModel,
    DatasetModel,
    JobModel,
    RuleProposalModel,
    RuleVersionModel,
    SemanticContractModel,
)
from src.models.rule_schemas import RuleStatus
from src.services.session_service import ensure_default_users
from src.time_utils import utc_now

logger = logging.getLogger(__name__)

_engine = None  # lazy-initialised


def get_engine():
    global _engine
    if _engine is None:
        _settings = get_settings()
        db_url = _settings.database_url
        connect_args = {}
        engine_options = {}
        if "sqlite" in db_url:
            connect_args["check_same_thread"] = False
        else:
            # Supabase's session-mode pool has a small, project-wide client
            # limit. Keep each Cloud Run instance bounded so overlapping
            # revisions and autoscaling cannot exhaust every DB session.
            engine_options.update(
                pool_size=_settings.database_pool_size,
                max_overflow=_settings.database_max_overflow,
                pool_timeout=_settings.database_pool_timeout_seconds,
                pool_recycle=300,
                pool_pre_ping=True,
                pool_use_lifo=True,
            )

        _engine = create_engine(db_url, connect_args=connect_args, **engine_options)

        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _connection_record):
            if "sqlite" in db_url:
                # Hỗ trợ hàm REGEXP trong SQLite cho rule REGEX_FORMAT
                # Đăng ký trước khi thực thi PRAGMA để tránh lỗi OperationalError trên Windows
                def _sqlite_regexp(expr, item):
                    if item is None:
                        return False
                    return re.search(expr, str(item)) is not None

                dbapi_conn.create_function("REGEXP", 2, _sqlite_regexp)

                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                # journal_mode returns one row; consume it before closing the
                # cursor so SQLite does not retain a busy statement on startup.
                cursor.fetchone()
                cursor.close()

    return _engine


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ProposedRuleModel(Base):
    __tablename__ = "proposed_rules"

    # Composite PK — rule_id chỉ unique trong 1 run
    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(512), primary_key=True)

    dataset_id: Mapped[str] = mapped_column(String(256), nullable=False)
    table_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    column_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    rule_type: Mapped[str] = mapped_column(String(64), nullable=False)

    # AI-proposed params — IMMUTABLE để giữ audit trail
    parameters: Mapped[str] = mapped_column(Text, nullable=False)

    # Steward override (nullable)
    edited_parameters: Mapped[str | None] = mapped_column(Text, nullable=True)

    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    dimension: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    rule_description: Mapped[str] = mapped_column(Text, nullable=False)
    ai_reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    rule_name: Mapped[str] = mapped_column(String(256), nullable=False, default="Rule proposal")
    business_rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    proposal_basis: Mapped[str] = mapped_column(String(32), nullable=False, default="DATA_PROFILE")
    evidence: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    confidence_breakdown: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    status: Mapped[str] = mapped_column(String(32), nullable=False, default=RuleStatus.PENDING.value, index=True)
    reviewer: Mapped[str | None] = mapped_column(String(256), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC))

    __table_args__ = (
        Index("ix_proposed_rules_run_status", "run_id", "status"),
        Index("ix_proposed_rules_run_dim", "run_id", "dimension"),
    )

    @property
    def effective_parameters(self) -> dict:
        """Trả về edited_parameters nếu Steward đã chỉnh, ngược lại AI-proposed.

        Test Generator chỉ đọc property này — không cần biết rule có bị sửa không.
        """
        raw = self.edited_parameters if self.edited_parameters else self.parameters
        return json.loads(raw) if raw else {}

    def to_dict(self) -> dict:
        """Chuyển sang dict cho API response."""
        return {
            "run_id": self.run_id,
            "rule_id": self.rule_id,
            "dataset_id": self.dataset_id,
            "table_name": self.table_name,
            "column": self.column_name,  # boundary rename: DB column_name → API column
            "rule_type": self.rule_type,
            "parameters": json.loads(self.parameters) if self.parameters else {},
            "edited_parameters": (json.loads(self.edited_parameters) if self.edited_parameters else None),
            "effective_parameters": self.effective_parameters,
            "confidence_score": self.confidence_score,
            "severity": self.severity,
            "dimension": self.dimension,
            "rule_description": self.rule_description,
            "ai_reasoning": self.ai_reasoning,
            "rule_name": self.rule_name,
            "business_rationale": self.business_rationale,
            "proposal_basis": self.proposal_basis,
            "evidence": json.loads(self.evidence or "{}"),
            "confidence": json.loads(self.confidence_breakdown or "{}"),
            "status": self.status,
            "reviewer": self.reviewer,
            "review_note": self.review_note,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ActiveRuleModel(Base):
    """Bảng lưu trữ các quy tắc kiểm thử chính thức (Active Ruleset) của từng dataset.

    Đây là Single Source of Truth cho Test Generator (Run 2).
    Được cập nhật khi Data Steward bấm Publish các rule đã APPROVED từ proposed_rules.
    """

    __tablename__ = "active_rules"

    rule_id: Mapped[str] = mapped_column(String(512), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    table_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    column_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    rule_type: Mapped[str] = mapped_column(String(64), nullable=False)

    # Active parameters (đã được duyệt/sửa bởi Steward hoặc AI)
    parameters: Mapped[str] = mapped_column(Text, nullable=False)

    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    dimension: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    rule_description: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE", index=True)  # ACTIVE / INACTIVE
    last_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "dataset_id": self.dataset_id,
            "table_name": self.table_name,
            "column": self.column_name,
            "rule_type": self.rule_type,
            "parameters": json.loads(self.parameters) if self.parameters else {},
            "severity": self.severity,
            "dimension": self.dimension,
            "rule_description": self.rule_description,
            "status": self.status,
            "last_run_id": self.last_run_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ProposalRunModel(Base):
    __tablename__ = "proposal_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="QUEUED"
    )  # QUEUED / RUNNING / DONE / FAILED
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC))

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "dataset_id": self.dataset_id,
            "status": self.status,
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class TestRunModel(Base):
    """Lưu metadata của mỗi lần chạy Run 2 (Execution Graph)."""

    __tablename__ = "test_runs"

    test_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="QUEUED"
    )  # QUEUED / RUNNING / DONE / FAILED
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC))

    def to_dict(self) -> dict:
        return {
            "test_run_id": self.test_run_id,
            "dataset_id": self.dataset_id,
            "status": self.status,
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class TestResultModel(Base):
    """Lưu kết quả chạy test của từng rule trong một test run."""

    __tablename__ = "test_results"

    test_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(512), primary_key=True)
    table_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    column_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    rule_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # PASSED / FAILED / ERROR / SKIPPED
    violation_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    violation_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    sample_failures: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list[str] — chỉ ID dòng vi phạm
    sql_text: Mapped[str] = mapped_column(Text, nullable=False)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC))

    __table_args__ = (
        Index("ix_test_results_run_status", "test_run_id", "status"),
        Index("ix_test_results_rule_rate", "rule_id", "violation_rate"),
    )

    def to_dict(self) -> dict:
        return {
            "test_run_id": self.test_run_id,
            "rule_id": self.rule_id,
            "table_name": self.table_name,
            "column": self.column_name,
            "rule_type": self.rule_type,
            "status": self.status,
            "violation_count": self.violation_count,
            "total_rows": self.total_rows,
            "violation_rate": self.violation_rate,
            "sample_failures": json.loads(self.sample_failures) if self.sample_failures else None,
            "sql_text": self.sql_text,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ---------------------------------------------------------------------------
def init_db() -> None:
    """Tạo tất cả bảng nếu chưa tồn tại. Tự động đồng bộ legacy approved rules vào active_rules."""
    engine = get_engine()
    settings = get_settings()
    if settings.app_env == "production":
        # Production schema changes are a controlled release operation. Running
        # create/alter DDL in every Cloud Run startup races active revisions,
        # takes table locks, and can make a healthy rollout fail its probe.
        logger.info("Skipping startup schema mutations in production.")
    else:
        Base.metadata.create_all(engine)
        _migrate_local_profile_columns(engine)
        _migrate_local_proposal_columns(engine)
        _migrate_local_workflow_columns(engine)
    logger.info("Database đã được khởi tạo tại: %s", get_settings().database_url)

    # Seed default demo dataset if not present
    try:
        with Session(engine) as session:
            ensure_default_users(session)
            demo_dataset = session.get(DatasetModel, "dataset-nyc-yellow-taxi-50k")
            if not demo_dataset:
                demo_dataset = DatasetModel(
                    id="dataset-nyc-yellow-taxi-50k",
                    name="NYC Yellow Taxi 50k Sample",
                    description="Sample trip data for DQ profiling",
                    status="REGISTERED",
                    row_count=50000,
                    source_label="semantic",
                    manifest_version="1.0.0",
                    checksum="dummy",
                )
                session.add(demo_dataset)
                session.commit()
                logger.info("Seeded default demo dataset 'dataset-nyc-yellow-taxi-50k'")
            for username, access_level in (("user", "READ"), ("steward", "MANAGE")):
                existing_access = (
                    session.query(DatasetAccessModel)
                    .filter(
                        DatasetAccessModel.dataset_id == demo_dataset.id,
                        DatasetAccessModel.username == username,
                    )
                    .first()
                )
                if not existing_access:
                    session.add(
                        DatasetAccessModel(
                            id=f"access-{demo_dataset.id}-{username}",
                            dataset_id=demo_dataset.id,
                            username=username,
                            access_level=access_level,
                            granted_by="system-seed",
                        )
                    )
            session.commit()
    except Exception as e:
        logger.warning("Failed to seed default dataset: %s", e)

    # Migration helper: nếu active_rules đang trống nhưng có proposed_rules APPROVED, tự động publish
    try:
        with Session(engine) as session:
            active_count = session.query(ActiveRuleModel).count()
            if active_count == 0:
                legacy_approved = session.query(ProposedRuleModel).filter_by(status=RuleStatus.APPROVED.value).all()
                if legacy_approved:
                    for p in legacy_approved:
                        active_rule = ActiveRuleModel(
                            rule_id=p.rule_id,
                            dataset_id=p.dataset_id,
                            table_name=p.table_name,
                            column_name=p.column_name,
                            rule_type=p.rule_type,
                            parameters=json.dumps(p.effective_parameters, ensure_ascii=False),
                            severity=p.severity,
                            dimension=p.dimension,
                            rule_description=p.rule_description,
                            status="ACTIVE",
                            last_run_id=p.run_id,
                        )
                        session.add(active_rule)
                    session.commit()
                    logger.info("Đã tự động migrate %d legacy approved rules sang active_rules.", len(legacy_approved))
    except Exception as exc:
        logger.warning("Không thể chạy migration helper cho active_rules: %s", exc)


def _migrate_local_profile_columns(engine) -> None:
    """Add aggregate profile evidence columns to pre-existing local SQLite files."""
    if engine.dialect.name != "sqlite":
        return
    inspector = inspect(engine)
    column_profile_columns = {column["name"] for column in inspector.get_columns("column_profiles")}
    profile_columns = {column["name"] for column in inspector.get_columns("profiles")}
    with engine.begin() as connection:
        if "min_value" not in column_profile_columns:
            connection.exec_driver_sql("ALTER TABLE column_profiles ADD COLUMN min_value FLOAT")
        if "max_value" not in column_profile_columns:
            connection.exec_driver_sql("ALTER TABLE column_profiles ADD COLUMN max_value FLOAT")
        additions = {
            "non_null_count": "INTEGER",
            "negative_rate": "FLOAT",
            "quantiles_json": "TEXT",
            "out_of_domain_rate": "FLOAT",
            "full_distinct_count": "INTEGER",
            "uniqueness_rate": "FLOAT",
            "is_unique_full_table": "BOOLEAN",
        }
        for name, sql_type in additions.items():
            if name not in column_profile_columns:
                connection.exec_driver_sql(f"ALTER TABLE column_profiles ADD COLUMN {name} {sql_type}")
        if "cross_field_metrics_json" not in profile_columns:
            connection.exec_driver_sql("ALTER TABLE profiles ADD COLUMN cross_field_metrics_json TEXT")


def _migrate_local_proposal_columns(engine) -> None:
    """Add core evidence fields to existing proposal tables.

    Deployments historically initialized metadata with ``create_all`` only, so
    additive proposal columns must be reconciled at startup for both local
    SQLite and the compose PostgreSQL database.
    """
    if engine.dialect.name == "postgresql":
        statements = (
            "ALTER TABLE proposed_rules ADD COLUMN IF NOT EXISTS rule_name VARCHAR(256) DEFAULT 'Rule proposal'",
            "ALTER TABLE proposed_rules ADD COLUMN IF NOT EXISTS business_rationale TEXT DEFAULT ''",
            "ALTER TABLE proposed_rules ADD COLUMN IF NOT EXISTS proposal_basis VARCHAR(32) DEFAULT 'DATA_PROFILE'",
            "ALTER TABLE proposed_rules ADD COLUMN IF NOT EXISTS evidence TEXT DEFAULT '{}'",
            "ALTER TABLE proposed_rules ADD COLUMN IF NOT EXISTS parameter_provenance TEXT DEFAULT '[]'",
            "ALTER TABLE proposed_rules ADD COLUMN IF NOT EXISTS assumptions TEXT DEFAULT '[]'",
            "ALTER TABLE proposed_rules ADD COLUMN IF NOT EXISTS confidence_breakdown TEXT DEFAULT '{}'",
            "ALTER TABLE rule_proposals ADD COLUMN IF NOT EXISTS rule_name VARCHAR(256) DEFAULT 'Rule proposal'",
            "ALTER TABLE rule_proposals ADD COLUMN IF NOT EXISTS business_rationale TEXT DEFAULT ''",
            "ALTER TABLE rule_proposals ADD COLUMN IF NOT EXISTS proposal_basis VARCHAR(32) DEFAULT 'DATA_PROFILE'",
            "ALTER TABLE rule_proposals ADD COLUMN IF NOT EXISTS evidence TEXT DEFAULT '{}'",
            "ALTER TABLE rule_proposals ADD COLUMN IF NOT EXISTS parameter_provenance TEXT DEFAULT '[]'",
            "ALTER TABLE rule_proposals ADD COLUMN IF NOT EXISTS assumptions TEXT DEFAULT '[]'",
            "ALTER TABLE rule_proposals ADD COLUMN IF NOT EXISTS confidence_breakdown TEXT DEFAULT '{}'",
        )
        with engine.begin() as connection:
            for statement in statements:
                connection.exec_driver_sql(statement)
        return
    if engine.dialect.name != "sqlite":
        return
    inspector = inspect(engine)
    additions = {
        "workflow_run_id": "VARCHAR(64)",
        "rule_name": "VARCHAR(256) NOT NULL DEFAULT 'Rule proposal'",
        "business_rationale": "TEXT NOT NULL DEFAULT ''",
        "proposal_basis": "VARCHAR(32) NOT NULL DEFAULT 'DATA_PROFILE'",
        "evidence": "TEXT NOT NULL DEFAULT '{}'",
        "parameter_provenance": "TEXT NOT NULL DEFAULT '[]'",
        "assumptions": "TEXT NOT NULL DEFAULT '[]'",
        "confidence_breakdown": "TEXT NOT NULL DEFAULT '{}'",
    }
    with engine.begin() as connection:
        for table_name in ("proposed_rules", "rule_proposals"):
            if table_name not in inspector.get_table_names():
                continue
            columns = {column["name"] for column in inspector.get_columns(table_name)}
            for name, sql_type in additions.items():
                if name not in columns:
                    connection.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN {name} {sql_type}")


def _migrate_local_workflow_columns(engine) -> None:
    """Reconcile additive workflow provenance columns on existing databases."""
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            # Older local/compose schemas used the ORM attribute name as the
            # physical primary-key column.  The deployed Supabase contract uses
            # ``rule_id`` instead.  Reconcile the name before ORM queries run,
            # otherwise proposal persistence fails when SQLAlchemy issues a
            # DELETE against a column that does not exist.
            inspector = inspect(engine)
            configuration_columns = (
                {column["name"] for column in inspector.get_columns("rule_configurations")}
                if "rule_configurations" in inspector.get_table_names()
                else set()
            )
            if "rule_proposal_id" in configuration_columns and "rule_id" not in configuration_columns:
                connection.exec_driver_sql("ALTER TABLE rule_configurations RENAME COLUMN rule_proposal_id TO rule_id")
            table_names = set(inspector.get_table_names())
            jobs_columns = (
                {column["name"] for column in inspector.get_columns("jobs")} if "jobs" in table_names else set()
            )
            proposal_columns = (
                {column["name"] for column in inspector.get_columns("rule_proposals")}
                if "rule_proposals" in table_names
                else set()
            )
            access_columns = (
                {column["name"] for column in inspector.get_columns("dataset_access")}
                if "dataset_access" in table_names
                else set()
            )
            statements = [
                # Rule identities are semantic paths, not UUIDs.  The initial
                # Supabase schema used VARCHAR(36), which truncates valid IDs
                # produced by the real Graph 1 proposer.  Expand every related
                # key before proposal persistence or review can run.
                "ALTER TABLE rule_proposals ALTER COLUMN id TYPE VARCHAR(512)",
                "ALTER TABLE rule_versions ALTER COLUMN id TYPE VARCHAR(640)",
                "ALTER TABLE rule_versions ALTER COLUMN rule_proposal_id TYPE VARCHAR(512)",
                "ALTER TABLE rule_configurations ALTER COLUMN rule_id TYPE VARCHAR(512)",
                "ALTER TABLE ruleset_versions ALTER COLUMN proposal_run_id TYPE VARCHAR(512)",
                "ALTER TABLE dq_results ALTER COLUMN rule_id TYPE VARCHAR(512)",
                # The first Supabase schema used different physical names from
                # the dashboard ORM.  ``create_all`` never reconciles existing
                # tables, so every select attempted to read columns that were
                # not present and surfaced as a generic HTTP 500.
                "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS linked_entity VARCHAR(256)",
                "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS correlation_id VARCHAR(64)",
                "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ",
                "ALTER TABLE rule_proposals ADD COLUMN IF NOT EXISTS rule_spec TEXT",
                "ALTER TABLE rule_proposals ADD COLUMN IF NOT EXISTS rule_type VARCHAR(64)",
                "ALTER TABLE dq_runs ADD COLUMN IF NOT EXISTS trigger_type VARCHAR(32) NOT NULL DEFAULT 'MANUAL'",
                "ALTER TABLE dq_runs ADD COLUMN IF NOT EXISTS started_at TIMESTAMP",
                "ALTER TABLE dq_runs ADD COLUMN IF NOT EXISTS ruleset_hash VARCHAR(256)",
                # psycopg returns native list/dict values for legacy JSON
                # columns, while this ORM intentionally stores serialized JSON
                # text.  Convert once so all old and new records have the same
                # runtime type and json.loads remains deterministic.
                "ALTER TABLE rule_proposals ALTER COLUMN evidence_refs TYPE TEXT USING evidence_refs::text",
                "ALTER TABLE dq_runs ALTER COLUMN rule_ids TYPE TEXT USING rule_ids::text",
                "ALTER TABLE dq_results ALTER COLUMN failed_row_ids TYPE TEXT USING failed_row_ids::text",
                "ALTER TABLE rule_proposals ADD COLUMN IF NOT EXISTS workflow_run_id VARCHAR(64)",
                "ALTER TABLE ruleset_versions ADD COLUMN IF NOT EXISTS workflow_run_id VARCHAR(64)",
                "ALTER TABLE ruleset_versions ADD COLUMN IF NOT EXISTS stale BOOLEAN NOT NULL DEFAULT FALSE",
                "ALTER TABLE dq_runs ADD COLUMN IF NOT EXISTS workflow_run_id VARCHAR(64)",
                "ALTER TABLE dq_runs ADD COLUMN IF NOT EXISTS stale BOOLEAN NOT NULL DEFAULT FALSE",
                "ALTER TABLE rule_configurations ADD COLUMN IF NOT EXISTS model_name VARCHAR(128) NOT NULL DEFAULT 'unspecified'",
                "ALTER TABLE rule_configurations ADD COLUMN IF NOT EXISTS created_at TIMESTAMP",
                "ALTER TABLE dq_runs ADD COLUMN IF NOT EXISTS compiler_version VARCHAR(64)",
                "ALTER TABLE dq_runs ADD COLUMN IF NOT EXISTS ruleset_version_id VARCHAR(64)",
                "ALTER TABLE dq_runs ADD COLUMN IF NOT EXISTS artifact_hash VARCHAR(256)",
                "ALTER TABLE dq_runs ADD COLUMN IF NOT EXISTS retry_history_json TEXT",
                "ALTER TABLE dq_runs ADD COLUMN IF NOT EXISTS error_message TEXT",
                "ALTER TABLE dq_runs ADD COLUMN IF NOT EXISTS dbt_status VARCHAR(32)",
                "ALTER TABLE dq_runs ADD COLUMN IF NOT EXISTS metrics_status VARCHAR(32)",
                "ALTER TABLE graph1_runs ADD COLUMN IF NOT EXISTS workspace_id VARCHAR(64)",
                "ALTER TABLE graph1_runs ADD COLUMN IF NOT EXISTS dataset_version_id VARCHAR(64)",
                "ALTER TABLE graph1_runs ADD COLUMN IF NOT EXISTS profile_run_id VARCHAR(64)",
                "ALTER TABLE analysis_runs ADD COLUMN IF NOT EXISTS workspace_id VARCHAR(64)",
                "ALTER TABLE analysis_runs ADD COLUMN IF NOT EXISTS dataset_version_id VARCHAR(64)",
                "ALTER TABLE analysis_runs ADD COLUMN IF NOT EXISTS profile_run_id VARCHAR(64)",
                "ALTER TABLE analysis_runs ADD COLUMN IF NOT EXISTS rule_review_snapshot_id VARCHAR(64)",
                "ALTER TABLE rule_versions ADD COLUMN IF NOT EXISTS dataset_version_id VARCHAR(64)",
                "ALTER TABLE dq_runs ADD COLUMN IF NOT EXISTS workspace_id VARCHAR(64)",
                "ALTER TABLE dq_runs ADD COLUMN IF NOT EXISTS dataset_version_id VARCHAR(64)",
                "ALTER TABLE dq_runs ADD COLUMN IF NOT EXISTS profile_run_id VARCHAR(64)",
                "ALTER TABLE dq_runs ADD COLUMN IF NOT EXISTS rule_review_snapshot_id VARCHAR(64)",
                "ALTER TABLE dq_runs ADD COLUMN IF NOT EXISTS source_checksum VARCHAR(256)",
                "ALTER TABLE dq_results ADD COLUMN IF NOT EXISTS violation_rate DOUBLE PRECISION",
                "ALTER TABLE dq_results ADD COLUMN IF NOT EXISTS duration_ms DOUBLE PRECISION",
                "ALTER TABLE dq_results ADD COLUMN IF NOT EXISTS dbt_status VARCHAR(32)",
                "ALTER TABLE dq_results ADD COLUMN IF NOT EXISTS metrics_status VARCHAR(32)",
                "ALTER TABLE dq_results ADD COLUMN IF NOT EXISTS error_message TEXT",
                "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS role VARCHAR(64) DEFAULT 'USER'",
                "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS csrf_token VARCHAR(256) DEFAULT ''",
                "ALTER TABLE datasets ADD COLUMN IF NOT EXISTS status VARCHAR(64) DEFAULT 'REGISTERED'",
                "ALTER TABLE datasets ADD COLUMN IF NOT EXISTS source_label VARCHAR(256) DEFAULT 'unknown'",
                "ALTER TABLE datasets ADD COLUMN IF NOT EXISTS manifest_version VARCHAR(64) DEFAULT 'v1'",
                "ALTER TABLE datasets ADD COLUMN IF NOT EXISTS checksum VARCHAR(256) DEFAULT ''",
                "ALTER TABLE user_accounts ADD COLUMN IF NOT EXISTS status VARCHAR(32) DEFAULT 'ACTIVE'",
                "ALTER TABLE user_accounts ADD COLUMN IF NOT EXISTS created_by VARCHAR(100)",
                "ALTER TABLE user_accounts ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMP",
                "ALTER TABLE dataset_access ADD COLUMN IF NOT EXISTS username VARCHAR(100)",
                "CREATE INDEX IF NOT EXISTS ix_dataset_access_username ON dataset_access (username)",
                "UPDATE rule_proposals SET rule_name = 'Rule proposal' WHERE rule_name IS NULL",
                "UPDATE rule_proposals SET business_rationale = '' WHERE business_rationale IS NULL",
                "UPDATE rule_proposals SET proposal_basis = 'DATA_PROFILE' WHERE proposal_basis IS NULL",
                "UPDATE rule_proposals SET evidence = '{}' WHERE evidence IS NULL",
                "UPDATE rule_proposals SET parameter_provenance = '[]' WHERE parameter_provenance IS NULL",
                "UPDATE rule_proposals SET assumptions = '[]' WHERE assumptions IS NULL",
                "UPDATE rule_proposals SET confidence_breakdown = '{}' WHERE confidence_breakdown IS NULL",
            ]
            if "dataset_id" in jobs_columns:
                statements.append(
                    "UPDATE jobs SET linked_entity = dataset_id WHERE linked_entity IS NULL AND dataset_id IS NOT NULL"
                )
            if "rule" in proposal_columns:
                statements.extend(
                    [
                        "UPDATE rule_proposals SET rule_spec = rule::text WHERE rule_spec IS NULL AND rule IS NOT NULL",
                        "UPDATE rule_proposals SET rule_type = rule->>'type' "
                        "WHERE rule_type IS NULL AND rule IS NOT NULL",
                        "ALTER TABLE rule_proposals ALTER COLUMN rule DROP NOT NULL",
                        "ALTER TABLE rule_proposals ALTER COLUMN rule SET DEFAULT '{}'::json",
                    ]
                )
            if "user_id" in access_columns:
                statements.append("ALTER TABLE dataset_access ALTER COLUMN user_id DROP NOT NULL")
            for statement in statements:
                connection.exec_driver_sql(statement)
        return
    if engine.dialect.name != "sqlite":
        return
    inspector = inspect(engine)
    additions = {
        "rule_proposals": {"workflow_run_id": "VARCHAR(64)"},
        "ruleset_versions": {"workflow_run_id": "VARCHAR(64)", "stale": "BOOLEAN NOT NULL DEFAULT 0"},
        "dq_runs": {
            "workflow_run_id": "VARCHAR(64)",
            "stale": "BOOLEAN NOT NULL DEFAULT 0",
            "ruleset_version_id": "VARCHAR(64)",
            "compiler_version": "VARCHAR(64)",
            "artifact_hash": "VARCHAR(256)",
            "retry_history_json": "TEXT",
            "error_message": "TEXT",
            "dbt_status": "VARCHAR(32)",
            "metrics_status": "VARCHAR(32)",
            "workspace_id": "VARCHAR(64)",
            "dataset_version_id": "VARCHAR(64)",
            "profile_run_id": "VARCHAR(64)",
            "rule_review_snapshot_id": "VARCHAR(64)",
            "source_checksum": "VARCHAR(256)",
        },
        "graph1_runs": {
            "workspace_id": "VARCHAR(64)",
            "dataset_version_id": "VARCHAR(64)",
            "profile_run_id": "VARCHAR(64)",
        },
        "analysis_runs": {
            "workspace_id": "VARCHAR(64)",
            "dataset_version_id": "VARCHAR(64)",
            "profile_run_id": "VARCHAR(64)",
            "rule_review_snapshot_id": "VARCHAR(64)",
        },
        "rule_versions": {"dataset_version_id": "VARCHAR(64)"},
        # Existing local databases predate the provenance field on the ORM
        # model.  Without this additive migration, approving a proposal fails
        # when SQLAlchemy selects RuleConfigurationModel.
        "rule_configurations": {
            "model_name": "VARCHAR(128) NOT NULL DEFAULT 'unspecified'",
            "created_at": "DATETIME",
        },
        "dq_results": {
            "violation_rate": "FLOAT",
            "duration_ms": "FLOAT",
            "dbt_status": "VARCHAR(32)",
            "metrics_status": "VARCHAR(32)",
            "error_message": "TEXT",
        },
    }
    with engine.begin() as connection:
        for table_name, columns_to_add in additions.items():
            if table_name not in inspector.get_table_names():
                continue
            columns = {column["name"] for column in inspector.get_columns(table_name)}
            for name, sql_type in columns_to_add.items():
                if name not in columns:
                    connection.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN {name} {sql_type}")

        # SQLite cannot rename a primary-key column in-place.  Rebuild only
        # this small configuration table, preserving every configuration while
        # moving its physical key from the old local name to the Supabase
        # contract name expected by RuleConfigurationModel.
        refreshed = inspect(engine)
        if "rule_configurations" in refreshed.get_table_names():
            configuration_columns = {column["name"] for column in refreshed.get_columns("rule_configurations")}
            if "rule_proposal_id" in configuration_columns and "rule_id" not in configuration_columns:
                connection.exec_driver_sql("ALTER TABLE rule_configurations RENAME TO rule_configurations_legacy_key")
                connection.exec_driver_sql(
                    """
                    CREATE TABLE rule_configurations (
                        rule_id VARCHAR(64) NOT NULL PRIMARY KEY,
                        execution_status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
                        schedule_frequency VARCHAR(16) NOT NULL DEFAULT 'MANUAL',
                        timezone VARCHAR(64) NOT NULL DEFAULT 'UTC',
                        last_run_at DATETIME,
                        next_run_at DATETIME,
                        model_name VARCHAR(128) NOT NULL DEFAULT 'unspecified',
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL,
                        FOREIGN KEY(rule_id) REFERENCES rule_proposals (id)
                    )
                    """
                )
                connection.exec_driver_sql(
                    """
                    INSERT INTO rule_configurations (
                        rule_id, execution_status, schedule_frequency, timezone,
                        last_run_at, next_run_at, model_name, created_at, updated_at
                    )
                    SELECT
                        rule_proposal_id, execution_status, schedule_frequency, timezone,
                        last_run_at, next_run_at, model_name, created_at, updated_at
                    FROM rule_configurations_legacy_key
                    """
                )
                connection.exec_driver_sql("DROP TABLE rule_configurations_legacy_key")


# ---------------------------------------------------------------------------
# CRUD — ProposalRun (Mapped to JobModel)
# ---------------------------------------------------------------------------


def create_run(run_id: str, dataset_id: str) -> dict:
    """Tạo bản ghi run mới (Job) với status=PENDING/QUEUED."""
    with Session(get_engine()) as session:
        job = JobModel(
            id=run_id,
            type="PROPOSE_RULES",
            status="PENDING",
            progress=0.0,
            message="Queued for rule proposal generation",
            attempt_count=0,
            linked_entity=dataset_id,
            idempotency_key=f"propose-run-{run_id}",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        session.add(job)
        session.commit()
        return {
            "run_id": job.id,
            "dataset_id": job.linked_entity,
            "status": "QUEUED",
            "error": None,
            "created_at": job.created_at.isoformat(),
        }


def update_run_status(run_id: str, status: str, error: str | None = None) -> None:
    """Cập nhật status của run (Job)."""
    with Session(get_engine()) as session:
        job = session.query(JobModel).filter(JobModel.id == run_id).first()
        if job:
            job.status = "RUNNING" if status == "RUNNING" else ("SUCCEEDED" if status == "DONE" else status)
            if error is not None:
                job.error = error
            job.updated_at = utc_now()
            session.commit()


def get_run(run_id: str) -> dict | None:
    """Lấy thông tin run (Job) theo run_id."""
    with Session(get_engine()) as session:
        job = session.query(JobModel).filter(JobModel.id == run_id).first()
        if job:
            status_map = {"PENDING": "QUEUED", "RUNNING": "RUNNING", "SUCCEEDED": "DONE", "FAILED": "FAILED"}
            return {
                "run_id": job.id,
                "dataset_id": job.linked_entity or "unknown",
                "status": status_map.get(job.status, job.status),
                "error": job.error,
                "created_at": job.created_at.isoformat() if job.created_at else None,
            }
        return None


def save_proposed_rules(run_id: str, dataset_id: str, rules: list[dict]) -> int:
    """Lưu danh sách rule vào DB với status=PROPOSED."""
    saved = 0
    with Session(get_engine()) as session:
        from src.models.database import RuleConfigurationModel

        # 1. Clean pending proposals that have not been approved/merged yet (they have no versions/configs)
        session.query(RuleProposalModel).filter(
            RuleProposalModel.dataset_id == dataset_id, RuleProposalModel.status.in_(["PROPOSED", "PENDING"])
        ).delete(synchronize_session=False)

        # 2. Identify the specific rule IDs being proposed in this run
        new_rule_ids = []
        for r in rules:
            r_id = r.get("rule_id", f"rule_{uuid.uuid4().hex}")
            new_rule_ids.append(r_id)

        # 3. For these specific rule IDs, clean up their versions/configs and the proposals themselves
        # if they exist (e.g. from previous approved/merged states) to avoid duplicates and FK violations.
        # This keeps all OTHER historical approved rules completely intact.
        if new_rule_ids:
            session.query(RuleConfigurationModel).filter(
                RuleConfigurationModel.rule_proposal_id.in_(new_rule_ids)
            ).delete(synchronize_session=False)

            session.query(RuleVersionModel).filter(RuleVersionModel.rule_proposal_id.in_(new_rule_ids)).delete(
                synchronize_session=False
            )

            session.query(RuleProposalModel).filter(RuleProposalModel.id.in_(new_rule_ids)).delete(
                synchronize_session=False
            )

        # 4. Clean proposed rules run logs for idempotency
        session.query(ProposedRuleModel).filter(ProposedRuleModel.run_id == run_id).delete(synchronize_session=False)
        session.commit()

        for rule in rules:
            rule_id = rule.get("rule_id", f"rule_{uuid.uuid4().hex}")
            # HITL owns review state; raw proposer output intentionally has no review fields.
            status_val = rule.get("status") or "PENDING"

            # Map for RuleProposalModel: if it is PENDING or PROPOSED, map to PROPOSED. Otherwise keep it.
            rp_status = "PROPOSED" if status_val in ("PENDING", "PROPOSED") else status_val

            # Map parameters to RuleSpec
            rule_spec = {"type": rule.get("rule_type", "not_null"), "column": rule.get("column")}
            params = rule.get("parameters", {})
            if "min" in params:
                rule_spec["min_value"] = params["min"]
            if "max" in params:
                rule_spec["max_value"] = params["max"]
            if "accepted_values" in params:
                rule_spec["allowed_values"] = params["accepted_values"]
            if "target_column" in params:
                rule_spec["target_column"] = params["target_column"]
                rule_spec["operator"] = params.get("operator", "<=")
                rule_spec["columns"] = [rule.get("column"), params["target_column"]]
            if "fingerprint_columns" in params:
                rule_spec["fingerprint_columns"] = params["fingerprint_columns"]

            row = RuleProposalModel(
                id=rule_id,
                dataset_id=dataset_id,
                title=rule.get("rule_name") or rule.get("rule_description", "Rule proposal"),
                description=rule.get("rule_description", ""),
                severity=rule.get("severity", "MEDIUM").upper(),
                status=rp_status,
                rule_type=rule.get("rule_type", "not_null"),
                rule_spec=json.dumps(rule_spec),
                evidence_refs=json.dumps(rule.get("selected_evidence_refs", []), ensure_ascii=False),
                evidence_summary=rule.get("ai_reasoning", ""),
                confidence=rule.get("confidence_score", 1.0),
                rule_name=rule.get("rule_name") or rule.get("rule_description", "Rule proposal"),
                business_rationale=rule.get("business_rationale") or rule.get("ai_reasoning", ""),
                proposal_basis=rule.get("proposal_basis", "DATA_PROFILE"),
                evidence=json.dumps(rule.get("evidence", {}), ensure_ascii=False),
                confidence_breakdown=json.dumps(
                    rule.get("confidence")
                    or {
                        "overall": rule.get("confidence_score", 1.0),
                        "evidence_strength": rule.get("confidence_score", 1.0),
                        "business_support": rule.get("confidence_score", 1.0),
                        "sample_representativeness": rule.get("confidence_score", 1.0),
                        "explanation": "Legacy confidence score",
                    },
                    ensure_ascii=False,
                ),
                model_name="agent-proposer",
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            session.add(row)

            # Write to ProposedRuleModel for backward compatibility & tests
            params_str = json.dumps(params, ensure_ascii=False)
            proposed_row = ProposedRuleModel(
                run_id=run_id,
                rule_id=rule_id,
                dataset_id=dataset_id,
                table_name=rule.get("table_name", "source_rows"),
                column_name=rule.get("column"),
                rule_type=rule.get("rule_type", "not_null"),
                parameters=params_str,
                confidence_score=rule.get("confidence_score", 1.0),
                severity=rule.get("severity", "MEDIUM"),
                dimension=rule.get("dimension", "VALIDITY"),
                rule_description=rule.get("rule_description", ""),
                ai_reasoning=rule.get("ai_reasoning", ""),
                rule_name=rule.get("rule_name") or rule.get("rule_description", "Rule proposal"),
                business_rationale=rule.get("business_rationale") or rule.get("ai_reasoning", ""),
                proposal_basis=rule.get("proposal_basis", "DATA_PROFILE"),
                evidence=json.dumps(rule.get("evidence", {}), ensure_ascii=False),
                confidence_breakdown=json.dumps(
                    rule.get("confidence")
                    or {
                        "overall": rule.get("confidence_score", 1.0),
                        "evidence_strength": rule.get("confidence_score", 1.0),
                        "business_support": rule.get("confidence_score", 1.0),
                        "sample_representativeness": rule.get("confidence_score", 1.0),
                        "explanation": "Legacy confidence score",
                    },
                    ensure_ascii=False,
                ),
                status=status_val,
            )
            session.add(proposed_row)

            saved += 1
        session.commit()
    logger.info("Saved %d rule proposals for dataset_id=%s", saved, dataset_id)
    return saved


def list_rules(
    run_id: str | None = None,
    status: str | None = None,
    table_name: str | None = None,
    dimension: str | None = None,
) -> list[dict]:
    """Truy vấn danh sách rule proposals."""
    with Session(get_engine()) as session:
        query = session.query(ProposedRuleModel)
        if run_id:
            query = query.filter(ProposedRuleModel.run_id == run_id)
        if status:
            query = query.filter(ProposedRuleModel.status == status)
        if table_name:
            query = query.filter(ProposedRuleModel.table_name == table_name)
        if dimension:
            query = query.filter(ProposedRuleModel.dimension == dimension)

        rows = query.all()
        result = []
        for r in rows:
            params = json.loads(r.parameters) if r.parameters else {}
            edited_params = json.loads(r.edited_parameters) if r.edited_parameters else None
            effective_params = edited_params if edited_params else params

            result.append(
                {
                    "run_id": r.run_id,
                    "rule_id": r.rule_id,
                    "dataset_id": r.dataset_id,
                    "table_name": r.table_name,
                    "column": r.column_name,
                    "rule_type": r.rule_type,
                    "parameters": params,
                    "edited_parameters": edited_params,
                    "effective_parameters": effective_params,
                    "confidence_score": r.confidence_score,
                    "severity": r.severity,
                    "dimension": r.dimension,
                    "rule_description": r.rule_description,
                    "ai_reasoning": r.ai_reasoning,
                    "rule_name": r.rule_name,
                    "business_rationale": r.business_rationale,
                    "proposal_basis": r.proposal_basis,
                    "evidence": json.loads(r.evidence or "{}"),
                    "confidence": json.loads(r.confidence_breakdown or "{}"),
                    "status": r.status,
                    "reviewer": r.reviewer,
                    "review_note": r.review_note,
                    "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
            )
        return result


def review_rule(
    run_id: str,
    rule_id: str,
    status: str,
    edited_parameters: dict | None = None,
    severity: str | None = None,
    reviewer: str | None = None,
    review_note: str | None = None,
) -> dict | None:
    """Cập nhật một rule proposal. Nếu APPROVED, tạo rule_version."""
    with Session(get_engine()) as session:
        row = session.query(RuleProposalModel).filter(RuleProposalModel.id == rule_id).first()
        if not row:
            return None

        # Validate edited_parameters
        if edited_parameters is not None:
            if row.rule_type == "RANGE":
                if not isinstance(edited_parameters, dict) or (
                    "min" not in edited_parameters and "max" not in edited_parameters
                ):
                    raise ValueError("edited_parameters không hợp lệ cho rule RANGE (yêu cầu min hoặc max)")
            elif row.rule_type == "ACCEPTED_VALUES":
                if not isinstance(edited_parameters, dict) or "accepted_values" not in edited_parameters:
                    raise ValueError("edited_parameters không hợp lệ cho rule ACCEPTED_VALUES")
            elif row.rule_type == "REGEX_FORMAT":
                if not isinstance(edited_parameters, dict) or "regex" not in edited_parameters:
                    raise ValueError("edited_parameters không hợp lệ cho rule REGEX_FORMAT")

        db_status = "APPROVED" if status == "APPROVED" else "REJECTED"
        row.status = db_status
        row.updated_at = utc_now()
        if severity:
            row.severity = severity.upper()

        spec = json.loads(row.rule_spec)

        # Determine original parameter format from spec before editing
        orig_spec_params = {}
        if row.rule_type == "RANGE":
            orig_spec_params = {"min": spec.get("min_value"), "max": spec.get("max_value")}
        elif row.rule_type == "ACCEPTED_VALUES":
            orig_spec_params = {"accepted_values": spec.get("allowed_values")}
        elif row.rule_type == "REGEX_FORMAT":
            orig_spec_params = {"regex": spec.get("regex")}

        if edited_parameters:
            if "min" in edited_parameters:
                spec["min_value"] = edited_parameters["min"]
            if "max" in edited_parameters:
                spec["max_value"] = edited_parameters["max"]
            if "accepted_values" in edited_parameters:
                spec["allowed_values"] = edited_parameters["accepted_values"]
            if "regex" in edited_parameters:
                spec["regex"] = edited_parameters["regex"]
            row.rule_spec = json.dumps(spec)

        # Update ProposedRuleModel for backward compatibility / tests
        proposed_row = session.get(ProposedRuleModel, (run_id, rule_id))
        orig_params = orig_spec_params
        if proposed_row:
            proposed_row.status = db_status
            if severity:
                proposed_row.severity = severity.upper()
            if reviewer:
                proposed_row.reviewer = reviewer
            if review_note:
                proposed_row.review_note = review_note
            if edited_parameters:
                proposed_row.edited_parameters = json.dumps(edited_parameters, ensure_ascii=False)

            if proposed_row.parameters:
                orig_params = json.loads(proposed_row.parameters)

        session.commit()

        if db_status == "APPROVED":
            # Write rule version
            rv_id = f"rv_{row.id}"
            # Check existing version
            existing_rv = session.query(RuleVersionModel).filter(RuleVersionModel.id == rv_id).first()
            if existing_rv:
                existing_rv.rule_spec = json.dumps(spec)
                existing_rv.created_at = utc_now()
            else:
                rv = RuleVersionModel(
                    id=rv_id,
                    rule_proposal_id=row.id,
                    dataset_id=row.dataset_id,
                    rule_spec=json.dumps(spec),
                    status="APPROVED",
                    version=1,
                    created_at=utc_now(),
                )
                session.add(rv)
            session.commit()

        effective_params = edited_parameters if edited_parameters is not None else orig_params

        return {
            "run_id": run_id,
            "rule_id": row.id,
            "dataset_id": row.dataset_id,
            "table_name": "source_rows",
            "column": spec.get("column"),
            "rule_type": row.rule_type,
            "parameters": orig_params,
            "edited_parameters": edited_parameters,
            "effective_parameters": effective_params,
            "confidence_score": row.confidence,
            "severity": row.severity,
            "dimension": proposed_row.dimension if proposed_row else "VALIDITY",
            "rule_description": row.description,
            "ai_reasoning": row.evidence_summary,
            "status": "PENDING" if row.status == "PROPOSED" else row.status,
            "reviewer": reviewer or (proposed_row.reviewer if proposed_row else None),
            "review_note": review_note or (proposed_row.review_note if proposed_row else None),
            "reviewed_at": row.updated_at.isoformat() if row.updated_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }


def bulk_review(run_id: str, decisions: list[dict]) -> tuple[list[dict], list[str]]:
    updated = []
    not_found = []
    for d in decisions:
        res = review_rule(
            run_id=run_id,
            rule_id=d["rule_id"],
            status=d["status"],
            edited_parameters=d.get("edited_parameters"),
            severity=d.get("severity"),
            reviewer=d.get("reviewer"),
            review_note=d.get("review_note"),
        )
        if res:
            updated.append(res)
        else:
            not_found.append(d["rule_id"])
    return updated, not_found


def get_review_summary(run_id: str) -> dict:
    """Tóm tắt tiến độ review."""
    with Session(get_engine()) as session:
        rows = session.query(ProposedRuleModel).filter(ProposedRuleModel.run_id == run_id).all()

    total = len(rows)
    pending = sum(1 for r in rows if r.status in ("PENDING", "PROPOSED"))
    approved = sum(1 for r in rows if r.status == "APPROVED")
    rejected = sum(1 for r in rows if r.status == "REJECTED")

    return {
        "total": total,
        "pending": pending,
        "approved": approved,
        "rejected": rejected,
        "edited": 0,
        "is_complete": (pending == 0 and total > 0),
        "by_dimension": {},
        "by_severity": {},
    }


def get_approved_rules(run_id: str) -> list[dict]:
    """Lấy tất cả rule APPROVED cho một run — input contract cho Test Generator."""
    return list_rules(run_id=run_id, status=RuleStatus.APPROVED.value)


# ---------------------------------------------------------------------------
# CRUD — TestRun & TestResult (Run 2)
# ---------------------------------------------------------------------------


def create_test_run(test_run_id: str, dataset_id: str) -> dict:
    """Tạo bản ghi test run mới với status=QUEUED."""
    with Session(get_engine()) as session:
        # Check if job exists, if not create dummy job to satisfy foreign key constraint
        job = session.get(JobModel, test_run_id)
        if not job:
            job = JobModel(
                id=test_run_id,
                type="RUN_DQ",
                status="RUNNING",
                progress=0.0,
                message="Running Graph 2 data-quality checks",
                attempt_count=0,
                linked_entity=dataset_id,
                idempotency_key=f"run-dq-job-{test_run_id}",
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            session.add(job)

        run = TestRunModel(
            test_run_id=test_run_id,
            dataset_id=dataset_id,
            status="QUEUED",
        )
        session.add(run)
        session.commit()
        return run.to_dict()


def update_test_run_status(test_run_id: str, status: str, error: str | None = None) -> None:
    """Cập nhật status của test run (RUNNING / DONE / FAILED)."""
    with Session(get_engine()) as session:
        run = session.get(TestRunModel, test_run_id)
        if run:
            run.status = status
            if error is not None:
                run.error = error
            session.commit()


def get_test_run(test_run_id: str) -> dict | None:
    """Lấy thông tin test run theo test_run_id."""
    with Session(get_engine()) as session:
        run = session.get(TestRunModel, test_run_id)
        return run.to_dict() if run else None


def list_test_runs(dataset_id: str | None = None) -> list[dict]:
    """Danh sách các test runs đã thực hiện."""
    with Session(get_engine()) as session:
        query = session.query(TestRunModel)
        if dataset_id:
            query = query.filter(TestRunModel.dataset_id == dataset_id)
        rows = query.order_by(TestRunModel.created_at.desc()).all()
        return [r.to_dict() for r in rows]


def save_test_results(test_run_id: str, results: list[dict]) -> int:
    """Lưu kết quả chạy test của danh sách rules vào DB.

    Idempotent: xoá kết quả cũ cùng test_run_id trước khi insert.
    """
    saved = 0
    with Session(get_engine()) as session:
        session.query(TestResultModel).filter_by(test_run_id=test_run_id).delete()

        for res in results:
            row_samples = res.get("sample_failures")
            if row_samples is None:
                row_samples = res.get("sample_refs")
            if row_samples is not None and not isinstance(row_samples, str):
                sample_fail_str = json.dumps(row_samples, default=str, ensure_ascii=False)
            else:
                sample_fail_str = row_samples

            v_count = res.get("violation_count")
            if v_count is None:
                v_count = res.get("failed_count", 0)

            t_rows = res.get("total_rows")
            if t_rows is None:
                t_rows = res.get("checked_count", 0)

            row = TestResultModel(
                test_run_id=test_run_id,
                rule_id=res.get("rule_id", ""),
                table_name=res.get("table_name", ""),
                column_name=res.get("column"),
                rule_type=res.get("rule_type", ""),
                status=res.get("status", "PASSED"),
                violation_count=v_count,
                total_rows=t_rows,
                violation_rate=res.get("violation_rate", 0.0),
                sample_failures=sample_fail_str,
                sql_text=res.get("sql_text", ""),
                duration_ms=res.get("duration_ms", 0.0),
                error=res.get("error"),
            )
            session.add(row)
            saved += 1
        session.commit()
    logger.info("Đã lưu %d test results cho test_run_id=%s", saved, test_run_id)
    return saved


def get_test_results(test_run_id: str, status: str | None = None) -> list[dict]:
    """Lấy danh sách test results của một test run."""
    with Session(get_engine()) as session:
        query = session.query(TestResultModel).filter(TestResultModel.test_run_id == test_run_id)
        if status:
            query = query.filter(TestResultModel.status == status)
        rows = query.order_by(TestResultModel.table_name, TestResultModel.rule_id).all()
        return [r.to_dict() for r in rows]


def get_rule_history(rule_id: str, limit: int = 30) -> list[dict]:
    """Lấy lịch sử violation_rate của một rule qua các test runs (cho Anomaly Detector)."""
    with Session(get_engine()) as session:
        rows = (
            session.query(TestResultModel)
            .filter(TestResultModel.rule_id == rule_id)
            .order_by(TestResultModel.created_at.desc())
            .limit(limit)
            .all()
        )
        return [r.to_dict() for r in rows]


# ---------------------------------------------------------------------------
# CRUD — Active Rules (Published Ruleset)
# ---------------------------------------------------------------------------


def publish_approved_rules(run_id: str) -> int:
    """Xuất bản (Publish/Merge) các rules đã APPROVED từ run_id vào bảng active_rules.

    Quy trình:
    1. Lấy dataset_id từ proposal run.
    2. Tìm tất cả các rule_proposals của dataset có status='APPROVED'.
    3. Upsert vào active_rules (với status='ACTIVE', cập nhật parameters, updated_at).
    4. Đổi status trong rule_proposals thành 'MERGED'.
    """
    merged_count = 0
    with Session(get_engine()) as session:
        # Get dataset_id from proposal run (JobModel)
        job = session.get(JobModel, run_id)
        if not job:
            logger.warning("publish_approved_rules: run_id=%s not found in jobs table", run_id)
            return 0
        dataset_id = job.linked_entity

        approved_proposals = session.query(RuleProposalModel).filter_by(dataset_id=dataset_id, status="APPROVED").all()

        def _extract_clean_parameters(rule_type: str, spec: dict) -> dict:
            params = {}
            if rule_type == "RANGE":  # range type
                if "min" in spec:
                    params["min"] = spec["min"]
                elif "min_value" in spec:
                    params["min"] = spec["min_value"]
                if "max" in spec:
                    params["max"] = spec["max"]
                elif "max_value" in spec:
                    params["max"] = spec["max_value"]
            elif rule_type == "ACCEPTED_VALUES":
                if "accepted_values" in spec:
                    params["accepted_values"] = spec["accepted_values"]
                elif "allowed_values" in spec:
                    params["accepted_values"] = spec["allowed_values"]
            elif rule_type == "REGEX_FORMAT":
                if "regex" in spec:
                    params["regex"] = spec["regex"]
            elif rule_type == "CROSS_FIELD_COMPARISON":
                if "target_column" in spec:
                    params["target_column"] = spec["target_column"]
                if "operator" in spec:
                    params["operator"] = spec["operator"]
            return params

        for p in approved_proposals:
            spec = json.loads(p.rule_spec)
            clean_params = _extract_clean_parameters(p.rule_type, spec)
            params_str = json.dumps(clean_params, ensure_ascii=False)

            table_name = spec.get("table_name") or (p.id.split(".")[0] if "." in p.id else "source_rows")
            column_name = spec.get("column")
            proposed_source = (
                session.query(ProposedRuleModel)
                .filter(ProposedRuleModel.rule_id == p.id)
                .order_by(ProposedRuleModel.created_at.desc())
                .first()
            )
            dimension = proposed_source.dimension if proposed_source else "VALIDITY"

            active_rule = session.get(ActiveRuleModel, p.id)
            if active_rule:
                # Update existing active rule
                active_rule.dataset_id = p.dataset_id
                active_rule.table_name = table_name
                active_rule.column_name = column_name
                active_rule.rule_type = p.rule_type
                active_rule.parameters = params_str
                active_rule.severity = p.severity
                active_rule.dimension = dimension
                active_rule.rule_description = p.description
                active_rule.status = "ACTIVE"
                active_rule.last_run_id = run_id
                active_rule.updated_at = datetime.now(UTC)
            else:
                # Insert new active rule
                active_rule = ActiveRuleModel(
                    rule_id=p.id,
                    dataset_id=p.dataset_id,
                    table_name=table_name,
                    column_name=column_name,
                    rule_type=p.rule_type,
                    parameters=params_str,
                    severity=p.severity,
                    dimension=dimension,
                    rule_description=p.description,
                    status="ACTIVE",
                    last_run_id=run_id,
                )
                session.add(active_rule)

            # Cập nhật status trong proposed_rules thành MERGED
            p.status = "MERGED"

            # Cập nhật status trong rule_versions thành MERGED
            rv_id = f"rv_{p.id}"
            rv = session.get(RuleVersionModel, rv_id)
            if rv:
                rv.status = "MERGED"

            merged_count += 1

        session.commit()

    logger.info("Đã publish %d rules vào active_rules từ run_id=%s", merged_count, run_id)
    return merged_count


def get_active_rules(dataset_id: str | None = None, table_name: str | None = None) -> list[dict]:
    """Lấy danh sách các rules đang hoạt động (status='ACTIVE') phục vụ Test Generator."""
    with Session(get_engine()) as session:
        query = session.query(ActiveRuleModel).filter_by(status="ACTIVE")
        if dataset_id:
            query = query.filter_by(dataset_id=dataset_id)
        if table_name:
            query = query.filter_by(table_name=table_name)

        rows = query.order_by(ActiveRuleModel.table_name, ActiveRuleModel.rule_id).all()
        return [r.to_dict() for r in rows]


def deactivate_rule(rule_id: str) -> bool:
    """Vô hiệu hoá một rule đang active."""
    with Session(get_engine()) as session:
        rule = session.get(ActiveRuleModel, rule_id)
        if rule:
            rule.status = "INACTIVE"
            rule.updated_at = datetime.now(UTC)
            session.commit()
            return True
        return False


def save_generated_dbt_yaml(
    run_id: str,
    yaml_content: str | None = None,
    *,
    artifact_metadata: dict | None = None,
) -> bool:
    """Store dbt artifact metadata without duplicating the YAML in the database."""
    try:
        with Session(get_engine()) as session:
            audit = AuditEventModel(
                id=f"audit-{uuid.uuid4().hex[:8]}",
                actor_role="SYSTEM",
                action_code="GENERATE_DBT_YAML_TESTS",
                entity_type="job",
                entity_id=run_id,
                detail_json=json.dumps(
                    {"run_id": run_id, "artifact": artifact_metadata or {}},
                    ensure_ascii=False,
                ),
                created_at=utc_now(),
            )
            session.add(audit)
            session.commit()
            logger.info("Đã lưu tệp dbt YML vào nhật ký Audit CSDL cho run_id=%s", run_id)
        return True
    except Exception as exc:
        logger.warning("save_generated_dbt_yaml failed: %s", exc)
        return False


def save_semantic_contract(
    run_id: str,
    dataset_id: str,
    contract: dict,
    status: str = "DRAFT",
) -> str:
    """Save or update a semantic contract in the database."""
    contract_id = f"sem-{uuid.uuid4().hex[:12]}"
    try:
        with Session(get_engine()) as session:
            existing = session.query(SemanticContractModel).filter(SemanticContractModel.run_id == run_id).first()
            if existing:
                existing.status = status
                existing.contract_json = json.dumps(contract, ensure_ascii=False)
                existing.updated_at = utc_now()
                contract_id = existing.id
            else:
                record = SemanticContractModel(
                    id=contract_id,
                    dataset_id=dataset_id,
                    run_id=run_id,
                    status=status,
                    contract_json=json.dumps(contract, ensure_ascii=False),
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
                session.add(record)
            session.commit()
            logger.info("Saved semantic contract %s for run %s into DB", contract_id, run_id)
        return contract_id
    except Exception as exc:
        logger.warning("save_semantic_contract failed: %s", exc)
        return ""
