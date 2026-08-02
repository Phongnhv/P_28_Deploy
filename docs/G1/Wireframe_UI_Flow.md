# WIREFRAME & UI FLOW — RidePulse DQ (AutoDQ Agent)
### Autonomous Data Quality & Anomaly Intelligence Platform for Ride-Hailing Services

---

## 1. Tổng quan dự án & Bài toán nghiệp vụ

Trong dự án **RidePulse DQ (AutoDQ Agent) — Autonomous Data Quality & Anomaly Intelligence Platform**, Thành viên C chịu trách nhiệm toàn bộ phần **Wireframe & UI Flow**, thiết kế cấu trúc luồng thao tác người dùng, phác thảo giao diện Ant Design và lập trình bản Web Application Interactive Prototype.

### 1.1 Bối cảnh dữ liệu vận hành Ride-Hailing

Hệ thống xử lý 4 tập dữ liệu vận hành chính:
- `dich_vu_xe_trips` (chuyến đi)
- `dich_vu_xe_drivers` (tài xế)
- `dich_vu_xe_customers` (hành khách)
- `dich_vu_xe_payments` (thanh toán)

Dữ liệu thường xuyên mắc phải các lỗi dữ liệu bẩn (Bad Data) như:
- `NULL` ở khóa chính (`trip_id`, `driver_id`)
- Cước âm (`fare_amount < 0`)
- Vi phạm khoảng giá trị (outliers)
- Sai format timestamp
- Freshness lag (dữ liệu cập nhật trễ)

Giải pháp **AI Agent + HITL** giúp tự động hóa khâu đọc metadata, đề xuất rule, kiểm thử dbt và phát hiện bất thường bằng ML.

### 1.2 Tham chiếu kiến trúc kỹ thuật

Chi tiết danh sách công nghệ (LangGraph, dbt/Great Expectations, scikit-learn Isolation Forest/Z-score, Dagster, Postgres, Vector DB, FastAPI, React + Ant Design, Docker + Cloud Run) được Technical Lead chuẩn hóa đầy đủ tại **Brief Mục 7** và **PRD Mục 10**. Phần Wireframe chỉ tham chiếu các công nghệ liên quan trực tiếp đến trải nghiệm giao diện (LangGraph Agent, dbt test execution, Isolation Forest anomaly flag) khi mô tả từng màn hình bên dưới.

---

## 2. Phân tích vai trò người dùng & Phân quyền

### 2.1 Data Steward (Core Operator)
Có toàn quyền connect dataset, chạy profiling, duyệt AI rules (Approve / Reject / Edit), thực thi dbt test và xem chẩn đoán nguyên nhân gốc AI Root Cause Diagnosis.

### 2.2 Viewer (Data Analyst / Executive)
Giao diện Read-Only an toàn, chỉ xem Dashboard Data Health Score, các sự cố và báo cáo Trend Analysis mà không thể thao tác sửa đổi quy tắc.

### 2.3 Data Officer (Governance)
Vai trò giám sát tuân thủ, **không có màn hình đăng nhập riêng trong UI Flow** (không xuất hiện trong Screen 1 – Login & Role Selection). Data Officer giám sát phạm vi truy cập của Agent (chỉ đọc metadata, không đọc dữ liệu PII chi tiết) và theo dõi log audit hoàn toàn ngoài hệ thống UI — khớp với mô tả tại Brief Mục 3.3 và PRD Persona 3.

### 2.4 Bảng Ma trận Phân quyền (Role Matrix)

| Quyền hạn / Thao tác | 👨‍💻 Data Steward | 👁️ Viewer (Data Analyst / Executive) | 🛡️ Data Officer (Governance) |
| :--- | :---: | :---: | :---: |
| Kết nối Dataset & Profiling | ✅ | ❌ | ❌ |
| Duyệt AI Rules (HITL Review) | ✅ | ❌ | ❌ |
| Chỉnh sửa Rule Threshold / Severity | ✅ | ❌ | ❌ |
| Thực thi Test Suite (dbt / ML) | ✅ | ❌ | ❌ |
| Xem Dashboard Data Health Score | ✅ | ✅ | ❌ (giám sát qua audit log, ngoài UI) |
| Xem Chi tiết Anomaly & AI Diagnosis | ✅ | ✅ | ❌ (giám sát qua audit log, ngoài UI) |
| Xem Trend Analysis & ML Metrics | ✅ | ✅ | ❌ (giám sát qua audit log, ngoài UI) |
| Giám sát phạm vi truy cập metadata & audit log | — | — | ✅ |

---

## 3. UI Flow (Sơ đồ luồng đi người dùng)

Luồng thao tác được thiết kế mạch lạc qua 7 bước theo đúng yêu cầu đề bài:

```
[1. Đăng nhập (Login)] ➔ [2. Dashboard Tổng quan] ➔ [3. Select Dataset] ➔ [4. Agent Profiling & Rule Proposal]
                                                                                      │
[7. Detail Report — AI Diagnosis & Trend Analysis]  ⟵  [6. Test Execution & Anomaly Detection]  ⟵  [5. HITL Review (Chỉ Steward)]
```

1. **Đăng nhập (Login):** Chọn vai trò Steward hoặc Viewer.
2. **Dashboard Tổng quan:** Xem chỉ số Data Health Score chung (87.4% — số liệu minh họa demo).
3. **Select Dataset:** Chọn bảng dữ liệu vận hành `dich_vu_xe_trips`.
4. **Agent Profiling & Rule Proposal:** AI hiện gợi ý các Rule (Not-null, Range check, Format).
5. **HITL Review (Chỉ Steward):** Checkbox Duyệt / Sửa / Từ chối Rule trực quan.
6. **Test Execution & Anomaly Detection:** Hệ thống chạy test dbt & mô hình ML Isolation Forest.
7. **Detail Report — AI Diagnosis & Trend Analysis:** Xem chi tiết lỗi, chẩn đoán nguyên nhân AI Diagnosis và biểu đồ xu hướng.

---

## 4. Wireframe Layout — Khung màn hình chính

Thành viên C phác thảo các màn hình trọng tâm theo yêu cầu với phong cách Ant Design Admin Dashboard (Frame 1440px Desktop Grid). Ba nhóm màn hình trọng tâm dưới đây tương ứng trực tiếp với các Screen trong Mục 5:

- **Screen 5 — Rule Review Screen (HITL Review Table):** Bảng chứa danh sách Rule do AI tạo (Column Name, Rule Type, AI Reason, Confidence %, Suggested Threshold, Status: Pending/Approved/Rejected, Action buttons).
- **Screen 8 & 9 — Anomaly Dashboard & Alert Stream / AI Diagnosis Modal:** Biểu đồ Time-series hiển thị các điểm bất thường (Anomaly dots màu đỏ), bảng danh sách Alert đi kèm nút `🤖 AI Diagnosis` (nhấn vào hiện Modal giải thích nguyên nhân gốc rễ).
- **Screen 10 — Trend & Evaluation Screen:** Biểu đồ đường thể hiện chỉ số Data Quality Score theo tuần/tháng và các thông số Precision (94.2%), Recall (91.8%), F1-Score (93.0%) của mô hình ML — **số liệu minh họa demo**, không phải ngưỡng mục tiêu chính thức (xem Success Metrics tại Brief Mục 8 / PRD Mục 3.3).

---

## 5. Hồ sơ hình ảnh UI Prototype (Screen 1–11)

### Screen 1: Đăng nhập & Chọn Phân quyền (Login & Role Selection)
![Screen 1: Đăng nhập & Chọn Phân quyền](file:///c:/Users/ADMIN/WorkPlace/Vinuni/AssignmentProject/P-028/docs/images/screen_1_login.png)
- **📌 Phân tích UX/UI:** Màn hình đăng nhập chọn vai trò Data Steward hoặc Viewer.

---

### Screen 2: Steward Dashboard (Data Health Score 87.4% — số liệu minh họa demo)
![Screen 2: Steward Dashboard](file:///c:/Users/ADMIN/WorkPlace/Vinuni/AssignmentProject/P-028/docs/images/screen_2_steward_dashboard.png)
- **📌 Phân tích UX/UI:** Dashboard tổng quan với điểm số Data Health Score 87.4%.

---

### Screen 3: Catalog Lựa chọn Dataset (dich_vu_xe_trips)
![Screen 3: Catalog Lựa chọn Dataset](file:///c:/Users/ADMIN/WorkPlace/Vinuni/AssignmentProject/P-028/docs/images/screen_3_dataset_catalog.png)
- **📌 Phân tích UX/UI:** Danh mục tra cứu bảng dữ liệu vận hành gọi xe.

---

### Screen 4: Dataset Profiling & Metadata Insights
![Screen 4: Dataset Profiling & Metadata Insights](file:///c:/Users/ADMIN/WorkPlace/Vinuni/AssignmentProject/P-028/docs/images/screen_4_dataset_profiling.png)
- **📌 Phân tích UX/UI:** Kết quả AI Profiling phân tích Null %, Unique % và Outliers.

---

### Screen 5: Rule Review Screen (HITL Review Table)
![Screen 5: Rule Review Screen](file:///c:/Users/ADMIN/WorkPlace/Vinuni/AssignmentProject/P-028/docs/images/screen_5_ai_rule_proposals.png)
- **📌 Phân tích UX/UI:** Bảng HITL review chứa danh sách Rule do AI gợi ý với các nút Approve/Reject/Edit.

---

### Screen 6: Rule Edit Modal
![Screen 6: Rule Edit Modal](file:///c:/Users/ADMIN/WorkPlace/Vinuni/AssignmentProject/P-028/docs/images/screen_6_rule_edit_modal.png)
- **📌 Phân tích UX/UI:** Modal chỉnh sửa thông số Threshold, Severity và Mô tả rule.

---

### Screen 7: Running Tests & Streaming Console Log
![Screen 7: Running Tests & Streaming Console Log](file:///c:/Users/ADMIN/WorkPlace/Vinuni/AssignmentProject/P-028/docs/images/screen_7_execution_log.png)
- **📌 Phân tích UX/UI:** Chạy dbt test suite với Stepper 4 bước và Live Terminal Log.

---

### Screen 8: Anomaly Dashboard & Alert Stream
![Screen 8: Anomaly Dashboard & Alert Stream](file:///c:/Users/ADMIN/WorkPlace/Vinuni/AssignmentProject/P-028/docs/images/screen_8_anomaly_dashboard.png)
- **📌 Phân tích UX/UI:** Biểu đồ Time-Series gắn đốm đỏ Anomaly dots và bảng Alert có nút AI Diagnosis.

---

### Screen 9: AI Diagnosis Modal
![Screen 9: AI Diagnosis Modal](file:///c:/Users/ADMIN/WorkPlace/Vinuni/AssignmentProject/P-028/docs/images/screen_9_ai_diagnosis_modal.png)
- **📌 Phân tích UX/UI:** Modal giải thích nguyên nhân gốc do AI Agent chẩn đoán.

---

### Screen 10: Trend & Evaluation Screen
![Screen 10: Trend & Evaluation Screen](file:///c:/Users/ADMIN/WorkPlace/Vinuni/AssignmentProject/P-028/docs/images/screen_10_trend_analysis.png)
- **📌 Phân tích UX/UI:** Biểu đồ xu hướng 30 ngày và các chỉ số ML Metrics (Precision, Recall, F1) — số liệu minh họa demo.

---

### Screen 11: Executive Viewer Dashboard (Read-Only View)
![Screen 11: Executive Viewer Dashboard](file:///c:/Users/ADMIN/WorkPlace/Vinuni/AssignmentProject/P-028/docs/images/screen_11_viewer_dashboard.png)
- **📌 Phân tích UX/UI:** Giao diện Read-Only an toàn 100% dành cho Viewer.
