"""Unit tests for steward_insights_node (DQ Health Score & Steward Insights Advisor)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.nodes.steward_insights_node import (
    _extract_remediation_actions,
    _get_grade,
    calculate_dq_metrics,
    steward_insights_node,
)
from src.agents.state import AgentState


def test_calculate_dq_metrics_empty():
    """Kiểm tra khi không có test results nào."""
    res = calculate_dq_metrics([])
    assert res["dq_score"] == 100.0
    assert res["dq_grade"] == "A"
    assert res["total_rules"] == 0
    assert res["passed_count"] == 0


def test_calculate_dq_metrics_all_passed():
    """Kiểm tra khi tất cả rules đều PASSED."""
    test_results = [
        {"rule_id": "r1", "status": "PASSED", "violation_rate": 0.0, "severity": "CRITICAL", "dimension": "UNIQUENESS"},
        {"rule_id": "r2", "status": "PASSED", "violation_rate": 0.0, "severity": "HIGH", "dimension": "COMPLETENESS"},
        {"rule_id": "r3", "status": "PASSED", "violation_rate": 0.0, "severity": "MEDIUM", "dimension": "VALIDITY"},
    ]
    res = calculate_dq_metrics(test_results)
    assert res["dq_score"] == 100.0
    assert res["dq_grade"] == "A"
    assert res["passed_count"] == 3
    assert res["failed_count"] == 0
    assert res["error_count"] == 0
    assert res["dq_dimensions"]["UNIQUENESS"] == 100.0
    assert res["dq_dimensions"]["COMPLETENESS"] == 100.0
    assert res["dq_dimensions"]["VALIDITY"] == 100.0


def test_calculate_dq_metrics_with_weighted_failures():
    """Kiểm tra tính điểm chính xác theo trọng số Severity."""
    # weights: CRITICAL=4, HIGH=3, MEDIUM=2, LOW=1 (Total weights = 10)
    # r1: CRITICAL (4), v_rate = 0.01 -> clean_rate = 0.99 -> 4 * 0.99 = 3.96
    # r2: HIGH (3), v_rate = 0.00 -> clean_rate = 1.00 -> 3 * 1.00 = 3.00
    # r3: MEDIUM (2), v_rate = 0.05 -> clean_rate = 0.95 -> 2 * 0.95 = 1.90
    # r4: LOW (1), v_rate = 0.10 -> clean_rate = 0.90 -> 1 * 0.90 = 0.90
    # Sum weighted clean = 3.96 + 3.00 + 1.90 + 0.90 = 9.76
    # Base score = (9.76 / 10.0) * 100 = 97.6
    test_results = [
        {"rule_id": "r1", "status": "FAILED", "violation_rate": 0.01, "severity": "CRITICAL", "dimension": "UNIQUENESS"},
        {"rule_id": "r2", "status": "PASSED", "violation_rate": 0.00, "severity": "HIGH", "dimension": "COMPLETENESS"},
        {"rule_id": "r3", "status": "FAILED", "violation_rate": 0.05, "severity": "MEDIUM", "dimension": "VALIDITY"},
        {"rule_id": "r4", "status": "FAILED", "violation_rate": 0.10, "severity": "LOW", "dimension": "VALIDITY"},
    ]
    res = calculate_dq_metrics(test_results)
    assert res["dq_score"] == 97.6
    assert res["dq_grade"] == "A"
    assert res["passed_count"] == 1
    assert res["failed_count"] == 3
    assert res["dq_dimensions"]["UNIQUENESS"] == 99.0
    assert res["dq_dimensions"]["COMPLETENESS"] == 100.0
    # VALIDITY: r3 (weight 2, clean 0.95 -> 1.90) + r4 (weight 1, clean 0.90 -> 0.90) = 2.80 / 3 = 93.33
    assert res["dq_dimensions"]["VALIDITY"] == 93.33


def test_calculate_dq_metrics_with_errors_and_anomalies():
    """Kiểm tra xử lý status ERROR (clean=0) và trừ điểm Anomaly penalty."""
    test_results = [
        {"rule_id": "r1", "status": "ERROR", "violation_rate": 0.0, "severity": "CRITICAL", "dimension": "VALIDITY"},
        {"rule_id": "r2", "status": "PASSED", "violation_rate": 0.0, "severity": "CRITICAL", "dimension": "UNIQUENESS"},
    ]
    # Total weight = 4 + 4 = 8.
    # r1: 4 * 0.0 = 0.0
    # r2: 4 * 1.0 = 4.0
    # Base score = (4.0 / 8.0) * 100 = 50.0
    # 2 anomalies * 2.0 penalty = 4.0 deduction -> final score = 46.0
    anomalies = [
        {"rule_id": "r1", "anomaly_type": "Z_SCORE_SPIKE"},
        {"rule_id": "r2", "anomaly_type": "STATIC_THRESHOLD"},
    ]
    res = calculate_dq_metrics(test_results, anomalies=anomalies)
    assert res["dq_score"] == 46.0
    assert res["dq_grade"] == "D"
    assert res["anomaly_count"] == 2
    assert res["anomaly_penalty"] == 4.0
    assert res["error_count"] == 1


def test_get_grade_ranges():
    """Kiểm tra các ngưỡng xếp hạng A, B, C, D."""
    assert _get_grade(100.0) == "A"
    assert _get_grade(95.0) == "A"
    assert _get_grade(94.9) == "B"
    assert _get_grade(85.0) == "B"
    assert _get_grade(84.9) == "C"
    assert _get_grade(70.0) == "C"
    assert _get_grade(69.9) == "D"
    assert _get_grade(0.0) == "D"


def test_extract_remediation_actions():
    """Kiểm tra trích xuất checklist hành động từ markdown."""
    md = """
    ## Actionable Next Steps
    - [ ] Data Steward review and approve adjusted rules.
    - [x] Data Engineering verified pipeline schema.
    * [ ] Re-run execution graph after fix.
    """
    actions = _extract_remediation_actions(md)
    assert len(actions) == 3
    assert actions[0]["action"] == "Data Steward review and approve adjusted rules."
    assert actions[0]["completed"] is False
    assert actions[1]["action"] == "Data Engineering verified pipeline schema."
    assert actions[1]["completed"] is True
    assert actions[2]["action"] == "Re-run execution graph after fix."
    assert actions[2]["completed"] is False


@pytest.mark.asyncio
async def test_steward_insights_node_with_mock_llm():
    """Kiểm tra thực thi node thành công với mock LLM."""
    state: AgentState = {
        "dataset_id": "test_trips",
        "test_results": [
            {"rule_id": "r1", "status": "PASSED", "violation_rate": 0.0, "severity": "CRITICAL", "dimension": "UNIQUENESS"},
            {"rule_id": "r2", "status": "FAILED", "violation_rate": 0.03, "severity": "MEDIUM", "dimension": "VALIDITY"},
        ],
        "anomalies": [],
    }

    mock_llm_response = (
        "# 📊 Steward Report\n\n"
        "### 1. Executive Summary\nDQ Score is 98.5.\n\n"
        "### 4. Actionable Next Steps\n"
        "- [ ] Adjust tolerance for rule r2.\n"
        "- [ ] Approve dataset publication.\n"
    )

    from langchain_core.messages import AIMessage

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=AIMessage(content=mock_llm_response))
    mock_llm.invoke.return_value = AIMessage(content=mock_llm_response)
    mock_llm.return_value = AIMessage(content=mock_llm_response)

    with patch("src.agents.nodes.steward_insights_node.get_llm", return_value=mock_llm):
        output = await steward_insights_node(state)

    assert "dq_score" in output
    assert output["dq_score"] > 0
    assert "dq_grade" in output
    assert "dq_dimensions" in output
    assert "steward_summary" in output
    assert "# 📊 Steward Report" in output["steward_summary"]
    assert len(output["remediation_actions"]) == 2
    assert output["remediation_actions"][0]["action"] == "Adjust tolerance for rule r2."


@pytest.mark.asyncio
async def test_steward_insights_node_fallback_on_llm_error():
    """Kiểm tra cơ chế fallback an toàn khi LLM gặp lỗi."""
    state: AgentState = {
        "dataset_id": "test_trips",
        "test_results": [
            {"rule_id": "r1", "status": "FAILED", "violation_rate": 0.05, "severity": "HIGH", "dimension": "COMPLETENESS"},
        ],
        "anomalies": [{"rule_id": "r1", "description": "Spike detected"}],
    }

    with patch("src.agents.nodes.steward_insights_node.get_llm", side_effect=RuntimeError("API Connection Timeout")):
        output = await steward_insights_node(state)

    assert "dq_score" in output
    assert output["dq_score"] < 100.0
    assert "steward_summary" in output
    assert "Báo Cáo Chất Lượng Dữ Liệu: test_trips" in output["steward_summary"]
    assert len(output["remediation_actions"]) >= 1
