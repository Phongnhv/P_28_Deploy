# Báo cáo rà soát cho SOL — chỉ phạm vi đoạn hội thoại được cung cấp

**Ngày:** 2026-09-02  
**Phạm vi:** chỉ các nội dung trong đoạn hội thoại được dán kèm, bắt đầu từ phản hồi lúc khoảng 21:40 về Graph 1 sinh rule và kết thúc ở phản hồi lúc khoảng 22:12 về report Graph 3.  
**Không bao gồm:** các vấn đề dataset, demo Steward, session, logo, Cloud Run migration hoặc các trao đổi khác ngoài đoạn hội thoại này.

> Mục đích là để SOL kiểm tra lại đúng hai nhóm: **Graph 1B/rule proposal** và **Graph 3/generated Markdown report**. Mật khẩu/token không được ghi vào báo cáo.

## 1. Kết luận ngắn

Các phản ánh trong đoạn hội thoại là đúng về bản chất:

- CLI/trace có đủ thông tin nghiệp vụ tiếng Việt, nhưng UI workflow đang lấy nhầm field tiếng Anh hoặc không truyền tiếp các field quan trọng.
- `assumptions` và `parameter_provenance` bị mất từ model trung gian rồi bị ghi cứng thành `[]` ở bước persistence.
- UI chậm hơn CLI vì có thể cộng dồn timeout, retry/fallback và polling stale; đây không phải thời gian khỏe mạnh của một lần proposer.
- Graph 3 có report Markdown thật do `report_writer` sinh ra, nhưng workflow deploy cũ bỏ qua state chứa report và UI hiển thị structured hypothesis cards/fallback thay thế.
- Phần sửa report đã được thực hiện ở local và test code pass; chưa deploy và chưa browser-verify live.

## 2. Ma trận vấn đề và trạng thái

| Mã | Vấn đề trong đoạn hội thoại | Mức xác nhận | Đã sửa? | SOL cần làm |
|---|---|---|---|---|
| R1 | `title`, `description`, `evidence_summary` lấy từ candidate tiếng Anh thay vì `rule_name`, `rule_description`, `ai_reasoning` của Agent | **Đã xác nhận trong code** tại `dashboard_agent_workflow.py` | **Chưa sửa** | Sửa normalization, ưu tiên field Agent; candidate chỉ là fallback |
| R2 | `DashboardProposal` không khai báo `assumptions` và `parameter_provenance` | **Đã xác nhận trong code** | **Chưa sửa** | Bổ sung field vào dataclass/model trung gian và test type/data flow |
| R3 | Bước persistence ghi cứng `assumptions="[]"`, `parameter_provenance="[]"` | **Đã xác nhận trong code** tại `rule_proposer_workflow.py` | **Chưa sửa** | Lưu `json.dumps()` từ output Agent; proposal cũ không tự khôi phục nếu không còn trace |
| R4 | Frontend tìm `title_vi`, `description_vi`, `evidence_summary_vi` nhưng API không trả các field này | **Đã xác nhận trong code** tại `App.tsx` | **Chưa sửa** | Khi chọn tiếng Việt, dùng field canonical `rule_name`, `rule_description`, `ai_reasoning`/`evidence_summary` |
| R5 | Accordion “Vì sao có luật này” mặc định đóng | **Đã xác nhận** | **Chưa sửa** | Giữ accordion nhưng hiển thị business rationale ngắn ngay trên card; chi tiết vẫn mở rộng |
| R6 | CLI Graph 1 nhanh hơn UI | **Đã tái hiện** | **Chưa sửa dứt điểm** | Đo và loại bỏ duplicate proposer/fallback; thống nhất timeout/retry |
| R7 | DeepAgent batch đầu lỗi structured output rồi rơi sang Legacy 1-shot | **Đã tái hiện trong CLI trace** | **Chưa sửa** | Sửa validator confidence/structured output hoặc báo lỗi rõ; fallback chỉ chạy một lần có chủ đích |
| R8 | UI có thể chạy Graph 1B, timeout/fallback sang proposer khác rồi frontend vẫn hiển thị Generating | **Đã xác nhận qua code/logic hiện tại** | **Chưa sửa dứt điểm** | Ghi timing/fallback reason; đồng bộ job state và polling terminal state |
| R9 | Trace JSON của CLI có field tiếng Việt đầy đủ | **Đã xác nhận bằng run CLI** | Đã có ở CLI | Dùng trace làm nguồn regression: Agent → normalization → DB → API → UI |
| R10 | UI Graph 3 hiển thị card hypotheses/template thay vì report Markdown thật | **Đã xác nhận** | **Đã sửa local, chưa deploy** | Kiểm tra live artifact và UI sau khi deploy |
| R11 | `run_anomaly_graph()` trả `steward_report_markdown` nhưng `run_analysis_report()` cũ bỏ qua state trả về | **Đã xác nhận trong code** | **Đã sửa local, chưa deploy** | Verify artifact `ANOMALY_REPORT.report_markdown` trên live |
| R12 | Report Markdown bị xem như raw text hoặc không được lấy từ workflow artifact | **Đã xác nhận trong code** | **Đã sửa local, chưa deploy** | Browser-check heading/table/list và không còn raw `#`/`|` |
| R13 | Có renderer fallback hardcode/template khi LLM lỗi | **Đã xác nhận trong code** | Không xóa; đã giới hạn vai trò local | Kiểm tra `report_source=LLM` và `FALLBACK`; không để fallback che report LLM |
| R14 | Một điểm chưa xác minh độc lập: trace JSON 37 luật được nhắc ở báo cáo ban đầu | **Đã được bổ sung bằng một CLI run khác**: trace thực tế có 36 luật | Không áp dụng | SOL cần dùng trace/revision đúng của deploy để đối chiếu, không dùng số 37 nếu không có file tương ứng |

## 3. Bằng chứng đã có

### 3.1 CLI Graph 1

Run CLI trên SQLite tạm, không đụng Supabase/deploy:

- Tổng thời gian: **201.48 giây (~3.36 phút)**.
- `rule_proposer`: **135.49 giây**.
- DeepAgent batch đầu lỗi sau **80.96 giây** vì validation confidence.
- Legacy fallback mất thêm **135.47 giây**.
- Kết quả: **36 rules**, có trace JSON.
- Trace có:
  - `rule_name`: 36/36;
  - `rule_description`: 36/36;
  - `business_rationale`: 36/36;
  - `ai_reasoning`: 36/36;
  - `assumptions`: 36/36;
  - `parameter_provenance`: 16/36, hợp lý vì chỉ rule có parameter mới cần field này.

### 3.2 Report Graph 3

File report Markdown được cung cấp có nội dung thực tế theo dataset/run, gồm summary, rule results, anomaly signals, hypotheses, evidence và recommended checks. Nó phù hợp với output LLM theo cấu trúc report writer, không phải một placeholder rỗng.

Ảnh UI lại đang thể hiện một view structured riêng: card giả thuyết, evidence ủng hộ/phản bác và recommended checks. Vì vậy hai nội dung bị trộn:

1. **Generated Markdown report:** nội dung cần hiển thị chính.
2. **Structured hypothesis panel:** dữ liệu phụ, có label/UI layout hardcode và có thể chứa text tiếng Anh.

## 4. Thay đổi local hiện tại

Đã sửa nhưng **chưa commit/push/deploy**:

- Backend giữ final state của Graph 3 và lưu `report_markdown`, `report_source`, `report_path` vào `ANOMALY_REPORT` artifact.
- Graph 3 UI đọc report từ durable workflow artifact, không phụ thuộc endpoint file legacy.
- Bỏ structured hypothesis panel khỏi nội dung chính Graph 3.
- Step 5 dùng `ReactMarkdown + remarkGfm` thay cho `<pre>` raw Markdown.
- Mock và regression test được cập nhật để kiểm tra report đi vào artifact.

Các phần **chưa sửa** trong đoạn hội thoại:

- Field mapping của Graph 1 (`rule_name`, `rule_description`, `ai_reasoning`).
- `assumptions`/`parameter_provenance` trong `DashboardProposal` và DB persistence.
- Tối ưu duplicate fallback/retry/polling của Rule proposer.
- Language QA để bảo đảm toàn bộ text report/card tiếng Việt.

## 5. Kiểm thử hiện tại

- Backend targeted tests: **15 passed**.
- Frontend production build: **pass**.
- `git diff --check`: **pass**.
- Browser harness live/local UI: **chưa hoàn tất**, vì Chrome yêu cầu cấp quyền “Allow remote debugging”.
- Chưa có kết luận rằng bản sửa đã chạy trên deploy; hiện chỉ có bằng chứng local/code-level.

## 6. Kế hoạch SOL kiểm tra và sửa

### P0 — Sửa và kiểm thử dữ liệu Graph 1

1. Mở rộng `DashboardProposal` với `assumptions` và `parameter_provenance`.
2. Normalize theo thứ tự:
   - title ← `rule_name`;
   - description ← `rule_description`;
   - evidence summary ← `ai_reasoning`;
   - rationale ← `business_rationale`;
   - fallback về candidate chỉ khi Agent thiếu field.
3. Persistence bằng JSON thật, không ghi `[]` mặc định nếu Agent đã trả dữ liệu.
4. API response và frontend tiếng Việt dùng field canonical, không dùng các field `_vi` không tồn tại.
5. Chạy lại Graph 1B để tạo proposal mới; không cố sửa ngược proposal cũ nếu không còn trace nguồn.

### P0 — Nối report Graph 3

1. Deploy đúng revision chứa thay đổi local.
2. Tạo run mới, không dùng run/session cũ.
3. Kiểm tra `ANOMALY_REPORT` artifact có Markdown không rỗng và `report_source` đúng.
4. Xác nhận UI hiển thị report Markdown thật, có heading/table/list; không hiển thị card template thay thế.
5. Tạo tình huống LLM timeout để xác nhận fallback được gắn nhãn `FALLBACK` và không làm job “running” vô hạn.

### P1 — Tối ưu Rule proposer

1. So sánh trace CLI/UI cùng dataset và cùng version.
2. Ghi riêng provider latency, worker time, timeout, retry, fallback và frontend polling.
3. Đảm bảo một job không chạy Graph 1B rồi gọi thêm một proposer độc lập ngoài fallback có chủ đích.
4. Sửa structured-output validator khiến DeepAgent batch fail; thêm regression test cho confidence validation.
5. Đặt timeout/retry budget rõ ràng và kết thúc UI ngay khi backend job terminal.

### P1 — Regression và browser evidence

1. Test xuyên suốt Agent trace → normalization → DB → API → UI.
2. Test Agent thiếu field để xác nhận fallback không làm mất field khác.
3. Test report LLM và report fallback.
4. Chạy browser harness trên deploy sau khi cấp quyền remote debugging.

## 7. Tiêu chí hoàn tất

- UI Graph 1 hiển thị đúng thông tin tiếng Việt từ Agent và không còn field bị rỗng do `_vi` giả.
- DB/API giữ nguyên `assumptions` và `parameter_provenance`.
- Một lần chạy khỏe mạnh không cộng dồn duplicate proposer/fallback; timing được trace rõ.
- Graph 3 hiển thị generated Markdown report làm nội dung chính.
- Fallback chỉ xuất hiện khi LLM lỗi và có source/status rõ ràng.
- Có browser evidence trên deploy cho Graph 1B và Graph 3.

