# Dataset/source routing — báo cáo sửa và kiểm thử

Ngày: 2026-09-03. Branch: `codex/test-dataset-profiler`.
Không commit, push, deploy hoặc ghi dữ liệu cloud. Các dataset kiểm thử được tạo trong SQLite local riêng.

## Nguyên nhân và thay đổi

1. **Nguồn mặc định quá rộng.** `src/agents/nodes/profiler_node.py:145` trước đây tự ưu tiên `source_rows` khi thiếu target/profile; DB tool không lọc dataset/version. Nay thiếu target bị từ chối; cả node và tool từ chối profile bảng dùng chung `source_rows`. Không thêm nhánh import toàn bộ dataset vào bảng này. CLI proposal dùng resolver versioned, không còn chọn dataset legacy mặc định.
2. **Không cố định version/profile.** `src/services/rule_proposer_workflow.py:263` trước đây ưu tiên `ProfileModel` legacy, sau đó mới tìm version mới nhất. Resolver mới ở `src/services/source_binding.py:13` kiểm tra dataset/version/workspace/artifact/checksum/profile. Binding được lưu ở stage UPLOAD_PROFILE trong `steps_json`, không cần thêm cột DB. Workflow versioned cũ không có binding phải khởi tạo lượt mới; không đoán nguồn để resume.
3. **Sai lineage artifact.** Artifact trước đây ghi `manifest_version` (ví dụ `versioned-v1`) vào `dataset_version_id`. Nay ghi ID version thật, profile ID và binding của workflow. Graph 1A/1B đọc snapshot cố định; Graph 2 materialize version đó; Graph 3 kiểm tra execution/workflow và ràng buộc resource ID ở các investigation tool. Không bỏ node/tool, không đổi model/timeout/validator.
4. **Tái sử dụng profile cũ.** Continue và nút Profile dataset cho dataset versioned nay tạo job `WORKFLOW_PROFILE`, profile snapshot mới theo workflow. Retry cùng workflow tái sử dụng snapshot của chính lượt đó; workflow mới giữ thêm snapshot, không xóa lịch sử. Endpoint ingestion legacy từ chối dataset versioned. Dictionary/semantic lịch sử không bị xóa.
5. **Rủi ro React state cũ.** `frontend/src/App.tsx:2831` truyền dataset ID tường minh; có khóa đồng bộ chống bấm lặp, generation guard và kiểm tra version. Trạng thái đang chuẩn bị được hiển thị; chọn dataset bị chặn trong lúc thao tác/job đang chạy. Khi refresh, profile được lấy theo binding của workflow.
6. **Source artifact cloud.** Truy vấn metadata PostgreSQL ở chế độ READ ONLY xác nhận dataset B thật (`dataset-import-151ff2348b12478793ac`, version `dv-89c39fe704244492859879ba`) dùng locator `local:` và không có bucket. Đây không phải bằng chứng session đã delete data; nó chứng minh nguồn đang tham chiếu filesystem local, không phải nguồn object storage bền vững. Kết hợp lỗi Explorer đã ghi nhận, file đó không truy cập được trên instance xử lý request. Code mới không cho Cloud Run lưu/đọc nguồn local, kể cả khi APP_ENV bị cấu hình development; yêu cầu object storage. **Chưa khôi phục artifact B cũ** vì không được phép ghi cloud.
7. **Lỗi bị gắn nhầm CSRF.** HTTP 422 thông thường trả VALIDATION_ERROR; Explorer trả SOURCE_ARTIFACT_MISSING / SOURCE_INTEGRITY_ERROR theo lỗi nguồn, không làm giao diện hiểu nhầm phiên đăng nhập hết hạn.
8. **Semantic mapping.** Adapter snapshot đọc được cả `min/max` và `min_value/max_value`. Graph1 Studio lấy metric thực từ `metrics_json` thay vì default của schema; null/negative rate giữ dạng tỷ lệ 0–1 đúng contract của digest.

## Kiểm thử tự động

Bộ kiểm thử chọn lọc: **102 passed**, 1 warning có sẵn về Pydantic field `schema`.
Ruff trên file thay đổi: pass. Frontend TypeScript/Vite build: pass (còn cảnh báo chunk >500 kB). `git diff --check`: pass.

Bao gồm:

- 3 CSV: A taxi 3 dòng, B customers 2 dòng, C stock 4 dòng. Kiểm tra schema, count, null rate B = 0.5, min stock = 0 và NOT_NULL failed_count tương ứng 0/1/0.
- Shared `source_rows` chỉ có A nhưng run B: không gọi SQL profiler toàn bảng; tool trả lỗi scope rõ ràng.
- Profile legacy 999 dòng và version v2 cùng tồn tại: workflow B/v1 vẫn dùng 2 dòng của v1.
- File thiếu, sai size, sai checksum: fail, không fallback nguồn khác.
- CSV chỉ có header: lưu profile thật 0 dòng, sau đó báo EMPTY_DATASET; không gọi agent để suy diễn trên nguồn rỗng.
- API create/replay preparation, profile job replay, từ chối resume sai dataset/version và key dùng cho version khác.
- Profile mới giữ lịch sử; retry không tạo snapshot thứ hai cho cùng workflow.
- Graph 3 profile tool trả đúng profile workflow, từ chối dataset khác, giữ đủ inventory 5 tool.
- Contract test artifact cho cả UNDERSTAND_DATA, PROPOSE_RULES, RUN_CHECKS và ANALYZE_REPORT giữ cùng binding. Đây là test lineage, không phải bằng chứng LLM end-to-end.

Lệnh tái chạy (dùng một basetemp mới, không tái sử dụng thư mục có dữ liệu):

```powershell
$env:LANGSMITH_TRACING='false'
$env:LANGCHAIN_TRACING_V2='false'
.venv-e2e/Scripts/python.exe -m pytest tests/unit/test_source_binding.py tests/unit/test_versioned_profile_workflow_gate.py tests/unit/test_graph2_versioned_execution.py tests/test_graph1_workflow.py tests/test_analysis_workflow.py tests/test_dashboard_agent_workflow.py tests/test_agents/test_graph.py tests/test_agents/test_proposal_run_status.py tests/test_agents/test_profiler_node.py tests/test_versioned_upload_idempotency.py tests/test_versioned_dataset_contract.py tests/test_services/test_rule_proposer_workflow.py tests/test_job_dispatch_contract.py tests/test_data_access_api.py -q --basetemp=.pytest-routing-new-run
```

## Browser Harness — API thật, nguồn local, không LLM

UI `http://127.0.0.1:5179`, API `http://127.0.0.1:8019`, DB `.pytest-routing-browser-20260903.db`. APP_ENV=test, AGENT_MODE=mock; credentials provider và tracing bị tắt trong tiến trình test. Không thay `.env`.

| Dataset | Version | Workflow | Profile mới | Rows |
|---|---|---|---|---:|
| Routing B Customers | dv-e27d72092d004106a22d744f | workflow-33652a549daf5c3a995490f5af7298f0 | profile-workflow-33652a549daf5c3a995490f5af7298f0 | 2 |
| Routing A Taxi | dv-8ed0780975b14f52b82660ab | workflow-4c2b3dbe3c8ded715f0b08f0a374931c | profile-workflow-4c2b3dbe3c8ded715f0b08f0a374931c | 3 |
| Routing C Stock | dv-3ac669dbfad64f0299b8e4af | workflow-caeddc900c4a7b52166589ee7ae9c271 | profile-workflow-caeddc900c4a7b52166589ee7ae9c271 | 4 |

- Upload qua API local rồi chọn lại từng card trong Browser Harness: Continue tạo snapshot mới từ file, chuyển sang Graph 1A. `source_rows` vẫn có **0 dòng**.
- Refresh B giữ đúng lựa chọn/workflow. Chạy bước understanding B tạo profile/semantic artifact đúng B, 2 dòng, hai cột `customer_id,amount`, cùng version/profile ID.
- Bấm Continue hai lần ở A chỉ có một workflow/profile mới.
- Không tạo/xóa dữ liệu cloud; local import profile gốc vẫn còn bên cạnh profile workflow mới.
- Recording: `C:/Users/ADMIN/.config/browser-harness/agent-workspace/recordings/source-routing-local` (36 frames).

## Giới hạn và việc còn lại

- **Chưa kiểm thử Browser end-to-end Graph 1A → 1B → 2 → 3 với LLM thật trên 2–3 dataset.** Browser đã kiểm chứng preparation/refresh/double-click và understanding B ở chế độ deterministic. Không dùng kết quả này để khẳng định Luna hết timeout/fallback hoặc toàn bộ node LLM đã chạy.
- Chưa deploy nên cloud hiện tại chưa nhận bản sửa. Artifact local cũ phải được khôi phục sang object storage từ đúng file/checksum, với quyền ghi cloud riêng; không sửa metadata để trỏ tùy tiện sang file khác.
- Chưa stress-test concurrency nhiều process hoặc restart worker giữa transaction. Đã có khóa UI, khóa ID workflow/idempotency và kiểm thử replay, nhưng không đồng nghĩa đã chứng minh mọi race phân tán.
- Các chỉnh sửa dùng skill UI/UX theo hướng trạng thái loading/disabled rõ ràng; không redesign giao diện. Bộ dữ liệu hướng dẫn React của skill bị thiếu nên không sử dụng kết quả tìm kiếm đó như bằng chứng.
