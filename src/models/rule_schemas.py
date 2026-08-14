"""Pydantic v2 schemas cho Rule Proposer structured output.

Critical: RuleParameters dùng closed model (không dùng bare dict) để
with_structured_output() tạo ra JSON Schema hợp lệ cho Mistral / OpenAI.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Literal

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
    confidence_score: float = Field(..., ge=0.0, le=1.0)
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

    @model_validator(mode="after")
    def _validate_parameters(self) -> ProposedRule:
        """Guardrail: kiểm tra từng rule_type có đủ tham số bắt buộc không."""
        rt = self.rule_type
        p = self.parameters

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
        if rt == RuleType.CROSS_FIELD_COMPARISON and (
            p.target_column is None or p.operator is None
        ):
            raise ValueError(
                "Rule CROSS_FIELD_COMPARISON yêu cầu target_column và operator "
                f"không được None (column={self.column!r})"
            )
        return self


# ---------------------------------------------------------------------------
# Top-level schema the LLM is bound to — one call per table
# ---------------------------------------------------------------------------

class TableRuleProposal(BaseModel):
    """Schema LLM trả về cho một bảng — one call per table."""

    model_config = ConfigDict(extra="forbid")

    table: str = Field(..., description="Tên bảng trong database.")
    rules: list[ProposedRule] = Field(
        default_factory=list,
        min_length=2,
        max_length=5,
        description="Danh sách các rule đề xuất cho bảng này.",
    )
