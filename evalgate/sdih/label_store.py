"""Cell-level and row-level ground truth for an injected dataset.

The store is the only thing downstream evaluators are allowed to treat as truth,
so it carries its own provenance: which classes were applicable, which were
actually injected, and which labels came from a pre-existing seed rather than
from this run.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evalgate.sdih.defect_taxonomy import DefectClass


@dataclass(frozen=True)
class CellLabel:
    row_id: str
    column: str | None  # None for table-level defects such as DUPLICATE_ROW
    defect: DefectClass
    origin: str = "sdih"  # "sdih" | "preexisting"
    detail: str = ""
    # Positional identity. Kept because SDIH may itself corrupt the id column
    # (MISSING_VALUE, DUPLICATE_ROW), after which lookup by row_id is unreliable.
    row_pos: int = -1


@dataclass
class LabelStore:
    """Ground truth for one dataset."""

    dataset_id: str
    seed: int
    labels: list[CellLabel] = field(default_factory=list)
    applicable_classes: list[str] = field(default_factory=list)
    injected_classes: list[str] = field(default_factory=list)
    not_applicable_classes: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    # -- construction ------------------------------------------------------
    def add(self, label: CellLabel) -> None:
        self.labels.append(label)

    def extend(self, labels: list[CellLabel]) -> None:
        self.labels.extend(labels)

    # -- queries -----------------------------------------------------------
    def by_class(self) -> dict[str, list[CellLabel]]:
        grouped: dict[str, list[CellLabel]] = defaultdict(list)
        for label in self.labels:
            grouped[label.defect.value].append(label)
        return dict(grouped)

    def by_column(self) -> dict[str, list[CellLabel]]:
        grouped: dict[str, list[CellLabel]] = defaultdict(list)
        for label in self.labels:
            grouped[label.column or "__table__"].append(label)
        return dict(grouped)

    def dirty_row_ids(self) -> set[str]:
        return {label.row_id for label in self.labels}

    def counts_by_class(self) -> dict[str, int]:
        return {name: len(items) for name, items in self.by_class().items()}

    def expected_columns_for(self, defect: DefectClass) -> set[str]:
        return {
            label.column
            for label in self.labels
            if label.defect is defect and label.column
        }

    # -- persistence -------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "seed": self.seed,
            "applicable_classes": sorted(self.applicable_classes),
            "injected_classes": sorted(self.injected_classes),
            "not_applicable_classes": sorted(self.not_applicable_classes),
            "counts_by_class": self.counts_by_class(),
            "total_labels": len(self.labels),
            "total_dirty_rows": len(self.dirty_row_ids()),
            "notes": self.notes,
            "labels": [
                {
                    "row_id": label.row_id,
                    "column": label.column,
                    "defect": label.defect.value,
                    "origin": label.origin,
                    "row_pos": label.row_pos,
                    "detail": label.detail,
                }
                for label in self.labels
            ],
        }

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return target

    def fingerprint(self) -> str:
        """Stable digest used by the determinism test."""
        import hashlib

        payload = "|".join(
            sorted(
                f"{label.row_id}::{label.column}::{label.defect.value}::{label.origin}"
                for label in self.labels
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, LabelStore):
            return NotImplemented
        return self.fingerprint() == other.fingerprint()
