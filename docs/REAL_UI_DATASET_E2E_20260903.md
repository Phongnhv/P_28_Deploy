# Kiểm thử UI thật ngày 2026-09-03

## Môi trường và phạm vi

- FE `http://127.0.0.1:5179`, API `http://127.0.0.1:8019`, dữ liệu và workflow trong Supabase hiện có.
- `AGENT_MODE=graph`, `gpt-5.6-luna`, rule proposer và investigation dùng DeepAgent, không bật legacy proposer fallback. LangSmith tracing bật cho `ridepulse-dq-dev`.
- Chọn dataset có sẵn qua UI, không upload/import lại, không di chuyển source cloud, không chạy migration hoặc seed. Backend local tắt lifespan để không chạy DDL/seed trên Supabase.
- Dataset `e2e_test_datapulse`, ID `dataset-import-151ff2348b12478793ac`, source version nội bộ `dv-89c39fe704244492859879ba`.
- Source checksum `72c4bde7bad9c2ae649fffd7bc117e0318251b68edf2a7a73e11290a73f211b2`. File local hiện có đã được đối chiếu checksum. Điều này không khôi phục locator local trên Cloud Run.
- File độc lập: 12 dòng, 6 cột `customer_id,email,age,amount,signup_date,status`. Email có 1 null; customer_id có 11 giá trị phân biệt; age 0–150; amount -15–5000, có 1 giá trị âm. Thống kê lưu ở `scratch/real-dataset-e2e-independent.json`.

## Các lỗi được tìm bằng UI/trace và đã sửa local

1. Dispatcher giữ connection khi gọi worker inline, cộng với API đọc lại ORM object sau commit làm cạn pool Supabase (size 2). Giải phóng transaction trước dispatch, giữ scalar job/workflow ID để enqueue; ingestion cũng đóng lookup session trước gọi worker. Regression kiểm tra pool chỉ có 1 connection.
2. Luna sinh quan hệ `signup_date <= signup_date` để thay cho so sánh ngày hiện tại. Bổ sung prompt và kiểm tra cột/quan hệ: đúng toàn bộ cột profile, hai vế khác nhau và thuộc schema; không nới validator. Heuristic semantic fallback được nhận diện thay vì gắn nhãn thành công LLM.
3. Graph 1B mang profile đúng B dưới key cố định `source_rows`, khác key semantic. Đổi key digest về dataset ID. Sau sửa này, trace lộ thêm candidate IDs do semantic builder tạo khác IDs dashboard; giữ nguyên candidate ID, tham số và evidence do dashboard đã tạo. Không sinh NOT_NULL cho cột được semantic xác nhận nullable.
4. API/UI đổi validity chưa đo từ `null` sang `0%`. Giữ null xuyên snapshot/evidence/API, UI hiển thị `—`, không kéo điểm tổng hợp xuống vì metric chưa đo.
5. UI Graph 2 lấy execution gần nhất toàn dataset, hiển thị kết quả workflow cũ trong workflow mới. Thêm query theo workflow ID và loại execution stale; xóa kết quả cũ khi workflow chưa có execution.
6. Report writer ghép cả Responses API reasoning block vào Markdown. Chỉ lấy text/output_text; không stringify reasoning/tool blocks. Bổ sung số dòng từ profile đã pin và phân biệt số lượt kiểm tra qua rules với số dòng dataset.

## Lượt đầu

Workflow `workflow-2aaa77147c03c698d734727697c2c0ef` đã COMPLETED qua các thao tác UI. Job profile đầu tiên bị kẹt do pool, được khôi phục qua dispatcher sau sửa; sau đó bấm Continue trên UI để chạy lại profile thành công.

- Graph 1A: các lời gọi ChatOpenAI dùng Luna thật, đúng ID B và đúng 6 cột; không có ID dataset taxi A trong input. Trace đầu `01a06659-0f81-7213-9c12-c332ab3ae870` giúp phát hiện quan hệ sai. Chạy lại sau sửa: relationships rỗng, age/amount đúng range, nghiệp vụ chưa chắc chắn nằm ở assumptions.
- Graph 1B trace `01a0665d-4666-73d0-a4a6-7fdc56ca3416`: DeepAgent middleware hiện diện; tạo 4 proposals không fallback. Từ chối email NOT_NULL vì semantic cho phép null; duyệt 3 NOT_NULL trên amount/signup_date/status.
- Graph 2 `run_13193e91`: 3 rules × 12 dòng = 36 lượt kiểm tra, 0 vi phạm; source version/profile/checksum đúng B. Đối chiếu file độc lập khớp. Đây là executor file/SQL dashboard (`G2_DIRECT`), không phải bằng chứng đã chạy dbt CLI.
- Graph 3 trace `01a06666-b973-7033-8428-fb34e09ce714`: decision INSUFFICIENT_HISTORY, không cần gọi LLM hypothesis; report writer có gọi Luna. Report text nêu chưa đủ lịch sử, không khẳng định dữ liệu sạch toàn diện. Lượt này giúp phát hiện lỗi reasoning-block trong Markdown.

## Lượt kiểm lại sau sửa

Workflow `workflow-5453273ca5499cc90e595aeba3ca43c8` tạo profile mới qua UI trên cùng source B. Graph 1A thật có 12 dòng/6 cột, validity null, relationships rỗng.

Graph 1B trace `01a0666c-ee5b-7d70-9378-6d0a023e9100` chứng minh key digest đã là dataset B, nhưng normalizer từ chối candidate IDs mới nên dùng deterministic policy fallback. Đây là lỗi nối candidate ID được sửa tiếp ở trên; không phải provider không trả output và không phải bằng chứng Luna hết lỗi.

Kết quả hoàn tất:

- Graph 1B chạy lại qua UI, trace `01a0666f-cbea-7bb3-a227-3652d970e7b7`: Luna/DeepAgent trả 5 proposals đúng candidate ID, không fallback. UI nhận 3 vì catalog evidence của versioned snapshot thiếu policy/min/max/full-distinct refs. Đã bổ sung catalog từ chính snapshot; replay read-only output Luna thật qua normalizer sau sửa chấp nhận 5/5. Đây là replay kiểm adapter, chưa phải một lượt UI sinh lại 5 rules.
- Duyệt 3 rules NOT_NULL (amount/signup_date/status) và thêm rule uniqueness `customer_id` qua UI để kiểm nhánh có vi phạm. Không thay nội dung dataset. Rule manual `rv_manual-29ef16de`.
- Graph 2 `run_f3760b61`: 4 rules × 12 dòng = 48 lượt kiểm tra; 3 PASS, 1 FAIL. Hai dòng vi phạm customer_id là row IDs 10 và 11, khớp pandas độc lập. Profile có một bản ghi trùng dư, còn uniqueness rule đếm cả hai dòng thuộc nhóm trùng.
- Dataset/version/profile/checksum lưu trên execution khớp source B. Semantic chỉ có 6 cột của B, các ranges/nullability khớp profile; không còn self-relationship. Đây là kiểm chứng scope và thống kê; các suy đoán nghiệp vụ vẫn cần steward xác nhận.
- Graph 3 lần đầu trace `01a06674-afde-71c3-9b62-45ecafadcc9f` phát hiện thêm lỗi: get_anomaly_case chạy trước persist, fallback về execution mới nhất và tự suy CRITICAL. Đã bỏ hoàn toàn lookup gần đúng/latest và decision giả. Tool được bind vào detector state hiện tại; ID khác bị từ chối. Test có execution khác ở DB vẫn trả NOT_FOUND cho case không tồn tại.
- Graph 3 chạy lại qua UI sau sửa: trace `01a0667e-531f-7ec1-a5fa-91b9c917d7a1`, job `83bbfe0a-8ef0-4270-9cd8-90f6d53328f3` SUCCEEDED. get_anomaly_case trả đúng B, `run_f3760b61`, ANOMALY 0.8 và 9 signals. Profile tool pin `profile-workflow-5453273ca5499cc90e595aeba3ca43c8`; metric/history/results đều đúng B và execution này.
- Hypothesis DeepAgent 24.4 giây, report writer Luna 34.7 giây. Report kết luận ANOMALY/HIGH, 12 dòng, 48 lượt kiểm tra, 2 dòng vi phạm; không còn mâu thuẫn CRITICAL hoặc reasoning block. Giả thuyết duplicate customer_id có confidence 97%; thiếu lịch sử được ghi rõ, không suy thành dữ liệu sạch.
- UI Graph3/Results từng dùng danh sách thống kê đã lọc (tối thiểu 100 dòng) để báo No Anomalies dù canonical decision ANOMALY. Sửa UI đọc decision từ artifact của đúng execution, hiển thị riêng số signals đã lọc; không thay threshold detector. Report panel làm mới khi artifact thay đổi. Màn Results đã kiểm bằng UI: 3/4 PASS, 48 checks, 2 failed, ANOMALY, đúng e2e_test_datapulse.

Bằng chứng local: `scratch/workflow-5453273ca5499cc90e595aeba3ca43c8-evidence.json`, `scratch/real-e2e-graph3-corrected-trace.json`, `scratch/real-e2e-final-steward-report.md`, `scratch/real-e2e-results.png`, `scratch/real-e2e-steward-report.png`.

Kiểm tra mã: 78 regression tests pass; thêm bộ 17 tests (có overlap với 78) pass sau sửa case scope và giữ nguyên lọc anomaly. Frontend build, Ruff các file sửa và git diff --check pass. Warning hiện có: Pydantic schema field shadow và bundle frontend lớn.

## Dataset NYC50k

Bản ghi `dataset-nyc-yellow-taxi-50k` vẫn tồn tại trong Supabase với trạng thái REGISTERED. `init_db` có thể tự tạo lại demo khi chạy local/dev; lần chạy kiểm thử này đặt `SEED_LEGACY_DEMO_DATASET=false`. Có bằng chứng về cơ chế tái tạo, chưa có bằng chứng xác định tiến trình nào đã tạo lại bản ghi lịch sử. Không tự xóa dataset này.

## Giới hạn

- Thao tác E2E tạo workflow, profile, rules và kết quả trên Supabase theo yêu cầu kiểm thử; không thay đổi nội dung source.
- Bản sửa chưa commit/push/deploy. Dataset B có source local hợp lệ trên máy này, không chứng minh instance cloud đọc được cùng locator.
- Cần phân biệt fallback trong từng lượt và node thực sự gọi model với node bị bỏ qua hợp lệ vì không có anomaly.
