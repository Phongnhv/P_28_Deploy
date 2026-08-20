"""A small, self-contained profiler used only by SDIH.

SDIH deliberately does not import ``src.agents.tools.db_profiler_tool``: it must
be able to profile a plain DataFrame before that data has ever reached the
product, and it must keep working if the product's profiler changes.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

_CATEGORICAL_MAX_DISTINCT = 20
_KEY_CANDIDATE_MIN_UNIQUENESS = 0.99

_START_TOKENS = ("start", "pickup", "begin", "created", "open", "from")
_END_TOKENS = ("end", "dropoff", "finish", "closed", "completed", "to")


def profile_dataframe(df: pd.DataFrame) -> dict[str, Any]:
    """Return the aggregate profile SDIH needs to choose injection targets."""
    row_count = int(len(df))
    columns: dict[str, dict[str, Any]] = {}

    for name in df.columns:
        series = df[name]
        non_null = series.dropna()
        # bool is numeric to pandas but has no meaningful quantile/sign semantics.
        is_bool = bool(pd.api.types.is_bool_dtype(series))
        is_numeric = bool(pd.api.types.is_numeric_dtype(series)) and not is_bool
        is_datetime = bool(pd.api.types.is_datetime64_any_dtype(series))
        is_text = bool(
            pd.api.types.is_string_dtype(series) or pd.api.types.is_object_dtype(series)
        ) and not is_numeric and not is_datetime

        distinct_count = int(non_null.nunique())
        entry: dict[str, Any] = {
            "name": str(name),
            "dtype": str(series.dtype),
            "is_numeric": is_numeric,
            "is_datetime": is_datetime,
            "is_bool": is_bool,
            "is_text": is_text,
            "null_rate": float(series.isna().mean()) if row_count else 0.0,
            "distinct_count": distinct_count,
            "uniqueness_rate": (distinct_count / row_count) if row_count else 0.0,
            "is_categorical": bool(
                (not is_numeric or distinct_count <= _CATEGORICAL_MAX_DISTINCT)
                and 0 < distinct_count <= _CATEGORICAL_MAX_DISTINCT
            ),
        }

        if is_numeric and len(non_null):
            entry.update(
                {
                    "min": float(non_null.min()),
                    "max": float(non_null.max()),
                    "mean": float(non_null.mean()),
                    "p05": float(non_null.quantile(0.05)),
                    "p25": float(non_null.quantile(0.25)),
                    "p50": float(non_null.quantile(0.50)),
                    "p75": float(non_null.quantile(0.75)),
                    "p95": float(non_null.quantile(0.95)),
                }
            )
        if is_datetime and len(non_null):
            entry["min"] = non_null.min().isoformat()
            entry["max"] = non_null.max().isoformat()
        if is_text and len(non_null):
            lengths = non_null.astype(str).str.len()
            entry["length_stats"] = {
                "min": int(lengths.min()),
                "max": int(lengths.max()),
                "avg": round(float(lengths.mean()), 2),
            }
        if entry["is_categorical"] and len(non_null):
            entry["domain"] = sorted({str(v) for v in non_null.unique()})

        columns[str(name)] = entry

    return {
        "row_count": row_count,
        "columns": columns,
        "key_candidates": _key_candidates(columns),
        "ordered_pairs": _ordered_pairs(columns),
    }


def _key_candidates(columns: dict[str, dict[str, Any]]) -> list[str]:
    return [
        name
        for name, column in columns.items()
        if column["uniqueness_rate"] >= _KEY_CANDIDATE_MIN_UNIQUENESS
        and column["null_rate"] == 0.0
    ]


def _ordered_pairs(columns: dict[str, dict[str, Any]]) -> list[tuple[str, str]]:
    """Heuristically pair start/end datetime columns for cross-field defects."""
    datetimes = [name for name, column in columns.items() if column["is_datetime"]]
    pairs: list[tuple[str, str]] = []
    for start in datetimes:
        lowered = start.lower()
        if not any(token in lowered for token in _START_TOKENS):
            continue
        for end in datetimes:
            if end == start:
                continue
            if any(token in end.lower() for token in _END_TOKENS):
                pairs.append((start, end))
                break
    if not pairs and len(datetimes) >= 2:
        pairs.append((datetimes[0], datetimes[1]))
    return pairs
