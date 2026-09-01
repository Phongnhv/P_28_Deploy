"""A narrower profile is not a regression.

The baseline is normally a ``ci`` run, and ``local`` deliberately selects far fewer
evaluators. Comparing the two by raw set difference reports every ci-only evaluator
as "disappeared" and raises a CRITICAL blocking finding for each -- eleven of them
on this project, enough to block every local run the moment a baseline was first
configured.

An evaluator the current profile does not select is out of scope for the
comparison, not missing from it. A name the profile *does* select and that has
genuinely gone is still a regression.
"""

from __future__ import annotations

import json

import pytest

from evalgate.core import regression_engine
from evalgate.schemas.eval_result import EvalResult, EvalStatus

# Real names, so profile membership is exercised rather than mocked.
LOCAL_AND_CI = "vacuity_probe_v1"      # ALL profiles
CI_ONLY = "replay_detection_v1"        # ci / nightly / pre_release, never local


def _result(evaluator: str, gate: str = "ai_quality", score: float = 100.0) -> EvalResult:
    return EvalResult(
        gate=gate, evaluator=evaluator, status=EvalStatus.PASS, score=score
    )


def _dump(results: list[EvalResult]) -> list[dict]:
    return [json.loads(r.model_dump_json()) for r in results]


@pytest.fixture
def stored_baseline(tmp_path, monkeypatch):
    """A ci baseline carrying both a shared evaluator and a ci-only one."""
    monkeypatch.setattr(regression_engine, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(regression_engine, "INDEX", tmp_path / "index.json")
    monkeypatch.setattr(regression_engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(regression_engine, "EVIDENCE_DIR", tmp_path / "evidence")

    baseline_results = [_result(LOCAL_AND_CI), _result(CI_ONLY)]
    regression_engine.save_run(
        {
            "run_id": "baseline-ci",
            "decision": "RELEASE_BLOCKED",
            "mode": "ci",
            "timestamp": "2026-08-30T00:00:00+00:00",
            "evaluation_schema_version": "2.0",
            "policy_version": "1.0",
            "corpus_version": "1.0",
            "normalizer_version": "1.0",
            "gate_scores": {},
            "hard_gates": [],
            "results": _dump(baseline_results),
        }
    )
    return "baseline-ci"


def _evaluate(results, baseline_id, profile):
    return regression_engine.evaluate(
        results, baseline_run_id=baseline_id, write_evidence=False, profile=profile
    )


def test_ci_only_evaluator_is_not_a_regression_for_a_local_run(stored_baseline) -> None:
    current = [_result(LOCAL_AND_CI)]
    outcome = _evaluate(current, stored_baseline, "local")

    assert outcome.metadata["composition_changed"]["removed"] == []
    assert CI_ONLY in outcome.metadata["composition_changed"]["out_of_profile"]
    assert "REG-EVALUATOR-REMOVED" not in {f.id for f in outcome.critical_findings}
    assert outcome.status == EvalStatus.PASS


def test_a_genuinely_missing_evaluator_is_still_a_regression(stored_baseline) -> None:
    """The shared evaluator vanishing from a ci run must still block."""
    current = [_result(CI_ONLY)]
    outcome = _evaluate(current, stored_baseline, "ci")

    assert LOCAL_AND_CI in outcome.metadata["composition_changed"]["removed"]
    findings = {f.id for f in outcome.critical_findings}
    assert "REG-EVALUATOR-REMOVED" in findings
    assert any(f.blocks_release for f in outcome.critical_findings)


def test_unknown_profile_falls_back_to_the_unscoped_comparison(stored_baseline) -> None:
    """Without a profile there is no membership to scope by; report everything."""
    current = [_result(LOCAL_AND_CI)]
    outcome = _evaluate(current, stored_baseline, None)

    assert CI_ONLY in outcome.metadata["composition_changed"]["removed"]


def test_score_drop_is_reported_regardless_of_profile_width(stored_baseline) -> None:
    """Scoping membership must not suppress a real drop on a shared evaluator."""
    current = [_result(LOCAL_AND_CI, score=10.0)]
    outcome = _evaluate(current, stored_baseline, "local")

    assert outcome.metrics["gate_score_drop_max"].raw == pytest.approx(90.0)
    assert "REG-DROP" in {f.id for f in outcome.critical_findings}


def test_profiles_are_recorded_so_the_comparison_is_auditable(stored_baseline) -> None:
    outcome = _evaluate([_result(LOCAL_AND_CI)], stored_baseline, "local")
    assert outcome.metadata["profile"] == "local"
    assert outcome.metadata["baseline_profile"] == "ci"


def test_an_evaluator_that_reported_without_a_score_is_not_removed(stored_baseline) -> None:
    """Blocked is not gone.

    An evaluator that ran and honestly reported BLOCKED_BY_SYSTEM_CAPABILITY has no
    score, but it is present in the results. Treating "no score" as "removed"
    accused every local run of deleting vacuity_probe_v1, which was sitting right
    there in the output.
    """
    blocked = EvalResult(
        gate="ai_quality",
        evaluator=LOCAL_AND_CI,
        status=EvalStatus.BLOCKED_BY_SYSTEM_CAPABILITY,
        metadata={"reason": "no input-dataset artifact in this manifest"},
    )
    outcome = _evaluate([blocked], stored_baseline, "local")

    assert outcome.metadata["composition_changed"]["removed"] == []
    assert LOCAL_AND_CI in outcome.metadata["composition_changed"]["reported_without_a_score"]
    assert "REG-EVALUATOR-REMOVED" not in {f.id for f in outcome.critical_findings}


def test_profile_membership_matches_the_registry() -> None:
    local = regression_engine.profile_membership("local")
    ci = regression_engine.profile_membership("ci")
    assert LOCAL_AND_CI in local and LOCAL_AND_CI in ci
    assert CI_ONLY not in local and CI_ONLY in ci
    assert local < ci, "local must be a strict subset of ci"
    assert regression_engine.profile_membership(None) is None
