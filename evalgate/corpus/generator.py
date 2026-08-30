"""Deterministic multi-domain dataset corpus.

Generalisation is the headline promise of an "upload any dataset" product, and it
cannot be measured against a single dataset.  These archetypes are synthetic on
purpose: no network, no licence question, no real PII, and byte-identical output
for a given seed.  The real NYC fixture stays in the corpus as the reality anchor.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
NYC_PARQUET = (
    PROJECT_ROOT
    / "data"
    / "yellow_tripdata_2025"
    / "semantic_data"
    / "yellow_tripdata_2025_semantic_50k.parquet"
)


@dataclass(frozen=True)
class Archetype:
    dataset_id: str
    description: str
    rows: int
    id_column: str
    builder: Callable[[int, int], pd.DataFrame]
    synthetic: bool = True
    pii_columns: tuple[str, ...] = ()


def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


def _ids(prefix: str, count: int) -> list[str]:
    return [f"{prefix}-{i:06d}" for i in range(count)]


def build_retail(rows: int, seed: int) -> pd.DataFrame:
    rng = _rng(seed)
    start = pd.Timestamp("2026-01-01")
    order_at = start + pd.to_timedelta(rng.integers(0, 24 * 180, rows), unit="h")
    return pd.DataFrame(
        {
            "order_id": _ids("ord", rows),
            "customer_ref": [f"cus-{i:05d}" for i in rng.integers(0, rows // 3 + 1, rows)],
            "channel": rng.choice(["web", "mobile", "store", "partner"], rows),
            "payment_method": rng.choice(["card", "cash", "wallet", "transfer"], rows),
            "quantity": rng.integers(1, 12, rows),
            "unit_price": np.round(rng.gamma(3.0, 12.0, rows), 2),
            "discount_pct": np.round(rng.uniform(0, 0.4, rows), 3),
            "order_total": np.round(rng.gamma(4.0, 30.0, rows), 2),
            "currency": rng.choice(["VND", "USD"], rows, p=[0.8, 0.2]),
            "ordered_at": order_at,
            "shipped_at": order_at + pd.to_timedelta(rng.integers(1, 96, rows), unit="h"),
            "status": rng.choice(["NEW", "PAID", "SHIPPED", "RETURNED"], rows),
            "warehouse_code": rng.choice(["WH01", "WH02", "WH03"], rows),
            "sku": [f"SKU{i:07d}" for i in rng.integers(0, 4000, rows)],
            "line_count": rng.integers(1, 8, rows),
        }
    )


def build_clinical(rows: int, seed: int) -> pd.DataFrame:
    rng = _rng(seed)
    admitted = pd.Timestamp("2025-06-01") + pd.to_timedelta(
        rng.integers(0, 24 * 300, rows), unit="h"
    )
    return pd.DataFrame(
        {
            "encounter_id": _ids("enc", rows),
            "patient_code": [f"PT{i:06d}" for i in rng.integers(0, rows // 2 + 1, rows)],
            "patient_name": [f"Benh nhan {i:05d}" for i in rng.integers(0, 9999, rows)],
            "date_of_birth": pd.Timestamp("1950-01-01")
            + pd.to_timedelta(rng.integers(0, 365 * 60, rows), unit="D"),
            "national_id": [f"0{rng.integers(10**10, 10**11 - 1)}" for _ in range(rows)],
            "department": rng.choice(
                ["CARDIO", "NEURO", "ONCO", "ORTHO", "PEDIA"], rows
            ),
            "diagnosis_code": rng.choice(
                ["I10", "E11", "J45", "K21", "M54", "N39"], rows
            ),
            "severity": rng.choice(["MILD", "MODERATE", "SEVERE"], rows),
            "admitted_at": admitted,
            "discharged_at": admitted
            + pd.to_timedelta(rng.integers(2, 500, rows), unit="h"),
            "bed_days": rng.integers(1, 30, rows),
            "total_cost": np.round(rng.gamma(5.0, 900.0, rows), 2),
            "insurance_pct": np.round(rng.uniform(0, 1, rows), 2),
            "attending_doctor": [f"BS{i:04d}" for i in rng.integers(0, 400, rows)],
            "ward_code": rng.choice(["W1", "W2", "W3", "W4"], rows),
        }
    )


def build_hr(rows: int, seed: int) -> pd.DataFrame:
    rng = _rng(seed)
    hired = pd.Timestamp("2015-01-01") + pd.to_timedelta(
        rng.integers(0, 365 * 10, rows), unit="D"
    )
    return pd.DataFrame(
        {
            "employee_id": _ids("emp", rows),
            "full_name": [f"Nhan vien {i:05d}" for i in range(rows)],
            "email": [f"user{i:05d}@example.internal" for i in range(rows)],
            "phone": [f"09{rng.integers(10**7, 10**8 - 1)}" for _ in range(rows)],
            "address": [f"So {rng.integers(1, 999)} duong Test" for _ in range(rows)],
            "date_of_birth": pd.Timestamp("1970-01-01")
            + pd.to_timedelta(rng.integers(0, 365 * 35, rows), unit="D"),
            "department": rng.choice(["ENG", "SALES", "OPS", "HR", "FIN"], rows),
            "grade": rng.choice(["G1", "G2", "G3", "G4", "G5"], rows),
            "base_salary": np.round(rng.gamma(6.0, 3_000_000.0, rows), 0),
            "bonus": np.round(rng.gamma(2.0, 1_000_000.0, rows), 0),
            "hired_at": hired,
            "contract_end_at": hired
            + pd.to_timedelta(rng.integers(365, 365 * 6, rows), unit="D"),
            "manager_id": [f"emp-{i:06d}" for i in rng.integers(0, rows, rows)],
            "employment_type": rng.choice(["FULL", "PART", "CONTRACT"], rows),
            "office_code": rng.choice(["HN", "SG", "DN"], rows),
            "annual_leave_days": rng.integers(0, 25, rows),
            "performance_score": np.round(rng.uniform(1, 5, rows), 2),
            "is_active": rng.choice([True, False], rows, p=[0.9, 0.1]),
        }
    )


def build_iot(rows: int, seed: int) -> pd.DataFrame:
    rng = _rng(seed)
    reading_at = pd.Timestamp("2026-05-01") + pd.to_timedelta(np.arange(rows), unit="s")
    return pd.DataFrame(
        {
            "reading_id": _ids("rdg", rows),
            "device_code": rng.choice([f"DEV{i:03d}" for i in range(40)], rows),
            "reading_at": reading_at,
            "temperature_c": np.round(rng.normal(28.0, 3.5, rows), 3),
            "humidity_pct": np.round(rng.uniform(35, 95, rows), 2),
            "vibration_mm_s": np.round(np.abs(rng.normal(1.2, 0.4, rows)), 4),
            "battery_pct": np.round(rng.uniform(5, 100, rows), 1),
            "signal_dbm": np.round(rng.normal(-70, 8, rows), 1),
        }
    )


def build_wide(rows: int, seed: int) -> pd.DataFrame:
    """220 columns -- deliberately past the 64-column ProposalEvidence cap."""
    rng = _rng(seed)
    data: dict[str, Any] = {"record_id": _ids("wid", rows)}
    for i in range(160):
        data[f"metric_{i:03d}"] = np.round(rng.normal(100, 15, rows), 3)
    for i in range(59):
        data[f"flag_{i:03d}"] = rng.choice(["Y", "N"], rows)
    return pd.DataFrame(data)


def build_tiny(rows: int, seed: int) -> pd.DataFrame:
    rng = _rng(seed)
    return pd.DataFrame(
        {
            "row_key": _ids("tiny", rows),
            "amount": np.round(rng.uniform(1, 100, rows), 2),
            "label": rng.choice(["a", "b"], rows),
        }
    )


def build_nyc(rows: int, seed: int) -> pd.DataFrame:
    """The real shipped fixture. Reality anchor -- not synthetic."""
    if not NYC_PARQUET.exists():
        raise FileNotFoundError(f"NYC fixture not found at {NYC_PARQUET}")
    frame = pd.read_parquet(NYC_PARQUET)
    if rows and rows < len(frame):
        frame = frame.head(rows)
    return frame.reset_index(drop=True)


ARCHETYPES: dict[str, Archetype] = {
    "corpus-synth-retail": Archetype(
        "corpus-synth-retail", "Retail transactions", 20_000, "order_id", build_retail
    ),
    "corpus-synth-clinical": Archetype(
        "corpus-synth-clinical",
        "Clinical encounters, PII-like",
        5_000,
        "encounter_id",
        build_clinical,
        pii_columns=("patient_name", "date_of_birth", "national_id"),
    ),
    "corpus-synth-hr": Archetype(
        "corpus-synth-hr",
        "HR employees, PII-heavy",
        2_000,
        "employee_id",
        build_hr,
        pii_columns=("full_name", "email", "phone", "address", "date_of_birth"),
    ),
    "corpus-synth-iot": Archetype(
        "corpus-synth-iot", "IoT sensor time series", 100_000, "reading_id", build_iot
    ),
    "corpus-synth-wide": Archetype(
        "corpus-synth-wide", "220-column wide table", 1_000, "record_id", build_wide
    ),
    "corpus-synth-tiny": Archetype(
        "corpus-synth-tiny", "50-row edge case", 50, "row_key", build_tiny
    ),
    "corpus-nyc-taxi-50k": Archetype(
        "corpus-nyc-taxi-50k",
        "Real NYC yellow taxi fixture (reality anchor)",
        50_000,
        "source_row_id",
        build_nyc,
        synthetic=False,
    ),
}


def generate(dataset_id: str, *, seed: int = 20260819, rows: int | None = None) -> pd.DataFrame:
    archetype = ARCHETYPES[dataset_id]
    return archetype.builder(rows or archetype.rows, seed)


def list_archetypes() -> list[Archetype]:
    return list(ARCHETYPES.values())
