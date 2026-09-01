"""Golden cases must know which datasets they are statements about.

Before applicability existed, every case ran against every bundle. A case naming
`fare_amount` produced a hard FAIL on a clinical dataset -- penalising the agent for
not proposing a rule on a column that does not exist -- while `rule_not_on_columns`
and `forbidden_tokens` passed vacuously and quietly raised the score. Both
directions were wrong, and the suite-level `dataset_id` that was supposed to prevent
it was never read by the runner.
"""

from __future__ import annotations

import pytest

from evalgate.golden.applicability import (
    DatasetContext,
    SemanticColumn,
    resolve,
    resolve_evidence_ref,
    semantic_vocabulary,
)
from evalgate.golden.schema import Applicability, Assertion, GoldenCase, GoldenSuite


def _case(case_id: str, applies_to: Applicability, assertion_type: str = "rule_proposed") -> GoldenCase:
    return GoldenCase(
        id=case_id,
        tier=2,
        intent="test case",
        source="test",
        ground_truth_owner="test",
        applies_to=applies_to,
        assertions=[Assertion(type=assertion_type, rule_type="RANGE", column="amount")],
    )


@pytest.fixture
def taxi() -> DatasetContext:
    columns = {"fare_amount": {"min_value": -2.5, "quantiles": {"p05": 1.0}}, "vendor_id": {}}
    return DatasetContext(
        dataset_id="dataset-import-abc123",
        corpus_id="corpus-nyc-taxi-50k",
        columns=("fare_amount", "vendor_id", "source_row_id"),
        semantic=(
            SemanticColumn("fare_amount", "currency", "transaction_amount", False),
            SemanticColumn("vendor_id", "identifier", "primary_key", False),
        ),
        relationships=({"left_column": "pickup_at", "right_column": "dropoff_at"},),
        evidence_keys=frozenset({"profile.row_count"}),
        profile_columns=columns,
        profile_top_level=frozenset({"row_count", "duplicate_rate"}),
    )


@pytest.fixture
def clinical() -> DatasetContext:
    return DatasetContext(
        dataset_id="dataset-import-def456",
        corpus_id="corpus-synth-clinical",
        columns=("patient_name", "insurance_pct", "source_row_id"),
        semantic=(SemanticColumn("insurance_pct", "numeric", "measure", True),),
        profile_columns={"insurance_pct": {"min_value": 0.0}},
    )


# --- dataset binding -------------------------------------------------------

def test_dataset_bound_case_matches_its_own_corpus(taxi) -> None:
    scope = resolve(_case("C", Applicability(dataset_id="corpus-nyc-taxi-50k")), taxi)
    assert scope.applicable


def test_dataset_bound_case_skips_a_different_corpus(clinical) -> None:
    scope = resolve(_case("C", Applicability(dataset_id="corpus-nyc-taxi-50k")), clinical)
    assert not scope.applicable


def test_binding_uses_the_corpus_id_not_the_runtime_dataset_id(taxi) -> None:
    """The runtime id is minted per upload and can never be written down in advance.

    Every one of the nine original cases resolved to NOT_APPLICABLE against a real
    bundle because the suites named a dataset the product never calls itself.
    """
    assert taxi.dataset_id.startswith("dataset-import-")
    assert resolve(_case("C", Applicability(dataset_id="corpus-nyc-taxi-50k")), taxi).applicable
    assert not resolve(_case("C", Applicability(dataset_id=taxi.dataset_id[:8])), taxi).applicable


# --- semantic binding ------------------------------------------------------

def test_semantic_selector_resolves_to_the_matching_columns(taxi) -> None:
    scope = resolve(_case("C", Applicability(semantic_type="currency")), taxi)
    assert scope.applicable
    assert scope.columns == ("fare_amount",)


def test_semantic_selector_skips_a_dataset_without_that_meaning(clinical) -> None:
    """Having no currency column is not a failure to have one."""
    scope = resolve(_case("C", Applicability(semantic_type="currency")), clinical)
    assert not scope.applicable


def test_semantic_selector_reports_a_missing_contract_distinctly(taxi) -> None:
    """No interpretation is a different state from no matching column."""
    bare = DatasetContext(dataset_id="d", columns=("a",))
    scope = resolve(_case("C", Applicability(semantic_type="currency")), bare)
    assert not scope.applicable
    assert "semantic contract" in scope.reason


def test_business_role_narrows_the_selection(taxi) -> None:
    scope = resolve(_case("C", Applicability(business_role="primary_key")), taxi)
    assert scope.columns == ("vendor_id",)


# --- platform binding ------------------------------------------------------

def test_platform_invariant_applies_everywhere(taxi, clinical) -> None:
    case = _case("C", Applicability(always=True))
    assert resolve(case, taxi).applicable
    assert resolve(case, clinical).applicable


def test_named_platform_column_applies_wherever_it_exists(taxi, clinical) -> None:
    case = _case("C", Applicability(columns=["source_row_id"]))
    assert resolve(case, taxi).applicable
    assert resolve(case, clinical).applicable


def test_named_column_absent_is_not_applicable(taxi) -> None:
    assert not resolve(_case("C", Applicability(columns=["nonexistent"])), taxi).applicable


# --- suite inheritance -----------------------------------------------------

def test_suite_dataset_id_is_inherited_by_unscoped_cases() -> None:
    suite = GoldenSuite(
        version="2.0",
        dataset_id="corpus-nyc-taxi-50k",
        cases=[_case("UNSCOPED", Applicability())],
    )
    assert suite.resolved_cases()[0].applies_to.dataset_id == "corpus-nyc-taxi-50k"


def test_a_case_with_its_own_scope_does_not_inherit() -> None:
    """GC-E5 is about a platform surrogate key, not about one taxi fixture."""
    suite = GoldenSuite(
        version="2.0",
        dataset_id="corpus-nyc-taxi-50k",
        cases=[_case("OWN", Applicability(columns=["source_row_id"]))],
    )
    resolved = suite.resolved_cases()[0].applies_to
    assert resolved.dataset_id is None
    assert resolved.columns == ["source_row_id"]


# --- evidence resolution ---------------------------------------------------

def test_structural_citation_resolves_without_being_published(taxi) -> None:
    """profile.evidence_keys under-publishes; the figure is still really there.

    Membership-only checking reported 36 of 55 genuine citations as dangling,
    which would have failed the gate on the vocabulary rather than on the agent.
    """
    assert "profile.column.fare_amount.min_value" not in taxi.evidence_keys
    assert resolve_evidence_ref("profile.column.fare_amount.min_value", taxi)


def test_quantile_citation_resolves(taxi) -> None:
    assert resolve_evidence_ref("profile.column.fare_amount.quantile.p05", taxi)
    assert not resolve_evidence_ref("profile.column.fare_amount.quantile.p99", taxi)


def test_published_key_still_resolves(taxi) -> None:
    assert resolve_evidence_ref("profile.row_count", taxi)


def test_invented_citation_does_not_resolve(taxi) -> None:
    assert not resolve_evidence_ref("profile.column.fare_amount.made_up", taxi)
    assert not resolve_evidence_ref("profile.column.no_such_column.min_value", taxi)


# --- vocabulary reporting --------------------------------------------------

def test_vocabulary_makes_a_selector_miss_diagnosable(taxi) -> None:
    """A selector matching nothing is ambiguous without the observed vocabulary."""
    assert semantic_vocabulary(taxi) == {"currency": 1, "identifier": 1}
