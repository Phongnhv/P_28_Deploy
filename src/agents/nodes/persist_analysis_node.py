"""Persist Analysis Node — LangGraph Node for Graph 3.
Writes anomaly runs, signals, and hypotheses to the database.
"""

from __future__ import annotations

import logging
import uuid
import json
from datetime import datetime, UTC
from sqlalchemy.orm import Session
from src.agents.state import AnomalyGraphState
from src.services.rule_store import get_engine
from src.models.database import (
    AnomalyRunModel,
    AnomalySignalModel,
    AnomalyHypothesisModel,
)

logger = logging.getLogger(__name__)


async def persist_analysis_node(state: AnomalyGraphState) -> dict:
    """LangGraph Node: Persists anomaly execution runs, signals, and hypotheses to the database."""
    execution_run_id = state.get("execution_run_id") or state.get("anomaly_run_id")
    anomaly_run_id = state.get("anomaly_run_id") or f"anom-{uuid.uuid4().hex[:12]}"
    detector_config_version = state.get("detector_config_version", "anomaly-v1")
    
    decision_data = state.get("anomaly_decision") or {}
    signals = state.get("signal_observations", [])
    hypotheses = state.get("hypotheses", [])
    
    engine = get_engine()
    
    try:
        with Session(engine) as session:
            # Idempotency check: Delete existing anomaly run, signals, and hypotheses for the same execution + config version
            existing_run = session.query(AnomalyRunModel).filter(
                AnomalyRunModel.execution_run_id == execution_run_id,
                AnomalyRunModel.detector_config_version == detector_config_version
            ).first()
            
            if existing_run:
                logger.info("Found existing anomaly run ID %s. Updating fields and deleting old signals/hypotheses for idempotency.", existing_run.id)
                anomaly_run_id = existing_run.id
                session.query(AnomalySignalModel).filter(AnomalySignalModel.anomaly_run_id == anomaly_run_id).delete()
                session.query(AnomalyHypothesisModel).filter(AnomalyHypothesisModel.anomaly_run_id == anomaly_run_id).delete()
                
                # Update existing run
                existing_run.status = "SUCCEEDED"
                existing_run.decision = decision_data.get("decision", "NORMAL")
                existing_run.score = float(decision_data.get("score", 0.0))
                existing_run.confidence = float(decision_data.get("confidence", 0.0))
                existing_run.severity = decision_data.get("severity", "LOW")
                existing_run.error_message = decision_data.get("override_reason") or None
                existing_run.completed_at = datetime.now(UTC)
            else:
                # Create new AnomalyRun
                anomaly_run = AnomalyRunModel(
                    id=anomaly_run_id,
                    execution_run_id=execution_run_id,
                    detector_config_version=detector_config_version,
                    status="SUCCEEDED",
                    decision=decision_data.get("decision", "NORMAL"),
                    score=float(decision_data.get("score", 0.0)),
                    confidence=float(decision_data.get("confidence", 0.0)),
                    severity=decision_data.get("severity", "LOW"),
                    error_message=decision_data.get("override_reason") or None,
                    completed_at=datetime.now(UTC)
                )
                session.add(anomaly_run)
                session.flush()  # Ensure anomaly_runs row exists before children are added
            
            # Create AnomalySignals
            for sig in signals:
                sig_record = AnomalySignalModel(
                    id=sig.get("signal_id") or f"sig-{uuid.uuid4().hex[:12]}",
                    anomaly_run_id=anomaly_run_id,
                    family=sig["family"],
                    target_type=sig["target_type"],
                    target_id=sig["target_id"],
                    score=float(sig["score"]),
                    reliability=float(sig["reliability"]),
                    observed_value=str(sig.get("observed_value")) if sig.get("observed_value") is not None else None,
                    baseline=json.dumps(sig.get("baseline", {})) if sig.get("baseline") else None,
                    sufficient_history=bool(sig.get("sufficient_history", False)),
                    detector_name=sig["detector_name"],
                    detector_version=sig["detector_version"],
                    explanation_code=sig["explanation_code"],
                    evidence_refs=json.dumps(sig.get("evidence_refs", [])),
                )
                session.add(sig_record)
                
            # Create AnomalyHypotheses
            for h in hypotheses:
                hyp_record = AnomalyHypothesisModel(
                    id=f"hyp-{uuid.uuid4().hex[:12]}",
                    anomaly_run_id=anomaly_run_id,
                    hypothesis_type=h["hypothesis_type"],
                    summary=h["summary"],
                    confidence=float(h["confidence"]),
                    supporting_signal_ids=json.dumps(h.get("supporting_signal_ids", [])),
                    contradicting_signal_ids=json.dumps(h.get("contradicting_signal_ids", [])),
                    evidence_refs=json.dumps(h.get("evidence_refs", [])),
                    recommended_checks=json.dumps(h.get("recommended_checks", [])),
                    missing_evidence=h.get("missing_evidence"),
                    limitations=h.get("limitations"),
                    model_name=state.get("metadata", {}).get("model_name", "gemini-3.5-flash") if isinstance(state.get("metadata"), dict) else "gemini-3.5-flash",
                    prompt_version="1.0.0",
                    latency_ms=0,
                    fallback_used=state.get("hypothesis_status") == "FALLBACK_USED"
                )
                session.add(hyp_record)
                
            session.commit()
            logger.info("Successfully persisted anomaly analysis for run %s. Signals count: %d, Hypotheses count: %d",
                        anomaly_run_id, len(signals), len(hypotheses))
            
            return {
                "anomaly_run_id": anomaly_run_id,
                "metadata": {**state.get("metadata", {}), "persisted_anomaly_run_id": anomaly_run_id}
            }
            
    except Exception as exc:
        logger.error("Failed to persist anomaly analysis to database: %s", exc, exc_info=True)
        return {
            "metadata": {**state.get("metadata", {}), "persistence_error": str(exc)}
        }
