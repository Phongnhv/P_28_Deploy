import json

import pytest
from sqlalchemy.orm import Session

from src.models.database import ColumnProfileModel, DatasetModel, JobModel, ProfileModel, RuleProposalModel
from src.services.dashboard_agent_workflow import (
    AgentWorkflowError,
    _build_dashboard_rule_candidates,
    _normalise_graph_rules,
    build_proposal_evidence,
    generate_dashboard_proposals,
)
from src.services.job_runner import run_propose_rules
from src.services.rule_store import get_engine

DATASET_ID = "dataset-nyc-yellow-taxi-50k"


def seed_completed_profile() -> None:
    with Session(get_engine()) as session:
        dataset = session.query(DatasetModel).filter(DatasetModel.id == DATASET_ID).first()
        assert dataset is not None
        dataset.status = "PROFILE_READY"
        session.add(
            ProfileModel(
                dataset_id=DATASET_ID,
                row_count=100,
                completeness_score=99.0,
                validity_score=98.0,
                duplicate_rate=1.0,
                evidence_keys=json.dumps(["profile.row_count"]),
            )
        )
        for name, data_type, null_rate, distinct_count, min_value, max_value, sample_value in [
            ("source_row_id", "string", 0.0, 100, None, None, "private-row-id"),
            ("vendor_id", "string", 0.0, 100, None, None, "private-vendor"),
            ("trip_distance", "float", 0.01, 90, -1.0, 80.0, "9999.99"),
            ("payment_type", "string", 0.0, 6, None, None, "secret-payment"),
            ("pickup_at", "string", 0.0, 100, None, None, "2025-01-01T01:02:03"),
            ("dropoff_at", "string", 0.0, 100, None, None, "2025-01-01T01:13:03"),
            ("passenger_count", "integer", 0.0, 8, 0.0, 8.0, "4"),
        ]:
            session.add(
                ColumnProfileModel(
                    profile_dataset_id=DATASET_ID,
                    name=name,
                    data_type=data_type,
                    null_rate=null_rate,
                    distinct_count=distinct_count,
                    min_value=min_value,
                    max_value=max_value,
                    sample_value=sample_value,
                )
            )
        session.commit()


def test_evidence_allow_list_excludes_raw_samples_and_identifiers():
    seed_completed_profile()
    with Session(get_engine()) as session:
        evidence = build_proposal_evidence(session, DATASET_ID)

    serialized = evidence.model_dump_json()
    assert "private-row-id" not in serialized
    assert "private-vendor" not in serialized
    assert "secret-payment" not in serialized
    assert "2025-01-01T01:02:03" not in serialized
    assert "source_row_id" not in serialized
    assert all("sample" not in column for column in evidence.model_dump()["columns"])

    digest = evidence.to_agent_digest()["source_rows"]
    assert digest["dashboard_candidate_mode"] is True
    assert "private-vendor" not in json.dumps(digest)
    assert {candidate["rule_type"] for candidate in digest["dashboard_rule_candidates"]} == {
        "NOT_NULL",
        "RANGE",
        "ACCEPTED_VALUES",
        "CROSS_FIELD_COMPARISON",
    }


def test_dashboard_candidates_are_diverse_and_use_only_aggregate_evidence():
    seed_completed_profile()
    with Session(get_engine()) as session:
        evidence = build_proposal_evidence(session, DATASET_ID)

    candidates = _build_dashboard_rule_candidates(evidence)
    assert [candidate.dashboard_rule_type for candidate in candidates] == [
        "not_null",
        "numeric_range",
        "accepted_values",
        "cross_field_comparison",
    ]
    assert len({candidate.dashboard_rule_type for candidate in candidates}) == len(candidates)
    assert all(set(candidate.evidence_refs).issubset(evidence.evidence_keys) for candidate in candidates)


def test_graph_normalizer_rejects_parameter_drift_and_duplicate_categories():
    seed_completed_profile()
    with Session(get_engine()) as session:
        evidence = build_proposal_evidence(session, DATASET_ID)

    raw = [
        {
            "rule_type": "NOT_NULL", "column": "vendor_id", "parameters": {},
            "confidence_score": 0.95, "severity": "HIGH",
            "rule_description": "Vendor ID must be populated.", "ai_reasoning": "Aggregate completeness is stable.",
        },
        {
            "rule_type": "NOT_NULL", "column": "vendor_id", "parameters": {},
            "confidence_score": 0.90, "severity": "HIGH",
            "rule_description": "Duplicate not-null proposal.", "ai_reasoning": "Must be deduplicated.",
        },
        {
            "rule_type": "RANGE", "column": "trip_distance", "parameters": {"min": -10.0},
            "confidence_score": 0.99, "severity": "HIGH",
            "rule_description": "Invented threshold.", "ai_reasoning": "Must be rejected.",
        },
        {
            "rule_type": "RANGE", "column": "trip_distance", "parameters": {"min": 0.0, "max": 80.0},
            "confidence_score": 0.85, "severity": "HIGH",
            "rule_description": "Trip distance must be non-negative.", "ai_reasoning": "Aggregate minimum is negative.",
        },
        {
            "rule_type": "ACCEPTED_VALUES", "column": "payment_type",
            "parameters": {"accepted_values": ["1", "2", "3", "4", "5", "6"]},
            "confidence_score": 0.80, "severity": "MEDIUM",
            "rule_description": "Payment code must be governed.", "ai_reasoning": "The governed code set has six values.",
        },
    ]

    proposals = _normalise_graph_rules(raw, evidence)
    assert [proposal.rule_type for proposal in proposals] == [
        "not_null",
        "numeric_range",
        "accepted_values",
    ]


def test_mock_mode_returns_dashboard_supported_proposals():
    seed_completed_profile()
    with Session(get_engine()) as session:
        proposals = generate_dashboard_proposals(session, DATASET_ID)

    assert len(proposals) == 5
    assert {proposal.rule_type for proposal in proposals} == {
        "not_null",
        "numeric_range",
        "accepted_values",
        "cross_field_comparison",
        "duplicate_fingerprint",
    }
    assert all(proposal.model_name == "agent-mock-v1" for proposal in proposals)


def test_graph_mode_normalizes_only_evidence_backed_dashboard_rules(monkeypatch):
    seed_completed_profile()
    from src.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "agent_mode", "graph")
    monkeypatch.setattr(
        "src.services.dashboard_agent_workflow._invoke_dashboard_proposal_graph",
        lambda _evidence: [
            {
                "rule_type": "RANGE",
                "column": "trip_distance",
                "parameters": {"min": 0.0},
                "confidence_score": 0.9,
                "severity": "HIGH",
                "rule_description": "Trip distance must be non-negative.",
                "ai_reasoning": "Aggregate minimum is negative.",
            },
            {
                "rule_type": "CROSS_FIELD_COMPARISON",
                "column": "pickup_at",
                "parameters": {"target_column": "dropoff_at", "operator": "<="},
                "confidence_score": 0.8,
                "severity": "MEDIUM",
                "rule_description": "Pickup should precede dropoff.",
                "ai_reasoning": "Both values are aggregate datetime fields.",
            },
            {
                "rule_type": "REGEX_FORMAT",
                "column": "vendor_id",
                "parameters": {"regex": ".*"},
                "confidence_score": 0.9,
                "severity": "HIGH",
                "rule_description": "Unsupported output.",
                "ai_reasoning": "Must be rejected.",
            },
        ],
    )
    with Session(get_engine()) as session:
        proposals = generate_dashboard_proposals(session, DATASET_ID)

    assert [proposal.rule_type for proposal in proposals] == [
        "numeric_range",
        "cross_field_comparison",
        "not_null",
        "accepted_values",
    ]
    assert [proposal.model_name for proposal in proposals] == [
        "langgraph-openai",
        "langgraph-openai",
        "agent-policy-fallback-v1",
        "agent-policy-fallback-v1",
    ]


def test_graph_mode_uses_dashboard_graph_with_aggregate_digest(monkeypatch):
    seed_completed_profile()
    from src.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "agent_mode", "graph")

    async def fake_rule_proposer(state):
        digest = state["dataset_profile_digest"]
        serialized_digest = json.dumps(digest)
        assert "private-vendor" not in serialized_digest
        assert "secret-payment" not in serialized_digest
        assert "source_row_id" not in serialized_digest
        assert state["metadata"]["max_retries"] == 0
        dashboard_digest = digest["source_rows"]
        assert dashboard_digest["dashboard_candidate_mode"] is True
        assert len(dashboard_digest["dashboard_rule_candidates"]) == 4
        return {
            "proposed_rules": [
                {
                    "rule_type": "NOT_NULL",
                    "column": "vendor_id",
                    "parameters": {},
                    "confidence_score": 0.9,
                    "severity": "HIGH",
                    "rule_description": "Vendor ID must not be null.",
                    "ai_reasoning": "Aggregate null rate is zero.",
                },
                {
                    "rule_type": "CROSS_FIELD_COMPARISON",
                    "column": "pickup_at",
                    "parameters": {"target_column": "dropoff_at", "operator": "<="},
                    "confidence_score": 0.8,
                    "severity": "MEDIUM",
                    "rule_description": "Pickup should precede dropoff.",
                    "ai_reasoning": "Both values are timestamp fields.",
                },
            ],
            "rule_proposal_errors": [],
        }

    monkeypatch.setattr("src.agents.nodes.rule_proposer_node.rule_proposer_node", fake_rule_proposer)
    with Session(get_engine()) as session:
        proposals = generate_dashboard_proposals(session, DATASET_ID)

    assert [proposal.rule_type for proposal in proposals] == [
        "not_null",
        "cross_field_comparison",
        "numeric_range",
        "accepted_values",
    ]


def test_proposal_job_persists_graph_adapter_output(monkeypatch):
    seed_completed_profile()
    from src.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "agent_mode", "graph")
    monkeypatch.setattr(
        "src.services.dashboard_agent_workflow._invoke_dashboard_proposal_graph",
        lambda _evidence: [
            {
                "rule_type": "NOT_NULL",
                "column": "vendor_id",
                "parameters": {},
                "confidence_score": 0.9,
                "severity": "HIGH",
                "rule_description": "Vendor ID must not be null.",
                "ai_reasoning": "Aggregate null rate is zero.",
            },
            {
                "rule_type": "CROSS_FIELD_COMPARISON",
                "column": "pickup_at",
                "parameters": {"target_column": "dropoff_at", "operator": "<="},
                "confidence_score": 0.8,
                "severity": "MEDIUM",
                "rule_description": "Pickup should precede dropoff.",
                "ai_reasoning": "Both values are timestamp fields.",
            },
        ],
    )
    with Session(get_engine()) as session:
        session.add(
            JobModel(
                id="proposal-job",
                type="PROPOSE_RULES",
                status="PENDING",
                progress=0.0,
                idempotency_key="proposal-job-key",
                attempt_count=1,
            )
        )
        session.commit()

    run_propose_rules("proposal-job", DATASET_ID)

    with Session(get_engine()) as session:
        job = session.get(JobModel, "proposal-job")
        proposals = session.query(RuleProposalModel).filter(RuleProposalModel.dataset_id == DATASET_ID).all()
    assert job is not None and job.status == "SUCCEEDED"
    assert len(proposals) == 4
    assert {proposal.model_name for proposal in proposals} == {
        "langgraph-openai",
        "agent-policy-fallback-v1",
    }


def test_graph_mode_rejects_fabricated_or_unsupported_rules(monkeypatch):
    seed_completed_profile()
    from src.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "agent_mode", "graph")
    monkeypatch.setattr(
        "src.services.dashboard_agent_workflow._invoke_dashboard_proposal_graph",
        lambda _evidence: [
            {
                "rule_type": "RANGE",
                "column": "unknown_column",
                "parameters": {"min": 0},
                "confidence_score": 0.9,
                "severity": "HIGH",
                "rule_description": "Fabricated column.",
                "ai_reasoning": "No evidence exists.",
            }
        ],
    )
    with Session(get_engine()) as session:
        with pytest.raises(AgentWorkflowError, match="enough valid"):
            generate_dashboard_proposals(session, DATASET_ID)
