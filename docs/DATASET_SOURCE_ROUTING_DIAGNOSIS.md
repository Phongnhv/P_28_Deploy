# Dataset/source routing diagnosis

Ngày kiểm tra: 2026-09-03 (Asia/Bangkok)

## 1. Phạm vi và kết luận ngắn

Mục tiêu là xác định mốc đầu tiên dataset/version/source không còn khớp lựa chọn của người dùng. Lượt wizard có kiểm soát với `e2e_test_datapulse` (B) **không tái hiện** việc gửi nhầm dataset hoặc đọc `source_rows`: ID B được giữ từ UI đến workflow, node input và profile digest.

Hiện tượng `source_rows` được xác nhận trong **nhánh profiler DB tương thích ngược/CLI**, không phải trong lượt wizard B vừa theo dấu. Mốc lệch đầu tiên của nhánh này là khi `raw_profiler_node` không nhận `uploaded_dataset_profile` và không có `target_tables`; code khi đó chủ động ưu tiên bảng tên `source_rows`. Sau đó `db_profiler_tool` profile toàn bộ bảng được truyền vào và không có tham số `dataset_id`/`dataset_version_id`, nên có nguy cơ đọc dữ liệu của dataset khác rồi chỉ gắn nhãn bằng dataset đang chạy.

Vì vậy:

- Wizard B: chưa thấy dataset routing mismatch; Graph 1A đã nhận đúng B nhưng lượt này còn đứng ở node LLM `data_dictionary_generator`, nên chưa chạy Graph 1B–3.
- CLI/A-B trước đó: đang so sánh entrypoint khác với wizard; H5 được xác nhận. Lượt CLI đi qua durable/legacy-compatible path và có thể rơi vào `source_rows`.
- Nếu một run fallback có `dataset_id=B` nhưng `source_rows` chứa các dòng của A, nguy cơ “đọc A nhưng gắn nhãn B” là có thật trong nhánh đó. Lượt wizard B không cho thấy nguy cơ này.

Không sửa code/config, không import/xóa dữ liệu và không push trong đợt chẩn đoán này. Chỉ artefact báo cáo này được tạo.

## 2. Đối chứng môi trường

| Hạng mục | Giá trị ghi nhận |
|---|---|
| Branch/commit local | `codex/test-dataset-profiler` / `a3441c607b610e347d307fe428c2736f408802b2` |
| Frontend | [c3-app-028.vercel.app](https://c3-app-028.vercel.app/) |
| Frontend fingerprint | HTTP `ETag=fabfd973e51d0a184303e18d987a893c`; public response không cung cấp commit Vercel nên không suy đoán commit |
| Backend | [ridepulse-api-gbnhdahaya-as.a.run.app](https://ridepulse-api-gbnhdahaya-as.a.run.app) |
| API revision | `ridepulse-api-00057-r2d` |
| Worker | Cloud Run Job `ridepulse-worker`, command `python -m src.worker` |
| Worker image | `asia-southeast1-docker.pkg.dev/asignmentvinuni/ridepulse/ridepulse@sha256:8007831292caacccac4a8b1573ed06a50962e3ae023cf75c8390e5189636940c` |
| Runtime flags quan sát | `AGENT_MODE=graph`, `ANOMALY_INVESTIGATION_MODE=deepagent`, `SEED_LEGACY_DEMO_DATASET=false`; LangSmith input/output được ẩn |

Branch hiện tại không phải branch `deploy`; đây là trạng thái checkout được ghi nhận, không tự ý đổi branch.

## 3. Dataset đối chứng

Thông tin lấy qua API/Supabase read-only; không in connection string, key hay dữ liệu dòng.

| Dataset | ID | Version | Rows | Columns thực tế |
|---|---|---:|---:|---|
| A — NYC Yellow Taxi Semantic 10k | `dataset-import-06fe17bbf0a64165849f` | `dv-6330e69e9a594d398010b2cd`, v1, `versioned-v1` | 10,000 | `vendor_id`, `pickup_at`, `dropoff_at`, `passenger_count`, `trip_distance`, `rate_code_id`, `store_and_fwd_flag`, `pickup_location_id`, `dropoff_location_id`, `payment_type`, `fare_amount`, `extra`, `mta_tax`, `tip_amount`, `tolls_amount`, `improvement_surcharge`, `total_amount`, `congestion_surcharge`, `airport_fee`, `cbd_congestion_fee` |
| B — e2e_test_datapulse | `dataset-import-151ff2348b12478793ac` | `dv-89c39fe704244492859879ba`, v1, `versioned-v1` | 12 | `customer_id`, `email`, `age`, `amount`, `signup_date`, `status` |

Profile run của A/B đều `COMPLETED`, lần lượt 20/6 cột. Truy vấn read-only `source_rows GROUP BY dataset_id` tại thời điểm kiểm tra trả về:

| dataset_id trong `source_rows` | count |
|---|---:|
| `dataset-import-06fe17bbf0a64165849f` (A) | 10,000 |

B không có dòng `source_rows` trong kết quả. 10,000 dòng A là trạng thái dữ liệu đã tồn tại từ thử nghiệm trước; lượt chẩn đoán này không import thêm.

## 4. Trace wizard với dataset B

Workflow ID `workflow-ce20e7ec27094f0f8e4b`; job ID `e8521fa1-b153-4a68-81e8-7209c37f51dc`.

| Mốc | ID/version kỳ vọng | ID/version thực tế | Nguồn | Bằng chứng | Kết luận |
|---|---|---|---|---|---|
| Chọn thẻ | B / v1 | B / v1 | UI + `sessionStorage` | Card active là `e2e_test_datapulse`; `ridepulse.dataset` là ID B | Khớp |
| Bấm “Tiếp tục” | Chỉ chuyển sang Graph 1A, sau đó workflow phải là B | `POST /api/v1/datasets/<B>/workflows?fresh=true` | Browser network/performance entries | Request path chứa đúng ID B | Khớp |
| Bắt đầu Graph 1A | workflow B, `UNDERSTAND_DATA` | `POST /api/v1/workflows/<workflow-ce20...>/steps/UNDERSTAND_DATA`; job B | API response | Workflow response có `dataset_id=B`, step RUNNING | Khớp |
| Worker/node | run B, không dùng source của A | `build_profile_digest` SUCCEEDED; `data_dictionary_generator` RUNNING; cả hai có workflow B và `dataset_id=B` | `GET /api/v1/graph/node-runs` và node detail | Node 1 input có profile keyed B, target table `[B]` | Khớp |
| Graph input | profile B, rows 12, 6 cột | `dataset_profile` keyed B; `target_tables=[B]`; digest table B, rows 12, 6 cột | Node detail `nr-8fb...`/`nr-014...` | Không có dấu hiệu `source_rows` | Khớp |
| Profiler source | versioned profile/snapshot B | Canonical workflow dùng `ProfileRunSnapshot` cùng version B rồi dựng digest aggregate; không đưa source row vào LLM | `rule_proposer_workflow.py` + node input | Snapshot/profile đều cùng dataset/version | Khớp |
| Graph 1B | cùng B/v1 | Chưa chạy vì Graph 1A còn RUNNING | Job state | Không tạo job trùng | Chưa kiểm chứng |

Một request riêng cho Data Explorer B trả `CSRF_INVALID` với message `Source artifact is missing`. Đây là lỗi resolve source artifact của endpoint explorer tại thời điểm kiểm tra, cần tách khỏi kết luận profiler routing; metadata dataset/version/profile của B vẫn tồn tại và profile đã hoàn tất.

### Mốc đầu tiên bị lệch

Trong lượt wizard B: **không có mốc lệch nào được quan sát** trước khi lượt dừng ở LLM.

Trong nhánh CLI/fallback có hiện tượng `source_rows`: mốc đầu tiên là lựa chọn bảng tại `src/agents/nodes/profiler_node.py:134-145`, cụ thể nhánh `target_tables` rỗng và `source_rows` tồn tại. Đây xảy ra trước khi profile SQL chạy. Nếu nhánh này được gọi cho B, dataset ID chưa được dùng để lọc các dòng trong `source_rows`.

## 5. Ma trận kiểm thử tối thiểu

| Test | Trạng thái | Kết quả |
|---|---|---|
| T1. Chọn B từ danh sách upload → Tiếp tục → Graph 1A | Đã chạy một lượt | Routing pass: B xuyên suốt UI → API → worker → digest. Runtime chưa hoàn tất do node LLM còn RUNNING |
| T2. Đổi A → B khi không có job | Chưa chạy độc lập | Không tạo thêm job để tránh nhiễu. Code cho thấy selection ghi ID mới và reset workflow; riêng callback `onStartUnderstand` có rủi ro stale React closure khi vừa đổi ID vừa start |
| T3. Refresh khi đang chọn B | Chưa chạy độc lập | Browser ban đầu đã restore ID B từ `sessionStorage`; code có đọc/ghi `ridepulse.dataset`. Chưa refresh lại trong lúc job active để không làm nhiễu trace hiện tại |
| T4. So sánh wizard với CLI/A-B | Đã đối chiếu code và trace trước đó | Khác entrypoint: wizard durable versioned workflow; CLI `src.agents.graph` dùng durable/legacy-compatible runner và có default legacy dataset |

## 6. Kiểm thử routing offline, không gọi LLM/DB cloud

Dùng SQLite tạm với 3 dòng trong `source_rows` (2 dòng A, 1 dòng B) và một bảng riêng đại diện cho B. Chỉ ghi count/table key.

| Case | Kết quả |
|---|---|
| `db_profiler_tool.profile_database(table_name="source_rows")` | Profile `source_rows`, `total_rows=3`; không có input dataset ID nên không thể lọc riêng B |
| `raw_profiler_node` có `target_tables=[B-table]` | Chọn đúng bảng B |
| `raw_profiler_node` không có `target_tables`, không có uploaded profile | Chọn `source_rows` theo nhánh tương thích ngược |
| Có `metadata.uploaded_dataset_profile` của B | Trả profile upload trực tiếp, không quét DB |
| `dataset_id=B` nhưng `target_tables=["source_rows"]` | Vẫn profile toàn `source_rows`; ID B chỉ nằm trong state, không trở thành SQL filter |

Kết quả chứng minh điều kiện chọn `source_rows` là thiếu target/profile hoặc truyền đích danh `source_rows`; không phải do `source_rows` rỗng. Đồng thời chứng minh `db_profiler_tool` không có cơ chế phân biệt dòng A/B trong cùng bảng.

## 7. Đánh giá H1–H5

| Giả thuyết | Đánh giá trong phạm vi kiểm tra | Bằng chứng |
|---|---|---|
| H1 — Frontend giữ ID cũ/gửi sai ID | **Bác bỏ cho T1; còn rủi ro chưa tái hiện** | `App.tsx:2325-2345` ghi ID mới và reset state; request wizard chứa B. Tuy nhiên `App.tsx:3414-3417` gọi `selectDataset(id)` rồi lập tức gọi `startWorkflowStep(...)`, nên cần regression test cho thao tác start ngay trong card khác |
| H2 — API nhận đúng nhưng resume workflow dataset khác | **Bác bỏ cho T1** | API tạo workflow mới với B; response và node runs đều cùng workflow/dataset B |
| H3 — ID đúng nhưng không resolve profile/version nên profiler mặc định `source_rows` | **Đúng ở fallback path; bác bỏ cho canonical versioned wizard B** | `profiler_node.py:63-71` bypass bằng uploaded profile; `:134-145` fallback `source_rows`. Canonical `rule_proposer_workflow.py:256-264, 721-727` lấy snapshot cùng dataset/version |
| H4 — Profile cache/snapshot cũ/khác dataset | **Bác bỏ cho T1** | A/B có version/profile riêng; node input và digest của B đều keyed B, rows 12, 6 cột |
| H5 — CLI/A-B gọi entrypoint khác wizard | **Xác nhận** | `src/agents/graph.py:471-474, 477+` có CLI default legacy `dataset-nyc-yellow-taxi-50k` và durable compatibility runner; wizard gọi API workflow versioned |

## 8. Phân loại lỗi và nguy cơ

### Lỗi kỹ thuật đã xác nhận

1. **Profiler fallback chọn nguồn quá rộng — P1 trong legacy/fallback path.** Điều kiện `target_tables` rỗng ưu tiên bảng `source_rows` (`src/agents/nodes/profiler_node.py:134-145`). Đây là fallback chọn nguồn, không phải fallback LLM.
2. **DB profiler không có dataset/version scope — P0 nếu path này được dùng cho versioned dataset.** `profile_database` chỉ nhận `connection_string`, `table_name`, sampling (`src/agents/tools/db_profiler_tool.py:189-251`), rồi `SELECT COUNT(*) FROM "<table>"`. Không có `dataset_id`/`dataset_version_id` filter.
3. **CLI và wizard không cùng entrypoint — P1 về testability/observability.** CLI có default legacy dataset (`src/agents/graph.py:474`) và auto-compatible path; kết quả CLI không thể dùng làm bằng chứng trực tiếp cho wizard.

### Rủi ro chưa tái hiện, cần regression

- Callback Graph 1A có thể dùng closure `dataset` cũ nếu người dùng đổi card và start trong cùng interaction (`frontend/src/App.tsx:3414-3417`). Đây là rủi ro H1/H2, chưa xảy ra trong T1.
- Có thể gắn nhãn B cho profile toàn bảng `source_rows` nếu caller truyền `dataset_id=B` nhưng không truyền target/filter. Với trạng thái hiện tại `source_rows` chỉ chứa A, nguy cơ đọc A nhưng ghi nhãn B là có về mặt code; chưa thấy xảy ra trong wizard B.
- Data Explorer B báo thiếu source artifact; chưa đủ bằng chứng để quy kết đây là cùng nguyên nhân với `source_rows` fallback.

### Không nên gộp các fallback

- `source_rows` fallback: chọn nguồn dữ liệu DB khi thiếu target/profile.
- LLM legacy one-shot fallback: thay đường chạy Agent bằng cơ chế LLM tương thích cũ.
- heuristic/policy fallback: sinh kết quả deterministic khi Agent/validator thất bại.

Ba cơ chế trên độc lập; node SUCCEEDED hoặc có rule output không chứng minh routing đúng.

## 9. Kế hoạch sửa đề xuất (chưa thực hiện)

### P0 — Chặn đọc sai dataset

- Với `manifest_version=versioned-v1`, bắt buộc input có `dataset_id`, `dataset_version_id` và profile/snapshot cùng cặp khóa; thiếu hoặc mismatch thì fail rõ ràng, không rơi vào `source_rows`.
- Mở rộng contract profiler/tool để mọi truy vấn nguồn có dataset/version scope; nếu bảng không hỗ trợ scope thì không cho dùng nó cho versioned dataset.
- Ghi telemetry an toàn: `dataset_id`, `dataset_version_id`, `source_kind`, `source_table`, `source_filter_applied`, `fallback_kind`; không ghi secret/raw rows.

Regression: B có `source_rows` chứa A nhưng workflow B phải fail hoặc đọc artifact B, tuyệt đối không trả profile toàn `source_rows`; kiểm tra cả `table_key`, rows và columns.

### P1 — Đồng nhất wizard/CLI và sửa rủi ro state

- Dùng một resolver/entrypoint cho wizard và CLI; CLI phải truyền dataset/version/profile tường minh, không tự default legacy khi thiếu argument.
- Sửa callback start Graph 1A để dùng dataset ID vừa chọn làm tham số của cùng một request, không dựa vào React state closure vừa cập nhật.
- Khi resume workflow, API phải kiểm tra dataset ID trong URL, workflow row và state JSON trước khi chạy.

Regression: T2 đổi A → B rồi start ngay; refresh B; resume workflow A trong UI B phải bị từ chối; CLI và wizard cùng dataset phải có cùng source binding.

### P2 — Chất lượng test và báo cáo

- Thêm test SQLite cho 5 case routing ở mục 6.
- Thêm contract test kiểm tra `profile_source`/`report_source` và fallback reason riêng biệt.
- Trong UI/trace hiển thị dataset ID, version ID và source kind cạnh node; không chỉ hiển thị `SUCCEEDED`.

## 10. Giới hạn bằng chứng

- Chưa chạy Graph 1B–3 cho B vì Graph 1A đang RUNNING; không tạo job trùng.
- Chưa có cloud log payload/output đầy đủ do trace đã ẩn input/output; kết luận dựa trên API node detail, workflow state, code và SQLite offline.
- T1 không chứng minh mọi thao tác UI đều an toàn; nó chỉ bác bỏ mismatch cho đường đi cụ thể đã chạy. Các rủi ro callback và fallback cần regression sau khi có bản sửa.
