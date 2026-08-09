from typing import Any, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=5000, description="Tin nhắn từ user")


class ChatResponse(BaseModel):
    response: str = Field(..., description="Phản hồi từ agent")
    analysis: str = Field(default="", description="Phân tích nội bộ")


# ---------------------------------------------------------------------------
# DQ Proposal (Run 1) — POST /dq/propose
# ---------------------------------------------------------------------------

class ProposeRequest(BaseModel):
    dataset_id: str = Field(..., description="ID định danh dataset cần kiểm tra")
    connection_string: Optional[str] = Field(
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
    error: Optional[str] = None
    created_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Rule Review — GET /dq/rules
# ---------------------------------------------------------------------------

class RuleReviewResponse(BaseModel):
    id: int
    run_id: str
    dataset_id: str
    table_name: str
    column_name: Optional[str] = None
    rule_type: str
    parameters: dict[str, Any]
    edited_parameters: Optional[dict[str, Any]] = None
    effective_parameters: dict[str, Any]
    confidence_score: float
    severity: str
    ai_reasoning: str
    status: str  # PENDING / APPROVED / REJECTED
    reviewer: Optional[str] = None
    reviewed_at: Optional[str] = None
    created_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Rule Update — PATCH /dq/rules/{rule_id}
# ---------------------------------------------------------------------------

class RuleUpdateRequest(BaseModel):
    status: str = Field(..., description="APPROVED hoặc REJECTED")
    edited_parameters: Optional[dict[str, Any]] = Field(
        None,
        description="Tham số Steward chỉnh sửa (immutable AI params được giữ riêng)",
    )
    severity: Optional[str] = Field(
        None, description="Mức độ nghiêm trọng Steward muốn override"
    )
    reviewer: Optional[str] = Field(
        None, description="Tên / email Steward thực hiện review"
    )


# ---------------------------------------------------------------------------
# Bulk Review — POST /dq/rules/bulk-review
# ---------------------------------------------------------------------------

class BulkDecision(BaseModel):
    rule_id: int
    status: str  # APPROVED / REJECTED
    edited_parameters: Optional[dict[str, Any]] = None
    severity: Optional[str] = None
    reviewer: Optional[str] = None


class BulkReviewRequest(BaseModel):
    decisions: list[BulkDecision] = Field(
        ..., description="Danh sách quyết định duyệt/từ chối"
    )


class BulkReviewResponse(BaseModel):
    updated_count: int
    rules: list[RuleReviewResponse]


# ---------------------------------------------------------------------------
# Approved Rules — GET /dq/runs/{run_id}/approved-rules
# (input contract cho Test Generator)
# ---------------------------------------------------------------------------

class ApprovedRulesResponse(BaseModel):
    run_id: str
    count: int
    rules: list[RuleReviewResponse]

