import asyncio
import logging
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine

# Add project root to path
sys.path.append(str(Path(__file__).resolve().parents[1]))

import src.services.rule_store as rule_store_module
from src.agents.graph import build_proposal_graph
from src.services.rule_store import init_db, list_rules

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("run_taxi_proposal_test")


async def run_test():
    # 1. Setup SQLite
    logger.info("Initializing SQLite test database for NYC Taxi dataset...")
    db_file = Path("output/test_taxi_proposal.db")
    if db_file.exists():
        try:
            db_file.unlink()
        except Exception:
            pass

    db_url = f"sqlite:///{db_file.as_posix()}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})

    # Override engine in rule_store
    rule_store_module._engine = engine
    init_db()

    # 2. Load parquet
    parquet_path = Path("data/yellow_tripdata_2025/semantic_data/yellow_tripdata_2025_semantic_50k.parquet")
    logger.info(f"Loading parquet from {parquet_path}...")
    df = pd.read_parquet(parquet_path)

    # Sample 1,000 rows to speed up the profiling in test
    logger.info("Sampling 1,000 rows for fast execution...")
    df_sample = df.head(1000)

    # Ingest into SQLite trips_raw table
    logger.info("Ingesting taxi data into SQLite...")
    df_sample.to_sql("trips_raw", con=engine, if_exists="replace", index=False)

    logger.info("Taxi data ingested. Running proposal graph...")

    # 3. Invoke graph
    proposal_graph = build_proposal_graph()

    initial_state = {
        "dataset_id": "dataset-nyc-yellow-taxi-50k",
        "target_tables": ["trips_raw"],
        "metadata": {
            "connection_string": db_url,
            "sampling_rate": 1.0,
            "auto_confirm_semantic": True,  # Automatically bypass the HITL gate for this script
            "domain_hint": "NYC TLC Yellow Taxi Trip Records dataset containing trip distance, locations, passenger count, fare amounts, and total transaction amount.",
        },
    }

    final_state = await proposal_graph.ainvoke(initial_state)

    if "error" in final_state and final_state["error"]:
        logger.error(f"Graph execution failed with error: {final_state['error']}")
        sys.exit(1)

    run_id = final_state.get("rule_run_id")
    proposed_rules = list_rules(run_id)

    logger.info("=" * 75)
    logger.info("NYC TAXI PROPOSAL TEST COMPLETED SUCCESSFULLY!")
    logger.info(f"Run ID: {run_id}")
    logger.info(f"Total Rules Proposed: {len(proposed_rules)}")
    logger.info("=" * 75)

    for i, r in enumerate(proposed_rules, 1):
        logger.info(f"[{i}] Rule: {r.get('rule_id')} ({r.get('dimension')})")
        logger.info(f"    Name: {r.get('rule_name')}")
        logger.info(f"    Description: {r.get('rule_description')}")
        logger.info(f"    Business Rationale: {r.get('business_rationale')}")
        logger.info(f"    AI Reasoning: {r.get('ai_reasoning')}")


if __name__ == "__main__":
    asyncio.run(run_test())
