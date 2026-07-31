# PRODUCT BRIEF
## RidePulse DQ (AutoDQ Agent)
### Autonomous Data Quality & Anomaly Intelligence Platform for Ride-Hailing Services

---

**Ngày nộp:** 31/07/2026

**Nhóm thực hiện & Phân công vai trò:**

| Thành viên | Vai trò | Nhiệm vụ chính |
| :--- | :--- | :--- |
| Đạt | Product Lead / Architect | Viết Product Brief, quản lý tiến độ, tổng hợp file nộp cuối |
| Chiến | Product Owner / Business Analyst (PO/BA) | Xây dựng PRD (Features, User Stories, Scope) |
| Phong | UI/UX Lead & Flow Specialist | Thiết kế Wireframe & UI Flow (Figma/Ant Design) |
| Kiên | Technical Lead | Chuẩn hóa kiến trúc kỹ thuật, hỗ trợ B viết Tech Scope và hỗ trợ C hình dung luồng UI dữ liệu |

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

| Vai trò | Mô tả | Quyền hạn chính |
| :--- | :--- | :--- |
| **Data Steward** (Core Operator, gồm Lead Data Engineer) | Người vận hành chính trên UI | Toàn quyền: kết nối dataset, chạy profiling, duyệt/sửa/từ chối rule (HITL), thực thi test suite, xem AI Diagnosis & Trend |
| **Viewer** (Data Analyst / Executive Lead) | Người xem trên UI | Chỉ Read-Only: xem Dashboard Data Health Score, chi tiết Anomaly/AI Diagnosis, Trend Analysis — không thao tác sửa đổi |
| **Data Officer** (Governance) | Vai trò giám sát tuân thủ, không có màn hình đăng nhập riêng trong UI Flow | Giám sát phạm vi truy cập của Agent (chỉ đọc metadata, không đọc dữ liệu PII chi tiết) và log audit; theo dõi ngoài hệ thống UI, dựa trên audit log do hệ thống ghi nhận |

## 4. Luồng thao tác người dùng chính (7 bước)

1. **Đăng nhập (Login)** – chọn vai trò Data Steward hoặc Viewer.
2. **Dashboard tổng quan** – xem Data Health Score chung.
3. **Select Dataset** – chọn bảng vận hành (`dich_vu_xe_trips`, `dich_vu_xe_drivers`, `dich_vu_xe_customers`, `dich_vu_xe_payments`).
4. **Agent Profiling & Rule Proposal** – AI Agent phân tích metadata và gợi ý rule (Not-null, Range, Format...).
5. **HITL Review** (chỉ Data Steward) – duyệt/sửa/từ chối từng rule đề xuất.
6. **Test Execution & Anomaly Detection** – hệ thống chạy test dbt và mô hình ML Isolation Forest.
7. **Detail Report / AI Diagnosis & Trend Analysis** – xem chi tiết lỗi, chẩn đoán nguyên nhân gốc (AI Diagnosis) và biểu đồ xu hướng Data Quality Score kèm chỉ số Precision/Recall/F1 (đánh giá mô hình ML — Smart Thresholding & Evaluation).

## 5. Phạm vi dự án (Scope)

**In-Scope (Build Phase – MVP):**
- Web UI (React + Ant Design) hỗ trợ 2 vai trò: Data Steward và Viewer.
- Profiling dataset mô phỏng dịch vụ gọi xe, sinh rule đề xuất qua LangGraph Agent.
- Tích hợp HITL duyệt/sửa/từ chối rule ở mức đơn giản.
- Anomaly Detection bằng Isolation Forest trên 1–2 bảng dữ liệu trọng yếu (ví dụ: `dich_vu_xe_trips`).
- Deploy thử nghiệm trên Docker / Cloud Run.

**Out-of-Scope (Phát triển tương lai):**
- Phân quyền chi tiết nhiều lớp (Multi-tenant RBAC phức tạp).
- Kết nối trực tiếp toàn bộ Data Warehouse thật trong thực tế.
- Đa kênh cảnh báo (SMS/Call) — hiện chỉ hỗ trợ Dashboard / Webhook / Slack.

## 6. Kiến trúc kỹ thuật tham chiếu

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

---

## Success Metrics tham khảo

| Metric | Mục tiêu |
| :--- | :--- |
| Rule Adoption Rate | ≥ 70% |
| Time-to-Detect | Giảm so với quy trình thủ công |
| False Positive Rate | Giảm dần qua các vòng đánh giá Precision/Recall |
| Manual Test Effort Reduction | Giảm đáng kể (đo định tính qua demo) |
