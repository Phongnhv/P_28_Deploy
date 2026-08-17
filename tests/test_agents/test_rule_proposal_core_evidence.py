from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.agents.nodes.rule_proposer_node import _build_coverage_requirements, _stamp_rule
from src.models.rule_schemas import ProposedRule


def _range_payload() -> dict:
    return {
        "column": "amount",
        "rule_type": "RANGE",
        "parameters": {"min": 0.0},
        "rule_name": "Amount must be non-negative",
        "business_rationale": "Negative amounts distort financial totals.",
        "proposal_basis": "MIXED",
        "selected_evidence_refs": ["policy.nonnegative_column.amount"],
        "parameter_provenance": [{
            "parameter_name": "min",
            "source_type": "POLICY",
            "source_ref": "policy.nonnegative_column.amount",
            "derivation_method": "configured non-negative policy",
        }],
        "assumptions": [],
        "confidence": {
            "overall": 0.9,
            "evidence_strength": 1.0,
            "business_support": 0.9,
            "sample_representativeness": 0.8,
            "explanation": "Policy-backed threshold with supporting profile evidence.",
        },
        "severity": "HIGH",
        "dimension": "VALIDITY",
        "rule_description": "Amount must be greater than or equal to zero.",
        "ai_reasoning": "Dataset policy defines amount as non-negative.",
    }


def test_new_schema_requires_core_evidence_fields():
    payload = _range_payload()
    del payload["rule_name"]
    with pytest.raises(ValidationError):
        ProposedRule.model_validate(payload)


def test_parameter_provenance_must_cover_all_parameters():
    payload = _range_payload()
    payload["parameters"]["max"] = 100.0
    with pytest.raises(ValidationError, match="parameter_provenance"):
        ProposedRule.model_validate(payload)


def test_confidence_breakdown_rejects_inconsistent_overall():
    payload = _range_payload()
    payload["confidence"]["overall"] = 0.1
    with pytest.raises(ValidationError, match="confidence.overall"):
        ProposedRule.model_validate(payload)


def test_stamp_resolves_only_allowlisted_evidence_from_digest():
    digest = {
        "rows": 100,
        "sample": {"rate": 1.0, "n": 100},
        "dashboard_candidate_mode": True,
        "dashboard_rule_candidates": [{
            "candidate_id": "nonnegative:amount",
            "column": "amount",
            "rule_type": "RANGE",
            "parameters": {"min": 0.0},
            "dimension": "VALIDITY",
            "evidence": ["policy.nonnegative_column.amount", "profile.column.amount.min_value"],
        }],
        "columns": [{"name": "amount", "range": [-1.0, 50.0], "signals": ["has_negative_values"]}],
    }
    requirement = _build_coverage_requirements(digest)[0]
    payload = _range_payload()
    payload["candidate_id"] = "nonnegative:amount"
    rule = ProposedRule.model_validate(payload)
    stamped = _stamp_rule(rule, "source_rows", "run-1", requirement=requirement, table_digest=digest)

    assert stamped["evidence"]["observed_metrics"] == {
        "policy.nonnegative_column.amount": None
    }
    assert "status" not in stamped
    assert stamped["confidence_score"] == 0.9


def test_stamp_rejects_evidence_reference_from_another_candidate():
    payload = _range_payload()
    payload["selected_evidence_refs"] = ["policy.nonnegative_column.other"]
    payload["parameter_provenance"][0]["source_ref"] = "policy.nonnegative_column.other"
    rule = ProposedRule.model_validate(payload)
    requirement = {
        "evidence_items": [{"id": "policy.nonnegative_column.amount", "value": None}]
    }
    assert _stamp_rule(rule, "source_rows", "run-1", requirement=requirement) == {}


@pytest.mark.parametrize(
    ("rule_type", "column", "parameters"),
    [
        ("FRESHNESS", "created_at", {}),
        ("NULL_RATE", "name", {}),
        ("ROW_COUNT", None, {}),
        ("ROW_COUNT", "name", {"min_row_count": 1}),
    ],
)
def test_rule_specific_required_parameters_and_scope(rule_type, column, parameters):
    payload = _range_payload()
    payload.update({"rule_type": rule_type, "column": column, "parameters": parameters})
    payload["parameter_provenance"] = []
    with pytest.raises(ValidationError):
        ProposedRule.model_validate(payload)
