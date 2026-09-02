"""The Vietnamese steward-report template test, moved out of the evaluator.

This used to be ``report_grounding_probe_v1``. It rendered a template with hand-built
arguments and asserted the output contained the strings it had just passed in, which is a
template test wearing an evaluator's clothes: it returned 100.0 on every run and never
looked at the report the product actually produced. The evaluator now resolves the
bundle's narrative figures against its execution results.
"""

from __future__ import annotations

import pytest

renderer = pytest.importorskip("src.services.report_renderer")


@pytest.fixture(scope="module")
def rendered() -> str:
    return renderer.render_steward_report_vi(
        execution_run_id="exec-run-123",
        dataset_id="ds-test-456",
        anomaly_state={
            "anomaly_decision": {
                "decision": "WATCH",
                "score": 0.45,
                "confidence": 0.85,
                "severity": "TRUNG BÌNH",
            },
            "anomaly_run_id": "anom-run-456",
            "signal_observations": [
                {
                    "signal_type": "Z_SCORE_DEVIATION",
                    "score": 0.8,
                    "summary": "Z-score deviation observed on revenue column",
                    "rule_id": "r_revenue",
                    "severity": "HIGH",
                }
            ],
        },
    )


def test_report_starts_with_the_steward_header(rendered: str) -> None:
    assert rendered.strip().startswith("# Báo Cáo Data Steward")


@pytest.mark.parametrize("token", ["exec-run-123", "ds-test-456"])
def test_identifiers_reach_the_rendered_report(rendered: str, token: str) -> None:
    assert token in rendered


def test_the_decision_is_carried_through(rendered: str) -> None:
    assert "WATCH" in rendered
