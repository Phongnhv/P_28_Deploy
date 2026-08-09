"""Rule Store — SQLAlchemy ORM models và CRUD cho HITL Rule Review.

Two tables:
  - proposed_rules: mỗi rule AI đề xuất, với trạng thái PENDING/APPROVED/REJECTED
  - proposal_runs : metadata của mỗi lần chạy Run 1 (để UI poll)

effective_parameters property: Test Generator chỉ đọc cái này — không cần biết
Steward có sửa hay không.

Dùng database_url từ settings (SQLite mặc định, Postgres-ready).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

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

logger = logging.getLogger(__name__)

settings = get_settings()
engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})

# Bật WAL mode cho SQLite để tránh locking khi nhiều thread đọc/ghi song song
@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _connection_record):
    if "sqlite" in settings.database_url:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


# ---------------------------------------------------------------------------
# Base & Models
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


class ProposedRuleModel(Base):
    __tablename__ = "proposed_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    dataset_id: Mapped[str] = mapped_column(String(256), nullable=False)
    table_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    column_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    rule_type: Mapped[str] = mapped_column(String(64), nullable=False)

    # AI-proposed params — IMMUTABLE để giữ audit trail
    parameters: Mapped[str] = mapped_column(Text, nullable=False)

    # Steward override (nullable)
    edited_parameters: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    ai_reasoning: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PENDING", index=True
    )
    reviewer: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_proposed_rules_run_status", "run_id", "status"),
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
            "id": self.id,
            "run_id": self.run_id,
            "dataset_id": self.dataset_id,
            "table_name": self.table_name,
            "column_name": self.column_name,
            "rule_type": self.rule_type,
            "parameters": json.loads(self.parameters) if self.parameters else {},
            "edited_parameters": (
                json.loads(self.edited_parameters)
                if self.edited_parameters
                else None
            ),
            "effective_parameters": self.effective_parameters,
            "confidence_score": self.confidence_score,
            "severity": self.severity,
            "ai_reasoning": self.ai_reasoning,
            "status": self.status,
            "reviewer": self.reviewer,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ProposalRunModel(Base):
    __tablename__ = "proposal_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="QUEUED"
    )  # QUEUED / RUNNING / DONE / FAILED
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "dataset_id": self.dataset_id,
            "status": self.status,
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ---------------------------------------------------------------------------
# DB initialisation
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Tạo tất cả bảng nếu chưa tồn tại. Gọi khi khởi động app."""
    Base.metadata.create_all(engine)
    logger.info("Database đã được khởi tạo tại: %s", settings.database_url)


# ---------------------------------------------------------------------------
# CRUD — ProposalRun
# ---------------------------------------------------------------------------

def create_run(run_id: str, dataset_id: str) -> dict:
    """Tạo bản ghi run mới với status=QUEUED."""
    with Session(engine) as session:
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
    with Session(engine) as session:
        run = session.get(ProposalRunModel, run_id)
        if run:
            run.status = status
            if error is not None:
                run.error = error
            session.commit()


def get_run(run_id: str) -> dict | None:
    """Lấy thông tin run theo run_id."""
    with Session(engine) as session:
        run = session.get(ProposalRunModel, run_id)
        return run.to_dict() if run else None


# ---------------------------------------------------------------------------
# CRUD — ProposedRule
# ---------------------------------------------------------------------------

def save_proposed_rules(run_id: str, dataset_id: str, rules: list[dict]) -> int:
    """Lưu danh sách rule (từ rule_proposer_node) vào DB với status=PENDING.

    Returns:
        Số rule đã lưu thành công.
    """
    saved = 0
    with Session(engine) as session:
        for rule in rules:
            row = ProposedRuleModel(
                run_id=run_id,
                dataset_id=dataset_id,
                table_name=rule.get("table_name", ""),
                column_name=rule.get("column"),
                rule_type=rule.get("rule_type", ""),
                parameters=json.dumps(rule.get("parameters", {}), ensure_ascii=False),
                edited_parameters=None,
                confidence_score=rule.get("confidence_score", 0.0),
                severity=rule.get("severity", "MEDIUM"),
                ai_reasoning=rule.get("ai_reasoning", ""),
                status="PENDING",
            )
            session.add(row)
            saved += 1
        session.commit()
    logger.info("Đã lưu %d rules cho run_id=%s, dataset_id=%s", saved, run_id, dataset_id)
    return saved


def list_rules(
    run_id: str | None = None,
    status: str | None = None,
    table_name: str | None = None,
) -> list[dict]:
    """Truy vấn danh sách rule với bộ lọc tuỳ chọn.

    Dùng cho GET /dq/rules endpoint (Screen 5 — Rule Review Table).
    """
    with Session(engine) as session:
        query = session.query(ProposedRuleModel)
        if run_id:
            query = query.filter(ProposedRuleModel.run_id == run_id)
        if status:
            query = query.filter(ProposedRuleModel.status == status)
        if table_name:
            query = query.filter(ProposedRuleModel.table_name == table_name)
        rows = query.order_by(ProposedRuleModel.id).all()
        return [r.to_dict() for r in rows]


def review_rule(
    rule_id: int,
    status: str,
    edited_parameters: dict | None = None,
    severity: str | None = None,
    reviewer: str | None = None,
) -> dict | None:
    """Cập nhật một rule: approve / reject / edit.

    Returns:
        Dict của rule đã cập nhật, hoặc None nếu không tìm thấy.
    """
    with Session(engine) as session:
        row = session.get(ProposedRuleModel, rule_id)
        if not row:
            return None
        row.status = status
        row.reviewed_at = datetime.now(timezone.utc)
        if reviewer:
            row.reviewer = reviewer
        if edited_parameters is not None:
            row.edited_parameters = json.dumps(edited_parameters, ensure_ascii=False)
        if severity:
            row.severity = severity
        session.commit()
        return row.to_dict()


def bulk_review(decisions: list[dict]) -> list[dict]:
    """Duyệt / từ chối nhiều rule cùng lúc (checkbox flow).

    decisions: list[{rule_id, status, edited_parameters?, severity?, reviewer?}]

    Returns:
        Danh sách dict rule đã cập nhật.
    """
    updated: list[dict] = []
    with Session(engine) as session:
        for decision in decisions:
            rule_id = decision.get("rule_id")
            row = session.get(ProposedRuleModel, rule_id)
            if not row:
                logger.warning("bulk_review: không tìm thấy rule_id=%s", rule_id)
                continue
            row.status = decision.get("status", row.status)
            row.reviewed_at = datetime.now(timezone.utc)
            if decision.get("reviewer"):
                row.reviewer = decision["reviewer"]
            if decision.get("edited_parameters") is not None:
                row.edited_parameters = json.dumps(
                    decision["edited_parameters"], ensure_ascii=False
                )
            if decision.get("severity"):
                row.severity = decision["severity"]
            updated.append(row.to_dict())
        session.commit()
    return updated


def get_approved_rules(run_id: str) -> list[dict]:
    """Lấy tất cả rule APPROVED cho một run — input contract cho Test Generator."""
    return list_rules(run_id=run_id, status="APPROVED")
