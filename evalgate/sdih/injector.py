"""Synthetic Defect Injection Harness -- deterministic ground truth for any schema.

The product cannot ship a fixed golden set once users upload arbitrary datasets,
so ground truth has to be *generated* per dataset.  SDIH profiles a DataFrame,
picks columns that satisfy each defect class's precondition, injects a fixed
number of defects per class at disjoint row positions, and records a cell-level
label for every one of them.  Cost: no LLM call, no network.

``preexisting_labels`` exists because a dataset can already contain injected
defects (the shipped NYC 50k fixture carries 1,250 of them at MUTATION_SEED=1337).
Injecting on top of those without accounting for them would silently overstate
precision, since the pre-existing defects would be scored as false positives.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from evalgate.sdih.defect_taxonomy import (
    ALL_DEFECTS,
    DefectClass,
    applicable_columns,
)
from evalgate.sdih.label_store import CellLabel, LabelStore
from evalgate.sdih.profiler import profile_dataframe

DEFAULT_N_PER_CLASS = 50
MIN_CLASSES_FOR_MEASURABLE_DATASET = 3

_INVALID_CATEGORY_TOKEN = "__SDIH_INVALID__"
_TYPE_VIOLATION_TOKEN = "__SDIH_NOT_A_VALUE__"
_STALE_YEARS = 10

#: Classes whose label is a claim about a *relationship*, not about one cell.
#:
#: "This row is a duplicate" is only true while some other row still holds the same
#: key; "pickup is after dropoff" is only true while both columns keep the values
#: that made it true. Disjoint row positions are therefore not enough for these --
#: another class writing anywhere in the same column can silently falsify the label,
#: and the resulting ground truth penalises the agent for missing a defect that is
#: no longer there. Their target columns are claimed exclusively.
RELATIONAL_DEFECTS: frozenset[DefectClass] = frozenset(
    {DefectClass.DUPLICATE_ROW, DefectClass.CROSS_FIELD_VIOLATION}
)


@dataclass
class InjectionPlan:
    """Which class goes into which column, and how many."""

    dataset_id: str
    seed: int
    n_per_class: int
    targets: dict[str, str] = field(default_factory=dict)  # class -> column (or pair)
    not_applicable: list[str] = field(default_factory=list)
    expected_counts: dict[str, int] = field(default_factory=dict)
    #: Non-fatal notes about the plan, e.g. a column claimed by several cell-local
    #: classes. Reported rather than silently accepted: a column labelled as
    #: simultaneously missing, mistyped and outlying is hard for a reviewer to read.
    warnings: list[str] = field(default_factory=list)

    @property
    def is_measurable(self) -> bool:
        return len(self.targets) >= MIN_CLASSES_FOR_MEASURABLE_DATASET


def build_plan(
    df: pd.DataFrame,
    *,
    dataset_id: str,
    seed: int,
    n_per_class: int = DEFAULT_N_PER_CLASS,
    profile: dict[str, Any] | None = None,
    skip_columns: dict[str, set[str]] | None = None,
) -> InjectionPlan:
    """Choose one target column per applicable defect class.

    ``skip_columns`` maps a defect class name to columns that must not be used --
    the mechanism that keeps SDIH away from columns already carrying pre-existing
    defects of that same class.
    """
    profile = profile or profile_dataframe(df)
    skip = skip_columns or {}
    rng = np.random.default_rng(seed)

    plan = InjectionPlan(dataset_id=dataset_id, seed=seed, n_per_class=n_per_class)

    # Relational classes choose first and claim their columns exclusively; a
    # cell-local class writing into the same column would falsify their labels.
    ordered = [d for d in ALL_DEFECTS if d in RELATIONAL_DEFECTS] + [
        d for d in ALL_DEFECTS if d not in RELATIONAL_DEFECTS
    ]
    claimed: set[str] = set()
    used_by: dict[str, list[str]] = {}

    for defect in ordered:
        candidates = applicable_columns(defect, profile)
        blocked = set(skip.get(defect.value, set()))
        if defect not in RELATIONAL_DEFECTS:
            blocked |= claimed
        candidates = [c for c in candidates if c not in blocked and not (
            # A cross-field target is a "left|right" pair; reject it if either side
            # is already claimed.
            "|" in c and set(c.split("|")) & blocked
        )]
        if not candidates:
            plan.not_applicable.append(defect.value)
            continue
        # Deterministic pick: sort then draw with the seeded generator.
        candidates = sorted(candidates)
        chosen = candidates[int(rng.integers(0, len(candidates)))]
        plan.targets[defect.value] = chosen
        plan.expected_counts[defect.value] = min(n_per_class, max(0, len(df) // 20))

        parts = chosen.split("|") if "|" in chosen else [chosen]
        if defect in RELATIONAL_DEFECTS:
            claimed.update(parts)
        for part in parts:
            used_by.setdefault(part, []).append(defect.value)

    for column, classes in sorted(used_by.items()):
        if len(classes) > 1:
            plan.warnings.append(
                f"{column}: targeted by {len(classes)} cell-local classes {sorted(classes)}"
            )
    return plan


def _row_ids(df: pd.DataFrame, id_column: str | None) -> list[str]:
    if id_column and id_column in df.columns:
        return [str(v) for v in df[id_column].tolist()]
    return [str(i) for i in df.index.tolist()]


def _disjoint_slices(
    total_rows: int, plan: InjectionPlan, seed: int
) -> dict[str, np.ndarray]:
    """Assign each defect class a non-overlapping block of row positions.

    Disjointness matters: overlapping injections would make a single row carry two
    labels and turn precision accounting ambiguous.
    """
    rng = np.random.default_rng(seed + 1)
    order = rng.permutation(total_rows)
    assignments: dict[str, np.ndarray] = {}
    cursor = 0
    for defect_name in sorted(plan.targets):
        count = plan.expected_counts.get(defect_name, 0)
        count = min(count, max(0, total_rows - cursor))
        assignments[defect_name] = order[cursor : cursor + count]
        cursor += count
    return assignments


def inject(
    df: pd.DataFrame,
    plan: InjectionPlan,
    *,
    id_column: str | None = None,
    preexisting_labels: list[CellLabel] | None = None,
) -> tuple[pd.DataFrame, LabelStore]:
    """Apply ``plan`` to a copy of ``df`` and return the dirty frame plus labels."""
    dirty = df.copy(deep=True)
    row_ids = _row_ids(dirty, id_column)
    store = LabelStore(dataset_id=plan.dataset_id, seed=plan.seed)
    store.applicable_classes = sorted(plan.targets)
    store.not_applicable_classes = sorted(plan.not_applicable)

    if preexisting_labels:
        store.extend(preexisting_labels)
        store.notes.append(
            f"{len(preexisting_labels)} pre-existing labels merged before injection; "
            "these count as true defects, not as agent false positives."
        )

    profile = profile_dataframe(df)
    slices = _disjoint_slices(len(dirty), plan, plan.seed)
    rng = np.random.default_rng(plan.seed + 2)

    for defect_name, positions in slices.items():
        if len(positions) == 0:
            continue
        defect = DefectClass(defect_name)
        target = plan.targets[defect_name]
        applied = _apply_defect(
            dirty, defect, target, positions, row_ids, profile, rng, store
        )
        if applied:
            store.injected_classes.append(defect_name)

    store.injected_classes = sorted(set(store.injected_classes))
    return dirty, store


def _apply_defect(
    dirty: pd.DataFrame,
    defect: DefectClass,
    target: str,
    positions: np.ndarray,
    row_ids: list[str],
    profile: dict[str, Any],
    rng: np.random.Generator,
    store: LabelStore,
) -> bool:
    columns = profile["columns"]

    if defect is DefectClass.MISSING_VALUE:
        for pos in positions:
            dirty.iloc[pos, dirty.columns.get_loc(target)] = None
            store.add(CellLabel(row_ids[pos], target, defect, row_pos=int(pos), detail="set to NULL"))
        return True

    if defect is DefectClass.SIGN_FLIP:
        loc = dirty.columns.get_loc(target)
        for pos in positions:
            value = dirty.iloc[pos, loc]
            if pd.isna(value):
                continue
            dirty.iloc[pos, loc] = -abs(float(value)) - 1.0
            store.add(CellLabel(row_ids[pos], target, defect, row_pos=int(pos), detail="negated"))
        return True

    if defect is DefectClass.OUT_OF_RANGE:
        column = columns[target]
        iqr = max(float(column.get("p75", 0)) - float(column.get("p25", 0)), 1.0)
        extreme = float(column.get("max", 0)) + 5.0 * iqr
        loc = dirty.columns.get_loc(target)
        for pos in positions:
            dirty.iloc[pos, loc] = extreme
            store.add(
                CellLabel(row_ids[pos], target, defect, row_pos=int(pos), detail=f"set to {extreme}")
            )
        return True

    if defect is DefectClass.INVALID_CATEGORY:
        loc = dirty.columns.get_loc(target)
        if not pd.api.types.is_object_dtype(dirty.iloc[:, loc]):
            dirty[target] = dirty[target].astype(object)
            loc = dirty.columns.get_loc(target)
        for pos in positions:
            dirty.iloc[pos, loc] = _INVALID_CATEGORY_TOKEN
            store.add(CellLabel(row_ids[pos], target, defect, row_pos=int(pos), detail="out-of-domain"))
        return True

    if defect is DefectClass.TYPE_VIOLATION:
        dirty[target] = dirty[target].astype(object)
        loc = dirty.columns.get_loc(target)
        for pos in positions:
            dirty.iloc[pos, loc] = _TYPE_VIOLATION_TOKEN
            store.add(CellLabel(row_ids[pos], target, defect, row_pos=int(pos), detail="non-castable"))
        return True

    if defect is DefectClass.STALE_TIMESTAMP:
        loc = dirty.columns.get_loc(target)
        for pos in positions:
            value = dirty.iloc[pos, loc]
            if pd.isna(value):
                continue
            dirty.iloc[pos, loc] = pd.Timestamp(value) - pd.DateOffset(
                years=_STALE_YEARS
            )
            store.add(
                CellLabel(row_ids[pos], target, defect, row_pos=int(pos), detail="shifted 10 years back")
            )
        return True

    if defect is DefectClass.FORMAT_VIOLATION:
        dirty[target] = dirty[target].astype(object)
        loc = dirty.columns.get_loc(target)
        for pos in positions:
            value = dirty.iloc[pos, loc]
            dirty.iloc[pos, loc] = f"{value}#"
            store.add(CellLabel(row_ids[pos], target, defect, row_pos=int(pos), detail="pattern broken"))
        return True

    if defect is DefectClass.OUTLIER:
        column = columns[target]
        spread = max(float(column.get("p95", 1)) - float(column.get("p05", 0)), 1.0)
        loc = dirty.columns.get_loc(target)
        for pos in positions:
            dirty.iloc[pos, loc] = float(column.get("p95", 0)) + 3.0 * spread
            store.add(CellLabel(row_ids[pos], target, defect, row_pos=int(pos), detail="extreme value"))
        return True

    if defect is DefectClass.DUPLICATE_ROW:
        # Overwrite the key column so the row becomes a duplicate of another one.
        #
        # The donor must not itself be one of the rows being overwritten. If it is,
        # a later iteration replaces the donor's key and the value copied here no
        # longer appears anywhere else -- the label then claims a duplicate that
        # does not exist, and the agent is penalised for not finding it.
        loc = dirty.columns.get_loc(target)
        targets = {int(p) for p in positions}
        donors = [int(d) for d in rng.permutation(len(dirty)) if int(d) not in targets]
        if not donors:
            # Every row is a target (only possible on a very small frame); there is
            # no untouched row to duplicate from, so the class is not applicable.
            return False
        for index, pos in enumerate(positions):
            donor = donors[index % len(donors)]
            dirty.iloc[pos, loc] = dirty.iloc[donor, loc]
            store.add(
                CellLabel(
                    row_ids[pos], target, defect, row_pos=int(pos), detail=f"key copied from row {donor}"
                )
            )
        return True

    if defect is DefectClass.CROSS_FIELD_VIOLATION:
        left, right = target.split("|", 1)
        left_loc = dirty.columns.get_loc(left)
        right_loc = dirty.columns.get_loc(right)
        for pos in positions:
            left_value = dirty.iloc[pos, left_loc]
            right_value = dirty.iloc[pos, right_loc]
            if pd.isna(left_value) or pd.isna(right_value):
                continue
            dirty.iloc[pos, left_loc] = right_value
            dirty.iloc[pos, right_loc] = left_value
            store.add(
                CellLabel(
                    row_ids[pos], left, defect, row_pos=int(pos), detail=f"swapped with {right}"
                )
            )
        return True

    return False
