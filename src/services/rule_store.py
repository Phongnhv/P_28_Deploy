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
from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    Float,
    Index,
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


# ---------------------------------------------------------------------------
# DB initialisation
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Tạo tất cả bảng nếu chưa tồn tại. Gọi khi khởi động app."""
    Base.metadata.create_all(get_engine())
    logger.info("Database đã được khởi tạo tại: %s", get_settings().database_url)


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
