"""Every raw metric becomes a 0..100 score through exactly one normalizer.

Keeping normalisation in one place is what makes it safe to add a metric later:
the aggregator never sees a raw unit, so no metric can accidentally be summed on
the wrong scale.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

_MIN = 0.0
_MAX = 100.0


def _clamp(value: float) -> float:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return _MIN
    return max(_MIN, min(_MAX, float(value)))


def ratio(value: float | None) -> float | None:
    """Higher is better: recall, F1, fidelity, coverage."""
    if value is None:
        return None
    return _clamp(value * 100.0)


def inverse_ratio(value: float | None) -> float | None:
    """Lower is better: error rate, false-positive rate, violation rate."""
    if value is None:
        return None
    return _clamp((1.0 - value) * 100.0)


def variance(stdev: float | None, *, factor: float = 200.0) -> float | None:
    """Lower spread is better.

    Used for ``generalization_variance``: a stdev of 0.15 across datasets already
    costs 30 points, which is the intended pressure for a "works on any dataset"
    product.
    """
    if stdev is None:
        return None
    return _clamp(100.0 - stdev * factor)


def latency_band(milliseconds: float | None) -> float | None:
    if milliseconds is None:
        return None
    if milliseconds <= 1000:
        return 100.0
    if milliseconds <= 3000:
        return 70.0
    if milliseconds <= 10000:
        return 30.0
    return 0.0


def budget(usd: float | None, *, budget_usd: float) -> float | None:
    if usd is None:
        return None
    if budget_usd <= 0:
        return 0.0
    return _clamp((1.0 - usd / budget_usd) * 100.0)


_SEVERITY_SCORES = {
    "CRITICAL": 0.0,
    "HIGH": 25.0,
    "MEDIUM": 60.0,
    "LOW": 85.0,
    "NONE": 100.0,
}


def severity(worst: str | None) -> float | None:
    if worst is None:
        return None
    return _SEVERITY_SCORES.get(str(worst).upper(), 0.0)


def boolean(value: bool | None) -> float | None:
    if value is None:
        return None
    return 100.0 if value else 0.0


def zero_tolerance(violation_count: int | None) -> float | None:
    """No interpolation: one CRITICAL violation is as bad as many."""
    if violation_count is None:
        return None
    return 100.0 if int(violation_count) == 0 else 0.0


def psi_band(psi: float | None) -> float | None:
    if psi is None:
        return None
    if psi < 0.1:
        return 100.0
    if psi < 0.25:
        return 60.0
    return 0.0


def time_band(seconds: float | None) -> float | None:
    """Time-to-first-value: the product metric for an upload-anything tool."""
    if seconds is None:
        return None
    minutes = seconds / 60.0
    if minutes <= 5:
        return 100.0
    if minutes <= 15:
        return 70.0
    if minutes <= 60:
        return 40.0
    return 0.0


def percentile(values: Sequence[float], q: float) -> float | None:
    """Linear-interpolated percentile without a numpy dependency."""
    cleaned = sorted(v for v in values if v is not None)
    if not cleaned:
        return None
    if len(cleaned) == 1:
        return float(cleaned[0])
    position = (len(cleaned) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return float(cleaned[int(position)])
    return float(cleaned[low] + (cleaned[high] - cleaned[low]) * (position - low))


def stdev(values: Sequence[float]) -> float | None:
    cleaned = [v for v in values if v is not None]
    if len(cleaned) < 2:
        return None
    mean = sum(cleaned) / len(cleaned)
    return math.sqrt(sum((v - mean) ** 2 for v in cleaned) / len(cleaned))


NORMALIZERS = {
    "ratio": ratio,
    "inverse_ratio": inverse_ratio,
    "variance": variance,
    "latency_band": latency_band,
    "budget": budget,
    "severity": severity,
    "boolean": boolean,
    "zero_tolerance": zero_tolerance,
    "psi_band": psi_band,
    "time_band": time_band,
}
