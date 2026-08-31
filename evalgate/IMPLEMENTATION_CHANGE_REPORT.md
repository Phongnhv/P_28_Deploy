# EVALGATE — IMPLEMENTATION CHANGE REPORT

## Trạng thái

- Mục tiêu: nâng EvalGate từ report harness lên release gate fail-closed.
- Revision bắt đầu: `5ecbc45`.
- Git push/commit: không thực hiện.
- Paid model/load test: không thực hiện trong phiên triển khai cục bộ.
- Thay đổi có sẵn `.ai-log/session.jsonl`: được bảo toàn, không tính là thay đổi của implementation này.

## Added

| File | Nội dung |
|---|---|
| `evalgate/core/evaluator_registry.py` | Registry evaluator khai báo tập trung; profile không được tham chiếu evaluator vô danh. |
| `evalgate/core/artifact_provenance.py` | Xác minh manifest, Git SHA, clean-workspace, path containment và SHA-256. |
| `evalgate/core/suppression_policy.py` | Ratchet suppression có owner/ticket/TTL và danh sách non-suppressible. |
| `evalgate/schemas/artifact_manifest.py` | Pydantic contract đóng cho product-run evidence. |
| `evalgate/policies/evaluation_policy.yaml` | Policy version, coverage floor, score weights và runtime budgets. |
| `evalgate/policies/suppressions.yaml` | Registry suppression; mặc định rỗng, không che backlog. |
| `evalgate/policies/approved_baseline.yaml` | Metadata baseline được phê duyệt; trạng thái hiện tại `bootstrap_required`. |
| `evalgate/product_run.py` | Sinh bundle kiểm chứng 7 frozen corpus và provenance manifest. |
| `evalgate/gates/gate1_ai_quality/live_agent_e2e.py` | Adapter kết quả live SDIH/GEval; thiếu kết quả báo NOT_EXECUTED. |
| `evalgate/gates/gate2_security/upload_behaviour_probe.py` | Adapter adversarial upload; accepted case tạo HG-S4. |
| `evalgate/gates/gate2_security/prompt_injection_probe.py` | Promptfoo result adapter fail-closed. |
| `evalgate/gates/gate3_observability/trace_coverage.py` | Trace completeness và critical-node error metrics. |
| `evalgate/gates/gate5_reliability/load_slo.py` | k6 p95/error-rate result adapter. |
| `evalgate/gates/gate7_business/steward_outcome.py` | Aggregate steward acceptance/edit metrics có sample floor. |
| `src/services/eval_telemetry.py` | Opt-in LLM telemetry: hash prompt, token, latency, error type; không lưu nội dung. |
| `.github/workflows/evalgate-nightly.yml` | Nightly live profile, budget 5 USD. |
| `.github/workflows/evalgate-pre-release.yml` | Pre-release profile và approved target, budget 10 USD. |
| `evalgate/gates/gate3_observability/__init__.py` | Khai báo package observability evaluator. |
| `evalgate/gates/gate7_business/__init__.py` | Khai báo package business evaluator. |
| `evalgate/tests/test_production_upgrade.py` | Provenance, collision, suppression, secret và row-level acceptance tests. |
| `evalgate/tests/test_live_adapters.py` | Fail-closed/live-adapter contract tests. |
| `evalgate/mutations/catalog.yaml` | Danh mục 10 mutation CRITICAL và detector test tương ứng. |
| `evalgate/tests/test_mutation_catalog.py` | Kiểm tra mọi mutation CRITICAL có detector thực thi được; probe bắt transition thiếu audit. |
| `evalgate/IMPLEMENTATION_CHANGE_REPORT.md` | Báo cáo Added/Modified/Deleted, verification, residual risk và rollback. |

## Modified

| File | Thay đổi |
|---|---|
| `.env.local.example` | Xóa credential có hình dạng thật, thay bằng placeholder. Credential cũ vẫn phải revoke/rotate ngoài Git. |
| `.github/workflows/ci.yml` | Bỏ `continue-on-error` ở verdict; tạo/verify corpus bundle cùng workflow. |
| `evalgate/run.py` | Registry validation, nightly mode, provenance, ratchet, approved baseline và budget enforcement. |
| `evalgate/aggregator.py` | Central score policy; metric collision/config error trả EVALGATE_INVALID exit 6. |
| `evalgate/core/regression_engine.py` | Evaluator removal là blocking regression; không so baseline khác policy/schema/corpus version; chỉ PASS/WARNING mới đủ điều kiện làm baseline. |
| `evalgate/gates/gate1_ai_quality/replay_evaluator.py` | TP/FP/FN theo row ID; count-only artifact thành NOT_MEASURED; status không còn luôn FAIL. |
| `evalgate/gates/gate2_security/secret_scan.py` | Không miễn trừ `.example`/docs theo tên file; vẫn redacted evidence. |
| `evalgate/reports/renderer.py` | Report thêm contract version, provenance, collision và ratchet findings. |
| `evalgate/config/profiles.yaml` | Tách ci/nightly/pre-release; registry là nguồn duy nhất của profile membership. |
| `evalgate/policies/hard_gates.yaml` | Bật upload/prompt-injection gates và đồng bộ version 6.0. |
| `evalgate/policies/weights.yaml` | Chuyển thành compatibility marker; runtime chỉ đọc `evaluation_policy.yaml` để loại bỏ nguồn score thứ hai. |
| `evalgate/pyproject.toml` | Thêm optional `live` dependency group. |
| `evalgate/schemas/eval_result.py` | STALE_EVIDENCE và evaluation contract version fields. |
| `evalgate/tests/test_phase_a.py` | Cập nhật contract regression: evaluator removal phải block và baseline có đủ version. |
| `src/services/llm.py` | Provider type gồm Google và gắn aggregate-only telemetry callback. |
| `src/services/node_event_stream.py` | Trace identity/timestamp/error taxonomy; redaction raw rows/sample failures. |
| `tests/test_node_event_stream.py` | Kiểm tra trace fields và redaction raw rows. |
| `evalgate/EVALGATE_REPORT.md` | Đánh dấu điểm số cũ là historical. |

## Deleted

| File | Lý do |
|---|---|
| `evalgate/policies/thresholds.yaml` | Tránh hai nguồn policy; score policy chuyển sang `evaluation_policy.yaml`. |
| `evalgate/deepeval.md` | Thiết kế v1 trỏ tới framework `eval/` không tồn tại. |
| `evalgate/implementation_plan.md` | Plan read-only v1 đã lỗi thời sau khi production architecture được triển khai. |

## Verification đã thực hiện

| Lệnh | Kết quả |
|---|---|
| `python -m compileall -q evalgate src/services` | PASS |
| `pytest evalgate/tests/ tests/test_node_event_stream.py` | **214 passed, 1 skipped**. |
| `ruff check evalgate/ ...` | PASS. |
| `python -m evalgate.product_run ...` | PASS; manifest checksum hợp lệ. |
| `python -m evalgate.run --mode local --dry-run --allow-dirty --manifest <exact-path>` | Harness chạy end-to-end; advisory RELEASE_BLOCKED, score withheld do coverage 0.38; hard gates HG-S6/HG-A6/HG-S8/HG-G4. |
| Secret scan read-only | **FAIL đúng thiết kế**, 3 credential-shaped finding trên 462 tracked files: `.ai-log/session.jsonl`, `README.md`, `docs/guide/chapter-09.md`; report không chứa giá trị. |
| YAML parse cho workflow/config/policy/mutation catalog | PASS, 11 file. |
| Full `pytest tests/ --maxfail=5` | 125 pass, 2 skip, 5 fail trong `test_dashboard_agent_workflow.py`; failure thuộc candidate-count/order của source hiện có, không phát sinh từ EvalGate/telemetry. |

## Chưa được tuyên bố hoàn tất

- `product_run.py` hiện xác minh cả 7 generator/frozen corpus và provenance, chưa gọi đầy đủ ingest→LLM→approve→execute cho 7 corpus.
- Promptfoo, DeepEval và k6 adapter đã fail-closed nhưng chưa có paid/live execution trong phiên này.
- Nightly workflow cần bước producer tạo các file result trước khi adapter có measurement.
- Business gate cần aggregate export thật từ tối thiểu 3 dataset và 20 proposal.
- Approved known-good baseline run ID chưa được bootstrap trên CI.
- Giá model không hard-code; nightly/pre-release phải cấu hình `EVAL_LLM_INPUT_USD_PER_MILLION` và `EVAL_LLM_OUTPUT_USD_PER_MILLION`, nếu không estimated cost mặc định bằng 0.
- Editable install đã sửa package discovery, nhưng local verification không thể hoàn tất vì virtualenv thiếu `wheel` và sandbox không truy cập PyPI; CI có network phải xác minh bước này.
- Product suite còn 5 failure có sẵn ở candidate diversity/count/order; CI sẽ tiếp tục đỏ cho tới khi product branch đồng bộ source và test.
- Mutation catalog hiện map 10/10 tình huống CRITICAL sang detector test chạy được; đây là contract mutation coverage, chưa phải engine tự patch source rồi hoàn nguyên repository.
- HG-S6 hiện cố ý block release vì còn 3 credential-shaped value trong file tracked. `.ai-log/session.jsonl` là thay đổi có sẵn của người dùng và không được sửa trong phiên này; cần rotate/revoke rồi sanitize lịch sử bằng quy trình riêng.
- Token budget riêng chưa có producer đáng tin; cost/time budget đã fail-closed qua result cost và workflow timeout, nhưng live producer còn phải xuất token count để gate token budget.

## Rollback

- Có thể tắt nightly/pre-release workflow độc lập mà không ảnh hưởng merge gate.
- Có thể bỏ `--manifest` để chạy local diagnostic, nhưng CI phải truyền chính xác manifest đã finalize.
- Không khôi phục credential cũ hoặc exemption `.example`.
- Không khôi phục `continue-on-error` cho EvalGate verdict.
