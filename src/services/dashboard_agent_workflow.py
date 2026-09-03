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
import logging
import math
import re
import threading
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from src.config import get_settings
from src.models.database import ColumnProfileModel, DatasetModel, ProfileModel

logger = logging.getLogger(__name__)

SUPPORTED_RULE_TYPES = {
    "not_null",
    "numeric_range",
    "accepted_values",
    "cross_field_comparison",
    "duplicate_fingerprint",
}
SAFE_OPERATORS = {"<", "<=", ">", ">=", "==", "!="}

# Graph 1B has two LLM nodes. Keep its upper bound finite so a provider/network
# stall becomes a recorded workflow failure instead of an indefinitely RUNNING
# browser job on a Cloud Run instance.
RULE_PROPOSAL_GRAPH_TIMEOUT_SECONDS = 240

logger = logging.getLogger(__name__)

RULE_POLICY_PATH = Path(__file__).resolve().parents[1] / "resources" / "rule_policies.json"


class AgentWorkflowError(ValueError):
    """An expected, redacted failure returned by the product workflow."""


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
    rule_description: str = ""
    ai_reasoning: str = ""
    parameter_provenance: list[dict[str, Any]] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)


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
    """Đọc policy tuỳ chọn theo dataset. Thiếu file KHÔNG phải lỗi.

    Mọi nơi gọi ``get_dataset_rule_policy`` đều đã xử lý ``None``
    (``routes.py:1000``, ``:319``, ``:505``, ``:798``, ``job_runner.py:124``), tức
    thiết kế ban đầu coi policy là **phần ghi đè tuỳ chọn cho từng dataset**: dataset
    nào không có policy thì bỏ qua kiểm tra governed value, chứ không phải dừng lại.

    Chỉ hàm này phá vỡ hợp đồng đó. Khi ``ac4b663`` xoá mất file, một tệp cấu hình
    vắng mặt đã làm chết 7 điểm gọi — trong đó có Data explorer — dù mọi caller đều
    sẵn sàng chạy tiếp mà không cần policy.

    Điều này còn chặn chính mục tiêu sản phẩm: người dùng upload dataset bất kỳ sẽ
    không bao giờ có entry viết tay trong ``rule_policies.json``. Nếu thiếu file là
    lỗi chí mạng thì **không dataset mới nào chạy được**.

    File hỏng thì vẫn báo lỗi: đó là cấu hình sai, khác với cấu hình không có.
    """
    if not RULE_POLICY_PATH.exists():
        logger.info(
            "Không có %s — chạy không kèm policy theo dataset. "
            "Kiểm tra governed value sẽ được bỏ qua.",
            RULE_POLICY_PATH.name,
        )
        return RulePolicyDocument(datasets={})
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
    # Unknown datasets must not inherit NYC Taxi semantics. Callers that have
    # no immutable schema evidence receive no domain policy and can only use
    # explicitly supplied semantic contracts.
    return None


#: Headroom above the p95 quantile. Wide enough that ordinary variation does not
#: trip the rule, narrow enough that the rule still has something to reject.
_UPPER_BOUND_HEADROOM = 1.10


def _upper_bound(column) -> float | None:
    """An upper bound derived from the distribution, not from the observed maximum.

    A RANGE rule whose bounds are taken from the same column's min and max admits
    every value that existed at profiling time, so it can never report a violation
    -- it looks like a control and is one only on paper. Anchoring the top of the
    range on p95 plus headroom keeps normal rows inside it while leaving the tail
    outside, which is what makes the rule capable of firing at all.

    Returns None when the profile cannot support a bound that would actually
    constrain anything; the caller then leaves the rule open-topped rather than
    inventing a threshold with no evidence behind it.
    """
    p95 = column.quantiles.get("p95") if column.quantiles else None
    if p95 is None:
        return None
    bound = round(float(p95) * _UPPER_BOUND_HEADROOM, 4)
    # A bound at or above the observed maximum constrains nothing on this data.
    if column.max_value is not None and bound >= float(column.max_value):
        return None
    return bound


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
    validity_score: float | None = Field(ge=0.0, le=100.0)
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
            self.dataset_id: {
                "table": self.dataset_id,
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


def _proposal_evidence_from_versioned_snapshot(
    dataset: DatasetModel, snapshot: dict[str, Any]
) -> ProposalEvidence:
    """Adapt the canonical immutable profile snapshot for the proposal agent.

    Versioned imports persist aggregate evidence in ``profile_runs`` instead of
    the legacy ``profiles``/``column_profiles`` pair. Keep the proposal agent's
    allow-listed payload identical while sourcing it from the canonical snapshot.
    """
    columns = []
    for item in snapshot.get("columns", []):
        if not isinstance(item, dict) or not item.get("name"):
            continue
        columns.append(
            ProposalColumnEvidence(
                name=str(item["name"]),
                data_type=str(item.get("data_type") or "string"),
                null_rate=float(item.get("null_rate") or 0.0),
                distinct_count=int(item.get("distinct_count") or 0),
                non_null_count=(
                    int(item["non_null_count"])
                    if item.get("non_null_count") is not None
                    else None
                ),
                negative_rate=(
                    float(item["negative_rate"])
                    if item.get("negative_rate") is not None
                    else None
                ),
                quantiles=item.get("quantiles") or {},
                out_of_domain_rate=(
                    float(item["out_of_domain_rate"])
                    if item.get("out_of_domain_rate") is not None
                    else None
                ),
                full_distinct_count=(
                    int(item["full_distinct_count"])
                    if item.get("full_distinct_count") is not None
                    else None
                ),
                uniqueness_rate=(
                    float(item["uniqueness_rate"])
                    if item.get("uniqueness_rate") is not None
                    else None
                ),
                is_unique_full_table=item.get("is_unique_full_table"),
                min_value=(float(item["min_value"]) if item.get("min_value") is not None else None),
                max_value=(float(item["max_value"]) if item.get("max_value") is not None else None),
            )
        )
    if not columns:
        raise AgentWorkflowError("The completed profile has no eligible columns for proposal generation.")
    cross_field_metrics = [
        ProposalCrossFieldEvidence.model_validate(item)
        for item in snapshot.get("cross_field_metrics", [])
        if isinstance(item, dict)
    ]
    evidence_keys = list(snapshot.get("evidence_keys") or [])
    # The versioned snapshot must expose the same reference catalogue as the
    # legacy adapter; otherwise real range/identifier evidence is silently
    # rejected by the unchanged candidate provenance validator.
    for column in columns:
        prefix = f"profile.column.{column.name}"
        evidence_keys.extend([f"{prefix}.null_rate", f"{prefix}.distinct_count", f"{prefix}.data_type"])
        for field_name in ("min_value", "max_value", "non_null_count", "negative_rate", "out_of_domain_rate"):
            if getattr(column, field_name) is not None:
                evidence_keys.append(f"{prefix}.{field_name}")
        evidence_keys.extend(f"{prefix}.quantile.{name}" for name in column.quantiles)
        if column.full_distinct_count is not None:
            evidence_keys.extend(f"{prefix}.{name}" for name in ("full_distinct_count", "uniqueness_rate", "is_unique_full_table"))
    policy = get_dataset_rule_policy(dataset.id, columns)
    if policy:
        evidence_keys.extend(f"policy.required_identifier.{name}" for name in policy.required_identifiers)
        evidence_keys.extend(f"policy.nonnegative_column.{name}" for name in policy.nonnegative_columns)
        evidence_keys.extend(f"policy.governed_value_set.{name}" for name in policy.governed_value_sets)
        evidence_keys.extend(f"policy.cross_field.{r.left_column}.{r.operator}.{r.right_column}" for r in policy.cross_field_rules)
        if policy.duplicate_fingerprint_columns:
            evidence_keys.append("policy.duplicate_fingerprint")
    evidence_keys.extend(
        f"profile.cross_field.{metric.left_column}.{metric.operator}.{metric.right_column}.violation_rate"
        for metric in cross_field_metrics
    )
    return ProposalEvidence(
        dataset_id=dataset.id,
        manifest_version=dataset.manifest_version,
        row_count=int(snapshot["row_count"]),
        completeness_score=float(snapshot["completeness_score"]),
        validity_score=snapshot["validity_score"],
        duplicate_rate=float(snapshot["duplicate_rate"]),
        evidence_keys=list(dict.fromkeys(evidence_keys)),
        columns=columns,
        cross_field_metrics=cross_field_metrics,
    )


def build_proposal_evidence(db: Session, dataset_id: str, *, workflow_run_id: str | None = None) -> ProposalEvidence:
    """Build the only payload that may be passed to the proposal graph."""
    dataset = db.query(DatasetModel).filter(DatasetModel.id == dataset_id).first()
    if not dataset:
        raise AgentWorkflowError(f"Dataset {dataset_id} not found.")
    if dataset.manifest_version == "versioned-v1":
        from src.models.database import WorkflowRunModel
        from src.services.rule_proposer_workflow import _profile_snapshot
        from src.services.source_binding import workflow_binding
        run = db.get(WorkflowRunModel, workflow_run_id) if workflow_run_id else None
        if workflow_run_id and (not run or run.dataset_id != dataset_id):
            raise AgentWorkflowError("Workflow dataset mismatch")
        binding = workflow_binding(db, run) if run else None
        return _proposal_evidence_from_versioned_snapshot(dataset, _profile_snapshot(db, dataset_id, binding=binding))

    profile = db.query(ProfileModel).filter(ProfileModel.dataset_id == dataset_id).first()
    columns = (
        db.query(ColumnProfileModel)
        .filter(ColumnProfileModel.profile_dataset_id == dataset_id)
        .order_by(ColumnProfileModel.name)
        .all()
    )

    if not profile or not columns or dataset.status != "PROFILE_READY":
        try:
            from src.services.job_runner import _profile_uploaded_dataset, _uploaded_dataset_path
            uploaded_path = _uploaded_dataset_path(dataset_id)
            if uploaded_path:
                _profile_uploaded_dataset(db, dataset_id, uploaded_path)
                profile = db.query(ProfileModel).filter(ProfileModel.dataset_id == dataset_id).first()
                columns = (
                    db.query(ColumnProfileModel)
                    .filter(ColumnProfileModel.profile_dataset_id == dataset_id)
                    .order_by(ColumnProfileModel.name)
                    .all()
                )
        except Exception as err:
            logger.warning("Could not auto-profile uploaded dataset: %s", err)

    safe_columns: list[ProposalColumnEvidence] = []
    cross_field_metrics: list[ProposalCrossFieldEvidence] = []

    # Versioned imports have no legacy ProfileModel/ColumnProfileModel rows.
    # Keep Graph 1B on the exact snapshot adapter used by Graph 1A instead of
    # rebuilding a second, weaker mapping from the versioned profile payload.
    if dataset.manifest_version == "versioned-v1":
        try:
            from src.services.rule_proposer_workflow import _profile_snapshot

            return _proposal_evidence_from_versioned_snapshot(
                dataset, _profile_snapshot(db, dataset_id)
            )
        except Exception as err:
            raise AgentWorkflowError(
                "A completed versioned profile snapshot is required before requesting proposals."
            ) from err

    if profile and columns:
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
        cross_field_metrics = [
            ProposalCrossFieldEvidence.model_validate(item) for item in _parse_json_list(profile.cross_field_metrics_json)
        ]
    else:
        try:
            from src.services.rule_proposer_workflow import (
                _snapshot_from_versioned_profile,
                _versioned_profile_snapshot_row,
            )
            versioned_row = _versioned_profile_snapshot_row(db, dataset_id)
            if versioned_row:
                snap = _snapshot_from_versioned_profile(versioned_row)
                raw_cols = snap.get("columns") or []
                safe_columns = [
                    ProposalColumnEvidence(
                        name=col["name"],
                        data_type=col.get("data_type", "string"),
                        null_rate=float(col.get("null_rate") or 0.0),
                        distinct_count=col.get("distinct_count"),
                        non_null_count=col.get("non_null_count"),
                        negative_rate=col.get("negative_rate"),
                        quantiles=_parse_json_dict(json.dumps(col.get("quantiles"))) if isinstance(col.get("quantiles"), (dict, list)) else {},
                        out_of_domain_rate=col.get("out_of_domain_rate"),
                        full_distinct_count=col.get("full_distinct_count"),
                        uniqueness_rate=col.get("uniqueness_rate"),
                        is_unique_full_table=col.get("is_unique_full_table"),
                        min_value=col.get("min_value"),
                        max_value=col.get("max_value"),
                    )
                    for col in raw_cols
                    if isinstance(col, dict) and col.get("name") and col.get("name") != "source_row_id"
                ]
                dataset.status = "PROFILE_READY"
                db.commit()
        except Exception as err:
            logger.warning("Could not build proposal evidence from versioned profile: %s", err)
    if not safe_columns:
        raise AgentWorkflowError("A completed aggregate profile is required before requesting proposals.")

    evidence_keys = [
        "profile.row_count",
        "profile.completeness_score",
        "profile.validity_score",
        "profile.duplicate_rate",
    ]
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

    if profile and profile.cross_field_metrics_json:
        cross_field_metrics = [
            ProposalCrossFieldEvidence.model_validate(item) for item in _parse_json_list(profile.cross_field_metrics_json)
        ]
        evidence_keys.extend(
            f"profile.cross_field.{metric.left_column}.{metric.operator}.{metric.right_column}.violation_rate"
            for metric in cross_field_metrics
        )

    policy = get_dataset_rule_policy(dataset_id, safe_columns)
    if policy:
        evidence_keys.extend(f"policy.required_identifier.{column}" for column in policy.required_identifiers)
        evidence_keys.extend(f"policy.nonnegative_column.{column}" for column in policy.nonnegative_columns)
        evidence_keys.extend(f"policy.governed_value_set.{column}" for column in policy.governed_value_sets)
        evidence_keys.extend(
            f"policy.cross_field.{rule.left_column}.{rule.operator}.{rule.right_column}"
            for rule in policy.cross_field_rules
        )
        if policy.duplicate_fingerprint_columns:
            evidence_keys.append("policy.duplicate_fingerprint")

    row_count = profile.row_count if profile else dataset.row_count
    completeness_score = profile.completeness_score if profile else 100.0
    validity_score = profile.validity_score if profile else 100.0
    duplicate_rate = profile.duplicate_rate if profile else 0.0

    return ProposalEvidence(
        dataset_id=dataset_id,
        manifest_version=dataset.manifest_version,
        row_count=row_count,
        completeness_score=completeness_score,
        validity_score=validity_score,
        duplicate_rate=duplicate_rate,
        evidence_keys=list(dict.fromkeys(evidence_keys)),
        columns=safe_columns,
        cross_field_metrics=cross_field_metrics,
    )


def generate_dashboard_proposals(
    db: Session, dataset_id: str, semantic_contract: dict[str, Any] | None = None
) -> list[DashboardProposal]:
    """Return two to five validated proposals in the configured local agent mode.

    This is the compatibility entrypoint used by the standalone dashboard job.
    The wizard uses :func:`generate_rule_proposals_via_graph_1b` so its node
    telemetry and three-node graph remain visible to the steward.
    """
    evidence = build_proposal_evidence(db, dataset_id)
    settings = get_settings()
    if settings.agent_mode == "mock":
        return _mock_proposals(evidence)

    try:
        candidates = _build_dashboard_rule_candidates(evidence)
        if len(candidates) < 2:
            return _mock_proposals(evidence)
        if semantic_contract is not None:
            raw_rules = _invoke_dashboard_proposal_graph(evidence, semantic_contract=semantic_contract)
        else:
            raw_rules = _invoke_dashboard_proposal_graph(evidence)
    except Exception as exc:
        logger.warning("Graph proposal generation failed for dataset %s, falling back: %s", dataset_id, exc)
        return _mock_proposals(evidence)

    proposals = _normalise_graph_rules(raw_rules, evidence)
    if not proposals:
        raise AgentWorkflowError(
            "The proposal graph did not return enough valid evidence-backed rules."
        )
    proposals = _complete_with_policy_candidates(proposals, evidence)
    if 2 <= len(proposals) <= 5:
        return proposals

    return _mock_proposals(evidence)


def generate_dashboard_policy_fallback_proposals(
    db: Session, dataset_id: str, *, workflow_run_id: str | None = None
) -> list[DashboardProposal]:
    """Build an evidence-backed deterministic fallback without another LLM call.

    This is intentionally separate from ``generate_dashboard_proposals``.  If
    the full wizard graph has already spent its provider budget and failed, the
    workflow must not invoke a second proposer graph and double the latency.
    The fallback only promotes the server-owned policy candidates, so it cannot
    invent thresholds or fields.
    """
    evidence = build_proposal_evidence(db, dataset_id, workflow_run_id=workflow_run_id)
    proposals = _complete_with_policy_candidates([], evidence)
    if not proposals:
        raise AgentWorkflowError(
            "The completed profile does not contain enough policy-backed candidates for fallback proposals."
        )
    return proposals


def generate_rule_proposals_via_graph_1b(
    db: Session,
    dataset_id: str,
    semantic_contract: dict[str, Any],
    *,
    workflow_run_id: str | None = None,
) -> list[DashboardProposal]:
    """Return proposals produced by the full Graph 1B (three nodes).

    This entrypoint drives the documented three-node graph while reusing the same
    validation, normalisation and policy-completion pipeline, so the proposals it
    returns are indistinguishable in shape from the compatibility entrypoint.

    Raises ``AgentWorkflowError`` like its sibling; the caller decides whether to
    fall back to the deterministic policy path.
    """
    evidence = build_proposal_evidence(db, dataset_id, workflow_run_id=workflow_run_id)
    if get_settings().agent_mode == "mock":
        return _mock_proposals(evidence)

    if len(_build_dashboard_rule_candidates(evidence)) < 2:
        raise AgentWorkflowError("The aggregate profile has fewer than two evidence-backed dashboard candidates.")

    # Evidence loading starts a synchronous SQLAlchemy transaction. Release it
    # before the async graph waits on the LLM; telemetry and UI polling need the
    # same small, project-wide Supabase connection budget.
    db.commit()
    raw_rules = _invoke_rule_proposal_graph(evidence, semantic_contract, workflow_run_id=workflow_run_id)
    proposals = _normalise_graph_rules(raw_rules, evidence)
    if not proposals:
        raise AgentWorkflowError("Graph 1B did not return enough valid, evidence-backed rules.")
    proposals = _complete_with_policy_candidates(proposals, evidence)
    # Only a lower bound here. The 2..5 window belongs to the dashboard shortcut,
    # where five tiles is a layout budget; this path feeds the step-3 review
    # queue, which routinely holds dozens. Enforcing the upper bound threw away
    # complete, evidence-backed sets for being too useful -- measured as 14 valid
    # rules rejected outright.
    if len(proposals) < 2:
        raise AgentWorkflowError(
            f"Graph 1B produced only {len(proposals)} evidence-backed rule(s); at least 2 are required."
        )
    return proposals


def _table_keyed_contract(semantic_contract: dict[str, Any], dataset_id: str) -> dict[str, Any]:
    """Return the contract in the ``{"tables": {name: contract}}`` shape nodes expect.

    The workflow stores a *flattened* contract in its artifact -- columns and
    assumptions at the top level, no table key -- because the wizard only ever
    handles one dataset.  ``rule_candidate_builder`` and ``prompt_customizer``
    both iterate ``contract["tables"]`` and return empty when it is missing, so
    passing the artifact payload through unchanged makes them no-ops that still
    report success.  Re-wrapping here is what makes them do real work.
    """
    if isinstance(semantic_contract.get("tables"), dict) and semantic_contract["tables"]:
        return semantic_contract
    return {**semantic_contract, "tables": {dataset_id: semantic_contract}}


def _invoke_rule_proposal_graph(
    evidence: ProposalEvidence,
    semantic_contract: dict[str, Any],
    *,
    workflow_run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Run Graph 1B: rule_candidate_builder ➔ prompt_customizer ➔ rule_proposer."""
    from src.agents.graph import build_rule_proposal_graph
    from src.services.node_telemetry import start_graph_run

    contract = _table_keyed_contract(semantic_contract, evidence.dataset_id)
    binding = semantic_contract.get("source_binding") or {}

    async def invoke() -> list[dict[str, Any]]:
        start_graph_run(workflow_run_id=workflow_run_id, dataset_id=evidence.dataset_id)
        graph = build_rule_proposal_graph()
        digest = evidence.to_agent_digest()
        tables = contract["tables"]
        for table_name, table_digest in digest.items():
            if isinstance(table_digest, dict):
                table_digest["confirmed_semantic_contract"] = tables.get(table_name, semantic_contract)
        result = await graph.ainvoke(
            {
                "dataset_id": evidence.dataset_id,
                "dataset_version_id": binding.get("dataset_version_id"),
                "profile_run_id": binding.get("profile_run_id"),
                "rule_run_id": f"graph1b-proposal-{uuid.uuid4().hex}",
                "dataset_profile_digest": digest,
                "semantic_contract": contract,
                "normalized_data_dictionary": {"tables": tables},
                "target_tables": [evidence.dataset_id],
                "metadata": {
                    "workflow": "graph_1b",
                    "source_binding": binding,
                    "evidence_source": "persisted_aggregate_profile",
                    "max_retries": 0,
                },
            }
        )
        errors = result.get("rule_proposal_errors", [])
        if errors:
            raise AgentWorkflowError("Graph 1B could not produce a valid structured response.")
        return result.get("proposed_rules", [])

    return _run_coroutine_safely(
        asyncio.wait_for(invoke(), timeout=RULE_PROPOSAL_GRAPH_TIMEOUT_SECONDS)
    )


def _invoke_dashboard_proposal_graph(
    evidence: ProposalEvidence, semantic_contract: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Resume Graph 1 after semantic review using aggregate-only evidence."""
    from src.agents.graph import build_dashboard_proposal_graph

    policy = get_dataset_rule_policy(evidence.dataset_id, evidence.columns)
    required = set(policy.required_identifiers if policy else [])
    governed = set(policy.governed_value_sets if policy else {})
    semantic_columns = []
    for column in evidence.columns:
        inferred_role = _column_role(column.name, column.data_type)
        semantic_type = (
            "identifier"
            if column.name in required
            else "category"
            if column.name in governed
            else "timestamp"
            if inferred_role == "datetime"
            else "numeric"
            if inferred_role == "numeric"
            else "category"
        )
        semantic_columns.append(
            {
                "name": column.name,
                "semantic_type": semantic_type,
                "nullable_expected": column.name not in required,
                "confidence": 1.0 if column.name in required or column.name in governed else 0.8,
            }
        )
    relationships = [
        {
            "left_column": item.left_column,
            "operator": item.operator,
            "right_column": item.right_column,
        }
        for item in (policy.cross_field_rules if policy else [])
    ]
    if semantic_contract is None:
        semantic_contract = {
            "status": "confirmed",
            "tables": {
                evidence.dataset_id: {
                    "table_purpose": "Validated dashboard dataset",
                    "columns": semantic_columns,
                    "relationships": relationships,
                }
            },
        }

    async def invoke() -> list[dict[str, Any]]:
        graph = build_dashboard_proposal_graph()
        digest = evidence.to_agent_digest()
        if semantic_contract:
            tables = semantic_contract.get("tables") or {}
            for table_name, table_digest in digest.items():
                if isinstance(table_digest, dict):
                    table_digest["confirmed_semantic_contract"] = tables.get(table_name, semantic_contract)
        result = await graph.ainvoke(
            {
                "dataset_id": evidence.dataset_id,
                "rule_run_id": f"dashboard-proposal-{uuid.uuid4().hex}",
                "dataset_profile_digest": digest,
                "semantic_contract": semantic_contract,
                "normalized_data_dictionary": {
                    "tables": (semantic_contract or {}).get("tables", {})
                },
                "metadata": {
                    "workflow": "dashboard",
                    "evidence_source": "persisted_aggregate_profile",
                    "graph_stages": [
                        "rule_candidate_builder",
                        "prompt_customizer",
                        "rule_proposer",
                    ],
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


    for raw in raw_rules:
        matched_candidate = _match_dashboard_candidate(raw, candidates, evidence)
        if not matched_candidate:
            continue
        proposal = _normalise_graph_rule(raw, evidence, matched_candidate)
        if not proposal:
            continue
        if matched_candidate.id in candidate_ids:
            continue
        candidate_ids.add(matched_candidate.id)
        accepted.append((proposal, matched_candidate))
    # The model chooses candidates; server policy owns stable display order.
    accepted.sort(key=lambda item: item[1].priority, reverse=True)
    return [proposal for proposal, _candidate in accepted]


def _normalise_text_list(value: Any) -> list[str]:
    """Keep only bounded, user-readable string assumptions from Agent output."""
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _normalise_parameter_provenance(value: Any) -> list[dict[str, Any]]:
    """Preserve provenance entries without letting malformed values cross the API."""
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _normalise_text(value: Any, fallback: str) -> str:
    """Use the Agent narrative when present, otherwise the allow-listed candidate."""
    normalized = str(value or "").strip()
    return normalized or fallback


def _has_unsupported_range_number(text: str, candidate: DashboardRuleCandidate) -> bool:
    """Reject a narrative that repeats a threshold discarded by server policy."""
    if candidate.rule_type != "RANGE":
        return False
    canonical = [
        number
        for number in (
            _finite_float(candidate.rule_spec.get("min_value")),
            _finite_float(candidate.rule_spec.get("max_value")),
        )
        if number is not None
    ]
    if not canonical:
        return False
    for raw_number in re.findall(r"(?<![A-Za-z])[-+]?\d+(?:[.,]\d+)?", text):
        number = _finite_float(raw_number.replace(",", "."))
        if number is not None and not any(math.isclose(number, allowed, rel_tol=1e-9, abs_tol=1e-9) for allowed in canonical):
            return True
    return False


def _build_observed_metrics(candidate: DashboardRuleCandidate, evidence: ProposalEvidence) -> dict[str, Any]:
    col_map = {c.name: c for c in evidence.columns}
    col = col_map.get(candidate.column)
    metrics: dict[str, Any] = {"sample_row_count": evidence.row_count}
    if col:
        metrics.update({
            "null_rate": round(col.null_rate, 4),
            "null_count": int(round(col.null_rate * evidence.row_count)),
            "distinct_count": col.distinct_count,
            "data_type": col.data_type,
        })
        if col.min_value is not None:
            metrics["min_value"] = col.min_value
        if col.max_value is not None:
            metrics["max_value"] = col.max_value
    return metrics


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
    raw_description = _normalise_text(raw.get("rule_description"), candidate.description)
    description = (
        candidate.description
        if _has_unsupported_range_number(raw_description, candidate)
        else raw_description
    )
    reasoning = _normalise_text(
        raw.get("ai_reasoning"),
        _safe_evidence_summary(evidence, candidate.evidence_refs),
    )
    if not 1 <= len(description) <= 500 or not 1 <= len(reasoning) <= 1_000:
        return None
    rule_name = _normalise_text(raw.get("rule_name"), candidate.title)
    business_rationale = _normalise_text(raw.get("business_rationale"), description)
    assumptions = _normalise_text_list(raw.get("assumptions"))
    parameter_provenance = _normalise_parameter_provenance(raw.get("parameter_provenance"))

    model_name = f"langgraph-{get_settings().llm_provider}"
    if not set(candidate.evidence_refs).issubset(evidence.evidence_keys):
        return None
    selected_refs = raw.get("selected_evidence_refs") or candidate.evidence_refs
    if not set(selected_refs).issubset(candidate.evidence_refs):
        return None

    col_map = {c.name: c for c in evidence.columns}
    col = col_map.get(candidate.column)
    dynamic_ceiling = candidate.confidence_ceiling
    if col and candidate.dashboard_rule_type == "not_null" and col.null_rate == 0.0:
        dynamic_ceiling = max(candidate.confidence_ceiling, 0.98)

    capped_confidence = min(confidence, dynamic_ceiling)
    normalized_breakdown = dict(
        confidence_payload
        or {
            "overall": capped_confidence,
            "evidence_strength": capped_confidence,
            "business_support": capped_confidence,
            "sample_representativeness": 1.0,
            "explanation": "Dashboard candidate confidence ceiling",
        }
    )
    normalized_breakdown["overall"] = capped_confidence

    return DashboardProposal(
        id=f"proposal-{uuid.uuid4().hex}",
        title=candidate.title,
        # Keep the stable candidate title for the EN view, while the VI view
        # uses rule_name below in the frontend.  The description and evidence
        # fields are the Agent's canonical Vietnamese narratives.
        description=description,
        severity=candidate.severity,
        rule_type=candidate.dashboard_rule_type,
        rule_spec=candidate.rule_spec,
        evidence_refs=candidate.evidence_refs,
        evidence_summary=reasoning,
        confidence=capped_confidence,
        model_name=model_name,
        rule_name=rule_name,
        business_rationale=business_rationale,
        proposal_basis=str(raw.get("proposal_basis") or "MIXED"),
        evidence={
            "sample_row_count": evidence.row_count,
            "sample_rate": 1.0,
            "sampling_caveat": None,
            "observed_metrics": _build_observed_metrics(candidate, evidence),
            "source_refs": candidate.evidence_refs,
        },
        confidence_breakdown=normalized_breakdown,
        rule_description=description,
        ai_reasoning=reasoning,
        assumptions=assumptions,
        parameter_provenance=parameter_provenance,
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
        (metric.left_column, metric.operator, metric.right_column): metric for metric in evidence.cross_field_metrics
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
                id=f"not-null:{column}",
                rule_type="NOT_NULL",
                column=column,
                parameters={},
                dashboard_rule_type="not_null",
                rule_spec={"type": "not_null", "column": column},
                evidence_refs=evidence_refs,
                selection_reason="Dataset policy marks this identifier as required; the profile supplies its null rate.",
                priority=90,
                title=f"{column} must be populated",
                description=f"Require every row to contain the policy-required identifier {column}.",
                severity="HIGH",
                confidence_ceiling=0.85,
            )
        )

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
        upper = _upper_bound(column)
        parameters: dict[str, Any] = {"min": 0.0}
        rule_spec: dict[str, Any] = {"type": "numeric_range", "column": column.name, "min_value": 0.0}
        if upper is not None:
            parameters["max"] = upper
            rule_spec["max_value"] = upper
        candidates.append(
            DashboardRuleCandidate(
                id=f"nonnegative:{column.name}",
                rule_type="RANGE",
                column=column.name,
                parameters=parameters,
                dashboard_rule_type="numeric_range",
                rule_spec=rule_spec,
                evidence_refs=evidence_refs,
                selection_reason=(
                    "Dataset policy defines this measure as non-negative; full-table bounds, negative rate "
                    "and quantiles describe current behavior."
                    + (
                        f" Upper bound {upper} sits above p95 so the rule can still reject an outlier."
                        if upper is not None
                        else ""
                    )
                ),
                priority=100,
                title=(
                    f"{column.name} must be non-negative"
                    if upper is None
                    else f"{column.name} must be between 0 and {upper}"
                ),
                description=f"Reject rows where the policy-defined non-negative measure {column.name} is below zero.",
                severity="HIGH",
                confidence_ceiling=0.9,
            )
        )

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
                    id=f"governed-enum:{column}",
                    rule_type="ACCEPTED_VALUES",
                    column=column,
                    parameters={"accepted_values": allowed_values},
                    dashboard_rule_type="accepted_values",
                    rule_spec={"type": "accepted_values", "column": column, "allowed_values": allowed_values},
                    evidence_refs=evidence_refs,
                    selection_reason=(
                        "Dataset policy supplies the governed code set; the full-table profile supplies "
                        "observed cardinality and out-of-domain rate."
                    ),
                    priority=80,
                    title=f"{column} must use governed values",
                    description=f"Validate {column} against the governed values configured for this dataset.",
                    severity="MEDIUM",
                    confidence_ceiling=0.85,
                )
            )

    for relationship in policy.cross_field_rules:
        if relationship.left_column not in columns or relationship.right_column not in columns:
            continue
        policy_ref = (
            f"policy.cross_field.{relationship.left_column}.{relationship.operator}.{relationship.right_column}"
        )
        evidence_refs = [
            policy_ref,
            f"profile.column.{relationship.left_column}.data_type",
            f"profile.column.{relationship.right_column}.data_type",
        ]
        metric = cross_metrics.get((relationship.left_column, relationship.operator, relationship.right_column))
        if metric is not None:
            evidence_refs.append(
                f"profile.cross_field.{relationship.left_column}.{relationship.operator}."
                f"{relationship.right_column}.violation_rate"
            )
        candidates.append(
            DashboardRuleCandidate(
                id=f"cross-field:{relationship.left_column}:{relationship.operator}:{relationship.right_column}",
                rule_type="CROSS_FIELD_COMPARISON",
                column=relationship.left_column,
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
                severity="CRITICAL",
                confidence_ceiling=0.9,
            )
        )

    existing_column_rules = {candidate.column for candidate in candidates}
    for col in evidence.columns:
        if col.name in ("source_row_id", "id"):
            continue
        if col.name not in existing_column_rules:
            if col.null_rate == 0.0:
                candidates.append(
                    DashboardRuleCandidate(
                        id=f"not-null:{col.name}", rule_type="NOT_NULL", column=col.name, parameters={},
                        dashboard_rule_type="not_null", rule_spec={"type": "not_null", "column": col.name},
                        evidence_refs=[f"profile.column.{col.name}.null_rate"],
                        selection_reason=f"Column {col.name} observed null rate is 0.0% across all {evidence.row_count:,} rows.",
                        priority=90, title=f"{col.name} must not be null",
                        description=f"Ensure every row contains a valid {col.name}.",
                        severity="HIGH", confidence_ceiling=0.95,
                    )
                )
            elif col.null_rate <= 0.01:
                candidates.append(
                    DashboardRuleCandidate(
                        id=f"not-null:{col.name}", rule_type="NOT_NULL", column=col.name, parameters={},
                        dashboard_rule_type="not_null", rule_spec={"type": "not_null", "column": col.name},
                        evidence_refs=[f"profile.column.{col.name}.null_rate"],
                        selection_reason=f"Column {col.name} observed null rate is {col.null_rate*100:.1f}%.",
                        priority=70, title=f"{col.name} must not be null",
                        description=f"Ensure every row contains a valid {col.name}.",
                        severity="MEDIUM", confidence_ceiling=0.75,
                    )
                )
            if col.data_type in ("numeric", "float", "integer", "real") and col.min_value is not None:
                min_val = 0.0 if col.min_value >= 0 else float(col.min_value)
                upper = _upper_bound(col)
                range_parameters: dict[str, Any] = {"min": min_val}
                range_spec: dict[str, Any] = {
                    "type": "numeric_range", "column": col.name, "min_value": min_val,
                }
                range_refs = [f"profile.column.{col.name}.min_value"]
                if upper is not None:
                    range_parameters["max"] = upper
                    range_spec["max_value"] = upper
                    range_refs.extend(
                        [f"profile.column.{col.name}.quantile.p95", f"profile.column.{col.name}.max_value"]
                    )
                candidates.append(
                    DashboardRuleCandidate(
                        id=f"range:{col.name}", rule_type="RANGE", column=col.name,
                        parameters=range_parameters, dashboard_rule_type="numeric_range",
                        rule_spec=range_spec,
                        evidence_refs=range_refs,
                        selection_reason=(
                            f"Observed minimum for {col.name} is {col.min_value}."
                            if upper is None
                            else (
                                f"Observed minimum for {col.name} is {col.min_value}; the upper bound "
                                f"{upper} sits above p95 and below the observed maximum, so the rule "
                                "can reject an outlier instead of admitting everything."
                            )
                        ),
                        priority=85,
                        title=(
                            f"{col.name} minimum threshold (>= {min_val})"
                            if upper is None
                            else f"{col.name} expected range [{min_val}, {upper}]"
                        ),
                        description=(
                            f"Validate that {col.name} values are greater than or equal to {min_val}."
                            if upper is None
                            else f"Validate that {col.name} values fall between {min_val} and {upper}."
                        ),
                        severity="MEDIUM", confidence_ceiling=0.85,
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
    supplied_max = _finite_float(parameters.get("max"))
    # Omitting the upper bound is not inventing one. Candidates carry a p95-derived
    # maximum so the rule can fire at all, and a model asked for a "must be
    # non-negative" rule often answers with the lower bound alone. It has still named
    # the right candidate and made nothing up, and the server persists its own
    # canonical parameters either way -- so this is a match, not a fallback.
    if supplied_max is None and candidate.parameters.get("max") is not None:
        return True
    observed_max = next((column.max_value for column in evidence.columns if column.name == candidate.column), None)
    return observed_max is not None and supplied_max == observed_max


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
    reasoning = _safe_evidence_summary(evidence, candidate.evidence_refs)
    return {
        "rule_name": candidate.title,
        "business_rationale": candidate.description,
        "proposal_basis": "MIXED",
        "evidence": {
            "sample_row_count": evidence.row_count,
            "sample_rate": 1.0,
            "sampling_caveat": None,
            "observed_metrics": _build_observed_metrics(candidate, evidence),
            "source_refs": candidate.evidence_refs,
        },
        "confidence_breakdown": {
            "overall": confidence,
            "evidence_strength": confidence,
            "business_support": confidence,
            "sample_representativeness": 1.0,
            "explanation": "Deterministic policy candidate fallback",
        },
        "rule_description": candidate.description,
        "ai_reasoning": reasoning,
        "assumptions": [],
        "parameter_provenance": [],
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
    priority_by_type: dict[str, int] = {}
    for candidate in _build_dashboard_rule_candidates(evidence):
        priority_by_type[candidate.dashboard_rule_type] = max(
            priority_by_type.get(candidate.dashboard_rule_type, 0), candidate.priority
        )
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
    used_ids: set[str] = set()

    result: list[DashboardProposal] = []
    for candidate in _build_dashboard_rule_candidates(evidence):
        primary_id = mock_ids.get(candidate.dashboard_rule_type)
        if primary_id and primary_id not in used_ids:
            proposal_id = primary_id
        else:
            proposal_id = f"proposal-{candidate.id.replace(':', '-')}"
        used_ids.add(proposal_id)

        result.append(
            DashboardProposal(
                id=proposal_id,
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
        )

    policy = get_dataset_rule_policy(evidence.dataset_id, evidence.columns)
    fingerprint_columns = policy.duplicate_fingerprint_columns if policy else []
    duplicate_refs = ["profile.duplicate_rate", "policy.duplicate_fingerprint"]
    if (
        fingerprint_columns
        and set(fingerprint_columns).issubset(available)
        and set(duplicate_refs).issubset(evidence.evidence_keys)
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
                evidence={
                    "sample_row_count": evidence.row_count,
                    "sample_rate": 1.0,
                    "sampling_caveat": None,
                    "observed_metrics": {},
                    "source_refs": duplicate_refs,
                },
                confidence_breakdown={
                    "overall": 0.8,
                    "evidence_strength": 0.8,
                    "business_support": 0.8,
                    "sample_representativeness": 1.0,
                    "explanation": "Policy-backed duplicate candidate",
                },
                parameter_provenance=[],
                assumptions=[],
            )
        )
    if not result:
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
