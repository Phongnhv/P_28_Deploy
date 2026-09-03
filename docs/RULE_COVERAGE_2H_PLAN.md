# Kế hoạch khôi phục độ bao phủ rule trong 2 giờ

## Mục tiêu và bằng chứng

Khôi phục khả năng đề xuất nhiều loại rule có căn cứ, giữ nguyên source binding dataset/version/profile/checksum và đường DeepAgent. Số rule là kết quả của evidence và semantic, không đặt mục tiêu cứng phải bằng 32.

Đã đọc context và kiểm tra trace production Taxi `01a066e4-9e5c-7210-ba8a-e5a7e611a46f`: checklist 16 NOT_NULL → candidates 16 NOT_NULL → model trả đủ 16 → lưu/thực thi 16. Trong lần này, mất coverage xảy ra trước LLM.

Phát hiện bổ sung: profile Taxi dùng `data_type=number`; nhánh RANGE tổng quát của checklist dashboard chỉ nhận numeric/float/integer/real. Thử lại trên snapshot đã lưu, chỉ đổi alias trong bộ nhớ, tạo 27 candidates (12 RANGE + 15 NOT_NULL), so với 16 NOT_NULL trước đó. Đây là kết quả candidate generation; chưa chứng minh 27 rule sẽ được lưu và chạy. Không sửa code hoặc dữ liệu trong phép thử này.

Nhánh `dashboard_candidate_mode` còn bỏ qua builder tổng quát. Matcher phía sau chỉ nhận checklist, nên mở builder phải đồng bộ candidate ID, parameters và provenance. Proposer có thể bỏ qua candidates model chưa trả; cần theo dõi và retry phần thiếu. Executor upload hiện thiếu nhiều loại rule và thiếu nhánh từ chối loại không hỗ trợ.

## Thứ tự triển khai

| Thời gian | Công việc | Điều kiện xong |
|---|---|---|
| 0–10 phút | Chốt snapshot/profile/semantic Taxi và Netflix từ trace, đếm theo loại ở từng tầng. Phần baseline đã có. | Replay cục bộ không gọi LLM, không đọc latest thay cho profile đã pin. |
| 10–35 phút | Chuẩn hóa kiểu số; nối executor của workflow versioned với adapter đã có trong `versioned_dataset.py`. Chuẩn hóa alias/tham số và giữ nguyên row IDs, counts, ERROR/FAIL. | RANGE trở lại; rule không hỗ trợ/cột ngoài schema trả lỗi rõ; test dữ liệu có lỗi biết trước. |
| 35–65 phút | Thống nhất một danh sách candidates cho builder, prompt và matcher. Tái sử dụng logic tổng quát có evidence; loại trùng theo bảng/cột/loại/tham số. Loại giới hạn tổng rules và lọc loại mang tính trình bày. | Mọi candidate hợp lệ có ID/provenance ổn định, đi được đến lưu và publish. |
| 65–85 phút | Replay Graph 1B trên BE local với cùng snapshot/semantic và model thật, tracing LangSmith. Chia batch, xử lý toàn bộ batch; retry một lần chỉ IDs còn thiếu. | Có số candidate/returned/accepted/rejected/missing theo loại; không âm thầm coi output thiếu là đầy đủ; không legacy fallback. |
| 85–105 phút | Qua API workflow thật: review → publish → Graph 2 → Graph 3 cho Taxi và Netflix. Đối chiếu kết quả với file nguồn và trace; smoke UI phần hiển thị loại mới. | Counts và failed row IDs đúng; báo cáo/signal IDs thuộc execution và dataset đang chạy. |
| 105–120 phút | Dự phòng sửa lỗi cụ thể, chạy lại bước bị ảnh hưởng, chốt diff và báo cáo kết quả. | Có bản sửa review được và bảng coverage trước/sau; ghi rõ phần chưa xác minh. |

## Những kiểm tra bắt buộc

- Ưu tiên NOT_NULL, RANGE, ACCEPTED_VALUES, UNIQUE có căn cứ, CROSS_FIELD_COMPARISON và DUPLICATE_FINGERPRINT. REGEX_FORMAT/NULL_RATE cần pattern/ngưỡng có nguồn; FRESHNESS chỉ sinh khi có SLA, không mặc định 24 giờ cho dữ liệu lịch sử.
- `vendor_id` có 4 giá trị trên 10.000 dòng: không tự suy ra UNIQUE từ semantic identifier. Không dùng mọi giá trị quan sát được làm enum hợp lệ nếu chưa có căn cứ.
- Quan hệ pickup/dropoff phải so sánh datetime, không ép hai chuỗi ngày sang số.
- Các ngưỡng từ thống kê phải được đánh dấu là đề xuất cần review, không trình bày thành chính sách nghiệp vụ đã xác nhận.
- Thử mỗi loại được mở với mẫu PASS và mẫu có lỗi biết trước; kiểm tra schema/parameter sai trả ERROR, không PASS giả. Kiểm tra báo cáo phân biệt lỗi dữ liệu và lỗi thực thi.
- Giữ giới hạn timeout/tool calls/concurrency để thời gian chạy hữu hạn. Chúng không phải trần số rule. Không retry toàn bộ graph khi chỉ thiếu một batch.
- Narrative phải ghép đúng candidate ID; không lấy mô tả của candidate khác chỉ theo vị trí để bù đủ số lượng.
- Đối chiếu `candidate counts → model returned → validated → persisted → published → executed`; mỗi rule bị loại cần lý do.

## Cách tiết kiệm thời gian

Sử dụng snapshot và semantic đã lưu để replay Graph 1B trong môi trường local cách ly; chỉ gọi lại Graph 1A khi thay đổi thực sự ảnh hưởng semantic. Giữ model hiện tại, source binding và DeepAgent. Dùng cùng executor cho workflow thật và các loại rule mới, không xây thêm executor hoặc triển khai dbt CLI mới trong đợt này. Không thay ngưỡng EvalGate để làm bài test pass. Chỉ chạy regression liên quan, một lượt tích hợp thật mỗi dataset và chạy lại lỗi cụ thể nếu có.

Nếu gần mốc 90 phút mà nhóm mở rộng còn lỗi, chốt phần đã kiểm chứng: kiểu số + RANGE, thống nhất candidate contract và executor không PASS giả. Báo rõ loại chưa hoàn tất, không công bố đã gỡ toàn bộ giới hạn khi mới sửa một phần.

## Phạm vi file dự kiến

- `src/services/dashboard_agent_workflow.py`: chuẩn hóa evidence, candidates và matcher.
- `src/agents/nodes/rule_candidate_builder_node.py`: tái sử dụng builder qua cùng hợp đồng candidates.
- `src/agents/nodes/rule_proposer_node.py`: coverage và retry phần thiếu, ghép narrative đúng ID.
- `src/services/job_runner.py`, `src/services/versioned_dataset.py`: tái sử dụng adapter và hợp đồng kết quả.
- Tests sẵn có về source binding, Graph 2 versioned, proposer/workflow; bổ sung regression cho alias `number`, các loại mới và output thiếu.

## Kết quả triển khai hai phần đầu — 2026-09-03

Đã sửa local: chuẩn hóa kiểu số (bao gồm `number`), bỏ giới hạn 3 cột nonnegative và 64 cột evidence; cho phép nhiều loại rule trên cùng cột. Checklist bổ sung UNIQUE từ full-table uniqueness, NULL_RATE từ tỷ lệ thiếu và CROSS_FIELD_COMPARISON từ metrics có sẵn. Builder và matcher dùng cùng ID/tham số; bỏ ghép narrative theo vị trí. Không tự đặt mục tiêu số lượng rule hoặc tự thêm SLA freshness.

Graph 2 dùng chung `execute_rule_frame`, đọc file một lần mỗi run, giữ row references của dashboard và số lỗi đầy đủ. Rule/cột/tham số không hợp lệ trả ERROR; rule khác tiếp tục chạy. Có kiểm tra dataset/version của rule đã duyệt. API giữ ngưỡng NULL_RATE; frontend hiển thị và sửa UNIQUE/NULL_RATE đúng loại.

| Replay snapshot + file nguồn khớp checksum | Checklist | Sau builder | Accepted | Executed | Kết quả |
|---|---:|---:|---:|---:|---|
| Taxi 10k | 32 | 32 | 32 | 32 | 12 RANGE, 16 NOT_NULL, 4 NULL_RATE; 29 PASS, 3 FAIL dữ liệu |
| Netflix titles | 15 | 12 | 12 | 12 | 1 RANGE, 6 NOT_NULL, 2 UNIQUE, 3 NULL_RATE; 12 PASS |

Netflix bỏ 3 NOT_NULL theo nullable_expected trong semantic contract đã lưu, không phải trần số rule. Replay dùng narrative giả lập và các binder/stamper/matcher/executor thật; chưa xác minh số rules LLM thật sẽ trả hoặc Graph 3 live sau bản sửa.

Validation: 113 tests liên quan đã pass (bao gồm lưu kết quả Graph 2, cột ngoài schema, nhiều batch với 144 candidates trên 73 cột, source binding và workflow regression). Frontend production build pass. Kết quả replay đầy đủ nằm ở `scratch/taxi-rule-coverage-replay.json` và `scratch/netflix-rule-coverage-replay.json`.

Chạy lại nhanh, không gọi LLM hoặc ghi database ứng dụng:

```powershell
.venv-e2e/Scripts/python.exe -m scripts.replay_rule_coverage --audit <audit.json> --source <dataset.csv> --output <result.json>
```

Chưa commit/push/deploy các thay đổi này. Thay đổi CI và ghi chú coverage có sẵn trong worktree được giữ nguyên.

## Kiểm thử model thật + LangSmith — 2026-09-03

BE local tại `http://127.0.0.1:8021`, database SQLite cách ly ở `scratch/coverage-live/workflow.db`. Giữ dataset/version/profile ID và checksum của snapshot Taxi/Netflix đã lưu; semantic được tái sử dụng, không gọi lại Graph 1A. Model `gpt-5.6-luna`, Graph 1B và điều tra Graph 3 dùng DeepAgent, legacy/heuristic proposal fallback bị tắt.

Đã thêm retry một lần chỉ với candidate IDs còn thiếu, giữ nguyên rules đã trả đủ; coverage được lưu vào output LangGraph và debug JSON. Lượt thật này không cần retry: Taxi trả đủ hai batch 20+12, Netflix trả đủ một batch 12. Unit test kiểm tra retry chỉ gửi đúng IDs thiếu và fail rõ nếu vẫn thiếu.

| Dataset | Sinh/lưu/duyệt/thực thi | Kết quả Graph 2 | Graph 3 | Signal |
|---|---:|---|---|---:|
| Taxi 10k | 32/32 | 29 PASS, 3 FAIL; 320.000 lượt kiểm tra, 4.704 lượt vi phạm | ANOMALY, report_source=LLM | 65 |
| Netflix titles | 12/12 | 12 PASS; 105.684 lượt kiểm tra, 0 vi phạm | INSUFFICIENT_HISTORY, report_source=LLM | 25 |

Ba FAIL của Taxi là RANGE trên `passenger_count`, `airport_fee`, `congestion_surcharge`: mỗi cột có 1.568 giá trị thiếu bị executor xem là vi phạm. Không có ERROR/SKIPPED. Chưa tối ưu ý nghĩa nghiệp vụ của null trong RANGE theo phạm vi đã thống nhất.

Đã recompute độc lập bằng pandas và so sánh mọi result về checked_count, failed_count, status, row IDs. Đã kiểm tra dataset/version/profile/checksum của execution, workflow sở hữu rule, rule/result IDs trong signals, source binding và signal IDs được trích dẫn trong báo cáo. Không thấy cột hay rule Netflix lọt sang Taxi hoặc ngược lại. Nullable dataset_version_id trên bảng rule_versions dùng binding của workflow/published artifact; execution vẫn pin đúng version.

Tất cả bốn trace hoàn tất, không span lỗi; model trong các LLM span đều là `gpt-5.6-luna`:

- Taxi Graph 1B: https://smith.langchain.com/o/7df33a4e-5170-49b6-aab9-7203f568eba2/projects/p/c464ae9a-a73a-4194-b5f8-5f3878e693c6/r/01a06715-c104-7c83-a704-54076cfac879
- Netflix Graph 1B: https://smith.langchain.com/o/7df33a4e-5170-49b6-aab9-7203f568eba2/projects/p/c464ae9a-a73a-4194-b5f8-5f3878e693c6/r/01a06715-c105-7472-b2d5-5e1a3559fa4e
- Taxi Graph 3: https://smith.langchain.com/o/7df33a4e-5170-49b6-aab9-7203f568eba2/projects/p/c464ae9a-a73a-4194-b5f8-5f3878e693c6/r/01a06718-64ee-7df2-bb1d-fc97a8efaada
- Netflix Graph 3: https://smith.langchain.com/o/7df33a4e-5170-49b6-aab9-7203f568eba2/projects/p/c464ae9a-a73a-4194-b5f8-5f3878e693c6/r/01a06718-64ee-7df2-bb1d-fc87a7539f3d

Bằng chứng: `scratch/coverage-live/{taxi,netflix}-audit.json`, `verification.json`, `traces-summary.json`, `{taxi,netflix}-report.md`. Workflow local: Taxi `workflow-5dc69c94a96b4069ab01`, Netflix `workflow-d1ab76207117407dab76`. Graph 1B chạy khoảng 105 giây/45 giây; Graph 3 khoảng 96 giây/30 giây. Kiểm thử API review/publish/execute/report chạy đồng thời hai dataset. Script điều khiển đã được sửa tên field phản hồi artifact (`type`, `temporary`); các job đã thành công được tái sử dụng qua idempotency, không gọi lại model.

96 regression tests liên quan sau thay đổi retry/fallback đã pass; Ruff pass. Chưa commit hoặc deploy. Kết quả này xác minh BE local với source/snapshot đã lưu và API model/LangSmith thật, không phải lượt mới trên production hoặc UI.
