"""The semantic vocabulary has to be closed, because behaviour branches on it.

``rule_candidate_builder_node`` clamps a RANGE lower bound to zero only when the
type is exactly ``currency``. A model answering ``money`` produces a reading that is
correct in substance and inert in effect: the clamp never fires, the bound is
learned from data that already contains negative fares, and the rule admits every
value it was written to catch.

Golden selectors bind to the same strings, so a drifting vocabulary also removes
coverage silently -- the case resolves to nothing and is skipped rather than failed.
"""

from __future__ import annotations

import pytest

from src.models.semantic_contract import (
    SemanticColumn,
    SemanticType,
    normalize_semantic_type,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("currency", SemanticType.CURRENCY),
        ("money", SemanticType.CURRENCY),
        ("Price", SemanticType.CURRENCY),
        ("identifier", SemanticType.IDENTIFIER),
        ("uuid", SemanticType.IDENTIFIER),
        ("datetime", SemanticType.TIMESTAMP),
        ("INT", SemanticType.NUMERIC),
        ("enum", SemanticType.CATEGORY),
        ("PII", SemanticType.PII),
        ("pii", SemanticType.PII),
    ],
)
def test_synonyms_normalise_onto_the_vocabulary(raw: str, expected: SemanticType) -> None:
    assert normalize_semantic_type(raw) is expected


@pytest.mark.parametrize("raw", ["made_up", "", None, "  ", 42])
def test_anything_unrecognised_becomes_unknown(raw: object) -> None:
    """Recorded, not raised.

    Rejecting one odd column would fail the whole table's contract and hand the
    result to the name-based heuristic, replacing a mostly-correct reading with a
    guess. Unknown is visible, countable, and fires no rule template.
    """
    assert normalize_semantic_type(raw) is SemanticType.UNKNOWN


def test_the_model_coerces_on_construction() -> None:
    column = SemanticColumn(
        name="fare_amount", semantic_type="money", business_role="transaction_amount"
    )
    assert column.semantic_type is SemanticType.CURRENCY


def test_values_still_compare_as_plain_strings() -> None:
    """Downstream branches compare against literals and must keep working."""
    column = SemanticColumn(name="c", semantic_type="money", business_role="r")
    assert column.semantic_type == "currency"
    assert column.semantic_type in ("currency", "numeric")


def test_pii_keeps_its_exact_casing() -> None:
    """rule_candidate_builder_node compares against the literal "PII"."""
    assert SemanticType.PII.value == "PII"
    assert SemanticColumn(name="c", semantic_type="personal", business_role="r").semantic_type == "PII"


def test_currency_is_distinguishable_from_numeric() -> None:
    """The distinction the non-negative clamp depends on."""
    assert SemanticType.CURRENCY != SemanticType.NUMERIC
    assert normalize_semantic_type("amount") is not SemanticType.NUMERIC
