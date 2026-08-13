from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# DQ Proposal (Run 1) — POST /dq/propose
# ---------------------------------------------------------------------------

class ProposeRequest(BaseModel):
    dataset_id: str = Field(..., description="ID định danh dataset cần kiểm tra")
    connection_string: str | None = Field(
        None,
        description="Connection string DB. Nếu None, dùng DATABASE_URL từ cấu hình.",
    )
    sampling_rate: float = Field(
        default=1.0,
        ge=0.01,
        le=1.0,
        description="Tỷ lệ lấy mẫu (0.01–1.0). Mặc định 1.0 = toàn bộ.",
    )


class ProposeResponse(BaseModel):
    run_id: str = Field(..., description="Batch key dùng để poll và lọc rules")
    status: str = Field(..., description="QUEUED khi mới tạo")


# ---------------------------------------------------------------------------
# Run Status — GET /dq/runs/{run_id}
# ---------------------------------------------------------------------------

class RunStatusResponse(BaseModel):
    run_id: str
    dataset_id: str
    status: str  # QUEUED / RUNNING / DONE / FAILED
    error: str | None = None
    created_at: str | None = None


# ---------------------------------------------------------------------------
# Rule Review — GET /dq/runs/{run_id}/rules
# ---------------------------------------------------------------------------

class RuleReviewResponse(BaseModel):
    run_id: str
    rule_id: str
    dataset_id: str
    table_name: str
    column: str | None = None
    rule_type: str
    parameters: dict[str, Any]
    edited_parameters: dict[str, Any] | None = None
    effective_parameters: dict[str, Any]
    confidence_score: float
    severity: str
    dimension: str
    rule_description: str
    ai_reasoning: str
    status: str  # PENDING / APPROVED / REJECTED
    reviewer: str | None = None
    review_note: str | None = None
    reviewed_at: str | None = None
    created_at: str | None = None


# ---------------------------------------------------------------------------
# Rule Update — PATCH /dq/runs/{run_id}/rules/{rule_id}
# ---------------------------------------------------------------------------

class RuleUpdateRequest(BaseModel):
    status: Literal["APPROVED", "REJECTED"] = Field(
        ..., description="APPROVED hoặc REJECTED"
    )
    edited_parameters: dict[str, Any] | None = Field(
        None,
        description="Tham số Steward chỉnh sửa (immutable AI params được giữ riêng)",
    )
    severity: str | None = Field(
        None, description="Mức độ nghiêm trọng Steward muốn override"
    )
    reviewer: str | None = Field(
        None, description="Tên / email Steward thực hiện review"
    )
    review_note: str | None = Field(
        None, description="Lý do reject — bắt buộc khi status=REJECTED"
    )

    @model_validator(mode="after")
    def _check_review_note_on_reject(self) -> "RuleUpdateRequest":
        if self.status == "REJECTED" and not self.review_note:
            raise ValueError("review_note là bắt buộc khi status=REJECTED")
        return self


# ---------------------------------------------------------------------------
# Bulk Review — POST /dq/runs/{run_id}/rules/bulk-review
# ---------------------------------------------------------------------------

class BulkDecision(BaseModel):
    rule_id: str
    status: Literal["APPROVED", "REJECTED"]
    edited_parameters: dict[str, Any] | None = None
    severity: str | None = None
    reviewer: str | None = None
    review_note: str | None = None


class BulkReviewRequest(BaseModel):
    decisions: list[BulkDecision] = Field(
        ..., description="Danh sách quyết định duyệt/từ chối"
    )


class BulkReviewResponse(BaseModel):
    updated_count: int
    rules: list[RuleReviewResponse]
    not_found: list[str] = Field(
        default_factory=list,
        description="Danh sách rule_id không tìm thấy trong run này",
    )


# ---------------------------------------------------------------------------
# Review Summary — GET /dq/runs/{run_id}/review-summary
# ---------------------------------------------------------------------------

class DimensionCounts(BaseModel):
    total: int
    pending: int
    approved: int
    rejected: int


class ReviewSummaryResponse(BaseModel):
    total: int
    pending: int
    approved: int
    rejected: int
    edited: int
    is_complete: bool = Field(
        ..., description="True khi pending=0 và total>0 — tất cả rule đã được review"
    )
    by_dimension: dict[str, DimensionCounts]
    by_severity: dict[str, DimensionCounts]


# ---------------------------------------------------------------------------
# Approved Rules — GET /dq/runs/{run_id}/approved-rules
# (input contract cho Test Generator)
# ---------------------------------------------------------------------------

class ApprovedRulesResponse(BaseModel):
    run_id: str
    count: int
    rules: list[RuleReviewResponse]


# ---------------------------------------------------------------------------
# Test Execution Schemas — Run 2
# ---------------------------------------------------------------------------

class ExecuteTestsResponse(BaseModel):
    test_run_id: str
    status: str = "QUEUED"


class TestRunStatusResponse(BaseModel):
    test_run_id: str
    dataset_id: str
    status: str
    error: str | None = None
    created_at: str | None = None


class TestResultResponse(BaseModel):
    test_run_id: str
    rule_id: str
    table_name: str
    column: str | None = None
    rule_type: str
    status: str
    violation_count: int
    total_rows: int
    violation_rate: float
    sample_failures: list[dict[str, Any]] | None = None
    sql_text: str
    duration_ms: float
    error: str | None = None
    created_at: str | None = None


class TestResultsListResponse(BaseModel):
    test_run_id: str
    count: int
    results: list[TestResultResponse]


# ---------------------------------------------------------------------------
# Active Rules & Publish Schemas
# ---------------------------------------------------------------------------

class PublishRulesResponse(BaseModel):
    run_id: str
    published_count: int
    message: str


class ActiveRuleResponse(BaseModel):
    rule_id: str
    dataset_id: str
    table_name: str
    column: str | None = None
    rule_type: str
    parameters: dict[str, Any]
    severity: str
    dimension: str
    rule_description: str
    status: str  # ACTIVE / INACTIVE
    last_run_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ActiveRulesListResponse(BaseModel):
    total_rules: int
    rules: list[ActiveRuleResponse]


class ExecuteActiveTestsRequest(BaseModel):
    dataset_id: str = Field(default="all", description="ID định danh dataset hoặc 'all'")
    table_name: str | None = Field(default=None, description="Lọc theo bảng cụ thể")


