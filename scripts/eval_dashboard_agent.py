"""Bounded, redacted live evaluation for the dashboard proposal agent."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import tempfile
import time
from collections import Counter
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.config import get_settings
from src.models.database import Base, DatasetModel, JobModel
from src.services import rule_store
from src.services.dashboard_agent_workflow import build_proposal_evidence, generate_dashboard_proposals
from src.services.job_runner import run_ingest_profile

DATASET_ID = "dataset-nyc-yellow-taxi-50k"
MAX_LIVE_CALLS = 8


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=5, help="Number of live provider calls (1-8).")
    args = parser.parse_args()
    if not 1 <= args.runs <= MAX_LIVE_CALLS:
        parser.error(f"--runs must be between 1 and {MAX_LIVE_CALLS}")
    return args


def _prepare_profile(engine) -> dict:
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            DatasetModel(
                id=DATASET_ID,
                name="NYC Yellow Taxi 50k Sample",
                description="Bounded live agent evaluation dataset",
                status="REGISTERED",
                row_count=0,
                source_label="semantic",
                manifest_version="1.0.0",
                checksum="fixture-checksum",
            )
        )
        session.add(
            JobModel(
                id="eval-profile-job",
                type="INGEST_PROFILE",
                status="PENDING",
                progress=0.0,
                idempotency_key="eval-profile-job",
                attempt_count=1,
            )
        )
        session.commit()

    run_ingest_profile("eval-profile-job", DATASET_ID, actor_role="SYSTEM_EVAL")
    with Session(engine) as session:
        job = session.get(JobModel, "eval-profile-job")
        if job is None or job.status != "SUCCEEDED":
            raise RuntimeError("Local full-table profile preparation failed")
        evidence = build_proposal_evidence(session, DATASET_ID)
    return {
        "row_count": evidence.row_count,
        "column_count": len(evidence.columns),
        "cross_field_metric_count": len(evidence.cross_field_metrics),
        "candidate_count": len(evidence.to_agent_digest()["source_rows"]["dashboard_rule_candidates"]),
    }


def main() -> None:
    args = _parse_args()
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    settings.agent_mode = "graph"
    settings.llm_provider = "openai"

    with tempfile.TemporaryDirectory(prefix="ridepulse-agent-eval-") as temp_dir:
        database_path = Path(temp_dir) / "eval.db"
        engine = create_engine(
            f"sqlite:///{database_path}",
            connect_args={"check_same_thread": False},
        )
        rule_store._engine = engine
        profile_summary = _prepare_profile(engine)

        results: list[dict] = []
        selected_types: Counter[str] = Counter()
        latencies: list[float] = []
        for run_number in range(1, args.runs + 1):
            started = time.perf_counter()
            try:
                with Session(engine) as session:
                    proposals = generate_dashboard_proposals(session, DATASET_ID)
                latency = time.perf_counter() - started
                rule_types = [proposal.rule_type for proposal in proposals]
                model_names = [proposal.model_name for proposal in proposals]
                fallback_count = sum(name == "agent-policy-fallback-v1" for name in model_names)
                passed = (
                    2 <= len(proposals) <= 5
                    and len(rule_types) == len(set(rule_types))
                    and all(proposal.evidence_refs for proposal in proposals)
                )
                selected_types.update(rule_types)
                latencies.append(latency)
                results.append(
                    {
                        "run": run_number,
                        "passed": passed,
                        "latency_seconds": round(latency, 3),
                        "proposal_count": len(proposals),
                        "rule_types": rule_types,
                        "fallback_count": fallback_count,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - eval must record provider failures and continue
                results.append(
                    {
                        "run": run_number,
                        "passed": False,
                        "latency_seconds": round(time.perf_counter() - started, 3),
                        "error_type": type(exc).__name__,
                    }
                )

        passed_runs = sum(result["passed"] for result in results)
        total_fallbacks = sum(result.get("fallback_count", 0) for result in results)
        summary = {
            "provider": "openai",
            "model": settings.openai_model_name,
            "requested_runs": args.runs,
            "passed_runs": passed_runs,
            "success_rate": round(passed_runs / args.runs, 4),
            "fallback_proposal_count": total_fallbacks,
            "mean_latency_seconds": round(statistics.mean(latencies), 3) if latencies else None,
            "p95_latency_seconds": (
                round(sorted(latencies)[max(0, math.ceil(len(latencies) * 0.95) - 1)], 3) if latencies else None
            ),
            "selected_rule_type_frequency": dict(sorted(selected_types.items())),
            "profile": profile_summary,
            "runs": results,
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        rule_store._engine = None
        engine.dispose()


if __name__ == "__main__":
    main()
