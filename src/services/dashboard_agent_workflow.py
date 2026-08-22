"""Safe bridge between the dashboard workflow and the proposal LangGraph.

The dashboard owns the public API and persistence models.  This module supplies the
only permitted path from its persisted aggregate profile to the LangGraph proposer:
it creates a narrow evidence payload, validates the graph response and returns
dashboard-shaped typed rules.  It never accepts browser prompts, raw rows, SQL or
connection strings.
"""

from __future__ import annotations

import asyncio
import json
import math
import threading
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

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
RULE_POLICY_PATH = Path(__file__).resolve().parents[1] / "resources" / "rule_policies.json"


class AgentWorkflowError(ValueError):
    """An expected, redacted failure returned by the product workflow."""


class CrossFieldRulePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    left_column: str = Field(min_length=1, max_length=128)
    operator: Literal["<", "<=", ">", ">=", "==", "!="]
    right_column: str = Field(min_length=1, max_length=128)


class DatasetRulePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required_identifiers: list[str] = Field(default_factory=list)
    nonnegative_columns: list[str] = Field(default_factory=list)
    governed_value_sets: dict[str, list[str]] = Field(default_factory=dict)
    cross_field_rules: list[CrossFieldRulePolicy] = Field(default_factory=list)
    duplicate_fingerprint_columns: list[str] = Field(default_factory=list)


class RulePolicyDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    datasets: dict[str, DatasetRulePolicy]


@lru_cache(maxsize=1)
def _load_rule_policy_document() -> RulePolicyDocument:
    try:
        return RulePolicyDocument.model_validate_json(RULE_POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AgentWorkflowError("The dataset rule policy is missing or invalid.") from exc


def infer_dataset_rule_policy(columns: list[Any]) -> DatasetRulePolicy:
    req_ids = []
    non_negs = []
    gov_sets: dict[str, list[str]] = {}

    for c in columns:
        name = c.name if hasattr(c, "name") else c.get("name", "")
        if name == "source_row_id":
            continue
        name_lower = name.lower()
        if name_lower in ("id", "vendor_id", "trip_id") or name_lower.endswith("_id") or name_lower.endswith(" id"):
            req_ids.append(name)
            break
    if not req_ids:
        for c in columns:
            name = c.name if hasattr(c, "name") else c.get("name", "")
            null_rate = getattr(c, "null_rate", 0)
            if null_rate == 0 and name != "source_row_id":
                req_ids.append(name)
                break
    if not req_ids and columns:
        name0 = columns[0].name if hasattr(columns[0], "name") else columns[0].get("name", "")
        req_ids.append(name0)

    for c in columns:
        name = c.name if hasattr(c, "name") else c.get("name", "")
        if name in req_ids:
            continue
        dtype = getattr(c, "data_type", "")
        min_val = getattr(c, "min_value", None)
        if dtype in ("numeric", "float", "integer", "int", "real", "double") and min_val is not None and min_val >= 0:
            non_negs.append(name)
            if len(non_negs) >= 3:
                break
    if not non_negs:
        for c in columns:
            name = c.name if hasattr(c, "name") else c.get("name", "")
            dtype = getattr(c, "data_type", "")
            min_val = getattr(c, "min_value", None)
            if dtype in ("numeric", "float", "integer", "int", "real", "double") and min_val is not None:
                non_negs.append(name)
                break

    return DatasetRulePolicy(
        required_identifiers=req_ids,
        nonnegative_columns=non_negs,
        governed_value_sets=gov_sets,
        cross_field_rules=[],
        duplicate_fingerprint_columns=req_ids[:3],
    )


def get_dataset_rule_policy(dataset_id: str, columns: list[Any] | None = None) -> DatasetRulePolicy | None:
    doc = _load_rule_policy_document()
    policy = doc.datasets.get(dataset_id)
    if policy is not None:
        return policy
    if columns:
        return infer_dataset_rule_policy(columns)
    return doc.datasets.get("dataset-nyc-yellow-taxi-50k")



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
    title: str
    description: str
    severity: str
    confidence_ceiling: float

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
    non_null_count: int | None = Field(default=None, ge=0)
    negative_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    quantiles: dict[str, float] = Field(default_factory=dict)
    out_of_domain_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    full_distinct_count: int | None = Field(default=None, ge=0)
    uniqueness_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    is_unique_full_table: bool | None = None
    min_value: float | None = None
    max_value: float | None = None


class ProposalCrossFieldEvidence(BaseModel):
    """Aggregate violation metric for one configured relationship."""

    model_config = ConfigDict(extra="forbid")

    left_column: str = Field(min_length=1, max_length=128)
    operator: Literal["<", "<=", ">", ">=", "==", "!="]
    right_column: str = Field(min_length=1, max_length=128)
    checked_count: int = Field(ge=0)
    violation_count: int = Field(ge=0)
    violation_rate: float = Field(ge=0.0, le=1.0)


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
    cross_field_metrics: list[ProposalCrossFieldEvidence] = Field(default_factory=list)

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
                "distinct_count": column.distinct_count,
                "signals": ["no_nulls"] if column.null_rate == 0 else [],
            }
            if column.min_value is not None or column.max_value is not None:
                item["range"] = [column.min_value, column.max_value]
                if column.negative_rate is not None:
                    item["negative_pct"] = round(column.negative_rate * 100, 4)
                if column.negative_rate and column.negative_rate > 0:
                    item["signals"].append("has_negative_values")
            if column.quantiles:
                item["quantiles"] = column.quantiles
                if column.quantiles.get("p05") is not None and column.quantiles.get("p95") is not None:
                    item["typical_range"] = [column.quantiles["p05"], column.quantiles["p95"]]
            if column.out_of_domain_rate is not None:
                item["out_of_domain_pct"] = round(column.out_of_domain_rate * 100, 4)
                if column.out_of_domain_rate > 0:
                    item["signals"].append("has_out_of_domain_values")
            if column.full_distinct_count is not None:
                item["full_distinct_count"] = column.full_distinct_count
                item["uniqueness_pct"] = round((column.uniqueness_rate or 0.0) * 100, 4)
                if column.is_unique_full_table:
                    item["signals"].append("unique_full_table")
            digest_columns.append(item)

        hints = [metric.model_dump() for metric in self.cross_field_metrics]

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
    rule_name: str
    business_rationale: str
    proposal_basis: str
    evidence: dict[str, Any]
    confidence_breakdown: dict[str, Any]


def _parse_json_dict(raw: str | None) -> dict[str, float]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _parse_json_list(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


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
            non_null_count=column.non_null_count,
            negative_rate=column.negative_rate,
            quantiles=_parse_json_dict(column.quantiles_json),
            out_of_domain_rate=column.out_of_domain_rate,
            full_distinct_count=column.full_distinct_count,
            uniqueness_rate=column.uniqueness_rate,
            is_unique_full_table=column.is_unique_full_table,
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
        if column.non_null_count is not None:
            evidence_keys.append(f"{prefix}.non_null_count")
        if column.negative_rate is not None:
            evidence_keys.append(f"{prefix}.negative_rate")
        evidence_keys.extend(f"{prefix}.quantile.{name}" for name in column.quantiles)
        if column.out_of_domain_rate is not None:
            evidence_keys.append(f"{prefix}.out_of_domain_rate")
        if column.full_distinct_count is not None:
            evidence_keys.extend(
                [
                    f"{prefix}.full_distinct_count",
                    f"{prefix}.uniqueness_rate",
                    f"{prefix}.is_unique_full_table",
                ]
            )

    cross_field_metrics = [
        ProposalCrossFieldEvidence.model_validate(item)
        for item in _parse_json_list(profile.cross_field_metrics_json)
    ]
    evidence_keys.extend(
        f"profile.cross_field.{metric.left_column}.{metric.operator}.{metric.right_column}.violation_rate"
        for metric in cross_field_metrics
    )

    policy = get_dataset_rule_policy(dataset_id, safe_columns)
    if policy:
        evidence_keys.extend(f"policy.required_identifier.{column}" for column in policy.required_identifiers)
        evidence_keys.extend(f"policy.nonnegative_column.{column}" for column in policy.nonnegative_columns)
        evidence_keys.extend(
            f"policy.governed_value_set.{column}" for column in policy.governed_value_sets
        )
        evidence_keys.extend(
            f"policy.cross_field.{rule.left_column}.{rule.operator}.{rule.right_column}"
            for rule in policy.cross_field_rules
        )
        if policy.duplicate_fingerprint_columns:
            evidence_keys.append("policy.duplicate_fingerprint")

    return ProposalEvidence(
        dataset_id=dataset_id,
        manifest_version=dataset.manifest_version,
        row_count=profile.row_count,
        completeness_score=profile.completeness_score,
        validity_score=profile.validity_score,
        duplicate_rate=profile.duplicate_rate,
        evidence_keys=list(dict.fromkeys(evidence_keys)),
        columns=safe_columns,
        cross_field_metrics=cross_field_metrics,
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
    # The model chooses candidates; server policy owns stable display order.
    accepted.sort(key=lambda item: item[1].priority, reverse=True)
    return [proposal for proposal, _candidate in accepted]


def _normalise_graph_rule(
    raw: dict[str, Any], evidence: ProposalEvidence, candidate: DashboardRuleCandidate
) -> DashboardProposal | None:
    confidence_payload = raw.get("confidence") or {}
    confidence = _finite_float(confidence_payload.get("overall", raw.get("confidence_score")))
    if confidence is None or not 0.0 <= confidence <= 1.0:
        return None
    severity = str(raw.get("severity", "MEDIUM")).upper()
    if severity not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
        return None
    description = str(raw.get("rule_description", "")).strip()
    reasoning = str(raw.get("ai_reasoning", "")).strip()
    if not 1 <= len(description) <= 500 or not 1 <= len(reasoning) <= 1_000:
        return None

    model_name = f"langgraph-{get_settings().llm_provider}"
    if not set(candidate.evidence_refs).issubset(evidence.evidence_keys):
        return None
    selected_refs = raw.get("selected_evidence_refs") or candidate.evidence_refs
    if not set(selected_refs).issubset(candidate.evidence_refs):
        return None
    capped_confidence = min(confidence, candidate.confidence_ceiling)
    normalized_breakdown = dict(confidence_payload or {
        "overall": capped_confidence,
        "evidence_strength": capped_confidence,
        "business_support": capped_confidence,
        "sample_representativeness": 1.0,
        "explanation": "Dashboard candidate confidence ceiling",
    })
    normalized_breakdown["overall"] = capped_confidence
    return DashboardProposal(
        id=f"proposal-{uuid.uuid4().hex}",
        title=candidate.title,
        description=candidate.description,
        severity=candidate.severity,
        rule_type=candidate.dashboard_rule_type,
        rule_spec=candidate.rule_spec,
        evidence_refs=candidate.evidence_refs,
        evidence_summary=(
            f"{_safe_evidence_summary(evidence, candidate.evidence_refs)} "
            f"Selection basis: {candidate.selection_reason}"
        ),
        confidence=capped_confidence,
        model_name=model_name,
        rule_name=str(raw.get("rule_name") or candidate.title),
        business_rationale=str(raw.get("business_rationale") or candidate.description),
        proposal_basis=str(raw.get("proposal_basis") or "MIXED"),
        evidence={
            "sample_row_count": evidence.row_count,
            "sample_rate": 1.0,
            "sampling_caveat": None,
            "observed_metrics": {},
            "source_refs": candidate.evidence_refs,
        },
        confidence_breakdown=normalized_breakdown,
    )


def _build_dashboard_rule_candidates(evidence: ProposalEvidence) -> list[DashboardRuleCandidate]:
    """Create a small, diverse candidate set from safe aggregate evidence.

    This is deliberately conservative: it does not infer business constraints from
    a zero null rate alone, and it does not permit the model to invent thresholds.
    """
    columns = {column.name: column for column in evidence.columns}
    policy = get_dataset_rule_policy(evidence.dataset_id, evidence.columns)
    if not policy:
        return []
    candidates: list[DashboardRuleCandidate] = []
    cross_metrics = {
        (metric.left_column, metric.operator, metric.right_column): metric
        for metric in evidence.cross_field_metrics
    }

    for column in policy.required_identifiers:
        if column not in columns:
            continue
        evidence_refs = [f"policy.required_identifier.{column}", f"profile.column.{column}.null_rate"]
        if columns[column].full_distinct_count is not None:
            evidence_refs.extend(
                [
                    f"profile.column.{column}.full_distinct_count",
                    f"profile.column.{column}.uniqueness_rate",
                ]
            )
        candidates.append(
            DashboardRuleCandidate(
                id=f"not-null:{column}", rule_type="NOT_NULL", column=column, parameters={},
                dashboard_rule_type="not_null", rule_spec={"type": "not_null", "column": column},
                evidence_refs=evidence_refs,
                selection_reason="Dataset policy marks this identifier as required; the profile supplies its null rate.",
                priority=90, title=f"{column} must be populated",
                description=f"Require every row to contain the policy-required identifier {column}.",
                severity="HIGH", confidence_ceiling=0.85,
            )
        )
        break

    for column_name in policy.nonnegative_columns:
        column = columns.get(column_name)
        if column is None or column.min_value is None:
            continue
        evidence_refs = [
            f"policy.nonnegative_column.{column.name}",
            f"profile.column.{column.name}.min_value",
        ]
        if column.max_value is not None:
            evidence_refs.append(f"profile.column.{column.name}.max_value")
        if column.negative_rate is not None:
            evidence_refs.append(f"profile.column.{column.name}.negative_rate")
        evidence_refs.extend(
            f"profile.column.{column.name}.quantile.{name}"
            for name in ("p05", "p50", "p95")
            if name in column.quantiles
        )
        candidates.append(
            DashboardRuleCandidate(
                id=f"nonnegative:{column.name}", rule_type="RANGE", column=column.name,
                parameters={"min": 0.0}, dashboard_rule_type="numeric_range",
                rule_spec={"type": "numeric_range", "column": column.name, "min_value": 0.0},
                evidence_refs=evidence_refs,
                selection_reason=(
                    "Dataset policy defines this measure as non-negative; full-table bounds, negative rate "
                    "and quantiles describe current behavior."
                ),
                priority=100, title=f"{column.name} must be non-negative",
                description=f"Reject rows where the policy-defined non-negative measure {column.name} is below zero.",
                severity="HIGH", confidence_ceiling=0.9,
            )
        )
        break

    for column, allowed_values in policy.governed_value_sets.items():
        profile = columns.get(column)
        if profile and allowed_values:
            evidence_refs = [
                f"policy.governed_value_set.{column}",
                f"profile.column.{column}.distinct_count",
            ]
            if profile.out_of_domain_rate is not None:
                evidence_refs.append(f"profile.column.{column}.out_of_domain_rate")
            candidates.append(
                DashboardRuleCandidate(
                    id=f"governed-enum:{column}", rule_type="ACCEPTED_VALUES", column=column,
                    parameters={"accepted_values": allowed_values}, dashboard_rule_type="accepted_values",
                    rule_spec={"type": "accepted_values", "column": column, "allowed_values": allowed_values},
                    evidence_refs=evidence_refs,
                    selection_reason=(
                        "Dataset policy supplies the governed code set; the full-table profile supplies "
                        "observed cardinality and out-of-domain rate."
                    ),
                    priority=80, title=f"{column} must use governed values",
                    description=f"Validate {column} against the governed values configured for this dataset.",
                    severity="MEDIUM", confidence_ceiling=0.85,
                )
            )

    for relationship in policy.cross_field_rules:
        if relationship.left_column not in columns or relationship.right_column not in columns:
            continue
        policy_ref = (
            f"policy.cross_field.{relationship.left_column}."
            f"{relationship.operator}.{relationship.right_column}"
        )
        evidence_refs = [
            policy_ref,
            f"profile.column.{relationship.left_column}.data_type",
            f"profile.column.{relationship.right_column}.data_type",
        ]
        metric = cross_metrics.get(
            (relationship.left_column, relationship.operator, relationship.right_column)
        )
        if metric is not None:
            evidence_refs.append(
                f"profile.cross_field.{relationship.left_column}.{relationship.operator}."
                f"{relationship.right_column}.violation_rate"
            )
        candidates.append(
            DashboardRuleCandidate(
                id=f"cross-field:{relationship.left_column}:{relationship.operator}:{relationship.right_column}",
                rule_type="CROSS_FIELD_COMPARISON", column=relationship.left_column,
                parameters={"target_column": relationship.right_column, "operator": relationship.operator},
                dashboard_rule_type="cross_field_comparison",
                rule_spec={
                    "type": "cross_field_comparison",
                    "columns": [relationship.left_column, relationship.right_column],
                    "operator": relationship.operator,
                },
                evidence_refs=evidence_refs,
                selection_reason=(
                    "Dataset policy defines this relationship; the full-table profile measures its violation rate."
                ),
                priority=95,
                title=f"{relationship.left_column} must not follow {relationship.right_column}",
                description=(
                    f"Require {relationship.left_column} {relationship.operator} "
                    f"{relationship.right_column}, as configured by dataset policy."
                ),
                severity="CRITICAL", confidence_ceiling=0.9,
            )
        )
    return sorted(candidates, key=lambda candidate: candidate.priority, reverse=True)


def _match_dashboard_candidate(
    raw: dict[str, Any], candidates: list[DashboardRuleCandidate], evidence: ProposalEvidence
) -> DashboardRuleCandidate | None:
    """Return the exact deterministic candidate represented by a structured LLM rule."""
    rule_type = str(raw.get("rule_type", "")).upper()
    column = raw.get("column")
    parameters = raw.get("parameters") or {}
    if not isinstance(column, str) or not isinstance(parameters, dict):
        return None
    candidate_id = raw.get("candidate_id")
    if not isinstance(candidate_id, str):
        return None
    eligible_candidates = [candidate for candidate in candidates if candidate.id == candidate_id]
    if not eligible_candidates:
        return None
    for candidate in eligible_candidates:
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


def _fallback_core_fields(
    candidate: DashboardRuleCandidate, evidence: ProposalEvidence, confidence: float
) -> dict[str, Any]:
    return {
        "rule_name": candidate.title,
        "business_rationale": candidate.description,
        "proposal_basis": "MIXED",
        "evidence": {
            "sample_row_count": evidence.row_count,
            "sample_rate": 1.0,
            "sampling_caveat": None,
            "observed_metrics": {},
            "source_refs": candidate.evidence_refs,
        },
        "confidence_breakdown": {
            "overall": confidence,
            "evidence_strength": confidence,
            "business_support": confidence,
            "sample_representativeness": 1.0,
            "explanation": "Deterministic policy candidate fallback",
        },
    }


def _complete_with_policy_candidates(
    proposals: list[DashboardProposal], evidence: ProposalEvidence
) -> list[DashboardProposal]:
    """Fill omissions from a verified candidate set without inventing a rule.

    Small structured-output models can occasionally omit a checklist item despite a
    valid schema.  The fallback is deliberately unavailable when *no* model rule
    validates, which keeps provider/schema failures visible instead of masking them.
    """
    completed = list(proposals)
    if len(completed) >= 2:
        return completed
    present_types = {proposal.rule_type for proposal in proposals}
    for candidate in _build_dashboard_rule_candidates(evidence):
        if candidate.dashboard_rule_type in present_types:
            continue
        fallback_confidence = max(0.0, candidate.confidence_ceiling - 0.15)
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
                confidence=fallback_confidence,
                model_name="agent-policy-fallback-v1",
                **_fallback_core_fields(candidate, evidence, fallback_confidence),
            )
        )
        present_types.add(candidate.dashboard_rule_type)
        if len(completed) == 2:
            break
    priority_by_type = {
        candidate.dashboard_rule_type: candidate.priority
        for candidate in _build_dashboard_rule_candidates(evidence)
    }
    return sorted(completed, key=lambda proposal: priority_by_type.get(proposal.rule_type, 0), reverse=True)


def _policy_title(candidate: DashboardRuleCandidate) -> str:
    return candidate.title


def _policy_description(candidate: DashboardRuleCandidate) -> str:
    return candidate.description


def _policy_severity(candidate: DashboardRuleCandidate) -> str:
    return candidate.severity


def _mock_proposals(evidence: ProposalEvidence) -> list[DashboardProposal]:
    """Explicit offline mode for deterministic UI and automated tests."""
    available = {column.name for column in evidence.columns}
    mock_ids = {
        "not_null": "proposal-not-null",
        "numeric_range": "proposal-range",
        "accepted_values": "proposal-accepted-values",
        "cross_field_comparison": "proposal-cross-field",
    }
    result = [
        DashboardProposal(
            id=mock_ids[candidate.dashboard_rule_type],
            title=candidate.title,
            description=candidate.description,
            severity=candidate.severity,
            rule_type=candidate.dashboard_rule_type,
            rule_spec=candidate.rule_spec,
            evidence_refs=candidate.evidence_refs,
            evidence_summary=_safe_evidence_summary(evidence, candidate.evidence_refs),
            confidence=candidate.confidence_ceiling,
            model_name="agent-mock-v1",
            **_fallback_core_fields(candidate, evidence, candidate.confidence_ceiling),
        )
        for candidate in _build_dashboard_rule_candidates(evidence)
    ]

    policy = get_dataset_rule_policy(evidence.dataset_id, evidence.columns)
    fingerprint_columns = policy.duplicate_fingerprint_columns if policy else []
    duplicate_refs = ["profile.duplicate_rate", "policy.duplicate_fingerprint"]
    if fingerprint_columns and set(fingerprint_columns).issubset(available) and set(duplicate_refs).issubset(
        evidence.evidence_keys
    ):
        result.append(
            DashboardProposal(
                id="mock-duplicate-fingerprint",
                title="Duplicate fingerprint detection",
                description="Check the dataset-policy fingerprint for duplicate rows.",
                severity="MEDIUM",
                rule_type="duplicate_fingerprint",
                rule_spec={"type": "duplicate_fingerprint", "fingerprint_columns": fingerprint_columns},
                evidence_refs=duplicate_refs,
                evidence_summary=_safe_evidence_summary(evidence, duplicate_refs),
                confidence=0.8,
                model_name="agent-mock-v1",
                rule_name="Duplicate fingerprint detection",
                business_rationale="Duplicate business keys can double-count trips and financial measures.",
                proposal_basis="MIXED",
                evidence={"sample_row_count": evidence.row_count, "sample_rate": 1.0, "sampling_caveat": None, "observed_metrics": {}, "source_refs": duplicate_refs},
                confidence_breakdown={"overall": 0.8, "evidence_strength": 0.8, "business_support": 0.8, "sample_representativeness": 1.0, "explanation": "Policy-backed duplicate candidate"},
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
