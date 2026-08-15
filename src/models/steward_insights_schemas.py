from pydantic import BaseModel, Field


class StewardStructuredInsights(BaseModel):
    executive_dq_summary: str = Field(
        description="Đánh giá ngắn gọn, tổng quan tình hình sức khỏe dữ liệu dựa trên điểm DQ Score, Grade (A/B/C/D) và phân bổ chất lượng theo các chiều (Completeness, Validity, Uniqueness, Consistency, Freshness)."
    )
    failure_anomaly_drill_down: str = Field(
        description="Phân tích chi tiết các quy tắc bị lỗi (FAILED) hoặc cảnh báo bất thường (ANOMALY), đưa ra giả thuyết nguyên nhân gốc rễ (Potential Root Cause) và đánh giá mức độ rủi ro đối với downstream reports / business metrics."
    )
    rule_tuning_recommendations: str = Field(
        description="Nhận xét xem các quy tắc hiện tại có quá khắt khe (False positive) hay không và gợi ý điều chỉnh ngưỡng (threshold), bộ lọc (WHERE condition) hoặc bổ sung quy tắc mới."
    )
    steward_next_steps: list[str] = Field(
        description="Danh sách các hành động checklist công việc cần thực hiện tiếp theo dành cho Data Steward (ví dụ: review, duyệt/từ chối rule, nghiệm thu dataset)."
    )
    engineering_next_steps: list[str] = Field(
        description="Danh sách các hành động checklist công việc cần thực hiện tiếp theo dành cho Data Engineering / Source Team (ví dụ: kiểm tra pipeline, sửa source bug)."
    )
