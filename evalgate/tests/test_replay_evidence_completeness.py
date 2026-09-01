"""Recall must not be asserted from a truncated record of what a rule flagged.

The product records two lists of failing row ids: a 20-row illustration for a
steward, and a bounded evidence list for measurement. Scoring recall off the
illustration caps it at 20/|defects| -- roughly 0.8% on this fixture, against a
gate demanding 80% -- so a perfect agent and a broken one produce the same number.

When the record is short, HG-A1 is left NOT_EVALUATED rather than asserted. That
still blocks a release outside `local`, because the gate is mandatory; it just
stops attributing the gap to the agent.
"""

from __future__ import annotations

from evalgate.gates.gate1_ai_quality import replay_evaluator as replay
from evalgate.schemas.eval_result import EvalStatus

TRUTH = {"SIGN_FLIP": {"fare_amount": [f"r{i}" for i in range(100)]}}


def _run(*, ids: list[str], violation_count: int, truncated: bool | None = None) -> dict:
    entry = {
        "rule_id": "source_rows.fare_amount.RANGE",
        "column": "fare_amount",
        "rule_type": "RANGE",
        "status": "FAIL",
        "violation_count": violation_count,
        "total_rows": 1000,
        "violation_row_ids": ids,
    }
    if truncated is not None:
        entry["violation_row_ids_truncated"] = truncated
    return {"__path__": "execution/results.json", "dataset_id": "d", "test_results": [entry]}


def test_complete_evidence_is_scored() -> None:
    scored = replay.score_run(_run(ids=[f"r{i}" for i in range(100)], violation_count=100), TRUTH)
    assert scored["evidence_complete"] is True
    assert scored["recall_by_class"]["SIGN_FLIP"] == 1.0


def test_short_record_is_marked_truncated() -> None:
    """The rule found 100 rows and recorded 20: the record is incomplete."""
    scored = replay.score_run(_run(ids=[f"r{i}" for i in range(20)], violation_count=100), TRUTH)
    assert scored["evidence_complete"] is False
    assert "source_rows.fare_amount.RANGE" in scored["truncated_rules"]


def test_explicit_truncation_flag_is_honoured() -> None:
    scored = replay.score_run(
        _run(ids=[f"r{i}" for i in range(100)], violation_count=100, truncated=True), TRUTH
    )
    assert scored["evidence_complete"] is False


def test_evidence_list_is_preferred_over_the_illustration() -> None:
    """sample_refs must not win when the evidence list is present."""
    entry = {
        "rule_id": "source_rows.fare_amount.RANGE",
        "column": "fare_amount",
        "rule_type": "RANGE",
        "status": "FAIL",
        "violation_count": 100,
        "sample_refs": ["r0", "r1"],
        "violation_row_ids": [f"r{i}" for i in range(100)],
    }
    ids, truncated = replay._flagged_rows(entry)
    assert len(ids) == 100
    assert truncated is False


def test_illustration_alone_is_treated_as_truncated() -> None:
    """Older artifacts carry only sample_refs; they must not be scored as complete."""
    entry = {
        "rule_id": "r.c.RANGE",
        "status": "FAIL",
        "violation_count": 2584,
        "sample_refs": [f"r{i}" for i in range(20)],
    }
    ids, truncated = replay._flagged_rows(entry)
    assert len(ids) == 20
    assert truncated is True


def test_truncated_evidence_withholds_hg_a1_instead_of_failing_it(monkeypatch) -> None:
    """The regression this pins: a capped list reported recall 0 and failed HG-A1."""
    run = _run(ids=[f"z{i}" for i in range(20)], violation_count=2584)
    monkeypatch.setattr(replay, "load_archived_runs", lambda *a, **k: [run])

    result = replay.evaluate(TRUTH, write_evidence=False)

    metric = result.metrics["min_recall_per_class"]
    assert metric.raw is None
    assert metric.status == EvalStatus.NOT_MEASURED
    assert result.metrics["evidence_complete"].raw is False
    assert "HG-A1" not in {f.id for f in result.critical_findings}


def test_complete_evidence_still_fails_hg_a1_on_a_real_miss(monkeypatch) -> None:
    """The guard must not disable the gate when the record is trustworthy."""
    run = _run(ids=["nomatch-1", "nomatch-2"], violation_count=2)
    monkeypatch.setattr(replay, "load_archived_runs", lambda *a, **k: [run])

    result = replay.evaluate(TRUTH, write_evidence=False)

    assert result.metrics["evidence_complete"].raw is True
    assert result.metrics["min_recall_per_class"].raw == 0.0
    assert "HG-A1" in {f.id for f in result.critical_findings}
