# RIDEPULSE DQ — KẾ HOẠCH PHÁT TRIỂN & PHÂN CÔNG KỸ THUẬT CHI TIẾT

> **Dự án:** RidePulse DQ — AI Agent Xây dựng, Kiểm tra Data Quality & Phát hiện Bất thường  
> **Phiên bản:** v2.0 (Gate 3 — Production-Ready Upgrade)  
> **Ngày lập:** 2026-08-19  
> **Team:** Kiên (AI Infra Lead) · Chiến (ML/Anomaly & Eval) · Phong (DevOps/MLOps) · Đạt (LLM Core & Frontend)

---

## 1. TỔNG QUAN PHÂN TÍCH HỆ THỐNG

### 1.1 Điểm yếu & Hạn chế của hệ thống hiện tại (Gate 2 MVP)

Hệ thống Gate 2 đã đạt được luồng cơ bản end-to-end (`Profiler → Proposer → HITL → Test Generator → Runner → Anomaly → Report`), nhưng vẫn tồn tại các lỗ hổng kiến trúc nghiêm trọng cần giải quyết trước khi tiến lên production:

| # | Hạn chế | Mô tả chi tiết | Ảnh hưởng |
|---|---------|-----------------|-----------|
| **L1** | **Hard-code business rules & single-dataset** | Hệ thống chỉ hỗ trợ dataset `yellow_tripdata` với 9 rule types cố định trong `RuleType` enum. Worker `run_ingest_profile()` hard-code mapping vendor/payment. Schema `SourceRowModel` gắn chặt 21 cột NYC taxi. | Không mở rộng được cho dataset khác (payments, drivers, customers). Mỗi dataset mới yêu cầu sửa code. |
| **L2** | **Thiếu Dataset Understanding Agent** | LLM nhận profiling stats thô (null_rate, min/max, distinct_count) nhưng thiếu ngữ cảnh nghiệp vụ (data dictionary, domain semantics). Rule proposer dựa hoàn toàn vào prompt engineering tĩnh. | LLM sinh rule chung chung, thiếu reasoning chất lượng. `ai_reasoning` và `rule_description` mang tính generic. |
| **L3** | **Thiếu Dynamic Context Builder** | `ProposalEvidence` model là aggregate tĩnh. Không có cơ chế tự động build context dựa trên schema detected type, historical rules (RAG), và business metadata. | Prompt gửi LLM thiếu enrichment → confidence_score không phản ánh đúng chất lượng reasoning. |
| **L4** | **Rule Catalog chưa đủ chuẩn hóa** | `RuleType` enum hiện có 9 types nhưng thiếu `STATISTICAL_DISTRIBUTION`, `TEMPORAL_CONSISTENCY`. `RuleParameters` dùng flat optional fields → khó validate theo từng type. | Không cover đủ DQ dimensions. Parameter validation dựa vào `model_validator` manual, dễ bỏ sót. |
| **L5** | **Thiếu cơ chế đo lường offline/online (DeepEval)** | Không có benchmark pipeline đánh giá chất lượng output LLM (Faithfulness, Executability, Correctness). Chỉ có mock mode và manual smoke test. | Không track được regression khi đổi model/prompt. Không có baseline metrics để so sánh cross-model. |
| **L6** | **Visualization tĩnh, không dynamic** | Frontend `App.tsx` (90KB monolith) render static tables. Anomaly dashboard chỉ hiển thị bảng danh sách, chưa có time-series chart, trend analysis, hay dynamic drill-down. | UX kém cho Data Steward khi cần analyze pattern. Không hỗ trợ interactive exploration. |
| **L7** | **Thiếu State Machine & Resilience rõ ràng** | `AgentState` là flat `TypedDict`. Job status tracking qua `JobModel.status` đơn giản (`PENDING/RUNNING/SUCCEEDED/FAILED`). Không có retry policy có cấu trúc, circuit breaker, hay dead-letter queue. | Khi node fail giữa chừng, state không recover được. Không có observability cho từng transition. |
| **L8** | **Nguy cơ Hallucination gây fail compile** | `test_generator_node` và `llm_dbt_repair_node` vẫn cho phép LLM sinh/sửa YAML. Repair loop giới hạn 3 attempts nhưng thiếu guardrail semantic. | LLM có thể hallucinate column names, table names, hoặc sinh SQL syntax không hợp lệ → test compile fail. |

### 1.2 Chiến lược cải tiến trọng tâm — 7 Module

| Module | Tên | Mục tiêu | Owner chính |
|--------|-----|----------|-------------|
| **M1** | Multi-Dataset Architecture | Hỗ trợ N dataset với schema registry dynamic | Kiên |
| **M2** | Dataset Understanding Agent | LangGraph node tự động sinh Data Dictionary & Semantic Profile | Đạt |
| **M3** | Dynamic Context Builder | Auto-assemble prompt context từ profile + history + dictionary | Đạt |
| **M4** | 11 Cataloged Rule Types | Chuẩn hóa catalog với typed parameter schemas & target compilers | Kiên + Đạt |
| **M5** | DeepEval Benchmark Pipeline | Offline/Online eval cho LLM outputs (Faithfulness, Executability, Correctness) | Chiến |
| **M6** | Dynamic Visualization | Interactive dashboards, time-series charts, drill-down anomaly explorer | Đạt |
| **M7** | State Machine & Resilience | Formal FSM, retry policies, circuit breaker, structured logging | Phong |

---

## 2. KIẾN TRÚC HỆ THỐNG & AGENT WORKFLOW

### 2.1 StateFlow chi tiết — LangGraph Orchestrated Pipeline

Luồng trạng thái mở rộng từ 7 nodes hiện tại lên 10 trạng thái chính thức:

```
[QUEUED]
  │  User triggers DQ job / Scheduled run
  ▼
[PROFILING]
  │  raw_profiler_node: DuckDB/SQL stats query, schema introspection
  │  Output: dataset_profile (per-column stats + cross-column metrics)
  ▼
[UNDERSTANDING_DATASET]  ← NEW
  │  understanding_agent_node: LLM generates Data Dictionary
  │  Semantic type inference, business name mapping
  │  Output: dataset_understanding (dictionary + domain_context)
  ▼
[BUILDING_CONTEXT]  ← NEW
  │  context_builder_node: Assemble prompt context
  │  Sources: profile + dictionary + historical rules (RAG) + domain constraints
  │  Token budget: ≤ 4000 tokens. PII guard active.
  │  Output: context_payload (evidence-tracked, redacted)
  ▼
[PROPOSING_RULES]
  │  rule_proposer_node: Structured LLM output (11 typed rules)
  │  with_structured_output() → TableRuleProposalV2
  │  Output: proposed_rules (list of ProposedRule)
  ▼
[VALIDATING_RULES]
  │  Pydantic parse → evidence cross-reference → duplicate dedup
  │  Parameter range validation → schema identifier check
  │  Reject ratio tracking (target < 20%)
  ▼
[WAITING_FOR_REVIEW — HITL Gate]
  │  Data Steward reviews via Rule Approval Board
  │  Actions: Approve / Reject (with reason) / Edit parameters
  │  Bulk operations supported. Version tracking.
  ▼
[TEST_COMPILATION_AND_EXECUTION]
  │  Deterministic compiler: RuleType → SQL Template → bind params
  │  dbt YAML generation → dbt parse validation
  │  Repair loop (max 3 attempts) for dbt validation failures
  │  Read-only SQL runner: bounded results, max 20 sample IDs
  ▼
[ANOMALY_DETECTION]
  │  Hybrid model: Z-Score + Isolation Forest + Dynamic Threshold
  │  Cold-start: fixed 5% threshold
  │  Warm (≥5 runs): Z-score spike + IForest score + EMA breach
  │  Root cause attribution: correlate with metadata changes
  ▼
[REPORTING_AND_LOGGING]
  │  persist_report_node: Save test results + anomalies
  │  steward_insights_node: DQ Score calculation + grade + remediation
  │  Audit event logging. Structured trace output.
  │
  ▼
[COMPLETED] / [FAILED]
```

**Error Handling tại mỗi node:**
- Node failure → `ERROR_STATE` → persist error + audit log → Job `FAILED`
- LLM timeout/refusal → Fallback mock output (nếu circuit breaker open) hoặc retry (max 3)
- dbt parse failure → Repair loop (deterministic template re-render, không dùng LLM repair)

### 2.2 Chi tiết 11 Cataloged Rule Types

Mở rộng từ 9 types hiện tại, bổ sung `STATISTICAL_DISTRIBUTION` và `TEMPORAL_CONSISTENCY`:

| # | Rule Type | DQ Dimension | Parameter Schema | Validator Logic | Target Compiler (SQL) |
|---|-----------|-------------|------------------|-----------------|----------------------|
| **R1** | `NOT_NULL` | COMPLETENESS | `{}` (no params) | Column phải tồn tại trong schema. Không áp dụng cho cột có null_rate = 0% (redundant). | `SELECT COUNT(*) FROM {table} WHERE {col} IS NULL` |
| **R2** | `UNIQUE` | UNIQUENESS | `{}` (no params) | Column phải có `uniqueness_rate` > 0.95 trong profile để justify. | `SELECT {col}, COUNT(*) AS cnt FROM {table} GROUP BY {col} HAVING cnt > 1` |
| **R3** | `RANGE` | VALIDITY | `{min?: float, max?: float}` — ít nhất 1 required | Giá trị min/max phải nằm trong biên hợp lý so với profile (±3σ). Column phải là numeric type. | `SELECT COUNT(*) FROM {table} WHERE {col} < :min OR {col} > :max` |
| **R4** | `ACCEPTED_VALUES` | VALIDITY | `{accepted_values: list[str]}` — non-empty | Danh sách accepted phải cover ≥ 90% distinct values trong profile. Max 50 values. | `SELECT COUNT(*) FROM {table} WHERE CAST({col} AS TEXT) NOT IN (:vals)` |
| **R5** | `REGEX_FORMAT` | VALIDITY | `{regex: str}` — valid regex pattern | Regex phải compile thành công. Phải match ≥ 80% sample values trong profile. | `SELECT COUNT(*) FROM {table} WHERE {col} !~ :regex` (PostgreSQL) |
| **R6** | `FRESHNESS` | FRESHNESS | `{max_age_hours: float}` — positive | Column phải là timestamp/date type. max_age_hours phải > 0 và ≤ 8760 (1 năm). | `SELECT EXTRACT(EPOCH FROM NOW() - MAX({col}))/3600 AS age_hours FROM {table}` |
| **R7** | `ROW_COUNT` | COMPLETENESS | `{min_row_count: int}` — positive | Rule cấp bảng (column = None). min_row_count phải ≤ current row_count × 2. | `SELECT COUNT(*) AS total FROM {table}` |
| **R8** | `NULL_RATE` | COMPLETENESS | `{max_null_pct: float}` — [0.0, 100.0] | Khác NOT_NULL ở chỗ cho phép tỷ lệ null nhất định. Column phải có null_rate > 0 trong profile. | `SELECT (COUNT(*) FILTER (WHERE {col} IS NULL))::float / COUNT(*) * 100 FROM {table}` |
| **R9** | `CROSS_FIELD_COMPARISON` | CONSISTENCY | `{target_column: str, operator: str}` — cả hai required | Cả source và target column phải tồn tại và cùng comparable type. Operator ∈ {`<=`, `<`, `>=`, `>`, `=`, `!=`}. | `SELECT COUNT(*) FROM {table} WHERE NOT ({col} {op} {target_col})` |
| **R10** | `STATISTICAL_DISTRIBUTION` | ACCURACY | `{method: "zscore"\|"iqr", threshold: float}` | Column phải là numeric. Threshold > 0. | Z-score: `WITH stats AS (SELECT AVG({col}) mu, STDDEV({col}) sigma FROM {table}) SELECT COUNT(*) FROM {table}, stats WHERE ABS(({col}-mu)/NULLIF(sigma,0)) > :threshold` |
| **R11** | `TEMPORAL_CONSISTENCY` | CONSISTENCY | `{start_col: str, end_col: str, max_duration_hours?: float}` | Cả start_col và end_col phải là timestamp type. | `SELECT COUNT(*) FROM {table} WHERE {end_col} < {start_col} OR EXTRACT(EPOCH FROM {end_col}-{start_col})/3600 > :max_duration` |

#### Typed Parameter Schema (Pydantic v2)

```python
class RuleParametersV2(BaseModel):
    """Discriminated union parameter bag — validate theo rule_type."""
    model_config = ConfigDict(extra="forbid")

    # R3: RANGE
    min: float | None = None
    max: float | None = None
    # R4: ACCEPTED_VALUES
    accepted_values: list[str] | None = None
    # R5: REGEX_FORMAT
    regex: str | None = None
    # R6: FRESHNESS
    max_age_hours: float | None = None
    # R7: ROW_COUNT
    min_row_count: int | None = None
    # R8: NULL_RATE
    max_null_pct: float | None = None
    # R9: CROSS_FIELD_COMPARISON
    target_column: str | None = None
    operator: Literal["<=", "<", ">=", ">", "=", "==", "!=", "<>"] | None = None
    # R10: STATISTICAL_DISTRIBUTION (NEW)
    method: Literal["zscore", "iqr"] | None = None
    threshold: float | None = None
    percentile_low: float | None = None
    percentile_high: float | None = None
    # R11: TEMPORAL_CONSISTENCY (NEW)
    start_col: str | None = None
    end_col: str | None = None
    max_duration_hours: float | None = None
    allow_equal: bool | None = None
```

---

## 3. PHÂN CÔNG CÔNG VIỆC CHI TIẾT (WBS — WORK BREAKDOWN STRUCTURE)

---

### 3.1 KIÊN — AI Infrastructure Lead

> **Trách nhiệm tổng:** Backend core, Orchestration framework, Database architecture, Execution Pipeline & Test Compiler.

#### Module M1: Multi-Dataset Architecture

| Hạng mục | Chi tiết |
|----------|----------|
| **Nhiệm vụ chính** | Thiết kế Schema Registry cho phép đăng ký N dataset với schema động; refactor `SourceRowModel` từ fixed-column sang EAV hoặc JSONB; xây dựng Dataset Connector abstraction layer. |
| **Chi tiết kỹ thuật** | **1) Schema Registry:** Tạo bảng `dataset_schemas` lưu trữ column definitions dạng JSONB (`[{name, type, nullable, description}]`). Khi ingest dataset mới, hệ thống auto-introspect schema và persist. **2) Dynamic Source Table:** Thay thế `trips_raw` fixed-schema bằng pattern `source_rows_{dataset_id}` với DDL tự động sinh từ registry, hoặc dùng single table `source_data` với JSONB `row_data` column + GIN index. **3) Connector Factory:** Abstract class `DatasetConnector` với implementations: `PostgresConnector`, `DuckDBConnector`, `BigQueryConnector`, `SnowflakeConnector`. Mỗi connector implement `introspect_schema()`, `sample_rows(n)`, `execute_dq_query(sql)`. **4) Migration:** Alembic migration tạo `dataset_schemas`, `dataset_connectors` tables. Backward-compatible với data hiện tại. |
| **Tech Stack** | SQLAlchemy 2.0, Alembic, PostgreSQL (JSONB + GIN index), DuckDB (local analytics), Pydantic v2 |
| **Deliverables** | `src/models/dataset_schema.py`, `src/services/connector_factory.py`, `src/services/schema_registry.py`, `alembic/versions/xxx_multi_dataset.py`, API endpoints: `POST /datasets/register`, `GET /datasets/{id}/schema` |

#### Module M4: 11 Rule Catalog — Compiler Engine

| Hạng mục | Chi tiết |
|----------|----------|
| **Nhiệm vụ chính** | Mở rộng Rule Catalog từ 9 lên 11 types; xây dựng Deterministic Test Compiler sinh SQL/dbt YAML từ typed rule spec; loại bỏ dependency vào LLM cho test generation. |
| **Chi tiết kỹ thuật** | **1) Rule Template Registry:** Dictionary mapping `RuleType → SQLTemplate`. Mỗi template là parameterized SQL string với bind parameters. Template được unit-test riêng biệt. **2) Compiler Pipeline:** `RuleCompiler.compile(rule: ApprovedRule, schema: DatasetSchema) → CompiledTest` — validate identifiers against schema registry, render SQL với bind params, generate dbt YAML test block. **3) Execution Boundary:** `CompiledTest` must pass `SQLValidator.validate()` — reject non-SELECT, DDL/DML, comments, multi-statements, unknown identifiers. Chỉ cho phép aggregate functions trong allowlist. **4) dbt YAML Generator:** Deterministic template rendering thay vì LLM generation. Output `generated_dq_tests.yml` + `schema.yml` cho dbt parse validation. |
| **Tech Stack** | Jinja2 (SQL template rendering), SQLAlchemy `text()` with bind params, `dbt-core` parse validation, Pydantic v2 |
| **Deliverables** | `src/agents/nodes/rule_compiler.py`, `src/agents/nodes/sql_templates/` (11 template files), `src/models/rule_schemas_v2.py`, `tests/unit/test_rule_compiler.py` (22 test cases: success + failure per type) |

#### Module M7: State Machine — Backend Infrastructure

| Hạng mục | Chi tiết |
|----------|----------|
| **Nhiệm vụ chính** | Nâng cấp `AgentState` từ flat TypedDict sang Formal State Machine; implement Job State transitions với validation; state checkpoint persistence cho crash recovery. |
| **Chi tiết kỹ thuật** | **1) Formal State Enum:** `JobPhase` enum: `QUEUED → PROFILING → UNDERSTANDING → CONTEXT_BUILDING → PROPOSING → VALIDATING → AWAITING_REVIEW → COMPILING → EXECUTING → ANALYZING → REPORTING → COMPLETED / FAILED`. **2) Transition Guard:** `StateTransitionValidator` class — validate allowed transitions, reject invalid jumps (e.g., QUEUED → EXECUTING). **3) State Checkpoint:** Persist `AgentState` snapshot vào DB tại mỗi node boundary. Recovery = load last checkpoint + resume từ failed node. **4) Enhanced JobModel:** Thêm `current_phase`, `phase_entered_at`, `checkpoint_data` (JSONB) columns. |
| **Tech Stack** | SQLAlchemy 2.0, LangGraph `StateGraph`, PostgreSQL JSONB, Python `enum` |
| **Deliverables** | `src/models/state_machine.py`, migration `xxx_state_machine.py`, `src/services/state_checkpoint.py`, updated `src/agents/graph.py` |

---

### 3.2 CHIẾN — AI Infrastructure (ML/Anomaly & Evaluation)

> **Trách nhiệm tổng:** Anomaly Detection Models, DeepEval benchmark pipeline, Data profiling engine, Latency/Cost audit.

#### Module M5: DeepEval Benchmark Pipeline

| Hạng mục | Chi tiết |
|----------|----------|
| **Nhiệm vụ chính** | Xây dựng offline evaluation pipeline đánh giá chất lượng LLM rule proposals trên 3 trục: Faithfulness (trung thành với evidence), Executability (rule compilable thành SQL hợp lệ), Correctness (rule phát hiện đúng lỗi trên labeled test data). |
| **Chi tiết kỹ thuật** | **1) Test Dataset Preparation:** Tạo labeled dataset với injected errors — null injection (5-20%), out-of-range values, duplicate rows, format violations. Ground truth labels cho từng error type. **2) Faithfulness Metric:** So sánh `ai_reasoning` với evidence keys trong `ProposalEvidence`. Score = (cited_evidence ∩ actual_evidence) / cited_evidence. LLM-as-judge backup cho semantic matching. **3) Executability Metric:** Compile mỗi proposed rule qua `RuleCompiler` → binary pass/fail. Score = compiled_rules / total_proposed. **4) Correctness Metric:** Run compiled tests trên labeled dataset → precision (true violations / detected violations), recall (detected / total injected), F1. **5) Cross-Model Comparison:** Run pipeline với OpenAI GPT-4o, Anthropic Claude Sonnet, Google Gemini, Mistral. Output comparison matrix. **6) Latency & Cost Tracking:** Log `tokens_used`, `latency_ms`, `cost_usd` per LLM call. |
| **Tech Stack** | DeepEval framework, scikit-learn (metrics), pandas, pytest (test runner), matplotlib/seaborn (visualization) |
| **Deliverables** | `eval/benchmark_pipeline.py`, `eval/metrics/faithfulness.py`, `eval/metrics/executability.py`, `eval/metrics/correctness.py`, `eval/datasets/` (labeled test data), `eval/results/` (benchmark reports), CI integration `eval/run_benchmark.sh` |

#### Anomaly Detection Engine — Nâng cấp

| Hạng mục | Chi tiết |
|----------|----------|
| **Nhiệm vụ chính** | Nâng cấp anomaly detection từ rule-based sang hybrid model: Statistical (Z-score, IQR) + ML (Isolation Forest) + Dynamic Thresholding. |
| **Chi tiết kỹ thuật** | **1) Statistical Layer:** Giữ nguyên Z-score spike detection (warm-start ≥ 5 runs). Bổ sung IQR-based outlier detection cho skewed distributions. **2) Isolation Forest Layer:** Train trên historical DQ metrics (`violation_rate`, `null_rate_delta`, `row_count_change`) per column per dataset. Re-train trigger: mỗi 50 runs hoặc weekly. **3) Dynamic Thresholding:** Thay cold-start fixed 5% bằng adaptive threshold — exponential moving average (EMA) × 1.5. Window = 10 runs. **4) Ensemble Scoring:** `anomaly_score = w1 × zscore_flag + w2 × iforest_score + w3 × threshold_breach`. Weights tunable via config. **5) Root Cause Attribution:** Auto-correlate anomalies với metadata changes (schema drift, row_count spike). |
| **Tech Stack** | scikit-learn (IsolationForest, StandardScaler), numpy/scipy (statistics), pandas, DuckDB |
| **Deliverables** | `src/services/anomaly_engine.py`, `src/services/dynamic_threshold.py`, `src/models/anomaly_models.py`, `tests/unit/test_anomaly_engine.py` |

#### Data Profiling Engine — Nâng cấp

| Hạng mục | Chi tiết |
|----------|----------|
| **Nhiệm vụ chính** | Comprehensive profiling engine với distribution analysis, correlation detection, pattern recognition. |
| **Chi tiết kỹ thuật** | **1) Enhanced Column Profile:** Thêm metrics — `mean`, `median`, `stddev`, `skewness`, `kurtosis`, `p5/p25/p75/p95` quantiles, `most_frequent_values` (top-10), `pattern_frequency` (string columns). **2) Cross-Column Analysis:** Pearson correlation (numeric), Cramér's V (categorical). Functional dependency detection. **3) DQ Score per Column:** Composite = weighted (completeness + uniqueness + validity). **4) DuckDB Integration:** In-process analytics, load parquet/CSV trực tiếp. |
| **Tech Stack** | DuckDB, ydata-profiling (optional), scipy.stats, numpy |
| **Deliverables** | `src/services/profiling_engine.py`, `src/models/profile_models.py` (`ColumnProfileV2`), migration `xxx_enhanced_profiles.py` |

---

### 3.3 PHONG — AI Infrastructure (DevOps/MLOps & Production Readiness)

> **Trách nhiệm tổng:** CI/CD, Containerization, Cloud Deployment, Async Task Scheduler, Logging/Tracing, Agent State monitoring.

#### Module M7: State Machine & Resilience — Operations Layer

| Hạng mục | Chi tiết |
|----------|----------|
| **Nhiệm vụ chính** | Production-grade job execution: retry policies, circuit breaker, dead-letter queue, observability stack. |
| **Chi tiết kỹ thuật** | **1) Retry Policy Engine:** Configurable per job type — `max_retries` (default 3), `backoff_strategy` (exponential/linear), `retry_on` (exception types). **2) Circuit Breaker:** Monitor LLM API error rate. Open khi error > 50% trong 5-min window. Fallback = mock mode. **3) Dead Letter Queue:** Failed jobs sau max retries → `failed_jobs_dlq` table. Manual retry endpoint + alert notification. **4) Health Endpoints:** `/health` report: DB connectivity, LLM API status, queue depth, active jobs. Kubernetes liveness/readiness probes. |
| **Tech Stack** | tenacity (retry), Redis (circuit breaker state), Celery (task queue), PostgreSQL (DLQ) |
| **Deliverables** | `src/services/retry_policy.py`, `src/services/circuit_breaker.py`, `src/services/dead_letter_queue.py`, `src/api/health.py` |

#### CI/CD & Containerization

| Hạng mục | Chi tiết |
|----------|----------|
| **Nhiệm vụ chính** | Multi-stage Docker builds, GitHub Actions CI pipeline, automated testing & linting, Cloud Run deployment. |
| **Chi tiết kỹ thuật** | **1) Dockerfile:** Multi-stage (builder → runtime). Target < 500MB. Non-root user. Health check. **2) Docker Compose Production:** Services: `api`, `worker`, `db` (PostgreSQL 16), `redis`, `minio`. Resource limits. **3) GitHub Actions:** Triggers: push to `main`, PR. Steps: lint (ruff) → type-check (mypy) → unit tests → integration tests → Docker build → deploy (staging → production). **4) Cloud Run:** API service (min 0, max 5), Worker service (min 1), Cloud SQL proxy, Secret Manager. |
| **Tech Stack** | Docker, Docker Compose, GitHub Actions, Cloud Run, GCR, Terraform (optional) |
| **Deliverables** | `Dockerfile` (optimized), `Dockerfile.worker`, `docker-compose.prod.yml`, `.github/workflows/ci.yml`, `.github/workflows/deploy.yml`, `scripts/deploy_cloud_run.sh` |

#### Logging, Tracing & Observability

| Hạng mục | Chi tiết |
|----------|----------|
| **Nhiệm vụ chính** | Structured logging, distributed tracing, LLM call observability. |
| **Chi tiết kỹ thuật** | **1) Structured Logging:** JSON-formatted: `timestamp`, `level`, `service`, `trace_id`, `job_id`, `dataset_id`. Correlation ID propagation. **2) LangSmith:** `LANGCHAIN_TRACING_V2` cho all LangGraph invocations. Tags: `run_type`, `model_name`, `token_count`. **3) OpenTelemetry:** Traces: API → Agent → LLM → DB. Per-node execution time spans. OTLP export. **4) Prometheus Metrics:** `dq_jobs_total`, `dq_job_duration_seconds`, `llm_tokens_used_total`, `llm_latency_seconds`, `anomaly_detected_total`. |
| **Tech Stack** | LangSmith, OpenTelemetry SDK + OTLP, structlog, Prometheus client |
| **Deliverables** | `src/core/logging.py`, `src/core/tracing.py`, `src/core/metrics.py`, `docs/OBSERVABILITY.md` |

#### Async Task Scheduler

| Hạng mục | Chi tiết |
|----------|----------|
| **Nhiệm vụ chính** | Production-grade async task processing cho DQ jobs. |
| **Chi tiết kỹ thuật** | **1) Celery + Redis:** Task types: `ingest_profile`, `propose_rules`, `run_dq`, `run_benchmark`. Priority queues: `critical`, `default`, `low`. **2) Celery Beat:** Scheduled DQ runs per dataset: `MANUAL/HOURLY/DAILY/WEEKLY`. Timezone-aware. **3) Distributed Lock:** Redis-based per dataset — prevent concurrent runs. Lock TTL = 30 min. **4) Worker Config:** Concurrency = 2 (IO-bound). Prefetch = 1. Ack late for crash recovery. |
| **Tech Stack** | Celery 5.x, Redis 7.x, celery-beat, flower (monitoring) |
| **Deliverables** | `src/tasks/celery_app.py`, `src/tasks/dq_tasks.py`, `src/tasks/beat_schedule.py`, Docker services (redis, celery-worker, celery-beat, flower) |

---

### 3.4 ĐẠT — AI Application Lead (LLM Core & Frontend)

> **Trách nhiệm tổng:** Frontend React UI, LLM Prompt Engineering, Output Parsers, Dynamic Context/Rule Generation, HITL UI.

#### Module M2: Dataset Understanding Agent

| Hạng mục | Chi tiết |
|----------|----------|
| **Nhiệm vụ chính** | LangGraph node tự động sinh Data Dictionary và Semantic Profile — cung cấp business context cho Rule Proposer. |
| **Chi tiết kỹ thuật** | **1) Understanding Agent Node:** Input = `dataset_profile`. Output = `dataset_understanding` chứa: `data_dictionary` (per-column: business_name_vi, description, semantic_type, business_rules), `domain_context` (industry, data_source_type), `relationships` (foreign keys, logical groupings). **2) Prompt Design:** System prompt = "Data Analyst chuyên ride-hailing". Few-shot examples. Constraint: chỉ aggregate stats, KHÔNG raw data. **3) Semantic Type Inference:** Map DB types + column names + distributions → semantic types: `CURRENCY`, `TIMESTAMP`, `GEO_ID`, `ENUM`, `IDENTIFIER`, `MEASUREMENT`, `FLAG`. Heuristic + LLM fallback. **4) Pydantic Parser:** `DatasetUnderstanding` model. `with_structured_output()`. Retry max 2. **5) Caching:** Per (dataset_id, schema_hash). Invalidate on schema change. |
| **Tech Stack** | LangChain `with_structured_output()`, Pydantic v2, LangGraph, ChromaDB |
| **Deliverables** | `src/agents/nodes/understanding_agent_node.py`, `src/models/understanding_schemas.py`, `src/prompts/understanding_prompt.py`, `tests/unit/test_understanding_agent.py` |

#### Module M3: Dynamic Context Builder

| Hạng mục | Chi tiết |
|----------|----------|
| **Nhiệm vụ chính** | Auto-assemble optimal LLM context — đảm bảo Rule Proposer nhận đủ thông tin mà không exceed token limit. |
| **Chi tiết kỹ thuật** | **1) Context Assembly:** Merge 4 nguồn: (a) profile stats, (b) data dictionary, (c) historical rules (RAG/ChromaDB), (d) domain constraints. **2) Token Budget:** Target ≤ 4000 tokens. Priority: dictionary > profile > constraints > history. Auto-truncate lower-priority. **3) RAG:** Embed approved rules + rejection reasons vào ChromaDB. Query top-3 similar rules per column. **4) Evidence Tracking:** Mỗi element có `evidence_key` cho Faithfulness eval. Output = `ContextPayload`. **5) PII Guard:** Scan context for PII patterns → `[REDACTED]`. Log redactions. |
| **Tech Stack** | LangChain, ChromaDB, tiktoken, Pydantic v2 |
| **Deliverables** | `src/agents/nodes/context_builder_node.py`, `src/services/rag_service.py`, `src/services/token_budget.py`, `src/models/context_schemas.py`, `tests/unit/test_context_builder.py` |

#### Module M4: 11 Rule Types — LLM Integration

| Hạng mục | Chi tiết |
|----------|----------|
| **Nhiệm vụ chính** | Updated Rule Proposer prompt & parser cho 11 types; LLM Guardrails. |
| **Chi tiết kỹ thuật** | **1) Prompt V2:** 11 rule types với examples + constraints. Include Data Dictionary context. Few-shot cho STATISTICAL_DISTRIBUTION, TEMPORAL_CONSISTENCY. **2) Output Parser V2:** `TableRuleProposalV2` + `RuleParametersV2`. Strict `model_validator`. **3) Guardrails:** Pre-call: token budget check, context completeness. Post-call: parse → evidence cross-ref → dedup → range validation. Reject ratio < 20%. **4) Multi-Model:** `LLMAdapter` abstract + OpenAI/Anthropic/Google/Mistral implementations. Unified `with_structured_output()`. |
| **Tech Stack** | LangChain, Pydantic v2, langchain-openai/anthropic/google-genai/mistralai |
| **Deliverables** | `src/prompts/rule_proposer_v2_prompt.py`, `src/models/rule_schemas_v2.py`, `src/services/llm_adapter.py`, `src/agents/nodes/rule_proposer_node_v2.py` |

#### Module M6: Dynamic Visualization — Frontend

| Hạng mục | Chi tiết |
|----------|----------|
| **Nhiệm vụ chính** | Refactor monolithic `App.tsx` thành component architecture; interactive dashboards. |
| **Chi tiết kỹ thuật** | **1) Component Architecture:** Tách `App.tsx` (90KB) thành 15+ components: `DatasetCatalog`, `ProfileViewer`, `RuleReviewBoard`, `RuleEditor`, `TestExecutionConsole`, `AnomalyDashboard`, `AnomalyDrillDown`, `TrendAnalysis`, `DQScoreCard`, `StewardInsights`, `UserManagement`, `AuditLog`. **2) Data Catalog:** Interactive table, status badges, quick actions, schema viewer modal. **3) HITL Board:** Ant Design Table + filters (dimension, severity, status). Inline editing. Bulk approve/reject. Confidence progress bars. AI reasoning vs evidence comparison. **4) Anomaly Dashboard:** Recharts `LineChart` — DQ score time-series + anomaly markers. `ScatterPlot` — violation rate vs baseline. Click-to-drill modal. **5) Trend Analysis:** Multi-axis chart. Date range picker. Dataset/dimension filter. Export PDF/CSV. **6) Real-time:** WebSocket cho job status. Progress bars. Toast notifications. **7) Responsive:** Ant Design Grid. Desktop (1440px), tablet (768px), mobile (375px). |
| **Tech Stack** | React 18, TypeScript, Ant Design 5.x, Recharts, React Router v6, Axios, WebSocket |
| **Deliverables** | `frontend/src/components/` (15+ files), `frontend/src/pages/`, `frontend/src/hooks/` (useWebSocket, useDQData, useAuth), `frontend/src/api/client.ts`, updated `styles.css` |

#### Semantic Contract Reviewer UI

| Hạng mục | Chi tiết |
|----------|----------|
| **Nhiệm vụ chính** | UI cho Steward xem/edit AI-generated Data Dictionary + semantic contracts. |
| **Chi tiết kỹ thuật** | **1) Viewer:** Hiển thị dictionary. Steward edit business names/descriptions. Version tracking. **2) Diff View:** Show diff giữa versions. Highlight changes. **3) Export:** Download Markdown, JSON, YAML. dbt `schema.yml` compatible. |
| **Tech Stack** | React, Ant Design, react-diff-viewer, js-yaml |
| **Deliverables** | `frontend/src/components/SemanticContractViewer.tsx`, `ContractDiffView.tsx`, `ContractExporter.tsx` |

---

## 4. MA TRẬN TƯƠNG TÁC VÀ TÍCH HỢP (INTEGRATION MATRIX)

### 4.1 API Contract Definitions

#### Contract 1: Frontend (Đạt) ↔ Backend API (Kiên + Phong)

| Interface | Endpoint | Request | Response | Owner |
|-----------|----------|---------|----------|-------|
| Dataset Registration | `POST /api/v1/datasets/register` | `{name, description, connection_config, source_type}` | `{dataset_id, status: "REGISTERED"}` | Kiên (API), Đạt (UI) |
| Dataset Schema | `GET /api/v1/datasets/{id}/schema` | — | `{dataset_id, columns: [{name, type, nullable, stats}]}` | Kiên |
| DQ Proposal Trigger | `POST /api/v1/dq/propose` | `{dataset_id, sampling_rate?}` | `{run_id, status: "QUEUED"}` | Kiên |
| Rule Review List | `GET /api/v1/dq/runs/{run_id}/rules` | `?status=&dimension=` | `{rules: [RuleReviewResponse]}` | Kiên |
| Rule Update (HITL) | `PATCH /api/v1/dq/runs/{rid}/rules/{rule_id}` | `RuleUpdateRequest` | `{updated: RuleReviewResponse}` | Kiên + Đạt |
| Job Status Stream | `WS /api/v1/ws/jobs/{job_id}` | — | `{phase, progress, message, ts}` | Phong + Đạt |
| Anomaly Data | `GET /api/v1/anomalies/{dataset_id}` | `?from=&to=&rule_type=` | `{anomalies, trend}` | Kiên + Chiến |
| Dataset Understanding | `GET /api/v1/datasets/{id}/understanding` | — | `DatasetUnderstanding` | Kiên + Đạt |

#### Contract 2: LLM Outputs (Đạt) ↔ Test Compiler (Kiên)

| Handoff | Input Schema | Output Schema | Validation |
|---------|-------------|---------------|-----------|
| Proposer → Compiler | `TableRuleProposalV2` | `list[CompiledTest]` | Column exists in schema registry. Params pass RuleParametersV2 validation. Evidence keys mapped. |
| Compiler → Runner | `CompiledTest {sql, bind_params, rule_id}` | `TestResult {status, violation_count, total_rows, sample_ids}` | SQL passes SQLValidator: SELECT-only, known identifiers, no comments, single statement, bind params only. |
| Understanding → Context | `DatasetUnderstanding` | `ContextPayload` | Dictionary covers all profile columns. Semantic types in allowed enum. |

#### Contract 3: Profiler & Eval (Chiến) ↔ Agent & CI (Đạt + Phong)

| Handoff | Producer | Consumer | Format | Frequency |
|---------|----------|----------|--------|-----------|
| Profile Stats | Chiến | Đạt (understanding_agent, context_builder) | `ProfileResult` Pydantic | Per dataset run |
| Anomaly Scores | Chiến | Kiên (persist), Đạt (dashboard) | `AnomalyResult` Pydantic | Per test run |
| DeepEval Metrics | Chiến | Phong (CI gate), Đạt (prompt tuning) | `BenchmarkReport` JSON | Nightly CI |
| Model Performance | Chiến | Đạt (Trend Analysis UI) | `ModelMetrics` JSON | Per model re-train |

### 4.2 Integration Testing Strategy

| Test Type | Scope | Owners | Trigger |
|-----------|-------|--------|---------|
| **Unit Contract Tests** | Pydantic schema serialization at each boundary | All | Every PR |
| **Integration Smoke** | E2E: Register → Profile → Propose → HITL → Execute → Report | Kiên + Đạt | Nightly |
| **Frontend API Contract** | TypeScript type assertions vs backend schemas | Đạt + Kiên | Every PR |
| **DeepEval Regression Gate** | Block merge if Faithfulness < 0.7 or Executability < 0.9 | Chiến + Phong | `main` branch |

---

## 5. LỘ TRÌNH TRIỂN KHAI (TIMELINE & PHASES)

---

### Phase 1: Foundation & Profiling (Tuần 1–2)

**Mục tiêu:** Nền tảng kỹ thuật vững chắc — multi-dataset, state machine, CI/CD, enhanced profiling.

| Thành viên | Nhiệm vụ | Deliverables | Phụ thuộc |
|------------|----------|-------------|-----------|
| **Kiên** | M1: Schema Registry + Connector abstraction. M7: JobPhase enum + TransitionValidator + checkpoint. | `dataset_schema.py`, `connector_factory.py`, `state_machine.py`, migrations | — |
| **Chiến** | Enhanced Profiler: DuckDB-based, extended metrics, cross-column correlation. | `profiling_engine.py`, `profile_models.py`, migration | M1 schema (Kiên) |
| **Phong** | CI/CD: GitHub Actions (lint + test + build). Multi-stage Dockerfile. Docker Compose prod. | `.github/workflows/ci.yml`, `Dockerfile`, `docker-compose.prod.yml` | — |
| **Đạt** | Frontend refactor: Component architecture, base layout, routing, DatasetCatalog. | `frontend/src/components/`, `pages/`, `hooks/` | M1 APIs (Kiên) |

**Definition of Done Phase 1:**
- [ ] Đăng ký & ingest ≥ 2 dataset types thành công
- [ ] State machine persist & recover job phases
- [ ] CI pipeline green (lint + 100% unit tests pass)
- [ ] Frontend components render DatasetCatalog từ API

---

### Phase 2: Agent Core & Semantic Contract (Tuần 3–4)

**Mục tiêu:** Intelligence layer — Understanding Agent, Context Builder, DeepEval baseline, observability.

| Thành viên | Nhiệm vụ | Deliverables | Phụ thuộc |
|------------|----------|-------------|-----------|
| **Kiên** | RAG infra: ChromaDB setup, embedding pipeline, understanding API endpoints. | `rag_service.py`, API routes, ChromaDB Docker | Phase 1 |
| **Chiến** | DeepEval baseline: Labeled test dataset, Faithfulness + Executability metrics. | `eval/benchmark_pipeline.py`, `eval/metrics/`, `eval/datasets/` | Rule format (Đạt) |
| **Phong** | Observability: Structured logging, LangSmith, OpenTelemetry, health endpoints. | `src/core/logging.py`, `tracing.py`, `health.py` | Phase 1 CI |
| **Đạt** | M2: Understanding Agent + prompt + parser. M3: Context Builder + token budget + PII guard. | `understanding_agent_node.py`, `context_builder_node.py`, schemas | Profiler (Chiến), RAG (Kiên) |

**Definition of Done Phase 2:**
- [ ] Understanding Agent sinh dictionary với ≥ 80% semantic accuracy
- [ ] Context Builder output ≤ 4000 tokens, zero PII
- [ ] DeepEval baseline metrics recorded
- [ ] LangSmith traces visible for all LangGraph runs

---

### Phase 3: Rule Proposal, HITL & Execution Engine (Tuần 5–6)

**Mục tiêu:** Complete rule lifecycle — 11-type catalog, compiler, HITL, anomaly upgrade, async.

| Thành viên | Nhiệm vụ | Deliverables | Phụ thuộc |
|------------|----------|-------------|-----------|
| **Kiên** | M4 Compiler: 11 SQL templates, RuleCompiler, SQLValidator, dbt YAML generator. | `rule_compiler.py`, `sql_templates/`, 22 test cases | M4 schemas (Đạt) |
| **Chiến** | Anomaly upgrade: IsolationForest, dynamic threshold, ensemble scoring, root cause. | `anomaly_engine.py`, `dynamic_threshold.py`, tests | Test results (Kiên) |
| **Phong** | Async: Celery + Redis, task definitions, scheduled execution, locking, retry + circuit breaker. | `celery_app.py`, `dq_tasks.py`, `retry_policy.py`, Docker services | Phase 2 observability |
| **Đạt** | M4 LLM: Prompt V2 cho 11 types, parser V2, guardrails. HITL Board UI (table + inline edit + bulk). | `rule_proposer_v2_prompt.py`, `RuleReviewBoard.tsx`, `RuleEditor.tsx` | Compiler (Kiên), Profiler (Chiến) |

**Definition of Done Phase 3:**
- [ ] All 11 rule types: propose → compile → execute → detect anomaly
- [ ] HITL UI: approve/reject/edit rules, bulk operations
- [ ] Celery workers process jobs asynchronously
- [ ] Anomaly hybrid model: Z-score + IForest + dynamic threshold

---

### Phase 4: Optimization, Evals & Cloud Deployment (Tuần 7–8)

**Mục tiêu:** Production-readiness — visualization, full benchmark, cloud deploy, comprehensive testing.

| Thành viên | Nhiệm vụ | Deliverables | Phụ thuộc |
|------------|----------|-------------|-----------|
| **Kiên** | Integration testing, performance optimization, OpenAPI docs. | `tests/integration/`, API docs, benchmarks | All modules |
| **Chiến** | DeepEval full: Cross-model comparison, Correctness F1, model dashboard, threshold tuning. | Benchmark report, model matrix, tuned config | Full pipeline |
| **Phong** | Cloud Run deploy: Terraform, Secret Manager, Cloud SQL. Monitoring alerts. | Deploy scripts, Terraform, Grafana, alerts | All Docker |
| **Đạt** | M6: Anomaly Dashboard, Trend Analysis, DQ ScoreCard, Contract Viewer, WebSocket real-time. | 6+ dashboard components, WebSocket hooks | All APIs |

**Definition of Done Phase 4 (Gate 3 Complete):**
- [ ] System deployed on Cloud Run, SSL, auto-scaling
- [ ] DeepEval: Faithfulness ≥ 0.7, Executability ≥ 0.9, Correctness F1 ≥ 0.8
- [ ] All 6 major frontend views operational
- [ ] Zero raw PII in LLM payloads (verified by automated test)
- [ ] Audit log complete, structured traces in LangSmith
- [ ] Documentation: Architecture, API, Runbook, Deploy guide

---

> **Prepared by:** Senior AI Architect & Lead Technical Project Manager  
> **Review date:** 2026-08-19  
> **Next review:** Phase 1 completion checkpoint
