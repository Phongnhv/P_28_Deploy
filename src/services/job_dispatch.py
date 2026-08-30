"""Durable dispatch contract shared by API, local worker and Cloud Run.

The API owns authorization and persistence.  This module only creates a
durable job envelope and asks the configured worker transport to execute it;
the worker reloads the linked entity by id and applies the workflow's own
idempotent terminal-state guards.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.config import get_settings
from src.models.database import AnalysisRunModel, DatasetVersionModel, Graph1RunModel, JobModel
from src.services.gcp_run import dispatch_cloud_run_job
from src.services.job_service import claim_job, get_job, renew_job_lease, update_job_status
from src.services.rule_store import get_engine

logger = logging.getLogger(__name__)

SUPPORTED_JOB_TYPES = {
    "INGEST_PROFILE",
    "GRAPH1_EXECUTION",
    "GRAPH1_CONTINUATION",
    "ANALYSIS_GRAPH2_GRAPH3",
}


def job_checksum(job: JobModel) -> str | None:
    """Read the pre-upload checksum from the reservation envelope."""
    try:
        payload = json.loads(job.message or "{}")
        return str(payload.get("checksum")) if payload.get("checksum") else None
    except (TypeError, ValueError):
        return None


def create_persisted_job(
    db: Session,
    *,
    job_type: str,
    linked_entity: str,
    idempotency_key: str,
    message: str,
    correlation_id: str | None = None,
) -> tuple[JobModel, bool]:
    """Insert one durable job, resolving a concurrent unique-key winner."""
    if job_type not in SUPPORTED_JOB_TYPES:
        raise ValueError(f"Unsupported canonical job type: {job_type}")
    existing = db.query(JobModel).filter(JobModel.idempotency_key == idempotency_key).first()
    if existing:
        return existing, False
    job = JobModel(
        id=f"job-{uuid.uuid4().hex[:24]}",
        type=job_type,
        status="PENDING",
        progress=0.0,
        message=message,
        idempotency_key=idempotency_key,
        linked_entity=linked_entity,
        correlation_id=correlation_id or str(uuid.uuid4()),
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        winner = db.query(JobModel).filter(JobModel.idempotency_key == idempotency_key).first()
        if winner:
            return winner, False
        raise
    db.refresh(job)
    return job, True


def _run_persisted_job(job_id: str, job_type: str) -> bool:
    """Worker-side execution; safe to call from a process or a test harness."""
    if not claim_job(job_id):
        return False
    job = get_job(job_id)
    if not job or job.type != job_type:
        update_job_status(job_id, "FAILED", "Job type or job record is invalid")
        return False
    if not job.linked_entity:
        update_job_status(job_id, "FAILED", "Canonical job is missing its linked entity")
        return False
    stop_heartbeat = threading.Event()
    heartbeat = threading.Thread(
        target=lambda: _heartbeat(job_id, stop_heartbeat),
        name=f"job-lease-{job_id}",
        daemon=True,
    )
    heartbeat.start()
    try:
        if job_type == "INGEST_PROFILE":
            from src.services.job_runner import run_ingest_profile

            with Session(get_engine()) as db:
                version = db.get(DatasetVersionModel, job.linked_entity)
                if version:
                    target_dataset_id = version.dataset_id
                    run_ingest_profile(job.id, target_dataset_id, actor_role="WORKER", dataset_version_id=version.id)
                else:
                    run_ingest_profile(job.id, str(job.linked_entity), actor_role="WORKER")
        elif job_type in {"GRAPH1_EXECUTION", "GRAPH1_CONTINUATION"}:
            from src.services.graph1_workflow import execute_graph1_run

            asyncio.run(execute_graph1_run(str(job.linked_entity)))
        elif job_type == "ANALYSIS_GRAPH2_GRAPH3":
            from src.services.analysis_workflow import execute_analysis_run

            asyncio.run(execute_analysis_run(str(job.linked_entity)))
        else:
            raise ValueError(f"Unsupported canonical job type: {job_type}")
    except Exception as exc:
        logger.exception("Canonical job %s failed", job_id)
        update_job_status(job_id, "FAILED_RETRYABLE", str(exc)[:2000])
        stop_heartbeat.set()
        return False
    finally:
        stop_heartbeat.set()

    with Session(get_engine()) as db:
        job = db.get(JobModel, job_id)
        failed = False
        entity = None
        if job_type in {"GRAPH1_EXECUTION", "GRAPH1_CONTINUATION"}:
            entity = db.get(Graph1RunModel, job.linked_entity) if job else None
            failed = entity is None or entity.status == "FAILED"
        elif job_type == "ANALYSIS_GRAPH2_GRAPH3":
            entity = db.get(AnalysisRunModel, job.linked_entity) if job else None
            failed = entity is None or entity.status == "FAILED"
        if job:
            job.status = "FAILED_RETRYABLE" if failed else "SUCCEEDED"
            job.progress = 100.0 if not failed else job.progress
            job.message = "Worker execution failed" if failed else "Worker execution completed"
            job.error = getattr(entity, "error", None) if failed and entity is not None else (
                "Linked workflow entity was not found" if failed else None
            )
            db.commit()
    return not failed


def _heartbeat(job_id: str, stop: threading.Event) -> None:
    while not stop.wait(60):
        if not renew_job_lease(job_id):
            return


def dispatch_job(job_id: str, job_type: str) -> bool:
    """Dispatch using the same contract in local, test and production modes."""
    if job_type not in SUPPORTED_JOB_TYPES:
        return False
    settings = get_settings()
    # Tests execute through the worker entrypoint, never through FastAPI
    # BackgroundTasks.  This preserves deterministic fixtures while exercising
    # the same lease/idempotency path as a deployed worker.
    if settings.app_env == "test" or os.getenv("PYTEST_CURRENT_TEST") or os.getenv("WORKER_DISPATCH_MODE") == "inline":
        return _run_persisted_job(job_id, job_type)
    if dispatch_cloud_run_job(job_id, job_type):
        return True
    # Outside production a failed hand-off is almost always the developer having
    # no worker container: LOCAL_WORKER_URL defaults to the compose hostname
    # ``worker``, which does not resolve when the backend runs straight on the
    # host. Marking the job FAILED_RETRYABLE there left every import stuck at
    # "profiling" with nothing to retry it, so run it here instead. Production
    # keeps failing loudly, because there a missing worker is a real outage.
    if settings.app_env != "production":
        logger.warning("Worker dispatch failed for job %s; running it in-process.", job_id)
        return _run_persisted_job(job_id, job_type)
    return False


def dispatch_or_mark_failed(db: Session, job: JobModel) -> bool:
    """Dispatch after commit and make transport failure observable."""
    if dispatch_job(job.id, job.type):
        return True
    current = db.get(JobModel, job.id)
    if current:
        current.status = "FAILED_RETRYABLE"
        current.error = "Worker dispatch failed; job is eligible for retry."
        current.message = "Worker dispatch failed"
        db.commit()
    return False
