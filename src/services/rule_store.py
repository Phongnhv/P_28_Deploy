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
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from src.config import get_settings
from src.models.database import Base, JobModel, RuleProposalModel, RuleVersionModel
from src.models.rule_schemas import RuleStatus

logger = logging.getLogger(__name__)

_engine = None  # lazy-initialised

def get_engine():
    global _engine
    if _engine is None:
        _settings = get_settings()
        db_url = _settings.database_url
        connect_args = {}
        if "sqlite" in db_url:
            connect_args["check_same_thread"] = False

        _engine = create_engine(db_url, connect_args=connect_args)

        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _connection_record):
            if "sqlite" in db_url:
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                # Hỗ trợ hàm REGEXP trong SQLite cho rule REGEX_FORMAT
                def _sqlite_regexp(expr, item):
                    if item is None:
                        return False
                    return re.search(expr, str(item)) is not None
                dbapi_conn.create_function("REGEXP", 2, _sqlite_regexp)
                cursor.close()

    return _engine



# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ProposedRuleModel(Base):
    __tablename__ = "proposed_rules"

    # Composite PK — rule_id chỉ unique trong 1 run
    run_id:  Mapped[str] = mapped_column(String(64),  primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(512), primary_key=True)

    dataset_id:   Mapped[str]           = mapped_column(String(256), nullable=False)
    table_name:   Mapped[str]           = mapped_column(String(256), nullable=False, index=True)
    column_name:  Mapped[str | None] = mapped_column(String(256), nullable=True)
    rule_type:    Mapped[str]           = mapped_column(String(64),  nullable=False)

    # AI-proposed params — IMMUTABLE để giữ audit trail
    parameters: Mapped[str] = mapped_column(Text, nullable=False)

    # Steward override (nullable)
    edited_parameters: Mapped[str | None] = mapped_column(Text, nullable=True)

    confidence_score: Mapped[float] = mapped_column(Float,       nullable=False)
    severity:         Mapped[str]   = mapped_column(String(32),  nullable=False)
    dimension:        Mapped[str]   = mapped_column(String(32),  nullable=False, index=True)
    rule_description: Mapped[str]   = mapped_column(Text,        nullable=False)
    ai_reasoning:     Mapped[str]   = mapped_column(Text,        nullable=False)

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=RuleStatus.PENDING.value, index=True
    )
    reviewer:     Mapped[str | None] = mapped_column(String(256), nullable=True)
    review_note:  Mapped[str | None] = mapped_column(Text,        nullable=True)
    reviewed_at:  Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at:   Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        Index("ix_proposed_rules_run_status", "run_id", "status"),
        Index("ix_proposed_rules_run_dim",    "run_id", "dimension"),
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
            "run_id":             self.run_id,
            "rule_id":            self.rule_id,
            "dataset_id":         self.dataset_id,
            "table_name":         self.table_name,
            "column":             self.column_name,   # boundary rename: DB column_name → API column
            "rule_type":          self.rule_type,
            "parameters":         json.loads(self.parameters) if self.parameters else {},
            "edited_parameters":  (
                json.loads(self.edited_parameters)
                if self.edited_parameters
                else None
            ),
            "effective_parameters": self.effective_parameters,
            "confidence_score":   self.confidence_score,
            "severity":           self.severity,
            "dimension":          self.dimension,
            "rule_description":   self.rule_description,
            "ai_reasoning":       self.ai_reasoning,
            "status":             self.status,
            "reviewer":           self.reviewer,
            "review_note":        self.review_note,
            "reviewed_at":        self.reviewed_at.isoformat() if self.reviewed_at else None,
            "created_at":         self.created_at.isoformat() if self.created_at else None,
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

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ACTIVE", index=True
    )  # ACTIVE / INACTIVE
    last_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )
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

    run_id:     Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="QUEUED"
    )  # QUEUED / RUNNING / DONE / FAILED
    error:      Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )

    def to_dict(self) -> dict:
        return {
            "run_id":     self.run_id,
            "dataset_id": self.dataset_id,
            "status":     self.status,
            "error":      self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class TestRunModel(Base):
    """Lưu metadata của mỗi lần chạy Run 2 (Execution Graph)."""
    __tablename__ = "test_runs"

    test_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id:  Mapped[str] = mapped_column(String(256), nullable=False)
    status:      Mapped[str] = mapped_column(
        String(32), nullable=False, default="QUEUED"
    )  # QUEUED / RUNNING / DONE / FAILED
    error:       Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at:  Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )

    def to_dict(self) -> dict:
        return {
            "test_run_id": self.test_run_id,
            "dataset_id":  self.dataset_id,
            "status":      self.status,
            "error":       self.error,
            "created_at":  self.created_at.isoformat() if self.created_at else None,
        }


class TestResultModel(Base):
    """Lưu kết quả chạy test của từng rule trong một test run."""
    __tablename__ = "test_results"

    test_run_id:     Mapped[str] = mapped_column(String(64), primary_key=True)
    rule_id:         Mapped[str] = mapped_column(String(512), primary_key=True)
    table_name:      Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    column_name:     Mapped[str | None] = mapped_column(String(256), nullable=True)
    rule_type:       Mapped[str] = mapped_column(String(64), nullable=False)
    status:          Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # PASSED / FAILED / ERROR / SKIPPED
    violation_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_rows:      Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    violation_rate:  Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    sample_failures: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list[dict]
    sql_text:        Mapped[str] = mapped_column(Text, nullable=False)
    duration_ms:     Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    error:           Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at:      Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        Index("ix_test_results_run_status", "test_run_id", "status"),
        Index("ix_test_results_rule_rate", "rule_id", "violation_rate"),
    )

    def to_dict(self) -> dict:
        return {
            "test_run_id":     self.test_run_id,
            "rule_id":         self.rule_id,
            "table_name":      self.table_name,
            "column":          self.column_name,
            "rule_type":       self.rule_type,
            "status":          self.status,
            "violation_count": self.violation_count,
            "total_rows":      self.total_rows,
            "violation_rate":  self.violation_rate,
            "sample_failures": json.loads(self.sample_failures) if self.sample_failures else None,
            "sql_text":        self.sql_text,
            "duration_ms":     self.duration_ms,
            "error":           self.error,
            "created_at":      self.created_at.isoformat() if self.created_at else None,
        }



# ---------------------------------------------------------------------------
def init_db() -> None:
    """Tạo tất cả bảng nếu chưa tồn tại. Tự động đồng bộ legacy approved rules vào active_rules."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    logger.info("Database đã được khởi tạo tại: %s", get_settings().database_url)

    # Migration helper: nếu active_rules đang trống nhưng có proposed_rules APPROVED, tự động publish
    try:
        with Session(engine) as session:
            active_count = session.query(ActiveRuleModel).count()
            if active_count == 0:
                legacy_approved = (
                    session.query(ProposedRuleModel)
                    .filter_by(status=RuleStatus.APPROVED.value)
                    .all()
                )
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
            linked_entity=dataset_id,
            idempotency_key=f"propose-run-{run_id}",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        session.add(job)
        session.commit()
        return {
            "run_id": job.id,
            "dataset_id": job.linked_entity,
            "status": "QUEUED",
            "error": None,
            "created_at": job.created_at.isoformat()
        }

def update_run_status(run_id: str, status: str, error: str | None = None) -> None:
    """Cập nhật status của run (Job)."""
    with Session(get_engine()) as session:
        job = session.query(JobModel).filter(JobModel.id == run_id).first()
        if job:
            job.status = "RUNNING" if status == "RUNNING" else ("SUCCEEDED" if status == "DONE" else status)
            if error is not None:
                job.error = error
            job.updated_at = datetime.utcnow()
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
                "created_at": job.created_at.isoformat() if job.created_at else None
            }
        return None

# ---------------------------------------------------------------------------
# CRUD — ProposedRule (Mapped to RuleProposalModel)
# ---------------------------------------------------------------------------

def save_proposed_rules(run_id: str, dataset_id: str, rules: list[dict]) -> int:
    """Lưu danh sách rule vào DB với status=PROPOSED."""
    saved = 0
    with Session(get_engine()) as session:
        # Idempotency: delete old proposals for this dataset
        session.query(RuleProposalModel).filter(RuleProposalModel.dataset_id == dataset_id).delete()
        session.commit()

        for rule in rules:
            rule_id = rule.get("rule_id", f"rule_{uuid.uuid4().hex}")

            # Map parameters to RuleSpec
            rule_spec = {
                "type": rule.get("rule_type", "not_null"),
                "column": rule.get("column")
            }
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
                title=rule.get("rule_description", "Rule proposal"),
                description=rule.get("rule_description", ""),
                severity=rule.get("severity", "MEDIUM").upper(),
                status="PROPOSED",
                rule_type=rule.get("rule_type", "not_null"),
                rule_spec=json.dumps(rule_spec),
                evidence_refs=json.dumps([rule.get("dimension", "VALIDITY")]),
                evidence_summary=rule.get("ai_reasoning", ""),
                confidence=rule.get("confidence_score", 1.0),
                model_name="agent-proposer",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            session.add(row)
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
        query = session.query(RuleProposalModel)
        if status:
            query = query.filter(RuleProposalModel.status == status)

        rows = query.all()
        result = []
        for r in rows:
            spec = json.loads(r.rule_spec)

            # Map back to legacy schema representation
            result.append({
                "run_id": run_id or "run_1",
                "rule_id": r.id,
                "dataset_id": r.dataset_id,
                "table_name": "source_rows",
                "column": spec.get("column"),
                "rule_type": r.rule_type,
                "parameters": spec,
                "edited_parameters": None,
                "effective_parameters": spec,
                "confidence_score": r.confidence,
                "severity": r.severity,
                "dimension": dimension or "VALIDITY",
                "rule_description": r.description,
                "ai_reasoning": r.evidence_summary,
                "status": "PENDING" if r.status == "PROPOSED" else r.status,
                "reviewer": None,
                "review_note": None,
                "reviewed_at": None,
                "created_at": r.created_at.isoformat()
            })
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

        db_status = "APPROVED" if status == "APPROVED" else "REJECTED"
        row.status = db_status
        row.updated_at = datetime.utcnow()
        if severity:
            row.severity = severity.upper()

        spec = json.loads(row.rule_spec)
        if edited_parameters:
            spec.update(edited_parameters)
            row.rule_spec = json.dumps(spec)

        session.commit()

        if db_status == "APPROVED":
            # Write rule version
            rv_id = f"rv_{row.id}"
            # Check existing version
            existing_rv = session.query(RuleVersionModel).filter(RuleVersionModel.id == rv_id).first()
            if existing_rv:
                existing_rv.rule_spec = json.dumps(spec)
                existing_rv.created_at = datetime.utcnow()
            else:
                rv = RuleVersionModel(
                    id=rv_id,
                    rule_proposal_id=row.id,
                    dataset_id=row.dataset_id,
                    rule_spec=json.dumps(spec),
                    status="APPROVED",
                    version=1,
                    created_at=datetime.utcnow()
                )
                session.add(rv)
            session.commit()

        return {
            "run_id": run_id,
            "rule_id": row.id,
            "dataset_id": row.dataset_id,
            "status": "APPROVED" if db_status == "APPROVED" else "REJECTED",
            "effective_parameters": spec
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
            review_note=d.get("review_note")
        )
        if res:
            updated.append(res)
        else:
            not_found.append(d["rule_id"])
    return updated, not_found

def get_review_summary(run_id: str) -> dict:
    """Tóm tắt tiến độ review."""
    with Session(get_engine()) as session:
        rows = session.query(RuleProposalModel).all()

    total = len(rows)
    pending = sum(1 for r in rows if r.status == "PROPOSED")
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
        "by_severity": {}
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
            if row_samples is not None and not isinstance(row_samples, str):
                sample_fail_str = json.dumps(row_samples, default=str, ensure_ascii=False)
            else:
                sample_fail_str = row_samples

            row = TestResultModel(
                test_run_id=test_run_id,
                rule_id=res.get("rule_id", ""),
                table_name=res.get("table_name", ""),
                column_name=res.get("column"),
                rule_type=res.get("rule_type", ""),
                status=res.get("status", "PASSED"),
                violation_count=res.get("violation_count", 0),
                total_rows=res.get("total_rows", 0),
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
    1. Tìm tất cả các proposed_rules của run_id có status='APPROVED'.
    2. Upsert vào active_rules (với status='ACTIVE', cập nhật parameters, updated_at).
    3. Đổi status trong proposed_rules thành 'MERGED'.
    """
    merged_count = 0
    with Session(get_engine()) as session:
        approved_proposals = (
            session.query(ProposedRuleModel)
            .filter_by(run_id=run_id, status=RuleStatus.APPROVED.value)
            .all()
        )

        for p in approved_proposals:
            # Lấy effective parameters (ưu tiên edited_parameters)
            effective_params = p.effective_parameters
            params_str = json.dumps(effective_params, ensure_ascii=False)

            active_rule = session.get(ActiveRuleModel, p.rule_id)
            if active_rule:
                # Update existing active rule
                active_rule.dataset_id = p.dataset_id
                active_rule.table_name = p.table_name
                active_rule.column_name = p.column_name
                active_rule.rule_type = p.rule_type
                active_rule.parameters = params_str
                active_rule.severity = p.severity
                active_rule.dimension = p.dimension
                active_rule.rule_description = p.rule_description
                active_rule.status = "ACTIVE"
                active_rule.last_run_id = p.run_id
                active_rule.updated_at = datetime.now(UTC)
            else:
                # Insert new active rule
                active_rule = ActiveRuleModel(
                    rule_id=p.rule_id,
                    dataset_id=p.dataset_id,
                    table_name=p.table_name,
                    column_name=p.column_name,
                    rule_type=p.rule_type,
                    parameters=params_str,
                    severity=p.severity,
                    dimension=p.dimension,
                    rule_description=p.rule_description,
                    status="ACTIVE",
                    last_run_id=p.run_id,
                )
                session.add(active_rule)

            # Cập nhật status trong proposed_rules thành MERGED
            p.status = "MERGED"
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


