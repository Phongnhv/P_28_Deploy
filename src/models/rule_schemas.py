"""Pydantic v2 schemas cho Rule Proposer structured output.

Critical: RuleParameters dùng closed model (không dùng bare dict) để
with_structured_output() tạo ra JSON Schema hợp lệ cho Mistral / OpenAI.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class RuleType(StrEnum):
    NOT_NULL = "NOT_NULL"
    UNIQUE = "UNIQUE"
    RANGE = "RANGE"
    ACCEPTED_VALUES = "ACCEPTED_VALUES"   # kiểm tra giá trị enum
    REGEX_FORMAT = "REGEX_FORMAT"
    FRESHNESS = "FRESHNESS"
    ROW_COUNT = "ROW_COUNT"               # rule cấp bảng
    NULL_RATE = "NULL_RATE"               # null_pct phải nhỏ hơn ngưỡng
    CROSS_FIELD_COMPARISON = "CROSS_FIELD_COMPARISON"

class DataQualityDimension(StrEnum):
    """(Phần bổ sung cho HITL UI) Giúp Data Steward filter và nhóm các rule trên web"""
    COMPLETENESS = "COMPLETENESS"
    UNIQUENESS = "UNIQUENESS"
    VALIDITY = "VALIDITY"
    ACCURACY = "ACCURACY"
    CONSISTENCY = "CONSISTENCY"
    FRESHNESS = "FRESHNESS"

class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

class ProposalBasis(StrEnum):
    SCHEMA_CONSTRAINT = "SCHEMA_CONSTRAINT"
    DATA_PROFILE = "DATA_PROFILE"
    DATA_DICTIONARY = "DATA_DICTIONARY"
    HISTORICAL_RULE = "HISTORICAL_RULE"
    POLICY = "POLICY"
    MIXED = "MIXED"

class EvidenceSourceType(StrEnum):
    SCHEMA_CONSTRAINT = "SCHEMA_CONSTRAINT"
    DATA_PROFILE = "DATA_PROFILE"
    DATA_DICTIONARY = "DATA_DICTIONARY"
    HISTORICAL_RULE = "HISTORICAL_RULE"
    POLICY = "POLICY"

class RuleStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


# ---------------------------------------------------------------------------
# Parameter bag (closed — tất cả field optional)
# ---------------------------------------------------------------------------

class RuleParameters(BaseModel):
    """Closed param bag — chỉ điền các field liên quan đến rule_type."""

    model_config = ConfigDict(extra="forbid")

    min: float | None = None
    max: float | None = None
    accepted_values: list[str] | None = None
    regex: str | None = None
    max_age_hours: float | None = None
    max_null_pct: float | None = None
    min_row_count: int | None = None
    target_column: str | None = None
    operator: Literal["<=", "<", ">=", ">", "=", "==", "!=", "<>"] | None = None


class ParameterProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parameter_name: str = Field(min_length=1)
    source_type: EvidenceSourceType
    source_ref: str = Field(min_length=1)
    derivation_method: str = Field(min_length=1)


class RuleConfidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall: float = Field(ge=0.0, le=1.0)
    evidence_strength: float = Field(ge=0.0, le=1.0)
    business_support: float = Field(ge=0.0, le=1.0)
    sample_representativeness: float = Field(ge=0.0, le=1.0)
    explanation: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_overall(self) -> RuleConfidence:
        component_mean = (
            self.evidence_strength + self.business_support + self.sample_representativeness
        ) / 3
        if abs(self.overall - component_mean) > 0.25:
            raise ValueError("confidence.overall chênh quá 0.25 so với trung bình các thành phần")
        return self


class RuleEvidenceSnapshot(BaseModel):
    """Evidence do node resolve từ digest; model không được tự tạo object này."""

    model_config = ConfigDict(extra="forbid")

    sample_row_count: int = Field(ge=0)
    sample_rate: float = Field(ge=0.0, le=1.0)
    sampling_caveat: str | None = None
    observed_metrics: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Proposed rule (one row in the HITL review table)
# ---------------------------------------------------------------------------

class ProposedRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str | None = Field(
        None,
        description="Opaque candidate identifier supplied by the dashboard policy checklist.",
    )
    column: str | None = Field(
        None,
        description="None cho rule cấp bảng (ROW_COUNT). Phải khớp tên cột trong digest.",
    )
    rule_type: RuleType
    parameters: RuleParameters = Field(default_factory=RuleParameters)
    rule_name: str = Field(min_length=1)
    business_rationale: str = Field(min_length=1)
    proposal_basis: ProposalBasis
    selected_evidence_refs: list[str] = Field(min_length=1)
    parameter_provenance: list[ParameterProvenance] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    confidence: RuleConfidence
    severity: Severity

    dimension: DataQualityDimension = Field(
        ...,
        description="Phân loại khía cạnh chất lượng dữ liệu để hiển thị cho Data Steward."
    )
    rule_description: str = Field(
        ...,
        description=(
            "Một câu mô tả rule bằng tiếng Việt tự nhiên, dành cho Data Steward không biết code. "
            "Phải nêu rõ tên cột (bằng tiếng Việt nếu có trong Data Dictionary), theo sau là điều kiện cụ thể. "
            "Ví dụ: 'Cước phí cơ bản (fare_amount) không được mang giá trị âm, vì đây là khoản tiền thanh toán.' "
            "hoặc 'Số hành khách (passenger_count) phải từ 0 đến 6, phù hợp với sức chứa thực tế của taxi.' "
            "KHÔNG dùng thuật ngữ kỹ thuật như RANGE, NULL, regex, signal."
        ),
    )

    ai_reasoning: str = Field(
        ...,
        description=(
            "Rationale ngắn gọn bằng tiếng Việt, giải thích TẠI SAO rule được đề xuất bằng evidence aggregate "
            "và ngữ cảnh nghiệp vụ từ Data Dictionary. Không yêu cầu hoặc tiết lộ chuỗi suy luận nội bộ."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy_payload(cls, value):
        """Accept persisted/test payloads from the pre-core-evidence contract.

        The generated JSON schema still requires the new fields; this adapter only
        keeps old artifacts and fixtures readable during rollout.
        """
        if not isinstance(value, dict) or "confidence_score" not in value:
            return value
        upgraded = dict(value)
        score = upgraded.pop("confidence_score")
        description = str(upgraded.get("rule_description") or "Rule proposal")
        params = upgraded.get("parameters") or {}
        if isinstance(params, RuleParameters):
            params = params.model_dump(exclude_none=True)
        elif isinstance(params, dict):
            pass
        else:
            params = {}
        reference = "history:legacy:proposal"
        upgraded.setdefault("rule_name", description)
        upgraded.setdefault("business_rationale", str(upgraded.get("ai_reasoning") or description))
        upgraded.setdefault("proposal_basis", ProposalBasis.HISTORICAL_RULE)
        upgraded.setdefault("selected_evidence_refs", [reference])
        upgraded.setdefault(
            "parameter_provenance",
            [
                {
                    "parameter_name": name,
                    "source_type": EvidenceSourceType.HISTORICAL_RULE,
                    "source_ref": reference,
                    "derivation_method": "legacy proposal compatibility",
                }
                for name, parameter_value in params.items()
                if parameter_value is not None
            ],
        )
        upgraded.setdefault("assumptions", ["Upgraded from the legacy proposal schema."])
        upgraded.setdefault(
            "confidence",
            {
                "overall": score,
                "evidence_strength": score,
                "business_support": score,
                "sample_representativeness": score,
                "explanation": "Legacy confidence score",
            },
        )
        return upgraded

    @model_validator(mode="after")
    def _validate_parameters(self) -> ProposedRule:
        """Guardrail: kiểm tra từng rule_type có đủ tham số bắt buộc không và có parameter provenance không."""
        rt = self.rule_type
        p = self.parameters

        if len(self.selected_evidence_refs) != len(set(self.selected_evidence_refs)):
            raise ValueError("selected_evidence_refs không được trùng lặp")
        if rt == RuleType.ROW_COUNT and self.column is not None:
            raise ValueError("ROW_COUNT yêu cầu column=None")
        if rt != RuleType.ROW_COUNT and not self.column:
            raise ValueError(f"{rt.value} yêu cầu column")

        if rt == RuleType.RANGE and p.min is None and p.max is None:
            raise ValueError(
                f"Rule RANGE yêu cầu ít nhất một trong min/max nhưng cả hai đều None "
                f"(column={self.column!r})"
            )
        if rt == RuleType.ACCEPTED_VALUES and not p.accepted_values:
            raise ValueError(
                f"Rule ACCEPTED_VALUES yêu cầu danh sách accepted_values không rỗng "
                f"(column={self.column!r})"
            )
        if rt == RuleType.REGEX_FORMAT and not p.regex:
            raise ValueError(
                f"Rule REGEX_FORMAT yêu cầu trường regex không rỗng "
                f"(column={self.column!r})"
            )
        if rt == RuleType.FRESHNESS and p.max_age_hours is None:
            raise ValueError("FRESHNESS yêu cầu max_age_hours")
        if rt == RuleType.ROW_COUNT and p.min_row_count is None:
            raise ValueError("ROW_COUNT yêu cầu min_row_count")
        if rt == RuleType.NULL_RATE and p.max_null_pct is None:
            raise ValueError("NULL_RATE yêu cầu max_null_pct")
        if rt == RuleType.CROSS_FIELD_COMPARISON and (
            p.target_column is None or p.operator is None
        ):
            raise ValueError(
                "Rule CROSS_FIELD_COMPARISON yêu cầu target_column và operator "
                f"không được None (column={self.column!r})"
            )

        active_parameters = {
            name for name, value in p.model_dump().items() if value is not None
        }
        provenance_parameters = {item.parameter_name for item in self.parameter_provenance}
        if active_parameters != provenance_parameters:
            raise ValueError(
                "parameter_provenance phải chứa đúng một entry cho mỗi parameter đang sử dụng"
            )

        return self

class TableRuleProposal(BaseModel):
    """Schema LLM trả về cho một bảng — one call per table."""

    model_config = ConfigDict(extra="forbid")

    table: str = Field(..., description="Tên bảng trong database.")
    rules: list[ProposedRule] = Field(
        default_factory=list,
        description="Danh sách các rule đề xuất cho bảng này.",
    )
