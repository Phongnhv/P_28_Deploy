# Coding Agent Prompt: Finalize Data Steward Markdown Report After Graph 3

## Role

Bạn là coding agent làm việc trong repository RidePulse DQ. Hãy triển khai thay đổi này hoàn chỉnh, giữ phạm vi nhỏ, không làm hỏng contract hiện tại của Graph 2 và Graph 3, đồng thời bổ sung test chứng minh hành vi mới.

Trước khi sửa code:

1. Đọc `AGENTS.md` nếu tồn tại và tuân thủ toàn bộ hướng dẫn trong đó.
2. Đọc các file nguồn và test liên quan được liệt kê bên dưới.
3. Kiểm tra working tree; không ghi đè hoặc hoàn tác thay đổi không liên quan của người dùng.
4. Lập implementation plan ngắn trước khi bắt đầu chỉnh sửa.

## Task

Sửa workflow để sau khi Graph 2 chạy test và Graph 3 hoàn tất anomaly analysis/steward insights, hệ thống luôn tạo một file Markdown cuối cùng dành cho Data Steward.

Markdown phải là artifact cuối của toàn workflow. Không giả định rằng `steward_insights_node` hiện đang sinh Markdown: implementation hiện tại của node này trả về structured `hypotheses` và `hypothesis_status`, không trả về `steward_summary`.

## Current behavior and confirmed root cause

Luồng hiện tại:

```text
Graph 2
  test_generator
  -> validate_dbt_project
  -> test_runner
  -> persist_report_node
  -> END

Graph 3
  anomaly_detector
  -> steward_insights_node
  -> persist_analysis_node
  -> END
```

Các vấn đề đã xác nhận:

- `persist_report_node` nằm trong Graph 2 và chạy trước Graph 3.
- Helper ghi report trong `persist_report_node` chỉ tạo `.md` khi `steward_summary` truthy.
- Tại thời điểm Graph 2 chạy, `steward_summary` chưa tồn tại.
- `steward_insights_node` sinh `hypotheses`, không sinh `steward_summary` hoặc file `.md`.
- `persist_analysis_node` chỉ lưu anomaly run, signals và hypotheses vào database.
- Vì vậy workflow có thể hoàn tất test và anomaly analysis nhưng không có Markdown report.

## Target behavior

```text
Graph 2: execute tests -> persist deterministic execution results
    |
    v
Graph 3: detect anomalies -> generate structured hypotheses -> persist analysis
    |
    v
Finalize report: combine execution + anomaly + hypotheses -> render/write Markdown
```

Sau một lần chạy workflow đầy đủ:

- Luôn có một file `.md` cho Data Steward.
- Report chứa kết quả Graph 2 và Graph 3 khi chúng có sẵn.
- Không có anomaly hoặc không cần hypothesis vẫn phải tạo report.
- Không phụ thuộc vào một field `steward_summary` chưa được node nào tạo ra.
- Đường dẫn report được trả về trong state/metadata hoặc result của runner.

## Architecture requirements

### 1. Preserve graph boundaries

Giữ Graph 2 là deterministic execution/persistence boundary. Graph 3 phải tiếp tục có khả năng chạy hoặc retry độc lập bằng `execution_run_id` mà không chạy lại Graph 2.

Không chuyển anomaly/LLM logic vào Graph 2.

### 2. Separate insight generation from report rendering

Giữ trách nhiệm của `steward_insights_node` là tạo structured hypotheses.

Không bắt node LLM trực tiếp mở/ghi file. Tạo helper/service hoặc node finalization riêng để render và persist Markdown từ dữ liệu đã có.

Report renderer phải deterministic và không gọi thêm LLM chỉ để format Markdown.

### 3. Finalize at the correct orchestration point

Gọi report finalization sau khi Graph 3 hoàn tất và state có:

- `anomaly_decision`;
- `signal_observations`;
- `hypotheses`;
- `hypothesis_status`;
- `execution_run_id` và `dataset_id`.

Nếu runner hiện tại gọi Graph 2 rồi Graph 3, gọi finalizer tại orchestration boundary sau Graph 3. Nếu Graph 3 có entry point retry độc lập, retry thành công cũng phải refresh report.

Tránh chỉ nối node vào Graph 3 nếu node không thể lấy execution results. Có thể load execution results từ persistence bằng `execution_run_id`.

### 4. Reuse persisted execution data

Finalizer phải dùng `execution_run_id` làm correlation key và lấy dữ liệu Graph 2 từ nguồn persistence canonical hiện có:

- test/execution run record;
- persisted test/DQ results;
- DQ score hoặc metadata hiện có nếu đã được lưu.

Không chạy lại test và không dựa vào process memory của Graph 2.

### 5. Always create a useful report

Không dùng điều kiện kiểu `if steward_summary: write markdown` để quyết định report có tồn tại hay không.

Report phải được tạo khi:

- Có `ANOMALY`, `WATCH` hoặc `CRITICAL` và hypotheses.
- Decision là `NORMAL`, hypothesis status là `NOT_REQUIRED`.
- Graph 3 hoàn tất nhưng hypotheses rỗng.
- LLM lỗi nhưng fallback hypothesis được sử dụng.

Nếu orchestration hỗ trợ fallback khi Graph 3 thất bại, tạo partial/failure report ghi rõ analysis chưa hoàn tất. Không đánh dấu analysis thành công khi Graph 3 lỗi.

### 6. Idempotency and file naming

Finalization phải idempotent theo `execution_run_id`:

- Retry Graph 3 không tạo hàng loạt report mồ côi.
- Ưu tiên canonical path ổn định như `steward_report_<execution_run_id>.md`, hoặc có manifest/artifact reference trỏ rõ bản mới nhất.
- Nếu phù hợp, ghi temporary file rồi replace để tránh report dở dang.

Không đổi output directory convention hiện có nếu không cần thiết.

## Required Markdown content

Report cuối cần có:

1. `Data Steward Report` — tiêu đề.
2. Run metadata: execution ID, anomaly run ID, dataset ID, timestamp và status.
3. Data quality summary: score/grade, tổng rule, PASS/FAIL/ERROR và dimension summary nếu có.
4. Rule/test findings: ít nhất failed/error results, violation rate/count và evidence refs khi có.
5. Anomaly decision: decision, score, confidence, severity và override reason.
6. Anomaly signals: signal ID/family/target, score, reliability và explanation.
7. Steward hypotheses: type, summary, confidence, supporting/contradicting signals, evidence, recommended checks, missing evidence và limitations.
8. Analysis notes/errors: `NOT_REQUIRED`, fallback, empty hypotheses hoặc partial failure.

Không ghi raw sensitive data hoặc PII. Chỉ dùng evidence/sample identifiers workflow đã cho phép lưu.

## Files to inspect first

Tối thiểu đọc:

- `src/agents/graph.py`
- `src/agents/state.py`
- `src/agents/nodes/persist_report_node.py`
- `src/agents/nodes/steward_insights_node.py`
- `src/agents/nodes/persist_analysis_node.py`
- `src/agents/nodes/anomaly_detector_node.py`
- `src/services/job_runner.py`
- `src/services/rule_store.py`
- database models liên quan trong `src/models/database.py`
- `tests/test_agents/test_steward_insights_node.py`
- test hiện có cho execution graph, anomaly graph, report persistence và job runner.

Tìm tất cả call site của:

- `run_execution_graph`
- `run_anomaly_graph`
- `build_anomaly_graph`
- `persist_report_node`
- `steward_report_path`
- `report_file_path`
- endpoint/job retry Graph 3.

## Implementation steps

1. Xác nhận contract canonical của execution results và anomaly results.
2. Thiết kế report input/model hoặc assembly function rõ ràng.
3. Tách logic Markdown rendering/writing khỏi `persist_report_node`, hoặc tạo service finalizer mới.
4. Render Markdown deterministic từ persisted execution data và Graph 3 result.
5. Gọi finalizer sau Graph 3 trong CLI/orchestration hiện tại.
6. Tích hợp finalizer vào đường retry Graph 3 nếu tồn tại.
7. Trả `steward_report_path` hoặc artifact reference cho caller.
8. Giữ JSON report và database persistence tương thích ngược.
9. Thêm unit và integration tests.
10. Chạy targeted tests rồi test suite rộng hơn trong phạm vi hợp lý.

## Non-goals and constraints

- Không đổi prompt/thuật toán của `steward_insights_node` nếu không cần.
- Không thêm LLM call để viết Markdown.
- Không chạy lại Graph 2 khi retry Graph 3.
- Không đổi database schema trừ khi thật sự cần; nếu cần migration phải giải thích và test.
- Không xóa JSON report hiện tại.
- Không làm Graph 2 thất bại chỉ vì Graph 3/report finalization thất bại, trừ khi contract hiện tại yêu cầu atomic.
- Không sửa frontend/API ngoài phần cần thiết để expose report path.
- Không refactor rộng ngoài phạm vi.

## Required tests

### Unit tests

Chứng minh:

- Renderer tạo Markdown với execution results, anomaly decision, signals và hypotheses.
- `NORMAL` + `NOT_REQUIRED` vẫn tạo file và giải thích không cần hypothesis.
- Hypotheses rỗng vẫn tạo report.
- Fallback hypothesis được ghi rõ.
- Nội dung chứa execution ID và các section bắt buộc.
- Finalize hai lần cùng execution ID có hành vi idempotent.
- Không ghi `None`/traceback khó hiểu thay cho fallback thân thiện.

### Integration/orchestration tests

Chứng minh:

- Graph 2 và Graph 3 hoàn tất thì file `.md` tồn tại.
- Report chứa dữ liệu đúng `execution_run_id`.
- Report path/reference xuất hiện trong result hoặc metadata.
- Retry Graph 3 refresh report mà không chạy lại Graph 2.
- JSON/database persistence hiện tại vẫn hoạt động.

Mock LLM và external integrations. Test không phụ thuộc network thật.

## Verification commands

Xác định đúng test paths rồi chạy ít nhất:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
.venv\Scripts\python.exe -m pytest -p pytest_asyncio.plugin -q tests/test_agents/test_steward_insights_node.py
```

Sau khi thêm test, chạy targeted tests của report finalization, anomaly graph và orchestration/job runner. Có thể dùng Docker theo convention repository nếu local không chạy được, nhưng phải ghi rõ command và blocker.

Cuối cùng chạy suite rộng hơn trong phạm vi thay đổi nếu môi trường cho phép.

## Acceptance criteria

Task chỉ hoàn tất khi:

- Workflow Graph 2 -> Graph 3 tạo Markdown report cuối.
- Report không còn phụ thuộc `steward_summary` được set trước Graph 3.
- `steward_insights_node` vẫn trả structured hypotheses và không ghi file.
- `NORMAL`/`NOT_REQUIRED` vẫn có report.
- Anomaly có hypotheses/fallback tạo report chứa insight và recommended checks.
- Retry Graph 3 refresh report bằng `execution_run_id` mà không chạy lại Graph 2.
- Report path/artifact reference được expose cho caller.
- JSON/database persistence không regression.
- Test mới và targeted tests đều pass.
- Mọi thay đổi ngoài phạm vi đều được giải thích.

## Expected completion report

Khi hoàn tất, coding agent phải báo cáo:

1. Root cause đã xử lý.
2. Kiến trúc/luồng mới.
3. File đã sửa hoặc thêm.
4. Hành vi trong case `NORMAL`, anomaly, fallback và retry.
5. Test/command đã chạy và kết quả.
6. Limitation hoặc follow-up còn lại.

