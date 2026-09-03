# Báo cáo rà soát pipeline Cloud bằng Browser Harness

**Ngày:** 2026-09-03  
**Frontend:** https://c3-app-028.vercel.app  
**Backend:** https://ridepulse-api-gbnhdahaya-as.a.run.app  
**Branch:** `deploy`  
**Phạm vi:** kiểm thử và báo cáo; không sửa code, không commit/push.

## 1. Dataset kiểm thử

| Dataset | Đặc tính và lý do chọn | Workflow |
|---|---|---|
| `cloud-smoke` | 8 dòng; dataset nhỏ, có lỗi null đã biết ở `fare_amount` | `workflow-701b1610ae6e4a8c8320` |
| `NYC Yellow Taxi Semantic 10k` | 10.000 dòng; cột số, phân loại, timestamp dạng chuỗi, missing values và giá trị âm ngoại lệ | `workflow-1a151f28f2d148e8b86c` |

Hai dataset đáp ứng mức tối thiểu của kế hoạch. Không chạy dataset thứ ba để tránh phát sinh thêm job sau khi đã có đủ bằng chứng.

## 2. Kết quả theo workflow

| Dataset | Graph 1A | Graph 1B | Graph 2 | Graph 3 |
|---|---|---|---|---|
| `cloud-smoke` | Thành công; semantic contract được review và confirm | Thành công; 4 proposal, duyệt 3, từ chối `fare_amount NOT NULL` | Thành công; 3 rule, 24 lượt kiểm tra, 0 vi phạm | Thành công; `INSUFFICIENT_HISTORY`, 0 hypothesis, report source `LLM` |
| `NYC 10k` | Thành công; 20 cột được suy diễn và contract được confirm | Node `SUCCEEDED` nhưng LLM timeout; chuyển `deterministic-policy-fallback`; UI/artifact chỉ còn 1 proposal | Thành công; `run_2d51d96f`, 10.000 dòng, 0 vi phạm | Thành công; `INSUFFICIENT_HISTORY`, 0 hypothesis, report source `FALLBACK` |

### Bằng chứng job chính

- `cloud-smoke` Graph 1B: `00d20db3-8df7-4a1d-a275-4f50273fa053`; `rule_proposer` khoảng 22,4 giây.
- `cloud-smoke` Graph 3: `4cf4a94d-68c2-46b4-ab5b-98432f82738d`; report writer khoảng 30,2 giây.
- `NYC` Graph 1B: `eddcd5e1-ed92-450e-a67e-cf8f50764940`; `rule_proposer` 132.019 ms.
- `NYC` Graph 2: `844bf05d-0dbb-48f6-8343-5da3cda3a523`; DQ run `run_2d51d96f`.
- `NYC` Graph 3: `77ea47de-4ad6-44c6-8c56-b33866cc3bc4`; anomaly run `anom-7ce36282b2fd`.

## 3. Lỗi kỹ thuật đã xác nhận

### P1 — Graph 1B timeout nhưng vẫn báo node thành công

**Tái hiện:** chạy Graph 1B trên `NYC Yellow Taxi Semantic 10k` sau khi confirm contract.

- Log ghi `Request timed out` trong `rule_proposer`.
- Node API vẫn có `status=SUCCEEDED`.
- Artifact `RULE_SET` ghi:

```text
proposal_generation_mode = deterministic-policy-fallback
proposal_fallback_reason = AgentWorkflowError: Graph 1B could not produce a valid structured response.
```

- Log fallback nói heuristic tạo 37 proposal, nhưng endpoint proposal và UI chỉ hiển thị/lưu 1 proposal (`vendor_id must be populated`). Đây là mismatch cần điều tra.
- Không có bằng chứng của legacy path. Graph 3 log vẫn ghi `mode=deepagent`.

Nguyên nhân code đã xác định tại `C:\Users\ADMIN\WorkPlace\Vinuni\AssignmentProject\P-028-deploy-fresh\src\services\rule_proposer_workflow.py:754`: exception của Graph 1B bị bắt và workflow tiếp tục bằng deterministic policy fallback.

### P1 — `violation_rate` trả về `null` dù có counts

Đã kiểm chứng trực tiếp API:

| DQ run | Checked | Failed | API `violation_rate` | Tính độc lập |
|---|---:|---:|---:|---:|
| `run_831e0ce5` (`cloud-smoke`) | 8 | 0 | `null` | `0 / 8 = 0%` |
| `run_2d51d96f` (`NYC`) | 10.000 | 0 | `null` | `0 / 10.000 = 0%` |

Nguyên nhân code đã xác định:

- `C:\Users\ADMIN\WorkPlace\Vinuni\AssignmentProject\P-028-deploy-fresh\src\services\job_runner.py:1372` khởi tạo `aggregate_rate=None`.
- `job_runner.py:1442` lưu giá trị này cho cả rule cấp dòng.
- `C:\Users\ADMIN\WorkPlace\Vinuni\AssignmentProject\P-028-deploy-fresh\src\api\routes.py:375` cho phép trả về `null`.

Frontend vẫn tính được tỷ lệ từ counts ở một số màn hình, nhưng API, report và agent có thể nhận `null`/`N/A`.

### P1 — Report fallback nhưng node vẫn `SUCCEEDED`

Trên `NYC` Graph 3:

- `report_writer`: khoảng 33,5 giây, node `SUCCEEDED`.
- Artifact `ANOMALY_REPORT`: `report_source=FALLBACK`.
- Log cho biết LLM trả về object reasoning không có Markdown hợp lệ; hệ thống dùng template tiếng Việt.
- Report UI cũng ghi rõ báo cáo được tạo bằng template, không dùng AI.

Số lần retry nội bộ chưa có bằng chứng vì metadata chưa lưu `attempt_count`. Một lượt live trước cùng môi trường cũng fallback do `Request timed out`, nên đường fallback vì timeout là có thật nhưng chưa đo được retry count.

## 4. Đánh giá semantic và UX

### Đã xác nhận là hợp lý

- `cloud-smoke`: contract nhận diện đúng các cột chính; không approve `fare_amount NOT NULL` vì profile có 12,5% null và chưa có bằng chứng nghiệp vụ rằng cột này bắt buộc.
- `NYC`: contract nhận diện hợp lý identifier, numeric, category, location, timestamp và currency; các giả định về USD, enum, timezone, null semantics và công thức `total_amount` được ghi rõ là cần xác nhận.
- Cả hai Graph 3 đều trả `INSUFFICIENT_HISTORY`; không có bằng chứng để kết luận có anomaly thực sự.

### P2 — Nội dung ngôn ngữ chưa nhất quán

- UI có thể hiển thị narrative tiếng Việt, nhưng các field canonical API như `title`, `description`, `evidence_summary` vẫn tiếng Anh.
- Nhánh fallback Graph 1B của NYC tạo title/rationale tiếng Anh.
- Report fallback tiếng Việt nhưng giữ technical enum như `STATISTICAL`, `ML`, `INSUFFICIENT_HISTORY`.

Đây là lỗi localization/UX, chưa phải lỗi tính toán semantic.

## 5. Vấn đề chưa đủ bằng chứng

- Chưa tái hiện được khẳng định panel Graph 3 lọc sai mọi kết quả có `checked_count < 100`. `cloud-smoke` có 8 dòng nhưng kết luận `INSUFFICIENT_HISTORY` phù hợp và không có rule failed để chứng minh bị lọc.
- Chưa chứng minh hypothesis suy diễn nguyên nhân sai; cả hai lượt đều có 0 hypothesis do thiếu lịch sử.
- Chưa xác định được retry count thực tế của LLM.
- Không có bằng chứng dataset legacy NYC 50k được chạy trong hai workflow này.

## 6. Kế hoạch sửa ưu tiên

### P0 — Bảo toàn semantic safety

- Khi Graph 1B fallback, đánh dấu workflow `DEGRADED` hoặc yêu cầu steward xác nhận rõ.
- Không tự động publish/tiếp tục với bộ rule fallback nếu chưa review.
- Lưu đầy đủ proposal count, fallback reason và trace liên quan.

### P1 — Chuẩn hóa số liệu và trạng thái

- Tính `violation_rate=failed_count/checked_count` cho rule cấp dòng.
- Quy định rõ trường hợp `checked_count=0` bằng trạng thái riêng, không dùng lẫn với `null`.
- Tách trạng thái job thành công khỏi trạng thái LLM/report thành công.
- Lưu `report_source`, `fallback_reason`, `attempt_count` và latency.
- Điều tra mismatch 37 proposal trong log so với 1 proposal trong artifact/UI.

### P2 — Localization và anomaly semantics

- Chuẩn hóa phần user-facing sang tiếng Việt, giữ technical identifiers cần thiết.
- Phân biệt rõ `NO_ANOMALY`, `INSUFFICIENT_SAMPLE`, `FILTERED_BY_POLICY` và `ERROR` trên Graph 3.

## 7. Regression test bắt buộc

1. `cloud-smoke` 8 dòng: kiểm tra null semantics, sample nhỏ và `INSUFFICIENT_HISTORY`.
2. `NYC 10k`: kiểm tra numeric/category/date/missing values và các giả định semantic.
3. Mock LLM timeout ở Graph 1B: xác nhận workflow không báo thành công bình thường khi fallback.
4. Mock LLM trả reasoning object/Markdown lỗi ở report writer: kiểm tra `report_source` và fallback reason.
5. Rule cấp dòng với `0/checked` và `failed/checked`: xác nhận tỷ lệ không còn `null` ngoài trường hợp `checked_count=0`.
6. Anomaly có `checked_count<100`: xác nhận UI phân biệt thiếu mẫu với không có bất thường.

**Kết luận:** Hai workflow đều chạy end-to-end đến Graph 3, nhưng chưa thể tuyên bố pipeline hoàn toàn ổn. Đã xác nhận các vấn đề về timeout/fallback, `violation_rate=null`, report fallback ẩn sau trạng thái `SUCCEEDED`, và localization chưa nhất quán.
