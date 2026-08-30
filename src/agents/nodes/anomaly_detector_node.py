"""Anomaly Detector Node — LangGraph Node for Graph 3.
Invokes the canonical anomaly service to calculate signals and aggregate decisions.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from src.agents.state import AnomalyGraphState
from src.config import get_settings
from src.services.anomaly_service import detect_anomalies
from src.services.rule_store import get_engine

logger = logging.getLogger(__name__)


async def anomaly_detector_node(state: AnomalyGraphState) -> dict:
    """LangGraph Node: Calculates DQ anomalies and aggregated decisions using robust estimators."""
    execution_run_id = state.get("execution_run_id") or state.get("anomaly_run_id") or "test_run"
    version = state.get("detector_config_version") or get_settings().detector_config_version

    engine = get_engine()

    try:
        with Session(engine) as db:
            logger.info(
                "Running anomaly detection for execution run ID: %s with version: %s",
                execution_run_id,
                version,
            )
            result = detect_anomalies(db, execution_run_id, detector_config_version=version)

            decision_data = {
                "decision": result["decision"],
                "score": result["score"],
                "confidence": result["confidence"],
                "severity": result["severity"],
                "override_reason": result.get("override_reason", ""),
            }

            signals = result["signals"]
            effective_version = result.get("detector_config_version", version)
            rollout_mode = result.get("rollout_mode", "DISABLED")

            logger.info(
                "Anomaly detection completed. Decision: %s, Score: %s, Signals Count: %d, Mode: %s",
                decision_data["decision"],
                decision_data["score"],
                len(signals),
                rollout_mode,
            )

            # Export trace JSON for debugging/audit
            try:
                settings = get_settings()
                base_dir = getattr(settings, "output_dir", None) or "./output"
                out_dir = Path(base_dir) / "anomaly_detector"
                out_dir.mkdir(parents=True, exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                dump_file = out_dir / f"debug_anomalies_{timestamp}_{execution_run_id}.json"
                dump_file.write_text(
                    json.dumps(
                        {
                            "detector_config_version": effective_version,
                            "rollout_mode": rollout_mode,
                            "anomaly_decision": decision_data,
                            "signals": signals,
                            "signal_errors": result.get("errors", []),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                logger.info("Exported anomalies trace to: %s", dump_file)
            except Exception as trace_exc:
                logger.warning("Failed to write anomalies trace file: %s", trace_exc)

            return {
                "signal_observations": signals,
                "anomaly_decision": decision_data,
                "anomaly_status": "SUCCEEDED",
                "detector_config_version": effective_version,
                "rollout_mode": rollout_mode,
            }

    except Exception as exc:
        logger.error("Anomaly detector node execution failed: %s", exc, exc_info=True)
        return {
            "signal_observations": [],
            "anomaly_decision": {
                "decision": "ERROR",
                "score": 0.0,
                "confidence": 0.0,
                "severity": "HIGH",
                "override_reason": f"Detector exception: {exc}",
            },
            "anomaly_status": "FAILED",
            "error": str(exc),
        }
