"""Self-tests for the vacuity probe.

The probe makes a strong claim -- "this rule can never fail" -- so the risk that
matters most is a false positive. Calling a legitimate guard dead weight would
teach the team to ignore the gate, which is worse than not having it.

Most of these tests therefore assert the *negative* direction: a rule that can fire
must not be reported as vacuous.
"""

from __future__ import annotations

import pandas as pd
import pytest

from evalgate.gates.gate1_ai_quality import vacuity_probe as vp


@pytest.fixture
def frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "status": ["NEW", "PAID", "SHIPPED"] * 10,
            "amount": [1.0, -2.0, 50.0] * 10,
            "clean": list(range(30)),
            "leaky": [None] * 6 + list(range(24)),
        }
    )


def _rule(rule_type: str, column: str | None, **params) -> dict:
    return {
        "rule_id": f"t.{column}.{rule_type}",
        "table_name": "t",
        "column": column,
        "rule_type": rule_type,
        "parameters": params,
    }


# ---------------------------------------------------------------------------
# The finding this probe exists for
# ---------------------------------------------------------------------------

def test_an_allow_list_covering_every_observed_value_is_vacuous(frame):
    rule = _rule("ACCEPTED_VALUES", "status", accepted_values=["NEW", "PAID", "SHIPPED"])
    assert vp.judge_rule(rule, frame).verdict == "VACUOUS"


def test_an_allow_list_missing_an_observed_value_can_fire(frame):
    rule = _rule("ACCEPTED_VALUES", "status", accepted_values=["NEW", "PAID"])
    assert vp.judge_rule(rule, frame).verdict == "CAN_FIRE"


def test_range_containing_the_observed_extremes_is_vacuous(frame):
    rule = _rule("RANGE", "amount", min=-100, max=100)
    assert vp.judge_rule(rule, frame).verdict == "VACUOUS"


def test_range_that_excludes_negatives_can_fire(frame):
    # amount holds -2.0, so a floor of zero is a live rule, not a dead one.
    rule = _rule("RANGE", "amount", min=0)
    assert vp.judge_rule(rule, frame).verdict == "CAN_FIRE"


def test_null_rate_threshold_above_the_observed_rate_is_vacuous(frame):
    rule = _rule("NULL_RATE", "leaky", max_null_pct=99.0)
    assert vp.judge_rule(rule, frame).verdict == "VACUOUS"


def test_null_rate_threshold_below_the_observed_rate_can_fire(frame):
    rule = _rule("NULL_RATE", "leaky", max_null_pct=1.0)
    assert vp.judge_rule(rule, frame).verdict == "CAN_FIRE"


# ---------------------------------------------------------------------------
# Guarding against overreach
# ---------------------------------------------------------------------------

def test_a_satisfied_guard_is_never_called_vacuous(frame):
    # NOT_NULL on a clean column passes today and exists to catch tomorrow. Calling
    # it dead weight is exactly the false positive that would discredit the probe.
    verdict = vp.judge_rule(_rule("NOT_NULL", "clean"), frame)
    assert verdict.verdict == "NOT_JUDGED"
    assert verdict.reason


def test_every_unjudged_type_states_why(frame):
    for rule_type in vp.NOT_JUDGED:
        verdict = vp.judge_rule(_rule(rule_type, "clean"), frame)
        assert verdict.verdict == "NOT_JUDGED"
        assert verdict.reason.strip(), f"{rule_type} is skipped without saying why"


def test_a_row_count_floor_of_zero_is_vacuous_but_a_real_floor_is_not(frame):
    assert vp.judge_rule(_rule("ROW_COUNT", None, min_row_count=0), frame).verdict == "VACUOUS"
    assert vp.judge_rule(_rule("ROW_COUNT", None), frame).verdict == "VACUOUS"
    # 25 of 30 rows: a genuine floor that fires on real loss.
    assert vp.judge_rule(_rule("ROW_COUNT", None, min_row_count=25), frame).verdict == "CAN_FIRE"


def test_a_technically_live_but_absurd_floor_is_degenerate_not_vacuous():
    # Fires only if the table empties. Live, so not vacuous -- but naming it matters.
    # The band only exists on a table large enough for "1" to be absurd; on 30 rows
    # a floor of 1 is merely low, and the probe correctly declines to editorialise.
    big = pd.DataFrame({"x": range(50_000)})
    assert vp.judge_rule(_rule("ROW_COUNT", None, min_row_count=1), big).verdict == "DEGENERATE"
    small = pd.DataFrame({"x": range(30)})
    assert vp.judge_rule(_rule("ROW_COUNT", None, min_row_count=1), small).verdict == "CAN_FIRE"


def test_a_missing_column_is_not_data_rather_than_a_failure(frame):
    verdict = vp.judge_rule(_rule("ACCEPTED_VALUES", "absent", accepted_values=["x"]), frame)
    assert verdict.verdict == "NO_DATA"


def test_an_empty_allow_list_can_fire(frame):
    # Every row violates an empty allow-list, so the rule is live, not vacuous.
    assert vp.judge_rule(_rule("ACCEPTED_VALUES", "status"), frame).verdict == "CAN_FIRE"


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------

def test_evaluator_reports_a_rate_and_names_its_worst_type():
    result = vp.evaluate(write_evidence=False)
    if result.status.value.startswith(("BLOCKED", "NOT_")):
        pytest.skip(f"not runnable here: {result.metadata.get('reason')}")
    rate = result.metrics["vacuous_rule_rate"].raw
    assert 0.0 <= rate <= 1.0
    assert result.metadata.get("worst_type"), "the report must name the worst rule type"
    for breakdown in result.per_dataset_breakdown:
        assert breakdown.reason, f"{breakdown.dataset_id} has no explanation"


def test_no_ground_truth_is_consulted():
    """The probe must not depend on the golden set; that is the whole point of it.

    If it did, it would stop working for the datasets it exists to serve -- the ones
    nobody has labelled. Checked against the module's imports rather than its prose,
    so that explaining the distinction in a comment does not fail the test.
    """
    import ast

    tree = ast.parse(open(vp.__file__, encoding="utf-8").read())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    forbidden = [m for m in imported if "golden" in m or "sdih" in m]
    assert not forbidden, f"vacuity_probe imports {forbidden}; it must need no ground truth"
