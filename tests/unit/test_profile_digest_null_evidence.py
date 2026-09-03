import pytest

from src.agents.tools.profile_digest import generate_profile_digest


@pytest.mark.parametrize("null_count", [3, 4, 825, 2634])
def test_versioned_null_rates_remain_visible_to_semantic_model(null_count):
    profile = {"netflix": {"table_metadata": {"total_rows": 8807}, "columns": {
        "field": {"type": "string", "null_pct": null_count / 8807},
    }}}
    column = generate_profile_digest(profile)["netflix"]["columns"][0]
    assert column["null_pct"] == pytest.approx(null_count / 8807 * 100)
    assert column["null_pct"] > 0
    assert "no_nulls" not in column.get("signals", [])


@pytest.mark.parametrize("stats, no_nulls", [
    ({}, False),
    ({"null_count": 0}, True),
    ({"null_pct": 0.0}, True),
    ({"null_count": 2}, False),
    ({"null_count": 0, "null_pct": 0.1}, False),
    ({"null_count": 2, "null_pct": 0.0}, False),
])
def test_no_nulls_requires_measured_noncontradictory_evidence(stats, no_nulls):
    profile = {"dataset": {"columns": {"field": {"type": "string", **stats}}}}
    column = generate_profile_digest(profile)["dataset"]["columns"][0]
    assert ("no_nulls" in column.get("signals", [])) is no_nulls
    if not stats:
        assert column["null_pct"] is None
