  # PRODUCT BRIEF — RidePulse DQ (AutoDQ Agent)
### Autonomous Data Quality & Anomaly Intelligence Platform for Ride-Hailing Services

---

## Nhóm thực hiện & Phân công vai trò

| Thành viên | Vai trò | Nhiệm vụ chính |
| :--- | :--- | :--- |
| Vũ Nguyễn Quốc Đạt Đạt - A | Product Lead / Architect | Viết Product Brief, quản lý tiến độ, tổng hợp file nộp cuối |
| Lương Trung Chiến - B | Product Owner / Business Analyst (PO/BA) | Xây dựng PRD (Features, User Stories, Scope) |
| Nguyễn Hoàng Vĩnh Phong - C | UI/UX Lead & Flow Specialist | Thiết kế Wireframe & UI Flow (Figma/Ant Design) |
| Nguyễn Hữu Kiên - D | Technical Lead | Chuẩn hóa kiến trúc kỹ thuật, hỗ trợ B viết Tech Scope và hỗ trợ C hình dung luồng UI dữ liệu |

---

## 1. Bối cảnh & Bài toán nghiệp vụ

Dữ liệu vận hành dịch vụ gọi xe (Ride-Hailing) gồm 4 tập dữ liệu chính: `dich_vu_xe_trips` (chuyến đi), `dich_vu_xe_drivers` (tài xế), `dich_vu_xe_customers` (khách hàng), `dich_vu_xe_payments` (thanh toán). Dữ liệu có quy mô lớn, tăng trưởng liên tục và thường xuyên mắc lỗi "bẩn": giá trị `NULL` ở khóa chính, cước phí âm, outliers, sai format timestamp, freshness lag.

**Nỗi đau cốt lõi:**
- Đội Data Engineer tốn hàng trăm giờ viết test kiểm tra chất lượng dữ liệu thủ công (dbt/SQL tests).
- Phát hiện sự cố dữ liệu chậm, gây báo cáo kinh doanh sai lệch.
- Chi phí vận hành tăng do xử lý sự cố thủ công, thiếu giám sát tự động.
- Rủi ro vi phạm bảo mật dữ liệu nhạy cảm (PII/GDPR) khi hệ thống tự động truy cập dữ liệu.

## 2. Giải pháp sản phẩm

**RidePulse DQ (AutoDQ Agent)** là nền tảng AI Agent dựa trên **LangGraph**, tự động đọc metadata (schema, thống kê cơ bản) của dataset và gợi ý bộ rule kiểm tra chất lượng dữ liệu (Not-Null, Uniqueness, Range, Freshness, Format). Mọi rule do AI đề xuất đều đi qua cơ chế **Human-In-The-Loop (HITL)**, cho phép Data Steward duyệt, sửa hoặc từ chối trước khi áp dụng lên production.

Rule được duyệt sẽ được tự động biên dịch thành test suite (**dbt / Great Expectations**) và thực thi định kỳ. Song song, mô hình Machine Learning (**Isolation Forest / Z-score**) phát hiện các bất thường thống kê trên dữ liệu vận hành, kèm chẩn đoán nguyên nhân gốc rễ tự động (**AI Root Cause Diagnosis**) và theo dõi xu hướng chất lượng dữ liệu theo thời gian.

## 3. Đối tượng người dùng & Ma trận phân quyền

### 3.1 Data Steward (Core Operator)
Bao gồm Lead Data Engineer — người vận hành chính trên UI. Toàn quyền: kết nối dataset, chạy profiling, duyệt/sửa/từ chối rule (HITL), thực thi test suite, xem AI Diagnosis & Trend.

### 3.2 Viewer (Data Analyst / Executive)
Người xem trên UI. Chỉ Read-Only: xem Dashboard Data Health Score, chi tiết Anomaly/AI Diagnosis, Trend Analysis — không thao tác sửa đổi.

### 3.3 Data Officer (Governance)
Vai trò giám sát tuân thủ, **không có màn hình đăng nhập riêng trong UI Flow**. Giám sát phạm vi truy cập của Agent (chỉ đọc metadata, không đọc dữ liệu PII chi tiết) và log audit; theo dõi ngoài hệ thống UI, dựa trên audit log do hệ thống ghi nhận.

### 3.4 Bảng Ma trận Phân quyền (Role Matrix)

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

## 4. Luồng thao tác người dùng chính (7 bước)

### 4.1 Mô tả 7 bước

1. **Đăng nhập (Login)** – chọn vai trò Data Steward hoặc Viewer.
2. **Dashboard tổng quan** – xem Data Health Score chung.
3. **Select Dataset** – chọn bảng vận hành (`dich_vu_xe_trips`, `dich_vu_xe_drivers`, `dich_vu_xe_customers`, `dich_vu_xe_payments`).
4. **Agent Profiling & Rule Proposal** – AI Agent phân tích metadata và gợi ý rule (Not-null, Range, Format...).
5. **HITL Review** (chỉ Data Steward) – duyệt/sửa/từ chối từng rule đề xuất.
6. **Test Execution & Anomaly Detection** – hệ thống chạy test dbt và mô hình ML Isolation Forest.
7. **Detail Report — AI Diagnosis & Trend Analysis** – xem chi tiết lỗi, chẩn đoán nguyên nhân gốc (AI Diagnosis) và biểu đồ xu hướng Data Quality Score kèm chỉ số Precision/Recall/F1 (đánh giá mô hình ML — Smart Thresholding & Evaluation).

### 4.2 Ánh xạ Bước UI ↔ Feature ID (F01–F07)

| Bước UI Flow | Feature ID liên quan | Ghi chú |
| :--- | :--- | :--- |
| 1. Đăng nhập | — | Chọn vai trò (Data Steward / Viewer) |
| 2. Dashboard tổng quan | F04 | Data Health Score & Alerting |
| 3. Select Dataset | — | Chọn bảng vận hành |
| 4. Agent Profiling & Rule Proposal | **F01** | Dataset Profiler & Rule Proposer |
| 5. HITL Review | **F02** | HITL Approval Management |
| 6. Test Execution & Anomaly Detection | **F03 + F05** | Test Auto-Generator & Execution; ML Anomaly Detector |
| 7. Detail Report — AI Diagnosis & Trend Analysis | **F04 + F06 + F07** | Dashboard & Alerting; Smart Thresholding & Evaluation; Root Cause & Trend Analysis |

### 4.3 Bảng tra cứu Screen (đối chiếu Wireframe & UI Flow — Screen 1–11)

| Screen | Tên màn hình | Bước UI Flow liên quan | Vai trò truy cập |
| :--- | :--- | :--- | :--- |
| Screen 1 | Login & Role Selection | 1. Đăng nhập | Data Steward, Viewer |
| Screen 2 | Steward Dashboard | 2. Dashboard tổng quan | Data Steward |
| Screen 3 | Dataset Catalog | 3. Select Dataset | Data Steward |
| Screen 4 | Dataset Profiling & Metadata Insights | 4. Agent Profiling & Rule Proposal | Data Steward |
| Screen 5 | Rule Review Screen (HITL Review Table) | 5. HITL Review | Data Steward |
| Screen 6 | Rule Edit Modal | 5. HITL Review | Data Steward |
| Screen 7 | Running Tests & Streaming Console Log | 6. Test Execution & Anomaly Detection | Data Steward |
| Screen 8 | Anomaly Dashboard & Alert Stream | 6. Test Execution & Anomaly Detection | Data Steward |
| Screen 9 | AI Diagnosis Modal | 7. Detail Report — AI Diagnosis & Trend Analysis | Data Steward |
| Screen 10 | Trend & Evaluation Screen | 7. Detail Report — AI Diagnosis & Trend Analysis | Data Steward |
| Screen 11 | Executive Viewer Dashboard (Read-Only) | 2, 6, 7 (chế độ Read-Only) | Viewer |

## 5. Yêu cầu tính năng tóm tắt (F01–F07)

### 5.1 Tính năng Cơ bản (MVP)

| ID | Tính năng | Mô tả |
| :--- | :--- | :--- |
| **F01** | Dataset Profiler & Rule Proposer | LLM tự động đọc metadata (schema, thống kê cơ bản) của dataset và gợi ý danh sách rule kiểm tra chất lượng dữ liệu (Uniqueness, Not-Null, Range, Freshness...). |
| **F02** | HITL Approval Management | Giao diện cho phép Data Steward xem, duyệt, chỉnh sửa hoặc từ chối các rule do Agent đề xuất trước khi áp dụng lên production. |
| **F03** | Test Auto-Generator & Execution | Tự động biên dịch các rule đã được duyệt thành test code (dbt/Great Expectations) và thực thi theo lịch định kỳ. |
| **F04** | Dashboard & Alerting | Hiển thị các chỉ số chất lượng dữ liệu (Data Health Score) và gửi cảnh báo khi test thất bại qua Web GUI / Slack. |

### 5.2 Tính năng Nâng cao

| ID | Tính năng | Mô tả |
| :--- | :--- | :--- |
| **F05** | ML Anomaly Detector | Sử dụng Isolation Forest / Z-score để phát hiện các biến động bất thường về mặt thống kê (ví dụ: lượng chuyến đi giảm đột ngột 80%). |
| **F06** | Smart Thresholding & Evaluation | Tự động điều chỉnh ngưỡng (threshold) cảnh báo dựa trên đánh giá Precision/Recall từ dữ liệu gán nhãn lịch sử. |
| **F07** | Root Cause & Trend Analysis | AI đưa ra chẩn đoán/nguyên nhân dự đoán cho lỗi dữ liệu và theo dõi biểu đồ xu hướng chất lượng dữ liệu theo thời gian. |

## 6. Phạm vi dự án (Scope)

### 6.1 In-Scope (Build Phase – MVP)
- Web UI (React + Ant Design) hỗ trợ 2 vai trò: Data Steward và Viewer.
- Profiling dataset mô phỏng dịch vụ gọi xe, sinh rule đề xuất qua LangGraph Agent.
- Tích hợp HITL duyệt/sửa/từ chối rule ở mức đơn giản.
- Anomaly Detection bằng Isolation Forest trên 1–2 bảng dữ liệu trọng yếu (ví dụ: `dich_vu_xe_trips`).
- Deploy thử nghiệm trên Docker / Cloud Run.

### 6.2 Out-of-Scope (Phát triển tương lai)
- Phân quyền chi tiết nhiều lớp (Multi-tenant RBAC phức tạp).
- Kết nối trực tiếp toàn bộ Data Warehouse thật trong thực tế.
- Đa kênh cảnh báo (SMS/Call) — hiện chỉ hỗ trợ Dashboard / Webhook / Slack.

## 7. Kiến trúc kỹ thuật tham chiếu

| Thành phần | Công nghệ |
| :--- | :--- |
| AI Agent orchestration | LangGraph (profiler → rule proposer → test generator → anomaly detector) |
| Sinh & thực thi test dữ liệu | dbt / Great Expectations |
| Phát hiện bất thường | scikit-learn (Isolation Forest / Z-score) |
| Lập lịch chạy test | Dagster |
| Lưu trữ dữ liệu | Data Warehouse (Postgres) |
| Lưu lịch sử rule | Vector DB |
| Backend | FastAPI |
| Frontend | React + Ant Design |
| Triển khai | Docker + Cloud Run |

## 8. Success Metrics tham khảo

| Metric | Mục tiêu |
| :--- | :--- |
| Rule Adoption Rate | ≥ 70% |
| Time-to-Detect | Giảm so với quy trình thủ công |
| False Positive Rate | Giảm dần qua các vòng đánh giá Precision/Recall |
| Manual Test Effort Reduction | Giảm đáng kể (đo định tính qua demo) |
