"""Rule Store — SQLAlchemy ORM models và CRUD cho HITL Rule Review.

Two tables:
  - proposed_rules: mỗi rule AI đề xuất, với trạng thái PENDING/APPROVED/REJECTED
  - proposal_runs : metadata của mỗi lần chạy Run 1 (để UI poll)

PK của proposed_rules là composite (run_id, rule_id) vì rule_id chỉ unique
trong phạm vi 1 run — khớp với route /dq/runs/{run_id}/rules/{rule_id}.

effective_parameters property: Test Generator chỉ đọc cái này — không cần biết
Steward có sửa hay không.

Dùng database_url từ settings (SQLite mặc định, Postgres-ready).
"""

from __future__ import annotations

import json
import logging
import re
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
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from src.config import get_settings
from src.models.rule_schemas import RuleStatus

logger = logging.getLogger(__name__)

_engine = None  # lazy-initialised

def get_engine():
    """Trả về SQLAlchemy engine. Lazy-init lần đầu, sau đó cache.

    Hàm này (không phải module attribute) cho phép tests monkey-patch dễ dàng.
    """
    global _engine
    if _engine is None:
        _settings = get_settings()
        db_url = _settings.database_url
        if db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)

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
# Base & Models
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass



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
# CRUD — ProposalRun
# ---------------------------------------------------------------------------

def create_run(run_id: str, dataset_id: str) -> dict:
    """Tạo bản ghi run mới với status=QUEUED."""
    with Session(get_engine()) as session:
        run = ProposalRunModel(
            run_id=run_id,
            dataset_id=dataset_id,
            status="QUEUED",
        )
        session.add(run)
        session.commit()
        return run.to_dict()


def update_run_status(run_id: str, status: str, error: str | None = None) -> None:
    """Cập nhật status của run (RUNNING / DONE / FAILED)."""
    with Session(get_engine()) as session:
        run = session.get(ProposalRunModel, run_id)
        if run:
            run.status = status
            if error is not None:
                run.error = error
            session.commit()


def get_run(run_id: str) -> dict | None:
    """Lấy thông tin run theo run_id."""
    with Session(get_engine()) as session:
        run = session.get(ProposalRunModel, run_id)
        return run.to_dict() if run else None


# ---------------------------------------------------------------------------
# CRUD — ProposedRule
# ---------------------------------------------------------------------------

def save_proposed_rules(run_id: str, dataset_id: str, rules: list[dict]) -> int:
    """Lưu danh sách rule (từ hitl_gate_node) vào DB với status=PENDING.

    Idempotent: xoá rule cũ cùng run_id trước khi insert — chạy lại Run 1 trùng
    run_id không gây IntegrityError, không nhân đôi row.

    Returns:
        Số rule đã lưu thành công.
    """
    from src.models.rule_schemas import ProposedRule

    saved = 0
    with Session(get_engine()) as session:
        # Idempotency: xoá rule cũ cùng run_id trong cùng transaction
        session.query(ProposedRuleModel).filter_by(run_id=run_id).delete()

        for rule in rules:
            # Validate edited_parameters nếu có
            ep = rule.get("edited_parameters")
            if ep is not None:
                try:
                    ProposedRule.model_validate({
                        "column": rule.get("column"),
                        "rule_type": rule.get("rule_type"),
                        "parameters": ep,
                        "confidence_score": rule.get("confidence_score", 0.0),
                        "severity": rule.get("severity", "MEDIUM"),
                        "dimension": rule.get("dimension", "VALIDITY"),
                        "rule_description": rule.get("rule_description", ""),
                        "ai_reasoning": rule.get("ai_reasoning", ""),
                    })
                except Exception as exc:
                    raise ValueError(f"edited_parameters không hợp lệ: {exc}") from exc

            row = ProposedRuleModel(
                run_id=run_id,
                rule_id=rule.get("rule_id", ""),
                dataset_id=dataset_id,
                table_name=rule.get("table_name", ""),
                column_name=rule.get("column"),
                rule_type=rule.get("rule_type", ""),
                parameters=json.dumps(rule.get("parameters", {}), ensure_ascii=False),
                edited_parameters=(
                    json.dumps(ep, ensure_ascii=False) if ep is not None else None
                ),
                confidence_score=rule.get("confidence_score", 0.0),
                severity=rule.get("severity", "MEDIUM"),
                dimension=rule.get("dimension", "VALIDITY"),
                rule_description=rule.get("rule_description", ""),
                ai_reasoning=rule.get("ai_reasoning", ""),
                status=rule.get("status", RuleStatus.PENDING.value),
                reviewer=rule.get("reviewer"),
                review_note=rule.get("review_note"),
            )
            # Properly set reviewed_at if provided
            if rule.get("reviewed_at"):
                try:
                    row.reviewed_at = datetime.fromisoformat(
                        rule["reviewed_at"].rstrip("Z")
                    )
                except Exception:
                    pass

            session.add(row)
            saved += 1
        session.commit()
    logger.info("Đã lưu %d rules cho run_id=%s, dataset_id=%s", saved, run_id, dataset_id)
    return saved


def list_rules(
    run_id: str | None = None,
    status: str | None = None,
    table_name: str | None = None,
    dimension: str | None = None,
) -> list[dict]:
    """Truy vấn danh sách rule với bộ lọc tuỳ chọn.

    Dùng cho GET /dq/runs/{run_id}/rules endpoint (Screen 5 — Rule Review Table).
    """
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
        rows = query.order_by(ProposedRuleModel.rule_id).all()
        return [r.to_dict() for r in rows]


def review_rule(
    run_id: str,
    rule_id: str,
    status: str,
    edited_parameters: dict | None = None,
    severity: str | None = None,
    reviewer: str | None = None,
    review_note: str | None = None,
) -> dict | None:
    """Cập nhật một rule: approve / reject / edit.

    Returns:
        Dict của rule đã cập nhật, hoặc None nếu không tìm thấy.
    Raises:
        ValueError: khi edited_parameters không qua guardrail _validate_parameters.
    """
    # Validate edited_parameters trước khi ghi
    if edited_parameters is not None:
        try:
            _validate_edited_params(run_id, rule_id, edited_parameters)
        except ValueError:
            raise

    with Session(get_engine()) as session:
        row = session.get(ProposedRuleModel, (run_id, rule_id))
        if not row:
            return None
        row.status = status
        row.reviewed_at = datetime.now(UTC)
        if reviewer is not None:
            row.reviewer = reviewer
        if review_note is not None:
            row.review_note = review_note
        if edited_parameters is not None:
            row.edited_parameters = json.dumps(edited_parameters, ensure_ascii=False)
        if severity is not None:
            row.severity = severity
        session.commit()
        return row.to_dict()


def _validate_edited_params(run_id: str, rule_id: str, edited_parameters: dict) -> None:
    """Dùng ProposedRule guardrail để validate edited_parameters trước khi ghi vào DB."""
    from src.models.rule_schemas import ProposedRule

    with Session(get_engine()) as session:
        row = session.get(ProposedRuleModel, (run_id, rule_id))
        if not row:
            return  # row không tồn tại — review_rule sẽ trả None

    try:
        ProposedRule.model_validate({
            "column": row.column_name,
            "rule_type": row.rule_type,
            "parameters": edited_parameters,
            "confidence_score": row.confidence_score,
            "severity": row.severity,
            "dimension": row.dimension,
            "rule_description": row.rule_description,
            "ai_reasoning": row.ai_reasoning,
        })
    except Exception as exc:
        raise ValueError(f"edited_parameters không hợp lệ cho rule {rule_id}: {exc}") from exc


def bulk_review(
    run_id: str,
    decisions: list[dict],
) -> tuple[list[dict], list[str]]:
    """Duyệt / từ chối nhiều rule cùng lúc (checkbox flow).

    decisions: list[{rule_id, status, edited_parameters?, severity?, reviewer?, review_note?}]

    Returns:
        (updated: list[dict], not_found_ids: list[str])
    """
    updated: list[dict] = []
    not_found_ids: list[str] = []

    with Session(get_engine()) as session:
        for decision in decisions:
            rid = decision.get("rule_id")
            row = session.get(ProposedRuleModel, (run_id, rid))
            if not row:
                logger.warning("bulk_review: không tìm thấy rule_id=%s trong run_id=%s", rid, run_id)
                not_found_ids.append(rid)
                continue
            row.status = decision.get("status", row.status)
            row.reviewed_at = datetime.now(UTC)
            if decision.get("reviewer"):
                row.reviewer = decision["reviewer"]
            if decision.get("review_note") is not None:
                row.review_note = decision["review_note"]
            if decision.get("edited_parameters") is not None:
                row.edited_parameters = json.dumps(
                    decision["edited_parameters"], ensure_ascii=False
                )
            if decision.get("severity"):
                row.severity = decision["severity"]
        session.commit()
        # Đọc lại sau commit để trả về state mới nhất
        for decision in decisions:
            rid = decision.get("rule_id")
            row = session.get(ProposedRuleModel, (run_id, rid))
            if row:
                updated.append(row.to_dict())

    return updated, not_found_ids


def get_review_summary(run_id: str) -> dict:
    """Tóm tắt tiến độ review cho 1 run — badge UI.

    Returns dict:
      {total, pending, approved, rejected, edited, is_complete,
       by_dimension: {dim: {total, pending, approved, rejected}},
       by_severity:  {sev: {total, pending, approved, rejected}}}
    """
    with Session(get_engine()) as session:
        rows = (
            session.query(ProposedRuleModel)
            .filter(ProposedRuleModel.run_id == run_id)
            .all()
        )

    total    = len(rows)
    pending  = sum(1 for r in rows if r.status == RuleStatus.PENDING.value)
    approved = sum(1 for r in rows if r.status == RuleStatus.APPROVED.value)
    rejected = sum(1 for r in rows if r.status == RuleStatus.REJECTED.value)
    edited   = sum(1 for r in rows if r.edited_parameters is not None)

    by_dimension: dict[str, dict] = {}
    by_severity:  dict[str, dict] = {}

    for r in rows:
        for bucket, key in [(by_dimension, r.dimension), (by_severity, r.severity)]:
            if key not in bucket:
                bucket[key] = {"total": 0, "pending": 0, "approved": 0, "rejected": 0}
            bucket[key]["total"] += 1
            bucket[key][r.status.lower()] = bucket[key].get(r.status.lower(), 0) + 1

    return {
        "total":        total,
        "pending":      pending,
        "approved":     approved,
        "rejected":     rejected,
        "edited":       edited,
        "is_complete":  (pending == 0 and total > 0),
        "by_dimension": by_dimension,
        "by_severity":  by_severity,
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
            sample_fail = res.get("sample_failures")
            if sample_fail is not None and not isinstance(sample_fail, str):
                sample_fail_str = json.dumps(sample_fail, ensure_ascii=False)
            else:
                sample_fail_str = sample_fail

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


