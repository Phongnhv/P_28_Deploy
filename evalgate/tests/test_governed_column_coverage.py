"""Omitting a rule must not score better than proposing a broken one.

HG-A3 inspects ACCEPTED_VALUES rules for a tautological allow-list. An agent that
proposed no such rule for a governed column left it nothing to inspect, so the
evaluator returned NOT_MEASURED, dropped out of the aggregate, and HG-A3 reported
NOT_EVALUATED. Producing nothing was therefore quieter than producing something
wrong. HG-A8 scores the absence instead.
"""

from __future__ import annotations

import pytest

from evalgate.aggregator import evaluate_hard_gates
from evalgate.gates.gate1_ai_quality import governed_enum_conformance as gec
from evalgate.schemas.eval_result import EvalStatus


@pytest.fixture
def one_governed_column(monkeypatch):
    """A single governed column with a known allow-list and one excluded literal."""
    domain = gec.GovernedDomain(
        column="payment_type",
        allowed=["Cash", "Credit card"],
        excluded=["Invalid Payment (Dispute/Test)"],
        expected_defects=4,
        source="test-policy",
    )
    monkeypatch.setattr(gec, "load_governed_domains", lambda: [domain])
    return domain


def _with_proposals(monkeypatch, rules):
    monkeypatch.setattr(gec, "_load_proposals", lambda context=None: [("artifact.json", rules)])


def _hard_gate(results, gate_id: str):
    return next(g for g in evaluate_hard_gates(results) if g.id == gate_id)


def test_no_rule_for_a_governed_column_is_a_failure_not_a_silence(
    one_governed_column, monkeypatch
) -> None:
    _with_proposals(monkeypatch, [{"rule_type": "NOT_NULL", "column": "payment_type"}])
    result = gec.evaluate(write_evidence=False)

    assert result.status == EvalStatus.FAIL, (
        "an unconstrained governed column must be measured, not reported as unmeasured"
    )
    assert result.metrics["governed_column_coverage"].raw == 0.0
    assert "HG-A8" in {f.id for f in result.critical_findings}
    assert any(f.blocks_release for f in result.critical_findings)


def test_hg_a8_fires_on_the_uncovered_column(one_governed_column, monkeypatch) -> None:
    _with_proposals(monkeypatch, [{"rule_type": "NOT_NULL", "column": "payment_type"}])
    result = gec.evaluate(write_evidence=False)

    gate = _hard_gate([result], "HG-A8")
    assert gate.status == "FAIL"
    assert gate.observed == 0.0


def test_covered_column_does_not_trip_hg_a8(one_governed_column, monkeypatch) -> None:
    _with_proposals(
        monkeypatch,
        [
            {
                "rule_type": "ACCEPTED_VALUES",
                "column": "payment_type",
                "parameters": {"accepted_values": ["Cash", "Credit card"]},
            }
        ],
    )
    result = gec.evaluate(write_evidence=False)

    assert result.metrics["governed_column_coverage"].raw == 1.0
    assert _hard_gate([result], "HG-A8").status == "PASS"
    assert "HG-A8" not in {f.id for f in result.critical_findings}


def test_a_tautological_rule_is_still_caught_by_hg_a3(
    one_governed_column, monkeypatch
) -> None:
    """The new gate must not displace the one it protects."""
    _with_proposals(
        monkeypatch,
        [
            {
                "rule_type": "ACCEPTED_VALUES",
                "column": "payment_type",
                "parameters": {
                    "accepted_values": [
                        "Cash",
                        "Credit card",
                        "Invalid Payment (Dispute/Test)",
                    ]
                },
            }
        ],
    )
    result = gec.evaluate(write_evidence=False)

    assert result.metrics["governed_column_coverage"].raw == 1.0
    assert _hard_gate([result], "HG-A8").status == "PASS"
    assert _hard_gate([result], "HG-A3").status == "FAIL"


def test_omission_is_not_cheaper_than_a_broken_rule(
    one_governed_column, monkeypatch
) -> None:
    """The property the gate exists for, stated directly as a comparison."""
    _with_proposals(monkeypatch, [{"rule_type": "NOT_NULL", "column": "payment_type"}])
    omitted = gec.evaluate(write_evidence=False)

    _with_proposals(
        monkeypatch,
        [
            {
                "rule_type": "ACCEPTED_VALUES",
                "column": "payment_type",
                "parameters": {
                    "accepted_values": ["Cash", "Credit card", "Invalid Payment (Dispute/Test)"]
                },
            }
        ],
    )
    tautological = gec.evaluate(write_evidence=False)

    omitted_blocks = {g.id for g in evaluate_hard_gates([omitted]) if g.status == "FAIL"}
    tautological_blocks = {
        g.id for g in evaluate_hard_gates([tautological]) if g.status == "FAIL"
    }

    assert omitted_blocks, "omitting the rule must block the release"
    assert tautological_blocks, "a tautological rule must block the release"
