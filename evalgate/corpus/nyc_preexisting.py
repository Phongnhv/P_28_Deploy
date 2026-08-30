"""Recover the defects already present in the shipped NYC 50k fixture.

``data/yellow_tripdata_2025/semantic_data/SEMANTIC_PROCESSING_REPORT.md`` records
that 1,250 rows were mutated at ``MUTATION_SEED = 1337``, 250 each across five
classes.  Those defects are real and the agent is supposed to catch them.

If SDIH injected on top without knowing about them, two things would go wrong:
SDIH would skip exactly the columns that already carry defects (``vendor_id`` no
longer has ``null_rate == 0``; ``fare_amount`` no longer has ``min >= 0``), and
every pre-existing defect the agent *did* catch would be counted as a false
positive.  Precision would come out systematically too high.

So the fixture's defects are recovered from the data itself and merged into the
LabelStore with ``origin="preexisting"``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from evalgate.sdih.defect_taxonomy import DefectClass
from evalgate.sdih.label_store import CellLabel

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SEMANTIC_DIR = PROJECT_ROOT / "data" / "yellow_tripdata_2025" / "semantic_data"
MANIFEST = SEMANTIC_DIR / "manifest.json"
REPORT = SEMANTIC_DIR / "SEMANTIC_PROCESSING_REPORT.md"

MUTATION_SEED = 1337
EXPECTED_PER_CLASS = 250

#: Governed payment values, per the dataset policy that shipped with the fixture.
GOVERNED_PAYMENT_TYPES = {
    "Flex Fare trip",
    "Credit card",
    "Cash",
    "No charge",
    "Dispute",
    "Unknown",
    "Voided trip",
}

FINGERPRINT_COLUMNS = ("vendor_id", "pickup_at", "dropoff_at", "trip_distance")


def _row_id(frame: pd.DataFrame, pos: int) -> str:
    if "source_row_id" in frame.columns:
        return str(frame.iloc[pos]["source_row_id"])
    return str(pos)


def recover_labels(df: pd.DataFrame) -> tuple[list[CellLabel], dict[str, Any]]:
    """Derive labels for the five pre-seeded defect classes from the data."""
    labels: list[CellLabel] = []
    summary: dict[str, Any] = {
        "mutation_seed": MUTATION_SEED,
        "expected_per_class": EXPECTED_PER_CLASS,
        "recovered": {},
        "source": str(REPORT.relative_to(PROJECT_ROOT)) if REPORT.exists() else None,
    }

    def _record(positions: list[int], column: str, defect: DefectClass, detail: str):
        for pos in positions:
            labels.append(
                CellLabel(
                    row_id=_row_id(df, pos),
                    column=column,
                    defect=defect,
                    origin="preexisting",
                    row_pos=int(pos),
                    detail=detail,
                )
            )
        summary["recovered"][f"{defect.value}:{column}"] = len(positions)

    # 1. null_vendor_id -> MISSING_VALUE
    if "vendor_id" in df.columns:
        positions = df.index[df["vendor_id"].isna()].tolist()
        _record(
            [df.index.get_loc(i) for i in positions],
            "vendor_id",
            DefectClass.MISSING_VALUE,
            "pre-seeded null (MUTATION_SEED=1337)",
        )

    # 2 & 3. negative_fare_amount / negative_trip_distance -> SIGN_FLIP
    for column in ("fare_amount", "trip_distance"):
        if column not in df.columns:
            continue
        numeric = pd.to_numeric(df[column], errors="coerce")
        positions = [df.index.get_loc(i) for i in df.index[numeric < 0].tolist()]
        _record(
            positions,
            column,
            DefectClass.SIGN_FLIP,
            "pre-seeded negative (MUTATION_SEED=1337)",
        )

    # 4. invalid_payment_type -> INVALID_CATEGORY
    if "payment_type" in df.columns:
        values = df["payment_type"].astype(str)
        mask = ~values.isin(GOVERNED_PAYMENT_TYPES) & df["payment_type"].notna()
        positions = [df.index.get_loc(i) for i in df.index[mask].tolist()]
        _record(
            positions,
            "payment_type",
            DefectClass.INVALID_CATEGORY,
            "outside governed value set (MUTATION_SEED=1337)",
        )

    # 5. duplicate_fingerprint -> DUPLICATE_ROW
    present = [c for c in FINGERPRINT_COLUMNS if c in df.columns]
    if len(present) >= 2:
        duplicated = df.duplicated(subset=present, keep="first")
        positions = [df.index.get_loc(i) for i in df.index[duplicated].tolist()]
        _record(
            positions,
            present[0],
            DefectClass.DUPLICATE_ROW,
            f"duplicate business fingerprint over {present}",
        )

    summary["total_recovered"] = len(labels)
    return labels, summary


def columns_to_skip(labels: list[CellLabel]) -> dict[str, set[str]]:
    """Columns SDIH must not reuse for a class that already has defects there."""
    skip: dict[str, set[str]] = {}
    for label in labels:
        if label.column:
            skip.setdefault(label.defect.value, set()).add(label.column)
    return skip
