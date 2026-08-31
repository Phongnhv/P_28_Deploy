# BÁO CÁO CHỈ SỐ ĐIỂM & BẢN ĐỒ KIỂM THỬ EVALGATE (BENCHMARK BASELINE)

## DỰ ÁN: RIDEPULSE DQ — HỆ THỐNG QUẢN TRỊ CHẤT LƯỢNG DỮ LIỆU & PHÁT HIỆN BẤT THƯỜNG

> **Mục đích tài liệu:** Lưu trữ mốc cơ sở (Benchmark Baseline), hướng dẫn kiểm thử toàn trình và các chỉ số đo lường chi tiết của EvalGate để làm căn cứ đối chiếu, so sánh chất lượng với các hệ thống AI Agent khác (như DeepAgent, AutoGen, CrewAI hoặc LangGraph custom).

---

## 📌 PHẦN 1: TRẠNG THÁI SERVER VẬN HÀNH (LOCAL RUNTIME)

| Thành phần          | Địa chỉ truy cập (URL)                                    |    Trạng thái    | Ghi chú & Tài liệu API                                                                                                                                    |
| :-------------------- | :------------------------------------------------------------ | :-----------------: | :----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Backend API** | [**`http://127.0.0.1:8000`**](http://127.0.0.1:8000/)  | 🟢**RUNNING** | Healthcheck:[`http://127.0.0.1:8000/health`](http://127.0.0.1:8000/health)Swagger UI: [**`http://127.0.0.1:8000/docs`**](http://127.0.0.1:8000/docs) |
| **Frontend UI** | [**`http://localhost:5173/`**](http://localhost:5173/) | 🟢**RUNNING** | Giao diện Dashboard Data Quality, Profiler & HITL Review                                                                                                    |

### 🔑 Tài khoản đăng nhập kiểm thử (Seed Accounts):

* **Data Steward:** `steward` / Mật khẩu: `steward` (Quyền cao nhất để duyệt Rule, Publish & kích hoạt Test Run).
* **Standard User:** `user` / Mật khẩu: `user` (Xem Dashboard, theo dõi chất lượng dữ liệu).

---

## 🛠️ PHẦN 2: HƯỚNG DẪN CÁC LỆNH TEST EVALGATE TRÊN TERMINAL

### 1. Chạy chẩn đoán nhanh EvalGate (Local Dry-run):

Lệnh này quét toàn bộ các probe cục bộ (AST, Security, Contracts, Vacuity, Integrity) mà không ghi file:

```bash
python -m evalgate.run --mode local --allow-dirty --dry-run
```

*(Kết quả chẩn đoán nhanh: Bắt đúng các Hard Gates vi phạm `HG-S6`, `HG-S8`, `HG-G4`, độ phủ rủi ro đo được $< 0.60 \rightarrow$ Quyết định: **`RELEASE_BLOCKED`**, điểm số: **`WITHHELD`**).*

### 2. Chạy xuất báo cáo hoàn chỉnh (Markdown + JSON):

Lệnh này sẽ chạy toàn bộ các evaluator và xuất báo cáo vào thư mục `evalgate/reports/`:

```bash
python -m evalgate.run --mode local
```

* **Báo cáo người đọc:** `evalgate/reports/report.md`
* **Dữ liệu máy đọc:** `evalgate/reports/result.json`

### 3. Chạy toàn bộ Unit Tests của EvalGate (214 bài test):

```bash
pytest evalgate/tests/
```

### 4. Xác minh tính toàn vẹn của Golden Dataset (Frozen Checksums):

```bash
python -m evalgate.golden.freeze --verify
```

---

## 📊 PHẦN 3: TỔNG QUAN PHÁN QUYẾT & BẢN ĐỒ ĐIỂM SỐ CƠ SỞ (BASELINE)

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│ 🔴 PHÁN QUYẾT TỔNG THỂ : RELEASE_BLOCKED (Cấm Release ra Production!)       │
│ 📊 ĐIỂM SỐ TỔNG HỢP    : WITHHELD / 28.03 Điểm (Độ phủ thực tế: 53.6% < 60%)│
│ 🛡️ TRẠNG THÁI HARD GATE: 15/19 Hard Gates ĐANG FAIL                         │
│ 🔍 TỔNG EVALUATOR      : 19 Evaluator chạy thật / 30 Evaluator khai báo     │
│ 💰 CHI PHÍ RUNNING CI  : $0.00 (Hoàn toàn Deterministic, $0 LLM Cost)       │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Công thức tính điểm tổng hợp:

$$
\text{Score} = (\text{Gate 1} \times 36\%) + (\text{Gate 2} \times 29\%) + (\text{Gate 4} \times 20\%) + (\text{Gate 6} \times 15\%)
$$

*(Nếu Gate nào chưa đo, trọng số được Re-normalize tự động. Nếu tổng độ phủ $< 60\%$, điểm số bị khóa lại `score = WITHHELD` để chống ảo tưởng chất lượng).*

---

## 🛡️ PHẦN 4: BẢNG CHI TIẾT 19 HARD GATES (RÀO CẢN BẮT BUỘC)

Hard Gate chạy **TRƯỚC** điểm số. Chỉ cần **1 chỉ số vi phạm** là hệ thống lập tức bị khóa Release (`RELEASE_BLOCKED`):

| Mã Hard Gate       | Gate phụ trách | Metric đo lường                     | Điều kiện vi phạm | Thực trạng hệ thống hiện tại                                                                       |
| :------------------ | :--------------- | :------------------------------------- | :-------------------: | :------------------------------------------------------------------------------------------------------- |
| **`HG-A1`** | AI Quality       | `min_recall_per_class`               |       `<= 0`       | 🔴**FAIL** — Có ít nhất một lớp lỗi AI hoàn toàn vô hình (Recall = 0%).                 |
| **`HG-A2`** | AI Quality       | `schema_violation_rate`              |        `> 0`        | 🔴**FAIL** — Output có cấu trúc bị chính validator Pydantic của sản phẩm từ chối.       |
| **`HG-A3`** | AI Quality       | `tautological_enum_count`            |       `>= 1`       | 🔴**FAIL** — AI nạp đúng chuỗi `"Invalid Payment"` vào danh sách hợp lệ.                |
| **`HG-A5`** | AI Quality       | `golden_critical_failures`           |       `>= 1`       | 🔴**FAIL** — AI đề xuất 76 luật lên chính các bảng nội bộ (`jobs`, `datasets`).     |
| **`HG-A6`** | AI Quality       | `systemic_vacuous_rule_types`        |       `>= 1`       | 🔴**FAIL** — 89.2% rule `ACCEPTED_VALUES` không thể fail (vô dụng có hệ thống).          |
| **`HG-A7`** | AI Quality       | `latest_run_produced_output`         |       `== 0`       | 🔴**FAIL** — Lần chạy gần nhất của Agent bị crash trắng tay, không ra file.               |
| **`HG-S1`** | AI Security      | `unauthenticated_mutating_endpoints` |       `>= 1`       | 🔴**FAIL** — 8 endpoint nhạy cảm (như Publish Ruleset) gọi được không cần login.         |
| **`HG-S2`** | AI Security      | `cross_tenant_violations`            |       `>= 1`       | 🔴**FAIL** — Lỗ hổng BOLA/BFLA giữa các Tenant qua ASGI probe.                                |
| **`HG-S3`** | AI Security      | `raw_or_pii_egress_violations`       |       `>= 1`       | 🔴**FAIL** — 27 vi phạm rò rỉ dữ liệu thô và thông tin cá nhân (PII).                   |
| **`HG-S4`** | AI Security      | `malicious_upload_accepted_count`    |       `>= 1`       | ⚪**DEFERRED** — Chờ endpoint upload hoàn chỉnh.                                               |
| **`HG-S5`** | AI Security      | `indirect_injection_pass_rate`       |        `< 1`        | ⚪**DEFERRED** — Chờ promptfoo live integration.                                                 |
| **`HG-S6`** | AI Security      | `secret_findings`                    |       `>= 1`       | ✅**PASS** — Không có API Key / Secret nào bị lộ trong Git tracked files.                    |
| **`HG-S7`** | AI Security      | `default_credentials_active`         |       `== 1`       | 🔴**FAIL** — Tài khoản seed (`steward/steward`) vẫn hoạt động ngoài test.                |
| **`HG-S8`** | AI Security      | `internal_field_exposed_count`       |       `>= 1`       | 🔴**FAIL** — Trường `sql_text` bị lộ ra API công khai.                                     |
| **`HG-D1`** | Input Data       | `row_fidelity`                       |       `< 100`       | ✅**PASS** — Dữ liệu sạch nạp vào đạt nguyên vẹn 100%.                                   |
| **`HG-D2`** | Input Data       | `coercion_loss_count`                |       `>= 1`       | 🔴**FAIL** — 8 giá trị lỗi bị nuốt im lặng thành `NULL` (`null_ambiguity_rate = 1.0`). |
| **`HG-G1`** | Governance       | `policy_resolution_success_rate`     |       `< 100`       | 🔴**FAIL** — Thiếu file `rule_policies.json` dẫn tới 0/7 dataset nạp được policy.        |
| **`HG-G2`** | Governance       | `hitl_integrity`                     |       `< 100`       | 🔴**FAIL** — Thao tác duyệt luật trong DB tạm không để lại bản ghi `audit_events`.     |
| **`HG-G4`** | Governance       | `forgeable_actor_fields`             |       `>= 1`       | 🔴**FAIL** — Người duyệt `reviewer` do Client tự truyền lên body.                         |
| **`HG-G5`** | Governance       | `served_path_is_mocked`              |       `== 1`       | 🔴**FAIL** — Mặc định container chạy `AGENT_MODE=mock`.                                     |
| **`HG-R1`** | Governance       | `critical_capability_regressions`    |       `>= 1`       | 🔴**FAIL** — Năng lực tính DQ Score từng bị xóa mất.                                       |
| **`HG-R3`** | Governance       | `hard_gates_newly_failing`           |       `>= 1`       | ✅**PASS** — Không có Hard Gate nào bị thoái lui mới so với baseline.                      |

---

## 📈 PHẦN 5: BẢNG CHỈ SỐ CHI TIẾT THEO 7 GATES ĐỂ SO SÁNH (BENCHMARK METRICS)

### GATE 1: AI QUALITY (Chất lượng AI sinh luật & Phát hiện lỗi) — `20.19 / 100`

* **`detection_precision = 0.088`**: Cứ 100 cảnh báo thì có ~91 báo động giả.
* **`detection_recall_macro = 0.333`**: Tỷ lệ phát hiện lỗi trung bình trên các lớp lỗi đạt 33.3%.
* **`detection_f1_macro = 0.160`**: Điểm F1 tổng hợp (Ngưỡng đạt tiêu chuẩn: $\ge 0.60$).
* **`min_recall_per_class = 0.0`**: Lớp lỗi tệ nhất đạt 0% (vô hình).
* **`vacuous_rule_rate = 0.428`**: 80/187 luật không bao giờ báo lỗi được.
* **`worst_type_vacuity_rate = 0.892`**: 89.2% luật `ACCEPTED_VALUES` rỗng (66/74 rules).
* **`degenerate_threshold_rules = 17`**: 17 luật sống nhưng thoái hóa (vd: `min_row_count = 1` trên bảng 50.000 dòng).
* **`governed_enum_conformance = 0.571`**: Enum đề xuất thiếu 3/7 giá trị hợp lệ theo policy.
* **`golden_case_pass_rate = 0.375`**: Đạt 3/8 test case chuẩn đo được.
* **`empty_run_rate = 0.80`**: 4/5 lần chạy gần nhất trắng tay không có output.

### GATE 2: AI SECURITY (Bảo mật & Phân quyền) — `25.00 / 100`

* **`unauthenticated_mutating_endpoints = 8 / 44`**: 8 endpoint thay đổi dữ liệu không cần đăng nhập.
* **`raw_or_pii_egress_violations = 27`**: 19 file lộ raw rows 21 cột, 8 file lộ cột PII.
* **`secret_findings = 0`**: Đạt chuẩn không có secret trong mã nguồn.
* **`default_credentials_active = True`**: 3 tài khoản mặc định `steward`, `user`, `admin` còn active.

### GATE 3: OBSERVABILITY (Khả năng quan sát) — `NOT_IMPLEMENTED`

* Tracing OpenTelemetry chưa được bật trong môi trường chính thức (bị comment trong requirements).

### GATE 4: INPUT DATA (Tính toàn vẹn dữ liệu đầu vào) — `37.50 / 100`

* **`row_fidelity = 100.0%`**: Dữ liệu sạch qua nạp đạt toàn vẹn tuyệt đối.
* **`coercion_loss_count = 8`**: 8 trường hợp ép kiểu sai làm mất dữ liệu (`"12,50"` $\rightarrow$ `None`, `"1e999"` $\rightarrow$ `inf`).
* **`null_ambiguity_rate = 1.0`**: 100% NULL do lỗi không phân biệt được với NULL thật.
* **`multi_dataset_readiness_score = 0.0`**: 7/7 tiêu chí mở rộng dataset đều chưa đạt.

### GATE 5: RELIABILITY (Độ tin cậy & Chịu lỗi) — `57.14 / 100`

* Đạt 4/7 tiêu chuẩn: Có `upload_size_limit`, `per_tenant_quota`, `retry_policy`, `llm_timeout`.
* Chưa đạt 3 tiêu chuẩn: Thiếu `db_statement_timeout`, `circuit_breaker`, `job_queue_out_of_process` (vẫn chạy BackgroundTasks in-memory).

### GATE 6: GOVERNANCE (Quản trị & Hợp đồng) — `37.04 / 100`

* **`policy_resolution_success_rate = 0.0`**: Không load được chính sách quản trị.
* **`hitl_integrity = 0.0`**: Duyệt luật không lưu audit trail vào CSDL.
* **`safety_rule_conformance = 2 / 6`**: Đạt 2 trên 6 quy tắc an toàn cốt lõi.
* **`internal_field_exposed_count = 1`**: Lộ trường `sql_text` qua API response.
* **`critical_capability_regressions = 1`**: Mất năng lực tính điểm DQ Score.

### GATE 7: BUSINESS VALUE (Giá trị nghiệp vụ) — `NOT_MEASURED`

* Đang chờ thu thập đủ $\ge 20$ proposal từ $\ge 3$ dataset để đo tỷ lệ chấp nhận của Data Steward.

---

## 🔬 PHẦN 6: KHUNG ĐỐI CHIẾU SO SÁNH VỚI CÁC AGENT KHÁC (VÍ DỤ: DEEPAGENT)

Khi bạn triển khai **DeepAgent** hoặc một kiến trúc Agent mới, bạn có thể dùng bảng đối chiếu sau để so sánh trực tiếp với Baseline của EvalGate:

| Tiêu chí đánh giá / Metric             | Baseline Hiện Tại (RidePulse LangGraph) | DeepAgent / Model Mới | Mức độ Cải thiện (+ / -) |
| :------------------------------------------ | :---------------------------------------: | :--------------------: | :---------------------------: |
| **Phán quyết Release (Decision)**   |            `RELEASE_BLOCKED`            |    *[Điền sau]*    |                              |
| **Điểm AI Quality Score**           |              `20.19 / 100`              |    *[Điền sau]*    |                              |
| **Detection Precision**               |                 `8.8%`                 |    *[Điền sau]*    |                              |
| **Detection Recall (Macro)**          |                 `33.3%`                 |    *[Điền sau]*    |                              |
| **Tỷ lệ Rule rỗng (Vacuity Rate)** |    `42.8%` (ACCEPTED_VALUES = 89.2%)    |    *[Điền sau]*    |                              |
| **Số Hard Gate Đạt (Pass/19)**     |                `4 / 19`                |    *[Điền sau]*    |                              |
| **Bảo mật Tenant (BOLA/BFLA)**      |       Có vi phạm (`HG-S2` Fail)       |    *[Điền sau]*    |                              |
| **Toàn vẹn Audit Log (HITL)**       |         `0.0%` (`HG-G2` Fail)         |    *[Điền sau]*    |                              |
| **Chi phí chạy CI ($ / run)**       |                 `$0.00`                 |    *[Điền sau]*    |                              |
| **Tính tất định (Deterministic)** |           `100%` (Zero drift)           |    *[Điền sau]*    |                              |
