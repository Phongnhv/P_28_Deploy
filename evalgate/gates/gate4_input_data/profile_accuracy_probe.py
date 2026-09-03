"""Gate 4B: did the profiler tell the truth about the frame the product ingested?

Every number the agent reasons with comes from this profile. A rule's threshold cites
``profile.column.fare_amount.min_value``; a steward approves it on the strength of that
citation. If the profile is wrong, every downstream artefact is wrong in a way no other
evaluator can see -- the rule is well-formed, the citation resolves, and the figure it
resolves to is fiction.

The check is a recomputation. ``dataset-profile`` is what the product published;
``input-dataset`` is the exact frame it published that about, pinned by the same
checksum the manifest carries. Both come from the bundle, so this measures **this run**
rather than a fixture.

That is the whole point of the rewrite. The previous version built a 100-row SQLite
table, profiled it, and asserted three booleans. It never touched the user's data, so it
returned 100.0 for every run regardless of what the product did with the dataset it was
actually given -- and those 100 points went into ``input_data``, which is a claim about
ingestion fidelity. A probe that cannot fail on the artefact it grades is not evidence.
The synthetic fixtures still exist, as unit tests, in ``tests/test_profiler_math.py``.

Sample statistics are held to a weaker rule than full-table ones, deliberately.
``full_distinct_count`` is a claim about the whole column and is checked exactly;
``distinct_count`` may be computed over a sample, so it is only required to be a
non-negative value that does not exceed the full count. Demanding exactness there would
report a correct profiler as broken the day sampling is switched on.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from evalgate.core.context import EvalRunContext
from evalgate.normalizers import normalizers as norm
from evalgate.schemas.eval_result import (
    EvalResult,
    EvalStatus,
    Evidence,
    Finding,
    MetricValue,
    Severity,
    Threshold,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = PROJECT_ROOT / "evalgate" / "evidence" / "gate4"

GATE = "input_data"
EVALUATOR = "profile_accuracy_probe_v1"

#: Floating point comparison tolerance. Rates are ratios in [0, 1]; a profiler rounding
#: to six places is correct, one that is off by a percent is not.
_REL_TOL = 1e-6
_ABS_TOL = 1e-9


@dataclass
class Check:
    column: str | None
    field: str
    published: Any
    recomputed: Any
    passed: bool


def _payload(document: Any) -> dict[str, Any]:
    """Unwrap the governed-artifact envelope when there is one."""
    if isinstance(document, dict) and isinstance(document.get("payload"), dict):
        return document["payload"]
    return document if isinstance(document, dict) else {}


def _close(published: Any, recomputed: Any) -> bool:
    try:
        left, right = float(published), float(recomputed)
    except (TypeError, ValueError):
        return str(published) == str(recomputed)
    if math.isnan(left) or math.isnan(right):
        return math.isnan(left) and math.isnan(right)
    return math.isclose(left, right, rel_tol=_REL_TOL, abs_tol=_ABS_TOL)


def _recompute_column(series) -> dict[str, Any]:
    """The statistics the product's profile publishes, computed from the frame."""
    import pandas as pd

    present = series.dropna()
    stats: dict[str, Any] = {
        "null_rate": float(series.isna().mean()),
        "non_null_count": int(present.shape[0]),
        "full_distinct_count": int(present.nunique()),
        "uniqueness_rate": (
            float(present.nunique() / present.shape[0]) if present.shape[0] else 0.0
        ),
        "is_unique_full_table": bool(
            present.shape[0] > 0 and present.nunique() == present.shape[0]
        ),
    }
    if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
        numeric = pd.to_numeric(present, errors="coerce").dropna()
        if not numeric.empty:
            stats["min_value"] = float(numeric.min())
            stats["max_value"] = float(numeric.max())
            stats["negative_rate"] = float((numeric < 0).mean())
    return stats


def compare(profile: dict[str, Any], frame) -> tuple[list[Check], dict[str, Any]]:
    """Every published figure checked against the frame it describes."""
    checks: list[Check] = []

    published_rows = profile.get("row_count")
    if published_rows is not None:
        checks.append(
            Check(None, "row_count", published_rows, len(frame),
                  _close(published_rows, len(frame)))
        )

    columns = [c for c in profile.get("columns", []) if isinstance(c, dict) and c.get("name")]
    published_names = {str(c["name"]) for c in columns}
    frame_names = {str(c) for c in frame.columns}
    missing_from_profile = sorted(frame_names - published_names)
    absent_from_frame = sorted(published_names - frame_names)

    for column in columns:
        name = str(column["name"])
        if name not in frame_names:
            checks.append(Check(name, "column_exists", "published", "absent", False))
            continue
        recomputed = _recompute_column(frame[name])
        for field, actual in recomputed.items():
            if field not in column or column[field] is None:
                continue
            checks.append(
                Check(name, field, column[field], actual, _close(column[field], actual))
            )
        # Sample statistic: bounded rather than exact. See the module docstring.
        sampled = column.get("distinct_count")
        if sampled is not None:
            full = recomputed["full_distinct_count"]
            checks.append(
                Check(name, "distinct_count<=full", sampled, full,
                      0 <= int(sampled) <= full)
            )

    context = {
        "columns_published": len(columns),
        "columns_in_frame": len(frame_names),
        "columns_missing_from_profile": missing_from_profile,
        "columns_absent_from_frame": absent_from_frame,
    }
    return checks, context


def evaluate(
    *, write_evidence: bool = True, context: EvalRunContext | None = None
) -> EvalResult:
    if context is None or not context.records("dataset-profile") or not context.records("input-dataset"):
        # Never a pass. A profile that was not compared is not a profile that was right.
        return EvalResult(
            gate=GATE,
            evaluator=EVALUATOR,
            status=EvalStatus.NOT_MEASURED,
            metadata={
                "reason": (
                    "profile accuracy is measured against the bundle's own dataset-profile "
                    "and input-dataset; neither is available without a manifest"
                )
            },
        )

    try:
        import pandas as pd
    except ImportError:  # pragma: no cover - pandas is a hard dependency
        return EvalResult(
            gate=GATE, evaluator=EVALUATOR, status=EvalStatus.NOT_EXECUTED,
            metadata={"reason": "pandas is unavailable"},
        )

    profile = _payload(
        json.loads(context.path_for(context.records("dataset-profile")[0]).read_text(encoding="utf-8"))
    )
    dataset_path = context.path_for(context.records("input-dataset")[0])
    frame = (
        pd.read_parquet(dataset_path)
        if dataset_path.suffix.lower() == ".parquet"
        else pd.read_csv(dataset_path)
    )

    if not profile.get("columns"):
        return EvalResult(
            gate=GATE, evaluator=EVALUATOR, status=EvalStatus.NOT_MEASURED,
            metadata={"reason": "the published profile carries no column statistics"},
        )

    checks, detail = compare(profile, frame)
    failed = [c for c in checks if not c.passed]
    fidelity = (len(checks) - len(failed)) / len(checks) if checks else 0.0
    coverage = (
        detail["columns_published"] / detail["columns_in_frame"]
        if detail["columns_in_frame"] else 0.0
    )
    row_check = next((c for c in checks if c.field == "row_count"), None)

    evidence: list[Evidence] = []
    if write_evidence:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        target = EVIDENCE_DIR / "profile_accuracy_probe.json"
        target.write_text(
            json.dumps(
                {
                    "dataset_id": profile.get("dataset_id") or context.dataset_id,
                    "input_dataset": str(dataset_path.name),
                    "statistics_checked": len(checks),
                    "mismatches": [asdict(c) for c in failed],
                    **detail,
                },
                ensure_ascii=False, indent=2, default=str,
            ),
            encoding="utf-8",
        )
        evidence.append(Evidence(type="file", path=str(target.relative_to(PROJECT_ROOT))))

    findings: list[Finding] = []
    if failed:
        sample = "; ".join(
            f"{c.column or 'dataset'}.{c.field}: published {c.published!r}, actual {c.recomputed!r}"
            for c in failed[:5]
        )
        findings.append(
            Finding(
                id="PROFILE-FIDELITY",
                severity=Severity.CRITICAL if row_check and not row_check.passed else Severity.HIGH,
                title=f"{len(failed)}/{len(checks)} published profile statistics do not match the data",
                detail=(
                    "Every rule threshold and every evidence citation is derived from these "
                    f"figures. Mismatches: {sample}"
                ),
                root_cause_hint=(
                    "the profiler and the ingested frame disagree; compare the profiling "
                    "query against the rows actually written by the import job"
                ),
                evidence_ref="evalgate/evidence/gate4/profile_accuracy_probe.json",
                blocks_release=False,
            )
        )

    return EvalResult(
        gate=GATE,
        evaluator=EVALUATOR,
        status=EvalStatus.FAIL if failed else EvalStatus.PASS,
        score=norm.ratio(fidelity),
        metrics={
            "profile_statistic_fidelity": MetricValue(
                raw=round(fidelity, 6), unit="ratio", normalized=norm.ratio(fidelity),
                note=f"{len(checks) - len(failed)}/{len(checks)} published figures match the frame",
            ),
            # The denominator, published on purpose: a fidelity of 1.0 over zero checks
            # is not a healthy profiler, it is an evaluator that inspected nothing.
            "profile_statistics_checked": MetricValue(
                raw=len(checks), unit="count", normalized=None
            ),
            "profile_column_coverage": MetricValue(
                raw=round(coverage, 4), unit="ratio", normalized=norm.ratio(coverage),
                note=f"{detail['columns_published']}/{detail['columns_in_frame']} frame columns profiled",
            ),
            "profile_row_count_matches": MetricValue(
                raw=bool(row_check.passed) if row_check else None,
                unit="boolean",
                normalized=norm.boolean(row_check.passed) if row_check else None,
                status=None if row_check else EvalStatus.NOT_MEASURED,
                note=None if row_check else "the profile publishes no row_count",
            ),
        },
        thresholds={
            "profile_statistic_fidelity": Threshold(**{"pass": 100.0, "warn": 99.0}),
            "profile_column_coverage": Threshold(**{"pass": 100.0, "warn": 90.0}),
        },
        evidence=evidence,
        critical_findings=findings,
        metadata={
            "mode": "recomputed from the bundle's own input-dataset",
            "dataset_id": profile.get("dataset_id") or context.dataset_id,
            **detail,
        },
    )
