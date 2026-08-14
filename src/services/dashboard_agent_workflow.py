"""Safe bridge between the dashboard workflow and the proposal LangGraph.

The dashboard owns the public API and persistence models.  This module supplies the
only permitted path from its persisted aggregate profile to the LangGraph proposer:
it creates a narrow evidence payload, validates the graph response and returns
dashboard-shaped typed rules.  It never accepts browser prompts, raw rows, SQL or
connection strings.
"""

from __future__ import annotations

import asyncio
import math
import threading
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from src.config import get_settings
from src.models.database import ColumnProfileModel, DatasetModel, ProfileModel

SUPPORTED_RULE_TYPES = {
    "not_null",
    "numeric_range",
    "accepted_values",
    "cross_field_comparison",
    "duplicate_fingerprint",
}
SAFE_OPERATORS = {"<", "<=", ">", ">=", "==", "!="}
PAYMENT_TYPE_VALUES = ["1", "2", "3", "4", "5", "6"]
GOVERNED_CODE_SETS = {"payment_type": PAYMENT_TYPE_VALUES}
NONNEGATIVE_FIELD_TOKENS = (
    "amount",
    "count",
    "distance",
    "fare",
    "fee",
    "passenger",
    "surcharge",
    "tax",
    "tip",
    "toll",
)
IDENTIFIER_PREFERENCE = ("trip_id", "record_id", "vendor_id", "id")


class AgentWorkflowError(ValueError):
    """An expected, redacted failure returned by the product workflow."""


@dataclass(frozen=True)
class DashboardRuleCandidate:
    """A deterministic, evidence-backed rule that the dashboard agent may select."""

    id: str
    rule_type: str
    column: str
    parameters: dict[str, Any]
    dashboard_rule_type: str
    rule_spec: dict[str, Any]
    evidence_refs: list[str]
    selection_reason: str
    priority: int

    def to_prompt_requirement(self) -> dict[str, Any]:
        """Render only policy and aggregate evidence for the structured proposer."""
        return {
            "candidate_id": self.id,
            "column": self.column,
            "rule_type": self.rule_type,
            "parameters": self.parameters,
            "dimension": _dimension_for_rule_type(self.rule_type),
            "evidence": self.evidence_refs,
            "selection_reason": self.selection_reason,
        }


class ProposalColumnEvidence(BaseModel):
    """Aggregate-only column evidence exposed to the proposal agent."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    data_type: str = Field(min_length=1, max_length=64)
    null_rate: float = Field(ge=0.0, le=1.0)
    distinct_count: int = Field(ge=0)
    min_value: float | None = None
    max_value: float | None = None


class ProposalEvidence(BaseModel):
    """Allow-listed evidence.  Deliberately excludes samples and raw identifiers."""

    model_config = ConfigDict(extra="forbid")

    dataset_id: str = Field(min_length=1, max_length=256)
    manifest_version: str = Field(min_length=1, max_length=64)
    row_count: int = Field(ge=1)
    completeness_score: float = Field(ge=0.0, le=100.0)
    validity_score: float = Field(ge=0.0, le=100.0)
    duplicate_rate: float = Field(ge=0.0, le=100.0)
    evidence_keys: list[str]
    columns: list[ProposalColumnEvidence] = Field(min_length=1, max_length=64)

    def to_agent_digest(self) -> dict[str, dict[str, Any]]:
        """Render the minimal LangGraph digest without raw rows or sample values."""
        digest_columns: list[dict[str, Any]] = []
        for column in self.columns:
            role = _column_role(column.name, column.data_type)
            item: dict[str, Any] = {
                "name": column.name,
                "type": column.data_type,
                "role": role,
                "null_pct": round(column.null_rate * 100, 4),
                "signals": ["no_nulls"] if column.null_rate == 0 else [],
            }
            if column.min_value is not None or column.max_value is not None:
                item["range"] = [column.min_value, column.max_value]
                if column.min_value is not None and column.min_value < 0:
                    item["signals"].append("has_negative_values")
            digest_columns.append(item)

        hints: list[dict[str, Any]] = []
        column_names = {column.name for column in self.columns}
        if {"pickup_at", "dropoff_at"}.issubset(column_names):
            hints.append({"type": "datetime_order", "columns": ["pickup_at", "dropoff_at"]})

        candidates = _build_dashboard_rule_candidates(self)

        return {
            "source_rows": {
                "table": "source_rows",
                "rows": self.row_count,
                "sample": {"rate": 0.0, "n": 0},
                "columns": digest_columns,
                "cross_column_hints": hints,
                # This switches the legacy proposer into the public dashboard
                # contract: choose and explain candidates, never invent a rule.
                "dashboard_candidate_mode": True,
                "dashboard_rule_candidates": [candidate.to_prompt_requirement() for candidate in candidates],
            }
        }


@dataclass(frozen=True)
class DashboardProposal:
    id: str
    title: str
    description: str
    severity: str
    rule_type: str
    rule_spec: dict[str, Any]
    evidence_refs: list[str]
    evidence_summary: str
    confidence: float
    model_name: str


def build_proposal_evidence(db: Session, dataset_id: str) -> ProposalEvidence:
    """Build the only payload that may be passed to the proposal graph."""
    dataset = db.query(DatasetModel).filter(DatasetModel.id == dataset_id).first()
    profile = db.query(ProfileModel).filter(ProfileModel.dataset_id == dataset_id).first()
    columns = (
        db.query(ColumnProfileModel)
        .filter(ColumnProfileModel.profile_dataset_id == dataset_id)
        .order_by(ColumnProfileModel.name)
        .all()
    )
    if not dataset or dataset.status != "PROFILE_READY" or not profile or not columns:
        raise AgentWorkflowError("A completed aggregate profile is required before requesting proposals.")

    safe_columns = [
        ProposalColumnEvidence(
            name=column.name,
            data_type=column.data_type,
            null_rate=column.null_rate,
            distinct_count=column.distinct_count,
            min_value=column.min_value,
            max_value=column.max_value,
        )
        for column in columns
        if column.name != "source_row_id"
    ]
    if not safe_columns:
        raise AgentWorkflowError("The completed profile has no eligible columns for proposal generation.")

    evidence_keys = ["profile.row_count", "profile.completeness_score", "profile.validity_score", "profile.duplicate_rate"]
    for column in safe_columns:
        prefix = f"profile.column.{column.name}"
        evidence_keys.extend([f"{prefix}.null_rate", f"{prefix}.distinct_count", f"{prefix}.data_type"])
        if column.min_value is not None:
            evidence_keys.append(f"{prefix}.min_value")
        if column.max_value is not None:
            evidence_keys.append(f"{prefix}.max_value")

    return ProposalEvidence(
        dataset_id=dataset_id,
        manifest_version=dataset.manifest_version,
        row_count=profile.row_count,
        completeness_score=profile.completeness_score,
        validity_score=profile.validity_score,
        duplicate_rate=profile.duplicate_rate,
        evidence_keys=evidence_keys,
        columns=safe_columns,
    )


def generate_dashboard_proposals(db: Session, dataset_id: str) -> list[DashboardProposal]:
    """Return two to five validated proposals in the configured local agent mode."""
    evidence = build_proposal_evidence(db, dataset_id)
    settings = get_settings()
    if settings.agent_mode == "mock":
        return _mock_proposals(evidence)

    if len(_build_dashboard_rule_candidates(evidence)) < 2:
        raise AgentWorkflowError("The aggregate profile has fewer than two evidence-backed dashboard candidates.")

    raw_rules = _invoke_dashboard_proposal_graph(evidence)
    proposals = _normalise_graph_rules(raw_rules, evidence)
    if not proposals:
        raise AgentWorkflowError("The proposal agent did not return enough valid, evidence-backed rules.")
    proposals = _complete_with_policy_candidates(proposals, evidence)
    if not 2 <= len(proposals) <= 5:
        raise AgentWorkflowError("The proposal workflow could not form a valid dashboard rule set.")
    return proposals


def _invoke_dashboard_proposal_graph(evidence: ProposalEvidence) -> list[dict[str, Any]]:
    """Run only the structured proposer node with the safe persisted-profile digest."""
    from src.agents.graph import build_dashboard_proposal_graph

    async def invoke() -> list[dict[str, Any]]:
        graph = build_dashboard_proposal_graph()
        result = await graph.ainvoke(
            {
                "dataset_id": evidence.dataset_id,
                "rule_run_id": f"dashboard-proposal-{uuid.uuid4().hex}",
                "dataset_profile_digest": evidence.to_agent_digest(),
                "metadata": {
                    "workflow": "dashboard",
                    "evidence_source": "persisted_aggregate_profile",
                    "max_retries": 0,
                },
            }
        )
        errors = result.get("rule_proposal_errors", [])
        if errors:
            raise AgentWorkflowError("The proposal agent could not produce a valid structured response.")
        return result.get("proposed_rules", [])

    return _run_coroutine_safely(invoke())


def _run_coroutine_safely(coroutine):
    """Run async LangGraph from a FastAPI task or an async test without loop nesting."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)

    outcome: dict[str, Any] = {}

    def runner() -> None:
        try:
            outcome["value"] = asyncio.run(coroutine)
        except BaseException as exc:  # re-raised in the caller thread below
            outcome["error"] = exc

    thread = threading.Thread(target=runner, name="dashboard-proposal-graph")
    thread.start()
    thread.join()
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


def _normalise_graph_rules(raw_rules: list[dict[str, Any]], evidence: ProposalEvidence) -> list[DashboardProposal]:
    candidates = _build_dashboard_rule_candidates(evidence)
    accepted: list[tuple[DashboardProposal, DashboardRuleCandidate]] = []
    candidate_ids: set[str] = set()
    rule_types: set[str] = set()

    for raw in raw_rules:
        matched_candidate = _match_dashboard_candidate(raw, candidates, evidence)
        if not matched_candidate:
            continue
        proposal = _normalise_graph_rule(raw, evidence, matched_candidate)
        if not proposal:
            continue
        if matched_candidate.id in candidate_ids or proposal.rule_type in rule_types:
            continue
        candidate_ids.add(matched_candidate.id)
        rule_types.add(proposal.rule_type)
        accepted.append((proposal, matched_candidate))
        if len(accepted) == 5:
            break
    # A small, deterministic policy weight breaks confidence ties while the model
    # still chooses which candidates to return and supplies the steward-facing text.
    accepted.sort(key=lambda item: (item[0].confidence, item[1].priority), reverse=True)
    return [proposal for proposal, _candidate in accepted]


def _normalise_graph_rule(
    raw: dict[str, Any], evidence: ProposalEvidence, candidate: DashboardRuleCandidate
) -> DashboardProposal | None:
    confidence = _finite_float(raw.get("confidence_score"))
    if confidence is None or not 0.0 <= confidence <= 1.0:
        return None
    severity = str(raw.get("severity", "MEDIUM")).upper()
    if severity not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
        return None
    description = str(raw.get("rule_description", "")).strip()
    reasoning = str(raw.get("ai_reasoning", "")).strip()
    if not 1 <= len(description) <= 500 or len(reasoning) > 1_000:
        return None

    title = description[:120]
    model_name = f"langgraph-{get_settings().llm_provider}"
    if not set(candidate.evidence_refs).issubset(evidence.evidence_keys):
        return None
    return DashboardProposal(
        id=f"proposal-{uuid.uuid4().hex}",
        title=title,
        description=description,
        severity=severity,
        rule_type=candidate.dashboard_rule_type,
        rule_spec=candidate.rule_spec,
        evidence_refs=candidate.evidence_refs,
        evidence_summary=_safe_evidence_summary(evidence, candidate.evidence_refs),
        confidence=confidence,
        model_name=model_name,
    )


def _build_dashboard_rule_candidates(evidence: ProposalEvidence) -> list[DashboardRuleCandidate]:
    """Create a small, diverse candidate set from safe aggregate evidence.

    This is deliberately conservative: it does not infer business constraints from
    a zero null rate alone, and it does not permit the model to invent thresholds.
    """
    columns = {column.name: column for column in evidence.columns}
    candidates: list[DashboardRuleCandidate] = []

    identifier_names = sorted(
        (
            name
            for name, column in columns.items()
            if column.null_rate <= 0.01 and (name == "id" or name.endswith("_id"))
        ),
        key=lambda name: (
            IDENTIFIER_PREFERENCE.index(name) if name in IDENTIFIER_PREFERENCE else len(IDENTIFIER_PREFERENCE),
            name,
        ),
    )
    if identifier_names:
        column = identifier_names[0]
        candidates.append(
            DashboardRuleCandidate(
                id=f"not-null:{column}", rule_type="NOT_NULL", column=column, parameters={},
                dashboard_rule_type="not_null", rule_spec={"type": "not_null", "column": column},
                evidence_refs=[f"profile.column.{column}.null_rate"],
                selection_reason="A required identifier has a stable complete aggregate profile.", priority=90,
            )
        )

    for column in sorted(columns.values(), key=lambda item: item.name):
        if (
            column.min_value is not None
            and column.min_value < 0
            and any(token in column.name.lower() for token in NONNEGATIVE_FIELD_TOKENS)
        ):
            candidates.append(
                DashboardRuleCandidate(
                    id=f"nonnegative:{column.name}", rule_type="RANGE", column=column.name,
                    parameters={"min": 0.0}, dashboard_rule_type="numeric_range",
                    rule_spec={"type": "numeric_range", "column": column.name, "min_value": 0.0},
                    evidence_refs=[f"profile.column.{column.name}.min_value", f"profile.column.{column.name}.max_value"],
                    selection_reason="The aggregate minimum is negative for a semantically non-negative measure.", priority=100,
                )
            )
            break

    for column, allowed_values in GOVERNED_CODE_SETS.items():
        profile = columns.get(column)
        if profile and 0 < profile.distinct_count <= len(allowed_values):
            candidates.append(
                DashboardRuleCandidate(
                    id=f"governed-enum:{column}", rule_type="ACCEPTED_VALUES", column=column,
                    parameters={"accepted_values": allowed_values}, dashboard_rule_type="accepted_values",
                    rule_spec={"type": "accepted_values", "column": column, "allowed_values": allowed_values},
                    evidence_refs=[f"profile.column.{column}.distinct_count"],
                    selection_reason="A governed code set is available and the aggregate cardinality is compatible with it.", priority=80,
                )
            )

    if {"pickup_at", "dropoff_at"}.issubset(columns):
        candidates.append(
            DashboardRuleCandidate(
                id="datetime-order:pickup_at:dropoff_at", rule_type="CROSS_FIELD_COMPARISON", column="pickup_at",
                parameters={"target_column": "dropoff_at", "operator": "<="},
                dashboard_rule_type="cross_field_comparison",
                rule_spec={"type": "cross_field_comparison", "columns": ["pickup_at", "dropoff_at"], "operator": "<="},
                evidence_refs=["profile.column.pickup_at.data_type", "profile.column.dropoff_at.data_type"],
                selection_reason="The dashboard has an explicit pickup-to-dropoff temporal ordering relationship.", priority=95,
            )
        )
    return candidates


def _match_dashboard_candidate(
    raw: dict[str, Any], candidates: list[DashboardRuleCandidate], evidence: ProposalEvidence
) -> DashboardRuleCandidate | None:
    """Return the exact deterministic candidate represented by a structured LLM rule."""
    rule_type = str(raw.get("rule_type", "")).upper()
    column = raw.get("column")
    parameters = raw.get("parameters") or {}
    if not isinstance(column, str) or not isinstance(parameters, dict):
        return None
    for candidate in candidates:
        if (
            candidate.rule_type == rule_type
            and candidate.column == column
            and _candidate_parameters_match(parameters, candidate, evidence)
        ):
            return candidate
    return None


def _candidate_parameters_match(
    parameters: dict[str, Any], candidate: DashboardRuleCandidate, evidence: ProposalEvidence
) -> bool:
    if parameters == candidate.parameters:
        return True
    # Some models repeat the observed maximum when asked for a non-negative rule.
    # Accept that faithful restatement only, then persist the canonical lower-bound
    # policy from the candidate.  Any padded or invented upper bound stays rejected.
    if candidate.rule_type != "RANGE" or set(parameters) - {"min", "max"}:
        return False
    if _finite_float(parameters.get("min")) != _finite_float(candidate.parameters.get("min")):
        return False
    observed_max = next((column.max_value for column in evidence.columns if column.name == candidate.column), None)
    return observed_max is not None and _finite_float(parameters.get("max")) == observed_max


def _dimension_for_rule_type(rule_type: str) -> str:
    return {
        "NOT_NULL": "COMPLETENESS",
        "RANGE": "VALIDITY",
        "ACCEPTED_VALUES": "VALIDITY",
        "CROSS_FIELD_COMPARISON": "CONSISTENCY",
    }.get(rule_type, "VALIDITY")


def _complete_with_policy_candidates(
    proposals: list[DashboardProposal], evidence: ProposalEvidence
) -> list[DashboardProposal]:
    """Fill omissions from a verified candidate set without inventing a rule.

    Small structured-output models can occasionally omit a checklist item despite a
    valid schema.  The fallback is deliberately unavailable when *no* model rule
    validates, which keeps provider/schema failures visible instead of masking them.
    """
    completed = list(proposals)
    present_types = {proposal.rule_type for proposal in proposals}
    for candidate in _build_dashboard_rule_candidates(evidence):
        if candidate.dashboard_rule_type in present_types:
            continue
        completed.append(
            DashboardProposal(
                id=f"proposal-{uuid.uuid4().hex}",
                title=_policy_title(candidate),
                description=_policy_description(candidate),
                severity=_policy_severity(candidate),
                rule_type=candidate.dashboard_rule_type,
                rule_spec=candidate.rule_spec,
                evidence_refs=candidate.evidence_refs,
                evidence_summary=_safe_evidence_summary(evidence, candidate.evidence_refs),
                confidence=0.75,
                model_name="agent-policy-fallback-v1",
            )
        )
        present_types.add(candidate.dashboard_rule_type)
    return completed


def _policy_title(candidate: DashboardRuleCandidate) -> str:
    titles = {
        "NOT_NULL": f"{candidate.column} must be populated",
        "RANGE": f"{candidate.column} must be non-negative",
        "ACCEPTED_VALUES": f"{candidate.column} must use governed values",
        "CROSS_FIELD_COMPARISON": "Pickup time must not follow dropoff time",
    }
    return titles[candidate.rule_type]


def _policy_description(candidate: DashboardRuleCandidate) -> str:
    descriptions = {
        "NOT_NULL": f"Require a value for the required identifier {candidate.column}.",
        "RANGE": f"Reject negative values in the non-negative measure {candidate.column}.",
        "ACCEPTED_VALUES": f"Validate {candidate.column} against its governed code set.",
        "CROSS_FIELD_COMPARISON": "Require pickup_at to be earlier than or equal to dropoff_at.",
    }
    return descriptions[candidate.rule_type]


def _policy_severity(candidate: DashboardRuleCandidate) -> str:
    return {
        "NOT_NULL": "HIGH",
        "RANGE": "HIGH",
        "ACCEPTED_VALUES": "MEDIUM",
        "CROSS_FIELD_COMPARISON": "CRITICAL",
    }[candidate.rule_type]


def _mock_proposals(evidence: ProposalEvidence) -> list[DashboardProposal]:
    """Explicit offline mode for deterministic UI and automated tests."""
    available = {column.name for column in evidence.columns}
    candidates = [
        ("proposal-not-null", "Vendor ID must not be null", "Ensure vendor_id is populated.", "HIGH", "not_null", {"type": "not_null", "column": "vendor_id"}, ["profile.column.vendor_id.null_rate"], 1.0),
        ("proposal-range", "Trip distance must be non-negative", "Flag negative trip_distance values.", "HIGH", "numeric_range", {"type": "numeric_range", "column": "trip_distance", "min_value": 0.0}, ["profile.column.trip_distance.min_value", "profile.column.trip_distance.max_value"], 0.95),
        ("proposal-accepted-values", "Payment type must be valid", "Check payment_type against the documented code set.", "MEDIUM", "accepted_values", {"type": "accepted_values", "column": "payment_type", "allowed_values": PAYMENT_TYPE_VALUES}, ["profile.column.payment_type.distinct_count"], 0.9),
        ("proposal-cross-field", "Pickup time must be before dropoff time", "Check pickup_at is not later than dropoff_at.", "CRITICAL", "cross_field_comparison", {"type": "cross_field_comparison", "columns": ["pickup_at", "dropoff_at"], "operator": "<="}, ["profile.column.pickup_at.data_type", "profile.column.dropoff_at.data_type"], 0.98),
        ("proposal-duplicate-fingerprint", "Duplicate fingerprint detection", "Check the documented trip fingerprint for duplicate rows.", "MEDIUM", "duplicate_fingerprint", {"type": "duplicate_fingerprint", "fingerprint_columns": ["vendor_id", "pickup_at", "passenger_count"]}, ["profile.duplicate_rate"], 0.85),
    ]
    result: list[DashboardProposal] = []
    for proposal_id, title, description, severity, rule_type, spec, refs, confidence in candidates:
        identifiers = set(spec.get("fingerprint_columns") or spec.get("columns") or [spec.get("column")])
        identifiers.discard(None)
        if not identifiers.issubset(available) or not set(refs).issubset(evidence.evidence_keys):
            continue
        result.append(
            DashboardProposal(
                id=proposal_id,
                title=title,
                description=description,
                severity=severity,
                rule_type=rule_type,
                rule_spec=spec,
                evidence_refs=refs,
                evidence_summary=_safe_evidence_summary(evidence, refs),
                confidence=confidence,
                model_name="agent-mock-v1",
            )
        )
    if not 2 <= len(result) <= 5:
        raise AgentWorkflowError("The completed profile does not contain enough supported fields for mock proposals.")
    return result


def _safe_evidence_summary(evidence: ProposalEvidence, refs: list[str]) -> str:
    """Human-readable aggregate summary; never echo values from source rows."""
    return f"Aggregate persisted profile evidence: {', '.join(refs)} (rows: {evidence.row_count})."


def _column_role(name: str, data_type: str) -> str:
    data_type = data_type.lower()
    if "time" in name or "date" in name or "time" in data_type or "date" in data_type:
        return "datetime"
    if any(token in data_type for token in ("int", "float", "real", "numeric", "decimal")):
        return "numeric"
    if name.endswith("_id"):
        return "categorical"
    return "generic"


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
