from __future__ import annotations

from pydantic import BaseModel, Field


class InferredDictionaryColumn(BaseModel):
    name: str = Field(..., description="Tên cột vật lý trong database.")
    description: str = Field(
        "",
        description="Mô tả nghiệp vụ ngắn gọn bằng tiếng Việt của cột này dựa trên dữ liệu thực tế và gợi ý domain."
    )
    semantic_type: str = Field(
        "unknown",
        description="Kiểu dữ liệu ngữ nghĩa dự đoán (ví dụ: identifier, timestamp, category, currency, numeric, text, location, PII, v.v.)."
    )
    business_role: str = Field(
        "unknown",
        description="Vai trò nghiệp vụ tương ứng bằng tiếng Anh dạng snake_case (e.g., primary_key, created_at, customer_id, transaction_amount)."
    )
    nullable_expected: bool = Field(
        True,
        description="Có cho phép null hay không theo logic nghiệp vụ thông thường."
    )
    governance_notes: list[str] = Field(
        default_factory=list,
        description="Các lưu ý về quản trị dữ liệu nếu có (ví dụ: chứa PII, cần mã hóa, dữ liệu nhạy cảm, định dạng đặc biệt)."
    )

class InferredDictionaryTable(BaseModel):
    table_name: str = Field(..., description="Tên bảng nghiệp vụ.")
    description: str = Field(
        "",
        description="Mô tả tóm tắt ý nghĩa và mục đích nghiệp vụ của bảng bằng tiếng Việt."
    )
    columns: list[InferredDictionaryColumn] = Field(
        default_factory=list,
        description="Danh sách mô tả chi tiết của từng cột trong bảng."
    )
    business_rules: list[str] = Field(
        default_factory=list,
        description="Các quy tắc hoặc giả định nghiệp vụ tự suy luận được từ profile của bảng (ví dụ: order_date phải nhỏ hơn ship_date, v.v.)."
    )
