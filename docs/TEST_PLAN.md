# RidePulse DQ — MVP Test Plan

> **Current automated baseline:** 5 pytest tests cho scaffold API/Agent.
>
> **Current gaps:** chưa có database, ingestion, profile, rule, frontend hoặc E2E tests.

## 1. Mục tiêu

- Chứng minh core flow đúng và có thể tái lập.
- Chặn unapproved/mutating rule execution.
- Không gọi live paid LLM trong automated test mặc định.
- Cho Product/QA owner có checklist manual rõ ràng.

## 2. Ownership và minimum coverage

| Area | Test type | Owner | Minimum coverage | Status |
|---|---|---|---|---|
| Config/health | Unit + API | Backend owner | Valid/invalid env, liveness/readiness | Partial |
| Current chat scaffold | API + Agent | Agent owner | Empty input và graph basic flow | Implemented |
| Dataset manifest | Unit | Product/QA + Backend | Checksum, seed, path allow-list | Not implemented |
| Ingestion | Integration | Backend owner | Success, duplicate, corrupt file, rollback | Not implemented |
| Profiling | Unit + Integration | Backend owner | Numeric/category/null/empty cases | Not implemented |
| Evidence | Unit + security | Agent owner | Stable refs, size guard, no raw rows | Not implemented |
| Rule schemas | Unit | Agent owner | Every type + invalid operator/payload | Not implemented |
| HITL lifecycle | API + Integration | Backend owner | Valid/invalid transitions, audit | Not implemented |
| SQL compiler | Unit + security | Backend owner | Every type, injection/DDL/DML rejection | Not implemented |
| Rule runner | Integration | Backend owner | Read-only, timeout, result persistence | Not implemented |
| Frontend | Component + manual | UI/Integration owner | Happy/loading/empty/error | Not implemented |
| MVP flow | E2E smoke | Product/QA owner | Full core journey | Not implemented |

Không dùng một coverage percentage chung để thay acceptance criteria. Code critical
như compiler/state transition cần branch/failure tests đầy đủ dù global coverage cao.

## 3. Test layers

### Unit tests

- Pydantic validation và settings.
- Manifest/checksum/sample determinism.
- Profile statistics với fixtures nhỏ.
- Evidence field allow-list và size limit.
- LangGraph nodes với mocked LLM.
- Rule state machine và Data Health Score.
- SQL compiler output/identifier validation.

### API tests

- Method/path/status/response schema theo `API_CONTRACT.md`.
- Missing/empty/invalid input.
- Resource not found và conflict.
- Error envelope không lộ exception/secret.
- Pagination/filter boundary.

### Agent tests

- Mocked valid structured proposal.
- Malformed JSON/schema mismatch.
- Unknown evidence reference.
- Timeout/provider exception.
- Prompt injection yêu cầu raw rows/custom SQL.
- Không assert exact natural-language wording nếu contract chỉ yêu cầu structure.

### Integration tests

- Local fixture Parquet → PostgreSQL → profile.
- Profile → evidence → mocked proposal → persistence.
- Approved rule → compile → read-only execute → result/audit.
- Transaction rollback khi một bước fail.
- Dagster wrapper gọi cùng service layer, không duplicate logic.

Database integration test phải dùng database/schema riêng và cleanup có target rõ.

### Frontend/manual tests

- Dataset list empty và loaded.
- Ingestion/profile pending/success/failure.
- Proposal review: approve/edit/reject.
- Run status polling và results.
- Network failure, API 422/409/500/503.
- Không hiển thị hard-coded demo metric như live result.

### Demo smoke test

1. Start clean local stack.
2. Ingest `dev_small` local cache.
3. Profile và tạo proposals bằng deterministic test provider/fallback.
4. Approve/edit/reject.
5. Compile/run approved rules.
6. Xác minh rejected rule không chạy.
7. Xem results/audit qua UI.
8. Stop/start stack và xác minh persisted state theo contract.

## 4. Required scenario matrix

| Scenario | Expected |
|---|---|
| Happy path | Core flow hoàn thành và persist đúng state |
| Invalid input | 422 hoặc documented error; không side effect |
| Empty dataset/profile | Stable empty output; không divide-by-zero |
| Missing resource | Stable 404 code |
| Duplicate request | Idempotent replay hoặc 409 theo contract |
| LLM timeout/malformed | Proposal run failed/retryable; không tạo executable rule |
| Database unavailable | 503/failed job; transaction không partial-success |
| Pending/rejected execution | Bị chặn và audit |
| SQL injection/DDL/DML | Compiler/DB role từ chối |
| Raw data leakage attempt | Không có raw row trong LLM payload/log |

## 5. Evaluation dataset sau MVP

MVP test correctness bằng fixtures deterministic. Precision/recall anomaly chỉ bắt đầu
sau MVP với synthetic corruption manifest giấu khỏi Agent:

- Clean/untouched partition để đo false positive.
- Corrupted development partition.
- Blind corrupted partition để đo generalization.
- Fixed seed, source IDs và versioned injection config.

Isolation Forest không có acceptance target cho đến khi B0/B1 deterministic/statistical
baseline tồn tại.

## 6. Commands

Current:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -v
.\.venv\Scripts\python.exe -m ruff check src tests
```

Proposed khi có test layout:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit -v
.\.venv\Scripts\python.exe -m pytest tests\integration -v
.\.venv\Scripts\python.exe -m pytest tests\e2e\test_mvp_smoke.py -v
npm --prefix frontend test -- --run
npm --prefix frontend run build
```

## 7. Evidence và release gate

- Lưu command, timestamp, commit SHA, environment và pass/fail summary.
- UI change có screenshot.
- Benchmark ghi hardware và row profile.
- Không merge P0 nếu test liên quan fail hoặc chưa chạy mà không có reviewer waiver.
- Trước demo: full pytest, Ruff, frontend checks, E2E smoke và manual flow đều pass.
