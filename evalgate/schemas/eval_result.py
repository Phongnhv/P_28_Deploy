"""Standard evaluation result contract for EvalGate.

Every evaluator, regardless of gate, returns an ``EvalResult``.  The contract is
deliberately closed (``extra="forbid"``) so an adapter that drifts from the shape
fails loudly instead of silently contributing a wrong number to the final score.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EvalStatus(StrEnum):
    """Outcome of an evaluator or of a single metric.

    The ``NOT_*`` family is excluded from the aggregate and triggers a weight
    re-normalisation.  ``BLOCKED_BY_SYSTEM_CAPABILITY`` is different in kind: the
    evaluator works, the *product* cannot yet be measured.  It stays visible in the
    report as a product gap rather than disappearing from it.
    """

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    NOT_MEASURED = "NOT_MEASURED"
    NOT_EXECUTED = "NOT_EXECUTED"
    BLOCKED_MISSING_CREDENTIAL = "BLOCKED_MISSING_CREDENTIAL"
    BLOCKED_MISSING_GROUND_TRUTH = "BLOCKED_MISSING_GROUND_TRUTH"
    BLOCKED_BY_SYSTEM_CAPABILITY = "BLOCKED_BY_SYSTEM_CAPABILITY"


#: Statuses that are dropped from the aggregate, forcing a weight re-normalisation.
EXCLUDED_FROM_AGGREGATE: frozenset[EvalStatus] = frozenset(
    {
        EvalStatus.NOT_APPLICABLE,
        EvalStatus.NOT_IMPLEMENTED,
        EvalStatus.NOT_MEASURED,
        EvalStatus.NOT_EXECUTED,
        EvalStatus.BLOCKED_MISSING_CREDENTIAL,
        EvalStatus.BLOCKED_MISSING_GROUND_TRUTH,
        EvalStatus.BLOCKED_BY_SYSTEM_CAPABILITY,
    }
)


class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NONE = "NONE"


class MetricValue(BaseModel):
    """A single measurement.

    ``raw`` and ``normalized`` are kept apart on purpose: metrics of different
    scales must never be summed before normalisation.
    """

    model_config = ConfigDict(extra="forbid")

    raw: float | int | bool | None = None
    unit: str = Field(description="ratio | count | ms | usd | stdev | psi | boolean | seconds")
    normalized: float | None = Field(default=None, ge=0.0, le=100.0)
    status: EvalStatus | None = None
    note: str | None = None


class Threshold(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    pass_: float | None = Field(default=None, alias="pass")
    warn: float | None = None
    hard_gate_floor: float | None = None


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(description="file | trace | query | command")
    path: str | None = None
    url: str | None = None
    detail: str | None = None


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    severity: Severity
    title: str
    detail: str
    root_cause_hint: str | None = None
    evidence_ref: str | None = None
    blocks_release: bool = False


class DatasetBreakdown(BaseModel):
    """Per-dataset result. Mandatory whenever an evaluator runs over a corpus."""

    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    status: EvalStatus
    score: float | None = None
    reason: str | None = None
    metrics: dict[str, float | None] = Field(default_factory=dict)
    recall_by_class: dict[str, float] = Field(default_factory=dict)
    applicable_classes: list[str] = Field(default_factory=list)


class CostRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm_usd: float = 0.0
    llm_tokens: int = 0
    wall_clock_seconds: float = 0.0


class EvalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gate: str
    evaluator: str
    evaluator_version: str = "1.0.0"
    score: float | None = Field(default=None, ge=0.0, le=100.0)
    status: EvalStatus
    metrics: dict[str, MetricValue] = Field(default_factory=dict)
    per_dataset_breakdown: list[DatasetBreakdown] = Field(default_factory=list)
    thresholds: dict[str, Threshold] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)
    critical_findings: list[Finding] = Field(default_factory=list)
    cost: CostRecord = Field(default_factory=CostRecord)
    run_id: str | None = None
    git_ref: str | None = None
    sdih_seed: int | None = None
    timestamp: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def counts_toward_aggregate(self) -> bool:
        return self.status not in EXCLUDED_FROM_AGGREGATE
