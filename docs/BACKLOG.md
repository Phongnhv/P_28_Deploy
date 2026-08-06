# RidePulse DQ — MVP Backlog

> **Status:** Proposed
>
> **Quy ước:** `P0` bắt buộc cho MVP; `P1` cần cho demo tốt; `P2` sau MVP.
>
> Estimate là planning estimate, không phải deadline.

Mỗi task có một owner chính. Task P0 cần reviewer khác owner. `Done` chỉ được dùng
khi acceptance criteria và verification result đã được ghi nhận.

## P0 — Bắt buộc để MVP chạy

### MVP-001 — Đổi product identity khỏi starter template

- **Owner:** Backend owner
- **Reviewer:** Product/QA owner
- **Status:** Ready
- **Dependencies:** None
- **Estimate:** 0.5d
- **Related files:** `src/main.py`, `src/config.py`, `.env.example`, tests liên quan
- **Acceptance criteria:**
  - [ ] App/OpenAPI/health dùng tên RidePulse DQ nhất quán.
  - [ ] Không thay đổi behavior endpoint ngoài identity/config đã duyệt.
  - [ ] Config tests và API regression tests pass.
- **Verification:** `.\.venv\Scripts\python.exe -m pytest tests -v`

### MVP-002 — Thêm database dependencies và settings

- **Owner:** Backend owner
- **Reviewer:** Agent owner
- **Status:** Ready
- **Dependencies:** MVP-001
- **Estimate:** 0.5d
- **Related files:** `requirements.txt`, `src/config.py`, `.env.example`, `tests/unit/test_config.py`
- **Acceptance criteria:**
  - [ ] SQLAlchemy, Alembic và PostgreSQL driver được pin theo policy của team.
  - [ ] `DATABASE_URL` được validate; không có credential hard-code.
  - [ ] Test config hợp lệ/không hợp lệ pass.
- **Verification:** `.\.venv\Scripts\python.exe -m pytest tests/unit/test_config.py -v`

### MVP-003 — Thêm PostgreSQL vào Docker Compose

- **Owner:** Backend owner
- **Reviewer:** UI/Integration owner
- **Status:** Blocked
- **Dependencies:** MVP-002
- **Estimate:** 0.5d
- **Related files:** `docker-compose.yml`, `.env.example`, `docs/RUNBOOK.md`
- **Acceptance criteria:**
  - [ ] PostgreSQL có named volume, healthcheck và credential qua env.
  - [ ] Backend chờ database healthy theo cấu hình được hỗ trợ.
  - [ ] `docker compose config` hợp lệ.
- **Verification:** `docker compose config`

### MVP-004 — Tạo DB session và migration đầu tiên

- **Owner:** Backend owner
- **Reviewer:** Agent owner
- **Status:** Blocked
- **Dependencies:** MVP-002, MVP-003
- **Estimate:** 1d
- **Related files:** `src/db/`, `alembic.ini`, `tests/integration/`
- **Acceptance criteria:**
  - [ ] Session lifecycle không leak connection.
  - [ ] Migration tạo/xóa được schema nền trên database rỗng.
  - [ ] Integration test dùng database riêng, không chạm dev data.
- **Verification:** `alembic upgrade head; .\.venv\Scripts\python.exe -m pytest tests/integration/test_database.py -v`

### MVP-005 — Định nghĩa dataset manifest và deterministic sample

- **Owner:** Product/QA owner
- **Reviewer:** Backend owner
- **Status:** Blocked — cần xác nhận month/source distribution
- **Dependencies:** None
- **Estimate:** 0.5d
- **Related files:** `data/manifest.json`, `tests/fixtures/`, `docs/DATA_MODEL.md`
- **Acceptance criteria:**
  - [ ] Manifest có source URL, month, local path, size, SHA-256, row profile và seed.
  - [ ] File lớn và sample runtime được gitignore.
  - [ ] Cùng source/seed tạo cùng `source_row_id` và sample.
- **Verification:** `.\.venv\Scripts\python.exe -m pytest tests/unit/test_dataset_manifest.py -v`

### MVP-006 — Implement chunked ingestion service

- **Owner:** Backend owner
- **Reviewer:** Product/QA owner
- **Status:** Blocked
- **Dependencies:** MVP-004, MVP-005
- **Estimate:** 1d
- **Related files:** `src/services/ingestion.py`, `tests/integration/test_ingestion.py`
- **Acceptance criteria:**
  - [ ] `dev_small` được ingest theo chunk, không cần load toàn bộ file vào RAM.
  - [ ] Row count/checksum/batch ID được persist.
  - [ ] Re-run cùng manifest idempotent; failure không để run giả `SUCCEEDED`.
- **Verification:** `.\.venv\Scripts\python.exe -m pytest tests/integration/test_ingestion.py -v`

### MVP-007 — Thêm dataset ingestion/list API

- **Owner:** Backend owner
- **Reviewer:** Product/QA owner
- **Status:** Blocked
- **Dependencies:** MVP-006
- **Estimate:** 0.5d
- **Related files:** `src/api/routes/datasets.py`, `src/models/api.py`, `tests/test_api/test_datasets.py`
- **Acceptance criteria:**
  - [ ] Request/response/status code khớp `docs/API_CONTRACT.md`.
  - [ ] Invalid manifest và duplicate request trả lỗi ổn định theo contract.
  - [ ] Route chỉ gọi service, không chứa ingestion logic.
- **Verification:** `.\.venv\Scripts\python.exe -m pytest tests/test_api/test_datasets.py -v`

### MVP-008 — Implement profiling service

- **Owner:** Backend owner
- **Reviewer:** Agent owner
- **Status:** Blocked
- **Dependencies:** MVP-006
- **Estimate:** 1d
- **Related files:** `src/services/profiling.py`, `src/models/profiles.py`, `tests/integration/test_profiling.py`
- **Acceptance criteria:**
  - [ ] Profile có row count, schema, null/distinct rate và numeric summary đã định nghĩa.
  - [ ] Profile được version theo dataset/batch.
  - [ ] Empty table và all-null column có output xác định, không crash.
- **Verification:** `.\.venv\Scripts\python.exe -m pytest tests/integration/test_profiling.py -v`

### MVP-009 — Thêm profile trigger/read API

- **Owner:** Backend owner
- **Reviewer:** Product/QA owner
- **Status:** Blocked
- **Dependencies:** MVP-007, MVP-008
- **Estimate:** 0.5d
- **Related files:** `src/api/routes/profiles.py`, `tests/test_api/test_profiles.py`
- **Acceptance criteria:**
  - [ ] Trigger trả job/profile status không block request dài.
  - [ ] Read endpoint trả đúng version hoặc 404 ổn định.
  - [ ] API tests bao phủ happy, missing và invalid ID.
- **Verification:** `.\.venv\Scripts\python.exe -m pytest tests/test_api/test_profiles.py -v`

### MVP-010 — Định nghĩa structured rule schemas

- **Owner:** Agent owner
- **Reviewer:** Backend owner
- **Status:** Ready
- **Dependencies:** None
- **Estimate:** 1d
- **Related files:** `src/models/rules.py`, `tests/unit/test_rule_schemas.py`
- **Acceptance criteria:**
  - [ ] Có schema cho `not_null`, `numeric_range`, `accepted_values`, `cross_field_comparison`, `duplicate_fingerprint`.
  - [ ] Severity, parameters và `evidence_refs` được validate chặt.
  - [ ] Custom SQL/unknown rule/operator bị reject.
- **Verification:** `.\.venv\Scripts\python.exe -m pytest tests/unit/test_rule_schemas.py -v`

### MVP-011 — Build aggregate-only evidence package

- **Owner:** Agent owner
- **Reviewer:** Product/QA owner
- **Status:** Blocked
- **Dependencies:** MVP-008, MVP-010
- **Estimate:** 1d
- **Related files:** `src/services/evidence.py`, `tests/unit/test_evidence.py`
- **Acceptance criteria:**
  - [ ] Evidence chỉ dùng schema/profile/data dictionary/approved rule.
  - [ ] Có size/token guard và stable evidence keys.
  - [ ] Test chứng minh raw trip rows không xuất hiện trong payload.
- **Verification:** `.\.venv\Scripts\python.exe -m pytest tests/unit/test_evidence.py -v`

### MVP-012 — Implement LangGraph rule proposal flow

- **Owner:** Agent owner
- **Reviewer:** Backend owner
- **Status:** Blocked — cần xác nhận LLM provider
- **Dependencies:** MVP-010, MVP-011
- **Estimate:** 1d
- **Related files:** `src/agents/graph.py`, `src/agents/state.py`, `src/agents/nodes/`, `tests/test_agents/`
- **Acceptance criteria:**
  - [ ] Graph load evidence → propose → validate và trả typed proposals.
  - [ ] Unit test dùng mocked LLM, không gọi API thật.
  - [ ] Malformed output/timeout tạo failure state có thể xử lý.
- **Verification:** `.\.venv\Scripts\python.exe -m pytest tests/test_agents -v`

### MVP-013 — Persist proposals và HITL lifecycle

- **Owner:** Backend owner
- **Reviewer:** Agent owner
- **Status:** Blocked
- **Dependencies:** MVP-004, MVP-012
- **Estimate:** 1d
- **Related files:** `src/services/rules.py`, `src/api/routes/rules.py`, `tests/integration/test_rule_lifecycle.py`
- **Acceptance criteria:**
  - [ ] Proposal bắt đầu ở `PROPOSED`; approve/edit/reject đúng state machine.
  - [ ] Invalid transition trả conflict và không đổi dữ liệu.
  - [ ] Mỗi transition có actor/timestamp/before/after audit record.
- **Verification:** `.\.venv\Scripts\python.exe -m pytest tests/integration/test_rule_lifecycle.py -v`

### MVP-014 — Implement SQL compiler foundation

- **Owner:** Backend owner
- **Reviewer:** Agent owner
- **Status:** Blocked
- **Dependencies:** MVP-010, MVP-013
- **Estimate:** 1d
- **Related files:** `src/services/rule_compiler.py`, `tests/unit/test_rule_compiler.py`
- **Acceptance criteria:**
  - [ ] Compiler nhận typed rule, không nhận arbitrary SQL string.
  - [ ] Table/column resolve từ metadata allow-list.
  - [ ] Unknown identifier, comment, multi-statement và DDL/DML bị reject.
- **Verification:** `.\.venv\Scripts\python.exe -m pytest tests/unit/test_rule_compiler.py -v`

### MVP-015 — Thêm năm MVP rule templates

- **Owner:** Backend owner
- **Reviewer:** Product/QA owner
- **Status:** Blocked
- **Dependencies:** MVP-014
- **Estimate:** 1d
- **Related files:** `src/services/rule_compiler.py`, `tests/unit/test_rule_templates.py`
- **Acceptance criteria:**
  - [ ] Mỗi supported rule sinh đúng một parameterized/read-only `SELECT`.
  - [ ] Semantics với `NULL`, empty dataset và boundary được test.
  - [ ] Unsupported parameter combination bị reject.
- **Verification:** `.\.venv\Scripts\python.exe -m pytest tests/unit/test_rule_templates.py -v`

### MVP-016 — Implement read-only rule runner

- **Owner:** Backend owner
- **Reviewer:** UI/Integration owner
- **Status:** Blocked
- **Dependencies:** MVP-003, MVP-015
- **Estimate:** 1d
- **Related files:** `src/services/rule_runner.py`, `tests/integration/test_rule_runner.py`
- **Acceptance criteria:**
  - [ ] Chỉ rule `APPROVED/ACTIVE` được execute.
  - [ ] DB role không thể `INSERT/UPDATE/DELETE/DDL`; có statement timeout.
  - [ ] Result persist failed/eligible count và bounded failed IDs.
- **Verification:** `.\.venv\Scripts\python.exe -m pytest tests/integration/test_rule_runner.py -v`

### MVP-017 — Thêm DQ run/results API

- **Owner:** Backend owner
- **Reviewer:** Product/QA owner
- **Status:** Blocked
- **Dependencies:** MVP-016
- **Estimate:** 0.5d
- **Related files:** `src/api/routes/runs.py`, `tests/test_api/test_runs.py`
- **Acceptance criteria:**
  - [ ] Create/status/results endpoints khớp API contract.
  - [ ] Unknown dataset/rule và invalid state trả lỗi đúng status.
  - [ ] Response không trả raw failing records.
- **Verification:** `.\.venv\Scripts\python.exe -m pytest tests/test_api/test_runs.py -v`

### MVP-018 — Tạo frontend scaffold và API client

- **Owner:** UI/Integration owner
- **Reviewer:** Backend owner
- **Status:** Blocked — cần chốt React/Vite
- **Dependencies:** Contract review của MVP-007, MVP-009, MVP-013, MVP-017
- **Estimate:** 1d
- **Related files:** `frontend/`, `.env.example`
- **Acceptance criteria:**
  - [ ] Frontend start/build bằng documented commands.
  - [ ] Base API URL qua env, không hard-code production URL.
  - [ ] API client xử lý success/error envelope tối thiểu.
- **Verification:** `npm --prefix frontend run build`

### MVP-019 — Implement Dataset/Profile/Rule Review UI

- **Owner:** UI/Integration owner
- **Reviewer:** Product/QA owner
- **Status:** Blocked
- **Dependencies:** MVP-009, MVP-013, MVP-018
- **Estimate:** 1.5d
- **Related files:** `frontend/src/`, UI tests
- **Acceptance criteria:**
  - [ ] User chọn dataset, xem profile và trigger proposal được.
  - [ ] Approve/edit/reject cập nhật state từ API thật.
  - [ ] Loading, empty, validation và API error state có test/evidence.
- **Verification:** `npm --prefix frontend test -- --run; npm --prefix frontend run build`

### MVP-020 — Implement DQ Results và audit summary UI

- **Owner:** UI/Integration owner
- **Reviewer:** Product/QA owner
- **Status:** Blocked
- **Dependencies:** MVP-017, MVP-018
- **Estimate:** 1d
- **Related files:** `frontend/src/`, UI tests
- **Acceptance criteria:**
  - [ ] User trigger run và xem status/results được.
  - [ ] Hiển thị failed/eligible counts và Data Health Score có context.
  - [ ] Không hiển thị số demo hard-code như kết quả thật.
- **Verification:** `npm --prefix frontend test -- --run; npm --prefix frontend run build`

### MVP-021 — Wrap core services bằng Dagster

- **Owner:** UI/Integration owner
- **Reviewer:** Backend owner
- **Status:** Blocked — cần xác nhận Dagster trong MVP
- **Dependencies:** MVP-006, MVP-008, MVP-016
- **Estimate:** 1d
- **Related files:** `src/orchestration/`, `docker-compose.yml`, tests
- **Acceptance criteria:**
  - [ ] Asset/job gọi service hiện có, không duplicate business logic.
  - [ ] Manual run có status và correlation ID.
  - [ ] Có một schedule disabled-by-default và unit test job definition.
- **Verification:** `.\.venv\Scripts\python.exe -m pytest tests/integration/test_dagster_jobs.py -v`

### MVP-022 — Viết end-to-end smoke test

- **Owner:** Product/QA owner
- **Reviewer:** UI/Integration owner
- **Status:** Blocked
- **Dependencies:** MVP-019, MVP-020, MVP-021
- **Estimate:** 1d
- **Related files:** `tests/e2e/`, `docs/RUNBOOK.md`
- **Acceptance criteria:**
  - [ ] Test chạy ingest → profile → proposal → approve → DQ result trên fixture.
  - [ ] Không cần live data download; live LLM có deterministic fallback được gắn nhãn.
  - [ ] Failure chỉ rõ phase và giữ evidence đã loại secret.
- **Verification:** `.\.venv\Scripts\python.exe -m pytest tests/e2e/test_mvp_smoke.py -v`

### MVP-023 — Chuẩn hóa error, idempotency và structured logs

- **Owner:** Backend owner
- **Reviewer:** Agent owner
- **Status:** Blocked
- **Dependencies:** MVP-007, MVP-009, MVP-013, MVP-017
- **Estimate:** 1d
- **Related files:** `src/api/`, `src/services/`, `tests/`
- **Acceptance criteria:**
  - [ ] Public errors dùng stable envelope và không lộ exception detail nhạy cảm.
  - [ ] Retry ingestion/profile/run không tạo duplicate ngoài contract.
  - [ ] Log có request/run/dataset correlation ID, không có secret/raw rows.
- **Verification:** `.\.venv\Scripts\python.exe -m pytest tests -v`

### MVP-024 — Hoàn thiện core integration/regression suite

- **Owner:** Backend owner
- **Reviewer:** Product/QA owner
- **Status:** Blocked
- **Dependencies:** MVP-023
- **Estimate:** 1d
- **Related files:** `tests/integration/`, `tests/test_api/`, `tests/test_agents/`
- **Acceptance criteria:**
  - [ ] Core happy path và failure boundaries chạy không cần paid API.
  - [ ] Pending/rejected execution, malformed LLM và DB failure có regression test.
  - [ ] Full pytest và Ruff pass từ clean environment.
- **Verification:** `.\.venv\Scripts\python.exe -m pytest tests -v; .\.venv\Scripts\python.exe -m ruff check src tests`

## P1 — Cần cho demo tốt

### DEMO-001 — Readiness health checks

- **Owner:** Backend owner
- **Reviewer:** UI/Integration owner
- **Status:** Blocked
- **Dependencies:** MVP-003, MVP-012
- **Estimate:** 0.5d
- **Related files:** `src/main.py`, `src/api/`, API tests
- **Acceptance criteria:**
  - [ ] Liveness không phụ thuộc external service; readiness phản ánh DB/required config.
  - [ ] Không báo `ready` giả khi dependency bắt buộc unavailable.
- **Verification:** `.\.venv\Scripts\python.exe -m pytest tests/test_api/test_health.py -v`

### DEMO-002 — UI loading/empty/error polish

- **Owner:** UI/Integration owner
- **Reviewer:** Product/QA owner
- **Status:** Blocked
- **Dependencies:** MVP-019, MVP-020
- **Estimate:** 1d
- **Related files:** `frontend/src/`, screenshots/tests
- **Acceptance criteria:**
  - [ ] Bốn core screens có loading, empty và recoverable error state.
  - [ ] Keyboard/basic accessibility và responsive demo viewport được kiểm tra.
- **Verification:** `npm --prefix frontend test -- --run; npm --prefix frontend run build`

### DEMO-003 — Benchmark dev_medium và demo

- **Owner:** Product/QA owner
- **Reviewer:** Backend owner
- **Status:** Blocked
- **Dependencies:** MVP-022
- **Estimate:** 0.5d
- **Related files:** `eval/results/`, `docs/RUNBOOK.md`
- **Acceptance criteria:**
  - [ ] Ghi row count, hardware, DB config, duration và peak memory.
  - [ ] Không công bố performance target không có evidence.
- **Verification:** `Get-Content eval\results\report.md`

### DEMO-004 — Demo rehearsal và recovery evidence

- **Owner:** Product/QA owner
- **Reviewer:** UI/Integration owner
- **Status:** Blocked
- **Dependencies:** MVP-022, DEMO-002, DEMO-003
- **Estimate:** 0.5d
- **Related files:** `docs/RUNBOOK.md`, `eval/results/`, screenshots
- **Acceptance criteria:**
  - [ ] Clean-start demo pass hai lần liên tiếp.
  - [ ] Có fallback cho network/LLM failure và rollback/reset instructions.
- **Verification:** Làm theo mục Demo checklist trong `docs/RUNBOOK.md`.

## P2 — Sau MVP

### EXT-001 — Synthetic corruption generator

- **Owner:** Product/QA owner
- **Reviewer:** Agent owner
- **Status:** Deferred
- **Dependencies:** MVP-022
- **Estimate:** 1d
- **Related files:** `eval/`, `tests/fixtures/`
- **Acceptance criteria:**
  - [ ] Fixed seed tạo cùng corruption và hidden manifest.
  - [ ] Manifest không đi vào Agent-visible runtime data.
- **Verification:** `.\.venv\Scripts\python.exe -m pytest tests/eval/test_corruption.py -v`

### EXT-002 — Robust statistics/group drift baseline

- **Owner:** Agent owner
- **Reviewer:** Product/QA owner
- **Status:** Deferred
- **Dependencies:** EXT-001
- **Estimate:** 1d
- **Related files:** `src/services/`, `eval/`
- **Acceptance criteria:**
  - [ ] Baseline có precision/recall/FPR trên blind partition.
  - [ ] Threshold và seed được persist, không chọn theo test result sau cùng.
- **Verification:** `.\.venv\Scripts\python.exe -m pytest tests/eval/test_statistics.py -v`

### EXT-003 — Isolation Forest candidate ranking spike

- **Owner:** Agent owner
- **Reviewer:** Product/QA owner
- **Status:** Deferred
- **Dependencies:** EXT-002
- **Estimate:** 1d
- **Related files:** `eval/`, decision record
- **Acceptance criteria:**
  - [ ] So sánh B1/B2 ở fixed false-positive budget và nhiều seed.
  - [ ] Có go/no-go decision; không giữ model nếu không tạo uplift.
- **Verification:** `.\.venv\Scripts\python.exe -m pytest tests/eval/test_isolation_forest.py -v`

### EXT-004 — Evidence-grounded diagnosis

- **Owner:** Agent owner
- **Reviewer:** Backend owner
- **Status:** Deferred
- **Dependencies:** EXT-002 hoặc EXT-003
- **Estimate:** 1d
- **Related files:** `src/agents/`, `eval/`
- **Acceptance criteria:**
  - [ ] Diagnosis chỉ cite persisted evidence keys hợp lệ.
  - [ ] Không claim root cause chắc chắn khi evidence chỉ là correlation.
- **Verification:** `.\.venv\Scripts\python.exe -m pytest tests/eval/test_diagnosis.py -v`

### EXT-005 — Viewer, notification hoặc cloud deployment discovery

- **Owner:** UI/Integration owner
- **Reviewer:** Product/QA owner
- **Status:** Deferred
- **Dependencies:** MVP Definition of Done
- **Estimate:** 1d discovery
- **Related files:** `docs/DECISIONS.md`, follow-up backlog
- **Acceptance criteria:**
  - [ ] Mỗi capability có problem, owner, security/cost impact và decision riêng.
  - [ ] Không implement trước khi scope/contract được approve.
- **Verification:** Review ADR và backlog follow-up đã được team approve.
