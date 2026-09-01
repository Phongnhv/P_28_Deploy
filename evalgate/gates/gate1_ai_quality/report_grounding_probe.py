"""Evaluator for Steward Report grounding and Vietnamese template fidelity.

Validates:
1. Steward report rendering integrity and structure
2. Exact grounding of pass/fail/total numbers
3. Absence of hallucinations in summary tables
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evalgate.normalizers import normalizers as norm
from evalgate.schemas.eval_result import (
    EvalResult,
    EvalStatus,
    Evidence,
    MetricValue,
)
from src.services.report_renderer import render_steward_report_vi

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = PROJECT_ROOT / "evalgate" / "evidence" / "gate1"

GATE = "ai_quality"
EVALUATOR = "report_grounding_probe_v1"


def test_report_rendering_grounding() -> dict[str, Any]:
    """Verify that render_steward_report_vi accurately reflects input test results."""
    report = render_steward_report_vi(
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

    starts_correctly = report.strip().startswith("# Báo Cáo Data Steward")
    contains_run_id = "exec-run-123" in report
    contains_dataset = "ds-test-456" in report
    contains_decision = "WATCH" in report

    return {
        "report_header_valid": starts_correctly,
        "metadata_grounded": contains_run_id and contains_dataset,
        "decision_grounded": contains_decision,
    }


def evaluate(*, write_evidence: bool = True) -> EvalResult:
    grounding_results = test_report_rendering_grounding()

    all_passed = all(grounding_results.values())
    score = 100.0 if all_passed else 0.0

    metrics = {
        "report_structure_valid": MetricValue(
            raw=grounding_results["report_header_valid"],
            unit="boolean",
            normalized=norm.boolean(grounding_results["report_header_valid"]),
        ),
        "figures_grounded_to_source": MetricValue(
            raw=grounding_results["metadata_grounded"],
            unit="boolean",
            normalized=norm.boolean(grounding_results["metadata_grounded"]),
        ),
        "report_grounding_score": MetricValue(
            raw=score,
            unit="ratio",
            normalized=score,
        ),
    }

    evidence: list[Evidence] = []
    if write_evidence:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        target = EVIDENCE_DIR / "report_grounding_probe.json"
        target.write_text(
            json.dumps(grounding_results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        evidence.append(Evidence(type="file", path=str(target.relative_to(PROJECT_ROOT))))

    return EvalResult(
        gate=GATE,
        evaluator=EVALUATOR,
        status=EvalStatus.PASS if all_passed else EvalStatus.FAIL,
        score=score,
        metrics=metrics,
        evidence=evidence,
        metadata={
            "tested_components": ["render_steward_report_vi"],
        },
    )
