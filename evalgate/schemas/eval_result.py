"""Standard evaluation result contract for EvalGate.

Every evaluator, regardless of gate, returns an ``EvalResult``.  The contract is
deliberately closed (``extra="forbid"``) so an adapter that drifts from the shape
fails loudly instead of silently contributing a wrong number to the final score.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

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
    STALE_EVIDENCE = "STALE_EVIDENCE"
    EVALUATOR_ERROR = "EVALUATOR_ERROR"
    MISSING_MANDATORY_EVIDENCE = "MISSING_MANDATORY_EVIDENCE"


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
        EvalStatus.STALE_EVIDENCE,
        EvalStatus.EVALUATOR_ERROR,
        EvalStatus.MISSING_MANDATORY_EVIDENCE,
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
    """Per-dataset result. Mandatory whenever an evaluator runs over a corpus.

    ``kind`` decides how the aggregator collapses the list, and getting it wrong
    silently discards an evaluator's score. ``dataset`` rows are collapsed with P25,
    which is right when each row is an independent dataset: it pulls the summary
    toward the bad tail so six healthy datasets cannot hide the seventh.

    That is the wrong operator for a list of pass/fail *cases*. Every row is 100 or 0,
    so P25 returns 0 the moment more than a quarter of them fail -- golden_conformance
    published a case pass rate of 0.60 and contributed 0.00 to its gate, and
    policy_resolution reported PASS with every metric at 100.0 and also contributed
    0.00. Neither number reached the score it was measured for.

    ``case`` rows are therefore collapsed as a pass rate, which is what a pass rate
    already is.
    """

    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    #: ``dataset`` -> collapse with P25. ``case`` -> collapse as a pass rate.
    kind: Literal["dataset", "case"] = "dataset"
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
    #: Run this result was compared against. Only set by evaluators that make a
    #: statement about change over time; a regression claim without it is unfalsifiable.
    baseline_run_id: str | None = None
    sdih_seed: int | None = None
    timestamp: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    evaluation_schema_version: str = "2.0"
    policy_version: str = "1.0"
    corpus_version: str = "1.0"
    normalizer_version: str = "1.0"

    def counts_toward_aggregate(self) -> bool:
        return self.status not in EXCLUDED_FROM_AGGREGATE
