"""Contract for the live upload adversarial probe.

The probe consumes a signed-off result produced against an explicitly supplied
target. It never starts or attacks an arbitrary service from the offline CI job.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from evalgate.core.context import EvalRunContext
from evalgate.normalizers import normalizers as norm
from evalgate.schemas.eval_result import EvalResult, EvalStatus, Finding, MetricValue, Severity


def evaluate(*, write_evidence: bool = True, context: EvalRunContext | None = None) -> EvalResult:
    result_path = os.getenv("EVALGATE_UPLOAD_PROBE_RESULT", "")
    context_record = context.records("upload-probe")[0] if context and context.records("upload-probe") else None
    if not result_path and context_record is None:
        return EvalResult(
            gate="ai_security",
            evaluator="upload_probe_v1",
            status=EvalStatus.NOT_EXECUTED,
            metadata={"reason": "no executed upload-probe result was supplied"},
        )
    try:
        payload = json.loads((context.path_for(context_record) if context_record else Path(result_path)).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return EvalResult(
            gate="ai_security", evaluator="upload_probe_v1",
            status=EvalStatus.NOT_EXECUTED, metadata={"reason": str(exc)},
        )
    accepted = int(payload.get("malicious_upload_accepted_count", 0))
    findings = [] if accepted == 0 else [Finding(
        id="HG-S4", severity=Severity.CRITICAL,
        title="Malicious upload cases were accepted",
        detail=f"{accepted} adversarial upload cases crossed the handler",
        blocks_release=True,
    )]
    return EvalResult(
        gate="ai_security", evaluator="upload_probe_v1",
        status=EvalStatus.PASS if accepted == 0 else EvalStatus.FAIL,
        score=norm.zero_tolerance(accepted),
        metrics={"malicious_upload_accepted_count": MetricValue(raw=accepted, unit="count", normalized=norm.zero_tolerance(accepted))},
        critical_findings=findings,
        metadata={"source": context_record.relative_path if context_record else result_path,
                  "executed_cases": payload.get("executed_cases", [])},
    )
