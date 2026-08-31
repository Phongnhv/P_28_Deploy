"""Unit tests for steward_insights_node (AI Root Cause Hypothesis Agent)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from src.agents.nodes.steward_insights_node import (
    HypothesisItem,
    HypothesisResponse,
    steward_insights_node,
    validate_and_sanitize_hypotheses,
)
from src.models.database import DqResultModel


def test_validate_and_sanitize_hypotheses():
    """Verify citation matching, type checks, and confidence clamping."""
    valid_signals = {"sig_1", "sig_2"}
    valid_evidence = {"rule_1", "column_a"}

    raw_hypotheses = [
        {
            "hypothesis_type": "SCHEMA_CHANGE",
            "summary": "Schema change detected.",
            "confidence": 1.20,  # Should clamp to 1.0
            "supporting_signal_ids": ["sig_1", "sig_invalid"],  # sig_invalid should filter out
            "contradicting_signal_ids": ["sig_2"],
            "evidence_refs": ["rule_1"],
            "recommended_checks": ["Check table DDL"],
        },
        {
            "hypothesis_type": "INVALID_TYPE",  # Should fall back to UNKNOWN
            "summary": "Invalid type.",
            "confidence": -0.5,  # Should clamp to 0.0
            "supporting_signal_ids": [],
            "contradicting_signal_ids": [],
            "evidence_refs": [],
            "recommended_checks": [],  # Should trigger default safe check
        },
    ]

    validated = validate_and_sanitize_hypotheses(raw_hypotheses, valid_signals, valid_evidence)

    assert len(validated) == 2
    assert validated[0]["hypothesis_type"] == "SCHEMA_CHANGE"
    assert validated[0]["confidence"] == 1.0
    assert validated[0]["supporting_signal_ids"] == ["sig_1"]

    assert validated[1]["hypothesis_type"] == "UNKNOWN"
    assert validated[1]["confidence"] == 0.0
    assert len(validated[1]["recommended_checks"]) > 0


@pytest.mark.asyncio
async def test_steward_insights_node_not_required():
    """Verify agent skips if decision is NORMAL."""
    state = {"anomaly_decision": {"decision": "NORMAL", "score": 0.0, "severity": "LOW"}, "signal_observations": []}
    output = await steward_insights_node(state)
    assert output["hypothesis_status"] == "NOT_REQUIRED"
    assert output["hypotheses"] == []


@pytest.mark.asyncio
async def test_steward_insights_node_fallback_on_error(test_db):
    """Verify deterministic fallback is generated when LLM fails."""
    # Write a failed rule to DB to mimic current execution
    with Session(test_db) as session:
        failed_res = DqResultModel(
            run_id="run_123",
            rule_id="rule_abc",
            rule_title="Rule ABC Complete",
            status="FAIL",
            checked_count=100,
            failed_count=5,
            failed_row_ids="[]",
        )
        session.add(failed_res)
        session.commit()

    state = {
        "execution_run_id": "run_123",
        "anomaly_run_id": "anom_123",
        "anomaly_decision": {"decision": "ANOMALY", "score": 0.85, "severity": "HIGH"},
        "signal_observations": [
            {
                "signal_id": "sig_1",
                "target_id": "rule_abc",
                "score": 0.90,
                "family": "BUSINESS_RULE",
                "target_type": "RULE",
                "reliability": 0.95,
                "detector_name": "TEST",
                "detector_version": "1.0",
                "explanation_code": "TEST",
            }
        ],
        "dataset_id": "test_trips",
    }

    with patch("src.agents.nodes.steward_insights_node.get_llm", side_effect=RuntimeError("LLM API Timeout")):
        output = await steward_insights_node(state)

    assert output["hypothesis_status"] == "FALLBACK_USED"
    assert len(output["hypotheses"]) == 1
    assert output["hypotheses"][0]["hypothesis_type"] == "DATA_QUALITY_VIOLATION"
    assert "sig_1" in output["hypotheses"][0]["supporting_signal_ids"]


@pytest.mark.asyncio
async def test_steward_insights_node_success(test_db):
    """Verify LLM success path generates validated structured hypotheses."""
    state = {
        "execution_run_id": "run_123",
        "anomaly_run_id": "anom_123",
        "anomaly_decision": {"decision": "ANOMALY", "score": 0.85, "severity": "HIGH"},
        "signal_observations": [
            {
                "signal_id": "sig_1",
                "target_id": "rule_abc",
                "score": 0.90,
                "family": "BUSINESS_RULE",
                "target_type": "RULE",
                "reliability": 0.95,
                "detector_name": "TEST",
                "detector_version": "1.0",
                "explanation_code": "TEST",
            }
        ],
        "dataset_id": "test_trips",
    }

    # Mock LLM Structured Output Response
    mock_hyp = HypothesisItem(
        hypothesis_type="SYSTEM_BUG",
        summary="A bug in the upstream software.",
        confidence=0.80,
        supporting_signal_ids=["sig_1"],
        contradicting_signal_ids=[],
        evidence_refs=["rule_abc"],
        recommended_checks=["Verify pipeline updates"],
    )
    mock_response = HypothesisResponse(hypotheses=[mock_hyp])

    mock_structured_llm = MagicMock()
    mock_structured_llm.ainvoke = AsyncMock(return_value=mock_response)

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured_llm

    with patch("src.agents.nodes.steward_insights_node.get_llm", return_value=mock_llm):
        output = await steward_insights_node(state)

    assert output["hypothesis_status"] == "SUCCEEDED"
    assert len(output["hypotheses"]) == 1
    assert output["hypotheses"][0]["hypothesis_type"] == "SYSTEM_BUG"
    assert output["hypotheses"][0]["confidence"] == 0.80
