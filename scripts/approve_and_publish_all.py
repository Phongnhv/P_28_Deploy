import logging

from sqlalchemy.orm import Session

from src.models.database import JobModel, RuleProposalModel
from src.services.rule_store import ProposedRuleModel, get_engine, publish_approved_rules

logging.basicConfig(level=logging.INFO)
engine = get_engine()

print("🚀 Starting auto-approve and publish script...")

with Session(engine) as session:
    # 1. Find the latest run_id from ProposedRuleModel
    latest_proposed = session.query(ProposedRuleModel).order_by(ProposedRuleModel.created_at.desc()).first()
    if not latest_proposed:
        print("❌ No proposed rules found in the database. Please run Graph 1 first.")
        exit(1)

    run_id = latest_proposed.run_id
    dataset_id = latest_proposed.dataset_id
    print(f"📌 Latest Run ID: {run_id}")
    print(f"📌 Dataset ID: {dataset_id}")

    # 2. Update status of rules in legacy_agent schema (ProposedRuleModel) to APPROVED
    legacy_updated = (
        session.query(ProposedRuleModel)
        .filter(ProposedRuleModel.run_id == run_id, ProposedRuleModel.status == "PENDING")
        .update({"status": "APPROVED"}, synchronize_session=False)
    )

    # 3. Update status of rules in rules schema (RuleProposalModel) to APPROVED
    proposals_updated = (
        session.query(RuleProposalModel)
        .filter(RuleProposalModel.dataset_id == dataset_id, RuleProposalModel.status == "PROPOSED")
        .update({"status": "APPROVED"}, synchronize_session=False)
    )

    # 4. Ensure a JobModel exists for this run_id (to satisfy publish_approved_rules constraint)
    job = session.get(JobModel, run_id)
    if not job:
        print(f"🔧 Creating missing JobModel for run_id: {run_id}")
        job = JobModel(
            id=run_id,
            type="PROPOSE_RULES",
            status="DONE",
            progress=1.0,
            idempotency_key=f"propose-run-{run_id}",
            linked_entity=dataset_id,
        )
        session.add(job)

    session.commit()
    print(f"✅ Approved {legacy_updated} rules in legacy_agent.proposed_rules")
    print(f"✅ Approved {proposals_updated} rules in rules.rule_proposals")

# 5. Publish approved rules to active_rules
published = publish_approved_rules(run_id)
print(f"🎉 Successfully published {published} rules to Active Ruleset (legacy_agent.active_rules)!")
