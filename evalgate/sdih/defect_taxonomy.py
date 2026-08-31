"""The 10 schema-agnostic defect classes SDIH can inject.

A defect class is only injected into a column that actually satisfies its
precondition.  A dataset with no eligible column for a class does **not** score
recall 0 for that class -- it reports the class as NOT_APPLICABLE.  Conflating
"the agent missed it" with "there was nothing to miss" is the single easiest way
to produce a wrong evaluation, so the distinction is enforced structurally.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class DefectClass(StrEnum):
    MISSING_VALUE = "MISSING_VALUE"
    SIGN_FLIP = "SIGN_FLIP"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    INVALID_CATEGORY = "INVALID_CATEGORY"
    TYPE_VIOLATION = "TYPE_VIOLATION"
    DUPLICATE_ROW = "DUPLICATE_ROW"
    CROSS_FIELD_VIOLATION = "CROSS_FIELD_VIOLATION"
    STALE_TIMESTAMP = "STALE_TIMESTAMP"
    FORMAT_VIOLATION = "FORMAT_VIOLATION"
    OUTLIER = "OUTLIER"


#: Which data-quality dimension each class exercises (Zhou et al. 2024 taxonomy).
DQ_DIMENSION: dict[DefectClass, str] = {
    DefectClass.MISSING_VALUE: "COMPLETENESS",
    DefectClass.SIGN_FLIP: "VALIDITY",
    DefectClass.OUT_OF_RANGE: "VALIDITY",
    DefectClass.INVALID_CATEGORY: "VALIDITY",
    DefectClass.TYPE_VIOLATION: "VALIDITY",
    DefectClass.DUPLICATE_ROW: "UNIQUENESS",
    DefectClass.CROSS_FIELD_VIOLATION: "CONSISTENCY",
    DefectClass.STALE_TIMESTAMP: "FRESHNESS",
    DefectClass.FORMAT_VIOLATION: "VALIDITY",
    DefectClass.OUTLIER: "ACCURACY",
}

#: Classes an agent is expected to find with a simple rule versus ones that need
#: real reasoning.  Recall is reported separately per band so a high score cannot
#: come purely from the easy half.
DIFFICULTY: dict[DefectClass, str] = {
    DefectClass.MISSING_VALUE: "EASY",
    DefectClass.SIGN_FLIP: "EASY",
    DefectClass.INVALID_CATEGORY: "EASY",
    DefectClass.DUPLICATE_ROW: "MEDIUM",
    DefectClass.OUT_OF_RANGE: "MEDIUM",
    DefectClass.STALE_TIMESTAMP: "MEDIUM",
    DefectClass.TYPE_VIOLATION: "HARD",
    DefectClass.CROSS_FIELD_VIOLATION: "HARD",
    DefectClass.FORMAT_VIOLATION: "HARD",
    DefectClass.OUTLIER: "HARD",
}

#: Rule types in the product catalogue that *should* catch each class.  Used to
#: attribute a detection to the right proposed rule during replay scoring.
EXPECTED_RULE_TYPES: dict[DefectClass, tuple[str, ...]] = {
    DefectClass.MISSING_VALUE: ("NOT_NULL", "NULL_RATE"),
    DefectClass.SIGN_FLIP: ("RANGE",),
    DefectClass.OUT_OF_RANGE: ("RANGE",),
    DefectClass.INVALID_CATEGORY: ("ACCEPTED_VALUES",),
    DefectClass.TYPE_VIOLATION: ("REGEX_FORMAT", "ACCEPTED_VALUES"),
    DefectClass.DUPLICATE_ROW: ("UNIQUE",),
    DefectClass.CROSS_FIELD_VIOLATION: ("CROSS_FIELD_COMPARISON",),
    DefectClass.STALE_TIMESTAMP: ("FRESHNESS",),
    DefectClass.FORMAT_VIOLATION: ("REGEX_FORMAT",),
    DefectClass.OUTLIER: ("RANGE", "STATISTICAL_DISTRIBUTION"),
}


# ---------------------------------------------------------------------------
# Applicability predicates -- each takes a column profile produced by profiler.py
# ---------------------------------------------------------------------------

def _is_numeric(column: dict[str, Any]) -> bool:
    return bool(column.get("is_numeric"))


def _is_datetime(column: dict[str, Any]) -> bool:
    return bool(column.get("is_datetime"))


def _is_text(column: dict[str, Any]) -> bool:
    return bool(column.get("is_text"))


def applicable_columns(
    defect: DefectClass, profile: dict[str, Any]
) -> list[str]:
    """Return the columns of ``profile`` eligible for ``defect``."""
    columns: dict[str, dict[str, Any]] = profile.get("columns", {})
    eligible: list[str] = []

    for name, column in columns.items():
        if defect is DefectClass.MISSING_VALUE:
            # Only a column with zero nulls today gives an unambiguous label.
            if column.get("null_rate") == 0.0:
                eligible.append(name)
        elif defect is DefectClass.SIGN_FLIP:
            if _is_numeric(column) and (column.get("min") is not None) and column["min"] >= 0:
                eligible.append(name)
        elif defect is DefectClass.OUT_OF_RANGE:
            if _is_numeric(column) and column.get("p75") is not None:
                eligible.append(name)
        elif defect is DefectClass.INVALID_CATEGORY:
            if column.get("is_categorical") and 0 < column.get("distinct_count", 0) <= 20:
                eligible.append(name)
        elif defect is DefectClass.TYPE_VIOLATION:
            if _is_numeric(column) or _is_datetime(column):
                eligible.append(name)
        elif defect is DefectClass.STALE_TIMESTAMP:
            if _is_datetime(column):
                eligible.append(name)
        elif defect is DefectClass.FORMAT_VIOLATION:
            length = column.get("length_stats") or {}
            if _is_text(column) and length.get("min") == length.get("max") and length.get("min"):
                eligible.append(name)
        elif defect is DefectClass.OUTLIER:
            if _is_numeric(column) and column.get("p95") is not None:
                eligible.append(name)

    if defect is DefectClass.DUPLICATE_ROW:
        # Table-level: needs at least one business-key candidate.
        if profile.get("key_candidates"):
            eligible = list(profile["key_candidates"][:1])
    if defect is DefectClass.CROSS_FIELD_VIOLATION:
        pairs = profile.get("ordered_pairs") or []
        if pairs:
            eligible = [f"{pairs[0][0]}|{pairs[0][1]}"]

    return eligible


ALL_DEFECTS: tuple[DefectClass, ...] = tuple(DefectClass)
