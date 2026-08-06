# RidePulse DQ — Product Specification

> **Document status:** Proposed, dùng để chốt MVP
>
> **Implementation status:** Not implemented; repository hiện là starter template
>
> **Nguồn kế hoạch kỹ thuật:** [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md)

## 1. Problem statement

Data Steward cần kiểm tra dataset mobility trước khi dữ liệu được dùng cho báo cáo hoặc
phân tích. Quy trình viết từng SQL data-quality check bằng tay chậm, khó tái sử dụng và
khó audit. RidePulse DQ đề xuất một workflow có LLM hỗ trợ đọc aggregate profile, đề
xuất rule có cấu trúc, bắt buộc con người duyệt và chỉ sau đó mới chạy rule read-only.

Repository hiện chưa giải quyết bài toán này. Code chỉ có FastAPI/LangGraph demo trả
response dạng placeholder; chưa có dataset, persistence, profiling, HITL, rule engine
hay frontend tích hợp.

## 2. Target user

### Primary user — Data Steward

- Chọn và ingest dataset đã được team cấu hình.
- Xem profile dữ liệu.
- Review, edit, approve hoặc reject rule do Agent đề xuất.
- Chạy approved rules và xem kết quả.

### Supporting role — Product/QA owner

- Chốt dataset, user flow và acceptance criteria.
- Chạy manual test và xác nhận demo flow.

Viewer, Data Officer và production administrator không thuộc MVP đầu tiên.

## 3. Team roles

| Role | Responsibility |
|---|---|
| Product/QA owner | Scope, user flow, datasets, acceptance criteria, manual testing |
| Backend owner | API, schemas, services, data access |
| Agent owner | LangGraph, prompts, tools, evaluation |
| UI/Integration owner | UI, API integration, demo flow, deployment support |

Mỗi task có một owner chính. Task P0 cần reviewer khác owner. Không merge task thiếu
acceptance criteria hoặc verification result.

## 4. MVP objective

Tạo một vertical slice chạy được trên 100,000–1,000,000 dòng:

```text
local NYC TLC Parquet
  -> ingest PostgreSQL
  -> aggregate profile
  -> Agent proposes structured rules
  -> Steward reviews rules
  -> approved rules compile to safe SELECT SQL
  -> read-only execution
  -> result dashboard and audit trail
```

MVP ưu tiên tính đúng, an toàn và tái lập. MVP không tối ưu cho production scale và
không dùng ML để thay thế deterministic data-quality rules.

## 5. Dataset

### Proposed

- Source: NYC TLC Yellow Taxi Trip Records.
- Initial candidate: `yellow_tripdata_2024-01.parquet` **[NEEDS CONFIRMATION]**.
- `dev_small`: deterministic sample 100,000 dòng.
- `dev_medium`: deterministic sample 300,000 dòng.
- `demo`: tối đa 1,000,000 dòng.
- Download một lần; demo sử dụng local cache và pinned checksum.

Yellow Taxi là taxi/mobility proxy, không phải dữ liệu của một ride-hailing platform
cụ thể. Chicago datasets bị loại khỏi scope.

### Current

Không có data file, manifest, ingestion code hoặc database table trong repository.

## 6. User journey

1. Data Steward mở Dataset screen và chọn source đã cấu hình.
2. Steward trigger ingestion/profile job và theo dõi status.
3. Hệ thống hiển thị row count, schema, null/distinct rate và numeric statistics.
4. Steward yêu cầu Agent đề xuất rule từ aggregate evidence.
5. Hệ thống validate và lưu proposal ở trạng thái `PROPOSED`.
6. Steward approve, edit hoặc reject từng proposal.
7. Hệ thống compile approved rule bằng template allow-list.
8. Steward chạy DQ run; database role read-only thực thi các `SELECT` checks.
9. Dashboard hiển thị pass/fail, failed/eligible count, Data Health Score và audit log.

## 7. Must-have features

Tất cả mục dưới đây là **Proposed / Not implemented**:

| ID | Feature | Outcome |
|---|---|---|
| F01 | Deterministic ingestion | Local Parquet được ingest idempotent vào PostgreSQL |
| F02 | Dataset profiling | Profile aggregate được persist và truy xuất qua API |
| F03 | Structured rule proposal | LangGraph/LLM trả rule đúng Pydantic schema, có evidence refs |
| F04 | HITL review | Steward approve/edit/reject; mọi transition được audit |
| F05 | Safe rule compiler | Chỉ allow-listed rule sinh `SELECT`; không nhận custom SQL |
| F06 | Read-only execution | Chỉ approved rules chạy bằng DB role không có quyền mutate raw |
| F07 | MVP dashboard | UI hỗ trợ dataset, profile, rule review và run results |
| F08 | Batch orchestration | Core services được Dagster wrap, có manual run và một schedule |

## 8. Nice-to-have sau khi core flow ổn định

- Group profile và robust statistical drift.
- Synthetic corruption generator và hidden evaluation manifest.
- Root-cause explanation dựa trên persisted evidence.
- Viewer read-only UI.
- Slack/webhook notification.
- Cloud deployment.
- Performance tuning trên hơn 1M dòng.

Các mục này không được tự động kéo vào task P0.

## 9. Explicit out of scope

- Chicago datasets.
- Streaming/Kafka/realtime anomaly detection.
- Fine-tune LLM hoặc train supervised anomaly model.
- RAG/vector database trong MVP.
- dbt/Great Expectations trong MVP.
- Isolation Forest trong MVP core.
- Multi-tenant RBAC và enterprise warehouse connectors.
- Gửi raw trip rows cho LLM.
- LLM-generated arbitrary SQL.
- Tự động sửa, xóa hoặc update raw data.

## 10. Business and safety rules

1. Raw source và `trips_raw` là immutable trong application workflow.
2. Profiler được scan dữ liệu; LLM chỉ nhận aggregate evidence.
3. Proposal chưa `APPROVED` không được compile hoặc execute.
4. Identifier SQL phải resolve từ metadata; không dùng raw string do LLM cung cấp.
5. Rule runner chỉ có quyền `SELECT` và có statement timeout.
6. Mọi state transition và execution phải có audit record.
7. Lỗi không được lộ credential, system prompt hoặc raw sensitive data.

## 11. Success criteria

| Criterion | Target MVP |
|---|---:|
| Supported rule compile rate | 100% |
| Approved-rule execution rate | 100% |
| Unapproved-rule execution | 0 |
| Mutating query execution | 0 |
| Deterministic re-run mismatch | 0 |
| Rule transition audit coverage | 100% |
| Raw records sent to LLM | 0 |

Performance target cụ thể chỉ được chốt sau benchmark `dev_small` trên hardware demo.

Data Health Score đề xuất:

```text
sum(severity_weight(rule) * (1 - failed_rows / eligible_rows))
----------------------------------------------------------------
sum(severity_weight(rule))
```

Severity weights và behavior khi chưa có eligible rule là **[NEEDS CONFIRMATION]**;
UI luôn phải hiển thị failed/eligible counts bên cạnh score.

## 12. Demo scenario

1. Start stack từ clean checkout theo `docs/RUNBOOK.md`.
2. Ingest local `dev_small` dataset; hiển thị checksum và row count.
3. Chạy profiling và mở profile screen.
4. Yêu cầu proposal; hiển thị evidence references.
5. Approve một rule, edit một rule và reject một rule.
6. Chạy DQ checks; chứng minh rejected rule không chạy.
7. Mở results và audit log.
8. Chạy smoke test để chứng minh flow có thể tái lập.

Demo không phụ thuộc network download hoặc live LLM bắt buộc; một deterministic LLM
stub chỉ được dùng trong demo fallback nếu UI ghi rõ đó là fallback mode.

## 13. Open Questions

1. Team có xác nhận dùng January 2024 làm file nguồn đầu tiên không?
2. LLM provider/model chính thức và ngân sách token là gì?
3. MVP bắt buộc React + Ant Design hay được phép nâng cấp prototype `ui_test` trước?
4. Dagster schedule có bắt buộc trong demo cuối hay chỉ cần manual job?
5. Hardware demo và thời gian ingest/profile chấp nhận được là bao nhiêu?
6. Ai giữ vai trò owner/reviewer cho từng nhóm task?
7. Có yêu cầu authentication tối thiểu trước demo không?
8. Dataset local cache sẽ được phân phối cho team bằng cách nào mà không commit file lớn?
9. Giá trị AI logging đang có trong `.env.example` có được xác nhận là public/shared
   credential của chương trình không? Nếu là secret thật, team phải rotate và xóa khỏi history.
