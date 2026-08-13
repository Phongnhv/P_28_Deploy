import json
import logging
from datetime import datetime

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from src.config import get_settings
from src.models.database import Base, JobModel, RuleProposalModel, RuleVersionModel

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
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

    return _engine

def init_db() -> None:
    """Tạo tất cả bảng nếu chưa tồn tại. Gọi khi khởi động app."""
    from src.models.database import DatasetModel
    Base.metadata.create_all(get_engine())

    with Session(get_engine()) as session:
        existing = session.query(DatasetModel).filter(DatasetModel.id == "dataset-nyc-yellow-taxi-50k").first()
        if not existing:
            dataset = DatasetModel(
                id="dataset-nyc-yellow-taxi-50k",
                name="NYC Yellow Taxi · Gate 2 artifact",
                description="A deterministic 50k-row mobility dataset with a fixed manifest and synthetic quality mutations.",
                status="REGISTERED",
                row_count=50000,
                source_label="NYC TLC Yellow Taxi · pinned source",
                manifest_version="gate2-v1",
                checksum="b1549ceb43dee8e083e34d81b22db37c3afa401737e831c7ed63fb83a5baeff7",
                updated_at=datetime.utcnow()
            )
            session.add(dataset)
            session.commit()
            logger.info("Seeded default dataset-nyc-yellow-taxi-50k record")
    logger.info("Database đã được khởi tạo tại: %s", get_settings().database_url)

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
            rule_id = rule.get("rule_id", f"rule_{uuid_str()}")

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
    return list_rules(run_id=run_id, status="APPROVED")

def uuid_str() -> str:
    import uuid
    return str(uuid.uuid4())
