"""Multi-Dataset Readiness Score: how far the code is from "any dataset".

Gate 1B cannot report a generalisation number yet, because six of the seven corpus
datasets cannot even be ingested.  That absence is a fact about the product, and
leaving it as a blank in the report would understate it.  This score measures the
same gap by static analysis, so the roadmap gets a number today.

Every dimension is a count or a boolean read straight from the repository -- no
model, no network, no judgement call.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from evalgate.normalizers import normalizers as norm
from evalgate.schemas.eval_result import (
    EvalResult,
    EvalStatus,
    Evidence,
    MetricValue,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = PROJECT_ROOT / "evalgate" / "evidence" / "readiness"

GATE = "input_data"
EVALUATOR = "multi_dataset_readiness_v1"

SCAN_ROOTS = ("src", "frontend/src", "dbt_project", "scripts")
NYC_TOKENS = re.compile(
    r"yellow_tripdata|vendor_id|trip_distance|nyc.?yellow|pickup_at|mta_tax",
    re.IGNORECASE,
)
UPLOAD_TOKENS = re.compile(r"UploadFile|multipart|File\(")


def _scan_hardcoded() -> dict[str, Any]:
    hits: list[str] = []
    for root in SCAN_ROOTS:
        base = PROJECT_ROOT / root
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or "__pycache__" in str(path) or "node_modules" in str(path):
                continue
            if path.suffix not in {".py", ".ts", ".tsx", ".sql", ".yml", ".yaml"}:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if NYC_TOKENS.search(text):
                hits.append(str(path.relative_to(PROJECT_ROOT)))
    return {"count": len(hits), "files": sorted(hits)}


def _has_upload_surface() -> bool:
    api = PROJECT_ROOT / "src" / "api"
    if not api.exists():
        return False
    return any(
        UPLOAD_TOKENS.search(path.read_text(encoding="utf-8", errors="ignore"))
        for path in api.rglob("*.py")
        if "__pycache__" not in str(path)
    )


def _has_generic_row_storage() -> bool:
    """A schema-agnostic landing table needs a JSON/JSONB payload column."""
    migrations = PROJECT_ROOT / "scripts" / "migrations"
    if not migrations.exists():
        return False
    for path in sorted(migrations.glob("*.sql")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"(row_data|values|payload|attributes)\s+JSONB", text, re.IGNORECASE):
            return True
    return False


def _dataset_owner_present() -> bool:
    model = PROJECT_ROOT / "src" / "models" / "database.py"
    if not model.exists():
        return False
    text = model.read_text(encoding="utf-8", errors="ignore")
    block = text.split("class DatasetModel")[-1].split("class ")[0]
    return any(token in block for token in ("owner", "tenant_id", "schema_json"))


def _evidence_column_cap() -> int | None:
    workflow = PROJECT_ROOT / "src" / "services" / "dashboard_agent_workflow.py"
    if not workflow.exists():
        return None
    match = re.search(r"columns:\s*list\[[^\]]+\]\s*=\s*Field\([^)]*max_length=(\d+)", workflow.read_text(encoding="utf-8"))
    return int(match.group(1)) if match else None


def _domain_in_system_prompt() -> bool:
    templates = PROJECT_ROOT / "src" / "agents" / "nodes" / "templates.py"
    if not templates.exists():
        return False
    return bool(
        re.search(r"taxi|NYC Yellow", templates.read_text(encoding="utf-8"), re.IGNORECASE)
    )


def _delete_dataset_endpoint() -> bool:
    routes = PROJECT_ROOT / "src" / "api" / "routes.py"
    if not routes.exists():
        return False
    return bool(re.search(r'@\w+\.delete\(\s*"/datasets/', routes.read_text(encoding="utf-8")))


def evaluate(*, write_evidence: bool = True) -> EvalResult:
    hardcoded = _scan_hardcoded()
    cap = _evidence_column_cap()

    dimensions: dict[str, dict[str, Any]] = {
        "upload_surface_exists": {"value": _has_upload_surface(), "weight": 0.25},
        "schema_agnostic_row_storage": {"value": _has_generic_row_storage(), "weight": 0.20},
        "dataset_has_owner_or_schema": {"value": _dataset_owner_present(), "weight": 0.15},
        "domain_not_hardcoded_in_prompt": {"value": not _domain_in_system_prompt(), "weight": 0.15},
        "dataset_deletion_endpoint": {"value": _delete_dataset_endpoint(), "weight": 0.10},
        "evidence_column_cap_sufficient": {
            "value": bool(cap and cap >= 200),
            "weight": 0.10,
            "detail": f"ProposalEvidence caps columns at {cap}" if cap else "cap not found",
        },
        "low_single_domain_coupling": {
            "value": hardcoded["count"] <= 10,
            "weight": 0.05,
            "detail": f"{hardcoded['count']} files reference the NYC schema",
        },
    }

    score = sum(d["weight"] for d in dimensions.values() if d["value"]) * 100.0

    metrics = {
        name: MetricValue(
            raw=bool(d["value"]),
            unit="boolean",
            normalized=norm.boolean(bool(d["value"])),
            note=str(d.get("detail") or "") or None,
        )
        for name, d in dimensions.items()
    }
    metrics["multi_dataset_readiness_score"] = MetricValue(
        raw=score, unit="ratio", normalized=score
    )
    metrics["single_domain_coupled_files"] = MetricValue(
        raw=hardcoded["count"], unit="count", normalized=None
    )

    evidence: list[Evidence] = []
    if write_evidence:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        target = EVIDENCE_DIR / "multi_dataset_readiness.json"
        target.write_text(
            json.dumps(
                {
                    "score": score,
                    "dimensions": {k: {kk: vv for kk, vv in v.items()} for k, v in dimensions.items()},
                    "hardcoded_files": hardcoded,
                    "evidence_column_cap": cap,
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        evidence.append(Evidence(type="file", path=str(target.relative_to(PROJECT_ROOT))))

    return EvalResult(
        gate=GATE,
        evaluator=EVALUATOR,
        status=EvalStatus.FAIL if score < 50 else EvalStatus.WARN,
        score=score,
        metrics=metrics,
        evidence=evidence,
        metadata={
            "interpretation": (
                "0 = single-dataset fixed-schema product; "
                "100 = ready to accept an arbitrary uploaded dataset"
            ),
            "hardcoded_file_count": hardcoded["count"],
        },
    )
