"""Adapter for a k6 JSON summary produced against an approved target."""

from __future__ import annotations

import json
import os
from pathlib import Path

from evalgate.core.context import EvalRunContext
from evalgate.normalizers import normalizers as norm
from evalgate.schemas.eval_result import EvalResult, EvalStatus, MetricValue


def evaluate(*, write_evidence: bool = True, context: EvalRunContext | None = None) -> EvalResult:
    result_path = os.getenv("EVALGATE_K6_RESULT", "")
    record = context.records("k6-result")[0] if context and context.records("k6-result") else None
    if not result_path and record is None:
        return EvalResult(gate="reliability", evaluator="k6_load_v1", status=EvalStatus.NOT_EXECUTED, metadata={"reason": "k6 summary not supplied; load requires an approved target"})
    try:
        payload = json.loads((context.path_for(record) if record else Path(result_path)).read_text(encoding="utf-8"))
        metrics = payload["metrics"]
        p95 = float(metrics["http_req_duration"]["values"]["p(95)"])
        failed = float(metrics["http_req_failed"]["values"]["rate"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        return EvalResult(gate="reliability", evaluator="k6_load_v1", status=EvalStatus.NOT_EXECUTED, metadata={"reason": f"invalid k6 summary: {exc}"})
    ok = p95 <= 3000 and failed <= 0.01
    return EvalResult(
        gate="reliability", evaluator="k6_load_v1", status=EvalStatus.PASS if ok else EvalStatus.FAIL,
        score=min(norm.latency_band(p95) or 0.0, norm.inverse_ratio(failed) or 0.0),
        metrics={
            "load_p95_ms": MetricValue(raw=p95, unit="ms", normalized=norm.latency_band(p95)),
            "load_failure_rate": MetricValue(raw=failed, unit="ratio", normalized=norm.inverse_ratio(failed)),
        }, metadata={"source": result_path},
    )
