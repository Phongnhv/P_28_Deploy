"""Offline contract tests for nightly and pre-release result adapters."""

from __future__ import annotations

import json

from evalgate.gates.gate1_ai_quality import live_agent_e2e
from evalgate.gates.gate2_security import prompt_injection_probe, upload_behaviour_probe
from evalgate.gates.gate3_observability import trace_coverage
from evalgate.gates.gate5_reliability import load_slo
from evalgate.gates.gate7_business import steward_outcome
from evalgate.schemas.eval_result import EvalStatus


def test_live_adapters_never_pass_when_input_is_missing(monkeypatch):
    for variable in (
        "EVALGATE_LIVE_AGENT_RESULT",
        "EVALGATE_PROMPTFOO_RESULT",
        "EVALGATE_UPLOAD_PROBE_RESULT",
        "EVALGATE_TRACE_FILE",
        "EVALGATE_K6_RESULT",
        "EVALGATE_STEWARD_EVENTS",
    ):
        monkeypatch.delenv(variable, raising=False)
    results = [
        live_agent_e2e.evaluate(write_evidence=False),
        live_agent_e2e.evaluate_geval(write_evidence=False),
        prompt_injection_probe.evaluate(write_evidence=False),
        upload_behaviour_probe.evaluate(write_evidence=False),
        trace_coverage.evaluate(write_evidence=False),
        load_slo.evaluate(write_evidence=False),
        steward_outcome.evaluate(write_evidence=False),
    ]
    assert all(result.status not in {EvalStatus.PASS, EvalStatus.WARN} for result in results)


def test_trace_adapter_requires_complete_redacted_event_shape(tmp_path, monkeypatch):
    target = tmp_path / "trace.jsonl"
    events = [
        {
            "trace_id": "t1", "workflow_run_id": "w1", "dataset_id": "d1",
            "event": "run_start", "timestamp": "2026-08-25T00:00:00Z",
        },
        {
            "trace_id": "t1", "workflow_run_id": "w1", "dataset_id": "d1",
            "event": "node", "node": "profile", "timestamp": "2026-08-25T00:00:01Z",
        },
    ]
    target.write_text("\n".join(json.dumps(item) for item in events), encoding="utf-8")
    monkeypatch.setenv("EVALGATE_TRACE_FILE", str(target))
    result = trace_coverage.evaluate(write_evidence=False)
    # Field completeness is perfect: both events carry every identity field.
    assert result.metrics["trace_coverage"].raw == 1.0
    # The verdict is not, and must not be. This trace instruments one node out of the
    # nineteen src/agents/graph.py declares, and a gate that called that PASS is what
    # let Gate 3 publish 100.00 while observing four nodes of one graph.
    assert result.metrics["instrumented_node_coverage"].raw < 0.5
    assert result.status == EvalStatus.FAIL
    assert any(f.id == "HG-O1" for f in result.critical_findings)


def test_trace_adapter_scores_the_weaker_of_the_two_coverages(tmp_path, monkeypatch):
    """A perfect field ratio over almost no instrumentation must not read as healthy."""
    target = tmp_path / "trace.jsonl"
    target.write_text(
        json.dumps({
            "trace_id": "t1", "workflow_run_id": "w1", "dataset_id": "d1",
            "event": "node", "node": "rule_proposer", "timestamp": "2026-09-02T00:00:00Z",
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("EVALGATE_TRACE_FILE", str(target))
    result = trace_coverage.evaluate(write_evidence=False)
    assert result.metrics["trace_coverage"].raw == 1.0
    assert result.score is not None and result.score < 100.0


def test_upload_probe_fails_when_any_adversarial_case_is_accepted(tmp_path, monkeypatch):
    target = tmp_path / "upload.json"
    target.write_text(
        json.dumps({"malicious_upload_accepted_count": 1, "executed_cases": ["fake-mime"]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("EVALGATE_UPLOAD_PROBE_RESULT", str(target))
    result = upload_behaviour_probe.evaluate(write_evidence=False)
    assert result.status == EvalStatus.FAIL
    assert result.critical_findings[0].id == "HG-S4"


def test_business_metrics_remain_unmeasured_below_sample_floor(tmp_path, monkeypatch):
    target = tmp_path / "business.json"
    target.write_text(
        json.dumps({"dataset_count": 2, "proposal_count": 100, "accepted_count": 80}),
        encoding="utf-8",
    )
    monkeypatch.setenv("EVALGATE_STEWARD_EVENTS", str(target))
    assert steward_outcome.evaluate(write_evidence=False).status == EvalStatus.NOT_MEASURED
