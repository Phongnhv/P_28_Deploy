"""The golden case format, and the assertions a case may make.

A golden case is a written-down expectation about what the agent should produce for
a situation whose correct answer is known in advance.  SDIH already answers *"did
the agent find the defect?"*; these cases answer the questions SDIH structurally
cannot:

  did it propose the right **kind** of rule, on the right column?
  did the threshold come from policy, or from the data it is supposed to judge?
  did it obey the constraints its own system prompt imposes?

Every assertion here is deterministic.  That is a design choice rather than a
limitation: a case whose outcome depends on a model call cannot be used as a
regression baseline, because the baseline itself would drift.  The one genuinely
subjective dimension -- is the explanation *good* -- is left to a separate
LLM-judge evaluator that is not part of the release gate.

The vocabulary is deliberately small.  Six assertion types cover every expectation
the project has actually written down; adding a seventh should require showing
that none of the six can express it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

GOLDEN_ROOT = Path(__file__).resolve().parent

AssertionType = Literal[
    # --- INTERPRETATION: did the agent understand what the column means? -------
    # The first decision the agent makes, and the one every later expectation
    # hangs off. A currency column read as plain "numeric" loses its non-negative
    # invariant, and the resulting bad threshold looks like a rule-proposal defect
    # while belonging to dataset_understanding.
    "semantic_type_is",       # the semantic contract assigns this type
    "nullable_expected_is",   # ... and this nullability
    "relationship_declared",  # an ordering/comparison relationship was recognised
    # --- PROCESS: how the agent decided, not just what it decided --------------
    # The deep agent can dry-run a candidate against the real data before proposing
    # it. Whether it did is a property of the reasoning, invisible in the output: a
    # rule that was verified and one that was guessed look identical once written
    # down. Read from the tool lifecycle in the run trace.
    "tools_were_used",            # the agent consulted the data at all
    "must_verify_before_asserting",  # ... using a tool that actually checks a claim
    # --- EVIDENCE: is the decision supported, or merely asserted? ---------------
    # The product already forces every rule to cite at least one reference
    # (rule_schemas.ProposedRule.selected_evidence_refs, min_length=1), so the
    # question worth asking is not "did it cite" but "does the citation resolve to
    # a figure that actually exists, and does it name the metric that decides the
    # threshold".
    "evidence_metric_exists",      # every cited ref resolves to a real profile metric
    "evidence_references_metric",  # the citation names one of the deciding metrics
    # --- Tier 2 / DECISION: expectations about the rule that was proposed -------
    "rule_proposed",        # a rule of this type exists on this column
    "rule_not_on_columns",  # this rule type must NOT target these columns
    "enum_from_policy",     # ACCEPTED_VALUES must respect the governed domain
    "parameter_bound",      # a numeric parameter must satisfy a bound
    "no_rules_on_tables",   # no proposal may target these tables at all
    "min_violations",       # execution must have flagged at least N rows
    "severity_ranks_above",  # ordinal, never absolute -- see the note below
    "confidence_monotonic",  # calibration: confident answers are not worse answers
    # --- NEGATIVE SPACE: what the agent must NOT do ----------------------------
    # Measurable even when the agent produced no finding at all, which is exactly
    # when a false-positive or abstention failure matters most.
    "max_false_positive_rate",  # clean rows flagged, against SDIH's known-clean set
    "must_abstain",             # INSUFFICIENT_HISTORY is the only honest answer here
    # --- Tier 3: expectations about the language the model produced ------------
    "forbidden_tokens",     # a field must not contain these substrings
    "must_cite_numbers",    # a field must contain at least one numeral
]

Layer = Literal["interpretation", "process", "evidence", "decision", "negative_space"]

#: Which decision surface each assertion belongs to.
#:
#: The order of LAYER_ORDER is causal, not cosmetic: interpretation feeds evidence
#: selection, which feeds the decision, and the negative space is judged against the
#: decision that was made. Attribution walks it to blame the earliest failing layer
#: rather than reporting one root cause four times.
LAYER_ORDER: tuple[Layer, ...] = (
    "interpretation", "process", "evidence", "decision", "negative_space",
)

LAYER_OF: dict[str, Layer] = {
    "semantic_type_is": "interpretation",
    "nullable_expected_is": "interpretation",
    "relationship_declared": "interpretation",
    "tools_were_used": "process",
    "must_verify_before_asserting": "process",
    "evidence_metric_exists": "evidence",
    "evidence_references_metric": "evidence",
    "rule_proposed": "decision",
    "rule_not_on_columns": "decision",
    "enum_from_policy": "decision",
    "parameter_bound": "decision",
    "no_rules_on_tables": "decision",
    "min_violations": "decision",
    "severity_ranks_above": "decision",
    "confidence_monotonic": "decision",
    "forbidden_tokens": "decision",
    "must_cite_numbers": "decision",
    "max_false_positive_rate": "negative_space",
    "must_abstain": "negative_space",
}


class Applicability(BaseModel):
    """Which datasets and columns a case is a statement about.

    Without this a case is silently a statement about every dataset. Three of the
    nine cases that existed before this field were bound to NYC column names, and
    the runner ran them against whatever bundle it was given: a missing
    ``fare_amount`` produced a FAIL rather than a NOT_APPLICABLE, so a clinical
    dataset was penalised for not being a taxi dataset. The other direction was
    worse -- ``rule_not_on_columns`` and ``forbidden_tokens`` passed vacuously and
    quietly raised the score.

    Selectors are resolved against the bundle's own semantic contract and profile,
    so a case says "every currency column" rather than naming one.
    """

    model_config = ConfigDict(extra="forbid")

    #: Bind to one dataset. For ground truth that genuinely cannot generalise, such
    #: as a governed enum published in a specific dataset's contract document.
    dataset_id: str | None = None
    #: Bind to meaning instead of to a name. Resolved through the semantic contract.
    semantic_type: str | None = None
    business_role: str | None = None
    #: Bind to a declared relationship, e.g. an ordered timestamp pair.
    relationship: str | None = None
    #: Bind to literal column names. Kept for platform-owned columns like
    #: ``source_row_id`` that the product creates on every dataset.
    columns: list[str] = Field(default_factory=list)
    #: True for platform invariants that hold on every run regardless of schema.
    always: bool = False

    @property
    def is_unscoped(self) -> bool:
        return not (
            self.dataset_id
            or self.semantic_type
            or self.business_role
            or self.relationship
            or self.columns
        )


class Assertion(BaseModel):
    """One checkable expectation.

    Fields are optional because each assertion type uses a different subset; the
    evaluator validates the combination it needs and reports a case as malformed
    rather than silently passing when a required field is missing.
    """

    model_config = ConfigDict(extra="forbid")

    type: AssertionType
    column: str | None = None
    rule_type: str | None = None
    columns: list[str] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    must_exclude: list[str] = Field(default_factory=list)
    parameter: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    rule_suffix: str | None = None
    at_least: int | None = None
    field: str | None = None
    tokens: list[str] = Field(default_factory=list)
    note: str | None = None
    # --- interpretation ---
    semantic_type: str | None = None
    business_role: str | None = None
    nullable_expected: bool | None = None
    operator: str | None = None
    # --- evidence ---
    metrics: list[str] = Field(default_factory=list)
    # --- decision: ordinal severity and calibration ---
    #: Severity is asserted as an ordering, never as an absolute label. "This rule
    #: ought to be HIGH" is an opinion, and an opinion must not block a release;
    #: "a missing primary key outranks a formatting nit" is an invariant.
    ranks_above: list[str] = Field(default_factory=list)
    #: Calibration is asserted as monotonicity, not as a target error. With a few
    #: dozen cases an absolute calibration figure is noise, but "the high-confidence
    #: group is not less accurate than the low-confidence group" is checkable and is
    #: the property a steward actually relies on when triaging a review queue.
    confidence_field: str | None = None
    # --- process ---
    #: Tools that count as verification rather than mere lookup.
    verifying_tools: list[str] = Field(default_factory=list)
    min_calls: int | None = None
    # --- negative space ---
    max_rate: float | None = None
    max_history_runs: int | None = None

    @property
    def layer(self) -> Layer:
        return LAYER_OF[self.type]


class GoldenCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    tier: Literal[2, 3]
    intent: str = Field(min_length=1, description="What a reader should learn from this case")
    #: Where the expectation comes from. A case with no source is an opinion, and an
    #: opinion must not be able to block a release.
    source: str = Field(min_length=1)
    ground_truth_owner: str = Field(min_length=1)
    severity: Literal["CRITICAL", "HIGH", "MEDIUM"] = "HIGH"
    assertions: list[Assertion] = Field(min_length=1)
    #: Omitted means "every run", which is correct only for platform invariants.
    #: A case naming dataset-specific columns and leaving this empty is the defect
    #: this field exists to make visible, so the suite loader warns about it.
    applies_to: Applicability = Field(default_factory=Applicability)

    @property
    def layers(self) -> list[Layer]:
        present = {assertion.layer for assertion in self.assertions}
        return [layer for layer in LAYER_ORDER if layer in present]


class GoldenSuite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    #: Optional since v2. Platform and semantic suites are statements about the
    #: product rather than about one dataset, and forcing a dataset id on them was
    #: what made every case look NYC-bound. Where present it is a default for the
    #: cases in the suite, and it is now actually enforced by the runner.
    dataset_id: str | None = None
    layer: Literal["platform", "semantic", "dataset"] | None = None
    description: str = ""
    cases: list[GoldenCase]

    def resolved_cases(self) -> list[GoldenCase]:
        """Cases with the suite's dataset id pushed down where a case omits one."""
        if not self.dataset_id:
            return self.cases
        resolved: list[GoldenCase] = []
        for case in self.cases:
            # Inherit only when the case says nothing about scope. A case that
            # already names its own columns has opted out of the suite's dataset:
            # GC-E5 is about `source_row_id`, a key the product creates on every
            # dataset, and pushing the suite's corpus id onto it would confine a
            # platform invariant to one taxi fixture.
            if not case.applies_to.is_unscoped or case.applies_to.always:
                resolved.append(case)
                continue
            merged = case.applies_to.model_copy(update={"dataset_id": self.dataset_id})
            resolved.append(case.model_copy(update={"applies_to": merged}))
        return resolved


def load_suite(path: Path) -> GoldenSuite:
    return GoldenSuite.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_all_suites(root: Path = GOLDEN_ROOT) -> list[tuple[Path, GoldenSuite]]:
    """Every ``*.cases.yaml`` under the golden root, sorted for stable reporting."""
    suites: list[tuple[Path, GoldenSuite]] = []
    for path in sorted(root.rglob("*.cases.yaml")):
        suites.append((path, load_suite(path)))
    return suites


def load_all_cases(root: Path = GOLDEN_ROOT) -> list[tuple[Path, GoldenCase]]:
    """Every case, paired with its suite path and carrying resolved applicability."""
    return [
        (path, case)
        for path, suite in load_all_suites(root)
        for case in suite.resolved_cases()
    ]


def load_manifest(root: Path = GOLDEN_ROOT) -> dict[str, Any]:
    target = root / "manifest.yaml"
    if not target.exists():
        return {}
    return yaml.safe_load(target.read_text(encoding="utf-8")) or {}
