# RidePulse DQ — Architecture Decisions

ADR ghi cả quyết định đã có trong code và quyết định MVP đề xuất. `Proposed` không phải
authorization để implement ngoài backlog/task được giao.

## ADR-001: Dùng FastAPI cho HTTP API

**Date:** 2026-08-06

**Status:** Accepted

### Context

Starter template đã có FastAPI app, CORS, Pydantic validation và async API tests.

### Decision

Giữ FastAPI làm backend HTTP framework.

### Alternatives considered

Flask, Django REST Framework.

### Consequences

Tái sử dụng scaffold/tests; route phải mỏng và business logic chuyển vào service.

## ADR-002: Dùng LangGraph cho Agent workflow

**Date:** 2026-08-06

**Status:** Accepted for framework; product graph not implemented

### Context

Code đã compile một `StateGraph` với `AgentState`, nhưng nodes hiện là placeholder.

### Decision

Giữ LangGraph; thay demo graph bằng short-lived rule proposal graph theo backlog.

### Alternatives considered

Gọi LLM trực tiếp trong route, custom workflow engine.

### Consequences

Có explicit state/nodes và unit-test boundary; HITL không suspend graph trong MVP.

## ADR-003: Dùng Pydantic làm contract boundary

**Date:** 2026-08-06

**Status:** Accepted

### Context

Current API đã dùng `ChatRequest`/`ChatResponse` và `BaseSettings`.

### Decision

Mọi public request/response và LLM structured rule phải có Pydantic schema.

### Alternatives considered

Untyped dict, dataclass-only validation.

### Consequences

Validation/error rõ hơn; schema change là contract change cần review.

## ADR-004: Một LLM provider qua service adapter

**Date:** 2026-08-06

**Status:** Accepted for current OpenAI adapter; provider choice needs confirmation

### Context

`src/services/llm.py` hiện tạo `ChatOpenAI`; graph chưa gọi service này.

### Decision

Không gọi provider trực tiếp từ route/node; dùng service adapter và mocked provider
trong automated tests. Model/provider production **[NEEDS CONFIRMATION]**.

### Alternatives considered

Provider-specific calls ở mỗi node, nhiều provider ngay MVP.

### Consequences

Dễ mock/swap; cần timeout, structured output và cost controls.

## ADR-005: PostgreSQL là persistence target của MVP

**Date:** 2026-08-06

**Status:** Proposed / Not implemented

### Context

Current config mặc định SQLite nhưng không có persistence code. MVP cần ingest đến 1M
dòng, aggregate SQL, workflow state và read-only execution role.

### Decision

Dùng PostgreSQL + SQLAlchemy 2 + Alembic; không hỗ trợ song song SQLite cho product flow.

### Alternatives considered

SQLite, DuckDB-only, data warehouse managed.

### Consequences

Phù hợp SQL pushdown/roles nhưng tăng setup/integration-test complexity.

## ADR-006: Template SQL compiler thay dbt/Great Expectations trong MVP

**Date:** 2026-08-06

**Status:** Proposed / Not implemented

### Context

Rules được tạo/chỉnh runtime; MVP cần allow-list chặt và patch nhỏ.

### Decision

LLM trả typed rule spec; server compiler sinh `SELECT` từ template. Không nhận custom SQL.

### Alternatives considered

dbt Core generic tests, Great Expectations, LLM-generated SQL.

### Consequences

Ít dependency và dễ kiểm soát; team tự chịu trách nhiệm semantics/compiler tests.

## ADR-007: Aggregate-only evidence cho LLM

**Date:** 2026-08-06

**Status:** Proposed / safety constraint

### Context

Profiler cần scan data nhưng raw timestamp/location rows không cần thiết cho rule proposal.

### Decision

LLM chỉ nhận schema, aggregate profile, dictionary excerpts và approved rule history.

### Alternatives considered

Raw row sampling, embedding toàn bộ dataset.

### Consequences

Giảm leakage/token; Agent không thể giải thích pattern đòi hỏi raw rows nếu profiler
chưa tạo aggregate evidence phù hợp.

## ADR-008: HITL state lưu database, không suspend graph

**Date:** 2026-08-06

**Status:** Proposed / Not implemented

### Context

Human review có thể kéo dài hơn request/process lifetime.

### Decision

Graph kết thúc sau khi persist `PROPOSED`; API/database xử lý approve/edit/reject.

### Alternatives considered

LangGraph interrupt/checkpointer ngay MVP, in-memory pending state.

### Consequences

Workflow dễ resume/audit; cần state-machine validation ở service layer.

## ADR-009: Batch-first và Dagster là adapter

**Date:** 2026-08-06

**Status:** Proposed / Not implemented

### Context

MVP không cần streaming nhưng cần manual/scheduled batch. Business logic phải test được
không phụ thuộc orchestrator.

### Decision

Ingestion/profile/rule-run là services thuần; Dagster wrap chúng sau vertical slice.

### Alternatives considered

Logic trực tiếp trong Dagster assets, background task FastAPI-only, Kafka.

### Consequences

Ít lock-in và test nhanh; thêm adapter/deployment service ở phase cuối.

## ADR-010: Frontend MVP dùng React + Vite + Ant Design

**Date:** 2026-08-06

**Status:** Proposed / Needs confirmation

### Context

Repository chỉ có `ui_test` HTML/CSS/JS prototype không nối backend.

### Decision

Tạo `frontend/` React/Vite/Ant Design sau khi API contract ổn định.

### Alternatives considered

Tiếp tục vanilla prototype, Streamlit, Next.js.

### Consequences

Phù hợp interactive workflow nhưng thêm Node toolchain; team phải xác nhận trước MVP-018.

## ADR-011: NYC TLC Yellow Taxi là dataset duy nhất của MVP

**Date:** 2026-08-06

**Status:** Proposed / source month needs confirmation

### Context

MVP cần data mobility công khai, Parquet theo tháng và có data dictionary. Chicago source
đã bị loại do accessibility/scope.

### Decision

Dùng một pinned NYC Yellow Taxi month; 100k/300k/up-to-1M deterministic profiles.

### Alternatives considered

Chicago datasets, synthetic-only data, nhiều tháng ngay phase đầu.

### Consequences

Scope dễ kiểm soát; phải nói rõ taxi data chỉ là mobility proxy và cache trước demo.

## ADR-012: Current deployment chỉ là backend Docker container

**Date:** 2026-08-06

**Status:** Accepted current state; target stack Proposed

### Context

Dockerfile và Compose hiện chỉ build/start FastAPI backend; chưa có DB/frontend/Dagster.

### Decision

Document current container đúng thực tế. Full local stack chỉ được claim sau Phase 6.

### Alternatives considered

Claim template compose là full stack, deploy cloud sớm.

### Consequences

Không gây hiểu nhầm; deployment work được giữ ngoài đường găng ban đầu.
