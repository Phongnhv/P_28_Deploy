"""Gate 5A: reliability controls that can be checked without generating load.

Most items here are a yes/no fact about the configuration, readable today.  It is
the half of reliability that does not need k6, a running cluster, or approval to
put load on anything.

**A control is not credited for existing.**  On 2026-08-22 commit e3bd462 added a
25-second LLM timeout; this evaluator flipped ``llm_timeout_configured`` to True and
handed the gate 14 points, while every proposal call began timing out and the
product's core AI feature stopped working entirely.  The check had asked *"is a
timeout configured?"* and never *"is this timeout right for the call it guards?"*.

A wrongly-valued control is worse than no control: it looks like protection, scores
like protection, and causes the failure.  So where evidence exists that a control is
misbehaving, the control does not count.  Where no evidence exists either way, the
honest report is that adequacy is unverified -- not that the control is fine.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from evalgate.normalizers import normalizers as norm
from evalgate.schemas.eval_result import (
    EvalResult,
    EvalStatus,
    Evidence,
    MetricValue,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = PROJECT_ROOT / "evalgate" / "evidence" / "gate5"
SRC = PROJECT_ROOT / "src"

GATE = "reliability"
EVALUATOR = "config_static_check_v1"


def _grep(pattern: str, *paths: Path) -> list[str]:
    hits: list[str] = []
    compiled = re.compile(pattern, re.IGNORECASE)
    for base in paths:
        if not base.exists():
            continue
        files = base.rglob("*.py") if base.is_dir() else [base]
        for file in files:
            if "__pycache__" in str(file):
                continue
            try:
                text = file.read_text(encoding="utf-8")
            except OSError:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if compiled.search(line):
                    hits.append(f"{file.relative_to(PROJECT_ROOT)}:{number}")
    return hits


def observed_timeout_failures() -> tuple[int, int]:
    """(runs that failed on a timeout, runs observed at all).

    A configured timeout that demonstrably caused a failure is not a control -- it is
    the cause. Reading the run history is the only way to tell the two apart, because
    the value alone says nothing: 25 seconds is generous for one call and fatal for
    another, and only the workload knows which.

    Returns (0, 0) when nothing has been observed, which the caller must report as
    NOT_MEASURED rather than as a healthy control.
    """
    try:
        from evalgate.gates.gate1_ai_quality.run_outcome_integrity import collect_runs
    except ImportError:  # pragma: no cover
        return 0, 0
    runs = [r for r in collect_runs() if r.workflow]
    if not runs:
        return 0, 0
    recent = runs[:5]
    return sum(1 for r in recent if r.failure_kind == "TIMEOUT"), len(recent)


def collect_controls() -> dict[str, dict[str, object]]:
    """Each control: whether it is configured, and where the evidence is.

    A control counts only when it is both **present** and **not demonstrably
    wrong**. The distinction was learned the hard way: commit e3bd462 added a 25s
    LLM timeout, this evaluator flipped `llm_timeout_configured` to True and awarded
    the gate 14 points, and every proposal call began timing out. Presence was
    rewarded; correctness was never asked about.
    """
    llm_timeout = _grep(r"\btimeout\s*=", SRC / "services" / "llm.py")
    db_timeout = _grep(r"statement_timeout", SRC)
    # A size cap is rarely a named setting. The product enforces one inline as
    # `if len(payload) > 100 * 1024 * 1024: raise HTTPException(413, ...)`, which the
    # earlier name-only pattern missed entirely -- reporting a control as absent while
    # it was being enforced two lines from an upload handler. HTTP 413 is the
    # unambiguous signal, so it is matched alongside the conventional setting names.
    upload_limit = _grep(
        r"max_upload|MAX_CONTENT_LENGTH|upload_size|max_file_size"
        r"|status_code\s*=\s*413|HTTP_413",
        SRC,
    )
    tenant_quota = _grep(r"quota|rate_limit|per_tenant", SRC)
    out_of_process_queue = _grep(r"celery|rq\.Queue|dramatiq", SRC)
    background_tasks = _grep(r"BackgroundTasks", SRC)
    retry_policy = _grep(r"tenacity|@retry|max_retries", SRC)
    circuit_breaker = _grep(r"circuit_breaker|CircuitBreaker", SRC)

    timed_out, observed = observed_timeout_failures()
    # Present but proven harmful counts as not configured: the score must not reward
    # a control that is currently breaking the workload it guards.
    timeout_ok = bool(llm_timeout) and timed_out == 0
    if not llm_timeout:
        timeout_note = "no timeout configured"
    elif observed == 0:
        timeout_note = "configured, but no run observed yet -- adequacy unverified"
    elif timed_out:
        timeout_note = (
            f"configured, but {timed_out} of the last {observed} runs failed ON this "
            f"timeout. A control that causes the failure is not a control"
        )
    else:
        timeout_note = f"configured and {observed} observed run(s) completed within it"

    return {
        "llm_timeout_configured": {
            "value": timeout_ok,
            "evidence": llm_timeout[:5],
            "note": timeout_note,
        },
        "db_statement_timeout_configured": {"value": bool(db_timeout), "evidence": db_timeout[:5]},
        "upload_size_limit_configured": {"value": bool(upload_limit), "evidence": upload_limit[:5]},
        "per_tenant_quota_configured": {"value": bool(tenant_quota), "evidence": tenant_quota[:5]},
        "job_queue_out_of_process": {
            "value": bool(out_of_process_queue),
            "evidence": out_of_process_queue[:5],
            "note": f"in-process BackgroundTasks used at {len(background_tasks)} sites"
            if background_tasks and not out_of_process_queue
            else "",
        },
        "retry_policy_configured": {"value": bool(retry_policy), "evidence": retry_policy[:5]},
        "circuit_breaker_configured": {"value": bool(circuit_breaker), "evidence": circuit_breaker[:5]},
    }


def evaluate(*, write_evidence: bool = True) -> EvalResult:
    controls = collect_controls()
    metrics = {
        name: MetricValue(
            raw=bool(control["value"]),
            unit="boolean",
            normalized=norm.boolean(bool(control["value"])),
            note=str(control.get("note") or "") or None,
        )
        for name, control in controls.items()
    }
    configured = sum(1 for c in controls.values() if c["value"])
    score = configured / len(controls) * 100.0

    evidence: list[Evidence] = []
    if write_evidence:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        target = EVIDENCE_DIR / "config_static_check.json"
        target.write_text(
            json.dumps(controls, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        evidence.append(Evidence(type="file", path=str(target.relative_to(PROJECT_ROOT))))

    return EvalResult(
        gate=GATE,
        evaluator=EVALUATOR,
        status=EvalStatus.FAIL if score < 50 else EvalStatus.WARN,
        score=score,
        metrics=metrics,
        evidence=evidence,
        metadata={"configured": configured, "total_controls": len(controls)},
    )
