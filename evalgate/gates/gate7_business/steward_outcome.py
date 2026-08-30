"""Compute steward outcomes from an exported, aggregate-only event document."""

from __future__ import annotations

import json
import os
from pathlib import Path

from evalgate.normalizers import normalizers as norm
from evalgate.schemas.eval_result import EvalResult, EvalStatus, MetricValue


def evaluate(*, write_evidence: bool = True) -> EvalResult:
    source = os.getenv("EVALGATE_STEWARD_EVENTS", "")
    if not source:
        return EvalResult(gate="business", evaluator="steward_behavior_v1", status=EvalStatus.NOT_MEASURED, metadata={"reason": "aggregate steward event export is not configured"})
    try:
        payload = json.loads(Path(source).read_text(encoding="utf-8"))
        datasets = int(payload["dataset_count"])
        total = int(payload["proposal_count"])
        accepted = int(payload["accepted_count"])
        edited = int(payload.get("edited_count", 0))
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        return EvalResult(gate="business", evaluator="steward_behavior_v1", status=EvalStatus.NOT_EXECUTED, metadata={"reason": f"invalid business export: {exc}"})
    if datasets < 3 or total < 20:
        return EvalResult(gate="business", evaluator="steward_behavior_v1", status=EvalStatus.NOT_MEASURED, metadata={"reason": "requires at least 3 datasets and 20 proposals", "dataset_count": datasets, "proposal_count": total})
    acceptance = accepted / total
    edit_rate = edited / total
    return EvalResult(
        gate="business", evaluator="steward_behavior_v1", status=EvalStatus.PASS,
        score=norm.ratio(acceptance),
        metrics={
            "steward_acceptance_rate": MetricValue(raw=acceptance, unit="ratio", normalized=norm.ratio(acceptance)),
            "steward_edit_rate": MetricValue(raw=edit_rate, unit="ratio", normalized=None),
        }, metadata={"source": source, "advisory": True},
    )
