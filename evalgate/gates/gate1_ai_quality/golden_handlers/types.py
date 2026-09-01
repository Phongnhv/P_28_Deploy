"""Data structures for golden assertion execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from evalgate.golden.applicability import DatasetContext, Scope


@dataclass
class AssertionOutcome:
    type: str
    passed: bool
    observed: str
    #: False when the artefacts contain nothing this assertion could inspect. An
    #: unmeasurable assertion must not count as a failure -- "we did not look" and
    #: "we looked and it was wrong" are different claims, and conflating them makes
    #: the pass rate meaningless.
    measurable: bool = True


@dataclass
class CaseOutcome:
    id: str
    tier: int
    severity: str
    intent: str
    source: str
    passed: bool
    assertions: list[AssertionOutcome]
    measurable: bool = True
    #: False when the case is not a statement about this dataset at all. Distinct
    #: from ``measurable``: "there is no currency column here" is not the same claim
    #: as "there was nothing in the artefacts to inspect", and neither is a failure.
    applicable: bool = True
    applicability_reason: str = ""
    #: Which decision surface failed first. A wrong semantic type produces a wrong
    #: candidate, a wrong rule and a wrong finding, and reporting all four sends a
    #: reader to fix rule_proposer for a defect owned by dataset_understanding.
    failed_layer: str | None = None


@dataclass
class HandlerContext:
    """Everything an assertion can be evaluated against."""

    rules: list[dict[str, Any]]
    results: list[dict[str, Any]]
    scope: Scope
    dataset: DatasetContext | None
    #: Graph 3's own output. A second decision surface with its own ground truth,
    #: and the only place an abstention can be observed.
    anomaly: dict[str, Any] = field(default_factory=dict)
    #: Tool lifecycle events from the run trace. The only record of *how* the agent
    #: decided; a verified rule and a guessed one are identical once written down.
    tool_events: list[dict[str, Any]] = field(default_factory=list)
