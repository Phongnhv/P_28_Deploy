"""HG-D1: does ingestion preserve the data, or quietly rewrite it?

``src/worker.py`` coerces every incoming cell with ``to_float`` / ``to_int`` /
``to_str``, and each of them returns ``None`` when conversion raises.  A value the
source recorded as ``"12,50"`` therefore lands in the database as NULL, with no
counter, no log line and no difference from a cell that was genuinely empty.

That matters more here than in an ordinary pipeline.  This is a data-quality
product: after ingestion silently manufactures nulls, profiling measures a high
null rate, and the agent proposes a NULL_RATE rule that accepts it.  The system
ends up certifying damage it caused itself.

Two things are measured, and they answer different questions:

  round-trip fidelity  -- does a clean value survive ingestion unchanged?
  the malformed matrix -- when a value cannot be converted, is anything reported?

No upload endpoint is required for either, so HG-D1 stops being an unmeasurable
hard gate today rather than after the ingest surface is built.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

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
EVALUATOR = "ingest_fidelity_v1"

#: Archetypes used for the round-trip pass. Synthetic on purpose: the check must not
#: depend on a data/ directory that is git-ignored and absent from a fresh clone.
ROUND_TRIP_ARCHETYPES = ("corpus-synth-retail", "corpus-synth-iot", "corpus-synth-tiny")

ACCEPT = "accept"   # a correct ingest preserves the value
REJECT = "reject"   # a correct ingest refuses the value and says so


@dataclass
class CoercionCase:
    raw: str
    coercer: str
    expectation: str
    why: str


#: Values a real CSV contains. Each one is either something ingestion must preserve
#: or something it must refuse loudly -- never something it may silently drop.
MALFORMED_MATRIX: tuple[CoercionCase, ...] = (
    CoercionCase("12,50", "to_float", REJECT, "European decimal comma; a real fare of 12.50"),
    CoercionCase("N/A", "to_int", REJECT, "textual missing marker, not an empty cell"),
    CoercionCase("1e999", "to_float", REJECT, "overflows to infinity rather than failing"),
    CoercionCase("nan", "to_float", REJECT, "parses to NaN and poisons every later aggregate"),
    CoercionCase("inf", "to_float", REJECT, "parses to infinity"),
    CoercionCase("0x1A", "to_int", REJECT, "hexadecimal literal"),
    # Python's int() understands Unicode decimal digits, so this converts to 3 rather
    # than being lost. Normalisation is defensible; it is listed to keep the matrix
    # honest about which surprises are harmless.
    CoercionCase("٣", "to_int", ACCEPT, "Arabic-Indic digit three normalises to 3"),
    CoercionCase("1.2.3", "to_float", REJECT, "malformed version-like token"),
    CoercionCase("$12.50", "to_float", REJECT, "currency symbol"),
    CoercionCase("  4  ", "to_int", ACCEPT, "surrounding whitespace is not corruption"),
    CoercionCase("-0.0", "to_float", ACCEPT, "signed zero is a valid amount"),
    CoercionCase("0", "to_int", ACCEPT, "zero must not be confused with missing"),
    CoercionCase("2025-01-15T10:30:00", "to_str", ACCEPT, "timestamps pass through as text"),
)


@dataclass
class CaseOutcome:
    raw: str
    coercer: str
    expectation: str
    produced: str
    silent_loss: bool
    why: str


def _coercers() -> dict[str, object]:
    from src.worker import to_float, to_int, to_str

    return {"to_float": to_float, "to_int": to_int, "to_str": to_str}


def _describe(value: object) -> str:
    if value is None:
        return "None"
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        if math.isinf(value):
            return "inf"
    return repr(value)


def run_malformed_matrix() -> list[CaseOutcome]:
    coercers = _coercers()
    outcomes: list[CaseOutcome] = []
    for case in MALFORMED_MATRIX:
        produced = coercers[case.coercer](case.raw)
        if case.expectation == REJECT:
            # A rejection is only safe when it is visible. Returning None, NaN or
            # infinity is indistinguishable from a legitimately empty cell.
            silent = produced is None or (
                isinstance(produced, float) and (math.isnan(produced) or math.isinf(produced))
            )
        else:
            silent = produced is None
        outcomes.append(
            CaseOutcome(
                raw=case.raw,
                coercer=case.coercer,
                expectation=case.expectation,
                produced=_describe(produced),
                silent_loss=silent,
                why=case.why,
            )
        )
    return outcomes


def run_round_trip() -> dict[str, object]:
    """Serialise clean values the way a CSV would, then coerce them back."""
    import pandas as pd

    from evalgate.corpus.generator import generate

    coercers = _coercers()
    total_cells = 0
    preserved_cells = 0
    total_rows = 0
    intact_rows = 0
    per_dataset: dict[str, dict[str, float]] = {}

    for dataset_id in ROUND_TRIP_ARCHETYPES:
        try:
            frame = generate(dataset_id, rows=200)
        except Exception:  # noqa: BLE001 - a missing archetype must not abort the gate
            continue
        numeric_columns = [
            column for column in frame.columns
            if pd.api.types.is_numeric_dtype(frame[column])
            and not pd.api.types.is_bool_dtype(frame[column])
        ]
        if not numeric_columns:
            continue
        dataset_cells = dataset_preserved = 0
        dataset_rows = dataset_intact = 0
        for _, row in frame[numeric_columns].iterrows():
            dataset_rows += 1
            row_ok = True
            for column in numeric_columns:
                original = row[column]
                if original is None or (isinstance(original, float) and math.isnan(original)):
                    continue
                dataset_cells += 1
                coercer = "to_int" if float(original).is_integer() and abs(original) < 2**53 else "to_float"
                restored = coercers[coercer](str(original))
                ok = restored is not None and math.isclose(
                    float(restored), float(original), rel_tol=1e-9, abs_tol=1e-9
                )
                if ok:
                    dataset_preserved += 1
                else:
                    row_ok = False
            if row_ok:
                dataset_intact += 1
        total_cells += dataset_cells
        preserved_cells += dataset_preserved
        total_rows += dataset_rows
        intact_rows += dataset_intact
        per_dataset[dataset_id] = {
            "cells": dataset_cells,
            "cell_fidelity": round(dataset_preserved / dataset_cells * 100.0, 4) if dataset_cells else 100.0,
            "row_fidelity": round(dataset_intact / dataset_rows * 100.0, 4) if dataset_rows else 100.0,
        }

    return {
        "cell_fidelity": (preserved_cells / total_cells * 100.0) if total_cells else 100.0,
        "row_fidelity": (intact_rows / total_rows * 100.0) if total_rows else 100.0,
        "cells_checked": total_cells,
        "rows_checked": total_rows,
        "per_dataset": per_dataset,
    }


def evaluate(*, write_evidence: bool = True) -> EvalResult:
    try:
        matrix = run_malformed_matrix()
    except Exception as exc:  # noqa: BLE001
        return EvalResult(
            gate=GATE,
            evaluator=EVALUATOR,
            status=EvalStatus.BLOCKED_MISSING_CREDENTIAL,
            metadata={"reason": f"cannot import the ingest coercers: {exc}"},
        )

    try:
        round_trip = run_round_trip()
    except Exception as exc:  # noqa: BLE001
        round_trip = {
            "cell_fidelity": None, "row_fidelity": None,
            "cells_checked": 0, "rows_checked": 0, "per_dataset": {},
            "error": f"{type(exc).__name__}: {exc}",
        }

    silent_losses = [o for o in matrix if o.silent_loss]
    rejected_cases = [o for o in matrix if o.expectation == REJECT]
    signalled = len(rejected_cases) - len([o for o in silent_losses if o.expectation == REJECT])
    signal_rate = signalled / len(rejected_cases) if rejected_cases else 1.0

    # Every produced None is indistinguishable from a genuinely empty cell, so the
    # ambiguity rate is the share of malformed inputs that end up looking empty.
    null_ambiguity = len([o for o in silent_losses if o.expectation == REJECT]) / len(rejected_cases) \
        if rejected_cases else 0.0

    row_fidelity = round_trip["row_fidelity"]
    cell_fidelity = round_trip["cell_fidelity"]

    evidence: list[Evidence] = []
    if write_evidence:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        target = EVIDENCE_DIR / "ingest_fidelity.json"
        target.write_text(
            json.dumps(
                {"malformed_matrix": [asdict(o) for o in matrix], "round_trip": round_trip},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        evidence.append(Evidence(type="file", path=str(target.relative_to(PROJECT_ROOT))))

    findings: list[Finding] = []
    lost = [o for o in silent_losses if o.expectation == REJECT]
    if lost:
        findings.append(
            Finding(
                id="HG-D1",
                severity=Severity.CRITICAL,
                title="Ingestion converts unparseable values into indistinguishable nulls",
                detail=(
                    f"{len(lost)}/{len(rejected_cases)} malformed inputs were accepted without a "
                    f"signal. Examples: "
                    + ", ".join(f"{o.coercer}({o.raw!r}) -> {o.produced}" for o in lost[:4])
                ),
                root_cause_hint=(
                    "src/worker.py to_float/to_int catch every exception and return None, so a "
                    "conversion failure is stored as an empty cell; profiling later measures the "
                    "resulting null rate as if it came from the source"
                ),
                evidence_ref="evalgate/evidence/gate4/ingest_fidelity.json",
                blocks_release=True,
            )
        )

    score_parts = [norm.ratio(signal_rate)]
    if cell_fidelity is not None:
        score_parts.append(cell_fidelity)
    score = sum(score_parts) / len(score_parts)

    metrics = {
        "coercion_signal_rate": MetricValue(
            raw=round(signal_rate, 4), unit="ratio", normalized=norm.ratio(signal_rate)
        ),
        "coercion_loss_count": MetricValue(
            raw=len(lost), unit="count", normalized=norm.zero_tolerance(len(lost))
        ),
        "null_ambiguity_rate": MetricValue(
            raw=round(null_ambiguity, 4), unit="ratio",
            normalized=norm.inverse_ratio(null_ambiguity),
        ),
    }
    if row_fidelity is not None:
        metrics["row_fidelity"] = MetricValue(
            raw=round(row_fidelity, 4), unit="ratio", normalized=row_fidelity
        )
        metrics["cell_fidelity"] = MetricValue(
            raw=round(cell_fidelity, 4), unit="ratio", normalized=cell_fidelity
        )

    return EvalResult(
        gate=GATE,
        evaluator=EVALUATOR,
        status=EvalStatus.FAIL if findings else EvalStatus.PASS,
        score=score,
        metrics=metrics,
        thresholds={
            "row_fidelity": Threshold(**{"pass": 100.0, "warn": 100.0}),
            "cell_fidelity": Threshold(**{"pass": 100.0, "warn": 99.9}),
            "coercion_loss_count": Threshold(**{"pass": 0.0, "warn": 0.0}),
        },
        evidence=evidence,
        critical_findings=findings,
        metadata={
            "round_trip_datasets": list(round_trip.get("per_dataset", {})),
            "cells_checked": round_trip.get("cells_checked"),
            "note": (
                "measured by driving src/worker.py's own coercers; no upload endpoint "
                "is required and no database is touched"
            ),
        },
    )
