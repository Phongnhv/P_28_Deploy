"""Evaluator for database and dataset profiler accuracy.

Validates:
1. Exact null-rate calculation on controlled fixtures
2. Exact distinct count and min/max detection
3. Column type classification
4. Date/time parsing and freshness gap computation
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import pandas as pd
from sqlalchemy import create_engine

from evalgate.normalizers import normalizers as norm
from evalgate.schemas.eval_result import (
    EvalResult,
    EvalStatus,
    Evidence,
    MetricValue,
)
from src.agents.tools import db_profiler_tool as prof

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = PROJECT_ROOT / "evalgate" / "evidence" / "gate4"

GATE = "input_data"
EVALUATOR = "profile_accuracy_probe_v1"


def test_profiler_statistics_accuracy() -> dict[str, Any]:
    """Test profiler calculations on a controlled 100-row fixture."""
    tmp_file = NamedTemporaryFile(suffix=".db", delete=False)
    tmp_file.close()
    db_path = Path(tmp_file.name)

    try:
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")

        # 100 rows: 10 nulls in val, min 30.0, max 208.0, 5 distinct categories
        rows = [
            {
                "id": i,
                "val": (float(i * 2 + 10) if i >= 10 else None),
                "cat": f"cat_{i % 5}",
                "ts": "2026-01-01 12:00:00",
            }
            for i in range(100)
        ]
        df = pd.DataFrame(rows)
        df.to_sql("test_table", engine, index=False)
        engine.dispose()

        conn_str = f"sqlite:///{db_path.as_posix()}"
        raw_profile_json = prof.profile_database.invoke(
            {"connection_string": conn_str, "table_name": "test_table"}
        )
        raw = json.loads(raw_profile_json)

        cols = raw.get("columns", {})

        val_col = cols.get("val", {})
        cat_col = cols.get("cat", {})

        null_exact = val_col.get("null_count") == 10 and abs(float(val_col.get("null_pct", 0)) - 0.10) < 0.01
        min_exact = float(val_col.get("min", 0)) == 30.0
        max_exact = float(val_col.get("max", 0)) == 208.0
        cat_distinct_exact = int(cat_col.get("distinct_full_table", 0)) == 5 or int(cat_col.get("distinct_in_sample", 0)) == 5

        return {
            "null_rate_accurate": null_exact,
            "min_max_accurate": min_exact and max_exact,
            "distinct_count_accurate": cat_distinct_exact,
        }
    finally:
        try:
            if db_path.exists():
                db_path.unlink()
        except OSError:
            pass


def test_freshness_parsing() -> dict[str, Any]:
    """Test date parsing and freshness computation."""
    iso_val, gap = prof._parse_and_calculate_freshness("2026-01-01 12:00:00")
    iso_none, gap_none = prof._parse_and_calculate_freshness(None)

    return {
        "datetime_parsed": iso_val is not None and "2026-01-01" in iso_val,
        "gap_seconds_computed": gap is not None and gap > 0,
        "none_handled": iso_none is None and gap_none is None,
    }


def evaluate(*, write_evidence: bool = True) -> EvalResult:
    stats_results = test_profiler_statistics_accuracy()
    fresh_results = test_freshness_parsing()

    all_passed = all(stats_results.values()) and all(fresh_results.values())
    total_checks = len(stats_results) + len(fresh_results)
    passed_checks = sum(1 for v in stats_results.values() if v) + sum(1 for v in fresh_results.values() if v)
    score = (passed_checks / total_checks) * 100.0

    metrics = {
        "null_rate_fidelity": MetricValue(
            raw=stats_results["null_rate_accurate"],
            unit="boolean",
            normalized=norm.boolean(stats_results["null_rate_accurate"]),
        ),
        "min_max_fidelity": MetricValue(
            raw=stats_results["min_max_accurate"],
            unit="boolean",
            normalized=norm.boolean(stats_results["min_max_accurate"]),
        ),
        "distinct_count_fidelity": MetricValue(
            raw=stats_results["distinct_count_accurate"],
            unit="boolean",
            normalized=norm.boolean(stats_results["distinct_count_accurate"]),
        ),
        "profile_accuracy_score": MetricValue(
            raw=score,
            unit="ratio",
            normalized=score,
        ),
    }

    evidence: list[Evidence] = []
    if write_evidence:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        target = EVIDENCE_DIR / "profile_accuracy_probe.json"
        target.write_text(
            json.dumps({"stats": stats_results, "freshness": fresh_results}, ensure_ascii=False, indent=2),
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
            "tested_components": ["profile_database", "_parse_and_calculate_freshness"],
        },
    )
