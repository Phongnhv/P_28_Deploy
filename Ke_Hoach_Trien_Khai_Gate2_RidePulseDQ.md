# KẾ HOẠCH TRIỂN KHAI GATE 2 (MVP) — RidePulse DQ (AutoDQ Agent)
### Autonomous Data Quality & Anomaly Intelligence Platform for Ride-Hailing Services

> **Ngày lập kế hoạch:** 04/08/2026 · **Deadline:** 16/08/2026 · **Còn lại:** 12 ngày

---

## 1. Bối cảnh & Nghĩa vụ thực thi

**Bài toán:** Đội Data Engineer của dịch vụ gọi xe (`dich_vu_xe_trips`, `dich_vu_xe_drivers`, `dich_vu_xe_customers`, `dich_vu_xe_payments`) đang mất hàng trăm giờ viết test dbt thủ công để bắt lỗi dữ liệu bẩn (NULL ở khóa chính, cước âm, outlier, sai format, freshness lag). **RidePulse DQ** là AI Agent (LangGraph) tự động profiling → đề xuất rule → HITL duyệt → sinh & chạy test dbt → phát hiện anomaly bằng ML (Isolation Forest/Z-score) → chẩn đoán nguyên nhân gốc bằng LLM.

**Ràng buộc bắt buộc:**
- Code push lên **Organization `AI20K`**, không dùng repo cá nhân.
- Agent **chỉ đọc metadata** (schema, null %, min/max...), **tuyệt đối không đọc dữ liệu chi tiết/PII** — đây là ràng buộc governance/GDPR cứng, áp dụng cho cả Profiler Node lẫn mọi log gửi lên Phoenix.
- Mọi rule AI đề xuất phải qua **HITL Data Steward duyệt** trước khi áp production — không có rule nào tự động apply.
- Data Officer (governance) **không có màn hình trong UI** — giám sát hoàn toàn qua audit log ngoài hệ thống.

### 10 Deliverables bắt buộc

| # | Deliverable | Yêu cầu cụ thể |
|---|---|---|
| 1 | Source Code (GitHub) | Push lên Org `AI20K`, cấu trúc thư mục rõ ràng, clean code |
| 2 | README.md | Installation từng bước, `.env.example`, Usage + lệnh chạy mẫu |
| 3 | Architecture Diagram | Data flow: Frontend/Backend ↔ LangGraph ↔ dbt ↔ Postgres ↔ Vector DB |
| 4 | PR History | ≥ 10 PRs merge vào `main`, phân bổ đều 4 thành viên |
| 5 | Video Demo | ≤ 3 phút, quay màn hình thật End-to-End, **không dùng slide tĩnh** |
| 6 | Eval Evidences | ≥ 5 test cases (Input → Output), chứng minh không hallucination |
| 7 | AI Log | Theo dõi qua **Arize Phoenix** (đã setup từ Gate 1) |
| 8 | Weekly Logs | Nộp đầy đủ qua lệnh `/weekly submit` |
| 9 | Web App UI | Ant Design, 2 vai trò Steward/Viewer hoạt động đúng phân quyền |
| 10 | ML Anomaly + AI Diagnosis | Isolation Forest/Z-score + chẩn đoán nguyên nhân gốc bằng LLM |

---

## 2. Phân công công việc

| Thành viên | Vai trò | Phạm vi cốt lõi |
|---|---|---|
| **A — Vũ Nguyễn Quốc Đạt** | Product Lead / Architect | Quản lý tiến độ, kiến trúc hệ thống, tích hợp Phoenix, duyệt PR master, quay & dựng Video Demo |
| **B — Lương Trung Chiến** | Product Owner / BA | Quản lý PRD/Scope, xây 5 Eval Test Cases, viết README.md, quản lý Weekly Logs |
| **C — Nguyễn Hoàng Vĩnh Phong** | UI/UX Lead & Frontend Engineer | React + Ant Design theo 11 màn hình wireframe, phân quyền Steward/Viewer, streaming console log |
| **D — Nguyễn Hữu Kiên** | Technical Lead & Backend/AI Engineer | LangGraph Agent (Profiler/Rule Proposer/dbt Generator/Anomaly Detector), FastAPI, Isolation Forest, Postgres, Docker |

**Ánh xạ UI Flow ↔ Feature ID:**

| Bước UI Flow | Feature ID | Người chịu trách nhiệm backend | Người chịu trách nhiệm UI |
|---|---|---|---|
| 1. Đăng nhập | — | Kiên (auth stub) | Phong (Screen 1) |
| 2. Dashboard tổng quan | F04 | Kiên | Phong (Screen 2, 11) |
| 3. Select Dataset | — | Kiên | Phong (Screen 3) |
| 4. Profiling & Rule Proposal | F01 | Kiên | Phong (Screen 4, 5) |
| 5. HITL Review | F02 | Kiên | Phong (Screen 5, 6) |
| 6. Test Execution & Anomaly | F03 + F05 | Kiên | Phong (Screen 7, 8) |
| 7. AI Diagnosis & Trend | F04+F06+F07 | Kiên | Phong (Screen 9, 10) |

---

## 3. Lộ trình triển khai theo Phase (ai làm gì, làm xong bàn giao cho ai)


### 🟦 Phase 1 — Khởi động hạ tầng (04/08 – 06/08, 3 ngày)

**Chuỗi bàn giao:** **Chiến** (chuẩn bị data) → **Kiên** (dựng DB + API) → **Đạt** (vẽ kiến trúc dựa trên schema thật) ⇉ song song **Phong** (dựng khung UI, không phụ thuộc Kiên)

| Ngày | Người | Việc làm chi tiết | Kết quả đầu ra | Bàn giao cho |
|---|---|---|---|---|
| Ngày 1 (04/08) | **Đạt** | Tạo repo trong Org `AI20K`, thiết lập branch protection cho `main` (bắt buộc PR review), dựng cấu trúc thư mục `backend/ frontend/ data/ evaluation/ docs/` | Repo sẵn sàng để cả nhóm push code | Cả nhóm bắt đầu code trên repo này |
| Ngày 1 (04/08) | **Chiến** | Thiết kế bộ dữ liệu mô phỏng (`.csv`/`.sql`) cho 4 bảng `dich_vu_xe_trips/drivers/customers/payments`, cố ý chèn lỗi bẩn (NULL khóa chính, cước âm, sai format, freshness lag) | File data mẫu trong `data/` | **D** (dùng để seed Postgres) |
| Ngày 1 (04/08) | **Phong** | Khởi tạo React + Ant Design app, cấu hình routing khung (chưa cần gọi API thật) | Skeleton frontend chạy được | Tự dùng tiếp ở Ngày 2 |
| Ngày 2 (05/08) | **Kiên** | Dựng Postgres, seed dữ liệu từ file của B vào 4 bảng; dựng FastAPI backend, endpoint `/health` | DB kết nối được, API `/health` OK, schema đã "chốt" | **A** (dùng schema thật để vẽ kiến trúc) và **D tự dùng** ở Phase 2 |
| Ngày 2 (05/08) | **Phong** | Kết nối routing với 2 role Steward/Viewer (dùng auth giả lập tạm thời, chưa cần chờ D) | Routing 2 role hoạt động | Tự dùng tiếp — sẵn sàng nhận API thật ở Phase 2 |
| Ngày 3 (06/08) | **Đạt** | Vẽ sơ đồ kiến trúc (Mermaid/PNG) dựa trên schema DB đã chốt từ D: Frontend↔FastAPI↔LangGraph Agent↔Postgres/Vector DB, nhánh log sang Phoenix | `docs/architecture.png` hoàn chỉnh | Nộp làm Deliverable 3 |
| Ngày 3 (06/08) | **Chiến** | Rà soát lại bộ data bẩn cùng D để đảm bảo đủ case cho 5 Eval Test Cases sẽ làm ở Phase 3 | Data cuối cùng đã confirm | **D** chốt seed data |
| **Mốc 1 — 23:59 ngày 06/08** | — | DB Postgres kết nối được · API `/health` chạy · UI routing 2 role hoạt động · Architecture diagram hoàn thiện | | |

---

### 🟩 Phase 2 — Core Agent & UI Flow (07/08 – 10/08, 4 ngày)

**Chuỗi bàn giao:** **Kiên** (Profiler → Rule Proposer → HITL endpoint → dbt Generator) → **Phong** (Screen 4 → 5 → 6, theo từng endpoint **Kiên** vừa xong) ⇉ song song **Đạt** (Phoenix tracing ngay khi **Kiên** merge LLM call đầu tiên) ⇉ song song **Chiến** (test thử case 1–3 ngay khi có Rule Proposer)

| Ngày | Người | Việc làm chi tiết | Kết quả đầu ra | Bàn giao cho |
|---|---|---|---|---|
| Ngày 4 (07/08) | **Kiên** | Xây **Profiler Node** trong `backend/app/agents/dq_agent.py`: đọc schema/null%/min-max từ Postgres, **tuyệt đối không đọc bản ghi chi tiết/PII** | API trả metadata profiling | **Phong** (build Screen 4) |
| Ngày 4 (07/08) | **Phong** | Nhận API profiling từ D → build **Screen 4 (Dataset Profiling & Metadata Insights)** | Screen 4 hoàn chỉnh | Tự chuyển sang Screen 5 khi **Kiên** xong Rule Proposer |
| Ngày 4 (07/08) | **Đạt** | Ngay khi D merge PR Profiler Node đầu tiên, tích hợp Phoenix tracing vào `app/main.py`, wrap LLM call để log token/latency/input-output (chỉ log kết quả suy luận, không log PII) | Phoenix bắt log ổn định | Dùng xuyên suốt các phase sau |
| Ngày 5 (08/08) | **Kiên** | Xây **Rule Proposer Node**: gửi metadata cho LLM, trả rule JSON (uniqueness/not-null/range/format/freshness) | API rule đề xuất (JSON schema chốt) | **Phong** (build Screen 5) và **Chiến** (thử case 1–3) |
| Ngày 5 (08/08) | **Phong** | Build **Screen 5 (HITL Review Table)**: cột Rule Type, AI Reason, Confidence %, Status, nút Approve/Edit/Reject | Screen 5 hiển thị đúng rule JSON từ **Kiên** | Chờ **Kiên** xong HITL endpoint để nối nút |
| Ngày 5 (08/08) | **Chiến** | Thử nghiệm Case 1 (`trip_id` NULL), Case 2 (`fare_amount < 0`), Case 3 (freshness lag) trên Rule Proposer thật, ghi log input/output, báo bug cho D nếu sai | Draft đầu của `evaluation/test_cases.json` | **Phong** (nếu phát sinh bug cần sửa) |
| Ngày 6 (09/08) | **Kiên** | Xây **HITL Interrupt State**: endpoint approve/edit/reject dừng luồng agent chờ phản hồi Steward | API HITL sẵn sàng | **Phong** (nối nút Approve/Edit/Reject vào Screen 5, build Screen 6) |
| Ngày 6 (09/08) | **Phong** | Nối API HITL vào Screen 5; build **Screen 6 (Rule Edit Modal)** — chỉnh Threshold/Severity | Screen 5 + 6 hoạt động end-to-end với backend | Sẵn sàng cho **Kiên** trigger dbt ở bước tiếp |
| Ngày 7 (10/08) | **Kiên** | Xây **dbt Generator Node**: biên dịch rule đã duyệt thành file SQL/YAML, kích hoạt `dbt test` | Rule được duyệt → sinh test dbt thật | Chuẩn bị cho Phase 3 (Screen 7 streaming log) |
| Ngày 7 (10/08) | **Đạt** | Review toàn bộ PR trong tuần, đảm bảo đã merge ≥ 6 PR vào `main` | Checkpoint PR đạt | |
| **Mốc 2 — 23:59 ngày 10/08** | — | Luồng F01–F04 (Profiler→Rule Proposer→HITL→dbt Generator) chạy end-to-end trên local · Screen 1–6 hoàn thiện · Phoenix log hoạt động · ≥ 6 PR merge | | |

---

### 🟨 Phase 3 — ML Anomaly, Eval & Đóng gói (11/08 – 13/08, 3 ngày)

**Chuỗi bàn giao:** **Kiên** (Isolation Forest → AI Diagnosis → SSE streaming) → **Phong** (Screen 7 → 8 → 9 → 10 → 11, theo từng endpoint) ⇉ song song **Kiên** (hoàn thiện case 4–5 ngay khi có Isolation Forest, rồi tổng hợp 5 test cases) ⇉ **Đạt + Kiên** (đóng gói Docker cuối phase)

| Ngày | Người | Việc làm chi tiết | Kết quả đầu ra | Bàn giao cho |
|---|---|---|---|---|
| Ngày 8 (11/08) | **Kiên** | Tích hợp **SSE endpoint** `/api/v1/tests/stream-log` đẩy stdout `dbt test` (dùng dbt Generator Node đã có từ Phase 2) | Log dbt chạy real-time qua API | **Phong** (build Screen 7) |
| Ngày 8 (11/08) | **Phong** | Build **Screen 7 (Streaming Console Log)** — giao diện terminal nhận SSE real-time | Screen 7 chạy được log thật khi Steward bấm "Run Test" | |
| Ngày 8 (11/08) | **Kiên** (song song) | Huấn luyện **Isolation Forest** (scikit-learn) trên `fare_amount`, `trip_distance`, gán cờ `-1` cho outlier | API trả danh sách anomaly | **Phong** (build Screen 8) và **Chiến** (thử Case 4–5) |
| Ngày 9 (12/08) | **Phong** | Build **Screen 8 (Anomaly Dashboard & Alert Stream)** — time-series với đốm đỏ anomaly, bảng Alert kèm nút "AI Diagnosis" | Screen 8 hiển thị đúng anomaly từ Isolation Forest | Chờ **Kiên** xong AI Diagnosis endpoint |
| Ngày 9 (12/08) | **Kiên** | Xây chuỗi **AI Diagnosis**: đưa context anomaly vào prompt LLM, sinh Root Cause Diagnosis, trả về API cho Screen 9 | API diagnosis sẵn sàng | **Phong** (build Screen 9) |
| Ngày 9 (12/08) | **Chiến** | Thử Case 4 (chuyến 0km, cước $100 → Isolation Forest gắn cờ) và Case 5 (tổng chuyến giảm 80% → cảnh báo thống kê); tổng hợp đủ 5 test cases vào `evaluation/test_cases.json` | File eval hoàn chỉnh (Deliverable 6) | Nộp cuối cùng |
| Ngày 10 (13/08) | **Phong** | Build **Screen 9 (AI Diagnosis Modal)**, **Screen 10 (Trend & Evaluation)**, **Screen 11 (Executive Viewer Dashboard, read-only)** — tái sử dụng các endpoint đã có, chỉ ẩn nút thao tác cho Viewer | 11 màn hình hoàn thiện toàn bộ | Sẵn sàng cho rehearsal Phase 4 |
| Ngày 10 (13/08) | **Đạt + Kiên** | Viết `Dockerfile` + `docker-compose.yml` đóng gói toàn bộ backend/frontend/DB; chạy thử trên máy sạch | Hệ thống chạy được bằng 1 lệnh `docker-compose up` | Deliverable 1/10 checklist |
| **Mốc 3 — 23:59 ngày 13/08** | — | Sản phẩm chạy hoàn chỉnh trên Docker · ≥ 10 PR merge · 5 test cases không hallucination | | |

---

### 🟥 Phase 4 — Nghiệm thu & Nộp bài (14/08 – 16/08, 2 ngày)

**Chuỗi bàn giao:** Cả nhóm rehearsal chung → **Đạt** quay/dựng video → **Chiến** hoàn thiện README + nộp log → **Đạt** đóng tag release

| Ngày | Người | Việc làm chi tiết | Kết quả đầu ra | Bàn giao cho |
|---|---|---|---|---|
| Ngày 11 (14/08) | **Cả nhóm** | Chạy rehearsal E2E toàn bộ luồng 7 bước UI Flow trên bản Docker, ghi nhận bug phát sinh | Danh sách bug cần fix gấp | **Phong/Kiên** fix ngay trong ngày |
| Ngày 11 (14/08) | **Phong** | Fix bug UI/tương thích API phát hiện lúc rehearsal, polish giao diện lần cuối | UI ổn định 100% | |
| Ngày 11 (14/08) | **Kiên** | Tối ưu hiệu năng API, viết thêm Unit Tests backend nếu rehearsal phát hiện lỗi luồng | Backend ổn định | |
| Ngày 11 (14/08) | **Đạt** | Quay Video Demo theo kịch bản 3 phút (0:00–0:30 bối cảnh → 0:30–1:15 profiling+rule → 1:15–2:00 HITL+dbt live log → 2:00–2:45 anomaly+AI diagnosis → 2:45–3:00 dashboard+kết luận), dùng đúng bản đã fix bug | Bản quay thô | Tự dựng ở ngày 12 |
| Ngày 11 (14/08) | **Chiến** | Review `README.md` lần cuối, chạy thử cài đặt trên máy sạch theo đúng hướng dẫn đã viết | README xác nhận chạy được | |
| Ngày 12 (15–16/08) | **Đạt** | Dựng/edit video hoàn chỉnh ≤ 3 phút, xuất file `.mp4` | Deliverable 5 hoàn tất | |
| Ngày 12 (15–16/08) | **Đạt** | Chạy `/weekly submit` cho tuần cuối, đảm bảo đủ toàn bộ Weekly Logs | Deliverable 8 hoàn tất | |
| Ngày 12 (16/08) | **Đạt** | Rà soát checklist 10 mục (Mục 4) cùng cả nhóm, đóng tag release Gate 2, nộp bài | Nộp thành công | |
| **Mốc 4 — 18:00 ngày 16/08** | — | Nộp bài thành công trên hệ thống tổ chức | | |

---

## 4. Checklist trước khi nộp Gate 2 (16/08/2026)

- [ ] 1. Codebase đã push đủ lên Org `AI20K`
- [ ] 2. README.md chạy thử thành công trên máy sạch
- [ ] 3. `docs/architecture.png` rõ nét, đầy đủ luồng dữ liệu
- [ ] 4. ≥ 10 PRs đã merge vào `main`
- [ ] 5. Video Demo `.mp4` ≤ 3 phút, quay thật end-to-end
- [ ] 6. `evaluation/` có đủ 5 test cases kèm input/output
- [ ] 7. Arize Phoenix ghi log đầy đủ các phiên LLM
- [ ] 8. Đã chạy `/weekly submit` đầy đủ các tuần
- [ ] 9. Web UI Ant Design mượt ở cả 2 role (Steward/Viewer)
- [ ] 10. Chạy thành công bằng Docker container

---

## 5. Rủi ro cần theo dõi sát

| Rủi ro | Tác động | Giảm thiểu |
|---|---|---|
| PR dồn vào cuối kỳ, không đạt 10 PR đúng hạn | Trượt Deliverable 4 | **Đạt** theo dõi số PR mỗi 2 ngày, chia nhỏ task để tăng tần suất merge |
| LLM sinh rule sai/hallucinate trên case ngoại lệ | Trượt Deliverable 6 | **Chiến** chuẩn bị case "khó" (dữ liệu sạch hoàn toàn) để kiểm tra agent không tự bịa lỗi |
| Video quay không kịp do bug phút chót | Trượt Deliverable 5 | Quay thử bản nháp cuối Phase 3, không để đến sát 16/08 |
| Phoenix log lỡ chứa dữ liệu PII | Vi phạm governance | **Kiên** review kỹ payload log trước khi merge tính năng liên quan đến Phoenix |

---
