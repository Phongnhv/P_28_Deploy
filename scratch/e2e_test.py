import asyncio
import os
import uuid
import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).resolve().parents[1]))

# Delete existing SQLite file to ensure clean database schema initialization
db_file = Path("e2e_test.db")
if db_file.exists():
    print("Deleting old e2e_test.db to refresh schema...")
    try:
        db_file.unlink()
        Path("e2e_test.db-wal").unlink(missing_ok=True)
        Path("e2e_test.db-shm").unlink(missing_ok=True)
    except Exception as e:
        print(f"Warning: could not delete database files: {e}")

# Force environment database URL to the clean sqlite test file
os.environ["DATABASE_URL"] = "sqlite:///e2e_test.db"
os.environ["DISABLE_TRACING"] = "true"

from src.services.rule_store import (
    init_db,
    create_run,
    save_proposed_rules,
    review_rule,
    publish_approved_rules,
    get_active_rules,
)
from src.agents.graph import run_execution_graph

async def main():
    print("1. Initializing DB...")
    init_db()
    
    dataset_id = "dataset-nyc-yellow-taxi-50k"
    proposal_run_id = f"prop-{uuid.uuid4().hex[:8]}"
    
    print(f"2. Creating Rule Proposal Run: {proposal_run_id}...")
    create_run(proposal_run_id, dataset_id)
    
    # Define a simple rule targeting "source_rows" table (which exists in seed catalog)
    rules = [
        {
            "rule_id": f"{dataset_id}.source_rows.fare_amount.NOT_NULL",
            "table_name": "source_rows",
            "column": "fare_amount",
            "rule_type": "NOT_NULL",
            "parameters": {},
            "confidence_score": 1.0,
            "severity": "CRITICAL",
            "dimension": "COMPLETENESS",
            "rule_description": "Verify fare_amount is not null",
            "ai_reasoning": "Standard verification",
            "status": "PENDING"
        }
    ]
    
    print("3. Saving Proposed Rules...")
    save_proposed_rules(proposal_run_id, dataset_id, rules)
    
    print("4. Approving Rule...")
    review_rule(proposal_run_id, f"{dataset_id}.source_rows.fare_amount.NOT_NULL", "APPROVED")
    
    print("5. Publishing Approved Rules...")
    publish_approved_rules(proposal_run_id)
    
    print("6. Verifying Active Rules...")
    active_rules = get_active_rules(dataset_id)
    print(f"   Found {len(active_rules)} active rules.")
    assert len(active_rules) >= 1, "Failed to publish rule!"
    
    print("7. Running E2E Execution Graph...")
    result = await run_execution_graph(dataset_id=dataset_id)
    print("E2E Graph finished successfully!")
    print(f"Test Run ID: {result['test_run_id']}")
    print(f"Results Count: {len(result['results'])}")
    print(f"Results: {result['results']}")

if __name__ == "__main__":
    asyncio.run(main())
