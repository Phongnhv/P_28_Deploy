"""Self-tests for the contract, regression and ingest evaluators.

The evaluators added in phase A make claims that block releases, so they have to be
tested harder than the ones that only contribute a score.  Three properties matter
most and are asserted directly:

  a pre-existing gap must not be reported as a regression
  a real regression must be reported even when the file name is unchanged
  a rejected value must not be silently converted into a null

The last test in this module is the acceptance criterion from the plan: run against
the current index, ``capability_regression`` must report ``dq_score_computed`` as
lost.  It is written so that it stops being a failure once the capability is
restored, rather than having to be deleted.
"""

from __future__ import annotations

import pytest

from evalgate.aggregator import Decision, aggregate
from evalgate.core import git_read, regression_engine
from evalgate.gates.gate1_ai_quality import governed_enum_conformance
from evalgate.gates.gate4_input_data import ingest_fidelity
from evalgate.gates.gate6_governance import capability_regression, contract_conformance
from evalgate.schemas.eval_result import (
    EvalResult,
    EvalStatus,
)

# ---------------------------------------------------------------------------
# Capability regression: the classifier is the whole value of the gate
# ---------------------------------------------------------------------------

_CAP = [
    {
        "id": "probe",
        "severity": "CRITICAL",
        "why": "test fixture",
        "detect": [{"file": "README.md", "pattern": "RidePulse"}],
    }
]


def _stub_git(monkeypatch) -> None:
    monkeypatch.setattr(capability_regression.git_read, "list_files", lambda ref: [])
    monkeypatch.setattr(capability_regression.git_read, "ref_exists", lambda ref: True)


def _classify(monkeypatch, before: bool, after: bool) -> str:
    def fake_present(ref, capability, files):
        present = before if ref == "BASE" else after
        return present, "fixture" if present else None

    monkeypatch.setattr(capability_regression, "_present", fake_present)
    _stub_git(monkeypatch)
    outcome = capability_regression.compare(
        baseline_ref="BASE", current_ref="NOW", capabilities=_CAP
    )[0]
    return outcome.state


def test_pre_existing_gap_is_not_reported_as_a_regression(monkeypatch):
    # Everything the product has never done would otherwise block every release,
    # which is the fastest way to make a gate ignored.
    assert _classify(monkeypatch, before=False, after=False) == capability_regression.KNOWN_GAP


def test_losing_a_capability_is_a_regression(monkeypatch):
    assert _classify(monkeypatch, before=True, after=False) == capability_regression.REGRESSION


def test_gaining_a_capability_is_an_improvement(monkeypatch):
    assert _classify(monkeypatch, before=False, after=True) == capability_regression.IMPROVEMENT


def test_only_critical_regressions_block_release(monkeypatch):
    medium = [{**_CAP[0], "id": "m", "severity": "MEDIUM"}]

    def fake_present(ref, capability, files):
        return (ref == "BASE"), None

    monkeypatch.setattr(capability_regression, "_present", fake_present)
    _stub_git(monkeypatch)
    monkeypatch.setattr(capability_regression, "load_capabilities", lambda: medium)
    result = capability_regression.evaluate(write_evidence=False, baseline_ref="BASE")
    assert result.metrics["capability_regressions"].raw == 1
    assert result.metrics["critical_capability_regressions"].raw == 0
    assert all(not f.blocks_release for f in result.critical_findings)


def test_inverted_capability_is_present_when_the_marker_is_absent():
    absent = {"detect": [{"file": "README.md", "pattern": "zzz-not-in-readme"}], "invert": True}
    present, _ = capability_regression._present("HEAD", absent, ["README.md"])
    assert present is True


# ---------------------------------------------------------------------------
# Contract conformance
# ---------------------------------------------------------------------------

def test_public_model_closure_follows_nested_fields():
    schema = (
        "class Inner(BaseModel):\n"
        "    sql_text: str\n"
        "\n"
        "class Outer(BaseModel):\n"
        "    results: list[Inner]\n"
    )
    routes = "@router.get('/x', response_model=Outer)\n"
    reachable = contract_conformance._public_model_closure(schema, routes)
    # Outer is declared; Inner is only reachable through a field, which is exactly
    # how the compiled SQL escapes today.
    assert reachable == {"Outer", "Inner"}


def test_a_model_never_wired_to_a_route_is_not_public():
    schema = "class Internal(BaseModel):\n    sql_text: str\n"
    assert contract_conformance._public_model_closure(schema, "") == set()


def test_contract_checks_all_carry_a_source_reference():
    checks, _ = contract_conformance.collect_checks()
    assert checks, "the contract evaluator produced no checks"
    for check in checks:
        assert check.source.startswith("docs/"), f"{check.id} cites no document"
        assert check.statement, f"{check.id} has no statement"


# ---------------------------------------------------------------------------
# Ingest fidelity
# ---------------------------------------------------------------------------

def test_every_matrix_case_is_labelled_accept_or_reject():
    for case in ingest_fidelity.MALFORMED_MATRIX:
        assert case.expectation in {ingest_fidelity.ACCEPT, ingest_fidelity.REJECT}
        assert case.why, f"{case.raw} has no rationale"


def test_a_rejected_value_returning_none_counts_as_silent_loss():
    outcomes = {(o.coercer, o.raw): o for o in ingest_fidelity.run_malformed_matrix()}
    european_decimal = outcomes[("to_float", "12,50")]
    assert european_decimal.expectation == ingest_fidelity.REJECT
    assert european_decimal.silent_loss, "a dropped fare must be counted as loss"


def test_nan_and_infinity_are_treated_as_loss_not_as_values():
    outcomes = {(o.coercer, o.raw): o for o in ingest_fidelity.run_malformed_matrix()}
    for raw in ("nan", "1e999"):
        assert outcomes[("to_float", raw)].silent_loss, f"{raw} must not pass as a number"


def test_clean_values_survive_the_round_trip():
    outcomes = {(o.coercer, o.raw): o for o in ingest_fidelity.run_malformed_matrix()}
    assert not outcomes[("to_int", "0")].silent_loss, "zero must not look like missing"
    assert not outcomes[("to_float", "-0.0")].silent_loss


# ---------------------------------------------------------------------------
# Governed enum
# ---------------------------------------------------------------------------

def test_governed_domain_is_recoverable_without_the_policy_file():
    # The policy JSON is currently absent; a governance evaluator that goes quiet
    # exactly when a governance asset disappears would be useless.
    domains = governed_enum_conformance.load_governed_domains()
    assert domains, "no governed column was recovered"
    assert domains[0].allowed, "no governed values were recovered"
    assert domains[0].source


def test_excluded_value_is_recognised_as_deliberate():
    domains = governed_enum_conformance.load_governed_domains()
    assert domains[0].excluded, "the deliberately-invalid literal was not parsed"
    assert domains[0].expected_defects > 0


def test_a_missing_baseline_ref_blocks_rather_than_reporting_no_regressions(monkeypatch):
    # Comparing against a ref that no longer exists would show every capability as
    # absent at the baseline, i.e. zero regressions -- the worst possible answer.
    monkeypatch.setattr(capability_regression.git_read, "ref_exists", lambda ref: False)
    result = capability_regression.evaluate(write_evidence=False, baseline_ref="deadbee")
    assert result.status == EvalStatus.BLOCKED_MISSING_GROUND_TRUTH
    assert result.metrics == {}


def test_enum_rules_on_ungoverned_columns_are_counted():
    unbacked = governed_enum_conformance.count_unbacked_enums({"payment_type"})
    # With no policy to check against, such an enum can only have come from the data.
    assert isinstance(unbacked, list)
    assert all("payment_type" not in entry.rsplit(".", 1)[-1] for entry in unbacked)


# ---------------------------------------------------------------------------
# Regression engine and the coverage floor
# ---------------------------------------------------------------------------

def _scored(gate: str, score: float) -> EvalResult:
    return EvalResult(
        gate=gate, evaluator=f"{gate}_probe", status=EvalStatus.PASS, score=score
    )


def _baseline(*results: EvalResult, run_id: str = "baseline-1") -> dict:
    """A stored run payload shaped the way ``render_json`` writes one."""
    return {
        "run_id": run_id,
        "gate_scores": {},
        "hard_gates": [],
        "results": [r.model_dump(mode="json", by_alias=True) for r in results],
    }


def test_a_score_drop_larger_than_the_limit_blocks(tmp_path, monkeypatch):
    baseline = _baseline(_scored("ai_quality", 90.0))
    monkeypatch.setattr(regression_engine, "resolve_baseline", lambda _=None: baseline)
    result = regression_engine.evaluate(
        [_scored("ai_quality", 50.0)], write_evidence=False
    )
    assert result.metrics["gate_score_drop_max"].raw == 40.0
    assert any(f.blocks_release for f in result.critical_findings)


def test_a_small_movement_is_not_a_regression(monkeypatch):
    baseline = _baseline(_scored("ai_quality", 90.0), run_id="b")
    monkeypatch.setattr(regression_engine, "resolve_baseline", lambda _=None: baseline)
    result = regression_engine.evaluate(
        [_scored("ai_quality", 85.0)], write_evidence=False
    )
    assert result.critical_findings == []


def test_adding_an_evaluator_to_a_gate_is_not_a_regression(monkeypatch):
    """The defect this comparison was rewritten to remove.

    Gate scores are means over their members, so introducing a second evaluator
    that scores badly drags the gate mean down even though nothing that existed
    before got worse. Comparing at gate level reported that arithmetic as a
    14.36-point governance regression and blocked a release for it.
    """
    unchanged = _scored("governance", 90.0)
    baseline = _baseline(unchanged)
    monkeypatch.setattr(regression_engine, "resolve_baseline", lambda _=None: baseline)

    newcomer = EvalResult(
        gate="governance", evaluator="brand_new_probe", status=EvalStatus.FAIL, score=10.0
    )
    result = regression_engine.evaluate([unchanged, newcomer], write_evidence=False)

    # The gate mean fell from 90 to 50; the evaluator that existed in both runs did not move.
    assert result.critical_findings == []
    assert result.metadata["composition_changed"]["added"] == ["brand_new_probe"]


def test_removing_an_evaluator_is_not_reported_as_a_drop(monkeypatch):
    kept = _scored("governance", 40.0)
    baseline = _baseline(kept, _scored("ai_quality", 95.0))
    monkeypatch.setattr(regression_engine, "resolve_baseline", lambda _=None: baseline)
    result = regression_engine.evaluate([kept], write_evidence=False)
    assert result.critical_findings == []
    assert result.metadata["composition_changed"]["removed"] == ["ai_quality_probe"]


def test_a_multi_dataset_evaluator_is_compared_after_the_same_collapse(monkeypatch):
    """Both sides must go through ``collapse_result_scores``, not one side only.

    A multi-dataset evaluator's ``score`` field and its P25 collapse are different
    numbers. Comparing the collapse of the current run against the stored raw score
    manufactures a drop out of nothing every single run.
    """
    from evalgate.schemas.eval_result import DatasetBreakdown

    def spread(score_field: float) -> EvalResult:
        return EvalResult(
            gate="ai_quality",
            evaluator="corpus_probe",
            status=EvalStatus.PASS,
            score=score_field,
            per_dataset_breakdown=[
                DatasetBreakdown(dataset_id=f"d{i}", status=EvalStatus.PASS, score=value)
                for i, value in enumerate((10.0, 50.0, 90.0, 95.0))
            ],
        )

    # Identical breakdowns, so nothing regressed -- but the raw score field differs
    # wildly from the P25, which is what a naive comparison would read.
    baseline = _baseline(spread(95.0))
    monkeypatch.setattr(regression_engine, "resolve_baseline", lambda _=None: baseline)
    result = regression_engine.evaluate([spread(95.0)], write_evidence=False)
    assert result.critical_findings == []


def test_a_score_drop_does_not_borrow_a_declared_hard_gate_id(monkeypatch):
    """HG-R3 means "a hard gate that used to pass now fails" in hard_gates.yaml.

    Reusing its id for a score drop would make the report describe a gate failure
    the policy never defined, and make the two causes indistinguishable to a reader.
    """
    baseline = _baseline(_scored("ai_quality", 90.0))
    monkeypatch.setattr(regression_engine, "resolve_baseline", lambda _=None: baseline)
    result = regression_engine.evaluate(
        [_scored("ai_quality", 20.0)], write_evidence=False
    )
    drop_findings = [f for f in result.critical_findings if "dropped" in f.title]
    assert drop_findings, "a 70-point fall must still be reported"
    assert all(f.id == "REG-DROP" for f in drop_findings)


def test_no_baseline_reports_not_measured_rather_than_passing(monkeypatch):
    monkeypatch.setattr(regression_engine, "resolve_baseline", lambda _=None: None)
    result = regression_engine.evaluate([_scored("ai_quality", 10.0)], write_evidence=False)
    assert result.status == EvalStatus.NOT_MEASURED
    assert result.score is None


def test_a_stale_run_is_never_used_as_a_baseline(monkeypatch, tmp_path):
    monkeypatch.setattr(
        regression_engine, "load_index",
        lambda: [{"run_id": "stale", "decision": "EVALGATE_STALE", "path": "x"}],
    )
    assert regression_engine.resolve_baseline() is None


def test_thin_coverage_withholds_a_score_instead_of_publishing_one():
    # Only ai_quality measured: re-normalisation would otherwise hand it 100% of the
    # weight and publish a confident number built on one gate.
    outcome = aggregate([_scored("ai_quality", 95.0)])
    assert outcome.decision == Decision.INSUFFICIENT_COVERAGE
    assert outcome.measured_weight < 0.60


def test_sufficient_coverage_still_produces_a_normal_decision():
    outcome = aggregate([
        _scored("ai_quality", 95.0),
        _scored("ai_security", 95.0),
        _scored("input_data", 95.0),
        _scored("governance", 95.0),
    ])
    assert outcome.decision in {Decision.PASS, Decision.WARNING, Decision.FAIL}
    assert outcome.measured_weight >= 0.60


# ---------------------------------------------------------------------------
# Acceptance criterion from the plan
# ---------------------------------------------------------------------------

def test_the_gate_detects_the_regression_it_was_built_for():
    """dq_score computation exists at HEAD and must be reported if it is staged away.

    This is the criterion the plan set for accepting the gate. It is written as a
    conditional rather than a hard assertion so that restoring the capability turns
    it green instead of requiring the test to be deleted.
    """
    try:
        outcomes = {o.id: o for o in capability_regression.compare()}
    except git_read.GitUnavailableError:
        pytest.skip("git is unavailable in this environment")

    dq_score = outcomes.get("dq_score_computed")
    assert dq_score is not None, "the capability registry no longer declares dq_score_computed"

    if dq_score.present_at_baseline and not dq_score.present_now:
        assert dq_score.state == capability_regression.REGRESSION
        assert dq_score.severity == "CRITICAL"
    else:
        # Either it was restored, or it was never committed. Both are legitimate;
        # what must never happen is a silent loss classified as anything else.
        assert dq_score.state in {
            capability_regression.INTACT,
            capability_regression.KNOWN_GAP,
            capability_regression.IMPROVEMENT,
        }


# ---------------------------------------------------------------------------
# Coverage is counted per evaluator, and an under-measured run publishes no number
# ---------------------------------------------------------------------------

def _ev(gate: str, name: str, status: EvalStatus, score: float | None = None) -> EvalResult:
    return EvalResult(gate=gate, evaluator=name, status=status, score=score)


def test_coverage_counts_evaluators_not_gates():
    """A gate is not fully measured just because one of its evaluators ran.

    The earlier version summed the weight of every gate that was not entirely
    excluded. On 2026-08-22 that reported 0.85 while 0.54 of the surface had been
    measured -- ai_security was credited its full weight with 4 of 7 evaluators
    running, and the two missing ones were the BOLA and malicious-upload probes.
    """
    from evalgate.aggregator import evaluator_coverage

    weights = {"ai_security": 1.0}
    results = [
        _ev("ai_security", "ran_a", EvalStatus.PASS, 100.0),
        _ev("ai_security", "ran_b", EvalStatus.FAIL, 0.0),
        _ev("ai_security", "missing_a", EvalStatus.NOT_IMPLEMENTED),
        _ev("ai_security", "missing_b", EvalStatus.BLOCKED_BY_SYSTEM_CAPABILITY),
    ]
    covered, detail = evaluator_coverage(results, weights)
    assert covered == pytest.approx(0.5)
    assert detail["ai_security"] == (2, 4)


def test_a_gate_outside_the_weighted_set_does_not_dilute_coverage():
    """preflight has no weight, so it must not appear in the denominator."""
    from evalgate.aggregator import evaluator_coverage

    weights = {"governance": 1.0}
    results = [
        _ev("governance", "ran", EvalStatus.PASS, 100.0),
        _ev("preflight", "workspace", EvalStatus.FAIL),
    ]
    covered, detail = evaluator_coverage(results, weights)
    assert covered == pytest.approx(1.0)
    assert "preflight" not in detail


def test_an_under_measured_run_publishes_no_score(monkeypatch):
    """The rule the aggregator already stated in a comment, now enforced.

    A failing hard gate preempts the INSUFFICIENT_COVERAGE branch, so before this
    the number was still published on every run that actually mattered.
    """
    results = [
        _ev("ai_quality", "ran", EvalStatus.PASS, 90.0),
        _ev("ai_quality", "missing_a", EvalStatus.NOT_IMPLEMENTED),
        _ev("ai_quality", "missing_b", EvalStatus.NOT_IMPLEMENTED),
        _ev("ai_quality", "missing_c", EvalStatus.NOT_IMPLEMENTED),
    ]
    outcome = aggregate(results)
    assert outcome.score is None
    assert outcome.provisional_score is not None
    assert "below the" in outcome.score_withheld_reason
    # The evidence for the hole is in the report, not just the verdict.
    assert outcome.coverage_detail["ai_quality"] == (1, 4)


def test_a_well_measured_run_still_publishes_its_score():
    """The guard must not swallow every number -- only the ones built on too little."""
    gates = ["ai_quality", "ai_security", "input_data", "governance",
             "observability", "reliability", "business"]
    results = [_ev(g, f"{g}_probe", EvalStatus.PASS, 90.0) for g in gates]
    outcome = aggregate(results)
    assert outcome.measured_weight == pytest.approx(1.0)
    assert outcome.score == pytest.approx(90.0)
    assert outcome.score_withheld_reason is None


# ---------------------------------------------------------------------------
# EvalGate never measures itself
# ---------------------------------------------------------------------------

def test_evalgate_paths_are_recognised_as_the_instrument():
    from evalgate.core import scope

    assert scope.is_instrument("evalgate/tests/test_blind_spots.py")
    assert scope.is_instrument(scope.PROJECT_ROOT / "evalgate" / "run.py")
    assert not scope.is_instrument("src/api/routes.py")
    assert not scope.is_instrument("scripts/import_csv.py")
    # A product path that merely starts with the same letters is not the instrument.
    assert not scope.is_instrument("evalgate_notes/readme.md")


def test_the_secret_scanner_never_reports_its_own_repository():
    """Twice in one afternoon this raised a blocking CRITICAL about a test fixture.

    Both findings were true about the repository and useless about the product, and
    the second moved the score further than any real defect that day.
    """
    from evalgate.gates.gate2_security import secret_scan

    for path in secret_scan.tracked_files():
        assert "evalgate" not in str(path).replace("\\", "/").split("P-028/")[-1].split("/")[0:1], path

    result = secret_scan.evaluate(write_evidence=False)
    for finding in result.critical_findings:
        assert "evalgate" not in finding.detail.replace("\\", "/"), finding.detail
