# EVALGATE — BÁO CÁO TOÀN THƯ KIẾN TRÚC, MÃ NGUỒN & KẾT QUẢ ĐÁNH GIÁ THỰC TẾ

> **Tài liệu chuẩn:** `REPORT_PRESENT.md` (Đồng bộ theo cấu trúc chuyên sâu của `EVALGATE_REPORT.md`)  
> **Nhánh Git:** `chien` · **Hợp đồng Policy:** `1.0` · **Schema Version:** `2.0` · **Hard Gates Policy:** `7.0`  
> **Dữ liệu đánh giá:** Lấy trực tiếp từ lần chạy chứng nhận mới nhất ngày **31/08/2026** (`product-5ace0bc6893e4fc2ae1d19d832d2edbe`) đối chiếu Pinned Baseline (`product-ffd77da3e3e14473940d70e1b99f89d1`).  
> **Đối tượng:** Senior AI System Reviewer, AI Architect, Data Governance & Security Auditor.

---

# MỤC LỤC TỔNG QUAN

1. [Bản Chất & 5 Bất Biến Thiết Kế Của EvalGate](#1-bản-chất--5-bất-biến-thiết-kế-của-evalgate)
2. [Kiến Trúc Luồng Hoạt Động Toàn Trình (End-to-End Execution Flow)](#2-kiến-trúc-luồng-hoạt-động-toàn-trình)
3. [Bản Đồ 7 Cổng Chất Lượng & Phân Tích Chi Tiết Từng File, Từng Hàm, Từng Dòng Mã](#3-bản-đồ-7-cổng-chất-lượng--phân-tích-chi-tiết)
   - [Gate 1: AI Quality (Trọng số 36% — Điểm 33.33)](#31-gate-1-ai-quality-trọng-số-36--điểm-3333)
   - [Gate 2: AI Security (Trọng số 29% — Điểm 100.00)](#32-gate-2-ai-security-trọng-số-29--điểm-10000)
   - [Gate 3: Observability (Trọng số 0% — NOT_MEASURED)](#33-gate-3-observability-trọng-số-0--not_measured)
   - [Gate 4: Input Data (Trọng số 20% — Điểm 75.00)](#34-gate-4-input-data-trọng-số-20--điểm-7500)
   - [Gate 5: Reliability (Trọng số 0% — PASS / Advisory 57.14)](#35-gate-5-reliability-trọng-số-0--pass--advisory-5714)
   - [Gate 6: Governance (Trọng số 15% — Điểm 77.78)](#36-gate-6-governance-trọng-số-15--điểm-7778)
   - [Gate 7: Business (Trọng số 0% — NOT_MEASURED)](#37-gate-7-business-trọng-số-0--not_measured)
4. [Danh Mục 24 Hard Gates & Cơ Chế Kiểm Soát Chặn Phát Hành](#4-danh-mục-24-hard-gates--cơ-chế-kiểm-soát-chặn-phát-hành)
5. [Tầng Hạ Tầng Cốt Lõi (Core Infrastructure) & Cơ Chế Toán Học](#5-tầng-hạ-tầng-cốt-lõi-core-infrastructure--cơ-chế-toán-học)
6. [Hệ Thống Golden Dataset 3 Tầng & Động Cơ Đo Lỗi SDIH](#6-hệ-thống-golden-dataset-3-tầng--động-cơ-đo-lỗi-sdih)
7. [Kết Quả Đánh Giá Chi Tiết Ngày 31/08/2026 (Run Chứng Nhận `64398cf`)](#7-kết-quả-đánh-giá-chi-tiết-ngày-31082026-run-chứng-nhận-64398cf)
8. [Các Cải Tiến Mới Hoàn Thành Trong Sprint 1 Tuần (Chiến)](#8-các-cải-tiến-mới-hoàn-thành-trong-sprint-1-tuần-chiến)
9. [Hướng Dẫn Vận Hành Hệ Thống & Câu Lệnh Thực Thi Chuẩn](#9-hướng-dẫn-vận-hành-hệ-thống--câu-lệnh-thực-thi-chuẩn)

---

# 1. BẢN CHẤT & 5 BẤT BIẾN THIẾT KẾ CỦA EVALGATE

EvalGate không phải là một bộ test unit thông thường, mà là **Hệ Thống Cổng Chất Lượng Quyết Định Phát Hành (Production Release Gate)**. Hệ thống hoạt động theo chu trình bảo đảm bằng chứng khép kín:

$$\text{Measurement (Đo lường)} \longrightarrow \text{Evidence (Bằng chứng số)} \longrightarrow \text{Normalization (Chuẩn hóa)} \longrightarrow \text{Scoring (Tính điểm)} \longrightarrow \text{Policy (Chính sách)} \longrightarrow \text{Release Decision}$$

### 5 Bất Biến Thiết Kế Bắt Buộc (Enforced Architectural Invariants)

1. **Hard Gate chạy TRƯỚC Điểm Số (Safety Overrides Score):**  
   Một lỗ hổng bảo mật rò rỉ dữ liệu (`HG-S2`) hay agent bị câm hoàn toàn (`HG-A7`) không bao giờ được phép bù đắp bởi điểm số cao ở cổng khác. Nếu bất kỳ Hard Gate nào bị `FAIL` hoặc rơi vào danh sách `block_reasons`, phán quyết lập tức bị khóa tại **`RELEASE_BLOCKED`** (Exit code 3), bất kể điểm số trung bình là bao nhiêu.

2. **Trạng Thái `NOT_*` Không Bị Phạt 0 Điểm (Exclusion & Re-normalization):**  
   Khi một evaluator chưa thể thực thi do thiếu tài nguyên ngoại vi (`NOT_IMPLEMENTED`, `NOT_EXECUTED`, `BLOCKED_BY_SYSTEM_CAPABILITY`), evaluator đó bị loại hoàn toàn khỏi mẫu số tính điểm và trọng số được tái chuẩn hóa (`effective_weights`). Quy tắc này loại bỏ động cơ nguy hiểm: "xóa evaluator hỏng để tăng điểm" hoặc "phạt oan sản phẩm vì thiếu hạ tầng kiểm thử".

3. **Thuật Toán Sụp Đổ Đa Dataset Khắt Khe (MIN & P25 Collapse):**  
   Khi evaluator chạy trên nhiều bộ dữ liệu:
   * **Với Hard-gate metrics:** Lấy giá trị cực trị tệ nhất **`MIN`** (một dataset bị rò rỉ là toàn bộ hệ thống bị coi là rò rỉ).
   * **Với Score metrics:** Lấy phân vị thứ 25 **`P25`** (`np.percentile(values, 25)`), phản ánh hiệu năng ở phần tư khó khăn nhất, kiên quyết không lấy trung bình cộng để che giấu điểm yếu.

4. **Phân Biệt `KNOWN_GAP` và `REGRESSION` (Ratchet Principle):**  
   Lỗi đã tồn tại từ baseline trước đó được gắn cờ `KNOWN_GAP` và không chặn release mới; chỉ những năng lực đã đạt ở baseline mà nay bị suy thoái quá ngưỡng (`SCORE_DROP_LIMIT = 5.0`) hoặc hard gate từ PASS chuyển sang FAIL mới bị định danh là `REGRESSION` và kích hoạt chặn phát hành.

5. **Sàn Đo Lường Bắt Buộc 60% (`MIN_MEASURED_WEIGHT = 0.60`):**  
   Nếu tổng trọng số của các evaluator thực sự đo được nhỏ hơn 60% tổng trọng số danh định, hệ thống từ chối công bố điểm (`score = WITHHELD`) và trả về `INSUFFICIENT_COVERAGE` hoặc `EVALGATE_INVALID` (Exit code 5 hoặc 6), ngăn chặn tuyệt đối việc "đo ít để lấy điểm cao ảo".

---

# 2. KIẾN TRÚC LUỒNG HOẠT ĐỘNG TOÀN TRÌNH

Luồng thực thi của EvalGate (`evalgate/run.py`) được thiết kế tách bạch thành 5 giai đoạn liên hoàn:

```mermaid
flowchart TD
    Trigger["CLI / CI: python -m evalgate.run --mode {local|ci|nightly|pre_release}"] --> Stage0["STAGE 0: Baseline & Manifest Loading<br/>(approved_baseline.yaml & manifest.json)"]
    Stage0 --> Stage1["STAGE 1: Preflight Workspace Integrity<br/>(evalgate/core/workspace_integrity.py)"]
    
    Stage1 -->|Cây Git Bẩn (Dirty)| StaleBranch["Gán cờ EVALGATE_STALE (Exit 4)<br/>(Chỉ cho phép qua nếu có --allow-dirty)"]
    Stage1 -->|Cây Git Sạch (Clean)| CleanBranch["Tiếp tục luồng chính thức"]
    StaleBranch --> CleanBranch
    
    CleanBranch --> Stage2["STAGE 2: Registry Dispatch & Profile Filtering<br/>(evalgate/core/evaluator_registry.py)"]
    
    subgraph SevenGates["7 CỔNG KIỂM THỬ SẢN PHẨM"]
        G1["Gate 1: AI Quality (36%)<br/>replay, golden, enum, vacuity, outcome,<br/>anomaly_probe, sql_probe, report_probe"]
        G2["Gate 2: AI Security (29%)<br/>authz, asgi_probe, egress, secret, default_cred, upload"]
        G3["Gate 3: Observability (0%)<br/>trace_coverage (Live/Nightly)"]
        G4["Gate 4: Input Data (20%)<br/>ingest_fidelity, multi_dataset, profile_accuracy"]
        G5["Gate 5: Reliability (0% adv)<br/>config_static_check, load_slo"]
        G6["Gate 6: Governance (15%)<br/>contract, hitl, capability, served_path, policy"]
        G7["Gate 7: Business (0%)<br/>steward_outcome"]
    end
    
    Stage2 --> SevenGates
    SevenGates --> Stage3["STAGE 3: Regression Engine Comparison<br/>(evalgate/core/regression_engine.py)"]
    Stage3 --> Stage4["STAGE 4: Aggregation & Policy Enforcement<br/>(evalgate/aggregator.py)"]
    
    Stage4 --> DecCheck{Kiểm tra 24 Hard Gates<br/>& Block Reasons}
    DecCheck -->|Có vi phạm| BlockedOut["DECISION: RELEASE_BLOCKED (Exit 3)"]
    DecCheck -->|Toàn bộ Pass| CovCheck{Measured Coverage<br/>>= 60%?}
    CovCheck -->|Dưới 60%| IncovOut["DECISION: INSUFFICIENT_COVERAGE (Exit 5)<br/>Score: WITHHELD"]
    CovCheck -->|Đạt >= 60%| ScoreCheck{Weighted Quality Score}
    
    ScoreCheck -->|Score >= 85.0| PassOut["DECISION: PASS (Exit 0)"]
    ScoreCheck -->|70.0 <= Score < 85.0| WarnOut["DECISION: WARNING (Exit 1)"]
    ScoreCheck -->|Score < 70.0| FailOut["DECISION: FAIL (Exit 2)"]
    
    BlockedOut --> Stage5["STAGE 5: Artifact Rendering & History Archival<br/>(evalgate/reports/renderer.py -> report.md, result.json)"]
    IncovOut --> Stage5
    PassOut --> Stage5
    WarnOut --> Stage5
    FailOut --> Stage5
```

---

# 3. BẢN ĐỒ 7 CỔNG CHẤT LƯỢNG & PHÂN TÍCH CHI TIẾT

Dưới đây là thông số kỹ thuật, cấu trúc mã nguồn, vị trí hàm và kết quả đo lường thực tế từ run chứng nhận ngày **31/08/2026** (`product-5ace0bc6893e4fc2ae1d19d832d2edbe`).

---

## 3.1. GATE 1: AI QUALITY (Trọng số Policy: 36% · Điểm ngày 31/8: `33.33 / 100`)

> **Nhiệm vụ:** Đánh giá năng lực cốt lõi của Agent: độ chính xác phát hiện lỗi dữ liệu (SDIH), tính hợp lệ của đề xuất rule, khả năng tuân thủ prompt, tránh rule rỗng và tính đúng đắn của logic thống kê bất thường.

### 1. `governed_enum_conformance_v1` (Trạng thái 31/8: `FAIL` · Score: `0.0`)
* **Tệp mã nguồn:** [evalgate/gates/gate1_ai_quality/governed_enum_conformance.py](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/evalgate/gates/gate1_ai_quality/governed_enum_conformance.py)
* **Hàm thực thi chính:** `evaluate(context: EvalRunContext, write_evidence: bool = True) -> EvalResult`
* **Logic dòng mã:**
  * Đọc proposals từ `context.artifact("proposals")`.
  * So sánh danh sách cột bị quản chế (`governed_columns` như `payment_type`, `vendor_id`) với các rules mà AI đề xuất.
  * **Hàm `_governed_column_coverage` (dòng 58–84):** Đo tỷ lệ các cột quản chế có ít nhất một rule `ACCEPTED_VALUES`. Nếu Agent không đề xuất rule nào cho cột quản chế, `governed_column_coverage = 0.0`.
* **Kết quả quan sát 31/8:** `governed_column_coverage = 0.0` $\rightarrow$ Kích hoạt **`HG-A8 FAIL`** (Chặn phát hành).

### 2. `golden_conformance_v1` (Trạng thái 31/8: `FAIL` · Score: `60.0`)
* **Tệp mã nguồn:** [evalgate/gates/gate1_ai_quality/golden_conformance.py](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/evalgate/gates/gate1_ai_quality/golden_conformance.py)
* **Kiến trúc phân rã (Refactored Module):**
  * [golden_handlers/tier1_sdih.py](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/evalgate/gates/gate1_ai_quality/golden_handlers/tier1_sdih.py): Xử lý assertion `_min_violations`, `_max_false_positive_rate`, `_must_abstain`.
  * [golden_handlers/tier2_rules.py](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/evalgate/gates/gate1_ai_quality/golden_handlers/tier2_rules.py): Xử lý `_rule_proposed`, `_semantic_type_is`, `_parameter_bound`, `_confidence_monotonic`.
  * [golden_handlers/tier3_llm.py](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/evalgate/gates/gate1_ai_quality/golden_handlers/tier3_llm.py): Xử lý `_forbidden_tokens`, `_must_cite_numbers`, `_tools_were_used`, `_must_verify_before_asserting`.
* **Hàm thực thi:** `evaluate_case(case: GoldenCase, context: HandlerContext) -> CaseOutcome`
* **Kết quả quan sát 31/8:**
  * `golden_applicability_rate`: `0.8125` (13/16 ca áp dụng hợp lệ $\rightarrow$ **`HG-A9 PASS`** vì $> 0.5$).
  * `golden_case_pass_rate`: `0.60` (60%).
  * `golden_critical_failures`: `0` $\rightarrow$ **`HG-A5 PASS`**.
  * `golden_rule_expectation_rate`: `0.6667`.
  * `golden_prompt_compliance_rate`: `0.0` (Do chạy fallback, không qua LLM).

### 3. `vacuity_probe_v1` (Trạng thái 31/8: `WARN` · Score: `66.67`)
* **Tệp mã nguồn:** [evalgate/gates/gate1_ai_quality/vacuity_probe.py](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/evalgate/gates/gate1_ai_quality/vacuity_probe.py)
* **Hàm thực thi:** `_probe_rule_vacuity(rule: dict, df: pd.DataFrame)`
* **Logic đo lường:** Kiểm tra xem rule do AI sinh ra có bị rỗng (luôn luôn PASS hoặc không thể FAIL trên dữ liệu) hay không. Ví dụ: rule `RANGE` với min âm vô cùng và max dương vô cùng.
* **Kết quả quan sát 31/8:** `vacuous_rule_rate = 0.3333` (1/3 rule bị rỗng), `systemic_vacuous_rule_types = 0` $\rightarrow$ **`HG-A6 PASS`**.

### 4. `run_outcome_integrity_v1` (Trạng thái 31/8: `PASS` · Score: `100.0`)
* **Tệp mã nguồn:** [evalgate/gates/gate1_ai_quality/run_outcome_integrity.py](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/evalgate/gates/gate1_ai_quality/run_outcome_integrity.py)
* **Hàm thực thi:** `evaluate_outcome(run_record: dict)`
* **Logic đo lường:** Kiểm tra run gần nhất có sinh ra kết quả hợp lệ hay crash hoàn toàn (`latest_run_produced_output`), tỷ lệ vi phạm schema đầu ra (`schema_violation_rate`).
* **Kết quả quan sát 31/8:** `latest_run_produced_output = True` $\rightarrow$ **`HG-A7 PASS`**; `schema_violation_rate = 0.0` $\rightarrow$ **`HG-A2 PASS`**.

### 5. `replay_detection_v1` (Trạng thái 31/8: `FAIL` · Score: `0.0`)
* **Tệp mã nguồn:** [evalgate/gates/gate1_ai_quality/replay_evaluator.py](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/evalgate/gates/gate1_ai_quality/replay_evaluator.py)
* **Hàm thực thi:** `evaluate_replay(execution_results, ground_truth)`
* **Kết quả quan sát 31/8:** `min_recall_per_class = None` (do mẫu lỗi hiển thị bị giới hạn 20 id trong dashboard path) $\rightarrow$ Đưa vào `block_reasons`.

### 6. Bổ sung Sprint: `anomaly_logic_probe_v1` (Gate 1 — Tích hợp mới)
* **Tệp mã nguồn:** [evalgate/gates/gate1_ai_quality/anomaly_logic_probe.py](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/evalgate/gates/gate1_ai_quality/anomaly_logic_probe.py)
* **Hàm thực thi:** `test_robust_zscore_math()`, `test_db_detection_flow()`
* **Nhiệm vụ:** Kiểm tra tính chính xác của thuật toán Median/MAD trong `src/services/anomaly_service.py`, cơ chế fallback khi $\text{MAD} = 0$, và luồng chạy end-to-end trên cơ sở dữ liệu SQLite cô lập.
* **Kết quả kiểm thử:** Đạt `100.0 / 100` (`EvalStatus.PASS`).

### 7. Bổ sung Sprint: `sql_compilation_probe_v1` (Gate 1 — Tích hợp mới)
* **Tệp mã nguồn:** [evalgate/gates/gate1_ai_quality/sql_compilation_probe.py](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/evalgate/gates/gate1_ai_quality/sql_compilation_probe.py)
* **Hàm thực thi:** `test_quote_ident()`, `test_row_predicate_compilation()`
* **Nhiệm vụ:** Kiểm tra việc biên dịch an toàn các rule (`NOT_NULL`, `RANGE`, `ACCEPTED_VALUES`, `REGEX_FORMAT`, `CROSS_FIELD_COMPARISON`) sang câu lệnh SQL parameterized trong `src/agents/nodes/test_generator_node.py`, chống SQL Injection qua identifier escaping (`_quote_ident`).
* **Kết quả kiểm thử:** Đạt `100.0 / 100` (`EvalStatus.PASS`).

### 8. Bổ sung Sprint: `report_grounding_probe_v1` (Gate 1 — Tích hợp mới)
* **Tệp mã nguồn:** [evalgate/gates/gate1_ai_quality/report_grounding_probe.py](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/evalgate/gates/gate1_ai_quality/report_grounding_probe.py)
* **Hàm thực thi:** `test_report_rendering_grounding()`
* **Nhiệm vụ:** Kiểm tra template báo cáo Markdown tiếng Việt của `render_steward_report_vi` (`src/services/report_renderer.py`), bảo đảm số liệu báo cáo khớp chính xác 100% với run metadata, không bịa số liệu hay sai lệch cấu trúc.
* **Kết quả kiểm thử:** Đạt `100.0 / 100` (`EvalStatus.PASS`).

---

## 3.2. GATE 2: AI SECURITY (Trọng số Policy: 29% · Điểm ngày 31/8: `100.00 / 100`)

> **Nhiệm vụ:** Bảo vệ an ninh cấp hệ thống: kiểm soát phân quyền (Authz), chống leo thang đặc quyền (BFLA/BOLA), quét lộ lọt bí mật mã nguồn (Secret leak), ngăn chặn rò rỉ dữ liệu thô/PII và kiểm soát upload độc hại.

| Evaluator | Hàm / File Thực Thi | Metric Đầu Ra | Kết Quả Ngày 31/8 | Trạng Thái |
|---|---|---|---|:---:|
| **`authz_probe_v1`** | `evalgate/gates/gate2_security/authz_probe.py`<br/>Hàm `_scan_router(router)` | `unauthenticated_mutating_endpoints = 0`<br/>`total_endpoints_scanned = 90` | 0 vi phạm trên 90 endpoint | 🟢 **PASS (100.0)**<br/>(`HG-S1 PASS`) |
| **`asgi_behaviour_probe_v1`** | `evalgate/gates/gate2_security/asgi_behaviour_probe.py`<br/>Hàm `probe_endpoint(client, case)` | `cross_tenant_violations = 0`<br/>`role_escalation_violations = 0`<br/>`probe_cases_conclusive = 161` | 161 ca kiểm thử HTTP thực tế đều chặn đúng chuẩn (401/403/422) | 🟢 **PASS (100.0)**<br/>(`HG-S2 PASS`) |
| **`egress_probe_v1`** | `evalgate/gates/gate2_security/egress_probe.py`<br/>Hàm `_scan_transcript(transcript)` | `raw_or_pii_egress_violations = 0`<br/>`pii_column_egress_violations = 0` | Không có dòng dữ liệu thô hay PII lọt qua API | 🟢 **PASS (100.0)**<br/>(`HG-S3 PASS`) |
| **`secret_scan_v1`** | `evalgate/gates/gate2_security/secret_scan.py`<br/>Hàm `scan_repo(patterns)` | `secret_findings = 0`<br/>`tracked_files_scanned = 481` | 0 secret trong 481 tệp theo dõi Git | 🟢 **PASS (100.0)**<br/>(`HG-S6 PASS`) |
| **`default_credential_probe_v1`** | `evalgate/gates/gate2_security/default_credential_probe.py`<br/>Hàm `check_database(session)` | `default_credentials_active = False`<br/>`seeded_credential_count = 0` | Không có tài khoản admin/test mặc định | 🟢 **PASS (100.0)**<br/>(`HG-S7 PASS`) |
| **`upload_probe_v1`** | `evalgate/gates/gate2_security/upload_behaviour_probe.py`<br/>Hàm `test_upload_boundary(client)` | `malicious_upload_accepted_count = 0` | Chặn file rỗng, MIME sai, giới hạn 100MB | 🟢 **PASS (100.0)**<br/>(`HG-S4 PASS`) |

---

## 3.3. GATE 3: OBSERVABILITY (Trọng số Policy: 0% danh định / Paid & Nightly)

* **Evaluator:** `trace_coverage_v1` ([evalgate/gates/gate3_observability/trace_coverage.py](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/evalgate/gates/gate3_observability/trace_coverage.py))
* **Trạng thái ngày 31/8:** `NOT_IMPLEMENTED` (Không có dependency OpenTelemetry trong profile CI $0).
* **Xử lý trọng số:** Bị loại khỏi mẫu số (`effective_weight = 0.0`), kích hoạt Re-normalization theo đúng Bất biến số 2.

---

## 3.4. GATE 4: INPUT DATA (Trọng số Policy: 20% · Điểm ngày 31/8: `75.00 / 100`)

> **Nhiệm vụ:** Kiểm định tính toàn vẹn của dữ liệu đầu vào: không làm biến dạng dữ liệu khi ingest, phát hiện việc nuốt lỗi thành giá trị null âm thầm, và kiểm tra độ sẵn sàng trên nhiều schema dữ liệu khác nhau.

### 1. `ingest_fidelity_v1` (Trạng thái 31/8: `PASS` · Score: `100.0`)
* **Tệp mã nguồn:** [evalgate/gates/gate4_input_data/ingest_fidelity.py](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/evalgate/gates/gate4_input_data/ingest_fidelity.py)
* **Hàm thực thi:** `evaluate_fidelity(raw_frame, ingested_table)`
* **Kết quả quan sát 31/8:**
  * `row_fidelity = 100.0` (Bảo toàn số dòng 100% $\rightarrow$ **`HG-D1 PASS`**).
  * `cell_fidelity = 100.0` (Giá trị từng ô không bị thay đổi).
  * `coercion_loss_count = 0` (Không có giá trị lỗi nào bị ép ngầm thành NULL $\rightarrow$ **`HG-D2 PASS`**).

### 2. `multi_dataset_readiness_v1` (Trạng thái 31/8: `WARN` · Score: `50.0`)
* **Tệp mã nguồn:** [evalgate/gates/readiness/multi_dataset_readiness.py](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/evalgate/gates/readiness/multi_dataset_readiness.py)
* **Hàm thực thi:** `evaluate_readiness()`
* **Logic đo lường:** Kiểm tra 9 tiêu chí mở rộng hệ thống (upload surface, schema-agnostic storage, domain không bị hardcode trong prompt, hỗ trợ xóa dataset...).
* **Kết quả quan sát 31/8:** Đạt 50.0/100 (Hệ thống đã có endpoint upload và domain generic, nhưng còn 35 tệp mã nguồn gắn chặt với cấu trúc single-domain NYC Taxi).

### 3. Bổ sung Sprint: `profile_accuracy_probe_v1` (Gate 4 — Tích hợp mới)
* **Tệp mã nguồn:** [evalgate/gates/gate4_input_data/profile_accuracy_probe.py](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/evalgate/gates/gate4_input_data/profile_accuracy_probe.py)
* **Hàm thực thi:** `test_profiler_statistics_accuracy()`, `test_freshness_parsing()`
* **Nhiệm vụ:** Đo độ chính xác của `profile_database` trên tập dữ liệu kiểm chuẩn 100 dòng (10 giá trị NULL, dải min=30, max=208, 5 category riêng biệt). Đảm bảo các chỉ số `null_pct` (0.10), `distinct_count`, `min`, `max` và khoảng cách thời gian `freshness` được tính toán với sai số $0.0\%$.
* **Kết quả kiểm thử:** Đạt `100.0 / 100` (`EvalStatus.PASS`).

---

## 3.5. GATE 5: RELIABILITY (Trọng số Policy: 0% danh định / Advisory · Trạng thái: `PASS`)

* **Evaluator:** `config_static_check_v1` ([evalgate/gates/gate5_reliability/config_static_check.py](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/evalgate/gates/gate5_reliability/config_static_check.py))
* **Hàm thực thi:** `_check_timeouts()`, `_check_quotas()`
* **Kết quả quan sát 31/8 (Advisory 57.14):**
  * `db_statement_timeout_configured`: `True`
  * `upload_size_limit_configured`: `True` (Chặn 100MB tại `routes.py`)
  * `retry_policy_configured`: `True`
  * `llm_timeout_configured`: `False` (Cần bổ sung timeout tường minh khi gọi LLM)

---

## 3.6. GATE 6: GOVERNANCE (Trọng số Policy: 15% · Điểm ngày 31/8: `77.78 / 100`)

> **Nhiệm vụ:** Giám sát tuân thủ hợp đồng kỹ thuật và quy trình quản trị dữ liệu: bảo đảm quy trình Human-in-the-Loop (HITL), kiểm tra đường chạy phục vụ không bị mock, và chống suy thoái năng lực sản phẩm theo thời gian.

| Evaluator | Hàm / File Thực Thi | Metric & Phân Tích | Điểm Ngày 31/8 |
|---|---|---|:---:|
| **`capability_regression_v1`** | `evalgate/core/capability_regression.py`<br/>Hàm `compare_capabilities(current, baseline)` | `critical_capability_regressions = 0`<br/>`capability_improvements = 1`<br/>`capability_known_gaps = 8` | 🟢 **100.0**<br/>(`HG-R1 PASS`) |
| **`hitl_integrity_v1`** | `evalgate/gates/gate6_governance/hitl_integrity.py`<br/>Hàm `verify_hitl_audit(session)` | `hitl_integrity = 100.0`<br/>`unaudited_transitions = 0`<br/>`reviewer_persisted = True` | 🟢 **100.0**<br/>(`HG-G2 PASS`) |
| **`served_path_fidelity_v1`** | `evalgate/gates/gate6_governance/served_path_fidelity.py`<br/>Hàm `inspect_routes(ast_tree)` | `served_path_is_mocked = False`<br/>`llm_credential_reaches_service = True` | 🟢 **100.0**<br/>(`HG-G5 PASS`) |
| **`policy_resolution_v1`** | `evalgate/gates/gate6_governance/policy_resolution.py`<br/>Hàm `validate_yaml_assets()` | `policy_resolution_success_rate = 100.0`<br/>`required_asset_presence = 100.0` | 🟢 **100.0**<br/>(`HG-G1 PASS`) |
| **`contract_conformance_v1`** | `evalgate/gates/gate6_governance/contract_conformance.py`<br/>Hàm `check_schema_safety()` | `internal_field_exposed_count = 0` (`HG-S8 PASS`)<br/>`forgeable_actor_fields = 0` (`HG-G4 PASS`)<br/>`contract_drift_count = 1` | 🟡 **66.67** |
| **`regression_engine_v1`** | `evalgate/core/regression_engine.py`<br/>Hàm `evaluate_regression()` | `gate_score_drop_max = 0.0`<br/>`hard_gates_newly_failing = 0` | 🟢 **100.0**<br/>(`HG-R3 PASS`) |

---

## 3.7. GATE 7: BUSINESS (Trọng số Policy: 0% danh định / Live Nightly)

* **Evaluator:** `steward_behavior_v1` ([evalgate/gates/gate7_business/steward_outcome.py](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/evalgate/gates/gate7_business/steward_outcome.py))
* **Trạng thái ngày 31/8:** `NOT_MEASURED` (Yêu cầu tương tác người dùng thực tế trên giao diện, không chạy trong merge gate CI $0).

---

# 4. DANH MỤC 24 HARD GATES & CƠ CHẾ KIỂM SOÁT CHẶN PHÁT HÀNH

EvalGate quản lý **24 Hard Gates** theo định nghĩa tại [evalgate/policies/hard_gates.yaml](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/evalgate/policies/hard_gates.yaml). Đây là bức tường bảo vệ kiên cố nhất:

| ID | Cổng (Gate) | Tên Tiêu Chí (Condition) | Biểu Thức Kiểm Soát (Rule) | Trạng Thái Ngày 31/8 | Giá Trị Đo Được (Observed) |
|---|---|---|---|:---:|:---:|
| **HG-A1** | ai_quality | Recall = 0 trên defect class được inject | `min_recall_per_class <= 0` | ⚪ `NOT_EVALUATED` | *Bằng chứng bị cắt ở dashboard path* |
| **HG-A2** | ai_quality | Output có cấu trúc bị validator từ chối | `schema_violation_rate > 0` | 🟢 **PASS** | `0.0` |
| **HG-A3** | ai_quality | ACCEPTED_VALUES chấp nhận giá trị cấm | `tautological_enum_count >= 1` | ⚪ `NOT_EVALUATED` | *Không có rule để soi* |
| **HG-A5** | ai_quality | Ca kiểm thử Golden CRITICAL bị hồi quy | `golden_critical_failures >= 1` | 🟢 **PASS** | `0` |
| **HG-A6** | ai_quality | Rule rỗng về mặt cấu trúc (không thể fail) | `systemic_vacuous_rule_types >= 1` | 🟢 **PASS** | `0` |
| **HG-A7** | ai_quality | Agent chạy nhưng không sinh ra kết quả | `latest_run_produced_output == 0` | 🟢 **PASS** | `1.0` (Có sinh output) |
| **HG-A8** | ai_quality | Cột quản chế không nhận được rule nào | `governed_column_coverage < 1` | 🔴 **FAIL** | **`0.0` (Chặn Release)** |
| **HG-A9** | ai_quality | Phần lớn ca golden bị bỏ qua do không khớp | `golden_applicability_rate < 0.5` | 🟢 **PASS** | `0.8125` (13/16 ca khớp) |
| **HG-S1** | ai_security | Endpoint ghi/LLM/SQL không yêu cầu xác thực | `unauthenticated_mutating_endpoints >= 1` | 🟢 **PASS** | `0` vi phạm |
| **HG-S2** | ai_security | Đọc hoặc ghi chéo tenant (BOLA / BFLA) | `cross_tenant_violations >= 1` | 🟢 **PASS** | `0` vi phạm / 161 test |
| **HG-S3** | ai_security | Dữ liệu thô hoặc cột PII lọt qua ranh giới API | `raw_or_pii_egress_violations >= 1` | 🟢 **PASS** | `0` vi phạm |
| **HG-S4** | ai_security | Chấp nhận tệp upload độc hại/sai chuẩn | `malicious_upload_accepted_count >= 1` | 🟢 **PASS** | `0` vi phạm |
| **HG-S5** | ai_security | Prompt Injection gián tiếp điều khiển output | `indirect_injection_pass_rate < 1` | ⚪ `NOT_EVALUATED` | *Chỉ chạy tại Nightly ($)* |
| **HG-S6** | ai_security | Lộ Secret / API Key trong tệp Git | `secret_findings >= 1` | 🟢 **PASS** | `0` secret / 481 tệp |
| **HG-S7** | ai_security | Tài khoản mặc định hoạt động ngoài test | `default_credentials_active == 1` | 🟢 **PASS** | `False` |
| **HG-S8** | ai_security | Chi tiết nội bộ bị phơi ra API công khai | `internal_field_exposed_count >= 1` | 🟢 **PASS** | `0` |
| **HG-D1** | input_data | Dữ liệu bị thay đổi âm thầm khi ingest | `row_fidelity < 100` | 🟢 **PASS** | `100.0%` |
| **HG-D2** | input_data | Nuốt giá trị lỗi thành NULL không phân biệt | `coercion_loss_count >= 1` | 🟢 **PASS** | `0` |
| **HG-G1** | governance | Lỗi phân giải chính sách quản trị dữ liệu | `policy_resolution_success_rate < 100` | 🟢 **PASS** | `100.0%` |
| **HG-G2** | governance | Bỏ qua HITL: rule active không có audit | `hitl_integrity < 100` | 🟢 **PASS** | `100.0%` |
| **HG-G4** | governance | Người thực hiện do caller tự khai báo | `forgeable_actor_fields >= 1` | 🟢 **PASS** | `0` |
| **HG-G5** | governance | Served path trả kết quả giả lập (mocked) | `served_path_is_mocked == 1` | 🟢 **PASS** | `False` |
| **HG-R1** | governance | Mất năng lực đã có từ baseline | `critical_capability_regressions >= 1` | 🟢 **PASS** | `0` |
| **HG-R3** | governance | Hard gate từng pass nay bị fail | `hard_gates_newly_failing >= 1` | 🟢 **PASS** | `0` |

### Phân Tích Cơ Chế Chặn Phát Hành (Block Reasons):
Trong run chứng nhận ngày 31/08/2026, release bị chặn bởi 2 nguyên nhân cốt lõi:
1. **Lỗi trực tiếp từ `HG-A8`:** Agent kích hoạt fallback và không thể sinh rule `ACCEPTED_VALUES` cho các cột quản chế (`governed_column_coverage = 0.0 < 1.0`).
2. **Cơ chế phòng vệ `block_reasons`:**
   ```text
   - mandatory hard gate(s) not evaluated: HG-A1, HG-A3
   - mandatory evaluator(s) failed: contract_conformance_v1, golden_conformance_v1,
                                    governed_enum_conformance_v1, replay_detection_v1
   ```
   Nếu chỉ nhìn vào danh sách Hard Gate mà không có `block_reasons`, một hệ thống có thể bị đánh lừa là "chỉ trượt 1 cổng". `block_reasons` bảo đảm rằng khi các evaluator bắt buộc thất bại, release không thể bị mở trộm qua suppression.

---

# 5. TẦNG HẠ TẦNG CỐT LÕI (CORE INFRASTRUCTURE) & CƠ CHẾ TOÁN HỌC

### 5.1. Thuật Toán Tổng Hợp Điểm (Aggregator Engine) — `evalgate/aggregator.py`
Công thức tính điểm trọng số thích ứng:

$$\text{Total Measured Weight} = \sum_{g \in \text{Measured Gates}} W_g$$

$$\text{Effective Weight}_g = \frac{W_g}{\text{Total Measured Weight}}$$

$$\text{Quality Score} = \sum_{g \in \text{Measured Gates}} \left( \text{Score}_g \times \text{Effective Weight}_g \right)$$

Tại run ngày 31/08/2026:
* $\text{Total Measured Weight} = 0.36 + 0.29 + 0.20 + 0.15 = 1.00$ (Đạt $100\%$ trọng số các gate chính).
* Tổng điểm: $(33.33 \times 0.36) + (100.0 \times 0.29) + (75.0 \times 0.20) + (77.78 \times 0.15) = 12.00 + 29.00 + 15.00 + 11.67 = \mathbf{67.67}$.

### 5.2. Công Cụ Hồi Quy (Regression Engine) — `evalgate/core/regression_engine.py`
Khác với các công cụ CI thông thường so sánh trung bình gate (vốn dễ gây báo động giả khi số lượng evaluator thay đổi), `regression_engine_v1`:
* Chỉ so sánh **phần giao (intersection)** của các evaluator xuất hiện ở cả baseline và run hiện tại:
  ```python
  compared = sorted(set(current_by_evaluator) & set(baseline_by_evaluator))
  ```
* Bắt lỗi hồi quy nếu điểm của bất kỳ evaluator nào sụt giảm vượt quá ngưỡng cho phép:
  $$\Delta \text{Score} = \text{Score}_{\text{baseline}} - \text{Score}_{\text{current}} > 5.0$$
* Xác nhận run 31/8: `gate_score_drop_max = 0.0`, không có evaluator nào bị suy thoái so với mốc `45887f7`.

### 5.3. Kiểm Tra Tính Toàn Vẹn Không Gian Làm Việc — `evalgate/core/workspace_integrity.py`
* Thực thi lệnh `git status --porcelain` để kiểm tra cây làm việc.
* Đảm bảo không có tệp staging dở dang, không có tệp untracked trong thư mục sản phẩm `src/`.
* Chống lại hiện tượng "chấm điểm trên mã chưa commit": nếu cây làm việc bẩn mà không có cờ `--allow-dirty`, hệ thống lập tức thoát với mã lỗi `Exit code 4 (EVALGATE_STALE)`.

---

# 6. HỆ THỐNG GOLDEN DATASET 3 TẦNG & ĐỘNG CƠ ĐO LỖI SDIH

Hệ thống Golden Dataset của EvalGate được thiết kế theo 3 tầng độc lập, loại bỏ việc gắn cứng (hardcoded) vào một dataset đơn lẻ:

### 6.1. Tầng 1: SDIH Synthetic Fault Injection (Đo Độ Nhạy & Độ Đặc Hiệu)
* **7 Archetype kiểm chuẩn:** NYC Taxi, Synth Clinical, Synth HR, Synth IoT, Synth Retail, Synth Tiny, Synth Wide.
* **5.764 nhãn lỗi đã kiểm chuẩn:** Chứa đầy đủ các dạng khuyết tật dữ liệu (outlier, null injection, boundary violation, pattern mismatch, duplicate key).
* **Tính bất biến số học:** Được bảo vệ bởi mã băm SHA-256 trong `manifest.yaml` và kiểm tra tự động qua lệnh:
  ```bash
  python -m evalgate.golden.freeze --verify
  # Kết quả: golden tier 1: OK
  ```

### 6.2. Tầng 2 & Tầng 3: Semantic Grounding & LLM Behavioral Verification
* **Tự động phân giải ngữ nghĩa qua [applicability.py](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/evalgate/golden/applicability.py):**  
  Case kiểm thử không neo vào tên cột tĩnh (`fare_amount`) mà neo vào khái niệm ngữ nghĩa (`semantic_type: currency`), tự động ánh xạ thông qua `SemanticContract` do chính agent trích xuất.
* **Quy Trách Nhiệm Tầng Lỗi (Failure Attribution):**  
  Nếu Agent đề xuất sai rule, hệ thống truy ngược xem lỗi bắt nguồn từ đâu trong chuỗi:
  $$\text{interpretation} \longrightarrow \text{process} \longrightarrow \text{evidence} \longrightarrow \text{decision} \longrightarrow \text{negative\_space}$$
  Tránh việc đổ lỗi cho `rule_proposer_node` khi nguyên nhân thực sự nằm ở việc `dataset_understanding_node` gán sai kiểu ngữ nghĩa.

---

# 7. KẾT QUẢ ĐÁNH GIÁ CHI TIẾT NGÀY 31/08/2026 (RUN CHỨNG NHẬN `64398cf`)

### 7.1. Bảng Tổng Hợp Chỉ Số Run Chứng Nhận

| Thông Số Hệ Thống | Giá Trị Chứng Nhận Ngày 31/08/2026 |
|---|---|
| **Mã Run ID** | `product-5ace0bc6893e4fc2ae1d19d832d2edbe` |
| **Git Commit SHA** | `64398cfcb12f1772f14328eaf4b2dacdae1c5844` (Nhánh `chien-eval`) |
| **Thời điểm chạy** | `2026-08-31T10:18:05.067282+00:00` (17:18 Giờ Việt Nam) |
| **Phán quyết phát hành (Release Decision)** | **`RELEASE_BLOCKED`** (Exit code 3) |
| **Điểm chất lượng tổng hợp (Quality Score)** | **`67.67 / 100.00`** *(Công bố chính thức, không còn bị WITHHELD)* |
| **Độ phủ đo lường thực tế (Measured Coverage)** | **`72.36%`** *(Vượt xa ngưỡng sàn 60%)* |
| **Độ phủ bằng chứng bắt buộc (Mandatory Coverage)** | **`100.0%`** *(Tăng từ mức 95% của baseline)* |
| **Baseline đối chiếu** | `product-ffd77da3e3e14473940d70e1b99f89d1` (`45887f7`) |
| **Tổng số Evaluator trong Profile CI** | 31 Evaluator (15 PASS · 4 FAIL · 3 WARN · 9 `NOT_*`/BLOCKED) |
| **Tổng số Metric đo được** | 84 Metrics số học độc lập |

### 7.2. So Sánh Bước Nhảy Kỹ Thuật Với Baseline

| Tiêu Chí So Sánh | Baseline `45887f7` (30/08) | Chứng Nhận `64398cf` (31/08) | Bước Tiến Đạt Được |
|---|:---:|:---:|---|
| **Quality Score** | 70.00 | **67.67** | Điểm số trung thực, hết hiện tượng "mù lỗi" |
| **Độ phủ rủi ro (Coverage)** | 65.36% | **72.36%** | **+7.00%** độ phủ thực tế |
| **Bằng chứng bắt buộc** | 95.0% | **100.0%** | **+5.0%** đạt tuyệt đối |
| **Độ phủ Gate AI Quality** | 4 / 8 | **5 / 8** | +1 Evaluator chạy thật |
| **Độ phủ Gate Governance** | 5 / 6 | **6 / 6** | Phủ trọn vẹn 100% Gate Governance |
| **Trạng thái Regression** | N/A | **PASS (0.0 drop)** | Không phát sinh hồi quy tiêu cực |

> **Nhận định của Auditor:** Điểm số 67.67 giảm nhẹ so với 70.00 không phải vì sản phẩm suy thoái, mà vì **cổng đánh giá đã hết "mù"**. Các bài kiểm tra khắt khe hơn đã được kích hoạt thành công, phản ánh đúng bản chất hệ thống mà không che giấu lỗi.

---

# 8. CÁC CẢI TIẾN MỚI HOÀN THÀNH TRONG SPRINT 1 TUẦN (CHIẾN)

Toàn bộ 14 nhiệm vụ trong Sprint cải tiến EvalGate đã được hoàn tất 100%, bảo đảm tuân thủ quy tắc kiểm thử tự động:

1. **Sửa lỗi crash `DeterministicEvalLLM`:**
   * Cho [src/services/deterministic_eval_llm.py](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/src/services/deterministic_eval_llm.py) kế thừa từ `SimpleChatModel` và trả về `AIMessage`, sửa dứt điểm lỗi `AttributeError: spec.count(":")` khi tích hợp DeepAgents.
2. **Giải quyết nợ kỹ thuật §20.10:**
   * Tách tệp `golden_conformance.py` (từ 904 dòng) thành cấu trúc module hóa gọn gàng [golden_handlers/](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/evalgate/gates/gate1_ai_quality/golden_handlers/), tệp runner rút gọn chỉ còn ~390 dòng.
3. **Bổ sung 4 Evaluators đo lường $0 hoàn toàn mới:**
   * `anomaly_logic_probe_v1` (Gate 1 AI Quality): Đo logic Median/MAD và Isolation Forest.
   * `sql_compilation_probe_v1` (Gate 1 AI Quality): Đo biên dịch SQL predicate và chống SQL Injection.
   * `profile_accuracy_probe_v1` (Gate 4 Input Data): Đo độ chính xác null-rate và distinct của profiler.
   * `report_grounding_probe_v1` (Gate 1 AI Quality): Đo tính grounded của báo cáo tiếng Việt cho Steward.
4. **Nâng cao chất lượng kiểm thử:**
   * Self-tests của EvalGate tăng từ **316 passed $\rightarrow$ 334 passed**.
   * Toàn bộ test suite dự án đạt **430 passed, 10 skipped, 0 FAIL**.
   * Linter `ruff check .`: **All checks passed (0 lỗi)**.

---

# 9. HƯỚNG DẪN VẬN HÀNH HỆ THỐNG & CÂU LỆNH THỰC THI CHUẨN

Tất cả câu lệnh phải được thực thi trong môi trường ảo (`venv`):

### 1. Kiểm tra Linter & Code Formatting
```powershell
.\venv\Scripts\python.exe -m ruff check .
```

### 2. Chạy Kiểm Thử Toàn Bộ Hệ Thống (Pytest)
```powershell
.\venv\Scripts\python.exe -m pytest -q
```

### 3. Kiểm Tra Tính Bất Biến Golden Tier 1 Checksum
```powershell
.\venv\Scripts\python.exe -m evalgate.golden.freeze --verify
```

### 4. Tạo Artifact Bundle Từ Served FastAPI Path Thực Tế
```powershell
.\venv\Scripts\python.exe -m evalgate.product_run --profile local
```

### 5. Chạy Cổng Đánh Giá EvalGate Toàn Diện
```powershell
# Chế độ CI kiểm tra phán quyết chính thức:
.\venv\Scripts\python.exe -m evalgate.run --mode ci --manifest output/evalgate-runs/<run_id>/manifest.json

# Chế độ chẩn đoán nhanh tại local:
.\venv\Scripts\python.exe -m evalgate.run --mode ci --allow-dirty --dry-run
```

---
*Báo cáo được tổng hợp tự động từ hiện trạng mã nguồn và dữ liệu thực thi của hệ thống EvalGate.*
