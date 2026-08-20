"""Gate 5A: reliability controls that can be checked without generating load.

Every item here is a yes/no fact about the configuration, readable today.  It is
the half of reliability that does not need k6, a running cluster, or approval to
put load on anything.
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


def collect_controls() -> dict[str, dict[str, object]]:
    """Each control: whether it is configured, and where the evidence is."""
    llm_timeout = _grep(r"\btimeout\s*=", SRC / "services" / "llm.py")
    db_timeout = _grep(r"statement_timeout", SRC)
    upload_limit = _grep(r"max_upload|MAX_CONTENT_LENGTH|upload_size", SRC)
    tenant_quota = _grep(r"quota|rate_limit|per_tenant", SRC)
    out_of_process_queue = _grep(r"celery|rq\.Queue|dramatiq", SRC)
    background_tasks = _grep(r"BackgroundTasks", SRC)
    retry_policy = _grep(r"tenacity|@retry|max_retries", SRC)
    circuit_breaker = _grep(r"circuit_breaker|CircuitBreaker", SRC)

    return {
        "llm_timeout_configured": {"value": bool(llm_timeout), "evidence": llm_timeout[:5]},
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
