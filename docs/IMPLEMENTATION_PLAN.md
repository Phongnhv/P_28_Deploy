# RidePulse DQ — MVP Implementation Plan

> **Status:** Proposed / Not implemented
>
> **Strategy:** Vertical slice trước, mở rộng sau
>
> **Backlog:** [BACKLOG.md](./BACKLOG.md)

## Nguyên tắc triển khai

- Code hiện tại là starter template, không xem placeholder là product behavior hoàn chỉnh.
- Mỗi phase tạo ra một flow quan sát được, không chia việc chỉ theo folder/layer.
- Service thuần Python phải chạy/test được trước khi wrap bằng Dagster hoặc gọi từ UI.
- API/data contract được review trước implementation ảnh hưởng public behavior.
- Feature post-MVP không được đưa vào P0 khi chưa có quyết định mới.
- Một task có một owner; task P0 có reviewer khác owner.

## Phase 0 — Scope and setup

### Objective

Chốt input contract và tạo nền PostgreSQL có migration/test, chưa implement Agent/UI.

### Deliverables

- Product identity/config được đổi từ template sang RidePulse DQ.
- Dataset source/manifest được xác nhận.
- PostgreSQL chạy local; SQLAlchemy/Alembic có migration đầu tiên.
- Test environment có database isolation strategy.

### Tasks

- `MVP-001` — Product identity và config baseline.
- `MVP-002` — Database dependencies/settings.
- `MVP-003` — PostgreSQL Docker Compose service.
- `MVP-004` — Session, models nền và migration đầu tiên.
- `MVP-005` — Dataset manifest và deterministic sample contract.

### Dependencies

- Product/QA owner xác nhận dataset month và cách chia sẻ local cache.
- Backend owner xác nhận sync/async database driver.

### Owner role

Backend owner; Product/QA owner review dataset contract.

### Exit criteria

- [ ] API start được với PostgreSQL từ clean setup.
- [ ] Migration upgrade/downgrade chạy được trên database test.
- [ ] Manifest validation từ chối file/checksum không đúng.
- [ ] Test và lint hiện có vẫn pass.

### Risks

- Driver/database choice kéo theo refactor sớm.
- Data file lớn bị commit nhầm.
- `.env` của các thành viên không đồng nhất.

### Verification method

Chạy migration trên database rỗng, unit test manifest/config và full pytest/Ruff.

## Phase 1 — Stable project skeleton

### Objective

Tạo ingestion và profiling service có API mỏng, chạy được trên `dev_small`.

### Deliverables

- Idempotent chunked Parquet ingestion.
- Persisted ingestion run và dataset profile.
- Dataset/profile endpoints với validation và error response ổn định.

### Tasks

- `MVP-006` — Ingestion service.
- `MVP-007` — Dataset ingestion/list API.
- `MVP-008` — Profiling service.
- `MVP-009` — Profile trigger/read API.

### Dependencies

Phase 0 và local `dev_small` fixture.

### Owner role

Backend owner; Product/QA owner chạy acceptance test bằng sample manifest.

### Exit criteria

- [ ] 100k dòng được ingest với row count/checksum đúng.
- [ ] Re-run cùng manifest không nhân đôi dữ liệu.
- [ ] Profile có schema, null/distinct và numeric summaries.
- [ ] Route không chứa profiling business logic.

### Risks

- Load toàn bộ Parquet vào RAM.
- Type mapping giữa PyArrow/PostgreSQL không ổn định.
- Idempotency key chưa đủ rõ.

### Verification method

Integration test Parquet → PostgreSQL → profile và manual API call qua Swagger.

## Phase 2 — First end-to-end happy path

### Objective

Đi từ persisted profile đến proposal và HITL state transition chưa cần frontend.

### Deliverables

- Rule schemas cho allow-listed types.
- Aggregate-only evidence package.
- LangGraph proposal flow với mocked/live provider adapter.
- Proposal persistence và approve/edit/reject API.

### Tasks

- `MVP-010` — Rule Pydantic schemas.
- `MVP-011` — Evidence builder.
- `MVP-012` — LangGraph rule proposal nodes/graph.
- `MVP-013` — Proposal persistence và HITL lifecycle.

### Dependencies

Phase 1 profile contract và quyết định LLM provider.

### Owner role

Agent owner cho evidence/graph; Backend owner cho persistence/API.

### Exit criteria

- [ ] Mocked LLM proposal hợp lệ được persist ở `PROPOSED`.
- [ ] Output malformed/evidence ref giả bị reject.
- [ ] Approve/edit/reject tạo audit event.
- [ ] Raw trip rows không xuất hiện trong LLM payload/log.

### Risks

- Prompt/schema coupling làm test không ổn định.
- Evidence package quá lớn.
- HITL bị implement bằng suspended in-memory graph.

### Verification method

Unit test từng node/service, API state transition tests và payload privacy assertion.

## Phase 3 — API and data contract stabilization

### Objective

Hoàn thành approved rule → safe SQL → persisted DQ result.

### Deliverables

- Compiler core và năm rule templates.
- Metadata identifier validation.
- Read-only rule runner.
- DQ run API và stable error envelope.

### Tasks

- `MVP-014` — SQL compiler foundation.
- `MVP-015` — MVP rule templates.
- `MVP-016` — Read-only rule runner.
- `MVP-017` — DQ run/results API.

### Dependencies

Phase 2 approved rule schema và database roles.

### Owner role

Backend owner; Agent owner review schema/compiler boundary.

### Exit criteria

- [ ] 100% supported fixture rules compile thành một `SELECT` statement.
- [ ] Pending/rejected rule không compile hoặc execute.
- [ ] DDL/DML, unknown identifier và multi-statement input bị reject.
- [ ] Result lưu failed/eligible count và bounded failed IDs.

### Risks

- SQL injection qua identifier/operator.
- Database role có quyền mutate.
- Rule semantics không rõ với `NULL`/empty dataset.

### Verification method

Compiler unit tests, security negative tests và PostgreSQL integration test bằng
read-only credential.

## Phase 4 — Error handling and reliability

### Objective

Làm core flow idempotent, quan sát được và phục hồi được trước khi nối UI.

### Deliverables

- Stable API error model.
- Timeout/retry boundary cho LLM và job.
- Correlation IDs và structured logs.
- Integration/regression suite cho core flow.

### Tasks

- `MVP-023` — Error envelope, idempotency và structured logging.
- `MVP-024` — Core integration/regression tests.
- `DEMO-001` — Readiness health checks.

### Dependencies

Phases 1–3.

### Owner role

Backend owner; Agent owner phụ trách LLM failure cases.

### Exit criteria

- [ ] Duplicate trigger không tạo duplicate state ngoài contract.
- [ ] Database/LLM timeout trả status có thể debug và retry an toàn.
- [ ] Core integration suite pass không cần live paid API.
- [ ] Log có correlation ID và không chứa secret/raw rows.

### Risks

- Retry gây duplicate ingestion/run.
- Exception detail hiện tại bị trả thẳng cho client.

### Verification method

Fault-injection tests với mocked timeout/database error, full pytest và log inspection.

## Phase 5 — UI/demo polish

### Objective

Cho Data Steward hoàn thành core journey qua browser.

### Deliverables

- React + Vite + Ant Design scaffold **[NEEDS CONFIRMATION]**.
- Dataset/Profile screen.
- Rule Review screen.
- DQ Results screen với loading/empty/error states.

### Tasks

- `MVP-018` — Frontend scaffold và API client.
- `MVP-019` — Dataset/Profile/Rule Review flow.
- `MVP-020` — DQ Results và audit summary.
- `DEMO-002` — UI empty/error/loading polish.

### Dependencies

Stable Phase 3 contracts và Phase 4 error model.

### Owner role

UI/Integration owner; Product/QA owner manual acceptance.

### Exit criteria

- [ ] Core journey chạy qua UI không cần database edit/CLI.
- [ ] Loading, empty và error state có thể reproduce.
- [ ] UI không hiển thị metric giả như kết quả thật.
- [ ] Screenshot và manual test evidence được lưu.

### Risks

- Prototype cũ tạo kỳ vọng feature ngoài scope.
- UI bắt đầu trước contract ổn định gây rework.

### Verification method

Frontend checks khi được cấu hình, API integration test và manual checklist trên browser.

## Phase 6 — Evaluation and deployment

### Objective

Đóng gói core services thành batch job, kiểm chứng 100k–1M dòng và chuẩn bị demo.

### Deliverables

- Dagster asset/job wrappers và một schedule disabled-by-default.
- Docker Compose local stack hoàn chỉnh.
- `dev_medium`/`demo` benchmark có hardware context.
- End-to-end smoke test và demo runbook.

### Tasks

- `MVP-021` — Dagster wrappers.
- `MVP-022` — End-to-end smoke test.
- `DEMO-003` — Medium/demo benchmark.
- `DEMO-004` — Demo evidence và recovery drill.

### Dependencies

Phases 0–5; team xác nhận Dagster là MVP requirement.

### Owner role

UI/Integration owner cho runtime; Backend/Agent owners cho job adapters; Product/QA
owner chạy demo acceptance.

### Exit criteria

- [ ] Clean checkout có thể start stack theo `RUNBOOK.md`.
- [ ] Core flow pass trên `dev_small` và `dev_medium`.
- [ ] Demo không phụ thuộc live data download.
- [ ] Test, lint, smoke test và manual demo checklist pass.

### Risks

- Docker/Dagster mở rộng scope quá muộn.
- Hardware demo không đủ cho 1M dòng.
- Live LLM/network làm demo không ổn định.

### Verification method

Clean-machine rehearsal, Docker health checks, timed benchmark và recorded smoke result.

## Sau MVP

Chỉ bắt đầu `EXT-*` sau khi Phase 6 exit criteria đạt: synthetic evaluation, robust
statistics, Isolation Forest, diagnosis, Viewer/RBAC, notification và cloud deployment.

## Definition of Done của MVP

- [ ] Dataset pinned và local cache có checksum.
- [ ] 100k/300k và tối đa 1M chạy cùng functional contract.
- [ ] Profile aggregate persist và hiển thị được.
- [ ] Proposal schema-valid, có valid evidence refs.
- [ ] HITL và audit đầy đủ.
- [ ] Chỉ approved rule chạy bằng read-only DB role.
- [ ] UI core journey và Dagster/manual batch chạy được.
- [ ] Full automated tests, lint và demo smoke pass.
- [ ] README, architecture, contracts và runbook khớp implementation thực tế.
