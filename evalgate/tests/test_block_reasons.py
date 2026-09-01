"""A release blocked for missing evidence must stay blocked.

Six conditions produce RELEASE_BLOCKED. Two of them raise a Finding; the other four
are statements about evidence that was never collected -- an unevaluated mandatory
hard gate, a mandatory evaluator that errored, one that failed, and mandatory
coverage below 1.0.

The suppression ratchet reasons about finding ids. When it found that set empty it
downgraded RELEASE_BLOCKED to a score band, so a run blocked purely for missing
evidence was promoted to PASS -- with an empty suppressions.yaml, requiring no
suppression at all. These tests pin the guard that closed it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from evalgate.aggregator import Decision, aggregate
from evalgate.core.suppression_policy import SuppressionResolution, apply_suppressions
from evalgate.schemas.eval_result import EvalResult, EvalStatus, MetricValue

GIT_SHA = "f" * 40


def _passing(gate: str, evaluator: str, score: float = 100.0) -> EvalResult:
    """A healthy mandatory evaluator, so the aggregate has a real score to publish."""
    return EvalResult(
        gate=gate,
        evaluator=evaluator,
        status=EvalStatus.PASS,
        score=score,
        metadata={"mandatory": True},
    )


def _healthy_run() -> list[EvalResult]:
    """Every weighted gate measured and passing: nothing here should block."""
    return [
        _passing("ai_quality", "ai_quality_probe_v1"),
        _passing("ai_security", "ai_security_probe_v1"),
        _passing("input_data", "input_data_probe_v1"),
        _passing("governance", "governance_probe_v1"),
    ]


def _ratchet(outcome, results) -> None:
    apply_suppressions(
        outcome, results, SuppressionResolution(), current_git_sha=GIT_SHA
    )


def test_healthy_run_is_not_blocked() -> None:
    """Control: without a blocking condition the fixture must reach a score band.

    Without this, a test asserting "still blocked" could pass because the fixture
    was blocked for some unrelated reason.
    """
    outcome = aggregate(_healthy_run(), profile="local")
    assert outcome.block_reasons == []
    assert outcome.decision in {Decision.PASS, Decision.WARNING, Decision.FAIL}


def test_mandatory_evaluator_error_blocks_and_survives_the_ratchet() -> None:
    results = _healthy_run()
    results.append(
        EvalResult(
            gate="ai_quality",
            evaluator="broken_evaluator_v1",
            status=EvalStatus.EVALUATOR_ERROR,
            metadata={"mandatory": True, "reason": "evaluator raised RuntimeError"},
        )
    )
    outcome = aggregate(results, profile="local")

    assert outcome.decision == Decision.RELEASE_BLOCKED
    assert any("could not run" in reason for reason in outcome.block_reasons)
    # No finding was raised, so the ratchet sees an empty id set.
    assert outcome.unsuppressed_findings == []

    _ratchet(outcome, results)
    assert outcome.decision == Decision.RELEASE_BLOCKED, (
        "an empty suppressions.yaml must not promote a run blocked on missing evidence"
    )


def test_missing_mandatory_evidence_blocks_and_survives_the_ratchet() -> None:
    results = _healthy_run()
    results.append(
        EvalResult(
            gate="input_data",
            evaluator="evidence_hungry_v1",
            status=EvalStatus.MISSING_MANDATORY_EVIDENCE,
            metadata={"mandatory": True, "reason": "manifest carries no execution-results"},
        )
    )
    outcome = aggregate(results, profile="local")

    assert outcome.decision == Decision.RELEASE_BLOCKED
    assert outcome.mandatory_evidence_coverage < 1.0
    assert outcome.unsuppressed_findings == []

    _ratchet(outcome, results)
    assert outcome.decision == Decision.RELEASE_BLOCKED


def test_unevaluated_mandatory_hard_gate_blocks_and_survives_the_ratchet() -> None:
    """A mandatory hard gate whose metric no evaluator produced.

    ``ci`` lists HG-A1 as mandatory; a run that produces no ``min_recall_per_class``
    leaves it NOT_EVALUATED. That is missing coverage, not a passing control.
    """
    results = _healthy_run()
    outcome = aggregate(results, profile="ci")

    assert outcome.decision == Decision.RELEASE_BLOCKED
    assert any("not evaluated" in reason for reason in outcome.block_reasons)

    _ratchet(outcome, results)
    assert outcome.decision == Decision.RELEASE_BLOCKED


def test_block_reasons_name_the_specific_gate() -> None:
    """The reason has to be actionable: a reader must learn which gate is missing."""
    outcome = aggregate(_healthy_run(), profile="ci")
    joined = " ".join(outcome.block_reasons)
    assert "HG-A1" in joined


def test_hard_gate_failure_still_reaches_the_ratchet() -> None:
    """The guard must not disable suppression for the case it was designed for.

    A failing hard gate raises a finding, carries no block_reason, and remains
    suppressible through the normal auditable path.
    """
    results = _healthy_run()
    results.append(
        EvalResult(
            gate="ai_quality",
            evaluator="recall_probe_v1",
            status=EvalStatus.FAIL,
            score=0.0,
            metrics={
                "min_recall_per_class": MetricValue(raw=0.0, unit="ratio", normalized=0.0)
            },
            metadata={"mandatory": False},
        )
    )
    outcome = aggregate(results, profile="local")

    assert outcome.decision == Decision.RELEASE_BLOCKED
    assert "HG-A1" in {gate.id for gate in outcome.hard_gates if gate.status == "FAIL"}
    assert outcome.block_reasons == [], (
        "a failed control is a finding, not missing evidence"
    )


def test_stale_run_is_not_recorded_as_usable_baseline(tmp_path, monkeypatch) -> None:
    """Only staleness disqualifies a baseline -- being blocked does not.

    Restricting usability to PASS/WARNING deadlocks every HG-R* gate on a product
    that has not passed yet: no stored run is ever eligible, so resolve_baseline
    always answers None.
    """
    from evalgate.core import regression_engine

    monkeypatch.setattr(regression_engine, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(regression_engine, "INDEX", tmp_path / "index.json")
    monkeypatch.setattr(regression_engine, "PROJECT_ROOT", tmp_path)

    stamp = datetime.now(UTC).isoformat()
    regression_engine.save_run(
        {"run_id": "blocked-1", "decision": "RELEASE_BLOCKED", "timestamp": stamp}
    )
    regression_engine.save_run(
        {"run_id": "stale-1", "decision": "EVALGATE_STALE", "timestamp": stamp}
    )

    flags = {entry["run_id"]: entry["usable_as_baseline"] for entry in regression_engine.load_index()}
    assert flags["blocked-1"] is True
    assert flags["stale-1"] is False
