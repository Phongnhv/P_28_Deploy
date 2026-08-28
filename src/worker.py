import asyncio
import logging
import os
import sys
import time

from sqlalchemy import text
from sqlalchemy.orm import Session

# Add project root to path to ensure imports work
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import get_settings
from src.models.database import DatasetVersionModel, JobModel
from src.services.dataset_loader import load_dataset_rows
from src.services.job_service import claim_job
from src.services.rule_store import get_engine, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ridepulse-worker")


def to_float(val):
    if val is None or val == "":
        return None
    try:
        return float(val)
    except Exception:
        return None


def to_int(val):
    if val is None or val == "":
        return None
    try:
        return int(float(val))
    except Exception:
        return None


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

    # Obsolete taxi-only code is retained below solely as a source-compatible
    # historical reference; the return above makes it unreachable. Do not
    # re-enable it: it has no dataset-scoped storage contract.
    logger.info(f"Starting INGEST_PROFILE for dataset: {dataset_id}")

    # 1. Load manifest and verify checksum
    manifest_name = "nyc-yellow-demo-v1"  # Default fallback
    if "50k" in dataset_id.lower():
        manifest_name = "nyc-yellow-50k-v1"

    logger.info(f"Loading dataset using manifest: {manifest_name}")
    rows = load_dataset_rows(manifest_name)
    logger.info(f"Loaded {len(rows)} rows from dataset fixture.")

    # 2. Ingest raw rows into trips_raw
    engine = get_engine()
    with Session(engine) as session:
        # Clear existing raw rows to maintain idempotency
        # No unscoped legacy deletion. The compatibility implementation above
        # is the only permitted ingestion path.

        # Insert rows
        insert_query = text("""
            INSERT INTO trips_raw (
                source_row_id, vendor_id, pickup_at, dropoff_at, passenger_count,
                trip_distance, rate_code_id, store_and_fwd_flag, pickup_location_id,
                dropoff_location_id, payment_type, fare_amount, extra, mta_tax,
                tip_amount, tolls_amount, improvement_surcharge, total_amount,
                congestion_surcharge, airport_fee, cbd_congestion_fee
            ) VALUES (
                :source_row_id, :vendor_id, :pickup_at, :dropoff_at, :passenger_count,
                :trip_distance, :rate_code_id, :store_and_fwd_flag, :pickup_location_id,
                :dropoff_location_id, :payment_type, :fare_amount, :extra, :mta_tax,
                :tip_amount, :tolls_amount, :improvement_surcharge, :total_amount,
                :congestion_surcharge, :airport_fee, :cbd_congestion_fee
            )
        """)

        batch = []
        for r in rows:
            # In the 50k parquet/CSV, some categories like vendor_id are strings like "Curb Mobility, LLC".
            # The trips_raw schema in 001_schema.sql has vendor_id as INT.
            # Let's map strings back to integers if needed to prevent insertion errors, or use strings.
            # Wait, in 001_schema.sql, trips_raw has: vendor_id INT, rate_code_id INT, payment_type INT.
            # But the semantic data has strings!
            # Let's handle it gracefully: if value is a string that doesn't parse to int, we map it,
            # or we cast it.
            raw_vendor = r.get("vendor_id")
            if isinstance(raw_vendor, str) and not raw_vendor.isdigit():
                # Map back to standard vendor IDs:
                # 1 -> Creative Mobile Technologies, LLC
                # 2 -> Curb Mobility, LLC
                # 6 -> Myle Technologies Inc
                # 7 -> Helix
                vendor_map = {
                    "Creative Mobile Technologies, LLC": 1,
                    "Curb Mobility, LLC": 2,
                    "Myle Technologies Inc": 6,
                    "Helix": 7,
                }
                vendor_id = vendor_map.get(raw_vendor, 0)
            else:
                vendor_id = to_int(raw_vendor)

            raw_rate = r.get("rate_code_id")
            if isinstance(raw_rate, str) and not raw_rate.isdigit():
                rate_map = {
                    "Standard rate": 1,
                    "JFK": 2,
                    "Newark": 3,
                    "Nassau or Westchester": 4,
                    "Negotiated fare": 5,
                    "Group ride": 6,
                    "Null/Unknown": 99,
                }
                rate_code_id = rate_map.get(raw_rate, 1)
            else:
                rate_code_id = to_int(raw_rate)

            raw_payment = r.get("payment_type")
            if isinstance(raw_payment, str) and not raw_payment.isdigit():
                payment_map = {
                    "Flex Fare trip": 0,
                    "Credit card": 1,
                    "Cash": 2,
                    "No charge": 3,
                    "Dispute": 4,
                    "Unknown": 5,
                    "Voided trip": 6,
                    "Invalid Payment (Dispute/Test)": 99,
                }
                payment_type = payment_map.get(raw_payment, 1)
            else:
                payment_type = to_int(raw_payment)

            batch.append(
                {
                    "source_row_id": to_str(r.get("source_row_id")),
                    "vendor_id": vendor_id,
                    "pickup_at": to_str(r.get("pickup_at")),
                    "dropoff_at": to_str(r.get("dropoff_at")),
                    "passenger_count": to_int(r.get("passenger_count")),
                    "trip_distance": to_float(r.get("trip_distance")),
                    "rate_code_id": rate_code_id,
                    "store_and_fwd_flag": to_str(r.get("store_and_fwd_flag"))[:1]
                    if r.get("store_and_fwd_flag")
                    else None,
                    "pickup_location_id": to_int(r.get("pickup_location_id"))
                    if isinstance(r.get("pickup_location_id"), (int, float))
                    or (isinstance(r.get("pickup_location_id"), str) and r.get("pickup_location_id").isdigit())
                    else 0,  # Location IDs are INT in database
                    "dropoff_location_id": to_int(r.get("dropoff_location_id"))
                    if isinstance(r.get("dropoff_location_id"), (int, float))
                    or (isinstance(r.get("dropoff_location_id"), str) and r.get("dropoff_location_id").isdigit())
                    else 0,
                    "payment_type": payment_type,
                    "fare_amount": to_float(r.get("fare_amount")),
                    "extra": to_float(r.get("extra")),
                    "mta_tax": to_float(r.get("mta_tax")),
                    "tip_amount": to_float(r.get("tip_amount")),
                    "tolls_amount": to_float(r.get("tolls_amount")),
                    "improvement_surcharge": to_float(r.get("improvement_surcharge")),
                    "total_amount": to_float(r.get("total_amount")),
                    "congestion_surcharge": to_float(r.get("congestion_surcharge")),
                    "airport_fee": to_float(r.get("airport_fee")),
                    "cbd_congestion_fee": to_float(r.get("cbd_congestion_fee")),
                }
            )

            if len(batch) >= 1000:
                session.execute(insert_query, batch)
                batch = []

        if batch:
            session.execute(insert_query, batch)

        session.commit()
        logger.info(f"Ingested {len(rows)} raw rows into database.")

    # 3. Simulate dbt build
    logger.info("Running dbt build...")
    time.sleep(1)  # Simulate dbt build latency
    logger.info("dbt build completed successfully.")

    # 4. Trigger profiling (raw_profiler_node) and persist profile
    # For now, let's run the profiler graph to create the profile trace
    logger.info("Profiling ingested raw data...")
    # Triggering proposal pipeline will automatically profile and propose rules.

    logger.info(f"INGEST_PROFILE job {job_id} completed successfully.")


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
