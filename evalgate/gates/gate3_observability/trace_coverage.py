"""Measure trace completeness from redacted node-event JSONL."""

from __future__ import annotations

import json
import os
from pathlib import Path

from evalgate.core.context import EvalRunContext
from evalgate.normalizers import normalizers as norm
from evalgate.schemas.eval_result import CostRecord, EvalResult, EvalStatus, MetricValue

REQUIRED_FIELDS = {"trace_id", "workflow_run_id", "dataset_id", "event", "timestamp"}


def evaluate(*, write_evidence: bool = True, context: EvalRunContext | None = None) -> EvalResult:
    trace_path = os.getenv("EVALGATE_TRACE_FILE", "")
    record = context.records("traces")[0] if context and context.records("traces") else None
    if not trace_path and record is None:
        return EvalResult(gate="observability", evaluator="trace_coverage_v1", status=EvalStatus.NOT_MEASURED, metadata={"reason": "EVALGATE_TRACE_FILE is not configured"})
    total = complete = errors = 0
    latencies: list[float] = []
    estimated_cost = 0.0
    total_tokens = 0
    try:
        source = context.path_for(record) if record else Path(trace_path)
        for line in source.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            total += 1
            event = json.loads(line)
            required = REQUIRED_FIELDS | ({"node"} if event.get("event") == "node" else set())
            complete += int(required <= set(event))
            errors += int(event.get("event") == "error")
            if event.get("latency_ms") is not None:
                latencies.append(float(event["latency_ms"]))
            estimated_cost += float(event.get("estimated_cost_usd") or 0.0)
            total_tokens += int(event.get("total_tokens") or 0)
    except (OSError, json.JSONDecodeError) as exc:
        return EvalResult(gate="observability", evaluator="trace_coverage_v1", status=EvalStatus.NOT_EXECUTED, metadata={"reason": f"invalid trace: {exc}"})
    coverage = complete / total if total else 0.0
    p95 = norm.percentile(latencies, 0.95)
    return EvalResult(
        gate="observability", evaluator="trace_coverage_v1",
        status=EvalStatus.PASS if coverage >= 0.95 and errors == 0 else EvalStatus.FAIL,
        score=norm.ratio(coverage),
        metrics={
            "trace_coverage": MetricValue(raw=coverage, unit="ratio", normalized=norm.ratio(coverage)),
            "critical_node_errors": MetricValue(raw=errors, unit="count", normalized=norm.zero_tolerance(errors)),
            "trace_p95_latency_ms": MetricValue(raw=p95, unit="ms", normalized=norm.latency_band(p95)),
            "llm_cost_usd": MetricValue(raw=estimated_cost, unit="usd", normalized=None),
        },
        cost=CostRecord(llm_usd=estimated_cost, llm_tokens=total_tokens),
        metadata={"source": record.relative_path if record else trace_path, "events": total},
    )
