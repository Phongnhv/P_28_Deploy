"""Validated, expiring exceptions for the merge-gate ratchet."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from evalgate.aggregator import AggregateOutcome, Decision, load_policy
from evalgate.schemas.eval_result import EvalResult

NON_SUPPRESSIBLE = frozenset({"HG-S2", "HG-S3", "HG-S6", "HG-S7", "HG-D1", "HG-D2"})


class Suppression(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^SUP-[A-Za-z0-9_-]+$")
    finding_id: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    ticket: str = Field(min_length=1)
    reason: str = Field(min_length=8)
    created_at: date
    expires_at: date
    baseline_git_sha: str = Field(pattern=r"^[0-9a-f]{7,40}$")


class SuppressionResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: list[Suppression] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


def load_suppressions(path: Path, *, today: date | None = None) -> SuppressionResolution:
    today = today or datetime.now(UTC).date()
    if not path.exists():
        return SuppressionResolution()
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    except (OSError, yaml.YAMLError) as exc:
        return SuppressionResolution(errors=[f"cannot read suppression policy: {exc}"])
    if not isinstance(document, list):
        return SuppressionResolution(errors=["suppression policy must be a YAML list"])

    active: list[Suppression] = []
    errors: list[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(document):
        try:
            item = Suppression.model_validate(raw)
        except ValidationError as exc:
            errors.append(f"entry {index}: {exc}")
            continue
        if item.id in seen:
            errors.append(f"duplicate suppression id: {item.id}")
        seen.add(item.id)
        if item.finding_id in NON_SUPPRESSIBLE:
            errors.append(f"{item.id}: {item.finding_id} is non-suppressible")
        elif item.expires_at < today:
            errors.append(f"{item.id}: expired on {item.expires_at.isoformat()}")
        elif item.created_at > item.expires_at:
            errors.append(f"{item.id}: created_at is after expires_at")
        else:
            active.append(item)
    return SuppressionResolution(active=active, errors=errors)


def apply_suppressions(
    outcome: AggregateOutcome,
    results: list[EvalResult],
    resolution: SuppressionResolution,
    *,
    current_git_sha: str,
) -> None:
    """Apply an auditable ratchet without altering any measurement."""
    if resolution.errors:
        outcome.decision = Decision.EVALGATE_INVALID
        outcome.override_reason = "; ".join(resolution.errors)
        return

    failing_ids = {gate.id for gate in outcome.hard_gates if gate.status == "FAIL"}
    failing_ids.update(
        finding.id
        for result in results
        for finding in result.critical_findings
        if finding.blocks_release
    )
    suppressible: set[str] = set()
    for item in resolution.active:
        if not (
            current_git_sha.startswith(item.baseline_git_sha)
            or item.baseline_git_sha.startswith(current_git_sha)
        ):
            continue
        if item.finding_id in failing_ids:
            suppressible.add(item.finding_id)

    outcome.suppressed_findings = sorted(suppressible)
    outcome.unsuppressed_findings = sorted(failing_ids - suppressible)
    central_policy = load_policy("evaluation_policy")
    measured_floor = float(central_policy["minimum_measured_weight"])
    bands = central_policy["score"]["decision_bands"]
    if (
        outcome.decision == Decision.RELEASE_BLOCKED
        and not outcome.unsuppressed_findings
        and outcome.measured_weight >= measured_floor
        and outcome.score is not None
    ):
        if outcome.score >= bands["pass"]:
            outcome.decision = Decision.PASS
        elif outcome.score >= bands["warning"]:
            outcome.decision = Decision.WARNING
        else:
            outcome.decision = Decision.FAIL
