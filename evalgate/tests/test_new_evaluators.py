"""Unit tests for the 4 newly added evaluators:
1. anomaly_logic_probe_v1
2. sql_compilation_probe_v1
3. profile_accuracy_probe_v1
4. report_grounding_probe_v1
"""

from __future__ import annotations

from evalgate.gates.gate1_ai_quality import anomaly_logic_probe as alp
from evalgate.gates.gate1_ai_quality import report_grounding_probe as rgp
from evalgate.gates.gate1_ai_quality import sql_compilation_probe as scp
from evalgate.gates.gate4_input_data import profile_accuracy_probe as pap
from evalgate.schemas.eval_result import EvalResult, EvalStatus


def test_anomaly_logic_probe_contract():
    res = alp.evaluate(write_evidence=False)
    assert isinstance(res, EvalResult)
    assert res.gate == "ai_quality"
    assert res.evaluator == "anomaly_logic_probe_v1"
    assert res.status == EvalStatus.PASS
    assert res.score == 100.0
    assert "anomaly_logic_score" in res.metrics


def test_sql_compilation_probe_contract():
    res = scp.evaluate(write_evidence=False)
    assert isinstance(res, EvalResult)
    assert res.gate == "ai_quality"
    assert res.evaluator == "sql_compilation_probe_v1"
    assert res.status == EvalStatus.PASS
    assert res.score == 100.0
    assert "sql_compilation_score" in res.metrics


def test_profile_accuracy_probe_contract():
    res = pap.evaluate(write_evidence=False)
    assert isinstance(res, EvalResult)
    assert res.gate == "input_data"
    assert res.evaluator == "profile_accuracy_probe_v1"
    assert res.status == EvalStatus.PASS
    assert res.score == 100.0
    assert "profile_accuracy_score" in res.metrics


def test_report_grounding_probe_contract():
    res = rgp.evaluate(write_evidence=False)
    assert isinstance(res, EvalResult)
    assert res.gate == "ai_quality"
    assert res.evaluator == "report_grounding_probe_v1"
    assert res.status == EvalStatus.PASS
    assert res.score == 100.0
    assert "report_grounding_score" in res.metrics
