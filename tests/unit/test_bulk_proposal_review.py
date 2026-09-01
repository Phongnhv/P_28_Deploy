"""Bulk approve/reject over a dataset's rule proposals.

Deciding forty-odd rules one PATCH at a time is slow and non-atomic: a failure
halfway through leaves the queue in a state nobody chose. These cover the whole
decision applied in one transaction.
"""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.api.routes import _apply_proposal_approval, _apply_proposal_rejection, _proposal_scope_query
from src.models.database import (
    Base,
    RuleConfigurationModel,
    RuleProposalModel,
    RuleVersionModel,
)
from src.time_utils import utc_now

DATASET_ID = "ds-bulk"

RULE_SPEC = json.dumps({"type": "NOT_NULL", "column": "fare_amount"})


def make_proposal(
    db: Session, proposal_id: str, status: str, workflow_run_id: str | None = None
) -> RuleProposalModel:
    proposal = RuleProposalModel(
        id=proposal_id,
        dataset_id=DATASET_ID,
        workflow_run_id=workflow_run_id,
        title=f"Rule {proposal_id}",
        description="",
        severity="HIGH",
        status=status,
        rule_type="NOT_NULL",
        model_name="test-model",
        rule_spec=RULE_SPEC,
        evidence_refs=json.dumps([]),
        evidence_summary="",
        confidence=0.85,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(proposal)
    db.commit()
    return proposal


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_approving_creates_the_rule_version_that_makes_it_executable(db: Session):
    proposal = make_proposal(db, "p-1", "PROPOSED")

    _apply_proposal_approval(db, proposal)
    db.commit()

    assert proposal.status == "APPROVED"
    version = db.get(RuleVersionModel, "rv_p-1")
    assert version is not None and version.status == "APPROVED"
    # Without a configuration row the approved rule has no execution settings
    # and never runs, so approval has to create one.
    configuration = db.query(RuleConfigurationModel).filter_by(rule_proposal_id="p-1").first()
    assert configuration is not None
    assert configuration.execution_status == "ACTIVE"


def test_approving_twice_does_not_duplicate_the_rule_version(db: Session):
    proposal = make_proposal(db, "p-2", "PROPOSED")

    _apply_proposal_approval(db, proposal)
    db.commit()
    _apply_proposal_approval(db, proposal)
    db.commit()

    versions = db.query(RuleVersionModel).filter_by(rule_proposal_id="p-2").all()
    assert len(versions) == 1


def test_rejecting_withdraws_the_authorised_version(db: Session):
    proposal = make_proposal(db, "p-3", "PROPOSED")
    _apply_proposal_approval(db, proposal)
    db.commit()

    _apply_proposal_rejection(db, proposal)
    db.commit()

    assert proposal.status == "REJECTED"
    # A rejected rule that keeps its version would still be picked up by a run.
    assert db.get(RuleVersionModel, "rv_p-3") is None


def test_rejecting_a_never_approved_proposal_is_not_an_error(db: Session):
    proposal = make_proposal(db, "p-4", "PROPOSED")

    _apply_proposal_rejection(db, proposal)
    db.commit()

    assert proposal.status == "REJECTED"
    assert db.get(RuleVersionModel, "rv_p-4") is None


def test_workflow_scope_excludes_other_generations_and_legacy_rows(db: Session):
    for index in range(4):
        make_proposal(db, f"current-{index}", "PROPOSED", "workflow-current")
    for index in range(28):
        make_proposal(db, f"historical-{index}", "PROPOSED", "workflow-old")
    make_proposal(db, "legacy-unowned", "PROPOSED")

    targets = _proposal_scope_query(db, DATASET_ID, "workflow-current").all()
    for proposal in targets:
        _apply_proposal_approval(db, proposal)
    db.commit()

    assert len(targets) == 4
    assert {proposal.status for proposal in targets} == {"APPROVED"}
    assert _proposal_scope_query(db, DATASET_ID, "workflow-current").count() == 4
    assert _proposal_scope_query(db, DATASET_ID, "workflow-old").filter_by(status="PROPOSED").count() == 28
    assert db.get(RuleProposalModel, "legacy-unowned").status == "PROPOSED"
