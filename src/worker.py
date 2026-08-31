import asyncio
import logging
import math
import os
import sys
import time

from sqlalchemy.orm import Session

# Add project root to path to ensure imports work
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import get_settings
from src.models.database import DatasetVersionModel, JobModel
from src.services.job_service import claim_job
from src.services.rule_store import get_engine, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ridepulse-worker")


class Unparseable:
    """A value ingestion refused, kept distinguishable from an empty cell.

    Returned rather than raised so the caller decides what a bad cell means -- fail
    the row, quarantine it, or count it -- while still being unable to mistake it for
    absent data. That distinction is the whole point: ``to_float`` used to answer
    ``None`` for both "the CSV had nothing here" and "the CSV had '12,50' and we gave
    up", so a parse failure was stored as an empty cell and the profiler later
    reported the resulting null rate as if it had come from the source.
    """

    __slots__ = ("raw",)

    def __init__(self, raw) -> None:
        self.raw = raw

    def __repr__(self) -> str:
        return f"Unparseable({self.raw!r})"

    def __bool__(self) -> bool:
        return False

    def __eq__(self, other) -> bool:
        return isinstance(other, Unparseable) and other.raw == self.raw

    def __hash__(self) -> int:
        return hash(("Unparseable", str(self.raw)))


def _is_blank(val) -> bool:
    return val is None or (isinstance(val, str) and not val.strip())


def to_float(val):
    if _is_blank(val):
        return None
    try:
        parsed = float(val)
    except (TypeError, ValueError):
        return Unparseable(val)
    # "nan", "inf" and anything that overflows -- "1e999" -- parse successfully and
    # are not data. Letting them through poisons every aggregate computed downstream,
    # and silently, because NaN propagates without raising.
    if math.isnan(parsed) or math.isinf(parsed):
        return Unparseable(val)
    return parsed


def to_int(val):
    if _is_blank(val):
        return None
    parsed = to_float(val)
    if parsed is None or isinstance(parsed, Unparseable):
        return parsed
    return int(parsed)


def to_str(val):
    if val is None:
        return None
    return str(val)


def run_ingest_profile(job_id: str, dataset_id: str):
    """
    Compatibility entrypoint for Docker Compose.

    The former implementation loaded a taxi manifest and deleted the shared
    ``trips_raw`` table.  It is intentionally disabled.  Compose jobs now
    delegate to the canonical versioned source runner, which requires an
    explicit immutable dataset version and cannot affect another dataset.
    """
    from src.models.database import DatasetVersionModel
    from src.services.job_runner import run_ingest_profile as run_canonical_ingest_profile

    engine = get_engine()
    with Session(engine) as session:
        job = session.query(JobModel).filter_by(id=job_id).first()
        if not job:
            raise RuntimeError(f"Job {job_id} not found")
        version_id = job.linked_entity if (job.linked_entity or "").startswith("dv-") else None
        version = session.get(DatasetVersionModel, version_id) if version_id else None
        if not version or version.dataset_id != dataset_id:
            raise RuntimeError("Legacy worker requires an explicit READY dataset version; use /workspaces/.../datasets/import")
    return run_canonical_ingest_profile(job_id, dataset_id, dataset_version_id=version.id)




async def run_propose_rules(job_id: str, dataset_id: str):
    """
    Worker handler for PROPOSE_RULES job type.
    Runs the LangGraph proposal graph to profile data and propose rules.
    """
    logger.info(f"Starting PROPOSE_RULES for dataset: {dataset_id} (job_id: {job_id})")
    from src.agents.graph import build_proposal_graph

    proposal_graph = build_proposal_graph()
    state = {
        "dataset_id": dataset_id,
        "rule_run_id": job_id,  # correlation ID/run_id matching job_id
        "metadata": {
            "connection_string": get_settings().database_url,
            "sampling_rate": 1.0,
        },
    }

    # Invoke proposal pipeline
    final_state = await proposal_graph.ainvoke(state)
    err = final_state.get("error")
    if err:
        if err == "AWAITING_SEMANTIC_REVIEW":
            logger.info("Proposal pipeline paused: AWAITING_SEMANTIC_REVIEW. Exiting worker cleanly.")
            from sqlalchemy.orm import Session

            from src.models.database import JobModel
            from src.services.rule_store import get_engine

            with Session(get_engine()) as session:
                db_job = session.query(JobModel).filter_by(id=job_id).first()
                if db_job:
                    db_job.status = "AWAITING_SEMANTIC_REVIEW"
                    db_job.error = None
                    session.commit()
            return
        raise Exception(f"Proposal pipeline failed: {err}")

    logger.info(f"PROPOSE_RULES job {job_id} completed successfully.")


def run_dq(job_id: str, dataset_id: str):
    """
    Worker handler for RUN_DQ job type.
    Compiles and executes approved DQ rules against trips_raw.
    """
    logger.info(f"Starting RUN_DQ for dataset: {dataset_id} (job_id: {job_id})")
    # Simulate DQ evaluation
    time.sleep(1)
    logger.info(f"RUN_DQ job {job_id} completed successfully.")


async def main():
    job_id = os.getenv("RUN_JOB_ID")
    job_type = os.getenv("RUN_JOB_TYPE")

    if not job_id or not job_type:
        logger.error("Environment variables RUN_JOB_ID and RUN_JOB_TYPE must be set.")
        sys.exit(1)

    logger.info(f"Initializing database and running job {job_id} of type {job_type}")
    init_db()

    engine = get_engine()
    linked_entity = None
    with Session(engine) as session:
        # Fetch the job
        job = session.query(JobModel).filter_by(id=job_id).first()
        if not job:
            logger.error(f"Job {job_id} not found in database.")
            sys.exit(1)
        if job.linked_entity:
            linked_entity = job.linked_entity
            if linked_entity.startswith("dv-"):
                version = session.get(DatasetVersionModel, linked_entity)
                if version:
                    linked_entity = version.dataset_id
        elif job_type in {"PROPOSE_RULES", "RUN_DQ"}:
            # Explicitly legacy-only default. Canonical versioned jobs must
            # always carry an immutable linked entity and are rejected below.
            linked_entity = "yellow_tripdata"

    # Canonical workflows share one durable dispatch/lease contract.  The
    # helper claims the job and reloads the linked entity itself, so the API
    # never has to serialize workflow state into a process invocation.
    from src.services.job_dispatch import SUPPORTED_JOB_TYPES, _run_persisted_job
    if job_type in SUPPORTED_JOB_TYPES:
        # ``main`` itself runs under asyncio because legacy proposal jobs use
        # an async graph.  The durable canonical runner owns its own event
        # loop, so execute it in a worker thread instead of nesting
        # ``asyncio.run`` inside the current loop.
        if not await asyncio.to_thread(_run_persisted_job, job_id, job_type):
            sys.exit(1)
        return

    # Legacy compatibility jobs retain the older handlers below.
    if not claim_job(job_id):
        logger.info(f"Job {job_id} could not be claimed (already running or completed). Exit.")
        sys.exit(0)

    # Run the corresponding job logic
    start_time = time.time()
    try:
        if not linked_entity:
            raise ValueError("Legacy job is missing its linked dataset entity")
        if job_type == "INGEST_PROFILE":
            # Run ingestion and profiling synchronously
            run_ingest_profile(job_id, linked_entity)
        elif job_type == "PROPOSE_RULES":
            # Run proposals using async graph
            await run_propose_rules(job_id, linked_entity)
        elif job_type == "RUN_DQ":
            run_dq(job_id, linked_entity)
        else:
            raise ValueError(f"Unknown job type: {job_type}")

        # Update job status on success
        with Session(engine) as session:
            db_job = session.query(JobModel).filter_by(id=job_id).first()
            if db_job:
                if db_job.status != "AWAITING_SEMANTIC_REVIEW":
                    db_job.status = "SUCCEEDED"
                    db_job.error = None
                session.commit()
        logger.info(f"Job {job_id} completed successfully in {time.time() - start_time:.2f} seconds.")

    except Exception as e:
        logger.error(f"Job {job_id} failed with error: {e}", exc_info=True)
        with Session(engine) as session:
            db_job = session.query(JobModel).filter_by(id=job_id).first()
            if db_job:
                db_job.status = "FAILED"
                db_job.error = str(e)
                session.commit()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
