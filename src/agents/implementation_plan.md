# Kế hoạch triển khai: Nâng cấp AI Agent sinh Quy tắc Logic Liên cột (CROSS_FIELD_COMPARISON)

Dựa trên phân tích kỹ thuật chuyên sâu, AI Agent hiện chưa thể sinh ra các quy tắc logic như `tpep_dropoff_datetime >= tpep_pickup_datetime` là do sự ngắt kết nối ở **tầng Pydantic Schema** và **Prompt Instructions**.

Kế hoạch này sẽ mở khóa Pydantic Schema, mở rộng Prompt với Few-Shot Example cụ thể, ưu tiên tín hiệu `cross_column_hints` trong Data Digest và cập nhật logic sinh `rule_id` để Agent tự động suy luận và đề xuất các quy tắc so sánh liên cột chuẩn xác.

---

## User Review Required

> [!IMPORTANT]
> - **Chuyển đổi Enum RuleType:** Thêm `CROSS_FIELD_COMPARISON` vào Enum `RuleType` và mở rộng `RuleParameters` với 2 trường optional `target_column` và `operator`.
> - **Thay đổi Prompt:** Bỏ câu lệnh cấm đoán LLM (giới hạn 8 rule đơn cột) và bổ sung 1 ví dụ Few-Shot chuẩn mực cho `CROSS_FIELD_COMPARISON`.
> - **Giữ nguyên Tính Tương Tự Backward:** Các rule đơn cột cũ (`NOT_NULL`, `RANGE`, `UNIQUE`...) vẫn hoạt động bình thường 100% không bị ảnh hưởng.

---

## Open Questions

Không có câu hỏi mở hóc hại. Tất cả các yêu cầu về logic liên cột đã được xác định rõ ràng qua bài test **E4** trong [TEAM_PLAN.md](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/docs/gate2-mvp/TEAM_PLAN.md).

---

## Proposed Changes

### Core AI Engine & Data Models

#### [MODIFY] [rule_schemas.py](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/src/models/rule_schemas.py)

1. Thêm `CROSS_FIELD_COMPARISON = "CROSS_FIELD_COMPARISON"` vào `RuleType(StrEnum)`.
2. Thêm 2 trường vào `RuleParameters`:
   ```python
   target_column: str | None = None  # Cột thứ 2 để so sánh (VD: "tpep_dropoff_datetime")
   operator: str | None = None       # Toán tử so sánh: "<=", ">=", "<", ">", "=="
   ```
3. Cập nhật `@model_validator(mode="after")` trong `ProposedRule`:
   - Khi `rule_type == RuleType.CROSS_FIELD_COMPARISON`, kiểm tra và yêu cầu bắt buộc `target_column` và `operator` không được `None`.

---

#### [MODIFY] [templates.py](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/src/agents/nodes/templates.py)

1. Cập nhật bảng `rule_type` trong `_RULE_PROPOSER_SYSTEM`:
   - Thêm định nghĩa cho `CROSS_FIELD_COMPARISON`: *"Áp dụng khi digest có `cross_column_hints` (như `datetime_order`) hoặc khi 2 cột có quan hệ thứ tự logic nghiệp vụ (như `pickup_datetime` <= `dropoff_datetime`)."*
   - Cập nhật câu nhắc nhở chấp nhận 9 giá trị `RuleType` (gồm cả `CROSS_FIELD_COMPARISON`).
   - Cập nhật hướng dẫn mapping `rule_type` $\rightarrow$ `dimension = CONSISTENCY` cho `CROSS_FIELD_COMPARISON`.
2. Bổ sung Few-Shot Example mẫu thứ 5 chuyên cho `CROSS_FIELD_COMPARISON` vào `_RULE_PROPOSER_FEW_SHOT`:
   ```json
   {
     "column": "tpep_pickup_datetime",
     "rule_type": "CROSS_FIELD_COMPARISON",
     "parameters": {
       "target_column": "tpep_dropoff_datetime",
       "operator": "<="
     },
     "severity": "CRITICAL",
     "dimension": "CONSISTENCY",
     "rule_description": "Thời điểm đón khách (tpep_pickup_datetime) phải xảy ra trước hoặc cùng lúc với thời điểm trả khách (tpep_dropoff_datetime).",
     "ai_reasoning": "Digest chỉ ra signal datetime_order giữa tpep_pickup_datetime và tpep_dropoff_datetime với 0% vi phạm. Về mặt nghiệp vụ taxi, hành khách luôn được đón trước khi trả, do đó tpep_pickup_datetime phải nhỏ hơn hoặc bằng tpep_dropoff_datetime."
   }
   ```

---

#### [MODIFY] [profile_digest.py](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/src/agents/tools/profile_digest.py)

1. Đưa `cross_column_hints` lên vị trí ưu tiên ngay đầu Dictionary của `table_digest` (ngay cạnh `table` và `rows`) để LLM chú ý ngay lập tức khi đọc JSON profile trong Prompt.

---

#### [MODIFY] [rule_proposer_node.py](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/src/agents/nodes/rule_proposer_node.py)

1. Cập nhật hàm `_stamp_rule`:
   - Nếu `rule_type == "CROSS_FIELD_COMPARISON"`, format `rule_id` dạng:
     `f"{table_name}.{col_key}.VS.{target_column}.{rule_type}"` để tránh đụng độ ID trong Database.

---

#### [MODIFY] [test_rule_proposer.py](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/tests/test_rule_proposer.py)

1. Bổ sung Unit Test mới kiểm thử việc parse và stamp rule `CROSS_FIELD_COMPARISON` từ LLM mock response.

---

## Verification Plan

### Automated Tests
- Chạy bộ kiểm thử tự động của Agent:
  ```powershell
  .\venv\Scripts\python.exe -m pytest tests/test_rule_proposer.py -v
  .\venv\Scripts\python.exe -m pytest tests/test_hitl_gate.py -v
  .\venv\Scripts\python.exe -m pytest tests/test_api/test_hitl_routes.py -v
  ```
- Kiểm tra linting và code style:
  ```powershell
  .\venv\Scripts\python.exe -m ruff check src tests
  ```

### Manual Verification
- Chạy standalone harness của `rule_proposer_node.py` để verify output JSON chứa rule `CROSS_FIELD_COMPARISON` cho `tpep_pickup_datetime` <= `tpep_dropoff_datetime`:
  ```powershell
  .\venv\Scripts\python.exe -m src.agents.nodes.rule_proposer_node
  ```
