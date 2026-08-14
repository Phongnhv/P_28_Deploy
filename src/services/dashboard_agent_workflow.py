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


class AgentWorkflowError(ValueError):
    """An expected, redacted failure returned by the product workflow."""


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

        return {
            "source_rows": {
                "table": "source_rows",
                "rows": self.row_count,
                "sample": {"rate": 0.0, "n": 0},
                "columns": digest_columns,
                "cross_column_hints": hints,
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

    raw_rules = _invoke_dashboard_proposal_graph(evidence)
    proposals = _normalise_graph_rules(raw_rules, evidence)
    if not 2 <= len(proposals) <= 5:
        raise AgentWorkflowError("The proposal agent did not return enough valid, evidence-backed rules.")
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
    allowed_columns = {column.name: column for column in evidence.columns}
    accepted: list[DashboardProposal] = []
    identities: set[str] = set()

    for raw in raw_rules:
        proposal = _normalise_graph_rule(raw, evidence, allowed_columns)
        if not proposal:
            continue
        identity = f"{proposal.rule_type}:{proposal.rule_spec}"
        if identity in identities:
            continue
        identities.add(identity)
        accepted.append(proposal)
        if len(accepted) == 5:
            break
    return accepted


def _normalise_graph_rule(
    raw: dict[str, Any], evidence: ProposalEvidence, allowed_columns: dict[str, ProposalColumnEvidence]
) -> DashboardProposal | None:
    rule_type = str(raw.get("rule_type", "")).upper()
    column = raw.get("column")
    parameters = raw.get("parameters") or {}
    if not isinstance(column, str) or column not in allowed_columns:
        return None
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

    profile_key = f"profile.column.{column}"
    title = description[:120]
    model_name = f"langgraph-{get_settings().llm_provider}"

    if rule_type == "NOT_NULL":
        spec = {"type": "not_null", "column": column}
        refs = [f"{profile_key}.null_rate"]
    elif rule_type == "RANGE":
        spec = _normalise_range(column, parameters, allowed_columns[column])
        if not spec:
            return None
        refs = [f"{profile_key}.min_value", f"{profile_key}.max_value"]
    elif rule_type == "ACCEPTED_VALUES":
        values = parameters.get("accepted_values")
        if column != "payment_type" or values != PAYMENT_TYPE_VALUES:
            return None
        spec = {"type": "accepted_values", "column": column, "allowed_values": PAYMENT_TYPE_VALUES}
        refs = [f"{profile_key}.distinct_count"]
    elif rule_type == "CROSS_FIELD_COMPARISON":
        target = parameters.get("target_column")
        operator = parameters.get("operator")
        if not isinstance(target, str) or target not in allowed_columns or operator not in SAFE_OPERATORS:
            return None
        spec = {"type": "cross_field_comparison", "columns": [column, target], "operator": operator}
        refs = [f"{profile_key}.data_type", f"profile.column.{target}.data_type"]
    elif rule_type == "UNIQUE":
        if allowed_columns[column].distinct_count != evidence.row_count:
            return None
        spec = {"type": "duplicate_fingerprint", "fingerprint_columns": [column]}
        refs = ["profile.row_count", f"{profile_key}.distinct_count"]
    else:
        return None

    if not set(refs).issubset(evidence.evidence_keys):
        return None
    return DashboardProposal(
        id=f"proposal-{uuid.uuid4().hex}",
        title=title,
        description=description,
        severity=severity,
        rule_type=spec["type"],
        rule_spec=spec,
        evidence_refs=refs,
        evidence_summary=_safe_evidence_summary(evidence, refs),
        confidence=confidence,
        model_name=model_name,
    )


def _normalise_range(
    column: str, parameters: dict[str, Any], evidence: ProposalColumnEvidence
) -> dict[str, Any] | None:
    if evidence.min_value is None or evidence.max_value is None:
        return None
    minimum = _finite_float(parameters.get("min"))
    maximum = _finite_float(parameters.get("max"))
    if minimum is None and maximum is None:
        return None
    if minimum is not None and minimum > evidence.max_value:
        return None
    if maximum is not None and maximum < evidence.min_value:
        return None
    if minimum is not None and maximum is not None and minimum > maximum:
        return None
    spec: dict[str, Any] = {"type": "numeric_range", "column": column}
    if minimum is not None:
        spec["min_value"] = minimum
    if maximum is not None:
        spec["max_value"] = maximum
    return spec


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
