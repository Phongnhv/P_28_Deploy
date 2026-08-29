"""Feature builder and validation for Isolation Forest anomaly detection.

Builds deterministic, schema-versioned feature vectors from execution results
and clean, causally-bounded, compatibility-partitioned historical runs.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from src.models.database import AnalysisRunModel, DqResultModel, DqRunModel
from src.services.rule_store import TestResultModel, TestRunModel

logger = logging.getLogger(__name__)

FEATURE_SCHEMA_VERSION = "iforest-features-v1"
FEATURE_NAMES: list[str] = [
    "violation_rate",
    "violation_rate_delta",
    "log1p_violation_count",
    "log1p_total_rows",
    "log1p_duration_ms",
]


@dataclass(frozen=True)
class RuleFeatureFrame:
    """Represents the complete feature bundle for a single rule."""

    rule_id: str
    current_vector: list[float] | None
    history_vectors: list[list[float]]
    history_run_ids: list[str]
    latest_historical_rate: float | None
    feature_schema_version: str
    compatibility_key: str
    skipped_sample_count: int
    disable_reason: str | None


def extract_feature_vector(
    violation_rate: float,
    prev_violation_rate: float | None,
    violation_count: int | float,
    total_rows: int | float,
    duration_ms: float | None = None,
) -> list[float]:
    """Compatibility helper: extract and validate a single 5-dimensional feature vector."""
    res = extract_validated_feature_vector(
        violation_count=violation_count,
        total_rows=total_rows,
        duration_ms=duration_ms,
        prev_violation_rate=prev_violation_rate,
    )
    if res is not None:
        return res
    # Fallback to zero-vector if called from legacy tests
    return [0.0, 0.0, 0.0, 0.0, 0.0]


def extract_validated_feature_vector(
    violation_count: int | float | None,
    total_rows: int | float | None,
    duration_ms: float | None,
    prev_violation_rate: float | None,
) -> list[float] | None:
    """Extract and strictly validate a single 5-dimensional feature vector.

    Validation rules:
      - All values must be finite numeric values.
      - total_rows > 0.
      - 0 <= violation_count <= total_rows.
      - duration_ms >= 0 and finite.
      - Recalculate violation_rate = violation_count / total_rows.
      - If prev_violation_rate is None (first chronological point), delta = 0.0.
      - Otherwise, delta = violation_rate - prev_violation_rate.

    Returns None if any validation check fails (reject rather than clamp).
    """
    if violation_count is None or total_rows is None:
        return None
    try:
        v_count = float(violation_count)
        t_rows = float(total_rows)
        dur = float(duration_ms) if duration_ms is not None else 0.0
    except (ValueError, TypeError):
        return None

    if not (math.isfinite(v_count) and math.isfinite(t_rows) and math.isfinite(dur)):
        return None

    if t_rows <= 0.0:
        return None
    if v_count < 0.0 or v_count > t_rows:
        return None
    if dur < 0.0:
        return None

    v_rate = v_count / t_rows

    if prev_violation_rate is not None:
        if not math.isfinite(prev_violation_rate) or not (0.0 <= prev_violation_rate <= 1.0):
            return None
        v_delta = v_rate - prev_violation_rate
    else:
        v_delta = 0.0

    return [
        float(v_rate),
        float(v_delta),
        float(math.log1p(v_count)),
        float(math.log1p(t_rows)),
        float(math.log1p(dur)),
    ]


def build_bulk_rule_feature_frames(
    db: Session,
    current_run: Any,
    current_results: list[Any],
    uses_test_store: bool,
    excluded_run_ids: set[str],
    feature_schema_version: str = "iforest-features-v1",
    max_history: int = 90,
) -> dict[str, RuleFeatureFrame]:
    """Accept all eligible rule results in one call and construct RuleFeatureFrames with one bulk query.

    Enforces:
      - Strict causal boundary: historical.created_at < current_run.created_at.
      - Compatibility partitioning (dataset_version, ruleset_version, compiler_version / snapshot).
      - Strict numeric feature validation.
    """
    if feature_schema_version != FEATURE_SCHEMA_VERSION:
        return {
            res.rule_id: RuleFeatureFrame(
                rule_id=res.rule_id,
                current_vector=None,
                history_vectors=[],
                history_run_ids=[],
                latest_historical_rate=None,
                feature_schema_version=feature_schema_version,
                compatibility_key="unsupported_schema",
                skipped_sample_count=0,
                disable_reason="UNSUPPORTED_FEATURE_SCHEMA",
            )
            for res in current_results
        }

    # Filter out business invariant rules from ML consideration
    eligible_results = []
    for res in current_results:
        rule_title = str(getattr(res, "rule_title", ""))
        is_biz = (
            rule_title.startswith("BUSINESS_")
            or "invariant" in rule_title.lower()
            or res.rule_id.endswith(".BUSINESS_RULE")
        )
        if not is_biz:
            eligible_results.append(res)

    if not eligible_results:
        return {}

    rule_ids = [res.rule_id for res in eligible_results]
    current_created_at = getattr(current_run, "created_at", None)

    frames: dict[str, RuleFeatureFrame] = {}

    if uses_test_store:
        execution_run_id = getattr(current_run, "test_run_id", "")
        # Resolve compatibility metadata from linked AnalysisRunModel
        analysis_run = (
            db.query(AnalysisRunModel)
            .filter(AnalysisRunModel.test_run_id == execution_run_id)
            .first()
        )
        if not analysis_run:
            # Missing compatibility metadata -> disable ML safely
            for res in eligible_results:
                frames[res.rule_id] = RuleFeatureFrame(
                    rule_id=res.rule_id,
                    current_vector=None,
                    history_vectors=[],
                    history_run_ids=[],
                    latest_historical_rate=None,
                    feature_schema_version=feature_schema_version,
                    compatibility_key="none",
                    skipped_sample_count=0,
                    disable_reason="MISSING_COMPATIBILITY_METADATA",
                )
            return frames

        cur_dataset_version_id = analysis_run.dataset_version_id
        cur_snapshot_id = analysis_run.rule_review_snapshot_id
        compatibility_key = f"ds_ver={cur_dataset_version_id}|snapshot={cur_snapshot_id}"

        # Bulk historical query
        hist_query = (
            db.query(
                TestResultModel.rule_id,
                TestResultModel.violation_count,
                TestResultModel.total_rows,
                TestResultModel.duration_ms,
                TestResultModel.status,
                TestRunModel.test_run_id.label("run_id"),
                TestRunModel.created_at,
            )
            .join(TestRunModel, TestRunModel.test_run_id == TestResultModel.test_run_id)
            .join(AnalysisRunModel, AnalysisRunModel.test_run_id == TestRunModel.test_run_id)
            .filter(
                TestResultModel.rule_id.in_(rule_ids),
                TestRunModel.dataset_id == current_run.dataset_id,
                TestRunModel.test_run_id != execution_run_id,
                TestRunModel.status == "DONE",
                TestResultModel.status.in_(["PASS", "FAIL", "PASSED", "FAILED"]),
            )
        )
        if current_created_at is not None:
            hist_query = hist_query.filter(TestRunModel.created_at < current_created_at)

        # Null-safe equality filtering on compatibility partition
        if cur_dataset_version_id is not None:
            hist_query = hist_query.filter(AnalysisRunModel.dataset_version_id == cur_dataset_version_id)
        else:
            hist_query = hist_query.filter(AnalysisRunModel.dataset_version_id.is_(None))

        if cur_snapshot_id is not None:
            hist_query = hist_query.filter(AnalysisRunModel.rule_review_snapshot_id == cur_snapshot_id)
        else:
            hist_query = hist_query.filter(AnalysisRunModel.rule_review_snapshot_id.is_(None))

        raw_history_rows = hist_query.order_by(TestRunModel.created_at.asc()).all()

    else:
        execution_run_id = getattr(current_run, "id", "")
        cur_dataset_version_id = getattr(current_run, "dataset_version_id", None)
        cur_ruleset_version_id = getattr(current_run, "ruleset_version_id", None)
        cur_compiler_version = getattr(current_run, "compiler_version", None)
        compatibility_key = (
            f"ds_ver={cur_dataset_version_id}|ruleset_ver={cur_ruleset_version_id}|compiler={cur_compiler_version}"
        )

        hist_query = (
            db.query(
                DqResultModel.rule_id,
                DqResultModel.failed_count.label("violation_count"),
                DqResultModel.checked_count.label("total_rows"),
                DqResultModel.duration_ms,
                DqResultModel.status,
                DqRunModel.id.label("run_id"),
                DqRunModel.created_at,
            )
            .join(DqRunModel, DqRunModel.id == DqResultModel.run_id)
            .filter(
                DqResultModel.rule_id.in_(rule_ids),
                DqRunModel.dataset_id == current_run.dataset_id,
                DqRunModel.id != execution_run_id,
                or_(DqRunModel.status == "SUCCEEDED", DqRunModel.status == "DONE"),
                DqResultModel.status.in_(["PASS", "FAIL", "PASSED", "FAILED"]),
            )
        )
        if current_created_at is not None:
            hist_query = hist_query.filter(DqRunModel.created_at < current_created_at)

        # Null-safe compatibility filtering
        if cur_dataset_version_id is not None:
            hist_query = hist_query.filter(DqRunModel.dataset_version_id == cur_dataset_version_id)
        else:
            hist_query = hist_query.filter(DqRunModel.dataset_version_id.is_(None))

        if cur_ruleset_version_id is not None:
            hist_query = hist_query.filter(DqRunModel.ruleset_version_id == cur_ruleset_version_id)
        else:
            hist_query = hist_query.filter(DqRunModel.ruleset_version_id.is_(None))

        if cur_compiler_version is not None:
            hist_query = hist_query.filter(DqRunModel.compiler_version == cur_compiler_version)
        else:
            hist_query = hist_query.filter(DqRunModel.compiler_version.is_(None))

        raw_history_rows = hist_query.order_by(DqRunModel.created_at.asc()).all()

    # Group historical rows by rule_id in memory
    history_by_rule: dict[str, list[Any]] = {rid: [] for rid in rule_ids}
    for row in raw_history_rows:
        if row.run_id in excluded_run_ids:
            continue
        if row.rule_id in history_by_rule:
            history_by_rule[row.rule_id].append(row)

    # Process each eligible rule result
    for res in eligible_results:
        rid = res.rule_id
        hist_rows = history_by_rule.get(rid, [])

        hist_vectors: list[list[float]] = []
        hist_run_ids: list[str] = []
        skipped_count = 0
        prev_rate: float | None = None

        for h_row in hist_rows:
            v_count = getattr(h_row, "violation_count", None)
            t_rows = getattr(h_row, "total_rows", None)
            dur_ms = getattr(h_row, "duration_ms", None)

            vec = extract_validated_feature_vector(
                violation_count=v_count,
                total_rows=t_rows,
                duration_ms=dur_ms,
                prev_violation_rate=prev_rate,
            )
            if vec is None:
                skipped_count += 1
                continue

            hist_vectors.append(vec)
            hist_run_ids.append(h_row.run_id)
            prev_rate = vec[0]  # violation_rate is at index 0

        # Keep only the last max_history valid historical samples
        if len(hist_vectors) > max_history:
            hist_vectors = hist_vectors[-max_history:]
            hist_run_ids = hist_run_ids[-max_history:]

        latest_hist_rate = hist_vectors[-1][0] if hist_vectors else None

        # Extract & validate current vector
        if uses_test_store:
            cur_v_count = getattr(res, "violation_count", None)
            cur_t_rows = getattr(res, "total_rows", None)
            cur_dur = getattr(res, "duration_ms", None)
        else:
            cur_v_count = getattr(res, "failed_count", None)
            cur_t_rows = getattr(res, "checked_count", None)
            cur_dur = getattr(res, "duration_ms", None)

        current_vector = extract_validated_feature_vector(
            violation_count=cur_v_count,
            total_rows=cur_t_rows,
            duration_ms=cur_dur,
            prev_violation_rate=latest_hist_rate,
        )

        disable_reason = None
        if current_vector is None:
            disable_reason = "INVALID_CURRENT_VECTOR"

        frames[rid] = RuleFeatureFrame(
            rule_id=rid,
            current_vector=current_vector,
            history_vectors=hist_vectors,
            history_run_ids=hist_run_ids,
            latest_historical_rate=latest_hist_rate,
            feature_schema_version=feature_schema_version,
            compatibility_key=compatibility_key,
            skipped_sample_count=skipped_count,
            disable_reason=disable_reason,
        )

    return frames


def build_clean_history_features(
    db: Session,
    dataset_id: str,
    rule_id: str,
    current_execution_run_id: str,
    uses_test_store: bool,
    excluded_run_ids: set[str],
    max_history: int = 90,
) -> tuple[list[list[float]], float | None]:
    """Compatibility wrapper for single-rule historical query (e.g. used in isolated unit tests)."""
    # Create a mock current run for compatibility wrapper
    class _MockRun:
        def __init__(self):
            self.id = current_execution_run_id
            self.test_run_id = current_execution_run_id
            self.dataset_id = dataset_id
            self.dataset_version_id = None
            self.ruleset_version_id = None
            self.compiler_version = None
            self.created_at = None

    class _MockResult:
        def __init__(self):
            self.rule_id = rule_id
            self.rule_title = "Rule"
            self.violation_count = 0
            self.total_rows = 100
            self.failed_count = 0
            self.checked_count = 100
            self.duration_ms = 0.0

    frames = build_bulk_rule_feature_frames(
        db=db,
        current_run=_MockRun(),
        current_results=[_MockResult()],
        uses_test_store=uses_test_store,
        excluded_run_ids=excluded_run_ids,
        max_history=max_history,
    )
    frame = frames.get(rule_id)
    if not frame:
        return [], None
    return frame.history_vectors, frame.latest_historical_rate


def extract_current_feature_vector(
    res: Any,
    uses_test_store: bool,
    prev_violation_rate: float | None,
) -> list[float]:
    """Compatibility wrapper: extract feature vector for current result."""
    if uses_test_store:
        v_count = getattr(res, "violation_count", 0)
        t_rows = getattr(res, "total_rows", 0)
        dur = getattr(res, "duration_ms", 0.0)
    else:
        v_count = getattr(res, "failed_count", 0)
        t_rows = getattr(res, "checked_count", 0)
        dur = getattr(res, "duration_ms", 0.0)

    vec = extract_validated_feature_vector(
        violation_count=v_count,
        total_rows=t_rows,
        duration_ms=dur,
        prev_violation_rate=prev_violation_rate,
    )
    return vec if vec is not None else [0.0, 0.0, 0.0, 0.0, 0.0]
