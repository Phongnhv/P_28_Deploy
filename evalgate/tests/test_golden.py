"""Self-tests for the golden dataset and the evaluator that runs it.

The golden set is the reference everything else is compared against, so its own
integrity has to be checked first. Three properties carry the most weight:

  a case must cite where its expectation came from
  an assertion with nothing to inspect must not count as a failure
  the frozen labels must still match what the generator produces
"""

from __future__ import annotations

import pytest

from evalgate.corpus.generator import ARCHETYPES, generate
from evalgate.gates.gate1_ai_quality import golden_conformance as gc
from evalgate.golden import freeze
from evalgate.golden.schema import Assertion, GoldenCase, load_all_suites
from evalgate.sdih.injector import inject

# ---------------------------------------------------------------------------
# Suite integrity
# ---------------------------------------------------------------------------

def test_every_suite_parses():
    suites = load_all_suites()
    assert suites, "no golden suite was found"


def test_every_case_cites_a_source_and_an_owner():
    # A case with no source is an opinion, and an opinion must not block a release.
    for path, suite in load_all_suites():
        for case in suite.cases:
            assert case.source.strip(), f"{path.name}:{case.id} cites no source"
            assert case.ground_truth_owner.strip(), f"{path.name}:{case.id} has no owner"
            assert case.intent.strip(), f"{path.name}:{case.id} explains nothing"


def test_case_ids_are_unique_across_suites():
    seen: dict[str, str] = {}
    for path, suite in load_all_suites():
        for case in suite.cases:
            assert case.id not in seen, f"{case.id} duplicated in {path.name} and {seen[case.id]}"
            seen[case.id] = path.name


def test_every_assertion_has_a_handler():
    for _path, suite in load_all_suites():
        for case in suite.cases:
            for assertion in case.assertions:
                assert assertion.type in gc._HANDLERS, f"{case.id}: no handler for {assertion.type}"


# ---------------------------------------------------------------------------
# Assertion semantics
# ---------------------------------------------------------------------------

def _case(assertion: Assertion, tier: int = 2) -> GoldenCase:
    return GoldenCase(
        id="T", tier=tier, intent="fixture", source="test", ground_truth_owner="test",
        assertions=[assertion],
    )


def test_nothing_to_inspect_is_not_a_failure():
    # "we did not look" and "we looked and it was wrong" are different claims.
    outcome = gc.run_case(
        _case(Assertion(type="forbidden_tokens", field="nope", tokens=["x"]), tier=3),
        [{"rule_id": "r"}],
        [],
    )
    assert outcome.measurable is False
    assert outcome.passed is False  # not counted either way; the rate excludes it


def test_a_present_violation_is_a_failure():
    outcome = gc.run_case(
        _case(Assertion(type="rule_not_on_columns", rule_type="UNIQUE", columns=["k"])),
        [{"rule_type": "UNIQUE", "column": "k", "table_name": "t"}],
        [],
    )
    assert outcome.measurable is True
    assert outcome.passed is False


def test_absence_of_a_required_rule_is_measurable_and_fails():
    # Unlike a missing field, a missing rule IS the finding, so it must be scored.
    outcome = gc.run_case(
        _case(Assertion(type="rule_proposed", column="c", rule_type="RANGE")), [], []
    )
    assert outcome.measurable is True
    assert outcome.passed is False


def test_enum_assertion_fails_only_on_the_excluded_value():
    good = [{"rule_type": "ACCEPTED_VALUES", "column": "c",
             "parameters": {"accepted_values": ["a", "b"]}, "__artifact__": "x"}]
    bad = [{"rule_type": "ACCEPTED_VALUES", "column": "c",
            "parameters": {"accepted_values": ["a", "BAD"]}, "__artifact__": "x"}]
    assertion = Assertion(type="enum_from_policy", column="c", must_exclude=["BAD"])
    assert gc.run_case(_case(assertion), good, []).passed is True
    assert gc.run_case(_case(assertion), bad, []).passed is False


def test_parameter_bound_rejects_a_negative_lower_bound():
    rules = [{"rule_type": "RANGE", "column": "c", "parameters": {"min": -5.0}}]
    assertion = Assertion(
        type="parameter_bound", column="c", rule_type="RANGE", parameter="min", minimum=0
    )
    assert gc.run_case(_case(assertion), rules, []).passed is False


def test_min_violations_reads_execution_results_not_proposals():
    results = [{"rule_id": "t.col.ACCEPTED_VALUES", "failed_count": 9}]
    assertion = Assertion(type="min_violations", rule_suffix="col.ACCEPTED_VALUES", at_least=4)
    assert gc.run_case(_case(assertion), [], results).passed is True
    assertion_high = Assertion(
        type="min_violations", rule_suffix="col.ACCEPTED_VALUES", at_least=99
    )
    assert gc.run_case(_case(assertion_high), [], results).passed is False


# ---------------------------------------------------------------------------
# Frozen tier 1
# ---------------------------------------------------------------------------

def test_frozen_labels_still_match_the_generator():
    ok, problems = freeze.verify()
    assert ok, "frozen golden labels drifted from the generator: " + "; ".join(problems)


def test_freeze_is_deterministic():
    first = freeze.build_labels("corpus-synth-tiny")[0]
    second = freeze.build_labels("corpus-synth-tiny")[0]
    assert first.fingerprint() == second.fingerprint()


@pytest.mark.parametrize("dataset_id", sorted(ARCHETYPES))
def test_every_label_matches_the_data_it_describes(dataset_id):
    """Integrity is not correctness.

    A fingerprint proves the file was not edited; it says nothing about whether the
    labels are true. The donor bug produced labels claiming a duplicate that did not
    exist, and the fingerprint was identical either way because it does not encode
    the donor. Only semantic verification catches that.
    """
    _store, _plan, report = freeze.build_labels(dataset_id)
    assert report.passed, (
        f"{dataset_id}: {len(report.failures)} label(s) contradict the data. "
        f"First: {report.failures[:1]}"
    )


@pytest.mark.parametrize("dataset_id", sorted(ARCHETYPES))
def test_relational_defects_own_their_column_exclusively(dataset_id):
    """DUPLICATE_ROW and CROSS_FIELD_VIOLATION must not share a target column.

    Their labels are claims about a relationship between rows, so any other class
    writing into the same column can falsify them. Disjoint row positions are not
    enough.
    """
    _store, plan, _report = freeze.build_labels(dataset_id)
    claimed: set[str] = set()
    for name in ("DUPLICATE_ROW", "CROSS_FIELD_VIOLATION"):
        target = plan.targets.get(name)
        if target:
            claimed.update(target.split("|"))
    others = {
        column
        for name, target in plan.targets.items()
        if name not in {"DUPLICATE_ROW", "CROSS_FIELD_VIOLATION"}
        for column in target.split("|")
    }
    assert not (claimed & others), (
        f"{dataset_id}: {sorted(claimed & others)} is claimed by a relational defect "
        f"and also written by a cell-local one"
    )


def test_duplicate_donor_is_never_itself_a_target():
    """The row a key is copied from must survive the loop unchanged."""
    from evalgate.sdih.injector import _disjoint_slices, build_plan
    from evalgate.sdih.profiler import profile_dataframe

    frame = generate("corpus-synth-retail", seed=freeze.SEED, rows=3000)
    plan = build_plan(
        frame, dataset_id="t", seed=freeze.SEED, profile=profile_dataframe(frame)
    )
    positions = {int(p) for p in _disjoint_slices(len(frame), plan, plan.seed)["DUPLICATE_ROW"]}
    _dirty, store = inject(frame, plan, id_column="order_id")
    donors = {
        int(label.detail.rsplit(" ", 1)[-1])
        for label in store.labels
        if label.defect.value == "DUPLICATE_ROW" and label.detail
    }
    assert not (donors & positions), "a donor row is also being overwritten"


def test_manifest_records_a_seed_and_a_fingerprint_per_dataset():
    from evalgate.golden.schema import load_manifest

    manifest = load_manifest()
    assert manifest.get("sdih_seed"), "the manifest does not record the seed"
    datasets = manifest.get("datasets") or {}
    assert datasets, "the manifest records no dataset"
    for name, entry in datasets.items():
        if entry.get("status") == "UNAVAILABLE":
            continue
        assert entry.get("fingerprint"), f"{name} has no fingerprint"


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------

def test_evaluator_runs_and_separates_measured_from_unmeasured():
    result = gc.evaluate(write_evidence=False)
    if result.status.value.startswith(("BLOCKED", "NOT_")):
        pytest.skip(f"golden evaluator not runnable here: {result.metadata.get('reason')}")
    rate = result.metrics["golden_case_pass_rate"].raw
    assert 0.0 <= rate <= 1.0
    statuses = {b.status.value for b in result.per_dataset_breakdown}
    # The suite deliberately contains a case that cannot be inspected against the
    # archived artefacts; it must surface as NOT_MEASURED rather than as a failure.
    assert statuses <= {"PASS", "FAIL", "NOT_MEASURED"}
