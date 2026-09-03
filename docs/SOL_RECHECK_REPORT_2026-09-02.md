# Báo cáo rà soát các vấn đề gần đây — gửi SOL

**Ngày:** 2026-09-02  
**Phạm vi:** toàn bộ chuỗi trao đổi gần đây, không giới hạn các tin nhắn sau 21:00; gồm repo/branch deploy, Cloud Run/Vercel, Supabase, dataset, demo Steward, session, Graph 1/2/3 và UI report.  
**Mục tiêu:** để SOL kiểm tra lại trên môi trường deploy, xác định phần nào đã thực sự ổn định và lập kế hoạch sửa tiếp theo.

> Báo cáo này tổng hợp các trao đổi và bằng chứng đã có. Mật khẩu/tokens của tài khoản demo không được ghi lại tại đây.

## 0. Phạm vi lịch sử cần SOL rà soát

Các vấn đề dưới đây đã được nêu xuyên suốt từ đầu chuỗi trao đổi, không chỉ trong phiên tối:

1. Push/PR đúng **deploy repo/branch**, không push nhầm repo VinUni.
2. Lỗi Graph 3 khi deploy dù kiểm thử local ổn; phân biệt backend, frontend, Cloud Run service và Cloud Run Job.
3. Chuyển các workflow chạy dài sang Cloud Run Jobs và xử lý các đường legacy còn sót.
4. Kiểm thử trực tiếp trên web/deploy sau khi laptop restart; xác minh E2E thay vì chỉ nhìn trạng thái local.
5. Logo góc trái phải quay về Home.
6. Demo Steward: tài khoản hiển thị sẵn, public demo, quyền truy cập dataset và giới hạn API call; nếu cần thì kiểm tra alignment password/hash trên Supabase.
7. Dataset NYC 50k xuất hiện lại sau khi từng xóa; kiểm tra khả năng seed/bootstrap/import tự tạo lại.
8. Chạy thử nhiều dataset, session cũ tự restore vào Graph 3, Rule proposer chậm và DeepAgent/fallback.
9. Dataset version 1 → version 2.
10. Graph 3 hiển thị template/card thay vì Markdown report thật, mất thông tin tiếng Việt và có raw Markdown ở Step 5.

Những mục chỉ mới được trao đổi hoặc đã xử lý ở một revision trước nhưng chưa có evidence live cuối cùng được đánh dấu **chưa xác nhận** trong ma trận bên dưới.

## 1. Tóm tắt điều hành

Có ba nhóm vấn đề chính:

1. **Luồng deploy và trạng thái workflow:** Cloud Run Jobs đã được dùng cho các stage chạy dài, nhưng vẫn còn đường legacy/compatibility. Cần xác minh revision live và đường thực thi thực tế của từng dataset.
2. **Graph 1B / Rule proposer:** CLI chạy nhanh hơn UI; UI có thể chậm do retry, timeout, fallback và polling. Đây là vấn đề hiệu năng đã tái hiện được, chưa tối ưu dứt điểm.
3. **Graph 3 / báo cáo:** report Markdown do `report_writer` sinh ra là dữ liệu thật, nhưng workflow deploy trước đây bỏ qua state trả về và UI hiển thị structured hypotheses/fallback thay thế. Đã sửa local, chưa deploy và chưa browser-verify trên live.

## 2. Ma trận trạng thái

| Mã | Vấn đề | Trạng thái xác nhận | Đã sửa? | Việc SOL cần kiểm tra |
|---|---|---|---|---|
| D1 | Push/PR nhầm repo VinUni hoặc nhầm branch | Đã xác nhận phạm vi: không được push repo VinUni; local đang ở branch `deploy` | Chưa tạo PR mới trong đợt này | Xác nhận remote, branch, commit live; chỉ thao tác trên deploy repo nếu được yêu cầu |
| D2 | Cloud Run Job thay cho request backend dài | Đã xác nhận trong code: `WORKFLOW_RUN_CHECKS` và `WORKFLOW_ANALYZE_REPORT` là job riêng | Đã có | Kiểm tra log live để chắc chắn Graph 2/3 không đi qua handler legacy |
| D3 | Vẫn thấy “legacy” | Đã xác nhận: code còn compatibility handler và fallback legacy | Chưa loại bỏ hoàn toàn | Phân biệt legacy dataset/run cũ với legacy code path; không xóa compatibility trước khi có migration plan |
| D4 | Backend chạy được nhưng frontend báo service unavailable | Đã xác nhận một phần: có mismatch giữa report được lưu trong DB/artifact và endpoint file legacy mà UI cũ gọi | Đã sửa local cho Graph 3 workflow | Test live bằng một workflow mới và kiểm tra network response/error state |
| D5 | Kiểm thử trực tiếp trên web/deploy sau khi laptop restart | Đã có các lần kiểm thử trước, nhưng chưa có evidence live cuối cho revision hiện tại | Chưa xác nhận hoàn toàn | Mở browser session mới, không dùng state cũ; chạy lại từ login đến Results |
| D6 | Yêu cầu PR/push vào deploy repo và không đụng repo VinUni | Đã xác nhận nguyên tắc; working tree hiện chưa push thay đổi mới | Chưa tạo PR/push mới | SOL chỉ kiểm tra/push đúng deploy remote khi được yêu cầu; kiểm tra remote URL trước thao tác |
| G1 | Rule proposer UI chậm hơn CLI | Đã tái hiện: CLI Graph 1 khoảng 3.36 phút, `rule_proposer` khoảng 135 giây; UI từng lên 8 phút | Chưa tối ưu dứt điểm | Đo từng attempt/retry/fallback/polling trên live; đặt timeout và ngân sách rõ ràng |
| G2 | DeepAgent bị treo hoặc rơi sang legacy | Đã xác nhận một CLI run: DeepAgent batch lỗi validation confidence rồi fallback Legacy 1-shot | Chưa chứng minh mọi run deploy đều DeepAgent thành công | Kiểm tra trace từng node, lý do fallback, số lần retry; sửa validation/timeout nếu cần |
| G3 | Graph 3 backend có thể hoàn tất nhưng UI không có report đúng | Đã xác nhận qua code: `run_anomaly_graph()` trả `steward_report_markdown`, còn `run_analysis_report()` cũ bỏ qua state này | Đã sửa local | Chạy Graph 3 mới trên live, lấy `ANOMALY_REPORT` artifact và đối chiếu nội dung Markdown |
| G4 | UI Graph 3 hiển thị card/template thay vì report Markdown | Đã xác nhận: ảnh là `AnomalyInvestigationPanel` structured hypotheses, không phải Markdown report đầy đủ | Đã bỏ panel khỏi luồng chính local | Xác nhận live chỉ còn report Markdown là nội dung chính; kiểm tra report vẫn đọc được khi LLM thành công |
| G5 | Field quan trọng tiếng Việt không hiện; nội dung card có tiếng Anh | Đã xác nhận: card đọc trực tiếp `summary`/`recommended_checks` persisted, còn report Markdown thật không được nối vào UI | Đã nối report thật local; chưa có language QA | Kiểm tra report LLM có giữ thông tin tiếng Việt; tách riêng lỗi dịch/English fragment nếu còn |
| G6 | Step 5 hiện raw Markdown (`#`, `|`) | Đã xác nhận trong code: dùng `<pre>` | Đã sửa local sang `ReactMarkdown + remarkGfm` | Browser-verify heading, bảng, list trên live |
| G7 | Report template hardcode/fallback | Đã xác nhận có `render_steward_report_vi()` hardcode làm fallback khi LLM lỗi | Không xóa; đã giới hạn vai trò fallback local | Kiểm tra `report_source=LLM/FALLBACK`; không hiển thị fallback như report Agent nếu LLM đã sinh thành công |
| UI1 | Logo góc trái bấm về Home | Đã được yêu cầu và có thay đổi UI trước đó theo trao đổi | Chưa re-verify trên revision hiện tại | Click logo từ từng Graph và từ trang sâu; kiểm tra URL/state về Home |
| UI2 | Login hiển thị sẵn tài khoản demo Steward | Đã được yêu cầu/cấu hình trong các trao đổi trước | Chưa có evidence live cuối | Kiểm tra field prefill không lộ password, đăng nhập đúng role và logout được |
| UI3 | Bật public demo cho Steward | Đã được yêu cầu; trạng thái live cuối chưa có evidence | Chưa xác nhận | Kiểm tra public-demo flag và behavior khi chưa đăng nhập/đã đăng nhập |
| AUTH1 | Demo Steward được giới hạn API call | Chính sách được hỏi nhưng chưa có evidence response/quota cuối | Chưa xác nhận | Gọi thử đến ngưỡng thấp trong môi trường an toàn; kiểm tra status code, audit log và không ảnh hưởng tài khoản thật |
| AUTH2 | Username/password demo không align với Supabase hash | Đã nêu phương án xử lý; chưa có bằng chứng cần cập nhật hash | Chưa sửa/không được tự ý sửa khi chưa đối chiếu | Kiểm tra account status, password verification và hash policy trong Supabase; chỉ cập nhật nếu mismatch được chứng minh |
| DS1 | Dataset NYC Yellow Taxi 50k xuất hiện lại | Dataset xuất hiện trong catalog/UI đã được xác nhận | Việc xóa trước đây không đủ để kết luận nguyên nhân | Kiểm tra seed/bootstrap/import history và DB sau restart; xác định ai/tác vụ nào tạo lại |
| DS2 | Có phải backend restart tự upload lại dataset? | Chưa xác nhận nguyên nhân cuối cùng | Chưa sửa | Đối chiếu audit log, import job, seed code và timestamps; không kết luận chỉ từ việc dataset xuất hiện lại |
| DS3 | Nâng dataset version 1 lên version 2 | Code đã có mô hình versioned dataset/profile snapshot | Chưa kiểm thử đầy đủ v1 → v2 end-to-end | Import cùng dataset thành version 2, kiểm tra profile/contract/rule/DQ run không dùng nhầm v1 |
| DS4 | Dataset khác chạy ổn không | Đã có một số local/triage checks, chưa có ma trận live đầy đủ | Chưa xác nhận toàn bộ | Chạy ít nhất 2 dataset khác loại: có anomaly và không anomaly; ghi latency, source, status |
| A1 | Demo Steward được cấp quyền một số dataset | Đã từng cấu hình theo trao đổi trước, cần xác minh live | Chưa có bằng chứng live mới trong đợt này | Kiểm tra access rows, READ/MANAGE, dataset visibility và không vượt quyền |
| A2 | Demo Steward public demo và giới hạn API call | Yêu cầu đã được nêu; trạng thái quota live chưa có bằng chứng cuối | Chưa xác nhận | Kiểm tra public demo flag, quota/rate-limit response và UI hiển thị tài khoản demo; không ghi password vào log |
| S1 | Đăng nhập lại vẫn giữ session cũ, vào thẳng Graph 3 | Hành vi đã xác nhận qua ảnh; cơ chế restore latest workflow cũng có trong frontend | Chưa sửa dứt điểm | Test logout/login, refresh, tab mới, đổi dataset; quy định rõ khi nào restore run cũ và khi nào tạo session mới |
| E1 | E2E hoàn chỉnh local/deploy | Local unit/integration checks pass; full browser live chưa hoàn tất | Chưa xác nhận hoàn toàn | Chạy fresh browser từ login → upload/select → Graph 1 → review → Graph 2 → Graph 3 → Results |

## 3. Những gì đã được kiểm thử có bằng chứng

### 3.1 Graph 1 CLI

Một run CLI trên dataset 50k đã hoàn tất khoảng **201.48 giây**:

- `rule_candidate_builder`: khoảng 0.9 giây trong Graph 1B;
- `prompt_customizer`: khoảng 16.3 giây;
- `rule_proposer`: khoảng 135.49 giây;
- một DeepAgent batch thất bại validation confidence và rơi sang Legacy fallback;
- trace JSON vẫn chứa `rule_name`, `rule_description`, `business_rationale`, `ai_reasoning`, `assumptions`; nội dung rule chính có tiếng Việt.

Kết luận: dữ liệu tiếng Việt và thông tin chi tiết **có tồn tại ở output/trace**, lỗi chính là tầng workflow/UI không hiển thị hoặc không lưu tiếp đúng cách.

### 3.2 Kiểm thử code sau sửa local

- Backend targeted tests: **15 passed**.
- Frontend production build: **pass**.
- `git diff --check`: **pass**.
- Browser harness: **chưa hoàn tất**, vì Chrome dừng ở yêu cầu cấp quyền “Allow remote debugging”. Đây là thiếu điều kiện kiểm thử UI, không phải kết luận app live đã pass.

### 3.3 Các kết luận không được suy ra chỉ từ kiểm thử local

- Local pass không chứng minh frontend live đang trỏ đúng backend/revision.
- Backend job hoàn tất không chứng minh frontend polling, session restore và report endpoint cùng đúng.
- Dataset còn thấy trong catalog không chứng minh backend restart tự upload; cần audit/import/seed evidence.
- Tài khoản demo tồn tại không chứng minh đã bị giới hạn quota hoặc có đúng dataset access.
- Một run Graph 3 thành công không chứng minh mọi run đều không fallback sang Legacy.

## 4. Thay đổi local mới nhất cần SOL review

Các thay đổi hiện đang ở working tree local, chưa commit/push:

1. `src/services/rule_proposer_workflow.py`
   - giữ state trả về từ `run_anomaly_graph()`;
   - lưu `report_markdown`, `report_source`, `report_path` vào artifact `ANOMALY_REPORT`.

2. `frontend/src/App.tsx`
   - đọc report từ durable workflow artifact thay vì endpoint file legacy;
   - bỏ `AnomalyInvestigationPanel` khỏi nội dung chính Graph 3.

3. `frontend/src/components/wizard/Step5Analytics.tsx`
   - render report bằng Markdown renderer thay vì `<pre>` raw text.

4. `frontend/src/api/mockApi.ts` và test workflow
   - bổ sung report Markdown vào mock artifact;
   - kiểm tra artifact thật sự giữ Markdown và source.

Lưu ý: fallback renderer hardcode vẫn được giữ để hệ thống không mất report khi provider LLM unavailable. Cần phân biệt rõ `LLM` và `FALLBACK` trong UI/trace.

## 5. Kế hoạch SOL kiểm tra và sửa

### P0 — Xác minh deploy và report Graph 3

1. Xác nhận commit/revision frontend và backend đang chạy live.
2. Tạo **workflow mới**, không dùng session/run cũ.
3. Chạy Graph 1 → review → Graph 2 → Graph 3 với dataset đã profile.
4. Kiểm tra:
   - Graph 3 job hoàn tất;
   - artifact `ANOMALY_REPORT` có `report_markdown` không rỗng;
   - `report_source` là `LLM` khi report writer thành công;
   - UI hiển thị heading/table/list đã render, không phải raw Markdown hay card placeholder.
5. Chạy lại khi provider timeout để xác nhận fallback có nhãn đúng và workflow không treo vô hạn.

### P1 — Điều tra latency và DeepAgent

1. Lấy trace của một run UI và một run CLI cùng dataset/version.
2. Tách thời gian: provider latency, retry, fallback, worker cold start, polling frontend.
3. Kiểm tra vì sao DeepAgent validation confidence fail; thêm test cho output validator.
4. Đặt giới hạn retry/timeout thống nhất và hiển thị trạng thái “fallback” thay vì “running” kéo dài.
5. Xác nhận một run thành công không gọi thêm legacy proposer ngoài fallback có chủ đích.

### P1 — Dataset/version/access

1. Audit lịch sử tạo lại `dataset-nyc-yellow-taxi-50k`: seed, import, worker startup, migration và audit log.
2. Không xóa lại cho đến khi xác định nguồn tạo; nếu là seed không mong muốn, chuyển seed sang explicit/dev-only.
3. Test version 1 → version 2:
   - version id tăng đúng;
   - profile snapshot, semantic contract, ruleset và DQ run trỏ đúng version;
   - không đọc nhầm dataset-level legacy profile.
4. Kiểm tra demo Steward chỉ có quyền READ trên dataset được cấp và quota API đúng policy.

### P1 — Session và browser E2E

1. Kiểm tra logout có xóa session/local selection hay không.
2. Phân biệt “restore latest workflow” với “start new run”.
3. Thêm nút/logic reset workflow khi người dùng muốn bắt đầu từ Graph 1.
4. Chạy browser harness sau khi cấp quyền remote debugging; lưu evidence cho từng stage.

### P1 — Các hạng mục sản phẩm và quyền demo

1. Kiểm tra logo Home ở tất cả wizard step và khi có active job.
2. Kiểm tra login demo được hiển thị an toàn, không render password thật trong DOM/log.
3. Kiểm tra public demo, role Steward, dataset access READ/MANAGE và API quota bằng tài khoản riêng.
4. Nếu credential không verify được, đối chiếu Supabase auth/account record trước khi thay đổi hash; ghi audit log cho mọi mutation.

## 6. Tiêu chí hoàn tất

- Không có push vào repo VinUni ngoài yêu cầu rõ ràng.
- Revision live được xác định và khớp source đã kiểm thử.
- Graph 3 report LLM hiển thị đúng nội dung Markdown generated; fallback chỉ xuất hiện khi LLM thực sự lỗi.
- Một fresh run đi xuyên suốt 6 bước mà không nhảy vào Graph 3 do session cũ.
- Rule proposer có trace latency rõ ràng; không bị kẹt ở 95% khi job đã lỗi/timeout.
- Dataset/version/access được kiểm tra bằng DB + audit log, không chỉ bằng UI.
