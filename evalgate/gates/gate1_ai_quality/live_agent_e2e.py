"""Adapters for paid/live agent results produced outside the offline merge gate."""

from __future__ import annotations

import json
import os
from pathlib import Path

from evalgate.core.context import EvalRunContext
from evalgate.normalizers import normalizers as norm
from evalgate.schemas.eval_result import EvalResult, EvalStatus, MetricValue


def _load(context: EvalRunContext | None = None, artifact_type: str = "live-agent") -> tuple[dict | None, str]:
    if context and context.records(artifact_type):
        return context.read_json(artifact_type), ""
    value = os.getenv("EVALGATE_LIVE_AGENT_RESULT", "")
    if not value:
        return None, "EVALGATE_LIVE_AGENT_RESULT is not configured"
    path = Path(value)
    try:
        return json.loads(path.read_text(encoding="utf-8")), ""
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"cannot read live-agent result: {exc}"


def evaluate(*, write_evidence: bool = True, context: EvalRunContext | None = None) -> EvalResult:
    payload, reason = _load(context)
    if payload is None:
        return EvalResult(
            gate="ai_quality",
            evaluator="live_sdih_detection_v1",
            status=EvalStatus.NOT_EXECUTED,
            metadata={"reason": reason, "paid": True},
        )
    recall = float(payload.get("detection_recall_macro", 0.0))
    f1 = float(payload.get("detection_f1_macro", 0.0))
    status = EvalStatus.PASS if f1 >= 0.60 else EvalStatus.WARN if f1 >= 0.40 else EvalStatus.FAIL
    return EvalResult(
        gate="ai_quality",
        evaluator="live_sdih_detection_v1",
        status=status,
        score=norm.ratio(f1),
        metrics={
            "live_detection_f1_macro": MetricValue(raw=f1, unit="ratio", normalized=norm.ratio(f1)),
            "live_detection_recall_macro": MetricValue(raw=recall, unit="ratio", normalized=norm.ratio(recall)),
        },
        metadata={"source": os.getenv("EVALGATE_LIVE_AGENT_RESULT"), "paid": True},
    )


def evaluate_geval(*, write_evidence: bool = True, context: EvalRunContext | None = None) -> EvalResult:
    payload, reason = _load(context, "geval-result")
    if payload is None or "geval_domain_score" not in payload:
        return EvalResult(
            gate="ai_quality",
            evaluator="geval_domain_v1",
            status=EvalStatus.NOT_EXECUTED,
            metadata={"reason": reason or "live result has no geval_domain_score", "paid": True},
        )
    score = float(payload["geval_domain_score"])
    status = EvalStatus.PASS if score >= 0.80 else EvalStatus.WARN if score >= 0.60 else EvalStatus.FAIL
    return EvalResult(
        gate="ai_quality",
        evaluator="geval_domain_v1",
        status=status,
        score=norm.ratio(score),
        metrics={"geval_domain_score": MetricValue(raw=score, unit="ratio", normalized=norm.ratio(score))},
        metadata={"source": os.getenv("EVALGATE_LIVE_AGENT_RESULT"), "paid": True},
    )
