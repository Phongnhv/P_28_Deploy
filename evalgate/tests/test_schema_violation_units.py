"""schema_violation_rate must compare items with items.

The numerator once summed Pydantic's field-error counts while the denominator
counted accepted rules, so "15 validation errors for TableRuleProposal" -- a single
badly-shaped proposal -- contributed 15 to a ratio whose other side was measured in
rules. HG-A2 escalates to CRITICAL above 50%, which that mismatch could reach on its
own.
"""

from __future__ import annotations

from evalgate.gates.gate1_ai_quality.run_outcome_integrity import (
    RunOutcome,
    schema_violation_rate,
)


def _run(*, accepted: int, rejected: int, field_errors: int = 0) -> RunOutcome:
    return RunOutcome(
        run_id="r" * 32,
        workflow="proposal",
        started_at="20260830T000000",
        reached_terminal=True,
        output_count=accepted,
        schema_accepted=accepted,
        schema_rejections=rejected,
        validation_errors=field_errors,
    )


def test_rate_counts_items_not_field_errors() -> None:
    """One bad proposal among ten is 10%, however many fields it got wrong."""
    rate = schema_violation_rate([_run(accepted=9, rejected=1, field_errors=15)])
    assert rate == 1 / 10


def test_field_error_volume_does_not_move_the_rate() -> None:
    """Severity of a rejection is diagnostic; it must not inflate the ratio."""
    mild = schema_violation_rate([_run(accepted=9, rejected=1, field_errors=1)])
    severe = schema_violation_rate([_run(accepted=9, rejected=1, field_errors=99)])
    assert mild == severe


def test_rate_stays_below_the_critical_band_for_a_single_bad_item() -> None:
    """The regression this pins: 15/(15+9) = 0.625 used to trip CRITICAL."""
    rate = schema_violation_rate([_run(accepted=9, rejected=1, field_errors=15)])
    assert rate is not None and rate < 0.5


def test_no_denominator_reports_none_rather_than_clean() -> None:
    assert schema_violation_rate([_run(accepted=0, rejected=0)]) is None


def test_rate_is_summed_across_the_window() -> None:
    runs = [_run(accepted=4, rejected=1), _run(accepted=4, rejected=1)]
    assert schema_violation_rate(runs) == 2 / 10


def test_all_items_rejected_is_total_failure() -> None:
    assert schema_violation_rate([_run(accepted=0, rejected=3, field_errors=40)]) == 1.0
