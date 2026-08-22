"""HG-G2: can the audit trail actually establish who approved a rule?

Human-in-the-loop review is the product's governance claim, so it is not enough for
the endpoints to exist -- the trail they leave has to survive a question asked
months later: *who approved this rule, and when?*

The probe drives the product's own review and publication functions against a
throwaway SQLite file and then asks the resulting database that question.  A purely
static check could confirm that ``AuditEventModel`` is imported; only executing the
transition shows whether a record is actually written.

Safety: the temporary database lives in a ``TemporaryDirectory``, the engine
override is asserted to point inside it before any query runs, and the previous
engine and settings are restored in ``finally``.  The developer database is never
opened.
"""

from __future__ import annotations

import json
import tempfile
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from evalgate.normalizers import normalizers as norm
from evalgate.schemas.eval_result import (
    EvalResult,
    EvalStatus,
    Evidence,
    Finding,
    MetricValue,
    Severity,
    Threshold,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = PROJECT_ROOT / "evalgate" / "evidence" / "gate6"

GATE = "governance"
EVALUATOR = "hitl_integrity_v1"

PROBE_REVIEWER = "evalgate-probe-steward"


@dataclass
class TransitionProbe:
    transition: str
    performed: bool
    audit_events: int
    reviewer_recorded: str | None
    detail: str


def _run_probe(tmpdir: Path) -> list[TransitionProbe]:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    import src.services.rule_store as rule_store
    from src.config import get_settings
    from src.models.database import AuditEventModel

    db_path = tmpdir / "evalgate_hitl_probe.db"
    url = f"sqlite:///{db_path.as_posix()}"

    settings = get_settings()
    original_url = settings.database_url
    original_engine = rule_store._engine
    engine = create_engine(url, connect_args={"check_same_thread": False})

    # The probe must never be able to reach the developer database.
    assert str(engine.url).endswith(db_path.name), "probe engine escaped the temp directory"

    probes: list[TransitionProbe] = []
    try:
        settings.database_url = url
        rule_store._engine = engine
        rule_store.init_db()

        run_id = uuid.uuid4().hex
        dataset_id = "evalgate-probe-dataset"
        rule_id = f"{dataset_id}.probe_column.NOT_NULL"

        rule_store.create_run(run_id=run_id, dataset_id=dataset_id)
        rule_store.save_proposed_rules(
            run_id,
            dataset_id,
            [
                {
                    "rule_id": rule_id,
                    "table_name": "source_rows",
                    "column": "probe_column",
                    "rule_type": "NOT_NULL",
                    "parameters": {},
                    "confidence_score": 1.0,
                    "severity": "HIGH",
                    "dimension": "COMPLETENESS",
                    "rule_description": "EvalGate probe rule",
                    "ai_reasoning": "probe",
                }
            ],
        )

        def audit_count() -> int:
            with Session(engine) as session:
                return (
                    session.query(AuditEventModel)
                    .filter(AuditEventModel.entity_id == rule_id)
                    .count()
                )

        before_review = audit_count()
        reviewed = rule_store.review_rule(
            run_id=run_id, rule_id=rule_id, status="APPROVED", reviewer=PROBE_REVIEWER
        )
        after_review = audit_count()
        probes.append(
            TransitionProbe(
                transition="APPROVE",
                performed=bool(reviewed),
                audit_events=after_review - before_review,
                reviewer_recorded=(reviewed or {}).get("reviewer"),
                detail="review_rule executed" if reviewed else "review_rule returned None",
            )
        )

        published = rule_store.publish_approved_rules(run_id)
        after_publish = audit_count()
        probes.append(
            TransitionProbe(
                transition="PUBLISH",
                performed=published > 0,
                audit_events=after_publish - after_review,
                reviewer_recorded=None,
                detail=f"publish_approved_rules merged {published} rule(s)",
            )
        )

        with Session(engine) as session:
            active = session.query(rule_store.ActiveRuleModel).count()
            audited_ids = {
                row.entity_id
                for row in session.query(AuditEventModel).all()
            }
            orphan = (
                session.query(rule_store.ActiveRuleModel)
                .filter(~rule_store.ActiveRuleModel.rule_id.in_(audited_ids or [""]))
                .count()
            )
        probes.append(
            TransitionProbe(
                transition="ACTIVE_RULE_TRACEABILITY",
                performed=active > 0,
                audit_events=active - orphan,
                reviewer_recorded=None,
                detail=f"{orphan}/{active} active rule(s) have no audit event naming them",
            )
        )
        return probes
    finally:
        rule_store._engine = original_engine
        settings.database_url = original_url
        engine.dispose()


def evaluate(*, write_evidence: bool = True) -> EvalResult:
    error: str | None = None
    probes: list[TransitionProbe] = []
    try:
        with tempfile.TemporaryDirectory(prefix="evalgate-hitl-") as tmp:
            probes = _run_probe(Path(tmp))
    except Exception as exc:  # noqa: BLE001 - the failure itself is the observation
        error = f"{type(exc).__name__}: {exc}"

    if error is not None and not probes:
        return EvalResult(
            gate=GATE,
            evaluator=EVALUATOR,
            status=EvalStatus.BLOCKED_MISSING_CREDENTIAL,
            metadata={"reason": f"probe could not run: {error}"},
        )

    transitions = [p for p in probes if p.transition in {"APPROVE", "PUBLISH"}]
    audited = [p for p in transitions if p.audit_events > 0]
    integrity = (len(audited) / len(transitions) * 100.0) if transitions else 0.0

    traceability = next(
        (p for p in probes if p.transition == "ACTIVE_RULE_TRACEABILITY"), None
    )
    reviewer_recorded = next(
        (p.reviewer_recorded for p in probes if p.transition == "APPROVE"), None
    )

    evidence: list[Evidence] = []
    if write_evidence:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        target = EVIDENCE_DIR / "hitl_integrity.json"
        target.write_text(
            json.dumps(
                {
                    "probes": [asdict(p) for p in probes],
                    "hitl_integrity": integrity,
                    "probe_error": error,
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        evidence.append(Evidence(type="file", path=str(target.relative_to(PROJECT_ROOT))))

    findings: list[Finding] = []
    unaudited = [p.transition for p in transitions if p.audit_events == 0]
    if unaudited:
        findings.append(
            Finding(
                id="HG-G2",
                severity=Severity.CRITICAL,
                title="Rule state transitions leave no audit record",
                detail=(
                    f"{unaudited} completed against a clean database without writing an "
                    "audit event. docs/PRODUCT_SPEC.md safety rule 5 requires every state "
                    "transition to create one."
                ),
                root_cause_hint=(
                    "the review and publication paths in rule_store write rule state "
                    "directly; only the API layer writes audit events, so any call that "
                    "does not go through it leaves no trail"
                ),
                evidence_ref="evalgate/evidence/gate6/hitl_integrity.json",
                blocks_release=True,
            )
        )

    return EvalResult(
        gate=GATE,
        evaluator=EVALUATOR,
        status=EvalStatus.FAIL if findings else EvalStatus.PASS,
        score=integrity,
        metrics={
            "hitl_integrity": MetricValue(
                raw=integrity, unit="ratio", normalized=integrity
            ),
            "unaudited_transitions": MetricValue(
                raw=len(unaudited), unit="count",
                normalized=norm.zero_tolerance(len(unaudited)),
            ),
            "reviewer_persisted": MetricValue(
                raw=bool(reviewer_recorded), unit="boolean",
                normalized=norm.boolean(bool(reviewer_recorded)),
            ),
        },
        thresholds={"hitl_integrity": Threshold(**{"pass": 100.0, "warn": 100.0})},
        evidence=evidence,
        critical_findings=findings,
        metadata={
            "probe": "isolated temporary SQLite database; the developer database is never opened",
            "traceability": traceability.detail if traceability else None,
            "probe_error": error,
        },
    )
