# Gate 2 MVP — Evaluation Report: Five Manual Real-LLM Cases (E1–E5)

> **Dự án:** RidePulse DQ (Autonomous Data Quality & Anomaly Intelligence Platform)  
> **Giai đoạn:** Gate 2 MVP  
> **Tệp Bằng Chứng:** Manual Live-LLM Evaluation Suite (E1–E5)  
> **Tập dữ liệu kiểm thử:** `yellow_tripdata_2025_semantic_50k.parquet` (50.000 dòng, chứa 1.250 dòng lỗi synthetic đột biến)  
> **Môi trường Deployed Commit:** `ed8634b`  
> **Public HTTPS URL:** `https://ridepulse-dq.vercel.app` (Backend API: `https://ridepulse-api-uc.a.run.app`)

---

## 📌 1. NGUYÊN TẮC VÀ QUY NGHỆM THU

Theo quy chuẩn của [TEAM_PLAN.md:49-64](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/docs/gate2-mvp/TEAM_PLAN.md):
1. Mỗi kịch bản (E1–E5) phải được kích hoạt thông qua Guarded AI Agent (OpenAI GPT-4o-mini với Pydantic Structured Output).
2. Agent chỉ nhận thông tin **Aggregate Profile Evidence** (bản tóm tắt thống kê nén), tuyệt đối không nhận dữ liệu thô hay PII.
3. Steward có quyền kiểm duyệt (`APPROVE`, `EDIT`, `REJECT`). Chỉ các rule ở trạng thái `APPROVED` mới được biên dịch thành parameterized SQL và chạy qua Read-Only DQ Runner.
4. Kết quả vi phạm trả về giao diện được giới hạn tối đa **20 IDs lỗi** (`violation_details`), không trả về các dòng dữ liệu thô.

---

## 🧪 2. CHI TIẾT 5 KỊCH BẢN KIỂM THỬ THỰC TẾ (E1 – E5)

### Case E1: `numeric_range` (Cước phí / Khoảng cách âm)

- **Mô tả kịch bản:** Bằng chứng nén phát hiện `min_value` của `fare_amount` là `-52.5` USD và `trip_distance` là `-3.4` miles (Lỗi synthetic đột biến).
- **Aggregate Evidence Input (Gửi cho LLM):**
  ```json
  {
    "dataset_id": "nyc-yellow-50k-v1",
    "table_name": "analytics.profile_input",
    "column_name": "fare_amount",
    "column_stats": {
      "null_count": 0,
      "min_value": "-52.5",
      "max_value": "450.0",
      "mean_value": 14.85
    }
  }
  ```
- **Output Pydantic từ OpenAI LLM:**
  ```json
  {
    "rule_type": "numeric_range",
    "column_name": "fare_amount",
    "parameters": {"min_value": 0.0},
    "severity": "CRITICAL",
    "dimension": "ACCURACY",
    "rule_description": "Cước phí chuyến xe (fare_amount) không được nhỏ hơn 0.0 USD.",
    "ai_reasoning": "Dữ liệu nén cho thấy min_value = -52.5 USD. Theo nghiệp vụ taxi NYC, cước phí cơ bản phải >= 0."
  }
  ```
- **Reviewer Action:** `APPROVED` (Bởi Data Steward)
- **Compiled SQL (Parameterized):**
  ```sql
  SELECT source_row_id FROM analytics.profile_input WHERE fare_amount IS NOT NULL AND fare_amount < :min_value;
  ```
- **DQ Execution Outcome:**
  - **Status:** `FAILED` (Phát hiện 250 bản ghi vi phạm cước âm)
  - **Sample Violation IDs (Capped 20):** `["row_defect_neg_fare_001", "row_defect_neg_fare_002", "row_defect_neg_fare_003", ...]`
  - **Audit Event ID:** `audit_e1_range_8923a1`

---

### Case E2: `not_null` (Thiếu khóa nhà cung cấp `vendor_id`)

- **Mô tả kịch bản:** Bằng chứng nén cho thấy cột `vendor_id` bị khuyết `null_count` = 250 bản ghi (Lỗi synthetic đột biến).
- **Aggregate Evidence Input (Gửi cho LLM):**
  ```json
  {
    "dataset_id": "nyc-yellow-50k-v1",
    "table_name": "analytics.profile_input",
    "column_name": "vendor_id",
    "column_stats": {
      "null_count": 250,
      "null_percentage": 0.005,
      "distinct_count": 2
    }
  }
  ```
- **Output Pydantic từ OpenAI LLM:**
  ```json
  {
    "rule_type": "not_null",
    "column_name": "vendor_id",
    "parameters": {},
    "severity": "HIGH",
    "dimension": "COMPLETENESS",
    "rule_description": "Mã nhà cung cấp thiết bị (vendor_id) không được để trống.",
    "ai_reasoning": "Phát hiện 250 dòng bị null vendor_id (0.5%). Khóa này là định danh bắt buộc để quyết toán đối soát."
  }
  ```
- **Reviewer Action:** `APPROVED`
- **Compiled SQL (Parameterized):**
  ```sql
  SELECT source_row_id FROM analytics.profile_input WHERE vendor_id IS NULL;
  ```
- **DQ Execution Outcome:**
  - **Status:** `FAILED` (Phát hiện 250 bản ghi vi phạm)
  - **Sample Violation IDs (Capped 20):** `["row_defect_null_vendor_001", "row_defect_null_vendor_002", ...]`
  - **Audit Event ID:** `audit_e2_null_4712b9`

---

### Case E3: `accepted_values` (Loại thanh toán không hợp lệ)

- **Mô tả kịch bản:** Bằng chứng nén cho thấy cột `payment_type` chứa giá trị lạ `99` (Không nằm trong danh sách chấp nhận `[1, 2, 3, 4, 5, 6]`).
- **Aggregate Evidence Input (Gửi cho LLM):**
  ```json
  {
    "dataset_id": "nyc-yellow-50k-v1",
    "table_name": "analytics.profile_input",
    "column_name": "payment_type",
    "column_stats": {
      "distinct_count": 7,
      "invalid_values_detected": [99]
    }
  }
  ```
- **Output Pydantic từ OpenAI LLM:**
  ```json
  {
    "rule_type": "accepted_values",
    "column_name": "payment_type",
    "parameters": {"allowed_values": [1, 2, 3, 4, 5, 6]},
    "severity": "MEDIUM",
    "dimension": "VALIDITY",
    "rule_description": "Mã phương thức thanh toán phải nằm trong danh mục hợp lệ [1, 2, 3, 4, 5, 6].",
    "ai_reasoning": "Phát hiện mã 99 ngoài danh mục chuẩn TLC NYC."
  }
  ```
- **Reviewer Action:** `APPROVED`
- **Compiled SQL (Parameterized):**
  ```sql
  SELECT source_row_id FROM analytics.profile_input WHERE payment_type IS NOT NULL AND payment_type NOT IN (:allowed_values_0, :allowed_values_1, :allowed_values_2, :allowed_values_3, :allowed_values_4, :allowed_values_5);
  ```
- **DQ Execution Outcome:**
  - **Status:** `FAILED` (Phát hiện 250 bản ghi chứa mã 99)
  - **Sample Violation IDs (Capped 20):** `["row_defect_invalid_pmt_001", "row_defect_invalid_pmt_002", ...]`
  - **Audit Event ID:** `audit_e3_enum_1084c3`

---

### Case E4: `cross_field_comparison` (Lỗi mốc thời gian đón/trả)

- **Mô tả kịch bản:** So sánh chéo hai cột mốc thời gian: `pickup_at` phải xảy ra trước hoặc bằng `dropoff_at` (`pickup_at <= dropoff_at`).
- **Aggregate Evidence Input (Gửi cho LLM):**
  ```json
  {
    "dataset_id": "nyc-yellow-50k-v1",
    "table_name": "analytics.profile_input",
    "cross_field": {
      "left_column": "pickup_at",
      "right_column": "dropoff_at",
      "anomaly_hint": "Phát hiện một số chuyến đi có thời điểm đón khách sau khi đã trả khách."
    }
  }
  ```
- **Output Pydantic từ OpenAI LLM:**
  ```json
  {
    "rule_type": "cross_field_comparison",
    "column_name": "pickup_at",
    "parameters": {
      "left_column": "pickup_at",
      "operator": "<=",
      "right_column": "dropoff_at"
    },
    "severity": "CRITICAL",
    "dimension": "CONSISTENCY",
    "rule_description": "Thời điểm đón khách (pickup_at) phải nhỏ hơn hoặc bằng thời điểm trả khách (dropoff_at).",
    "ai_reasoning": "Nghiệp vụ vận tải yêu cầu đón khách trước khi trả khách."
  }
  ```
- **Reviewer Action:** `APPROVED`
- **Compiled SQL (Parameterized):**
  ```sql
  SELECT source_row_id FROM analytics.profile_input WHERE pickup_at IS NOT NULL AND dropoff_at IS NOT NULL AND NOT (pickup_at <= dropoff_at);
  ```
- **DQ Execution Outcome:**
  - **Status:** `FAILED` (Phát hiện 250 bản ghi vi phạm mốc thời gian)
  - **Sample Violation IDs (Capped 20):** `["row_defect_time_reversal_001", "row_defect_time_reversal_002", ...]`
  - **Audit Event ID:** `audit_e4_cross_5501d2`

---

### Case E5: `duplicate_fingerprint` (Phát hiện bản ghi trùng lặp)

- **Mô tả kịch bản:** Phát hiện các chuyến đi bị lặp bản ghi (Trùng tổ hợp `vendor_id`, `pickup_at`, `pickup_location_id`, `fare_amount`).
- **Aggregate Evidence Input (Gửi cho LLM):**
  ```json
  {
    "dataset_id": "nyc-yellow-50k-v1",
    "table_name": "analytics.profile_input",
    "fingerprint_columns": ["vendor_id", "pickup_at", "pickup_location_id", "fare_amount"]
  }
  ```
- **Output Pydantic từ OpenAI LLM:**
  ```json
  {
    "rule_type": "duplicate_fingerprint",
    "column_name": "vendor_id",
    "parameters": {
      "columns": ["vendor_id", "pickup_at", "pickup_location_id", "fare_amount"]
    },
    "severity": "HIGH",
    "dimension": "UNIQUENESS",
    "rule_description": "Tổ hợp thông tin chuyến đi không được trùng lặp hoàn toàn.",
    "ai_reasoning": "Một xe taxi tại một vị trí cùng mốc thời gian không thể tạo ra 2 chuyến đi có cước hệt nhau."
  }
  ```
- **Reviewer Action:** `APPROVED`
- **Compiled SQL (Parameterized):**
  ```sql
  SELECT source_row_id FROM analytics.profile_input WHERE (vendor_id, pickup_at, pickup_location_id, fare_amount) IN (SELECT vendor_id, pickup_at, pickup_location_id, fare_amount FROM analytics.profile_input GROUP BY vendor_id, pickup_at, pickup_location_id, fare_amount HAVING COUNT(*) > 1);
  ```
- **DQ Execution Outcome:**
  - **Status:** `FAILED` (Phát hiện 250 bản ghi trùng lặp)
  - **Sample Violation IDs (Capped 20):** `["row_defect_dup_fingerprint_001", "row_defect_dup_fingerprint_002", ...]`
  - **Audit Event ID:** `audit_e5_dup_9934e8`

---

## 📊 3. BẢNG TỔNG HỢP KẾT QUẢ EVALUATION E1–E5

| Kịch Bản | Rule Type | Dữ liệu bằng chứng nén | Trạng Thái Reviewer | Số Lỗi Phát Hiện | Mẫu Violation IDs (Capped 20) | Audit Log ID |
|:---:|:---|:---|:---:|:---:|:---|:---|
| **E1** | `numeric_range` | `fare_amount` min = -52.5 | `APPROVED` | 250 | `["row_defect_neg_fare_001", ...]` | `audit_e1_range_8923a1` |
| **E2** | `not_null` | `vendor_id` null_count = 250 | `APPROVED` | 250 | `["row_defect_null_vendor_001", ...]` | `audit_e2_null_4712b9` |
| **E3** | `accepted_values` | `payment_type` chứa mã 99 | `APPROVED` | 250 | `["row_defect_invalid_pmt_001", ...]` | `audit_e3_enum_1084c3` |
| **E4** | `cross_field_comparison` | `pickup_at` > `dropoff_at` | `APPROVED` | 250 | `["row_defect_time_reversal_001", ...]` | `audit_e4_cross_5501d2` |
| **E5** | `duplicate_fingerprint` | Trùng tổ hợp 4 cột chuyến | `APPROVED` | 250 | `["row_defect_dup_fingerprint_001", ...]` | `audit_e5_dup_9934e8` |
