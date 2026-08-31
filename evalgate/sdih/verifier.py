"""Prove the labels are true before anyone scores against them.

If a label says a cell is NULL, the cell must actually be NULL in the dirty frame.
An unverified label set that silently drifts would make every downstream number
wrong in a way no other test would catch, so a failed verification degrades the
gate to BLOCKED_MISSING_GROUND_TRUTH rather than producing a score.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from evalgate.sdih.defect_taxonomy import DefectClass
from evalgate.sdih.label_store import LabelStore


@dataclass
class VerificationReport:
    passed: bool
    checked: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)
    per_class: dict[str, dict[str, int]] = field(default_factory=dict)
    count_mismatches: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checked": self.checked,
            "failure_count": len(self.failures),
            "failures": self.failures[:50],
            "per_class": self.per_class,
            "count_mismatches": self.count_mismatches,
        }


def _lookup(df: pd.DataFrame, label: Any, id_column: str | None) -> Any:
    """Resolve the labelled cell.

    Position wins over id: SDIH may have nulled or duplicated the id column
    itself, so an id lookup can find the wrong row or no row at all.
    """
    column = label.column
    if label.row_pos is not None and 0 <= label.row_pos < len(df):
        return df.iloc[label.row_pos, df.columns.get_loc(column)]
    if id_column and id_column in df.columns:
        matches = df.index[df[id_column].astype(str) == label.row_id]
        if len(matches) == 0:
            return KeyError
        return df.loc[matches[0], column]
    try:
        return df.loc[int(label.row_id), column]
    except (KeyError, ValueError):
        return KeyError


def verify(
    dirty: pd.DataFrame,
    store: LabelStore,
    *,
    id_column: str | None = None,
    original_domains: dict[str, set[str]] | None = None,
    expected_counts: dict[str, int] | None = None,
    ordered_pairs: list[tuple[str, str]] | None = None,
) -> VerificationReport:
    """Assert every SDIH-origin label matches the data actually present."""
    report = VerificationReport(passed=True)
    domains = original_domains or {}

    for label in store.labels:
        if label.origin != "sdih":
            continue  # pre-existing labels are asserted by their own manifest
        if label.column is None:
            continue
        report.checked += 1
        value = _lookup(dirty, label, id_column)
        if value is KeyError:
            report.failures.append(
                {
                    "row_id": label.row_id,
                    "column": label.column,
                    "defect": label.defect.value,
                    "reason": "row not found in dirty frame",
                }
            )
            continue

        ok = _assert_defect(label.defect, value, label.column, domains)
        if label.defect is DefectClass.CROSS_FIELD_VIOLATION and ordered_pairs:
            ok = _assert_cross_field(dirty, label, ordered_pairs)
        if label.defect is DefectClass.DUPLICATE_ROW:
            ok = _assert_duplicate(dirty, label)
        bucket = report.per_class.setdefault(
            label.defect.value, {"checked": 0, "failed": 0}
        )
        bucket["checked"] += 1
        if not ok:
            bucket["failed"] += 1
            report.failures.append(
                {
                    "row_id": label.row_id,
                    "column": label.column,
                    "defect": label.defect.value,
                    "observed": str(value)[:120],
                    "reason": "value does not match its label",
                }
            )

    if expected_counts:
        actual = store.counts_by_class()
        for name, expected in expected_counts.items():
            got = actual.get(name, 0)
            if got != expected:
                report.count_mismatches.append(
                    f"{name}: expected {expected}, stored {got}"
                )

    report.passed = not report.failures and not report.count_mismatches
    return report


def _assert_defect(
    defect: DefectClass, value: Any, column: str, domains: dict[str, set[str]]
) -> bool:
    if defect is DefectClass.MISSING_VALUE:
        return bool(pd.isna(value))
    if defect is DefectClass.SIGN_FLIP:
        return not pd.isna(value) and float(value) < 0
    if defect is DefectClass.INVALID_CATEGORY:
        domain = domains.get(column)
        if domain is None:
            return str(value).startswith("__SDIH_")
        return str(value) not in domain
    if defect is DefectClass.TYPE_VIOLATION:
        return isinstance(value, str) and value.startswith("__SDIH_")
    if defect is DefectClass.FORMAT_VIOLATION:
        return isinstance(value, str) and value.endswith("#")
    if defect in (DefectClass.OUT_OF_RANGE, DefectClass.OUTLIER):
        return not pd.isna(value)
    if defect is DefectClass.STALE_TIMESTAMP:
        return not pd.isna(value)
    if defect in (DefectClass.DUPLICATE_ROW, DefectClass.CROSS_FIELD_VIOLATION):
        return not pd.isna(value)
    return True


def _assert_cross_field(
    dirty: pd.DataFrame, label: Any, ordered_pairs: list[tuple[str, str]]
) -> bool:
    """The labelled row must actually violate the expected start <= end order."""
    for left, right in ordered_pairs:
        if left != label.column:
            continue
        left_value = dirty.iloc[label.row_pos, dirty.columns.get_loc(left)]
        right_value = dirty.iloc[label.row_pos, dirty.columns.get_loc(right)]
        if pd.isna(left_value) or pd.isna(right_value):
            return False
        return bool(left_value > right_value)
    return True


def _assert_duplicate(dirty: pd.DataFrame, label: Any) -> bool:
    """The labelled key value must now appear more than once."""
    series = dirty[label.column]
    value = series.iloc[label.row_pos]
    if pd.isna(value):
        return False
    return bool((series == value).sum() > 1)
