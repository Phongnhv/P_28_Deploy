# BÁO CÁO NHÓM - THÀNH VIÊN C
## CHUYÊN MÔN: PRODUCT DESIGN & UI/UX LEAD

**TRƯỜNG ĐẠI HỌC VINUNIVERSITY**  
**KHOA KỸ THUẬT & KHOA HỌC MÁY TÍNH**  
-----------------------------------  
- **Dự án:** RidePulse DQ – Autonomous Data Quality & Anomaly Intelligence Platform  
- **Môn học:** Product Development (P-028)  
- **Thực hiện bởi:** Thành viên C (UI/UX Lead)  
- **Nhiệm vụ chính:** Mục 3 - Wireframe & UI Flow (Figma & Ant Design)  
- **Lớp:** Product Development 2026  
- **Ngày nộp:** 31/07/2026  

---

## CHƯƠNG 1: TỔNG QUAN DỰ ÁN & BÀI TOÁN NGHIỆP VỤ

Trong dự án **RidePulse DQ – Autonomous Data Quality & Anomaly Intelligence Platform**, Thành viên C chịu trách nhiệm toàn bộ **Mục 3: Wireframe & UI Flow**, thiết kế cấu trúc luồng thao tác người dùng, phác thảo giao diện Ant Design và lập trình bản Web Application Interactive Prototype.

### 1.1 Bối cảnh Dữ liệu Vận hành Ride-Hailing
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

---

## CHƯƠNG 2: PHÂN TÍCH VAI TRÒ NGƯỜI DÙNG & PHÂN QUYỀN

1. **Data Steward (Core Operator):**  
   Có toàn quyền connect dataset, chạy profiling, duyệt AI rules (Approve / Reject / Edit), thực thi dbt test và xem chẩn đoán nguyên nhân gốc AI Root Cause Diagnosis.
2. **Viewer (Executive Lead):**  
   Giao diện Read-Only an toàn, chỉ xem Dashboard Data Health Score, các sự cố và báo cáo Trend Analysis mà không thể thao tác sửa đổi quy tắc.

### Bảng Ma trận Phân quyền (Role Matrix)

| Quyền hạn / Thao tác | 👨‍💻 Data Steward | 👁️ Viewer (Executive) |
| :--- | :---: | :---: |
| Kết nối Dataset & Profiling | ✅ | ❌ |
| Duyệt AI Rules (HITL Review) | ✅ | ❌ |
| Chỉnh sửa Rule Threshold / Severity | ✅ | ❌ |
| Thực thi Test Suite (dbt / ML) | ✅ | ❌ |
| Xem Dashboard Data Health Score | ✅ | ✅ |
| Xem Chi tiết Anomaly & AI Diagnosis | ✅ | ✅ |
| Xem Trend Analysis & ML Metrics | ✅ | ✅ |

---

## MỤC 3: WIREFRAME & UI FLOW (THÀNH VIÊN C ĐẢM NHẬN)

### A. UI Flow (Sơ đồ luồng đi người dùng)

Luồng thao tác được thiết kế mạch lạc qua 7 bước theo đúng yêu cầu đề bài:

```
[1. Đăng nhập (Login)] ➔ [2. Dashboard Tổng quan] ➔ [3. Select Dataset] ➔ [4. Agent Profiling & Rule Proposal]
                                                                                      │
[7. Detail Report / Trend]  [6. Test Execution & Anomaly Detection]  [5. HITL Review (Chỉ Steward)]
```

1. **Đăng nhập (Login):** Chọn vai trò Steward hoặc Viewer.
2. **Dashboard Tổng quan:** Xem chỉ số Data Health Score chung (87.4%).
3. **Select Dataset:** Chọn bảng dữ liệu vận hành `dich_vu_xe_trips`.
4. **Agent Profiling & Rule Proposal:** AI hiện gợi ý các Rule (Not-null, Range check, Format).
5. **HITL Review (Chỉ Steward):** Checkbox Duyệt / Sửa / Từ chối Rule trực quan.
6. **Test Execution & Anomaly Detection:** Hệ thống chạy test dbt & mô hình ML Isolation Forest.
7. **Detail Report / Alerting & Trend Analysis:** Xem chi tiết lỗi, chẩn đoán nguyên nhân AI Diagnosis và biểu đồ xu hướng.

### B. Khung Màn Hình Chính (Wireframe Layout - Ant Design Style)

Thành viên C phác thảo các màn hình trọng tâm theo yêu cầu với phong cách Ant Design Admin Dashboard (Frame 1440px Desktop Grid):

- **Màn 1: Rule Review Screen (HITL):** Bảng chứa danh sách Rule do AI tạo (Column Name, Rule Type, AI Reason, Confidence %, Suggested Threshold, Status: Pending/Approved/Rejected, Action buttons).
- **Màn 2: Anomaly Dashboard:** Biểu đồ Time-series hiển thị các điểm bất thường (Anomaly dots màu đỏ), bảng danh sách Alert đi kèm nút `🤖 AI Diagnosis` (nhấn vào hiện Modal giải thích nguyên nhân gốc rễ).
- **Màn 3: Trend & Evaluation Screen:** Biểu đồ đường thể hiện chỉ số Data Quality Score theo tuần/tháng và các thông số Precision (94.2%), Recall (91.8%), F1-Score (93.0%) của mô hình ML.

---

## CHƯƠNG 4: HỒ SƠ HÌNH ẢNH UI PROTOTYPE THỰC TẾ (11 SCREENS)

### Screen 1: Đăng nhập & Chọn Phân quyền (Login & Role Selection)
![Screen 1: Đăng nhập & Chọn Phân quyền](file:///c:/Users/ADMIN/WorkPlace/Vinuni/AssignmentProject/P-028/docs/images/screen_1_login.png)
- **📌 Phân tích UX/UI:** Màn hình đăng nhập chọn vai trò Data Steward hoặc Viewer.

---

### Screen 2: Steward Dashboard (Data Health Score 87.4%)
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

### Screen 5: Màn 1 - Rule Review Screen (HITL Review Table)
![Screen 5: Rule Review Screen](file:///c:/Users/ADMIN/WorkPlace/Vinuni/AssignmentProject/P-028/docs/images/screen_5_ai_rule_proposals.png)
- **📌 Phân tích UX/UI:** Bảng HITL review chứa danh sách Rule do AI gợi ý với các nút Approve/Reject/Edit.

---

### Screen 6: Màn 1 (Phụ) - Rule Edit Modal
![Screen 6: Rule Edit Modal](file:///c:/Users/ADMIN/WorkPlace/Vinuni/AssignmentProject/P-028/docs/images/screen_6_rule_edit_modal.png)
- **📌 Phân tích UX/UI:** Modal chỉnh sửa thông số Threshold, Severity và Mô tả rule.

---

### Screen 7: Running Tests & Streaming Console Log
![Screen 7: Running Tests & Streaming Console Log](file:///c:/Users/ADMIN/WorkPlace/Vinuni/AssignmentProject/P-028/docs/images/screen_7_execution_log.png)
- **📌 Phân tích UX/UI:** Chạy dbt test suite với Stepper 4 bước và Live Terminal Log.

---

### Screen 8: Màn 2 - Anomaly Dashboard & Alert Stream
![Screen 8: Anomaly Dashboard & Alert Stream](file:///c:/Users/ADMIN/WorkPlace/Vinuni/AssignmentProject/P-028/docs/images/screen_8_anomaly_dashboard.png)
- **📌 Phân tích UX/UI:** Biểu đồ Time-Series gắn đốm đỏ Anomaly dots và bảng Alert có nút AI Diagnosis.

---

### Screen 9: Màn 2 (Phụ) - AI Diagnosis Modal
![Screen 9: AI Diagnosis Modal](file:///c:/Users/ADMIN/WorkPlace/Vinuni/AssignmentProject/P-028/docs/images/screen_9_ai_diagnosis_modal.png)
- **📌 Phân tích UX/UI:** Modal giải thích nguyên nhân gốc do AI Agent chẩn đoán.

---

### Screen 10: Màn 3 - Trend & Evaluation Screen
![Screen 10: Trend & Evaluation Screen](file:///c:/Users/ADMIN/WorkPlace/Vinuni/AssignmentProject/P-028/docs/images/screen_10_trend_analysis.png)
- **📌 Phân tích UX/UI:** Biểu đồ xu hướng 30 ngày và các chỉ số ML Metrics (Precision, Recall, F1).

---

### Screen 11: Executive Viewer Dashboard (Read-Only View)
![Screen 11: Executive Viewer Dashboard](file:///c:/Users/ADMIN/WorkPlace/Vinuni/AssignmentProject/P-028/docs/images/screen_11_viewer_dashboard.png)
- **📌 Phân tích UX/UI:** Giao diện Read-Only an toàn 100% dành cho Viewer.

---

## CHƯƠNG 5: TỔNG KẾT BÀN GIAO SẢN PHẨM CỦA THÀNH VIÊN C

Thành viên C bàn giao đầy đủ sản phẩm đáp ứng 100% yêu cầu đề bài:

1. **File Báo cáo Word (.docx 10 trang):** [Báo cáo thành viên C.docx](file:///c:/Users/ADMIN/WorkPlace/Vinuni/AssignmentProject/P-028/docs/B%C3%A1o%20c%C3%A1o%20th%C3%A0nh%20vi%C3%AAn%20C.docx)
2. **Thư mục 11 Ảnh chụp UI chất lượng cao:** [docs/images/](file:///c:/Users/ADMIN/WorkPlace/Vinuni/AssignmentProject/P-028/docs/images)
3. **Bản Web App Prototype tương tác:** [ui_test/index.html](file:///c:/Users/ADMIN/WorkPlace/Vinuni/AssignmentProject/P-028/ui_test/index.html)
4. **Tài liệu Wireframe Specs:** [ridepulse_dq_design_spec.md](file:///c:/Users/ADMIN/WorkPlace/Vinuni/AssignmentProject/P-028/ridepulse_dq_design_spec.md)
