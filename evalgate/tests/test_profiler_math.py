"""The synthetic profiler fixtures, kept as tests rather than as an evaluator.

These assertions used to live inside ``profile_accuracy_probe_v1`` and contributed 100
points to the ``input_data`` gate on every run. They are worth keeping -- a profiler that
miscounts nulls on a controlled fixture is broken -- but they are a statement about
``db_profiler_tool``, not about the dataset any particular run ingested, so they belong to
EvalGate's own test suite. The evaluator now recomputes the bundle's published profile
from the bundle's own input frame.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import NamedTemporaryFile

import pandas as pd
import pytest
from sqlalchemy import create_engine

prof = pytest.importorskip("src.agents.tools.db_profiler_tool")


@pytest.fixture(scope="module")
def controlled_profile() -> dict:
    """Profile of a 100-row table: 10 nulls in `val`, min 30.0, max 208.0, 5 categories."""
    handle = NamedTemporaryFile(suffix=".db", delete=False)
    handle.close()
    db_path = Path(handle.name)
    try:
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        frame = pd.DataFrame(
            [
                {
                    "id": i,
                    "val": (float(i * 2 + 10) if i >= 10 else None),
                    "cat": f"cat_{i % 5}",
                    "ts": "2026-01-01 12:00:00",
                }
                for i in range(100)
            ]
        )
        frame.to_sql("test_table", engine, index=False)
        engine.dispose()
        raw = prof.profile_database.invoke(
            {"connection_string": f"sqlite:///{db_path.as_posix()}", "table_name": "test_table"}
        )
        yield json.loads(raw)
    finally:
        try:
            db_path.unlink()
        except OSError:
            pass


def test_null_rate_is_exact(controlled_profile: dict) -> None:
    val = controlled_profile["columns"]["val"]
    assert val["null_count"] == 10
    assert abs(float(val["null_pct"]) - 0.10) < 0.01


def test_min_and_max_are_exact(controlled_profile: dict) -> None:
    val = controlled_profile["columns"]["val"]
    assert float(val["min"]) == 30.0
    assert float(val["max"]) == 208.0


def test_distinct_count_is_exact(controlled_profile: dict) -> None:
    cat = controlled_profile["columns"]["cat"]
    assert 5 in {
        int(cat.get("distinct_full_table", 0)),
        int(cat.get("distinct_in_sample", 0)),
    }


def test_a_parseable_timestamp_yields_a_positive_gap() -> None:
    iso, gap = prof._parse_and_calculate_freshness("2026-01-01 12:00:00")
    assert iso is not None and "2026-01-01" in iso
    assert gap is not None and gap > 0


def test_a_missing_timestamp_is_not_invented() -> None:
    iso, gap = prof._parse_and_calculate_freshness(None)
    assert iso is None and gap is None
