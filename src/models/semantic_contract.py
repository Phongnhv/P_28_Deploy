from __future__ import annotations

from pydantic import BaseModel, Field


class SemanticColumn(BaseModel):
    name: str = Field(..., description="Tên cột vật lý trong database.")
    semantic_type: str = Field(
        ...,
        description="Kiểu dữ liệu ngữ nghĩa (ví dụ: identifier, timestamp, category, currency, numeric, text, location, PII, v.v.).",
    )
    business_role: str = Field(
        ..., description="Vai trò nghiệp vụ tương ứng (ví dụ: primary_key, transaction_amount, created_at, v.v.)."
    )
    nullable_expected: bool = Field(
        default=True, description="Liệu cột này có được phép mang giá trị trống/null theo logic nghiệp vụ không."
    )
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Độ tin cậy của dự đoán phân tích cột.")
    description: str | None = Field(None, description="Mô tả nghiệp vụ ngắn gọn bằng tiếng Việt của cột.")


class SemanticRelationship(BaseModel):
    left_column: str = Field(..., description="Cột vế trái.")
    operator: str = Field(..., description="Toán tử so sánh (<=, <, =, >, >=, !=).")
    right_column: str = Field(..., description="Cột vế phải.")
    description: str | None = Field(None, description="Mô tả mối quan hệ nghiệp vụ bằng tiếng Việt.")


class TableSemanticContract(BaseModel):
    table_name: str = Field(..., description="Tên bảng nghiệp vụ.")
    domain: str = Field(..., description="Lĩnh vực nghiệp vụ dự đoán của bảng (ví dụ: e-commerce, IoT, healthcare).")
    table_purpose: str = Field(..., description="Mục đích nghiệp vụ của bảng (bằng tiếng Việt).")
    columns: list[SemanticColumn] = Field(default_factory=list)
    relationships: list[SemanticRelationship] = Field(default_factory=list)
    business_assumptions: list[str] = Field(
        default_factory=list, description="Các giả định nghiệp vụ được rút ra từ bảng."
    )


class DatasetSemanticContract(BaseModel):
    dataset_id: str = Field(..., description="ID của dataset.")
    tables: dict[str, TableSemanticContract] = Field(
        default_factory=dict, description="Bản đồ các bảng và contract ngữ nghĩa tương ứng."
    )
