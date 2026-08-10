# Quy trình làm việc chi tiết của từng Sub-Agent (Sub-Agent Workflows)

Tài liệu này mô tả chi tiết quy trình xử lý (Workflow), dữ liệu đầu vào/đầu ra (Data Contract), công cụ sử dụng (Tools) và cơ chế rẽ nhánh/lặp (Agentic Loop) cho từng Sub-Agent trong hệ thống **RidePulse DQ**.

---

## 1. Profiler Sub-Agent (Agent Khảo sát Dữ liệu)

### 🎯 Mục tiêu
Tự động quét cấu trúc schema, phân tích thống kê phân phối dữ liệu (min, max, null rate, distinct count, data types) của dataset target (ví dụ: `dich_vu_xe_trips`) mà **không làm lộ dữ liệu nhạy cảm** (bảo đảm tính riêng tư & Governance).

### 📥 Đầu vào (Input)
* `dataset_id`: Mã định danh dataset (ví dụ: `dich_vu_xe_trips`).
* `connection_string`: Chuỗi kết nối Read-Only đến Target Data Warehouse (PostgreSQL / Snowflake / BigQuery).
* `sampling_rate`: Tỷ lệ lấy mẫu (mặc định: `100%` với bảng nhỏ hoặc `10%` với bảng >1M dòng).

### 🔄 Quy trình xử lý (Workflow)
```
[1. Tiếp nhận Task] ➔ [2. Thực thi SQL Profiling Tool] ➔ [3. Thu thập Metrics] ➔ [4. LLM Diễn giải Semantics] ➔ [5. Trả về Profile JSON]
```
1. **Trích xuất Metadata:** Gọi SQL Tool chạy các câu lệnh truy vấn metadata (`information_schema.columns`) lấy danh sách cột và kiểu dữ liệu.
2. **Tính toán chỉ số thống kê:** Chạy các câu lệnh tính toán thống kê song song (Aggregation Queries):
   - Đếm tổng số dòng (`total_rows`).
   - Tỷ lệ dữ liệu trống (`null_percentage`).
   - Số lượng giá trị duy nhất (`distinct_count`).
   - Giá trị lớn nhất / nhỏ nhất / trung bình / độ lệch chuẩn (`min`, `max`, `mean`, `stddev`).
3. **Phân tích ngữ nghĩa bằng LLM (Semantic Inference):** Truyền bản tóm tắt thống kê vào LLM để suy luận kiểu dữ liệu ngữ nghĩa (ví dụ: cột `driver_lat` với dải giá trị `[10.7, 10.8]` ➔ Nhận diện là `GEO_LATITUDE`).

### 🛠️ Tools sử dụng
* `DuckDB / AsyncPG Exec Tool`: Chạy SQL Profiling tốc độ cao.
* `Schema Inspector`: Đọc định dạng cột và chỉ mộc metadata.

### 📤 Đầu ra (Output)
* `dataset_profile_json`:
```json
{
  "dataset_name": "dich_vu_xe_trips",
  "total_rows": 4250000,
  "columns": {
    "fare_amount": { "type": "NUMERIC", "null_rate": 0.001, "min": 0, "max": 1500000, "mean": 85000 },
    "driver_id": { "type": "VARCHAR", "null_rate": 0.0, "distinct_count": 12500 },
    "trip_status": { "type": "VARCHAR", "distinct_values": ["COMPLETED", "CANCELLED", "IN_PROGRESS"] }
  }
}
```

---

## 2. Rule Proposer Sub-Agent (Agent Đề xuất Quy tắc Data Quality)

### 🎯 Mục tiêu
Dựa trên hồ sơ thống kê (`dataset_profile_json`) từ Profiler Agent và tri thức lịch sử từ Vector Database, suy luận và đề xuất bộ quy tắc chất lượng dữ liệu (Rules) kèm điểm tin cậy (Confidence Score) để trình cho Data Steward duyệt (HITL).

### 📥 Đầu vào (Input)
* `dataset_profile_json`: Kết quả từ Profiler Agent.
* `domain_context`: Ngữ nghĩa ngành (Ride-hailing / Gọi xe).

### 🔄 Quy trình xử lý (Workflow)
```
[1. Nhận Profile JSON] ➔ [2. RAG Search ChromaDB] ➔ [3. LLM Prompting & Reasoning] ➔ [4. Tạo Rule Specs] ➔ [5. Gửi sang HITL Node]
```
1. **Truy vấn Tri thức Lịch sử (RAG Search):** Query ChromaDB để tìm các rule tương tự từng được duyệt cho các dataset thuộc miền nghiệp vụ Gọi xe.
2. **LLM Suy luận Quy tắc (Reasoning Engine):** Đưa `dataset_profile_json` + kết quả RAG vào LLM Prompt để sinh ra các quy tắc theo 5 nhóm chính:
   - *Not-Null Check:* Kiểm tra cột bắt buộc không được trống.
   - *Range Check:* Giá trị nằm trong khoảng hợp lý (ví dụ: `fare_amount >= 0` và `<= 2,000,000`).
   - *Uniqueness Check:* Đảm bảo tính duy nhất (`trip_id`).
   - *Format Check:* Định dạng Email, SĐT, Tọa độ.
   - *Enum Check:* Danh mục hợp lệ (`trip_status IN ('COMPLETED', 'CANCELLED')`).
3. **Gán Confidence Score & AI Reason:** Mỗi rule đi kèm điểm tin cậy (%) và lý do đề xuất.
4. **Tạm dừng chờ Duyệt (HITL Breakpoint):** Chuyển toàn bộ danh sách đề xuất lên màn hình **Rule Review Screen** để Data Steward bấm `Approve`, `Reject` hoặc `Edit`.

### 🛠️ Tools sử dụng
* `ChromaDB Vector RAG Tool`: Tìm kiếm quy tắc tương tự trong quá khứ.
* `Rule Formatter Tool`: Chuẩn hóa định dạng JSON Rule Specification.

### 📤 Đầu ra (Output)
* `proposed_rules_list`:
```json
[
  {
    "rule_id": "RULE_001",
    "column": "fare_amount",
    "rule_type": "RANGE_CHECK",
    "parameters": { "min": 0, "max": 2000000 },
    "confidence_score": 0.95,
    "ai_reason": "Dữ liệu lịch sử 4.2M dòng không có cước phí âm và max dưới 1.5M VND."
  }
]
```

---

## 3. Test Generator Sub-Agent (Agent Sinh Mã & Chạy Test)

### 🎯 Mục tiêu
Chuyển đổi bộ quy tắc đã được Data Steward phê duyệt (`approved_rules`) thành mã kiểm thử thực tế (`dbt test` YAML / SQL Macros / Great Expectations), tự động kiểm tra cú pháp và đẩy sang **Dagster Orchestrator** để thực thi.

### 📥 Đầu vào (Input)
* `approved_rules`: Danh sách các quy tắc đã được Data Steward duyệt từ HITL Node.
* `target_framework`: Khung kiểm thử (`dbt-core` hoặc `great_expectations`).

### 🔄 Quy trình xử lý (Workflow)
```
[1. Nhận Approved Rules] ➔ [2. Sinh Mã dbt/GX Code] ➔ [3. Kiểm tra Cú pháp (Syntax Check)] ➔ [4. Agentic Loop Fix Cú pháp nếu lỗi] ➔ [5. Đẩy sang Dagster]
```
1. **Biên dịch Quy tắc (Code Rendering):** Chuyển đổi thông số JSON của từng rule thành file cấu hình YAML/SQL (ví dụ: `schema.yml` cho dbt).
2. **Kiểm tra Cú pháp Tự động (Syntax Validator):** Chạy lệnh `dbt parse` hoặc dry-run để xác nhận file code không bị lỗi cú pháp.
3. **Vòng lặp tự sửa lỗi (Agentic Loop):** Nếu cú pháp bị lỗi (ví dụ sai thụt lùi YML hoặc thiếu dấu ngoặc SQL):
   - Catch traceback error.
   - Gửi lại error cho LLM để tự sửa lỗi syntax code.
   - Kiểm thử lại cho đến khi pass (tối đa 3 lần).
4. **Triển khai Pipeline sang Dagster:** Ghi file test vào thư mục DAGs của **Dagster** và kích hoạt (Trigger) pipeline chạy test theo lịch định kỳ.

### 🛠️ Tools sử dụng
* `Jinja2 Code Generator`: Generator sinh mã dbt/GX.
* `dbt CLI / Parser Tool`: Thẩm định cú pháp file `.yml` và `.sql`.
* `Dagster GraphQL API Client`: Đẩy DAGs và trigger pipeline chạy tự động.

### 📤 Đầu ra (Output)
* `generated_code_files`: File `schema.yml` / `dbt_project` sẵn sàng thực thi.
* `dagster_run_id`: Mã phiên chạy pipeline trên Dagster.

---

## 4. Anomaly & Diagnosis Sub-Agent (Agent Phát hiện Bất thường & Chẩn đoán)

### 🎯 Mục tiêu
Phát hiện các điểm bất thường dữ liệu theo thời gian (Time-series Anomaly) bằng mô hình Machine Learning (`Isolation Forest` / `Z-score`) và dùng LLM kết hợp RAG để **chẩn đoán nguyên nhân gốc rễ (Root Cause Diagnosis)** kèm đề xuất câu lệnh SQL khắc phục.

### 📥 Đầu vào (Input)
* `test_execution_logs`: Kết quả và log chạy test từ Dagster.
* `time_series_metrics`: Chuỗi số liệu chỉ số Data Quality theo thời gian.

### 🔄 Quy trình xử lý (Workflow)
```
[1. Chạy ML Outlier Tool] ➔ [2. Nhận diện Anomaly] ➔ [3. RAG Search Log & Code Commit] ➔ [4. LLM Root Cause Diagnosis] ➔ [5. Gửi Notification Alert]
```
1. **Phát hiện Anomaly bằng ML Tool:**
   - Đưa chuỗi số liệu Data Quality Score theo ngày vào mô hình `scikit-learn IsolationForest` hoặc `Z-score`.
   - Đánh dấu các điểm dị biệt nằm ngoài phân phối bình thường (Anomaly Dots).
2. **Chẩn đoán Nguyên nhân Gốc rễ (Root Cause Reasoning):**
   - Khi có điểm bất thường, Agent thu thập: Log lỗi chi tiết từ Dagster, thông tin schema bị thay đổi, lịch sử deploy dịch vụ gần nhất.
   - Query ChromaDB để tìm các case lỗi tương tự trong quá khứ.
   - Gọi LLM phân tích và kết luận nguyên nhân (Ví dụ: *"Do microservice `promo_v2` trả về mã giảm giá bị nát dẫn đến `fare_amount = NULL`"*).
3. **Đề xuất Fix Script & Gửi Cảnh báo:**
   - Sinh câu lệnh SQL/dbt để cô lập hoặc khôi phục dòng dữ liệu lỗi.
   - Gọi **Alert Notification Service** gửi tin nhắn cảnh báo tới Slack/Email của Data Engineer.

### 🛠️ Tools sử dụng
* `scikit-learn IsolationForest Tool`: Thuật toán ML phát hiện điểm dị biệt.
* `Log Analyzer & ChromaDB RAG Tool`: Truy vấn tri thức lỗi & log lịch sử.
* `Slack Webhook / Email Alert Tool`: Bắn thông báo sự cố tức thời.

### 📤 Đầu ra (Output)
* `anomaly_report`:
```json
{
  "anomaly_detected": true,
  "metric": "null_rate_fare_amount",
  "detected_at": "2026-08-03T10:00:00Z",
  "severity": "CRITICAL",
  "root_cause_diagnosis": "Dịch vụ thanh toán promo_v2 bị timeout dẫn đến 15% số dòng fare_amount nhận giá trị NULL.",
  "recommended_fix_sql": "UPDATE dich_vu_xe_trips SET fare_amount = base_fare WHERE fare_amount IS NULL AND created_at >= '2026-08-03';"
}
```

---

## 5. Bảng tóm tắt so sánh 4 Sub-Agent

| Sub-Agent | Nhiệm vụ cốt lõi | Công cụ chính (Tools) | Vai trò của LLM |
| :--- | :--- | :--- | :--- |
| **1. Profiler Agent** | Quét Schema & Tính thống kê | DuckDB, AsyncPG SQL Exec | Diễn giải kiểu dữ liệu ngữ nghĩa (Semantic Types) |
| **2. Rule Proposer Agent** | Đề xuất quy tắc Data Quality | ChromaDB Vector Store | Suy luận logic kinh doanh & gán điểm tin cậy |
| **3. Test Generator Agent** | Sinh code dbt & Đẩy sang Dagster | dbt CLI, Dagster API | Tự động lặp lại sửa lỗi cú pháp code (Agentic Loop) |
| **4. Anomaly & Diagnosis Agent** | Phát hiện bất thường & Chẩn đoán | IsolationForest (scikit-learn), Slack Alert | Phân tích nguyên nhân gốc rễ & đề xuất SQL fix script |
