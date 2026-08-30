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
    # --- Tier 2: expectations about the rule that was proposed -----------------
    "rule_proposed",        # a rule of this type exists on this column
    "rule_not_on_columns",  # this rule type must NOT target these columns
    "enum_from_policy",     # ACCEPTED_VALUES must respect the governed domain
    "parameter_bound",      # a numeric parameter must satisfy a bound
    "no_rules_on_tables",   # no proposal may target these tables at all
    "min_violations",       # execution must have flagged at least N rows
    # --- Tier 3: expectations about the language the model produced ------------
    "forbidden_tokens",     # a field must not contain these substrings
    "must_cite_numbers",    # a field must contain at least one numeral
]


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


class GoldenSuite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    dataset_id: str
    description: str = ""
    cases: list[GoldenCase]


def load_suite(path: Path) -> GoldenSuite:
    return GoldenSuite.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_all_suites(root: Path = GOLDEN_ROOT) -> list[tuple[Path, GoldenSuite]]:
    """Every ``*.cases.yaml`` under the golden root, sorted for stable reporting."""
    suites: list[tuple[Path, GoldenSuite]] = []
    for path in sorted(root.rglob("*.cases.yaml")):
        suites.append((path, load_suite(path)))
    return suites


def load_manifest(root: Path = GOLDEN_ROOT) -> dict[str, Any]:
    target = root / "manifest.yaml"
    if not target.exists():
        return {}
    return yaml.safe_load(target.read_text(encoding="utf-8")) or {}
