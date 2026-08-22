# EVALGATE — BÁO CÁO TRIỂN KHAI, KIẾN TRÚC & TỰ ĐÁNH GIÁ

> **Dự án:** RidePulse DQ · **Nhánh:** `chien` @ `31e065a` · **Ngày:** 2026-08-22
> **Đối tượng đọc:** người review độc lập — bạn phải tự đánh giá được EvalGate mà không cần hỏi tác giả
> **Nguyên tắc của tài liệu:** nói cả những gì EvalGate **chưa** làm được, và những defect của **chính nó**

---

## MỤC LỤC

|  # | Mục                                                                                                                         |
| -: | ---------------------------------------------------------------------------------------------------------------------------- |
|  1 | [Tóm tắt điều hành](#1-tóm-tắt-điều-hành)                                                                           |
|  2 | [Kiến trúc](#2-kiến-trúc)                                                                                                 |
|  3 | [**Xây dựng thế nào — từng file, từng hàm**](#3-xây-dựng-thế-nào--từng-file-từng-hàm)                    |
|  4 | [Golden Dataset](#4-golden-dataset)                                                                                           |
|  5 | [**Từng gate đo gì, kết quả ra sao**](#5-từng-gate-đo-gì-và-kết-quả-ra-sao)                                  |
|  6 | [Tự đánh giá — defect đã sửa và còn lại](#6-tự-đánh-giá--defect-đã-sửa-và-còn-lại)                       |
|  7 | [Hardcode inventory](#7-hardcode-inventory)                                                                                   |
|  8 | [Độ phủ so với sản phẩm thực tế](#8-độ-phủ-so-với-sản-phẩm-thực-tế)                                           |
|  9 | [Thư mục `eval/` đã được xử lý](#9-thư-mục-eval-đã-được-xử-lý)                                            |
| 10 | [Plan còn lại](#10-plan-còn-lại)                                                                                          |
| 11 | [Cách chạy và cách review](#11-cách-chạy-và-cách-review)                                                              |
| 12 | [**Vá điểm mù — phát hiện từ lần chạy thật**](#12-vá-điểm-mù--phát-hiện-từ-lần-chạy-thật-22082026) |

---

## 1. TÓM TẮT ĐIỀU HÀNH

### 1.1 EvalGate là gì

Không phải một bộ thư viện đánh giá LLM. Là **cổng chất lượng quyết định release**:

```text
Measurement → Evidence → Normalization → Scoring → Policy → Release Decision
```

Điểm khác biệt cốt lõi so với một bộ metric AI nhập từ ngoài: hệ thống này **tự viết hợp đồng cho chính nó** trong `docs/PRODUCT_SPEC.md`, `docs/API_CONTRACT.md`, `docs/DATA_MODEL.md`, `docs/SUPABASE_DATASET_CONTRACT.md`. EvalGate đo hệ thống **theo hợp đồng đó**. Vi phạm không phải chuyện quan điểm — đó là hệ thống mâu thuẫn với tài liệu của chính mình.

### 1.2 Trạng thái

| Chỉ số                       |          Bản gốc | Sau Phase A | Sau golden |                                  Sau đợt sửa |
| ------------------------------ | -----------------: | ----------: | ---------: | ----------------------------------------------: |
| Evaluator chạy thật          |             7 / 18 |     13 / 25 |    15 / 26 |                               **19 / 30** |
| Hard gate                      | 12 (5 đo được) |     18 (12) |    19 (14) |               **19 (19 — hết gate mồ côi)** |
| Hard gate FAIL                 |                  4 |          10 |         11 |                                    **16** |
| Metric                         |                ~20 |          55 |         62 |                                    **75** |
| Self-test                      |                 26 |          47 |         63 |                                   **118** |
| Golden case                    |                  0 |           0 |          9 |                                     **9** |
| Golden label đóng băng      |                  0 |           0 |      5.814 | **5.764** (đã kiểm chứng ngữ nghĩa) |
| Defect của EvalGate đã sửa |                 — |           5 |          7 |                    **11** (+3 nêu ở §12) |
| LOC                            |              3.753 |       6.452 |      7.582 |                                 **9.886** |
| Dependency mới                |                 — |           0 |          0 |                                     **0** |
| Chi phí LLM / run             |                 $0 |          $0 |         $0 |                                    **$0** |

### 1.3 Bốn điều người review cần biết ngay

1. **Điểm không so sánh được giữa các phiên bản EvalGate.** 9.97 → 26.51 → 25.91 → 23.88 phản ánh việc thêm evaluator, không phải sản phẩm tốt lên. Đây chính là lý do `regression_engine` pin baseline theo `run_id`.
2. **Hai defect nghiêm trọng của chính EvalGate đã được tìm ra và sửa.** Chi tiết §6. Đáng chú ý nhất: gate regression từng **quên sạch** ngay khi lỗi được commit.
3. **Golden dataset đã có thật** — 3 tầng, 9 case, 5.764 nhãn đóng băng có checksum, $0 chi phí. §4.
4. **EvalGate vẫn chỉ phủ một phần sản phẩm.** Bốn module rủi ro nhất chưa có evaluator nào kiểm tra *hành vi*. §8.

---

## 2. KIẾN TRÚC

### 2.1 Luồng thực thi

```text
              python -m evalgate.run --mode {local|ci|pre_release}
                   [--allow-dirty] [--baseline RUN_ID] [--dry-run]
                                      │
        ╔═════════════════════════════▼═════════════════════════════╗
        ║ STAGE 0 — BASELINE RESOLUTION      run.resolve_baseline_ref║
        ║ đọc evalgate/runs/index.json → lấy git_ref của baseline    ║
        ║ → nếu không có: fallback "HEAD" và GHI RÕ trong report     ║
        ╚═════════════════════════════┬═════════════════════════════╝
                                      ▼
        ╔═══════════════════════════════════════════════════════════╗
        ║ STAGE 1 — PREFLIGHT      core/workspace_integrity.py      ║
        ║ index sạch? working tree sạch? có merge conflict?          ║
        ║ → bẩn ⇒ decision bị thay bằng EVALGATE_STALE (exit 4)      ║
        ║   nhưng evaluator VẪN chạy — dev vẫn thấy số               ║
        ╚═════════════════════════════┬═════════════════════════════╝
                                      ▼
                  config/profiles.yaml chọn evaluator theo --mode
                                      │
   ┌────────┬─────────┬──────────┬────┴─────┬──────────┬──────────┬────────┐
   ▼        ▼         ▼          ▼          ▼          ▼          ▼        ▼
 GATE 1  GATE 2   GATE 3     GATE 4    GATE 5    GATE 6   READINESS
AI QUAL  SECURITY  OBSERV   INPUT DATA RELIABIL  GOVERN
   │        │         │          │          │          │
 replay   authz    (trống)   ingest_    config    policy_res
 governed egress   NOT_IMPL  fidelity             contract
 golden   secret                                  hitl
                                                  capability
   │        │         │          │          │          │
   └────────┴─────────┴──────────┴──────────┴──────────┘
                                      │
                  mọi evaluator trả đúng một EvalResult
                  schemas/eval_result.py · pydantic extra="forbid"
                                      ▼
        ╔═══════════════════════════════════════════════════════════╗
        ║ STAGE 2 — REGRESSION     core/regression_engine.py        ║
        ║ · gate score tụt > 10 điểm       → blocking finding        ║
        ║ · hard gate PASS → FAIL          → blocking finding        ║
        ║   (đọc TRỰC TIẾP evaluate_hard_gates, không suy từ Finding)║
        ║ không có baseline ⇒ NOT_MEASURED (KHÔNG phải PASS)         ║
        ╚═════════════════════════════┬═════════════════════════════╝
                                      ▼
        ╔═══════════════════════════════════════════════════════════╗
        ║ STAGE 3 — AGGREGATE              aggregator.py            ║
        ║ ① 17 hard gate chạy TRƯỚC score — score không override     ║
        ║ ② collapse đa dataset: MIN cho hard-gate, P25 cho score    ║
        ║ ③ NOT_* bị loại, weight còn lại re-normalize               ║
        ║ ④ coverage floor: measured < 60% ⇒ INSUFFICIENT_COVERAGE   ║
        ║ ⑤ phát hiện va chạm tên metric                             ║
        ╚═════════════════════════════┬═════════════════════════════╝
                                      ▼
 PASS(0) │ WARNING(1) │ FAIL(2) │ RELEASE_BLOCKED(3) │ EVALGATE_STALE(4) │ INSUFFICIENT_COVERAGE(5)
                                      ▼
              reports/renderer.py → report.md + result.json
              regression_engine.save_run() → evalgate/runs/<run_id>/
              (run STALE vẫn được lưu, nhưng usable_as_baseline: false)
```

### 2.2 Năm bất biến thiết kế

Không được phép nới lỏng. Mỗi cái có test bảo vệ.

| Bất biến                               | Vì sao                                                                              | Test                                                              |
| ---------------------------------------- | ------------------------------------------------------------------------------------ | ----------------------------------------------------------------- |
| Hard gate chạy**trước** score   | Lỗ hổng CRITICAL không được để điểm cao che                                | `test_hard_gate_failure_blocks_release_despite_a_perfect_score` |
| `NOT_*` ≠ 0 điểm                    | "Chưa đo" ≠ "đo được và tệ". Cho 0 điểm sẽ khuyến khích xoá evaluator | `test_unmeasured_gate_is_excluded_not_scored_zero`              |
| Collapse**MIN / P25**, không mean | Sáu dataset tốt không được che một dataset hỏng                              | `test_collapse_uses_min_for_hard_gates_and_p25_for_scores`      |
| KNOWN_GAP ≠ REGRESSION                  | Nếu gộp, mọi release đều bị chặn và gate sẽ bị bỏ qua trong một tuần    | `test_pre_existing_gap_is_not_reported_as_a_regression`         |
| Assertion không đo được ≠ FAIL     | Cùng lý do, ở tầng golden case                                                   | `test_nothing_to_inspect_is_not_a_failure`                      |

---

## 3. XÂY DỰNG THẾ NÀO — TỪNG FILE, TỪNG HÀM

> Mục này để người review hiểu **tại sao mỗi mảnh tồn tại**, không chỉ nó làm gì.
> Ký hiệu: ★ = thêm mới trong đợt này · ✎ = sửa · (cũ) = có sẵn từ trước.

### 3.1 Contract — `schemas/eval_result.py` ✎

Mọi evaluator, bất kể gate nào, trả về đúng một `EvalResult`. Contract đóng (`extra="forbid"`) nên adapter lệch shape sẽ **fail to** thay vì âm thầm đóng góp một con số sai vào điểm cuối.

| Thành phần                  | Mục đích                                                                                                                                        |
| ----------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `EvalStatus`                | 10 trạng thái. Tách`NOT_*` (chưa đo) khỏi `FAIL` (đo rồi và tệ) — đây là phân biệt quan trọng nhất của toàn bộ hệ thống |
| `EXCLUDED_FROM_AGGREGATE`   | Tập status bị loại khỏi điểm tổng, kích hoạt re-normalize weight                                                                          |
| `MetricValue`               | Tách`raw` khỏi `normalized`. Metric khác thang **không bao giờ** được cộng trước khi chuẩn hoá                              |
| `Threshold`                 | pass / warn / hard_gate_floor                                                                                                                      |
| `Evidence`                  | file / trace / query / command — đường dẫn tới bằng chứng thô                                                                             |
| `Finding`                   | Có`blocks_release: bool` và `evidence_ref`. Finding không kèm evidence là ý kiến                                                        |
| `DatasetBreakdown`          | Bắt buộc khi evaluator chạy trên nhiều dataset — để collapse MIN/P25 hoạt động                                                          |
| `CostRecord`                | usd / token / wall-clock. Hiện toàn bộ = 0                                                                                                      |
| ★`baseline_run_id`         | **Mới.** Một tuyên bố về regression mà không nêu baseline là không thể phản chứng                                               |
| `counts_toward_aggregate()` | Một chỗ duy nhất quyết định kết quả có vào điểm không                                                                                 |

### 3.2 Chuẩn hoá — `normalizers/normalizers.py` (cũ)

13 hàm đưa mọi metric về thang 0..100. Đặt tập trung một chỗ là điều khiến việc thêm metric sau này an toàn: aggregator không bao giờ nhìn thấy đơn vị thô.

| Hàm                                          | Dùng khi                                                          |
| --------------------------------------------- | ------------------------------------------------------------------ |
| `ratio` / `inverse_ratio`                 | cao-hơn-tốt-hơn / thấp-hơn-tốt-hơn                          |
| `variance(factor=200)`                      | generalization variance — stdev 0.15 mất 30 điểm               |
| `latency_band`, `psi_band`, `time_band` | phân dải, không nội suy tuyến tính                           |
| `budget`                                    | chi phí so với ngân sách                                       |
| `severity`                                  | CRITICAL=0 … NONE=100                                             |
| `boolean`                                   | có/không                                                         |
| **`zero_tolerance`**                  | **không nội suy** — 1 vi phạm CRITICAL tệ ngang 50 cái |
| `percentile`, `stdev`                     | collapse đa dataset, không cần numpy                            |

Bất biến chung: mọi hàm trả `None` khi input `None` — không bao giờ biến "không có dữ liệu" thành 0 điểm.

### 3.3 Chấm điểm — `aggregator.py` ✎

| Hàm                                             | Mục đích                                                                                                                                                                                                |
| ------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `collapse_per_dataset`                         | **MIN** cho hard-gate metric, **P25** cho score metric. Mean sẽ để sáu dataset khoẻ che dataset thứ bảy — đúng chế độ hỏng mà sản phẩm "upload dataset bất kỳ" phải tránh |
| `collapse_result_scores`                       | Gộp một evaluator đa dataset thành một điểm gate                                                                                                                                                    |
| `_evaluate_rule`                               | DSL so sánh đóng (`value >= 1`). Allow-list ký tự + `eval` với `__builtins__={}`                                                                                                               |
| `evaluate_hard_gates`                          | Gom metric, đối chiếu 19 rule. Metric không tồn tại ⇒`NOT_EVALUATED`, **không** phải PASS                                                                                                 |
| ★`detect_metric_collisions`                   | **Mới.** Hard gate đọc metric từ namespace phẳng; hai evaluator cùng tên metric sẽ ghi đè im lặng. Hiện 0 va chạm — hàm này để ngày có va chạm thì nó hiện trong report      |
| `re_normalize_weights`                         | Loại gate không đo được, scale phần còn lại về 1.0                                                                                                                                               |
| `aggregate`                                    | ① hard gate → ② coverage floor → ③ score band. Số tính từ quá ít bằng chứng thì**không nên công bố**                                                                                |
| ★`MIN_MEASURED_WEIGHT = 0.60`                 | **Mới.** Re-normalize là đúng, nhưng nó cũng âm thầm dồn toàn bộ verdict lên phần còn lại                                                                                            |
| ★`EVALGATE_STALE` / `INSUFFICIENT_COVERAGE` | **Mới.** Hai trạng thái "không đủ cơ sở phán quyết", exit 4 và 5                                                                                                                          |

### 3.4 Git chỉ-đọc — `core/git_read.py` ★

Một cổng release **có thể sửa repo mà nó đang phán xét** thì không phải cổng. Ràng buộc này được cưỡng chế bằng code, không giao cho kỷ luật reviewer.

| Hàm                                                                                 | Mục đích                                                                                                                                                                                                                               |
| ------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `_READ_ONLY_SUBCOMMANDS`                                                           | Allow-list 8 subcommand:`show, rev-parse, ls-files, ls-tree, diff, status, log, cat-file`. Gọi cái khác → `ValueError`                                                                                                            |
| `run`                                                                              | Cổng duy nhất ra git. Memo hoá; exit non-zero →`None` (nghĩa "ref/path không tồn tại", không phải sự cố)                                                                                                                    |
| `clear_cache`                                                                      | Xoá memo — gọi ở đầu mỗi lần collect                                                                                                                                                                                              |
| `head_ref` / `head_sha`                                                          | `branch@sha`                                                                                                                                                                                                                            |
| ★`ref_sha`                                                                        | Tách sha khỏi nhãn`branch@sha`                                                                                                                                                                                                       |
| ★`ref_exists`                                                                     | **Quan trọng.** `list_files` trên ref không tồn tại trả list rỗng — không phân biệt được với "baseline không có file nào", và so sánh với rỗng sẽ báo **không có regression nào**. Phải fail to |
| `read_file(ref, path)`                                                             | `git show <ref>:<path>`; `INDEX_REF=""` đọc bản staged. **Không bao giờ checkout**                                                                                                                                         |
| `changed_paths` / `untracked_paths` / `unmerged_paths` / `staged_line_delta` | Trạng thái cây làm việc                                                                                                                                                                                                              |

### 3.5 Preflight — `core/workspace_integrity.py` ★

Verdict là phát biểu về **một revision cụ thể**. Nếu code sản phẩm đang staged mà chưa commit, evaluator đọc một thứ còn report ghi tên một thứ khác. Đó không phải "sai nhẹ hơn" — đó là sai theo cách không ai phát hiện được từ report.

| Thành phần         | Mục đích                                                                                                                                                                                         |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `PRODUCT_PATHSPEC` | `src`, `requirements.txt`, `dbt_project`, `scripts`. **File của EvalGate cố ý bị loại** — thêm evaluator không được làm harness từ chối chạy                           |
| `collect_state`    | staged / unstaged / untracked / unmerged / delta dòng                                                                                                                                              |
| `_reasons`         | Diễn giải sang câu người đọc được, đưa thẳng vào report                                                                                                                               |
| `evaluate`         | Trả`score=None` — preflight là cổng **của lần chạy**, không phải một chiều được chấm điểm, nên không bao giờ làm dịch chuyển điểm tổng theo bất kỳ hướng nào |

Finding mang id `PREFLIGHT-STALE` chứ **không** phải `HG-*`: staleness là điều kiện *hợp lệ của lần chạy*, không phải *chất lượng sản phẩm*. Gộp hai thứ sẽ khiến `--allow-dirty` không thể tôn trọng được.

### 3.6 Regression — `core/regression_engine.py` ★

Một gate chỉ phán xét revision hiện tại một cách cô lập thì không thấy được **chiều đi**. Nó cho cùng một điểm cho hệ thống luôn ở mức 40 và hệ thống tuần trước còn 90 — trong khi cái thứ hai là sự cố còn cái thứ nhất là backlog.

| Hàm                          | Mục đích                                                                                                                                                                            |
| ----------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `load_index` / `save_run` | Lịch sử run tại`evalgate/runs/<run_id>/`. Giữ 30 run gần nhất                                                                                                                  |
| ★`usable_as_baseline`      | Run STALE**vẫn được lưu** (trend line có giá trị, giấu đi là nói dối bằng cách bỏ sót) nhưng không bao giờ được chọn làm baseline                       |
| `resolve_baseline`          | Run mới nhất dùng được. Baseline chỉ định tường minh thì được tôn trọng kể cả khi stale — người vận hành đã yêu cầu so sánh đó                          |
| `current_gate_scores`       | Điểm gate của lần chạy hiện tại, dùng chính hàm collapse của aggregator                                                                                                     |
| ✎`current_evaluator_scores` | So sánh **theo từng evaluator**, chỉ trên phần giao của hai lần chạy. Trung bình gate đổi khi thành viên đổi — không phải regression nhưng số học giống hệt (DEFECT-11) |
| ✎`baseline_evaluator_scores` | Dựng lại `EvalResult` từ dump rồi cho qua **đúng cùng** `collapse_result_scores`. Đọc thẳng trường `score` sẽ so P25 với trung bình chưa collapse |
| `evaluate`                  | ① evaluator tụt >`SCORE_DROP_LIMIT=10` → blocking (id `REG-DROP`, **không** mượn `HG-R3`) · ② hard gate PASS→FAIL → blocking |
| ✎ so hard gate               | Gọi**trực tiếp** `evaluate_hard_gates(results)` thay vì suy từ `Finding.id`. Cách suy đã bỏ sót HG-D2 (fail bằng metric, evaluator phát Finding dưới id khác) |

Chỉ PASS→FAIL mới tính. Gate từ `NOT_EVALUATED` → `FAIL` là **độ phủ mới đến**, là tiến bộ chứ không phải thoái lui.

### 3.7 Capability — `gates/gate6_governance/capability_regression.py` ★

Không có gì trong pipeline hiện tại trả lời được *"thay đổi này có xoá mất năng lực nào không?"*. ruff không thấy lỗi cú pháp, pytest không có assertion về hành vi đó, code review thấy một file quen tên, và EvalGate chấm bất cứ cây nào đang nằm trên đĩa.

| Thành phần                   | Mục đích                                                                                                                  |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| `config/capabilities.yaml`   | 12 năng lực, mỗi cái một marker kiểm chứng được ở bất kỳ git ref nào                                           |
| `_matches`                   | Ba dạng detector:`{file, pattern}` · `{under, suffix, pattern}` · `{path_exists}`                                   |
| `_present`                   | `invert: true` = năng lực **là sự vắng mặt** của marker (ví dụ "sql_text không phải trường công khai") |
| `compare`                    | Phân loại 4 trạng thái theo bảng dưới                                                                                 |
| ★`BaselineUnavailableError` | Ref không resolve được ⇒`BLOCKED_MISSING_GROUND_TRUTH`, **không** phải "0 regression"                         |
| `evaluate`                   | Chỉ regression**CRITICAL** mới chặn release                                                                         |

```text
baseline có, giờ không  →  REGRESSION    (CRITICAL ⇒ chặn release)
baseline không, giờ không → KNOWN_GAP    (đã sai từ trước; báo, không chặn)
baseline không, giờ có   →  IMPROVEMENT  (ghi nhận)
baseline có, giờ có      →  INTACT
```

Gộp `KNOWN_GAP` vào `REGRESSION` sẽ chặn mọi release mãi mãi và biến gate thành vô nghĩa. Phân biệt này **chịu lực**, không phải trang trí.

### 3.8 Contract conformance — `gates/gate6_governance/contract_conformance.py` ★

Dự án này bất thường theo hướng có ích: nó **phát biểu bất biến của chính mình bằng văn xuôi**. Những câu đó giá trị hơn bất kỳ metric AI chung chung nào ở đây, vì chúng cụ thể, đã được cả đội đồng ý, và **mỗi câu đều kiểm được bằng máy**.

| Hàm                                   | Kiểm hợp đồng nào | Kết quả                                                                                                                                                                                                                                               |
| -------------------------------------- | ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `check_raw_rows_immutable`           | PRODUCT_SPEC §1       | ✅ PASS                                                                                                                                                                                                                                                 |
| `check_llm_receives_aggregate_only`  | PRODUCT_SPEC §2       | ✅ PASS                                                                                                                                                                                                                                                 |
| `check_only_approved_rule_runs`      | PRODUCT_SPEC §3       | ❌ 7 endpoint duyệt/thực thi nhận gọi ẩn danh                                                                                                                                                                                                      |
| `check_runner_credential_and_bounds` | PRODUCT_SPEC §4       | ❌ không module nào đọc`RUNNER_DATABASE_URL`                                                                                                                                                                                                      |
| `check_transitions_are_audited`      | PRODUCT_SPEC §5       | ❌ 3 hàm transition không ghi audit                                                                                                                                                                                                                   |
| `check_no_internal_fields_public`    | API_CONTRACT           | ❌`TestResultResponse.sql_text`                                                                                                                                                                                                                       |
| `check_actor_not_client_supplied`    | PRODUCT_SPEC §5       | ❌`reviewer=body.reviewer`                                                                                                                                                                                                                            |
| `check_job_state_vocabulary`         | API_CONTRACT           | ❌`AWAITING_SEMANTIC_REVIEW` + passthrough                                                                                                                                                                                                            |
| `check_single_run_state_owner`       | DATA_MODEL             | ❌ 5 bảng mang trạng thái thực thi                                                                                                                                                                                                                  |
| `_public_model_closure`              | (hạ tầng)            | Đi theo**field lồng nhau**. Endpoint khai `response_model=TestResultsListResponse`, trường vi phạm nằm ở tầng dưới trong `TestResultResponse` — check chỉ nhìn model khai báo sẽ báo sạch đúng ở ca nó sinh ra để bắt |

### 3.9 HITL — `gates/gate6_governance/hitl_integrity.py` ★

HITL là tuyên bố governance của sản phẩm. Endpoint tồn tại là chưa đủ — dấu vết chúng để lại phải trả lời được câu hỏi hỏi sau nhiều tháng: *ai đã duyệt luật này, khi nào?*

| Thành phần                            | Mục đích                                                                                                                                           |
| --------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `_run_probe`                          | **Thực thi** `create_run → save_proposed_rules → review_rule → publish_approved_rules` rồi hỏi DB kết quả                             |
| An toàn                                | SQLite trong`TemporaryDirectory`; `assert` engine nằm trong tmpdir **trước** mọi query; khôi phục engine + settings trong `finally` |
| `Q1` active rule không audit         | →`hitl_integrity`                                                                                                                                  |
| `Q2` reviewer có được lưu không | →`reviewer_persisted`                                                                                                                              |
| `Q3` traceability                     | → active rule không có audit event nào nêu tên                                                                                                  |

Check tĩnh chỉ xác nhận `AuditEventModel` được import. Chỉ **chạy** transition mới cho biết bản ghi có thực sự được viết hay không.

### 3.10 Governed enum — `gates/gate1_ai_quality/governed_enum_conformance.py` ★✎

Một rule `ACCEPTED_VALUES` mà enum lấy từ chính cột nó kiểm tra thì **không bao giờ fail được**: mọi giá trị xấu có mặt lúc profiling đều được nạp vào allow-list của chính nó.

| Hàm                        | Mục đích                                                                                                                                                                           |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ✎`load_governed_domains` | **Số nhiều.** Đọc *mọi* cột governed. Bản đầu hardcode `payment_type` nên trả về một *sàn dưới*, mà sàn dưới mời người đọc hiểu nhầm là tổng |
| Fallback đọc doc          | `rule_policies.json` đang mất; một evaluator governance im lặng đúng lúc một tài sản governance biến mất thì vô dụng. Nguồn thực dùng được ghi vào kết quả  |
| `score_proposals`         | So enum đề xuất với domain governed                                                                                                                                               |
| ★`count_unbacked_enums`  | `ACCEPTED_VALUES` trên cột **không có policy nào quản** — tautological theo cấu tạo. **76 rule**                                                               |
| `measure_planted_recall`  | Hợp đồng nói có 4 dòng invalid cố ý cài. Thực tế bắt 2                                                                                                                    |
| ★`_PATH_SCOPE_NOTE`      | **Quan trọng cho độ chính xác.** Nêu rõ finding nói về **đường Agent**; đường Dashboard lấy enum từ policy và **làm đúng**                     |

### 3.11 Ingest — `gates/gate4_input_data/ingest_fidelity.py` ★

`src/worker.py` ép kiểu mọi ô bằng `to_float/to_int/to_str`, và mỗi hàm trả `None` khi chuyển đổi lỗi. Giá trị nguồn ghi `"12,50"` do đó vào DB thành NULL — không counter, không log, không khác gì một ô vốn dĩ trống.

| Hàm                     | Mục đích                                                                                                      |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------- |
| `MALFORMED_MATRIX`     | 13 ca có nhãn`ACCEPT` (phải giữ nguyên) hoặc `REJECT` (phải từ chối **và nói ra**)          |
| `run_malformed_matrix` | Gọi thẳng hàm thật của`src/worker.py`. `None`, `NaN`, `inf` đều tính là **mất im lặng** |
| `run_round_trip`       | Giá trị sạch serialize như CSV rồi ép ngược — đo`row_fidelity` / `cell_fidelity` (2.200 ô)        |
| `null_ambiguity_rate`  | **Chỉ số then chốt**: tỷ lệ NULL sinh ra do lỗi, không phân biệt được với NULL thật          |

Đo được **ngay hôm nay** dù chưa có upload endpoint, vì đây là ba hàm thuần.

### 3.12 Golden — `golden/` ★ và `gates/gate1_ai_quality/golden_conformance.py` ★

Xem §4.

### 3.13 Vacuity — `gates/gate1_ai_quality/vacuity_probe.py` ★

Đây là evaluator **duy nhất trong Gate 1 không cần một nhãn nào**, nên là cái duy nhất còn hoạt động khi người dùng upload một dataset chưa ai gắn nhãn. Golden set hiệu chuẩn cái cân trước khi nó chạm dữ liệu người dùng; cái này cân chính con cá.

| Thành phần                         | Mục đích                                                                                                                                                                                                            |
| ------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `judge_rule`                       | Quyết định một rule có**bao giờ** báo được vi phạm trên dữ liệu nó canh gác không                                                                                                               |
| `NOT_JUDGED`                       | 5 rule type**cố ý không phán xét**, mỗi cái ghi rõ lý do. `NOT_NULL` trên cột sạch là **guard đang thoả mãn**, không phải rule chết — gán oan nó sẽ khiến cả đội bỏ qua gate |
| `SYSTEMIC_VACUITY_THRESHOLD = 0.5` | Một rule type mà quá nửa không thể fail thì không phải xui —**cơ chế sinh ra loại rule đó hỏng**                                                                                                 |
| `DEGENERATE_FLOOR_RATIO = 0.05`    | Phân biệt "không thể fail" với "sống nhưng vô dụng" (`min_row_count=1` trên bảng 50.000 dòng)                                                                                                            |
| Chế độ`mutation`                | **NOT_IMPLEMENTED, khai báo chứ không giả** — sẽ làm hỏng bản sao, biên dịch lại rule bằng chính compiler của sản phẩm, xem nó có kích hoạt không                                          |

**Bảng phán xét — vì sao mỗi loại được/không được phán xét:**

| Rule type              | Vacuous khi                                |                             Phán xét?                             |
| ---------------------- | ------------------------------------------ | :-----------------------------------------------------------------: |
| ACCEPTED_VALUES        | enum ⊇ giá trị quan sát được        |                                 ✅                                 |
| RANGE                  | [min,max] ⊇ [obs_min, obs_max]            |                                 ✅                                 |
| NULL_RATE              | `max_null_pct` ≥ tỷ lệ null thực tế |                                 ✅                                 |
| ROW_COUNT              | ngưỡng ≤ 0 hoặc không có             |                                 ✅                                 |
| NOT_NULL               | —                                         |               ❌ guard đang thoả mãn là hợp lệ               |
| UNIQUE                 | —                                         | ❌ cần biết cột có phải surrogate key; đã có golden case lo |
| REGEX_FORMAT           | —                                         |           ❌ cần chạy pattern; làm được, chưa làm           |
| FRESHNESS              | —                                         | ❌ phụ thuộc thời gian, không phải thuộc tính của tham số |
| CROSS_FIELD_COMPARISON | —                                         |        ❌ quan hệ, vi phạm phụ thuộc hai cột cùng lúc        |

**Kết quả trên 383 rule archive (không dùng nhãn nào):**

| Rule type                 |  Không thể fail |                  Tỷ lệ |
| ------------------------- | ----------------: | -----------------------: |
| **ACCEPTED_VALUES** | **66 / 74** | **89.2%** → HG-A6 |
| ROW_COUNT                 |           14 / 31 |   45.2% (+17 degenerate) |
| RANGE                     |            0 / 62 |                       0% |
| NULL_RATE                 |            0 / 20 |                       0% |

**0% ở RANGE và NULL_RATE là bằng chứng check này phân biệt được, không phải cờ bừa.** Prompt bảo model nới biên RANGE 10–20%, nếu check kém thì RANGE phải dính — nó không dính, vì `min=0` mà dữ liệu có giá trị âm nên rule vẫn kích hoạt được.

Đây là **bằng chứng thứ ba, độc lập** cho cùng một lỗi tautological:

| Phương pháp      | Nguồn sự thật              | Dùng được cho dataset lạ? |
| ------------------- | ----------------------------- | :----------------------------: |
| SDIH replay         | nhãn synthetic               |  ⚠️ cần sinh nhãn trước  |
| HG-A3 governed enum | policy trong`docs/`         |       ❌ cần có policy       |
| **vacuity**   | **không cần gì cả** |               ✅               |

### 3.14 Kết cục lần chạy — `gates/gate1_ai_quality/run_outcome_integrity.py` ★

Thêm 22/08 sau khi một lần chạy thất bại toàn phần đi qua cổng mà điểm không đổi.

| Hàm                        | Vì sao tồn tại                                                                                          |
| --------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `collect_runs`            | Gom artefact `output/**` theo `run_id` lấy từ **tên file** — correlator duy nhất phủ mọi stage |
| `_attribute`              | Quy thuộc workflow theo **signature stage**, không theo terminal stage: run chết sớm mới là ca cần bắt |
| `_read_terminal`          | Đọc artefact stage cuối; đếm output và bóc số lỗi từ chuỗi `"N validation errors"` của Pydantic |
| `schema_violation_rate`   | Từ chối / (từ chối + chấp nhận). Trả `None` khi không có mẫu số — **không** trả 0% sạch      |
| `evaluate`                | Điểm lấy theo **lần chạy mới nhất**, không lấy trung bình                                       |

Hằng số `RECENT_RUN_WINDOW = 5` cố ý nhỏ: cửa sổ dài cho phép một bức tường lịch sử khoẻ mạnh che một hệ thống đang hỏng hôm nay.

### 3.15 Đường phục vụ — `gates/gate6_governance/served_path_fidelity.py` ★

| Hàm             | Vì sao tồn tại                                                                                     |
| ---------------- | ------------------------------------------------------------------------------------------------------ |
| `_find_setting` | Tìm `AGENT_MODE` trong 6 file deploy. `- AGENT_MODE` trần (không có `=`) ghi nhận là **pass-through**, không phải khai báo |
| `inspect`       | Giải ra mode hiệu lực: khai báo live thắng, nếu không thì default trong code áp dụng          |
| `evaluate`      | `HG-G5` chặn release; thiếu credential báo dưới `CRED-UNSEEN` và **không bao giờ chặn**       |

Lý do `CRED-UNSEEN` không chặn: evaluator đọc được config trong repo nhưng **không nhìn thấy secret manager hay biến CI tiêm vào**, nên vắng mặt ở đây không phải bằng chứng vắng mặt lúc deploy.

### 3.16 Credential mặc định — `gates/gate2_security/default_credential_probe.py` ★

Sinh metric cho `HG-S7` — gate có trong policy từ v3 nhưng chưa từng có ai đo.

| Hàm                        | Vì sao tồn tại                                                                          |
| --------------------------- | ------------------------------------------------------------------------------------------ |
| `find_seeded_credentials` | Tìm hằng số seed có mật khẩu đoán được. Bỏ qua giá trị **toàn hoa** (role) và **tên hiển thị** |
| `_enclosing_guard`        | Lời gọi seed có nằm trong điều kiện kiểm môi trường không                          |
| `evaluate`                | Chỉ báo `active` khi **cả hai** nửa đúng: credential yếu **và** call site không guard |

Chỉ phân tích tĩnh: không thử đăng nhập, không truyền mật khẩu, và credential mô tả **theo hình dạng** (*"password equals username"*) chứ không theo giá trị — file evidence nằm trong repo nên không được mang credential dùng được.

### 3.17 Orchestrator — `run.py` ✎

| Hàm                        | Mục đích                                                                                                                                     |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| ★`resolve_baseline_ref`  | Lấy`git_ref` của baseline đã lưu. Fallback `HEAD` **chỉ đúng cho lần chạy đầu tiên** và được ghi rõ trong kết quả |
| `_registry(baseline_ref)` | Tên → callable. Import hoãn vào trong lambda: một evaluator hỏng không được ngăn cả harness khởi động                            |
| `load_profile`            | Đọc`profiles.yaml`, hỗ trợ `inherits`                                                                                                   |
| `_declared_but_not_run`   | 11 evaluator**khai báo tường minh** để chúng hiện trong report với trạng thái rõ ràng thay vì biến mất im lặng            |
| `collect_results`         | Evaluator crash →`NOT_EXECUTED` kèm lý do, không làm sập cả run                                                                        |
| `main`                    | preflight → evaluator → regression → aggregate → report → history                                                                          |

### 3.18 Báo cáo — `reports/renderer.py` ✎

`render_json` (máy đọc) và `render_markdown` (người đọc). Bổ sung `measured_weight`, `baseline`, và dòng **"This verdict is qualified"** khi decision bị override — để lời cảnh báo đi cùng con số thay vì nằm ở chỗ khác.

---

## 4. GOLDEN DATASET

### 4.1 Vì sao cần, và vì sao SDIH chưa đủ

SDIH sinh nhãn cell-level động cho schema bất kỳ, và trả lời được: *"agent có bắt được lỗi không?"*

Nhưng các thất bại thật của dự án **không phải** thất bại về phát hiện. Chúng là thất bại về **phán đoán**:

- đề xuất rule mà ngưỡng lấy từ chính dữ liệu nó phải phán xét
- đặt UNIQUE lên surrogate key vốn unique theo cấu tạo
- viết `business_rationale` đầy tên cột trong khi system prompt cấm

Không cái nào hiện ra dưới dạng "defect bị bỏ sót". Tất cả đều hiện ra ở golden case.

### 4.2 Ba tầng

| Tầng                        | Trả lời câu hỏi                                                  | Chi phí | Làm baseline được? |
| ---------------------------- | -------------------------------------------------------------------- | -------- | :--------------------: |
| **1** `tier1_sdih/`  | Ô nào lỗi, thuộc lớp nào?                                      | $0       |   ✅ có fingerprint   |
| **2** `tier2_rules/` | Có đề xuất**đúng rule**, **đúng nguồn** không? | $0       |    ✅ deterministic    |
| **3** `tier3_llm/`   | Văn bản sinh ra có tuân prompt của chính nó không?           | $0       |    ✅ deterministic    |

**Tầng 3 đáng chú ý:** đây thường là chỗ LLM judge xuất hiện, và ở đây thì không. Hai lệnh trong prompt đủ cụ thể để kiểm bằng thao tác chuỗi: *"CẤM dùng tên biến kỹ thuật trong `business_rationale`"* và *"BẮT BUỘC trích số liệu trong `ai_reasoning`"*. Kiểm bằng model sẽ chậm hơn, tốn tiền, và tệ hơn cả — tạo ra một baseline **tự nó trôi**. Baseline trôi thì không phát hiện được trôi ở bất cứ đâu khác.

### 4.3 Đã đóng băng

```text
evalgate/golden/
├── README.md              quy tắc: ai sở hữu nhãn, sửa thế nào, version ra sao
├── manifest.yaml          seed + fingerprint + sha256 cho từng snapshot
├── freeze.py              ghi tier 1 và verify
├── schema.py              format case + 8 loại assertion
├── tier1_sdih/            7 file, 5.764 nhãn
├── tier2_rules/           e1_e5.cases.yaml · agent_scope.cases.yaml
└── tier3_llm/             reasoning.cases.yaml
```

| Archetype             | Nhãn | Fingerprint        |
| --------------------- | ----: | ------------------ |
| corpus-nyc-taxi-50k   | 3.498 | `466538ac015f…` |
| corpus-synth-clinical |   500 | `b4c2e9368a78…` |
| corpus-synth-hr       |   500 | `032f41fc5138…` |
| corpus-synth-retail   |   450 | `888e29e3ecf5…` |
| corpus-synth-iot      |   400 | `8c31b9b3481c…` |
| corpus-synth-wide     |   400 | `38aa5cbd7cd0…` |
| corpus-synth-tiny     |    16 | `e8ff8ab9c62f…` |

`freeze.py --verify` sinh lại mọi nhãn từ seed và so fingerprint → snapshot bị sửa tay hoặc để cũ sẽ **bị bắt** chứ không được tin. Snapshot để review và diff; generator vẫn là nguồn sự thật.

### 4.4 Tám loại assertion

| Loại                   | Kiểm gì                                                      |
| ----------------------- | -------------------------------------------------------------- |
| `rule_proposed`       | Có rule loại X trên cột Y                                  |
| `rule_not_on_columns` | **Không** được có rule loại X trên các cột này |
| `enum_from_policy`    | `ACCEPTED_VALUES` tôn trọng domain governed                |
| `parameter_bound`     | Tham số số học thoả ràng buộc                            |
| `no_rules_on_tables`  | Không đề xuất rule nào trên các bảng này              |
| `min_violations`      | Thực thi phải bắt được ít nhất N dòng                 |
| `forbidden_tokens`    | Trường văn bản không được chứa các chuỗi này       |
| `must_cite_numbers`   | Trường phải chứa ít nhất một chữ số                   |

### 4.5 Kết quả 9 case

| Case                                  | Kết quả       | Quan sát                                                                                                          |
| ------------------------------------- | --------------- | ------------------------------------------------------------------------------------------------------------------ |
| GC-E1-RANGE-NONNEGATIVE               | ✅ PASS         | 13 RANGE trên`trip_distance`/`fare_amount`, min ≥ 0                                                          |
| GC-E2-NOTNULL-IDENTIFIER              | ✅ PASS         | 7 NOT_NULL trên`vendor_id`                                                                                      |
| GC-E3-ENUM-FROM-POLICY                | ❌ FAIL         | 7/8 đề xuất nạp giá trị bị loại trừ; bắt 2/4 dòng invalid                                               |
| GC-E4-CROSSFIELD-ORDERING             | ✅ PASS         | 7 CROSS_FIELD_COMPARISON trên`pickup_at`                                                                        |
| GC-E5-UNIQUE-ON-BUSINESS-KEY          | ❌ FAIL         | 4 UNIQUE trên`source_row_id` (surrogate key)                                                                    |
| GC-SCOPE-NO-METADATA-TABLES           | ❌ FAIL         | **76 rule trên bảng vận hành**: `jobs` 28, `proposal_runs` 21, `proposed_rules` 14, `datasets` 5 |
| GC-SCOPE-NO-RULES-ON-INTERNAL-COLUMNS | ❌ FAIL         | UNIQUE trên`proposal_runs.run_id`, `.status`                                                                  |
| GL-RATIONALE-NO-TECHNICAL-NAMES       | ⚪ NOT_MEASURED | Artefact cũ không có trường`business_rationale`                                                             |
| GL-REASONING-MUST-CITE-FIGURES        | ❌ FAIL         | 30/383 lập luận**không chứa một con số nào**                                                          |

`golden_case_pass_rate = 0.375` (3 đạt / 8 kiểm được). Case thứ 9 báo `NOT_MEASURED` kèm lý do, **không** tính là fail.

> **GC-E5 được viết để FAIL có chủ đích.** Golden set mô tả *điều gì nên đúng*, không mô tả *điều gì đang xảy ra*.

---

## 5. TỪNG GATE ĐO GÌ, VÀ KẾT QUẢ RA SAO

> Mục này viết cho người chưa đọc code. Mỗi gate mở đầu bằng **câu hỏi nó trả lời**,
> rồi tới từng metric: *đo cái gì* và *con số hiện tại nghĩa là gì*.
>
> Toàn cảnh: **19 evaluator chạy thật / 30 khai báo · 19 hard gate · 16 đang FAIL · 0 gate mồ côi**
> Điểm tổng **23.88/100** · Quyết định **`RELEASE_BLOCKED`** · Đo được **85%** trọng số
>
> Các con số trong mục này chụp ở lần chạy **trước** đợt vá điểm mù 22/08 (§12), khi điểm
> còn là 28.03. Bốn hard gate mới — `HG-A2`, `HG-A7`, `HG-G5`, `HG-S7` — được thêm sau đó
> và đều FAIL; chi tiết ở [§12](#12-vá-điểm-mù--phát-hiện-từ-lần-chạy-thật-22082026).

### Bản đồ nhanh

| Gate               | Câu hỏi gate này trả lời                                         |          Điểm | Trạng thái                  |
| ------------------ | --------------------------------------------------------------------- | --------------: | ----------------------------- |
| 1 · AI Quality    | Agent có đề xuất luật**đúng và có tác dụng** không? | **20.19** | 5 evaluator, 6 hard gate FAIL |
| 2 · AI Security   | Hệ thống có chống được lạm dụng không?                      | **25.00** | 3/4 probe = 0 điểm          |
| 3 · Observability | Khi sai, có truy được sai ở đâu không?                        |         *n/a* | **Chưa xây**          |
| 4 · Input Data    | Dữ liệu vào có nguyên vẹn không?                               | **25.00** | 1 hard gate FAIL              |
| 5 · Reliability   | Có chịu được sự cố phụ thuộc không?                         | **28.57** | 2/7 biện pháp               |
| 6 · Governance    | Quyết định của AI có truy vết và audit được không?         | **25.93** | 5 hard gate FAIL              |
| 7 · Business      | AI có giảm việc cho Steward không?                                |         *n/a* | **Chưa đo được**   |

---

### GATE 1 — AI QUALITY · 20.19 điểm

> **Câu hỏi:** agent đề xuất luật có *bắt được lỗi thật* không, và luật đó có *dùng được* không?

Năm evaluator soi cùng một agent từ năm hướng khác nhau.

#### 1a · `replay_detection` — agent có **bắt được** lỗi không?

Chấm 8 lần chạy đã lưu, đối chiếu với nhãn SDIH sinh ra.

| Metric                             | Đo cái gì                                                       |       Kết quả | Nghĩa là                                                         |
| ---------------------------------- | ------------------------------------------------------------------ | --------------: | ------------------------------------------------------------------ |
| `detection_precision`            | Trong các dòng agent gắn cờ, bao nhiêu % thật sự lỗi       | **0.088** | Cứ 100 cảnh báo thì ~91 là báo động giả                   |
| `detection_recall_macro`         | Trung bình theo*lớp lỗi*: mỗi lớp bắt được bao nhiêu % | **0.333** | Bỏ sót 2/3 số lỗi                                              |
| `detection_f1_macro`             | Cân bằng hai chỉ số trên                                      | **0.160** | Ngưỡng đạt là 0.60                                            |
| **`min_recall_per_class`** | **Lớp lỗi tệ nhất** bắt được bao nhiêu %            |   **0.0** | 🔴**HG-A1** — có lớp lỗi **hoàn toàn vô hình** |
| `archived_runs_scored`           | Số artefact được chấm                                         |               8 | (ngữ cảnh, không chấm điểm)                                  |

> Vì sao `min_recall_per_class` mới là cái chặn release, không phải F1: F1 = 0.16 vẫn có thể là *"bắt tốt vài lớp, kém vài lớp"*. `min = 0` nghĩa là **có ít nhất một loại lỗi mà hệ thống không bao giờ thấy** — nguy hiểm hơn nhiều so với "kém đều".

#### 1b · `vacuity_probe` — luật có **bao giờ fail được** không?

Không cần nhãn. Chỉ so tham số của luật với dữ liệu nó canh gác.

| Metric                                    | Đo cái gì                                     |       Kết quả | Nghĩa là                                                                           |
| ----------------------------------------- | ------------------------------------------------ | --------------: | ------------------------------------------------------------------------------------ |
| `vacuous_rule_rate`                     | % luật mà**vi phạm là bất khả thi**  | **0.428** | 80/187 luật không bao giờ báo lỗi được                                       |
| `worst_type_vacuity_rate`               | Loại luật tệ nhất                            | **0.892** | ACCEPTED_VALUES: 66/74 không thể fail                                              |
| **`systemic_vacuous_rule_types`** | Số loại luật mà**quá nửa** vô dụng |     **1** | 🔴**HG-A6** — không phải xui, mà **cơ chế sinh luật hỏng**       |
| `degenerate_threshold_rules`            | Luật sống nhưng vô dụng                     |    **17** | `min_row_count = 1` trên bảng 50.000 dòng                                       |
| `rules_not_judgeable`                   | Loại luật cố ý không phán xét             |             196 | `NOT_NULL` trên cột sạch là **guard hợp lệ**, không phải luật chết |

> **Đây là evaluator quan trọng nhất cho tương lai của sản phẩm**, vì nó là cái duy nhất **không cần nhãn** — nên là cái duy nhất còn hoạt động khi người dùng upload một dataset chưa ai gắn nhãn.

#### 1c · `governed_enum_conformance` — ngưỡng lấy từ **policy** hay từ **dữ liệu bẩn**?

| Metric                                | Đo cái gì                                                                               |       Kết quả | Nghĩa là                    |
| ------------------------------------- | ------------------------------------------------------------------------------------------ | --------------: | ----------------------------- |
| `governed_enum_conformance`         | Enum đề xuất phủ được bao nhiêu % tập hợp lệ theo policy                        | **0.571** | Thiếu 3/7 giá trị hợp lệ |
| **`tautological_enum_count`** | Số đề xuất**nạp vào allow-list đúng giá trị hợp đồng nói phải loại** |     **7** | 🔴**HG-A3**             |
| `unbacked_enum_rules`               | Enum trên cột**không có policy nào quản**                                      |    **76** | Tautological theo cấu tạo   |
| `planted_defect_recall`             | 4 dòng lỗi được**cố ý cài** → bắt được mấy                             |   **0.5** | Bắt 2/4                      |

> Hợp đồng `SUPABASE_DATASET_CONTRACT.md` cố ý để `"Invalid Payment (Dispute/Test)"` **ngoài** tập hợp lệ, để agent phát hiện 4 dòng lỗi. Agent lại **đưa chính giá trị đó vào danh sách hợp lệ** — luật tự vô hiệu hoá chính nó.

#### 1d · `golden_conformance` — có đề xuất **đúng loại luật** không?

Chạy 9 case kỳ vọng đã viết sẵn.

| Metric                                 | Đo cái gì                                                       |       Kết quả | Nghĩa là                                         |
| -------------------------------------- | ------------------------------------------------------------------ | --------------: | -------------------------------------------------- |
| `golden_case_pass_rate`              | % case đạt (chỉ tính case**kiểm được**)              | **0.375** | 3 đạt / 8 kiểm được / 1 không kiểm được |
| **`golden_critical_failures`** | Case mức CRITICAL trượt                                         |     **2** | 🔴**HG-A5**                                  |
| `golden_rule_expectation_rate`       | Tầng 2: đúng loại luật, đúng cột, đúng nguồn tham số   | **0.429** |                                                    |
| `golden_prompt_compliance_rate`      | Tầng 3: văn bản sinh ra có tuân prompt của chính nó không |   **0.0** | 30/383 lập luận không chứa một con số nào   |

**Case trượt đáng chú ý:** agent đề xuất **76 luật lên chính bảng vận hành của hệ thống** (`jobs` 28, `proposal_runs` 21, `proposed_rules` 14…) — tức là AI đang kiểm tra chất lượng nhật ký của chính nó.

#### 1e · `run_outcome_integrity` — lần chạy gần nhất có **ra được gì** không?

Bốn evaluator trên đều chấm *nội dung* thứ agent tạo ra. Không cái nào lên tiếng khi agent **không tạo ra gì cả**. Thêm ngày 22/08 sau khi một lần chạy thất bại toàn phần đi qua cổng mà điểm không nhúc nhích (§12).

| Metric                                     | Đo cái gì                                                    |        Kết quả | Nghĩa là                                                    |
| ------------------------------------------ | --------------------------------------------------------------- | ---------------: | ------------------------------------------------------------- |
| **`latest_run_produced_output`**     | Lần chạy mới nhất có sinh ra output nào không          | 🔴**False** | 🔴**HG-A7** — `EMPTY_OUTPUT`                            |
| `empty_run_rate`                         | Tỷ lệ lần chạy không ra gì trong 5 lần gần nhất     |     **0.80** | 4/5 lần chạy trắng tay                                     |
| **`schema_violation_rate`**          | % output có cấu trúc bị**chính validator sản phẩm** từ chối | 🔴**1.00** | 🔴**HG-A2** — gỡ khỏi `deferred` nhờ evaluator này |

> Điểm gate lấy theo **lần chạy mới nhất**, không lấy trung bình: quyết định release nói về hệ thống *lúc này*, và lấy trung bình cho phép một lần chạy tốt tuần trước trả nợ thay cho hệ thống đang hỏng hôm nay.
>
> Khi `output/` trống (checkout sạch trên CI), evaluator trả `NOT_MEASURED` kèm lý do — **không** trả PASS. Biến thiếu bằng chứng thành lời khẳng định khoẻ mạnh là lỗi tệ hơn không đo.

---

### GATE 2 — AI SECURITY · 25.00 điểm

> **Câu hỏi:** người ngoài có làm được điều không được phép không?

| Evaluator        | Metric                                           | Đo cái gì                                                      |                   Kết quả |
| ---------------- | ------------------------------------------------ | ----------------------------------------------------------------- | --------------------------: |
| `authz_probe`  | **`unauthenticated_mutating_endpoints`** | Endpoint**đổi trạng thái** mà không cần đăng nhập |      🔴**8** — HG-S1 |
|                  | `unauthenticated_read_endpoints`               | Endpoint đọc không cần đăng nhập                           |                           6 |
|                  | `total_endpoints_scanned`                      | Mẫu số                                                          |                          44 |
| `egress_probe` | `raw_row_egress_violations`                    | Artefact chứa**nguyên dòng dữ liệu** lọt ra ngoài    |                          19 |
|                  | `pii_column_egress_violations`                 | Trong đó có cột dữ liệu cá nhân                           |                           8 |
|                  | **`raw_or_pii_egress_violations`**       | Tổng                                                             |     🔴**27** — HG-S3 |
| `secret_scan`  | **`secret_findings`**                    | Mật khẩu/API key trong file được git theo dõi               | ✅**0** — HG-S6 PASS |
|                  | `tracked_files_scanned`                        | Mẫu số                                                          |                         362 |
| `default_credential_probe` | **`default_credentials_active`** | Tài khoản seed có mật khẩu đoán được, tạo mà không kiểm môi trường | 🔴**True** — HG-S7 |
|                  | `seeded_credential_count`                      | Số tài khoản như vậy                                          |                       **3** |

> `default_credential_probe` thêm ngày 22/08 để gỡ gate mồ côi **HG-S7** — gate này có trong policy từ v3 nhưng chưa từng có ai sinh metric, nên báo `NOT_EVALUATED` mọi lần chạy. Ba tài khoản `user` / `steward` / `admin` đều có mật khẩu trùng tên đăng nhập, seed từ `init_db()` **không kèm kiểm tra môi trường**. Đã xác nhận sống: `POST /api/v1/session` với `steward`/`steward` trả 200.
>
> **Vì sao 8/44 lại là 0 điểm chứ không phải 82 điểm:** trong 8 endpoint đó có `POST /dq/runs/{id}/publish` — tự duyệt và xuất bản luật mà **không cần đăng nhập**. Đó không phải "hỏng 18% cơ chế duyệt". Đó là **cơ chế duyệt không tồn tại**. Một cái khoá hỏng trong 44 cái thì ngôi nhà không an toàn 82%.

---

### GATE 3 — OBSERVABILITY · *chưa xây*

> **Câu hỏi:** khi hệ thống sai, có biết sai ở đâu không?

**Không có metric nào.** Lý do: OpenTelemetry bị comment trong `requirements.txt`, và đoạn khởi tạo tracing bị bọc `except: pass` — nên không ai biết tracing đang bật hay tắt.

Gate này báo `NOT_IMPLEMENTED` và **bị loại khỏi điểm tổng**, không bị tính 0 điểm. *"Chưa đo"* khác *"đo rồi và tệ"*.

---

### GATE 4 — INPUT DATA · 25.00 điểm

> **Câu hỏi:** dữ liệu đi vào hệ thống có còn nguyên vẹn không?

#### 4a · `ingest_fidelity`

| Metric                            | Đo cái gì                                                               |         Kết quả | Nghĩa là          |
| --------------------------------- | -------------------------------------------------------------------------- | ----------------: | ------------------- |
| `row_fidelity`                  | Giá trị**sạch** qua ingest rồi quay lại có nguyên vẹn không | ✅**100.0** | HG-D1 PASS          |
| `cell_fidelity`                 | Như trên, tính theo ô (2.200 ô)                                       |   **100.0** |                     |
| **`coercion_loss_count`** | Giá trị**hỏng** bị nuốt im lặng thành NULL/NaN/inf            |     🔴**8** | HG-D2               |
| `coercion_signal_rate`          | Trong các ca phải từ chối, bao nhiêu %**có báo ra**           |     **0.0** | Không ca nào báo |
| `null_ambiguity_rate`           | % NULL sinh do lỗi,**không phân biệt được** với NULL thật   |     **1.0** | 100%                |

> **Phải đọc hai dòng đầu và ba dòng sau cùng nhau:** dữ liệu sạch đi qua nguyên vẹn, nhưng **dữ liệu bẩn biến mất không để lại dấu vết**. `to_float("12,50")` → `None`. `to_float("1e999")` → `inf`. Không log, không đếm, không khác gì một ô vốn dĩ trống.
>
> Nghịch lý: đây là **công cụ kiểm tra chất lượng dữ liệu** đang tự tạo ra lỗi chất lượng dữ liệu ở bước nạp.

#### 4b · `multi_dataset_readiness`

Đo khoảng cách tới mục tiêu *"upload dataset bất kỳ"*. Cả 7 tiêu chí đều **False**:

`upload_surface_exists` · `schema_agnostic_row_storage` · `dataset_has_owner_or_schema` · `domain_not_hardcoded_in_prompt` · `dataset_deletion_endpoint` · `evidence_column_cap_sufficient` · `low_single_domain_coupling`

→ `multi_dataset_readiness_score = 0.0`, `single_domain_coupled_files = 32`

> ⚠️ **Khiếm khuyết thiết kế đã biết:** cái này đo *khoảng cách tới mục tiêu tương lai*, không đo *khuyết tật của cái đang có*. Trộn nó vào điểm chất lượng kéo `input_data` từ 50 xuống 25 — tức **một nửa** độ thấp của gate này là chỉ số lộ trình, không phải lỗi.

---

### GATE 5 — RELIABILITY · 28.57 điểm

> **Câu hỏi:** khi một phụ thuộc bên ngoài hỏng, hệ thống có chịu được không?

Bảy câu hỏi có/không về cấu hình:

| Biện pháp                         | Có? | Nếu thiếu thì sao                                          |
| ----------------------------------- | :--: | ------------------------------------------------------------- |
| `llm_timeout_configured`          |  ❌  | LLM treo ⇒ job treo vô hạn                                 |
| `db_statement_timeout_configured` |  ❌  | Một truy vấn chậm khoá cả worker                         |
| `upload_size_limit_configured`    |  ❌  | File khổng lồ ⇒ hết bộ nhớ                              |
| `per_tenant_quota_configured`     |  ✅  |                                                               |
| `job_queue_out_of_process`        |  ❌  | Dùng`BackgroundTasks` tại 12 chỗ ⇒ restart là mất job |
| `retry_policy_configured`         |  ✅  |                                                               |
| `circuit_breaker_configured`      |  ❌  | Phụ thuộc hỏng ⇒ thử lại mãi                           |

**2/7 = 28.57 điểm.** Không có hard gate ở đây — vì chưa có SLO thật để làm chuẩn.

---

### GATE 6 — GOVERNANCE · 25.93 điểm

> **Câu hỏi:** quyết định của AI có truy vết được không, và con người có thực sự nắm quyền không?

Sáu evaluator — đây là gate có nhiều hard gate nhất.

| Evaluator                 | Metric                                        | Đo cái gì                                                   |                   Kết quả |
| ------------------------- | --------------------------------------------- | -------------------------------------------------------------- | --------------------------: |
| `policy_resolution`     | **`policy_resolution_success_rate`**  | % dataset lấy được policy                                  |    🔴**0.0** — HG-G1 |
|                           | `required_asset_presence`                   | % tài sản governance còn tồn tại                          |               **0.0** |
| `hitl_integrity`        | **`hitl_integrity`**                  | % thao tác duyệt luật**để lại nhật ký**          |    🔴**0.0** — HG-G2 |
|                           | `unaudited_transitions`                     | Số thao tác không ghi nhật ký                             |                         2/2 |
|                           | `reviewer_persisted`                        | Danh tính người duyệt có được lưu không              |                     ✅ True |
| `contract_conformance`  | `safety_rule_conformance`                   | **6 quy tắc an toàn** dự án tự viết ra, đạt mấy |               **2/6** |
|                           | **`internal_field_exposed_count`**    | Chi tiết nội bộ lộ ra API công khai                       |      🔴**1** — HG-S8 |
|                           | **`forgeable_actor_fields`**          | Người duyệt do client**tự khai**                     |      🔴**1** — HG-G4 |
|                           | `job_state_vocabulary_violations`           | Trạng thái job ngoài 5 giá trị trong hợp đồng          |                           1 |
|                           | `duplicate_run_state_tables`                | Số bảng cùng mang trạng thái thực thi                    |                           5 |
| `capability_regression` | **`critical_capability_regressions`** | Năng lực**có ở bản trước, giờ mất**             |      🔴**1** — HG-R1 |
|                           | `capability_regressions`                    | Tổng mọi mức                                                |                           2 |
|                           | `capability_known_gaps`                     | Khoảng trống**có từ trước** (không chặn)         |                           9 |
|                           | `capability_improvements`                   | Năng lực mới xuất hiện                                    |                           1 |
| `regression_engine`     | `gate_score_drop_max`                       | Evaluator tụt nhiều nhất bao nhiêu điểm                  |                 ✅**0.0** |
|                           | **`hard_gates_newly_failing`**        | Hard gate từng đạt nay hỏng                                | ✅**0** — HG-R3 PASS |
| `served_path_fidelity`  | **`served_path_is_mocked`**           | Đường người dùng đi có gọi agent thật không          |    🔴**True** — HG-G5 |
|                           | `mock_branch_count`                         | Số nhánh short-circuit sang output đóng hộp               |                       **1** |
|                           | `llm_credential_reaches_service`            | Có credential nào tới được service không                 |                 **False** |

**Ba con số nặng nhất trong toàn bộ báo cáo:**

- `policy_resolution_success_rate = 0.0` — **0/7 dataset** lấy được policy. File `src/resources/rule_policies.json` bị xoá ở commit `ac4b663`. Không phải "hỗ trợ kém dataset lạ" — mà là **không dataset nào chạy được**, kể cả dataset sản phẩm ship kèm.
- `hitl_integrity = 0.0` — chạy thử thật trong CSDL tạm: duyệt luật rồi xuất bản, **không một dòng nhật ký nào được ghi**. Nghĩa là không thể chứng minh ai đã duyệt luật nào.
- `critical_capability_regressions = 1` — hàm tính **DQ Score** có ở commit hiện tại nhưng **đã bị xoá** trong phần thay đổi đang chờ commit. Không có lớp nào khác phát hiện: không phải lỗi cú pháp, không test nào phủ, và review thấy một file quen tên.

> ✅ Metric này **từng** báo `14.47` — một **báo động giả** (DEFECT-11): nó so trung bình
> gate của 4 evaluator với trung bình của 5. **Đã sửa** ở §12.2 — so sánh giờ theo từng
> evaluator, chỉ trên phần giao; lần chạy hiện tại báo `score_drops = []`.

---

### GATE 7 — BUSINESS · *chưa đo được*

> **Câu hỏi:** AI có thực sự giảm việc cho Data Steward không?

**Không có metric nào.** Điều kiện tối thiểu: ≥3 dataset và ≥20 đề xuất thật trong CSDL. Hiện chưa đạt.

Dữ liệu thô **đã có sẵn** trong `rule_proposals.status` và `audit_events` — chỉ chưa có evaluator đọc. Gate báo `NOT_MEASURED` và bị loại khỏi điểm tổng thay vì bị tính 0.

---

### 5.8 Ba hard gate được **cố ý gác lại**

Không được chấm, không tính là coverage — vì hệ thống hiện tại **không có cách nào** đo chúng:

| ID    | Đo gì                                         | Cần gì mới đo được                                      |
| ----- | ----------------------------------------------- | -------------------------------------------------------------- |
| HG-A2 | Structured output vi phạm schema               | Agent chạy live — đang bị chặn bởi`rule_policies.json` |
| HG-S4 | Upload file độc hại được chấp nhận      | **Endpoint upload** — sản phẩm chưa có              |
| HG-S5 | Prompt injection gián tiếp lái được agent | promptfoo + mạng + tiền LLM                                  |

> Một gate báo `NOT_EVALUATED` vĩnh viễn **không phải là coverage** — nó trông như một lớp kiểm soát trong khi không bảo đảm điều gì. Nên chúng nằm ở mục `deferred:` kèm điều kiện bật lại, thay vì giả vờ đang chạy.

---

### 5.9 Đọc điểm số cho đúng

Điểm gate **không phải trung bình**. Aggregator lấy **phân vị 25** trên breakdown, nên nó gần *"phần tư tệ nhất"* hơn là *"trung bình"*:

| Evaluator              | Tự báo | Aggregator**thực dùng** |
| ---------------------- | -------: | ------------------------------: |
| `golden_conformance` |    37.50 |                  **0.00** |
| `replay_detection`   |    15.98 |                  **0.00** |
| `vacuity_probe`      |    57.22 |                 **43.83** |

Lý do: sản phẩm hứa *"chạy được trên dataset bất kỳ"*. Nếu sáu dataset tốt che được một dataset hỏng thì lời hứa đó không kiểm chứng được.

**Điều quan trọng hơn con số:** hard gate chạy **trước** điểm. Kể cả nếu chỉnh cách chấm cho điểm lên 60 hay 80, quyết định vẫn là `RELEASE_BLOCKED` — vì riêng `policy_resolution_success_rate = 0` đã đủ chặn.

> **Điểm số ở đây không để đẹp. Nó để xếp thứ tự nên sửa gì trước.**

---

## 6. TỰ ĐÁNH GIÁ — DEFECT ĐÃ SỬA VÀ CÒN LẠI

### 6.1 Đã sửa trong đợt này

> **DEFECT-11 đã đóng** ngày 22/08/2026 cùng đợt vá điểm mù. Chi tiết và đoạn code
> trước/sau ở [§12.2](#12-vá-điểm-mù--phát-hiện-từ-lần-chạy-thật-22082026).

#### DEFECT-1 🟠 → ✅ Gate quên regression ngay khi nó được commit

`capability_regression` mặc định so **HEAD với index**. Ngay khi thay đổi được commit, HEAD *chính là* phiên bản đã mất năng lực.

```text
TRƯỚC:
  [A] baseline=HEAD vs index  →  dq_score_computed = REGRESSION  ← bắt được
  [B] baseline=HEAD vs HEAD   →  dq_score_computed = INTACT      ← MẤT DẤU

SAU:
  RUN 1: baseline: none stored     (capabilities compared against HEAD)
  RUN 2: baseline: evalgate-…4210Z (capabilities compared against 31e065a)  ← sha thật
```

Nguyên nhân: `save_run()` lưu `git_ref` và `resolve_baseline()` đọc lại được, nhưng **hai nửa không được đấu với nhau**. Sửa: `resolve_baseline_ref()` trong `run.py`.

**Kèm theo một lỗ hổng an toàn phát hiện khi sửa:** nếu baseline ref không còn tồn tại (rebase/gc), `list_files` trả rỗng ⇒ mọi năng lực trông như "vắng mặt ở baseline" ⇒ **0 regression**. Đã thêm `ref_exists` + `BaselineUnavailableError` ⇒ `BLOCKED_MISSING_GROUND_TRUTH`.

#### DEFECT-2 🟡 → ✅ Regression engine mù với hard gate không kèm Finding

Nó dò `Finding.id`, nhưng `HG-D2` FAIL thuần bằng metric rule trong khi `ingest_fidelity` phát Finding id `HG-D1`. Sửa: gọi thẳng `evaluate_hard_gates(results)`.

#### DEFECT-3 🟡 → ✅ `governed_enum` chỉ kiểm một cột

Sửa: `load_governed_domains()` số nhiều + `per_dataset_breakdown` từng cột + collapse MIN. Bổ sung metric `unbacked_enum_rules` = **76**.

#### DEFECT-4 🟡 → ✅ HG-A3 chưa nêu rõ nói về đường nào

Sửa: `_PATH_SCOPE_NOTE` đưa vào cả evidence lẫn text của Finding.

#### DEFECT-5 🟡 → ✅ Không bao giờ có baseline

Repo luôn bẩn ⇒ run luôn STALE ⇒ không run nào được lưu. Sửa: lưu cả run STALE với `usable_as_baseline: false`.

#### DEFECT-6 🟢 → ✅ Va chạm tên metric

Thêm `detect_metric_collisions`, đưa vào `AggregateOutcome` và report. Hiện 0 va chạm trên 62 metric.

#### DEFECT-7 🟢 → ✅ Cache git không xoá

`git_read.clear_cache()` gọi ở đầu `collect_results()`.

### 6.2 Lỗi phát hiện khi xây golden

**Golden evaluator bản đầu vi phạm nguyên tắc của chính EvalGate.** `GL-RATIONALE-NO-TECHNICAL-NAMES` báo FAIL trong khi thực chất artefact cũ **không có** trường `business_rationale` để kiểm. Đó là "chưa đo" bị tính thành "đo rồi và tệ" — đúng thứ mà `EXCLUDED_FROM_AGGREGATE` tồn tại để ngăn, chỉ là ở một tầng khác.

Sửa: thêm cờ `measurable` cho `AssertionOutcome` và `CaseOutcome`; case không kiểm được → `NOT_MEASURED`, bị loại khỏi pass rate, và lý do được ghi vào `metadata.not_inspectable`.

### 6.3 Lịch sử độ chính xác — 5 lỗi đã sửa khi xây Phase A

| Check           | Lỗi bản đầu                                                              | Đã sửa thành                                       |
| --------------- | ---------------------------------------------------------------------------- | ------------------------------------------------------ |
| `SAFETY-1`    | Coi`DELETE FROM trips_raw` (reload idempotent) là mutation                | Chỉ tính`UPDATE`; reload báo riêng như ghi chú |
| `SAFETY-2`    | Coi`routes.py` là module gọi LLM                                         | Thu hẹp vào`src/agents/`                           |
| `SAFETY-6`    | **Bỏ sót** `sql_text` vì chỉ nhìn `response_model=` khai báo | Closure đi theo field lồng nhau                      |
| `AUDIT-ACTOR` | Báo nhầm`username = body.username` của endpoint admin                   | Chỉ giữ`reviewer/actor/approved_by`                |
| ma trận ingest | `to_int("٣")` gán nhãn REJECT                                           | Là chuẩn hoá Unicode hợp lệ → ACCEPT             |

### 6.4 Đợt sửa sau golden — 2 bug trong SDIH, phát hiện nhờ chạy trên schema lạ

Cả hai lộ ra khi tôi chạy SDIH trên một dataset domain thư viện chưa từng có trong repo — đúng tình huống mà sản phẩm hướng tới ("upload dataset bất kỳ").

#### DEFECT-8 🟠 → ✅ Donor của DUPLICATE_ROW lại chính là một target khác

```python
# injector.py:249 (trước)
donors = rng.permutation(len(dirty))    # lấy từ TOÀN BỘ frame, kể cả các target
```

Vòng lặp sau ghi đè chính donor đó ⇒ giá trị vừa copy sang `pos` không còn tồn tại ở đâu ⇒ nhãn nói *"đây là bản trùng"* nhưng nó **không trùng**. Agent bị trừ điểm oan cho một defect không tồn tại.

**Sửa:** loại toàn bộ target khỏi donor pool. Kèm test `test_duplicate_donor_is_never_itself_a_target`.

#### DEFECT-9 🟠 → ✅ `_disjoint_slices` chỉ đảm bảo DÒNG rời nhau, không đảm bảo CỘT rời nhau

Bằng chứng trực tiếp: donor giữ `ord-001931#` — `FORMAT_VIOLATION` đã thêm `#` vào đúng cột mà `DUPLICATE_ROW` vừa copy sang.

**6/7 archetype có ít nhất một cột bị ≥2 defect class cùng nhắm**, 3 trong đó rơi đúng vào cột của `DUPLICATE_ROW`.

Điểm cốt lõi: với defect **cục bộ trong ô** (MISSING_VALUE, SIGN_FLIP…) thì dòng-rời-nhau là đủ. Với `DUPLICATE_ROW` và `CROSS_FIELD_VIOLATION` — nhãn của chúng là phát biểu về **quan hệ giữa các dòng** — dòng-rời-nhau **không đủ**.

**Sửa:** `RELATIONAL_DEFECTS` chọn cột trước và **giữ độc quyền**; class cục bộ không được dùng lại cột đó. Kèm `plan.warnings` cho va chạm cột cục bộ (không phải bug, nhưng nhãn "cột vừa thiếu vừa sai kiểu vừa outlier" khó diễn giải).

Hệ quả có chủ đích: `corpus-synth-retail` mất 50 nhãn vì `STALE_TIMESTAMP` giờ báo `NOT_APPLICABLE` thay vì sinh nhãn sai. **Ít nhãn nhưng đúng, tốt hơn nhiều nhãn mà có nhãn sai.**

#### DEFECT-10 🟡 → ✅ Điểm mù tôi tự tạo trong `freeze --verify`

`freeze --verify` chỉ so **fingerprint** và **sha256**. Nó báo `OK` trong khi 2 nhãn sai sự thật.

Bằng chứng thuyết phục nhất: sau khi sửa DEFECT-8, fingerprint của `corpus-synth-wide` **không đổi** (`38aa5cbd7cd0`) dù 2 nhãn của nó đã được sửa — **vì fingerprint không mã hoá donor**. Kiểm-fingerprint-đơn-thuần về mặt cấu tạo không thể bắt được bug này.

**Sửa:**

- `build_labels` giờ chạy `verifier.verify()` và trả kèm report
- `freeze` **từ chối ghi** archetype có nhãn sai (`status: REJECTED`, exit 1)
- `freeze --verify` kiểm **cả hai** chiều: toàn vẹn (sha256/fingerprint) **và** đúng đắn (ngữ nghĩa)
- 14 test mới, parametrize trên cả 7 archetype

> **Bài học:** tôi đã kiểm tra *tính toàn vẹn* mà bỏ qua *tính đúng đắn*. `verifier.py` tồn tại sẵn để trả lời câu thứ hai nhưng **không call site nào trong pipeline gọi nó** — chỉ có test kiểm ca phủ định. Đó là lý do bug sống sót.

#### Mức nhiễm trước khi sửa

|                                          |                                                                                                                 |
| ---------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| Nhãn sai trên golden đã đóng băng | 2 / 302 nhãn`DUPLICATE_ROW` (0.7%)                                                                           |
| Thuộc tính tệ hơn con số            | **Số nhãn sai thay đổi theo `MAX_ROWS`** — ở 3.000 dòng retail sai 2, ở 20.000 dòng wide sai 2 |

Một tập tham chiếu mà tính đúng đắn phụ thuộc số dòng thì không dùng làm tham chiếu được. Sau khi sửa: **7/7 archetype sạch ở cả hai mức row count.**

### 6.5 Còn lại — chưa sửa

| ID            | Vấn đề                                                                                                   | Mức | Vì sao chưa sửa                                                                            |
| ------------- | ----------------------------------------------------------------------------------------------------------- | :--: | --------------------------------------------------------------------------------------------- |
| **R-1** | Marker capability là regex trên text. Refactor hợp lệ có thể gây false positive                      |  🟡  | Cần`suppressions.yaml` có TTL + owner (P-19)                                              |
| **R-2** | `contract_conformance` dựa AST + regex; decorator động sẽ bị bỏ sót                                |  🟡  | Cần probe động (P-12…)                                                                    |
| **R-3** | `egress_probe` có 1 false positive đã biết (sink `llm_provider`)                                    |  🟡  | Cần`suppressions.yaml`                                                                     |
| **R-4** | Golden tier 2/3 chỉ chạy**replay** trên artefact cũ                                               |  🟠  | Bị chặn bởi`rule_policies.json` — vấn đề của **sản phẩm**                   |
| **R-5** | 7 evaluator có từ trước Phase A vẫn**chưa có test**                                            |  🟡  | P-20                                                                                          |
| **R-6** | Hardcode miền NYC còn tồn đọng                                                                         |  🟡  | Nên tổng quát hoá**cùng lúc** với sản phẩm, không trước                     |
| **R-7** | `run_outcome_integrity` đọc `output/` — bị gitignore nên **CI sạch trả `NOT_MEASURED`**  |  🟠  | Cần đẩy artefact lên như build artifact (§12.5)                                         |
| **R-8** | `served_path_fidelity` chỉ đọc file repo, **không hỏi `/api/v1/status`** của instance sống |  🟡  | Cần target sống; profile`ci` là $0 và offline theo thiết kế                           |
| **R-9** | `HG-S2` (BOLA/BFLA) vẫn chưa đo được, đã chuyển `deferred`                                     |  🟠  | Cần ASGI probe (P-08b); kiểm tra tĩnh không xác lập được truy cập đã thành công |

---

## 7. HARDCODE INVENTORY

### 7.1 Hợp lý (hằng số chính sách, có giải thích tại chỗ)

| Giá trị                                                               | Vị trí                                                  |
| ----------------------------------------------------------------------- | --------------------------------------------------------- |
| `MIN_MEASURED_WEIGHT = 0.60`                                          | `aggregator.py`                                         |
| `SCORE_DROP_LIMIT = 10.0`                                             | `core/regression_engine.py`                             |
| `SDIH_SEED = 20260819`                                                | `run.py`, `golden/freeze.py` — bắt buộc cố định |
| `MAX_ROWS = 20_000`                                                   | `golden/freeze.py` — ghi trong manifest                |
| `decision_bands` 85/70 · 19 hard gate rule (+3 deferred) · 7 weight | `policies/*.yaml` — đúng chỗ                        |

### 7.2 Nên đưa vào config 🟡

| Giá trị                         | Vị trí                        | Rủi ro                                                   |
| --------------------------------- | ------------------------------- | --------------------------------------------------------- |
| `("source_rows","trips_raw")`   | `contract_conformance.py:129` | Thêm bảng raw mới ⇒ SAFETY-1 im lặng bỏ qua         |
| `PRODUCT_PATHSPEC`              | `workspace_integrity.py:39`   | Thêm thư mục sản phẩm mới ⇒ preflight không thấy |
| `INTERNAL_RESPONSE_FIELDS`      | `contract_conformance.py:50`  | Trường nội bộ mới không tự động được bảo vệ |
| `ROUND_TRIP_ARCHETYPES`         | `ingest_fidelity.py:44`       | 3/7 archetype được dùng                               |
| `"source_rows"` (fixture probe) | `hitl_integrity.py:91`        | Cosmetic                                                  |

> `payment_type` **đã được gỡ hardcode** trong đợt này (DEFECT-3).

### 7.3 Hardcode miền NYC còn tồn đọng (có từ trước) 🟡

`PROBE_DATASETS` · `"dataset-nyc-yellow-taxi-50k"` (`policy_resolution.py`) · regex `yellow_tripdata|vendor_id|…` (`multi_dataset_readiness.py`) · `_nyc_ground_truth()` (`run.py`) · `REQUIRED_ASSETS` 3 đường dẫn `src/resources/*`.

Nhóm này phản ánh đúng thực tế sản phẩm hiện chỉ hỗ trợ một dataset.

### 7.4 Không phát hiện

- ✅ Không credential, API key, endpoint, đường dẫn tuyệt đối nào trong `evalgate/`
- ✅ Không ngưỡng nghiệp vụ nào bị chôn trong code mà không có giải thích
- ✅ `secret_scan_v1` quét 293 file: 0 finding

---

## 8. ĐỘ PHỦ SO VỚI SẢN PHẨM THỰC TẾ

|  # | Bề mặt sản phẩm           | Module chính                         | Đo? | Mức                                          |
| -: | ----------------------------- | ------------------------------------- | :--: | --------------------------------------------- |
|  1 | Auth / RBAC                   | `session_service.py`, `routes.py` | ⚠️ | Static AST,**chưa gửi request thật** |
|  2 | Ingest coercion               | `worker.py`                         |  ✅  | Gọi thẳng hàm thật                        |
|  3 | Ingest toàn tuyến           | `job_runner.run_ingest_profile`     |  ❌  | Chưa có upload endpoint                     |
|  4 | **Profiling**           | `db_profiler_tool.py` (605)         |  ❌  | **Không đo**                          |
|  5 | Semantic contract             | `dataset_understanding_node`        |  ❌  | Không đo                                    |
|  6 | Rule proposal (LLM)           | `rule_proposer_node.py` (787)       | ⚠️ | replay + governed enum + golden — chưa live |
|  7 | HITL                          | `rule_store.py`                     |  ✅  | Probe hành vi                                |
|  8 | **Sinh SQL / dbt YAML** | `test_generator_node.py` (700)      |  ❌  | **Không đo**                          |
|  9 | dbt gate                      | `validate_dbt_project_node`         |  ❌  | Không đo                                    |
| 10 | Thực thi test                | `test_runner_node.py` (610)         |  ❌  | Không đo                                    |
| 11 | **Anomaly detection**   | `anomaly_service.py` (400)          |  ❌  | **Không đo**                          |
| 12 | Hypothesis agent              | `steward_insights_node.py`          |  ❌  | Không đo                                    |
| 13 | **Report writer**       | `report_writer_node.py` (343)       |  ❌  | **Không đo**                          |
| 14 | API contract                  | `routes.py`, `schemas.py`         |  ✅  | 9 assertion                                   |
| 15 | Config / reliability          | toàn`src/`                         |  ✅  | 7 boolean                                     |
| 16 | Frontend                      | `frontend/`                         |  —  | N/A                                           |
| 17 | **Đường phục vụ (mock vs agent)** | `config.py`, `docker-compose.yml` |  ✅  | `served_path_fidelity` — tĩnh, thêm 22/08 |
| 18 | **Kết cục lần chạy**    | artefact `output/**`                |  ✅  | `run_outcome_integrity` — thêm 22/08        |
| 19 | Credential mặc định         | `session_service.py`, `rule_store.py` |  ✅  | `default_credential_probe` — thêm 22/08     |

> **Không evaluator nào kiểm tra *hành vi* của 4 module chứa lỗi nghiêm trọng nhất:** `anomaly_service.py`, `test_generator_node.py`, `report_writer_node.py`, `db_profiler_tool.py`. Cả bốn **đo được ngay, $0, không cần LLM** vì đều là hàm thuần.
>
> Ba bề mặt 17–19 được thêm ngày 22/08 sau khi chạy thật hệ thống (§12). Chúng nâng độ phủ từ **3/16 lên 6/19**, nhưng không đụng tới bốn module trên — đó vẫn là khoảng trống lớn nhất.

---

## 9. THƯ MỤC `eval/` ĐÃ ĐƯỢC XỬ LÝ

`eval/` là di sản của plan v1, dự định tạo `eval/eval_deepeval_framework.py` và `eval/golden_dataset.json` — **cả hai chưa bao giờ được tạo**. Chỉ còn vỏ với 2 file.

| File                                            | Thực trạng                                                                                              | Đã làm                    |
| ----------------------------------------------- | --------------------------------------------------------------------------------------------------------- | ---------------------------- |
| `eval/results/report.md` (46 dòng)           | Template**rỗng hoàn toàn**: mọi metric là `—`/`⏳`, `[User 1]`, `# Paste output here` | **ĐÃ XOÁ**          |
| `eval/results/E1_E5_EVALUATION.md` (30 dòng) | Mô tả E1–E5 bằng văn xuôi, không số, không run id                                                | **ĐÃ CHUYỂN ĐỔI** |
| Thư mục`eval/`                              |                                                                                                           | **ĐÃ XOÁ**          |

Việc đã làm, theo đúng thứ tự phụ thuộc đã nêu trong plan:

1. Nội dung E1–E5 → `evalgate/golden/tier2_rules/e1_e5.cases.yaml` (**thực thi được, chạy trong CI**)
2. Văn bản → `docs/EVAL_EVIDENCES_E1_E5.md`, có ghi chú trỏ tới bản YAML
3. `README.md:48` cập nhật link ✅ **trước** khi xoá
4. `docs/guide/deliverables/checklist.md` trỏ `eval/results/report.md` → `evalgate/reports/report.md`
5. `rm -rf eval` — xác nhận không còn link gãy

**Cố ý không sửa:** `PR12_Report.md` vẫn nhắc `eval/results/*`. Đó là bản ghi lịch sử của một PR đã xảy ra; sửa nó là làm sai lệch hồ sơ.

---

## 10. PLAN CÒN LẠI

> **Trạng thái: PROPOSED.** Chưa thực hiện. Không thao tác git nào.

### 10.1 P1 — đóng 4 khoảng trống hành vi

| ID             | File mới                                            | Đo gì                                                                                                                                                                                                                    | Chi phí |
| -------------- | ---------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- |
| **P-12** | `gates/gate1_ai_quality/anomaly_logic_probe.py`    | Dựng lịch sử tổng hợp trong DB tạm; assert rule 0.9% vi phạm sau 30 lần 0%**phải** báo bất thường (ngưỡng cứng `current_rate > 0.01` hiện chặn); assert VOLUME_DRIFT có lịch sử dùng được | $0       |
| **P-13** | `gates/gate1_ai_quality/sql_compilation_probe.py`  | Bảng golden`(rule, dialect) → predicate`; assert `generate_dbt_test_yaml` không âm thầm bỏ NULL_RATE / REGEX_FORMAT / ROW_COUNT                                                                                  | $0       |
| **P-14** | `gates/gate1_ai_quality/report_grounding_probe.py` | Mọi con số trong báo cáo Steward phải xuất hiện trong`dq_results`                                                                                                                                                 | $0       |
| **P-15** | `gates/gate4_input_data/profile_accuracy_probe.py` | Profile dataset đã biết trước; assert quantile / distinct / null_rate                                                                                                                                                 | $0       |

Nâng độ phủ hành vi **3/16 → 7/16**, vẫn 0 dependency, $0 LLM.

### 10.2 P1 — độ chính xác

| ID              | Hành động                                                                                                |
| --------------- | ----------------------------------------------------------------------------------------------------------- |
| **P-19**  | `evalgate/config/suppressions.yaml` — acknowledge false positive có TTL + owner (giải quyết R-1, R-3) |
| **P-20**  | Test cho 7 evaluator có từ trước Phase A (R-5)                                                          |
| **P-08b** | `gates/gate2_security/tenant_isolation_probe.py` — BOLA/BFLA qua `httpx.ASGITransport` → HG-S2        |

### 10.3 P2 — vệ sinh

| ID             | Hành động                                                                              |
| -------------- | ----------------------------------------------------------------------------------------- |
| **P-18** | Đưa 5 giá trị §7.2 vào`config/`                                                   |
| **P-21** | 3 lỗi ruff có sẵn:`corpus/generator.py`, `pii_classifier.py`, `test_evalgate.py` |
| **P-26** | Namespace metric`<evaluator>.<metric>` (hiện chỉ mới *phát hiện* va chạm)       |

### 10.4 PHASE B — cần duyệt riêng (đụng dữ liệu và CI)

| ID             | Hành động                                                                                                                                                                                      | Rủi ro      |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------ |
| **P-22** | **Redact** 19 artefact ở `output/test_runner/` chứa raw row 22 cột; 2 file ở `data/results/` chứa cột `license_plate`                                                           | Dữ liệu    |
| **P-23** | `evalgate/.gitignore` — bỏ `evidence/**`. ⚠️ **Chỉ sau P-22**, nếu không sẽ commit PII                                                                                          | Dữ liệu    |
| **P-24** | `.github/workflows/ci.yml` — thêm branch `chien`, `ruff check evalgate/`, `pytest evalgate/tests/`, `python -m evalgate.run --mode ci`, `python -m evalgate.golden.freeze --verify` | CI đỏ ngay |
| **P-25** | `pytest.ini` — `testpaths = tests evalgate/tests`                                                                                                                                            | Thấp        |

> Khi bật P-24, CI sẽ đỏ ngay (exit 4). Đó là hành vi đúng. Đề nghị 2 tuần `continue-on-error: true`.
>
> **P-24 cũng là điều kiện để R-4 tự biến mất:** trong CI cây luôn sạch (checkout từ commit), nên `EVALGATE_STALE` không còn xảy ra và baseline được tạo tự nhiên.

### 10.5 Thứ tự

```text
1. P-12 → P-15    bốn khoảng trống hành vi   ← giá trị cao nhất, $0
2. P-19, P-20     độ chính xác + trả nợ test
3. P-08b          BOLA động
4. P-18, P-21, P-26  vệ sinh
   ───── cần duyệt riêng ─────
5. P-22 → P-23    redact rồi mới bỏ gitignore
6. P-24, P-25     nối CI
```

---

## 11. CÁCH CHẠY VÀ CÁCH REVIEW

### 11.1 Chạy

```bash
python -m evalgate.run --mode local          # ~6s, chỉ static check
python -m evalgate.run --mode ci             # cổng merge, mọi evaluator $0
python -m evalgate.run --mode ci --dry-run   # không ghi gì ra đĩa
python -m evalgate.run --mode ci --allow-dirty
python -m evalgate.run --mode ci --baseline evalgate-20260822T014210Z-3c593b

python -m evalgate.golden.freeze             # đóng băng lại tier 1
python -m evalgate.golden.freeze --verify    # kiểm nhãn không trôi

pytest evalgate/tests/ -q                    # 118 self-test
```

**Exit code:** `0` PASS · `1` WARNING · `2` FAIL · `3` RELEASE_BLOCKED · `4` EVALGATE_STALE · `5` INSUFFICIENT_COVERAGE

### 11.2 Cách review một cách hoài nghi

| Câu hỏi                             | Lệnh                                                                                                |
| ------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Điểm này từ đâu ra?             | `evalgate/reports/report.md` §Metrics — raw → normalized → threshold → weight                 |
| Finding này có thật không?        | Mỗi Finding có`evidence_ref` trỏ file JSON có file:line                                        |
| Gate có tự chấm dễ dãi không?   | `pytest evalgate/tests/ -k "hard_gate or coverage or gap"`                                         |
| Golden nhãn có bị sửa tay không? | `python -m evalgate.golden.freeze --verify`                                                        |
| EvalGate có sửa gì không?         | `git status --short` — mọi thay đổi phải trong `evalgate/` (trừ 3 file docs đã nêu §9) |
| Có chạm DB thật không?            | `md5sum steward_local.db` trước/sau — đã kiểm, không đổi                                  |
| Có gọi LLM tốn tiền không?       | `CostRecord` trong `result.json` — `llm_usd: 0.0` toàn bộ                                   |
| Regression gate có hoạt động?     | `pytest evalgate/tests/test_phase_a.py -k detects_the_regression`                                  |
| EvalGate có thể sửa repo không?   | `evalgate/core/git_read.py` — allow-list 8 subcommand read-only                                   |

### 11.3 Điều KHÔNG được kết luận từ báo cáo này

- ❌ *"Điểm tăng nên hệ thống tốt lên"* — các lần chạy đo khác nhau, không so sánh được
- ❌ *"HG-S6 PASS nên không có secret"* — chỉ có nghĩa 362 file tracked không khớp pattern đã biết
- ❌ *"HG-D1 PASS nên ingest an toàn"* — giá trị sạch round-trip đúng; thiệt hại nằm ở HG-D2
- ❌ *"golden_case_pass_rate 0.375 là chất lượng agent"* — 9 case không phải mẫu đại diện; đó là 9 kỳ vọng cụ thể đã viết ra
- ❌ *"gate không FAIL nghĩa là an toàn"* — 3 hard gate nằm ở `deferred` (HG-S2, HG-S4, HG-S5) nên **không được tính là độ phủ**
- ❌ *"EvalGate phủ hệ thống"* — 4 module rủi ro nhất chưa được đo hành vi (§8)

---

---

## 12. VÁ ĐIỂM MÙ — PHÁT HIỆN TỪ LẦN CHẠY THẬT 22/08/2026

### 12.1 Điểm mù được phát hiện thế nào

Ba mục ở §5 và §6 đều dựa trên việc đọc artefact đã lưu. Ngày 22/08/2026 chúng tôi **chạy thật sản phẩm** rồi chấm lại, và phát hiện một lỗ hổng không suy ra được từ việc đọc code:

```text
Trước khi chạy sản phẩm : score 28.03
Sau khi Run 1 THẤT BẠI  : score 28.03    ← không đổi một chút nào
```

Run 1 thất bại toàn phần (0 rule, LLM trượt validator trên toàn bộ bảng). Sản phẩm ghi ra 7 artefact mới. EvalGate đọc hết và báo cáo **không có gì thay đổi**.

Nguyên nhân: mọi evaluator ở gate `ai_quality` đều chấm **nội dung** của thứ agent tạo ra — rule có vô hiệu không, có khớp golden set không, có tôn trọng governed domain không. Tất cả đều **im lặng** khi câu trả lời là *"agent không tạo ra gì cả"*, vì một lần chạy không sinh rule thì cũng không sinh rule nào để bắt lỗi.

Lần chạy đó cũng phơi ra hai điều nữa:

* `GET /api/v1/status` trên container trả `"agent_mode":"mock"` — **đường web không hề gọi LLM**
* `docker exec ridepulse-api env` cho thấy **không có `OPENAI_API_KEY`** trong service

Không evaluator nào hỏi hai câu đó.

### 12.2 Bốn thay đổi

| # | Việc                          | File                                                        |
| - | ------------------------------ | ----------------------------------------------------------- |
| 1 | Evaluator kết cục lần chạy | `gates/gate1_ai_quality/run_outcome_integrity.py` (mới)  |
| 2 | Evaluator đường phục vụ   | `gates/gate6_governance/served_path_fidelity.py` (mới)   |
| 3 | Producer cho HG-S7             | `gates/gate2_security/default_credential_probe.py` (mới) |
| 4 | Sửa DEFECT-11                 | `core/regression_engine.py`                               |

#### 1 · `run_outcome_integrity_v1` — "lần chạy gần nhất có ra gì không?"

Đọc luồng artefact tương quan sẵn có `output/<stage>/<tên>_<ngày>_<giờ>_<run_id>.json`, gom theo `run_id`, rồi với mỗi lần chạy xác định ba dữ kiện: có tới được stage cuối không, stage đó có output không, và bao nhiêu structured output bị chính validator của sản phẩm từ chối.

Bốn quyết định thiết kế đáng chú ý:

* **Quy thuộc theo signature stage, không theo terminal stage.** Một lần chạy chết trước stage cuối chính là ca cần bắt; khoá theo stage cuối sẽ làm nó tàng hình.
* **Điểm lấy theo lần chạy mới nhất, không lấy trung bình.** Quyết định release nói về hệ thống *lúc này*; lấy trung bình cho phép một lần chạy tốt tuần trước trả nợ thay cho hệ thống đang hỏng hôm nay.
* **Không có artefact → `NOT_MEASURED`, không phải `PASS`.** `output/` bị gitignore nên checkout sạch trên CI không có gì để đọc. Báo PASS ở đó là biến *thiếu bằng chứng* thành *khẳng định khoẻ mạnh*.
* **Không tới được validator → `schema_violation_rate = None`.** Không có mẫu số thì không được chấm 0% sạch sẽ.

Kết quả trên repo hiện tại:

| Chỉ số                       | Giá trị                                                 |
| ------------------------------ | --------------------------------------------------------- |
| `latest_run_produced_output` | **False** — `30ab0d5d6ced`: EMPTY_OUTPUT         |
| `empty_run_rate`             | **0.80** — 4/5 lần chạy gần nhất không ra gì |
| `schema_violation_rate`      | **1.00** — 100% bị validator từ chối            |

Nó còn bắt được một lần chạy `48dec0276443` **DIED_EARLY** mà rà tay đã bỏ sót.

**Điều này gỡ `deferred` cho HG-A2.** Tiền đề ghi trong `hard_gates.yaml` là *"cần một lần gọi agent thật"* — tiền đề đó nay đã được đáp ứng, và số lần từ chối đang nằm sẵn trong artefact.

#### 2 · `served_path_fidelity_v1` — "đường người dùng đi có phải đường đang chấm không?"

Mọi con số `ai_quality` trong báo cáo này đo LangGraph agent. Điều đó chỉ có nghĩa nếu agent là thứ trả lời request của người dùng. Nếu đường phục vụ trả output đóng hộp, báo cáo đang mô tả code không ai chạm tới, và **điểm cao sẽ là gây hiểu lầm chứ không chỉ là thiếu sót**.

Kiểm hai điều kiện độc lập, vì mỗi cái một mình đã đủ làm đường phục vụ thành giả:

```text
served_path_is_mocked           = True   (effective mode: mock)
  ├─ AGENT_MODE không được set trong docker-compose.yml / .env
  ├─ rơi về default trong code tại src/config.py:45
  └─ 1 nhánh short-circuit tại src/services/dashboard_agent_workflow.py:349

llm_credential_reaches_service  = False
  └─ OPENAI_API_KEY có trong .env nhưng KHÔNG được truyền vào service
```

`- AGENT_MODE` trần trong compose (không có `=`) được ghi nhận là **pass-through**, không phải một khai báo — nó tự nó không chọn gì cả.

Một lưu ý về mức độ: `HG-G5` (mocked) **chặn release**; còn thiếu credential thì báo dưới id `CRED-UNSEEN` và **không bao giờ chặn**. Evaluator đọc được các file config trong repo này, nhưng **không nhìn thấy secret manager hay biến do CI tiêm vào** — nên vắng mặt ở đây không phải bằng chứng vắng mặt lúc deploy. Đây là tín hiệu để kiểm tra, không phải sự thật đã xác lập.

#### 3 · `default_credential_probe_v1` — gỡ gate mồ côi HG-S7

`HG-S7` tồn tại trong policy từ v3 và báo `NOT_EVALUATED` **mọi lần chạy**, vì không ai sinh `default_credentials_active`. Chính file policy đã viết ra quy tắc mà nó đang vi phạm: *"một gate báo NOT_EVALUATED mãi mãi thì không phải là độ phủ — nó trông như một chốt kiểm soát trong khi không bảo đảm điều gì."*

Probe tìm hai nửa, và **cả hai đều cần**: một routine seed tạo tài khoản có mật khẩu đoán được, và một call site chạy nó mà không hỏi đang ở môi trường nào. Tài khoản demo seed sẵn là tiện lợi hợp lý khi dev; rủi ro là việc seed chạy ở **mọi nơi `init_db()` chạy**, bao gồm production.

```text
user     | password equals username | src/services/session_service.py:17
steward  | password equals username | src/services/session_service.py:17
admin    | password equals username | src/services/session_service.py:17
call site: ensure_default_users  guarded=None  src/services/rule_store.py:305
```

Đã xác nhận sống: `POST /api/v1/session` với `steward`/`steward` trả **200** kèm session cookie trên stack đang chạy.

Chỉ phân tích tĩnh — không thử đăng nhập, không truyền mật khẩu, và credential được mô tả **theo hình dạng** (*"password equals username"*) chứ không theo giá trị, nên file evidence không bao giờ mang một credential dùng được.

> **Hai false positive tự bắt trong lúc xây.** Bản đầu đọc trường role `"ADMIN"` thành mật khẩu yếu; sửa xong lại đọc tên hiển thị `"Admin"` thành mật khẩu yếu. Cả hai sẽ gắn cờ **mọi** tài khoản admin seed sẵn bất kể mật khẩu mạnh đến đâu — đúng loại báo động giả dạy cả đội bỏ qua gate. Cả hai có test riêng khoá lại.

#### 4 · DEFECT-11 — báo động giả về regression

`current_gate_scores()` tính điểm gate bằng **trung bình các evaluator thuộc gate đó**. Nên khi thêm một evaluator, trung bình gate tụt xuống dù **không có gì tệ đi** — và về mặt số học nó **không phân biệt được** với một regression thật.

Cụ thể: baseline lưu governance = 27.67 (5 evaluator), lần chạy sau tính 13.89 (4 evaluator) → báo tụt 13.78 điểm và **chặn release**, trong khi governance thực chất *tăng*.

Sửa: so sánh **theo từng evaluator**, chỉ so những evaluator có mặt ở **cả hai** lần chạy.

```python
# trước — trung bình gate, đổi khi thành viên đổi
for gate, current in current_scores.items():
    previous = baseline_scores.get(gate)

# sau — từng evaluator, chỉ phần giao
compared = sorted(set(current_by_evaluator) & set(baseline_by_evaluator))
```

Trong lúc sửa lại lộ ra **một lỗi thứ hai cùng loại do chính bản sửa gây ra**: `baseline_evaluator_scores()` ban đầu đọc thẳng trường `score` đã lưu, trong khi phía hiện tại dùng `collapse_result_scores()` (P25). Với mọi evaluator đa-dataset, hai số đó khác nhau — nên nó **chế ra một cú tụt từ hư không mỗi lần chạy**. Đã sửa: dựng lại `EvalResult` từ dump và cho qua **đúng cùng một hàm** ở cả hai phía.

Id của finding cũng đổi từ `HG-R3` sang `REG-DROP`. `HG-R3` được khai trong `hard_gates.yaml` là *"một hard gate từng pass nay fail"* — mượn id đó cho một cú tụt điểm sẽ khiến báo cáo mô tả một gate failure mà policy chưa từng định nghĩa.

### 12.3 HG-S2 chuyển sang `deferred`

`HG-S2` (cross-tenant BOLA/BFLA) cũng là gate mồ côi. Khác với HG-S7, nó **không** được cấp producer, mà chuyển sang `deferred` kèm tiền đề.

Lý do: kiểm tra tĩnh không thể thay thế. Thiếu lời gọi `require_dataset_access` là bằng chứng **thiếu chốt chặn**, nhưng `cross_tenant_violations` khẳng định rằng truy cập **đã thực sự thành công** — và chỉ một request được thực thi mới xác lập được điều đó. Đặt một con số tĩnh vào chỗ đó là nói dối về loại bằng chứng đang có.

### 12.4 Kết quả

|                     | Trước         | Sau                       |
| ------------------- | --------------- | ------------------------- |
| Hard gate active    | 20 (2 mồ côi) | **19 (0 mồ côi)** |
| Hard gate fail      | 12              | **16**              |
| Evaluator (profile`ci`) | 15          | **18**              |
| Test                | 92              | **118**             |
| Score               | 28.03           | **23.88**           |
| `measured_weight` | 0.85            | 0.85                      |

Bốn gate fail mới — `HG-A2`, `HG-A7`, `HG-G5`, `HG-S7` — đều là lỗi **có thật, đã xác nhận sống**, trước đây không nhìn thấy. `HG-R3` biến mất khỏi danh sách vì báo động giả đã hết.

Điểm **giảm** 4.15 là kết quả đúng: nó không phản ánh sản phẩm tệ đi mà phản ánh **cổng đã hết mù**. Điểm 28.03 trước đây là quá cao — nó được tính trong khi bốn lỗi chặn release đang không được đo.

### 12.5 Giới hạn còn lại

* `run_outcome_integrity` phụ thuộc `output/` — bị gitignore, nên trên CI sạch nó trả `NOT_MEASURED` và bị loại khỏi tổng hợp kèm tái chuẩn hoá trọng số. Đây là hành vi đúng theo thiết kế, nhưng nghĩa là **gate mạnh nhất về "hệ thống có chạy không" lại yếu nhất đúng lúc ở CI**. Muốn có ở CI thì cần đẩy artefact lên như build artifact.
* `served_path_fidelity` chỉ đọc file trong repo. Nó **không** truy vấn `/api/v1/status` của instance đang chạy — điều đó sẽ cần một target sống, và cả profile `ci` được thiết kế là $0 và offline.
* `HG-S2` vẫn chưa được đo. Tiền đề đã ghi trong `hard_gates.yaml`.

---

## PHỤ LỤC — TRẠNG THÁI

```text
Ngày:                          2026-08-22
Nhánh:                         chien @ 31e065a
Self-test:                     118/118 xanh
Golden tier 1:                 7/7 archetype, 5.764 nhan, KIEM CHUNG NGU NGHIA dat
File sản phẩm (src/) bị sửa:   KHÔNG
File bị xoá:                   eval/results/report.md, eval/results/E1_E5_EVALUATION.md,
                               thư mục eval/   (đã được người dùng phê duyệt)
File docs bị sửa:              README.md (link), docs/guide/deliverables/checklist.md (link)
File docs được thêm:           docs/EVAL_EVIDENCES_E1_E5.md (chuyển từ eval/)
Dependency cài thêm:           KHÔNG
Chi phí LLM:                   $0.00
git add / commit / push:       KHÔNG THỰC HIỆN
steward_local.db:              checksum không đổi (đã kiểm trước/sau)
ruff evalgate/:                3 lỗi có sẵn từ trước, 0 lỗi mới
Mục 10 (PLAN):                 PROPOSED — chưa thực hiện
```
