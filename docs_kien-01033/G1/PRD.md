# PRODUCT REQUIREMENTS DOCUMENT (PRD) — RidePulse DQ (AutoDQ Agent)
### Autonomous Data Quality & Anomaly Intelligence Platform for Ride-Hailing Services

---

## 1. Giới thiệu tài liệu

### 1.1. Mục đích
Tài liệu này mô tả các yêu cầu sản phẩm cho RidePulse DQ (AutoDQ Agent) – một AI Agent giúp tự động khảo sát, đề xuất, kiểm thử và giám sát chất lượng dữ liệu (Data Quality) cho các dataset vận hành của dịch vụ gọi xe, đồng thời phát hiện các bất thường (anomaly) và hỗ trợ chẩn đoán nguyên nhân gốc rễ. Tài liệu là cơ sở để đội UI/UX thiết kế wireframe/UI flow và để đội kỹ thuật triển khai kiến trúc hệ thống.

### 1.2. Phạm vi áp dụng
Tài liệu áp dụng cho giai đoạn Build Phase của dự án tập trung vào phần MVP và các tính năng nâng cao có thể trình diễn (demo) trên bộ dữ liệu mô phỏng của dịch vụ gọi xe.

### 1.3. Đối tượng đọc tài liệu
* **Product Lead / Architect:** đối chiếu PRD với Product Brief.
* **UI/UX Designer:** dựa vào User Stories và Flow để vẽ wireframe.
* **Technical Lead:** dựa vào Feature Requirements và NFR để thiết kế kiến trúc (LangGraph, dbt/Great Expectations, Isolation Forest, Postgres...).

---

## 2. Bối cảnh & Vấn đề (Background & Problem Statement)

### 2.1. Hiện trạng
Dữ liệu vận hành của dịch vụ gọi xe (chuyến đi, tọa độ GPS, cước phí, thông tin tài xế/khách hàng) có quy mô lớn và tăng trưởng liên tục. Dữ liệu thường xuất hiện các lỗi phổ biến: giá trị NULL, sai định dạng, giá trị ngoại lai (ví dụ: cước phí âm, chuyến đi 0km).

### 2.2. Pain points
* Đội Data Engineer tốn hàng trăm giờ để viết test kiểm tra chất lượng dữ liệu thủ công (dbt/SQL tests).
* Việc phát hiện sự cố dữ liệu diễn ra chậm, dẫn đến báo cáo kinh doanh sai lệch.
* Chi phí vận hành tăng cao do xử lý sự cố thủ công, thiếu quy trình giám sát tự động.
* Chưa có cơ chế đảm bảo an toàn dữ liệu nhạy cảm (PII) khi hệ thống tự động truy cập dữ liệu.

### 2.3. Cơ hội
Ứng dụng AI Agent (LangGraph) kết hợp các công cụ kiểm thử dữ liệu tiêu chuẩn (dbt / Great Expectations) và mô hình phát hiện bất thường (Isolation Forest / Z-score) để tự động hóa toàn bộ vòng đời quản trị chất lượng dữ liệu, có sự giám sát của con người (Human-In-The-Loop) nhằm đảm bảo an toàn và độ tin cậy.

---

## 3. Mục tiêu sản phẩm & Success Metrics

### 3.1. Mục tiêu kinh doanh (Business Goals)
* Giảm thời gian và công sức đội Data Engineer bỏ ra để viết và bảo trì test chất lượng dữ liệu thủ công.
* Rút ngắn thời gian phát hiện và phản ứng với sự cố dữ liệu (từ phát sinh lỗi đến khi cảnh báo tới người phụ trách).
* Tăng độ tin cậy của báo cáo kinh doanh nhờ dữ liệu đầu vào được kiểm soát chất lượng liên tục.
* Đảm bảo tuân thủ các quy định về bảo mật dữ liệu nhạy cảm (PII/GDPR) trong toàn bộ quy trình tự động hóa.

### 3.2. Mục tiêu sản phẩm (Product Goals)
* Cho phép Data Steward duyệt rule chất lượng dữ liệu do AI đề xuất trước khi áp dụng lên production (HITL).
* Tự động sinh và thực thi test dữ liệu định kỳ, không cần viết code thủ công cho từng rule.
* Phát hiện bất thường thống kê/ML trên dữ liệu vận hành và cung cấp gợi ý nguyên nhân.

### 3.3. Success Metrics (đề xuất đo lường ở giai đoạn sau MVP)

> **Lưu ý:** các con số cụ thể (Data Health Score 87.4%; Precision 94.2% / Recall 91.8% / F1 93.0%) xuất hiện trong Wireframe (Screen 2, Screen 10) chỉ là **số liệu minh họa demo**, không phải ngưỡng mục tiêu chính thức. Ngưỡng mục tiêu chính thức duy nhất được thống nhất giữa Brief và PRD là bảng dưới đây.

| Metric | Mô tả | Mục tiêu tham khảo |
| :--- | :--- | :--- |
| **Rule Adoption Rate** | Tỷ lệ rule do AI đề xuất được Data Steward duyệt (không sửa hoặc sửa nhỏ) | ≥ 70% |
| **Time-to-Detect** | Thời gian trung bình từ khi lỗi/anomaly phát sinh đến khi được cảnh báo | Giảm so với quy trình thủ công |
| **False Positive Rate** | Tỷ lệ cảnh báo anomaly không chính xác trên tổng số cảnh báo | Giảm dần qua các vòng đánh giá Precision/Recall |
| **Manual Test Effort Reduction** | Số giờ viết test thủ công được cắt giảm so với trước khi có Agent | Giảm đáng kể (định tính, đo qua demo) |

---

## 4. Đối tượng người dùng (User Personas) & Ma trận phân quyền

### 4.1. Persona 1 — Data Steward / Lead Data Engineer
* **Vai trò:** Người duyệt rule chất lượng dữ liệu, quản lý toàn bộ quy trình kiểm thử, xem cảnh báo chuyên sâu.
* **Mục tiêu:** Đảm bảo rule được AI đề xuất là chính xác, an toàn trước khi chạy trên production.
* **Nỗi đau:** Không có thời gian rà soát dữ liệu thủ công; cần công cụ hỗ trợ ra quyết định nhanh, tin cậy.

### 4.2. Persona 2 — Viewer (Data Analyst / Executive)
* **Vai trò:** Xem Dashboard tổng quan sức khỏe dữ liệu (Data Health Score), tra cứu cảnh báo và nguyên nhân.
* **Mục tiêu:** Nhận cảnh báo kèm giải thích nguyên nhân khi dữ liệu bất thường để xử lý sự cố nhanh chóng.
* **Nỗi đau:** Không có quyền chỉnh sửa rule, cần giao diện dễ đọc, trực quan (không chuyên sâu kỹ thuật).

### 4.3. Persona 3 — Data Officer (Governance)
* **Vai trò:** Đảm bảo hệ thống tuân thủ quy định bảo mật, giám sát phạm vi truy cập dữ liệu của Agent. Không có màn hình đăng nhập riêng trong UI Flow (không xuất hiện ở Screen 1); theo dõi hoàn toàn qua audit log ngoài hệ thống UI.
* **Mục tiêu:** Agent chỉ đọc metadata (schema, data type, thống kê tổng hợp), không truy cập dữ liệu nhạy cảm (PII).
* **Nỗi đau:** Rủi ro vi phạm GDPR/PII nếu hệ thống tự động truy cập trực tiếp vào dữ liệu chi tiết.

### 4.4. Bảng Ma trận Phân quyền (Role Matrix)

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

## 5. Phạm vi sản phẩm (Product Scope)

### 5.1. In-Scope (Build Phase)
* Xây dựng Web UI (React + Ant Design) đơn giản, hỗ trợ 2 vai trò: Data Steward và Viewer.
* Khảo sát (profiling) dataset mô phỏng dịch vụ gọi xe (`dich_vu_xe_trips`, `dich_vu_xe_drivers`, `dich_vu_xe_customers`, `dich_vu_xe_payments`), sinh rule đề xuất thông qua LangGraph Agent.
* Tích hợp cơ chế HITL cho phép duyệt/sửa/từ chối rule ở mức đơn giản.
* Chạy Anomaly Detection bằng Isolation Forest trên 1–2 bảng dữ liệu trọng yếu (ví dụ: `dich_vu_xe_trips`).
* Deploy thử nghiệm trên Docker / Cloud Run phục vụ demo.

### 5.2. Out-of-Scope (Current phase)
* Phân quyền chi tiết nhiều lớp (Multi-tenant RBAC phức tạp).
* Kết nối trực tiếp tới toàn bộ hệ thống Data Warehouse phức tạp trong thực tế (Postgres).
* Đa kênh cảnh báo (SMS/Call) — hiện chỉ hỗ trợ Dashboard / Webhook / Slack.

---

## 6. Yêu cầu tính năng (Feature Requirements)

### 6.1. Tính năng Cơ bản (MVP)

| ID | Tính năng | Mô tả |
| :--- | :--- | :--- |
| **F01** | Dataset Profiler & Rule Proposer | LLM tự động đọc metadata (schema, thống kê cơ bản) của dataset và gợi ý danh sách rule kiểm tra chất lượng dữ liệu (Uniqueness, Not-Null, Range, Freshness...). |
| **F02** | HITL Approval Management | Giao diện cho phép Data Steward xem, duyệt, chỉnh sửa hoặc từ chối các rule do Agent đề xuất trước khi áp dụng lên production. |
| **F03** | Test Auto-Generator & Execution | Tự động biên dịch các rule đã được duyệt thành test code (dbt/Great Expectations) và thực thi theo lịch định kỳ. |
| **F04** | Dashboard & Alerting | Hiển thị các chỉ số chất lượng dữ liệu (Data Health Score) và gửi cảnh báo khi test thất bại qua Web GUI / Slack. |

### 6.2. Tính năng Nâng cao

| ID | Tính năng | Mô tả |
| :--- | :--- | :--- |
| **F05** | ML Anomaly Detector | Sử dụng Isolation Forest / Z-score để phát hiện các biến động bất thường về mặt thống kê (ví dụ: lượng chuyến đi giảm đột ngột 80%). |
| **F06** | Smart Thresholding & Evaluation | Tự động điều chỉnh ngưỡng (threshold) cảnh báo dựa trên đánh giá Precision/Recall từ dữ liệu gán nhãn lịch sử. |
| **F07** | Root Cause & Trend Analysis | AI đưa ra chẩn đoán/nguyên nhân dự đoán cho lỗi dữ liệu và theo dõi biểu đồ xu hướng chất lượng dữ liệu theo thời gian. |

---

## 7. User Stories & Acceptance Criteria

Các User Story được viết theo định dạng: *"Là một [vai trò], tôi muốn [nhu cầu], để [giá trị/lợi ích]"*, kèm Acceptance Criteria (AC) để xác nhận hoàn thành.

### US01 – Data Profiling
* **User Story:** Là một Data Steward, tôi muốn Agent tự động quét metadata của bảng `dich_vu_xe_trips`, để tôi không phải ngồi đọc hàng triệu dòng dữ liệu thủ công.
* **Liên quan tính năng:** F01
* **Acceptance Criteria:**
  1. Agent đọc được schema và thống kê cơ bản (tỷ lệ null, kiểu dữ liệu, min/max) của bảng được chọn.
  2. Kết quả profiling hiển thị trên UI trong thời gian chấp nhận được cho mục đích demo.
  3. Danh sách rule đề xuất được sinh ra kèm mô tả loại rule (Not-null, Range, Format...).

### US02 – HITL Approval
* **User Story:** Là một Data Steward, tôi muốn xem và chỉnh sửa danh sách rule do AI đề xuất trước khi apply, để đảm bảo AI không tạo ra các rule sai làm nghẽn pipeline.
* **Liên quan tính năng:** F02
* **Acceptance Criteria:**
  1. Giao diện hiển thị đầy đủ danh sách rule với trạng thái Pending/Approved/Rejected.
  2. Data Steward có thể sửa tham số của rule (ví dụ: ngưỡng Range) trước khi duyệt.
  3. Chỉ rule ở trạng thái Approved mới được đưa vào bước sinh test tự động (F03).

### US03 – Governance
* **User Story:** Là một Data Officer, tôi muốn Agent chỉ đọc metadata (schema, data types) chứ không xem dữ liệu nhạy cảm, để tuân thủ quy định bảo mật (GDPR/PII).
* **Liên quan tính năng:** F01, NFR-Security
* **Acceptance Criteria:**
  1. Agent không truy vấn hoặc hiển thị dữ liệu chi tiết ở mức bản ghi cho các trường nhạy cảm (PII).
  2. Phạm vi truy cập của Agent giới hạn ở metadata và số liệu thống kê tổng hợp.
  3. Có log ghi nhận các lần Agent truy cập metadata phục vụ kiểm toán (audit).

### US04 – Alert & Root Cause
* **User Story:** Là một Data Analyst, tôi muốn nhận cảnh báo kèm giải thích nguyên nhân khi dữ liệu cước phí bị bất thường, để tôi xử lý sự cố nhanh chóng.
* **Liên quan tính năng:** F04, F05, F07
* **Acceptance Criteria:**
  1. Khi mô hình phát hiện anomaly, hệ thống tạo cảnh báo hiển thị trên Dashboard.
  2. Mỗi cảnh báo có nút "AI Diagnosis" hiển thị chẩn đoán nguyên nhân dự đoán.
  3. Cảnh báo có thể được đẩy qua kênh Webhook/Slack theo cấu hình.

### US05 – Trend & Evaluation (bổ sung)
* **User Story:** Là một Data Steward, tôi muốn xem xu hướng Data Quality Score theo thời gian và các chỉ số Precision/Recall của mô hình, để đánh giá hiệu quả của hệ thống phát hiện bất thường.
* **Liên quan tính năng:** F06, F07
* **Acceptance Criteria:**
  1. Biểu đồ đường thể hiện Data Quality Score theo tuần/tháng.
  2. Hiển thị chỉ số Precision/Recall dựa trên dữ liệu gán nhãn lịch sử (nếu có).
  3. Ngưỡng cảnh báo có thể được điều chỉnh tự động dựa trên kết quả đánh giá.

---

## 8. Luồng nghiệp vụ chính (7 bước) & Tham chiếu Screen

Mục này tóm tắt luồng thao tác chính để đối chiếu với User Stories ở Mục 7 và làm cơ sở cho wireframe (xem Wireframe & UI Flow Mục 3 và Mục 5).

### 8.1. Mô tả 7 bước

1. **Đăng nhập** – chọn vai trò (Steward hoặc Viewer).
2. **Dashboard tổng quan** – xem Data Health Score chung.
3. **Select Dataset** – chọn bảng dữ liệu vận hành (`dich_vu_xe_trips`, `dich_vu_xe_drivers`, `dich_vu_xe_customers`, `dich_vu_xe_payments`).
4. **Agent Profiling & Rule Proposal** – AI Agent hiển thị rule gợi ý (F01).
5. **HITL Review** (chỉ Steward) – duyệt/sửa/từ chối rule (F02).
6. **Test Execution & Anomaly Detection** – hệ thống chạy test và mô hình ML (F03, F05).
7. **Detail Report — AI Diagnosis & Trend Analysis** – xem chi tiết lỗi, chẩn đoán nguyên nhân và biểu đồ xu hướng (F04, F06, F07).

### 8.2. Ánh xạ Bước UI Flow ↔ Feature ID ↔ Screen

| Bước UI Flow | Feature ID | Screen tương ứng |
| :--- | :--- | :--- |
| 1. Đăng nhập | — | Screen 1 |
| 2. Dashboard tổng quan | F04 | Screen 2, Screen 11 (Viewer) |
| 3. Select Dataset | — | Screen 3, Screen 4 |
| 4. Agent Profiling & Rule Proposal | F01 | Screen 4 |
| 5. HITL Review | F02 | Screen 5, Screen 6 |
| 6. Test Execution & Anomaly Detection | F03 + F05 | Screen 7, Screen 8 |
| 7. Detail Report — AI Diagnosis & Trend Analysis | F04 + F06 + F07 | Screen 9, Screen 10 |

---

## 9. Yêu cầu phi chức năng

| Nhóm | Yêu cầu |
| :--- | :--- |
| **Bảo mật & Governance** | Agent chỉ được phép đọc metadata (schema, data type, thống kê tổng hợp); không truy cập trực tiếp dữ liệu nhạy cảm (PII) ở mức bản ghi, tuân thủ nguyên tắc GDPR. |
| **Human-In-The-Loop** | Mọi rule do AI đề xuất bắt buộc phải qua bước duyệt của Data Steward trước khi được áp dụng trên production. |
| **Độ chính xác cảnh báo** | Hệ thống cần cơ chế đánh giá (Precision/Recall) và tinh chỉnh ngưỡng để giảm tỷ lệ cảnh báo sai (false positive). |
| **Hiệu năng** | Việc chạy kiểm tra chất lượng dữ liệu trên các bảng lớn cần thực hiện theo lịch, trong giới hạn tài nguyên tính toán cho phép của môi trường demo. |
| **Khả năng mở rộng** | Kiến trúc cần cho phép bổ sung thêm loại rule hoặc mô hình phát hiện bất thường trong tương lai mà không phải thiết kế lại toàn bộ hệ thống. |
| **Góc nhìn khác** | Cần log lại lịch sử rule, kết quả test và cảnh báo để phục vụ audit và Root Cause Analysis. |

---

## 10. Yêu cầu kỹ thuật tham chiếu (Technical Reference)

Chi tiết kiến trúc hệ thống do Technical Lead chuẩn hóa; PRD tham chiếu nhanh các thành phần chính để đảm bảo tính năng ở Mục 6 khả thi với stack đã chọn (đồng bộ với Brief Mục 7):
* **AI Agent orchestration:** LangGraph (profiler → rule proposer → test generator → anomaly detector).
* **Sinh & thực thi test dữ liệu:** dbt / Great Expectations.
* **Phát hiện bất thường:** scikit-learn (Isolation Forest / Z-score).
* **Lập lịch chạy test:** Dagster.
* **Lưu trữ dữ liệu:** Data Warehouse (Postgres).
* **Lưu lịch sử rule:** Vector DB.
* **Backend:** FastAPI.
* **Frontend:** React + Ant Design.
* **Triển khai:** Docker + Cloud Run.

---

## 11. Giả định & Ràng buộc

### 11.1. Giả định
* Dữ liệu dùng để demo là dữ liệu mô phỏng, không chứa dữ liệu thật của người dùng cuối.
* Nhóm có quyền truy cập môi trường Postgres để triển khai thử nghiệm.
* Có sẵn một tập dữ liệu gán nhãn lịch sử (dù nhỏ) để phục vụ đánh giá Precision/Recall cho tính năng nâng cao (F06).

### 11.2. Ràng buộc
* Thời gian phát triển giới hạn trong khung GATE 1, ưu tiên hoàn thiện MVP trước khi mở rộng tính năng nâng cao.
* Hạ tầng demo giới hạn tài nguyên tính toán, cần tối ưu cho 1–2 bảng dữ liệu trọng yếu thay vì toàn bộ hệ thống.
* Không triển khai phân quyền RBAC phức tạp, kết nối toàn bộ Data Warehouse thật, hay đa kênh cảnh báo (SMS/Call) trong phạm vi GATE 1.

---

## 12. Rủi ro (Risks) & Giải pháp giảm thiểu

| Rủi ro | Ảnh hưởng | Giải pháp giảm thiểu |
| :--- | :--- | :--- |
| **AI đề xuất rule sai/không phù hợp** | Pipeline production bị nghẽn hoặc bỏ sót lỗi thật | Bắt buộc bước HITL duyệt rule (F02) trước khi áp dụng |
| **Tỷ lệ cảnh báo sai (false positive) cao** | Giảm niềm tin của người dùng vào hệ thống | Áp dụng Smart Thresholding & đánh giá Precision/Recall (F06) |
| **Rò rỉ dữ liệu nhạy cảm (PII)** | Vi phạm quy định bảo mật/GDPR | Giới hạn Agent chỉ đọc metadata, không truy cập dữ liệu chi tiết (US03) |
| **Thời gian phát triển GATE 1 hạn chế** | Không hoàn thiện đủ tính năng nâng cao | Ưu tiên MVP (F01–F04), tính năng nâng cao demo ở mức tối thiểu khả thi |

---

## 13. Release Plan / Roadmap

| Giai đoạn | Nội dung | Tính năng |
| :--- | :--- | :--- |
| **Phase 1 – MVP (GATE 1)** | Xây dựng luồng cơ bản: profiling → đề xuất rule → HITL duyệt → sinh & chạy test → dashboard & cảnh báo | F01, F02, F03, F04 |
| **Phase 2 – Nâng cao (demo mở rộng)** | Bổ sung phát hiện bất thường bằng ML, tự động điều chỉnh ngưỡng, phân tích nguyên nhân & xu hướng | F05, F06, F07 |
| **Phase 3 – Định hướng tương lai (ngoài phạm vi hiện tại)** | RBAC nhiều lớp, kết nối Data Warehouse thật, đa kênh cảnh báo (SMS/Call) | Out-of-scope |

---

## 14. Phụ lục: Thuật ngữ (Glossary)

| Thuật ngữ | Giải thích |
| :--- | :--- |
| **HITL (Human-In-The-Loop)** | Cơ chế yêu cầu con người xác nhận/duyệt trước khi hệ thống tự động thực thi hành động. |
| **Data Health Score** | Chỉ số tổng hợp phản ánh mức độ "khỏe mạnh" (đầy đủ, chính xác, nhất quán) của một dataset. |
| **Anomaly Detection** | Kỹ thuật phát hiện các điểm dữ liệu hoặc xu hướng bất thường so với hành vi thông thường. |
| **Root Cause Analysis (RCA)** | Quá trình xác định nguyên nhân gốc rễ gây ra một sự cố hoặc bất thường dữ liệu. |
| **PII (Personally Identifiable Information)** | Thông tin có thể định danh cá nhân, cần được bảo vệ theo quy định (ví dụ GDPR). |
