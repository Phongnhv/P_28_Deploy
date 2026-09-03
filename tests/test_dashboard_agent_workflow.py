import json

import pytest
from sqlalchemy.orm import Session

from src.models.database import ColumnProfileModel, DatasetModel, JobModel, ProfileModel, RuleProposalModel
from src.services.dashboard_agent_workflow import (
    AgentWorkflowError,
    _build_dashboard_rule_candidates,
    _normalise_graph_rules,
    build_proposal_evidence,
    generate_dashboard_policy_fallback_proposals,
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
                cross_field_metrics_json=json.dumps(
                    [
                        {
                            "left_column": "pickup_at",
                            "operator": "<=",
                            "right_column": "dropoff_at",
                            "checked_count": 100,
                            "violation_count": 3,
                            "violation_rate": 0.03,
                        }
                    ]
                ),
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
                    non_null_count=round(100 * (1 - null_rate)),
                    negative_rate=0.01
                    if name == "trip_distance"
                    else (0.0 if data_type in {"integer", "float"} else None),
                    quantiles_json=(
                        json.dumps({"p05": 0.1, "p25": 1.0, "p50": 2.5, "p75": 5.0, "p95": 12.0})
                        if data_type in {"integer", "float"}
                        else "{}"
                    ),
                    out_of_domain_rate=0.02 if name == "payment_type" else None,
                    full_distinct_count=distinct_count,
                    uniqueness_rate=distinct_count / max(1, round(100 * (1 - null_rate))),
                    is_unique_full_table=null_rate == 0.0 and distinct_count == 100,
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
    by_name = {column.name: column for column in evidence.columns}
    assert by_name["trip_distance"].negative_rate == 0.01
    assert by_name["trip_distance"].quantiles["p50"] == 2.5
    assert by_name["payment_type"].out_of_domain_rate == 0.02
    assert by_name["vendor_id"].full_distinct_count == 100
    assert by_name["vendor_id"].is_unique_full_table is True
    assert evidence.cross_field_metrics[0].violation_rate == 0.03

    digest = evidence.to_agent_digest()[evidence.dataset_id]
    assert digest["dashboard_candidate_mode"] is True
    assert "private-vendor" not in json.dumps(digest)
    assert {candidate["rule_type"] for candidate in digest["dashboard_rule_candidates"]} == {
        "NOT_NULL",
        "RANGE",
        "ACCEPTED_VALUES",
        "CROSS_FIELD_COMPARISON",
        "UNIQUE",
    }
    digest_columns = {column["name"]: column for column in digest["columns"]}
    assert digest_columns["trip_distance"]["negative_pct"] == 1.0
    assert digest_columns["trip_distance"]["typical_range"] == [0.1, 12.0]
    assert digest_columns["payment_type"]["out_of_domain_pct"] == 2.0
    assert digest["cross_column_hints"][0]["violation_rate"] == 0.03


def test_dashboard_candidates_are_diverse_and_use_only_aggregate_evidence():
    seed_completed_profile()
    with Session(get_engine()) as session:
        evidence = build_proposal_evidence(session, DATASET_ID)

    candidates = _build_dashboard_rule_candidates(evidence)
    assert {candidate.dashboard_rule_type for candidate in candidates} == {
        "numeric_range",
        "cross_field_comparison",
        "not_null",
        "accepted_values",
        "unique",
    }
    assert len(candidates) >= 4
    assert all(set(candidate.evidence_refs).issubset(evidence.evidence_keys) for candidate in candidates)


def test_graph_normalizer_rejects_parameter_drift_and_duplicate_categories():
    seed_completed_profile()
    with Session(get_engine()) as session:
        evidence = build_proposal_evidence(session, DATASET_ID)

    raw = [
        {
            "candidate_id": "not-null:vendor_id",
            "rule_type": "NOT_NULL",
            "column": "vendor_id",
            "parameters": {},
            "confidence_score": 0.95,
            "severity": "HIGH",
            "rule_description": "Vendor ID must be populated.",
            "ai_reasoning": "Aggregate completeness is stable.",
        },
        {
            "candidate_id": "not-null:vendor_id",
            "rule_type": "NOT_NULL",
            "column": "vendor_id",
            "parameters": {},
            "confidence_score": 0.90,
            "severity": "HIGH",
            "rule_description": "Duplicate not-null proposal.",
            "ai_reasoning": "Must be deduplicated.",
        },
        {
            "candidate_id": "nonnegative:trip_distance",
            "rule_type": "RANGE",
            "column": "trip_distance",
            "parameters": {"min": -10.0},
            "confidence_score": 0.99,
            "severity": "HIGH",
            "rule_description": "Invented threshold.",
            "ai_reasoning": "Must be rejected.",
        },
        {
            "candidate_id": "nonnegative:trip_distance",
            "rule_type": "RANGE",
            "column": "trip_distance",
            "parameters": {"min": 0.0, "max": 80.0},
            "confidence_score": 0.85,
            "severity": "HIGH",
            "rule_description": "Trip distance must be non-negative.",
            "ai_reasoning": "Aggregate minimum is negative.",
        },
        {
            "candidate_id": "governed-enum:payment_type",
            "rule_type": "ACCEPTED_VALUES",
            "column": "payment_type",
            "parameters": {
                "accepted_values": [
                    "Flex Fare trip",
                    "Credit card",
                    "Cash",
                    "No charge",
                    "Dispute",
                    "Unknown",
                    "Voided trip",
                ]
            },
            "confidence_score": 0.80,
            "severity": "MEDIUM",
            "rule_description": "Payment type must be governed.",
            "ai_reasoning": "Use the governed semantic value set.",
        },
    ]

    proposals = _normalise_graph_rules(raw, evidence)
    assert [proposal.rule_type for proposal in proposals] == [
        "numeric_range",
        "not_null",
        "accepted_values",
    ]


def test_graph_normalizer_uses_canonical_text_spec_and_confidence_ceiling():
    seed_completed_profile()
    with Session(get_engine()) as session:
        evidence = build_proposal_evidence(session, DATASET_ID)

    proposals = _normalise_graph_rules(
        [
            {
                "candidate_id": "nonnegative:trip_distance",
                "rule_type": "RANGE",
                "column": "trip_distance",
                "parameters": {"min": 0.0, "max": 80.0},
                "confidence_score": 1.0,
                "severity": "CRITICAL",
                "rule_description": "Trip distance must be between 0 and 80 miles.",
                "ai_reasoning": "The aggregate maximum is 80.",
            }
        ],
        evidence,
    )

    assert len(proposals) == 1
    proposal = proposals[0]
    # The candidate now carries an upper bound derived from p95 (12.0) plus headroom,
    # so a RANGE rule can actually reject an outlier instead of admitting every value
    # that existed at profiling time. The model's restated 80.0 is still discarded in
    # favour of the server-owned bound -- which is what the next two assertions check.
    assert proposal.title == "trip_distance must be between 0 and 13.2"
    assert "80" not in proposal.description
    assert proposal.rule_spec == {
        "type": "numeric_range",
        "column": "trip_distance",
        "min_value": 0.0,
        "max_value": 13.2,
    }
    assert proposal.severity == "HIGH"
    assert proposal.confidence == 0.9


def test_graph_normalizer_preserves_agent_narrative_and_trace_fields():
    seed_completed_profile()
    with Session(get_engine()) as session:
        evidence = build_proposal_evidence(session, DATASET_ID)

    proposals = _normalise_graph_rules(
        [
            {
                "candidate_id": "nonnegative:trip_distance",
                "rule_type": "RANGE",
                "column": "trip_distance",
                "parameters": {"min": 0.0, "max": 80.0},
                "confidence_score": 0.85,
                "severity": "HIGH",
                "rule_name": "Khoảng cách chuyến đi hợp lệ",
                "rule_description": "Khoảng cách chuyến đi không được âm.",
                "ai_reasoning": "Hồ sơ dữ liệu ghi nhận giá trị âm ở cột khoảng cách.",
                "business_rationale": "Giá trị âm làm sai lệch tổng quãng đường.",
                "assumptions": ["Đơn vị đo là dặm."],
                "parameter_provenance": [
                    {
                        "parameter_name": "min_value",
                        "source_type": "DATA_PROFILE",
                        "source_ref": "profile.column.trip_distance.min_value",
                        "derivation_method": "server_policy",
                    }
                ],
            }
        ],
        evidence,
    )

    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.rule_name == "Khoảng cách chuyến đi hợp lệ"
    assert proposal.description == "Khoảng cách chuyến đi không được âm."
    assert proposal.evidence_summary == "Hồ sơ dữ liệu ghi nhận giá trị âm ở cột khoảng cách."
    assert proposal.business_rationale == "Giá trị âm làm sai lệch tổng quãng đường."
    assert proposal.assumptions == ["Đơn vị đo là dặm."]
    assert proposal.parameter_provenance[0]["parameter_name"] == "min_value"


def test_policy_fallback_does_not_invoke_a_second_proposer_graph(monkeypatch):
    seed_completed_profile()
    from src.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "agent_mode", "graph")
    monkeypatch.setattr(
        "src.services.dashboard_agent_workflow._invoke_dashboard_proposal_graph",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fallback must not call an LLM graph")),
    )

    with Session(get_engine()) as session:
        proposals = generate_dashboard_policy_fallback_proposals(session, DATASET_ID)

    assert len(proposals) >= 2
    assert all(proposal.model_name == "agent-policy-fallback-v1" for proposal in proposals)


def test_graph_normalizer_rejects_mismatched_candidate_id():
    seed_completed_profile()
    with Session(get_engine()) as session:
        evidence = build_proposal_evidence(session, DATASET_ID)

    proposals = _normalise_graph_rules(
        [
            {
                "candidate_id": "governed-enum:payment_type",
                "rule_type": "RANGE",
                "column": "trip_distance",
                "parameters": {"min": 0.0},
                "confidence_score": 0.9,
                "severity": "HIGH",
                "rule_description": "Mismatched candidate.",
                "ai_reasoning": "This must be rejected.",
            }
        ],
        evidence,
    )
    assert proposals == []


def test_mock_mode_returns_dashboard_supported_proposals(monkeypatch):
    from src.config import get_settings

    monkeypatch.setenv("AGENT_MODE", "mock")
    get_settings.cache_clear()
    try:
        seed_completed_profile()
        with Session(get_engine()) as session:
            proposals = generate_dashboard_proposals(session, DATASET_ID)

        assert len(proposals) >= 5
    finally:
        get_settings.cache_clear()

    assert {proposal.rule_type for proposal in proposals} == {
        "not_null",
        "numeric_range",
        "accepted_values",
        "cross_field_comparison",
        "duplicate_fingerprint",
        "unique",
    }
    assert all(proposal.model_name == "agent-mock-v1" for proposal in proposals)
    payment_rule = next(proposal for proposal in proposals if proposal.rule_type == "accepted_values")
    assert payment_rule.rule_spec["allowed_values"] == [
        "Flex Fare trip",
        "Credit card",
        "Cash",
        "No charge",
        "Dispute",
        "Unknown",
        "Voided trip",
    ]


def test_graph_mode_normalizes_only_evidence_backed_dashboard_rules(monkeypatch):
    seed_completed_profile()
    from src.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "agent_mode", "graph")
    monkeypatch.setattr(
        "src.services.dashboard_agent_workflow._invoke_dashboard_proposal_graph",
        lambda _evidence: [
            {
                "candidate_id": "nonnegative:trip_distance",
                "rule_type": "RANGE",
                "column": "trip_distance",
                "parameters": {"min": 0.0},
                "confidence_score": 0.9,
                "severity": "HIGH",
                "rule_description": "Trip distance must be non-negative.",
                "ai_reasoning": "Aggregate minimum is negative.",
            },
            {
                "candidate_id": "cross-field:pickup_at:<=:dropoff_at",
                "rule_type": "CROSS_FIELD_COMPARISON",
                "column": "pickup_at",
                "parameters": {"target_column": "dropoff_at", "operator": "<="},
                "confidence_score": 0.8,
                "severity": "MEDIUM",
                "rule_description": "Pickup should precede dropoff.",
                "ai_reasoning": "Both values are aggregate datetime fields.",
            },
            {
                "candidate_id": "unsupported:vendor_id",
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
    ]
    assert [proposal.model_name for proposal in proposals] == [
        "langgraph-openai",
        "langgraph-openai",
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
        dashboard_digest = digest[state["dataset_id"]]
        assert dashboard_digest["dashboard_candidate_mode"] is True
        assert len(dashboard_digest["dashboard_rule_candidates"]) >= 4
        return {
            "proposed_rules": [
                {
                    "candidate_id": "not-null:vendor_id",
                    "rule_type": "NOT_NULL",
                    "column": "vendor_id",
                    "parameters": {},
                    "confidence_score": 0.9,
                    "severity": "HIGH",
                    "rule_description": "Vendor ID must not be null.",
                    "ai_reasoning": "Aggregate null rate is zero.",
                },
                {
                    "candidate_id": "cross-field:pickup_at:<=:dropoff_at",
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
        "cross_field_comparison",
        "not_null",
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
                "candidate_id": "not-null:vendor_id",
                "rule_type": "NOT_NULL",
                "column": "vendor_id",
                "parameters": {},
                "confidence_score": 0.9,
                "severity": "HIGH",
                "rule_description": "Vendor ID must not be null.",
                "ai_reasoning": "Aggregate null rate is zero.",
            },
            {
                "candidate_id": "cross-field:pickup_at:<=:dropoff_at",
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
    assert len(proposals) == 2
    assert {proposal.model_name for proposal in proposals} == {"langgraph-openai"}


def test_graph_mode_fallback_only_reaches_minimum_rule_count(monkeypatch):
    seed_completed_profile()
    from src.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "agent_mode", "graph")
    monkeypatch.setattr(
        "src.services.dashboard_agent_workflow._invoke_dashboard_proposal_graph",
        lambda _evidence: [
            {
                "candidate_id": "not-null:vendor_id",
                "rule_type": "NOT_NULL",
                "column": "vendor_id",
                "parameters": {},
                "confidence_score": 0.9,
                "severity": "HIGH",
                "rule_description": "Vendor ID is required.",
                "ai_reasoning": "The dataset policy marks it as required.",
            }
        ],
    )
    with Session(get_engine()) as session:
        proposals = generate_dashboard_proposals(session, DATASET_ID)

    assert len(proposals) == 2
    assert [proposal.rule_type for proposal in proposals] == ["numeric_range", "not_null"]
    assert [proposal.model_name for proposal in proposals] == [
        "agent-policy-fallback-v1",
        "langgraph-openai",
    ]


def test_graph_mode_rejects_fabricated_or_unsupported_rules(monkeypatch):
    seed_completed_profile()
    from src.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "agent_mode", "graph")
    monkeypatch.setattr(
        "src.services.dashboard_agent_workflow._invoke_dashboard_proposal_graph",
        lambda _evidence: [
            {
                "candidate_id": "nonnegative:trip_distance",
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
