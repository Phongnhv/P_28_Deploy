# EVALGATE — BÁO CÁO THỰC HIỆN (STAGE 2)

> **Dự án:** RidePulse DQ → Universal DQ Agent
> **Kế hoạch nguồn:** [`docs/EVALGATE IMPLEMENTATION PLAN — v2.md`](<../EVALGATE%20IMPLEMENTATION%20PLAN%20—%20v2.md>)
> **Nhánh:** `chien` · **Ngày:** 2026-08-19
> **Phạm vi thực hiện:** Nhóm A — $0 chi phí LLM, không cài thêm dependency, không sửa file hiện có
> **Trạng thái:** ✅ Hoàn thành · **Chưa push lên Git**

---

## 1. TÓM TẮT ĐIỀU HÀNH

EvalGate đã chạy được end-to-end và cho ra kết quả thật đầu tiên của dự án:

```
decision: RELEASE_BLOCKED     score: 9.97 / 100     exit code: 3
hard gates FAIL (4): HG-A1, HG-S1, HG-S3, HG-G1
hard gates PASS (1): HG-S6
hard gates NOT_EVALUATED (7)
```

| Chỉ số                        | Giá trị                                                               |
| ------------------------------- | ----------------------------------------------------------------------- |
| File**thêm mới**        | **49** (3,753 dòng Python)                                       |
| File**sửa**              | **0**                                                             |
| File**xoá / đổi tên** | **0**                                                             |
| Dependency cài thêm           | **0**                                                             |
| Chi phí LLM                    | **$0.00**                                                         |
| Test EvalGate                   | **26/26 xanh** (1.52s)                                            |
| Gate chạy được              | 5 / 7                                                                   |
| Evaluator chạy được         | 7 / 18 (11 cái còn lại được khai báo tường minh, không giấu) |

---

## 2. PHẠM VI: LÀM GÌ VÀ KHÔNG LÀM GÌ

### 2.1. Đã làm

| Phase     | Nội dung                                                                       | Trạng thái |
| --------- | ------------------------------------------------------------------------------- | ------------ |
| Phase 1   | Evaluation Core — schemas, normalizers, aggregator, policies, run.py, renderer | ✅           |
| Phase 2   | SDIH + Corpus 7 archetype                                                       | ✅           |
| Phase 6   | Gate 6 Governance — policy resolution (HG-G1)                                  | ✅           |
| Phase 3′ | **Gate 1 REPLAY MODE** (bổ sung ngoài plan — xem §6.1)                | ✅           |
| Phase 4′ | Gate 2 Security — authz, egress, PII classifier, secret scan                   | ✅           |
| Phase 9′ | Gate 5A — config static check                                                  | ✅           |
| Bonus     | Multi-Dataset Readiness Score                                                   | ✅           |
| —        | Self-test 26 case                                                               | ✅           |

### 2.2. Chưa làm — và lý do

Đây là những evaluator được **khai báo tường minh trong `run.py`** để chúng hiện lên trong báo cáo với trạng thái rõ ràng, thay vì biến mất im lặng:

| Evaluator                       | Trạng thái                     | Lý do                                                                 |
| ------------------------------- | -------------------------------- | ---------------------------------------------------------------------- |
| `ingest_fidelity_v1`          | `BLOCKED_BY_SYSTEM_CAPABILITY` | Chưa có endpoint upload                                              |
| `upload_probe_v1`             | `BLOCKED_BY_SYSTEM_CAPABILITY` | Chưa có endpoint upload để gửi file độc hại                    |
| `generalization_evaluator_v1` | `BLOCKED_BY_SYSTEM_CAPABILITY` | 6/7 dataset corpus không nạp được vào hệ thống                 |
| `promptfoo_injection_v1`      | `NOT_EXECUTED`                 | Cần`npx promptfoo` + mạng + tiền LLM                              |
| `geval_domain_v1`             | `NOT_EXECUTED`                 | Cần`deepeval` + tiền LLM                                           |
| `gx_suite_builder_v1`         | `NOT_IMPLEMENTED`              | Cần`great-expectations`                                             |
| `evidently_drift_v1`          | `NOT_IMPLEMENTED`              | Cần`evidently`                                                      |
| `trace_coverage_v1`           | `NOT_IMPLEMENTED`              | Deps OTel đang bị comment; instrumentation bị`except: pass` nuốt |
| `k6_load_v1`                  | `NOT_EXECUTED`                 | Load test cần phê duyệt riêng                                      |
| `steward_behavior_v1`         | `NOT_MEASURED`                 | DB có < 3 dataset và < 20 proposal                                   |
| `hitl_integrity_v1`           | `NOT_MEASURED`                 | Nhánh legacy không ghi audit event                                   |

---

## 3. CHI TIẾT TỪNG BƯỚC THỰC HIỆN

### Bước 1 — Dựng khung thư mục

Kiểm tra trước: `ls -d evalgate evalgate_proposed` → cả hai không tồn tại ⇒ không có nguy cơ ghi đè. Tạo 14 thư mục theo đúng cấu trúc §11.1 của plan.

### Bước 2 — Evaluation Result Contract

**File:** `evalgate/schemas/eval_result.py`

- `EvalStatus` — 10 trạng thái, gồm `BLOCKED_BY_SYSTEM_CAPABILITY` mà plan yêu cầu
- `MetricValue` tách `raw` / `unit` / `normalized` ⇒ **không thể cộng nhầm hai metric khác thang đo**
- `EvalResult` với `per_dataset_breakdown` bắt buộc, `model_config = extra="forbid"`
- `EXCLUDED_FROM_AGGREGATE` — frozenset các trạng thái bị loại khỏi tổng

**Đã sửa trong quá trình làm:** `Threshold` khai báo `model_config` hai lần (Python lấy cái sau, vẫn chạy nhưng sai ý đồ) → gộp thành một.

### Bước 3 — Normalizers

**File:** `evalgate/normalizers/normalizers.py` — 10 normalizer + `percentile()` + `stdev()` thuần Python (không phụ thuộc numpy).

Kiểm chứng: `ratio(0.032)=3.2` · `variance(0.15)=70.0` · `zero_tolerance(1)=0.0` · `percentile([...],0.25)=31.6`

### Bước 4 — Policies

**File:** `evalgate/policies/{weights,thresholds,hard_gates}.yaml`

Kiểm chứng: tổng trọng số = **1.0** chính xác; **12 hard gate** đúng như §10.5.

### Bước 5 — Aggregator

**File:** `evalgate/aggregator.py`

Ba quy tắc của plan được cài đặt và test riêng:

1. Hard gate đánh giá **trước** aggregate; score không override được
2. `collapse_per_dataset()` — **MIN** cho hard-gate metric, **P25** cho score metric
3. `re_normalize_weights()` — loại trạng thái `NOT_*`, scale phần còn lại lên 1.0

Kiểm chứng khớp **chính xác** ví dụ §10.4 của plan (loại Business 7%):

```
ai_quality 30.1%  ai_security 23.7%  input_data 16.1%
governance 12.9%  observability 8.6%  reliability 8.6%     tổng = 1.0
```

`_evaluate_rule()` dùng một DSL đóng (chỉ ký tự số và toán tử so sánh) thay vì `eval` tự do.

### Bước 6 — SDIH (trái tim EvalGate)

**File:** `evalgate/sdih/{defect_taxonomy,profiler,injector,label_store,verifier}.py`

- `defect_taxonomy.py` — 10 defect class + điều kiện áp dụng + `DQ_DIMENSION` + `DIFFICULTY` (EASY/MEDIUM/HARD, để recall không thể cao nhờ toàn lớp dễ) + `EXPECTED_RULE_TYPES`
- `profiler.py` — profiler tự chứa, **không** import `src/` (để profiling được DataFrame trước khi nó chạm vào sản phẩm)
- `injector.py` — `build_plan()` → `inject()`, dùng `np.random.default_rng(seed)`, vị trí inject **disjoint** giữa các class
- `label_store.py` — nhãn cell-level + `fingerprint()` SHA-256 để test tính tái lập
- `verifier.py` — assert từng nhãn khớp dữ liệu thật; fail ⇒ `BLOCKED_MISSING_GROUND_TRUTH`

**Hai lỗi đã phát hiện và sửa trong quá trình test:**

| Lỗi                             | Triệu chứng                                                                                                  | Cách sửa                                                                          |
| -------------------------------- | -------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| Cột`bool` bị coi là numeric | `pandas.quantile()` ném `TypeError: numpy boolean subtract` trên `corpus-synth-hr`                     | Tách`is_bool` khỏi `is_numeric` trong profiler                                |
| Tra nhãn theo`row_id` bị vỡ | 51 + 26 verify failure — vì SDIH tự sửa chính cột ID (MISSING_VALUE null hoá ID, DUPLICATE_ROW copy ID) | Thêm`row_pos` vào `CellLabel`; verifier ưu tiên tra theo **vị trí** |

Ngoài ra `verifier` được nâng cấp để assert **thật** thay vì chỉ kiểm tra not-null:

- `CROSS_FIELD_VIOLATION` → phải thực sự `start > end`
- `DUPLICATE_ROW` → giá trị key phải xuất hiện > 1 lần

**Kết quả cuối — SDIH chạy sạch trên cả 7 archetype:**

| Dataset               | Rows  | Cols | Class inject được | Verify  |
| --------------------- | ----- | ---- | -------------------- | ------- |
| corpus-synth-retail   | 3,000 | 15   | 10/10                | ✅ PASS |
| corpus-synth-clinical | 3,000 | 15   | 10/10                | ✅ PASS |
| corpus-synth-hr       | 2,000 | 18   | 10/10                | ✅ PASS |
| corpus-synth-iot      | 3,000 | 8    | 8/10                 | ✅ PASS |
| corpus-synth-wide     | 1,000 | 220  | 8/10                 | ✅ PASS |
| corpus-synth-tiny     | 50    | 3    | 8/10                 | ✅ PASS |
| corpus-nyc-taxi-50k   | 3,000 | 21   | 8/10                 | ✅ PASS |

Các class không áp dụng được đều được ghi vào `not_applicable_classes` — **không bị chấm recall = 0**, đúng nguyên tắc §17 của plan.

### Bước 7 — Corpus Generator

**File:** `evalgate/corpus/generator.py` — 7 archetype deterministic, không cần mạng, không license.

`corpus-synth-wide` cố ý có **220 cột** để chạm giới hạn `ProposalEvidence` (64 cột).

### Bước 8 — Xử lý defect có sẵn của NYC (sửa lỗi C2)

**File:** `evalgate/corpus/nyc_preexisting.py`

Fixture NYC 50k **đã có sẵn 1,250 dòng lỗi** gieo ở `MUTATION_SEED = 1337`. Plan v2 coi nó là dataset sạch. Nếu SDIH gieo chồng lên mà không biết, hai hậu quả xảy ra: SDIH sẽ né đúng các cột đã có lỗi, và mọi lỗi có sẵn mà agent bắt được sẽ bị tính là **false positive** ⇒ precision cao giả tạo.

Module này khôi phục nhãn từ chính dữ liệu, gắn `origin="preexisting"`.

### Bước 9 → 13 — Các Gate

| File                                               | Gate | Kỹ thuật                                                                             |
| -------------------------------------------------- | ---- | -------------------------------------------------------------------------------------- |
| `gates/gate1_ai_quality/replay_evaluator.py`     | 1    | Chấm điểm 7 artifact run đã lưu, không gọi agent                               |
| `gates/gate2_security/authz_probe.py`            | 2    | AST walk`src/api/routes.py` — không cần chạy server, không đụng DB            |
| `gates/gate2_security/pii_classifier.py`         | 2    | Regex + token tên cột;**fail-closed** với FREE_TEXT                           |
| `gates/gate2_security/egress_probe.py`           | 2    | Kết hợp tín hiệu tĩnh (code) + thực nghiệm (artifact trên đĩa)               |
| `gates/gate2_security/secret_scan.py`            | 2    | Chỉ quét file`git ls-files`; báo cáo prefix, **không bao giờ in secret** |
| `gates/gate6_governance/policy_resolution.py`    | 6    | Gọi thật`get_dataset_rule_policy()` trên 7 dataset                                |
| `gates/gate5_reliability/config_static_check.py` | 5A   | 7 control boolean                                                                      |
| `gates/readiness/multi_dataset_readiness.py`     | —   | 7 chiều có trọng số                                                                |

### Bước 14 — Orchestrator + Renderer

**File:** `evalgate/run.py`, `evalgate/reports/renderer.py`

Exit code là hợp đồng với CI: `0 PASS · 1 WARNING · 2 FAIL · 3 RELEASE_BLOCKED`. Đã kiểm chứng exit code thật = **3**.

### Bước 15 — Self-test

**File:** `evalgate/tests/test_evalgate.py` — **26 test, xanh toàn bộ trong 1.52s**

Bao gồm: normalizer boundary · re-normalize · MIN/P25 · hard gate override · `BLOCKED_BY_SYSTEM_CAPABILITY` không kéo điểm · SDIH schema-agnostic (7 archetype) · tính tái lập theo seed · seed khác cho nhãn khác · vị trí inject không chồng nhau · **verifier phát hiện được nhãn bị làm giả** · bool không phá profiler · `preexisting_labels` được giữ.

**Một lỗi trong chính test đã sửa:** gán chuỗi `"restored"` vào cột `float64` → `TypeError`. Sửa thành khôi phục giá trị gốc đúng dtype.

### Bước 16 — Sửa false positive HG-S6

Lần chạy đầu HG-S6 **FAIL** với 3 finding. Kiểm tra thủ công: cả 3 đều là chuỗi ví dụ (`postgresql://postgres:password@localhost:5432/dbname` trong `.env.example`, `docs/guide/chapter-07.md`, `chapter-09.md`). Đã bổ sung `DOC_PATH_HINTS` và `PLACEHOLDER_SECRETS`.

Sau khi sửa: **HG-S6 PASS, 0 finding / 293 file tracked**. Một hard gate báo sai còn nguy hiểm hơn không có hard gate — nó làm người ta ngừng đọc báo cáo.

---

## 4. FILE ĐÃ THÊM / SỬA / XOÁ

### 4.1. ✅ SỬA: **0 file** · ✅ XOÁ: **0 file** · ✅ ĐỔI TÊN: **0 file**

Kiểm chứng:

```
$ git status --short
?? "docs/EVALGATE IMPLEMENTATION PLAN — v2.md"
?? evalgate/

$ git status --short | grep -v '^??'
(rỗng — không có dòng M / D / R nào)
```

### 4.2. THÊM MỚI — 49 file

**Core (7)**

```
evalgate/__init__.py                    evalgate/pyproject.toml
evalgate/.gitignore                     evalgate/run.py
evalgate/aggregator.py                  evalgate/schemas/eval_result.py
evalgate/normalizers/normalizers.py
```

**Policies (3)** — `weights.yaml` · `thresholds.yaml` · `hard_gates.yaml`

**SDIH (5)** — `defect_taxonomy.py` · `profiler.py` · `injector.py` · `label_store.py` · `verifier.py`

**Corpus (2)** — `generator.py` · `nyc_preexisting.py`

**Gates (6)** — `replay_evaluator.py` · `authz_probe.py` · `pii_classifier.py` · `egress_probe.py` · `secret_scan.py` · `policy_resolution.py` · `config_static_check.py` · `multi_dataset_readiness.py`

**Report + test (3)** — `reports/renderer.py` · `tests/test_evalgate.py` · báo cáo này

**Sinh ra lúc chạy (9)** — 7 file evidence JSON + `reports/report.md` + `reports/result.json` (đã `.gitignore` trong `evalgate/.gitignore`)

**`__init__.py` (14)**

### 4.3. File hiện có được ĐỌC (không sửa)

`src/api/routes.py` · `src/services/dashboard_agent_workflow.py` · `src/agents/nodes/test_runner_node.py` · `src/agents/nodes/steward_insights_node.py` · `src/models/database.py` · `src/agents/nodes/templates.py` · `scripts/migrations/*.sql` · `output/reports/*.json` · `output/test_runner/*.json` · `data/yellow_tripdata_2025/semantic_data/*`

---

## 5. KẾT QUẢ ĐO ĐƯỢC

### 5.1. Bảng điểm

| Gate            | Score          | Trọng số hiệu dụng | Ghi chú                  |
| --------------- | -------------- | ---------------------- | ------------------------- |
| ai_quality      | 0.00           | 32.9%                  | P25 qua 7 run đã lưu   |
| ai_security     | 33.33          | 25.9%                  | 1/3 evaluator PASS        |
| input_data      | 0.00           | 17.6%                  | = Readiness Score         |
| governance      | 0.00           | 14.1%                  | policy resolution 0/7     |
| observability   | n/a            | *loại*              | `NOT_IMPLEMENTED`       |
| reliability     | 14.29          | 9.4%                   | 1/7 control               |
| business        | n/a            | *loại*              | `NOT_MEASURED`          |
| **TỔNG** | **9.97** | 100%                   | **RELEASE_BLOCKED** |

### 5.2. Hard gates

| ID               | Trạng thái      | Quan sát                                   |
| ---------------- | ----------------- | ------------------------------------------- |
| HG-A1            | ❌**FAIL**  | `min_recall_per_class = 0.0`              |
| HG-S1            | ❌**FAIL**  | 8 endpoint mutating không xác thực       |
| HG-S3            | ❌**FAIL**  | 27 vi phạm egress (19 raw row + 8 PII)     |
| HG-G1            | ❌**FAIL**  | policy resolution 0/7 dataset               |
| HG-S6            | ✅**PASS**  | 0 secret / 293 file tracked                 |
| 7 gate còn lại | `NOT_EVALUATED` | Metric chưa được evaluator nào sinh ra |

### 5.3. Số liệu chi tiết

**Gate 1 — Replay (7 run đã lưu):**

| Metric                     | Raw    |
| -------------------------- | ------ |
| `detection_precision`    | 0.0884 |
| `detection_recall_macro` | 0.3333 |
| `detection_f1_macro`     | 0.1598 |
| `min_recall_per_class`   | 0.0    |

Run tốt nhất (`932ce25f`, 31 rule): recall theo class = `SIGN_FLIP 1.00` · `INVALID_CATEGORY 0.00` · `DUPLICATE_ROW 0.00`

**Gate 2:**

| Metric                                 | Raw                                              |
| -------------------------------------- | ------------------------------------------------ |
| `unauthenticated_mutating_endpoints` | **8** (`dq_router` 7 + `POST /jobs` 1) |
| `unauthenticated_read_endpoints`     | 6                                                |
| `total_endpoints_scanned`            | 44                                               |
| `raw_row_egress_violations`          | 19                                               |
| `pii_column_egress_violations`       | 8                                                |
| `secret_findings`                    | 0                                                |

**Gate 5A** — 1/7 control (chỉ `retry_policy`). `job_queue_out_of_process = False`, `BackgroundTasks` in-process dùng ở **12 chỗ**.

**Multi-Dataset Readiness Score = 0.0 / 100** — 7/7 chiều đều `False`, 34 file khớp NYC schema.

---

## 6. BỐN SAI LỆCH SO VỚI PLAN v2 ĐÃ SỬA

### 6.1. C1 — Thêm `--mode replay` cho Gate 1

**Vấn đề:** Plan xếp Phase 3 (Gate 1) trước Phase 6 (Gate 6). Nhưng agent hiện `raise AgentWorkflowError` cho mọi dataset ⇒ Gate 1 sẽ `BLOCKED` cho **7/7**, không phải 6/7. Đồng thời §18 của plan ghi *"NYC: F1 ≈3%"* — con số này đến từ artifact ngày 16/08, không phải từ một lần chạy live.

**Đã sửa:** Viết `replay_evaluator.py` chấm điểm artifact đã lưu. Cho ra số thật, $0, không cần sửa source.

### 6.2. C2 — `preexisting_labels` cho NYC

**Vấn đề:** Plan coi `corpus-nyc-taxi-50k` là dataset sạch (§14.1).

**Đã sửa:** `nyc_preexisting.py` + tham số `preexisting_labels` và `skip_columns` trong `build_plan()`/`inject()`.

### 6.3. C4 — `corpus-synth-wide` sẽ chết ở Pydantic

Plan mô tả dataset 220 cột để *"test prompt budget"*. Thực tế `ProposalEvidence.columns` là `Field(max_length=64)` ⇒ `ValidationError`, run chết, không có proposal để chấm. Readiness Score đã ghi nhận dưới chiều `evidence_column_cap_sufficient = False`.

### 6.4. Đổi thứ tự phase

Thực hiện theo **1 → 2 → 6 → 3′ → 4′ → 9′**, đúng như chính §17 của plan yêu cầu (*"Gate 6 chạy trước Gate 1"*), thay vì 1→2→3→4 như §15.

---

## 7. PHÁT HIỆN MỚI TRONG QUÁ TRÌNH THỰC HIỆN

### 7.1. 🔴 Ground truth của fixture NYC đã bị phá huỷ từ khâu tiền xử lý

Khôi phục nhãn từ dữ liệu thật cho kết quả:

| Lớp lỗi                  | Báo cáo ghi | Khôi phục được | Chênh        |
| -------------------------- | ------------- | ------------------- | ------------- |
| `null_vendor_id`         | 250           | **0**         | ❌ biến mất |
| `negative_fare_amount`   | 250           | **2,334**     | +2,084        |
| `negative_trip_distance` | 250           | 250                 | ✅ khớp      |
| `invalid_payment_type`   | 250           | 250                 | ✅ khớp      |
| `duplicate_fingerprint`  | 250           | 264                 | +14           |

Nguyên nhân, xác minh trực tiếp trên dữ liệu:

```
vendor_id   : "Unknown Vendor"                  → 250 dòng   (không phải NULL)
payment_type: "Invalid Payment (Dispute/Test)"  → 250 dòng
```

**250 giá trị NULL gieo vào `vendor_id` đã bị semantic transform thay bằng chuỗi `"Unknown Vendor"`.** Nghĩa là rule `vendor_id.NOT_NULL` báo PASSED là **đúng** — không còn null nào để bắt. Lỗi nằm ở khâu chuẩn bị dữ liệu, không phải ở agent.

Đây là xác nhận chính xác giả thuyết `root_cause_hint` mà plan v2 nêu ở §9.

### 7.2. 🟠 Con số precision 1.71% trong plan không vững

Plan (và bản kiểm chứng trước đó của tôi) tính `precision = 500 / 29,238 = 1.71%`, giả định đúng 500 lỗi thật.

Thực tế `fare_amount` có **2,334** giá trị âm chứ không phải 250 — phần lớn là dữ liệu gốc NYC (hoàn tiền/điều chỉnh). Với ground truth khôi phục đầy đủ, precision đo lại được là **8.84%**, recall macro **33.3%**, F1 **15.98%**.

Đây là một **đính chính cho cả plan v2 lẫn nhận định trước đó của tôi**: con số cũ tái lập được về mặt số học nhưng mẫu số sai.

Phân rã chính xác của `fare_amount.RANGE` (4,557 vi phạm): 2,334 âm + 2,223 vượt ngưỡng 57.2 — khớp tuyệt đối.

### 7.3. 🟠 52% dòng bị gắn cờ đến từ 2 rule không có lớp lỗi tương ứng

Hai rule `NULL_RATE` (`passenger_count`, `store_and_fwd_flag`) mỗi cái gắn cờ 7,672 dòng = **15,344 / 29,238 (52%)**, trong khi bộ lỗi gieo không có lớp nào tương ứng.

### 7.4. 🟠 `HG-S3`: chuỗi rò rỉ có 4 nhánh, và có cột định danh trực tiếp

19 artifact đã lưu chứa raw row đầy đủ; 8 trong số đó có cột được phân loại PII. Cột phát hiện được: **`license_plate`**.

Đường đi: `_fetch_sample_failures()` `SELECT *` → `sample_failures` → (1) DB, (2) đĩa, (3) API không auth, (4) **`steward_insights_node` serialize `failed_or_error_rules[:15]` vào `failed_rules_json` gửi sang LLM provider** — raw row đi kèm mà không hề được gọi tên trong prompt.

---

## 8. CÁCH CHẠY

```bash
# Dùng venv/ — LƯU Ý: .venv/ KHÔNG có pytest
cd "<project-root>"

# Chạy toàn bộ EvalGate ($0, không mạng)
venv/Scripts/python.exe -m evalgate.run --mode local

# Chạy thử, không ghi evidence/report
venv/Scripts/python.exe -m evalgate.run --mode local --dry-run

# Self-test
venv/Scripts/python.exe -m pytest evalgate/tests/ -q
```

**Đầu ra:** `evalgate/reports/report.md` · `evalgate/reports/result.json` · `evalgate/evidence/**`

**Exit code:** `0` PASS · `1` WARNING · `2` FAIL · `3` RELEASE_BLOCKED

---

## 9. CÁC BƯỚC CẦN LÀM ĐỂ ÁP DỤNG

### 9.1. Bước 0 — Quyết định cần bạn duyệt trước tiên

**Khôi phục `src/resources/` từ `origin/main`.** Đây là **1 lệnh git ghi file**, tôi chưa thực hiện vì chưa được duyệt.

```bash
git checkout origin/main -- src/resources/
```

Tác động:

- Đưa test suite hiện tại từ **19 đỏ** về xanh (`test_dataset_fixture` 4 · `test_dataset_loader` 4 · `test_dashboard_agent_workflow` 11)
- `HG-G1` chuyển từ *"hệ thống không chạy được"* về đúng trạng thái plan muốn đo: *"policy không resolve được cho dataset lạ"*
- Mở đường cho Gate 1 chạy **live mode** về sau

⚠️ Không khôi phục thì mọi công việc EvalGate tiếp theo vẫn chạy được (replay mode không phụ thuộc), nhưng CI của dự án vẫn đỏ và Gate 1 vĩnh viễn chỉ ở chế độ replay.

### 9.2. Gắn EvalGate vào CI

Tạo workflow **mới** — **không sửa** [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml):

```yaml
# .github/workflows/evalgate.yml
name: EvalGate
on: [pull_request]
jobs:
  evalgate:
    runs-on: self-hosted
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -r requirements.txt
      - run: python -m evalgate.run --mode ci --out evalgate/reports
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: evalgate-report
          path: evalgate/reports/
```

⚠️ **Bẫy đã phát hiện:** `numpy` và `pyyaml` **không được khai báo trực tiếp** trong [`requirements.txt`](../../requirements.txt) (chỉ có transitively qua pandas/dbt). Tương tự `sklearn`, `scipy`, `tiktoken` đã cài trong `venv/` nhưng không có trong requirements. Nếu CI cài từ requirements, các gói này có thể vắng mặt. → Cài EvalGate bằng `pip install -e evalgate/` (đã pin đầy đủ trong `evalgate/pyproject.toml`).

⚠️ **Bẫy thứ hai:** `pytest tests/` (test suite của dự án) **treo > 5 phút** trên máy này. Cần điều tra trước khi ghép vào cùng một job, nếu không mọi PR sẽ timeout.

### 9.3. Mở khoá các gate còn lại

| Muốn có                     | Cần cài                                                                                                  | Chi phí   | Mở khoá                                                     |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------- | ---------- | ------------------------------------------------------------- |
| Gate 4B — GX suite auto-sinh | `pip install -e "evalgate/[gx]"`                                                                         | $0         | `gx_suite_builder_v1`                                       |
| Gate 1E — GEval domain       | `pip install -e "evalgate/[judge]"`                                                                      | ~$0.08/run | `geval_domain_v1`                                           |
| Gate 3 — Trace coverage      | `pip install -e "evalgate/[tracing]"` + bỏ `except: pass` ở `src/main.py`, `src/agents/graph.py` | $0         | `trace_coverage_v1` ⚠️ **cần sửa file hiện có** |
| Gate 4C — Drift              | `pip install -e "evalgate/[drift]"`                                                                      | $0         | `evidently_drift_v1`                                        |
| Gate 2E — Injection          | `npx promptfoo` (node v24.15.0 đã có sẵn)                                                            | ~$0.22/run | `promptfoo_injection_v1`                                    |
| Gate 5C — Load               | k6 binary + cờ`--allow-load-test`                                                                       | $0         | `k6_load_v1`                                                |

### 9.4. Mở khoá bằng cách sửa sản phẩm (ngoài phạm vi EvalGate)

| Muốn có               | Sản phẩm cần thêm                       |
| ----------------------- | ------------------------------------------- |
| Gate 1B generalization  | Endpoint upload + lưu trữ schema-agnostic |
| Gate 4A ingest fidelity | Như trên                                  |
| Gate 2D upload probe    | Như trên                                  |
| Gate 2B BOLA probe      | `DatasetModel.owner` / `tenant_id`      |
| Gate 6B HITL integrity  | Nhánh legacy ghi audit event               |
| Gate 7 Business         | ≥ 3 dataset và ≥ 20 proposal trong DB    |

### 9.5. Đề xuất bổ sung ngoài plan v2

Plan không có gate nào đo hai vùng sau, cả hai đều làm được với chi phí $0:

1. **`1F anomaly_detection_f1`** — SDIH gieo defect tăng dần qua N run rồi đo z-score/EMA có bắt được spike không. Hiện `anomaly_detector_node` và `dashboard_anomaly` là hai bản song song với tham số khác nhau, không bản nào được đo.
2. **`1G repair_loop_integrity`** — `llm_dbt_repair_node` (tối đa 3 attempt) là bề mặt hallucination còn lại duy nhất trong luồng execution; không có gì đo số lần repair, tỉ lệ thành công, hay repair có bịa cột không.

---

## 10. XÁC NHẬN RÀNG BUỘC

```
✅ Không sửa bất kỳ file hiện có nào       (git status: 0 dòng M/D/R)
✅ Không xoá / đổi tên file nào
✅ Không cài thêm dependency
✅ Không gọi LLM                            ($0.00)
✅ Không truy cập mạng
✅ Không git add / commit / push
✅ Mọi thứ nằm dưới evalgate/ + báo cáo này
✅ Evidence không chứa secret               (secret_scan chỉ in prefix)
```

---

> **Người thực hiện:** Claude (Stage 2, phạm vi Nhóm A)
> **Cần duyệt tiếp:** §9.1 khôi phục `src/resources/` · §9.3 cài dependency · §9.5 hai gate bổ sung
