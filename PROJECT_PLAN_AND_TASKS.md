# RIDEPULSE DQ — KẾ HOẠCH KỸ THUẬT & PHÂN CÔNG THỰC HIỆN

> **Dự án:** RidePulse DQ — AI Agent Xây dựng, Kiểm tra Data Quality & Phát hiện Bất thường  
> **Phiên bản:** v2.0 (Gate 3 — Production-Ready)  
> **Ngày lập:** 2026-08-19  
> **Team:** Kiên · Chiến · Phong · Đạt

---

## TECH STACK CHUẨN (CHỈ SỬ DỤNG CÁC CÔNG NGHỆ SAU)

| # | Thành phần | Công nghệ |
|---|-----------|-----------|
| 1 | LLM Core | LLM sinh rule & diễn giải (OpenAI / Anthropic / Google GenAI / Mistral) |
| 2 | Agent Framework | LangGraph (StateGraph 4 node chính) |
| 3 | DQ Test Engine | Great Expectations / dbt tests |
| 4 | Anomaly Detection | scikit-learn (Isolation Forest, Z-score) |
| 5 | Orchestrator & Scheduler | Airflow hoặc Dagster |
| 6 | Data Warehouse | Snowflake / BigQuery |
| 7 | Vector Storage | Vector DB (ChromaDB — lưu embeddings lịch sử rule) |
| 8 | Backend Core | FastAPI (Python) |
| 9 | Frontend Web | React + Ant Design |
| 10 | DevOps & Deployment | Docker + Cloud Run |

---

## 1. TỔNG QUAN HỆ THỐNG & ĐỊNH HƯỚNG CẢI TIẾN

### 1.1 Hiện trạng & Bài toán

Dữ liệu vận hành dịch vụ gọi xe (trips, drivers, customers, payments) thường xuyên chứa giá trị null, sai định dạng, ngoại lai — nhưng đội Data phải viết tay hàng trăm bộ test dbt thủ công. Hệ thống Gate 2 MVP hiện tại đã xây dựng được luồng cơ bản end-to-end nhưng còn tồn tại các hạn chế:

| # | Hạn chế | Ảnh hưởng |
|---|---------|-----------|
| L1 | **Single-dataset cố định** — Schema `SourceRowModel` gắn chặt 21 cột NYC Yellow Taxi. Worker hard-code mapping vendor/payment. | Không mở rộng cho dataset khác mà không sửa code. |
| L2 | **Thiếu Dataset Understanding Agent** — LLM nhận stats thô nhưng thiếu ngữ cảnh nghiệp vụ (data dictionary, domain semantics). | Rule đề xuất generic, thiếu reasoning nghiệp vụ chất lượng. |
| L3 | **Thiếu Dynamic Context Builder** — Prompt gửi LLM không tự động enrich từ historical rules (RAG) và business metadata. | Confidence score không phản ánh đúng chất lượng suy luận. |
| L4 | **Rule Catalog chưa đủ** — 9 rule types, thiếu String Length, Distribution Quantiles, Schema Conformance, Anomaly Outlier Check. | Không cover đủ 6 DQ dimensions cho production. |
| L5 | **Thiếu đo lường LLM offline** — Không có benchmark đánh giá Faithfulness, Executability, Correctness. | Không track regression khi đổi model/prompt. |
| L6 | **UI tĩnh** — Frontend `App.tsx` monolith 90KB, thiếu time-series chart, trend analysis, drill-down. | UX kém cho Data Steward phân tích pattern. |
| L7 | **Thiếu pipeline định kỳ** — Chưa có orchestrator (Airflow/Dagster) chạy DQ checks theo lịch tự động. | Phụ thuộc hoàn toàn vào trigger thủ công. |
| L8 | **Chưa tích hợp warehouse chuẩn** — Chỉ chạy trên SQLite/PostgreSQL local, chưa kết nối Snowflake/BigQuery. | Không kiểm tra được dữ liệu production quy mô lớn. |

### 1.2 Chiến lược cải tiến — 7 Module

Mỗi module được map trực tiếp vào Tech Stack chỉ định:

| Module | Tên | Giải quyết | Tech Stack áp dụng |
|--------|-----|-----------|---------------------|
| **M1** | Multi-Dataset & Warehouse Integration | L1, L8 | FastAPI, Snowflake/BigQuery, Great Expectations |
| **M2** | Dataset Understanding Agent | L2 | LangGraph, LLM, Vector DB |
| **M3** | Dynamic Context Builder (Semantic Contract) | L3 | LangGraph, Vector DB, LLM |
| **M4** | 11 Cataloged DQ Rules & Test Compiler | L4 | Great Expectations / dbt tests |
| **M5** | DeepEval Offline Validation | L5 | scikit-learn (metrics), Great Expectations |
| **M6** | Dynamic UI & Dashboard | L6 | React + Ant Design |
| **M7** | Orchestrated Pipeline & Resilience | L7 | Airflow/Dagster, Docker, Cloud Run |

---

## 2. KIẾN TRÚC AGENT & LUỒNG THỰC THI (LANGGRAPH WORKFLOW)

### 2.1 Tổng quan kiến trúc

```
┌───────────────────────────────────────────────────────────────────────┐
│                        React + Ant Design UI                         │
│  (Data Catalog, HITL Review Board, Anomaly Dashboard, Trend Charts)  │
└─────────────────────────────┬─────────────────────────────────────────┘
                              │ REST API
┌─────────────────────────────▼─────────────────────────────────────────┐
│                         FastAPI Backend                                │
│  (Auth/RBAC, Dataset CRUD, Rule CRUD, Job Management, WebSocket)      │
└──────┬──────────────┬─────────────────┬──────────────────┬────────────┘
       │              │                 │                  │
       ▼              ▼                 ▼                  ▼
┌──────────┐   ┌─────────────┐   ┌───────────┐   ┌────────────────┐
│ LangGraph│   │ Snowflake / │   │ Vector DB │   │ Airflow/Dagster│
│ Agent    │   │ BigQuery    │   │ (ChromaDB)│   │ Scheduler      │
│ (4 Nodes)│   │ Warehouse   │   │ Rule      │   │ DQ Pipeline    │
│          │   │             │   │ Embeddings│   │ DAGs           │
└──────────┘   └─────────────┘   └───────────┘   └────────────────┘
       │              │
       ▼              ▼
┌──────────────────────────────┐
│  Great Expectations / dbt    │
│  Test Execution Engine       │
└──────────────────────────────┘
```

### 2.2 LangGraph StateGraph — 4 Node chính

```
                        ┌────────────┐
                        │   START    │
                        └─────┬──────┘
                              │ Input: dataset_id, warehouse_connection
                              ▼
                   ┌─────────────────────┐
                   │  1. PROFILER NODE   │
                   │  (Quét metadata &   │
                   │   thống kê cột)     │
                   └─────────┬───────────┘
                             │ Output: dataset_profile, data_dictionary
                             │ (query stats từ Snowflake/BigQuery)
                             ▼
                  ┌──────────────────────┐
                  │ 2. RULE PROPOSER NODE│
                  │  (LLM sinh 11-type   │
                  │   DQ rules + HITL)   │
                  └─────────┬────────────┘
                            │ Context: profile + Vector DB history + dictionary
                            │ Output: proposed_rules (Pydantic structured)
                            │
                   ┌────────▼────────┐
                   │  ⏸ HITL GATE   │
                   │  Data Steward   │
                   │  Review via UI  │
                   │  (Ant Design)   │
                   └────────┬────────┘
                            │ Only APPROVED rules pass
                            ▼
                ┌────────────────────────┐
                │ 3. TEST GENERATOR NODE │
                │  (Compile rules →      │
                │   GE Expectations /    │
                │   dbt YAML tests)      │
                └────────────┬───────────┘
                             │ Output: GE expectation suite / dbt schema.yml
                             │ Execute tests trên Snowflake/BigQuery
                             ▼
                ┌────────────────────────┐
                │ 4. ANOMALY DETECTOR    │
                │    NODE                │
                │  (Isolation Forest /   │
                │   Z-score trên kết quả │
                │   test)                │
                └────────────┬───────────┘
                             │ Output: anomalies, dq_score, alerts
                             ▼
                        ┌────────┐
                        │  END   │
                        └────────┘
```

### 2.3 Graph State Schema

```python
class AgentState(TypedDict, total=False):
    # Input
    dataset_id: str
    warehouse_connection: dict  # Snowflake/BigQuery connection config

    # Node 1: Profiler
    dataset_profile: dict  # Per-column stats từ warehouse
    data_dictionary: dict  # LLM-generated semantic metadata

    # Node 2: Rule Proposer
    context_payload: dict  # Assembled context (profile + RAG + dictionary)
    proposed_rules: list  # LLM structured output: list[ProposedRule]
    approved_rules: list  # Sau HITL gate: chỉ rules APPROVED

    # Node 3: Test Generator
    ge_expectation_suite: dict  # Great Expectations JSON suite
    dbt_test_yaml: str  # dbt schema.yml test definitions
    test_results: list  # Kết quả thực thi từ GE/dbt

    # Node 4: Anomaly Detector
    anomalies: list  # Danh sách anomaly detected
    dq_score: float  # Điểm chất lượng tổng thể (0–100)
    dq_grade: str  # Xếp hạng: A / B / C / D

    # Metadata
    run_id: str
    error: str
```

### 2.4 Cơ chế HITL (Human-in-the-Loop)

**Luồng hoạt động:**

1. **Rule Proposer Node** sinh `proposed_rules` → persist vào DB với trạng thái `PENDING`.
2. **LangGraph interrupt**: Graph pause tại HITL Gate. FastAPI trả `run_id` cho frontend.
3. **Data Steward** mở React + Ant Design UI → xem danh sách rules → thực hiện:
   - **Approve**: Rule chuyển sang `APPROVED` (giữ nguyên hoặc chỉnh parameters).
   - **Reject**: Rule chuyển sang `REJECTED` (bắt buộc ghi `review_note`).
   - **Edit**: Chỉnh threshold/severity → lưu `edited_parameters` tách biệt `ai_parameters`.
4. **Resume graph**: Khi Steward hoàn tất review, frontend gọi `POST /dq/runs/{run_id}/resume`.
5. **Test Generator Node** chỉ nhận `approved_rules` — rules PENDING/REJECTED không bao giờ được compile.

**Governance:**
- LLM chỉ nhận aggregate metadata/statistics — KHÔNG gửi raw data rows, PII, credentials.
- Mọi hành động review được ghi Audit Log (actor, action, timestamp, before/after).

### 2.5 Tương tác với Vector DB & Warehouse

| Tương tác | Thời điểm | Mục đích |
|-----------|-----------|----------|
| **Profiler → Snowflake/BigQuery** | Node 1 | Chạy SQL aggregate queries (`COUNT`, `AVG`, `STDDEV`, `NULL` rate, `DISTINCT`, quantiles) trên bảng dữ liệu production. |
| **Rule Proposer → Vector DB** | Node 2 (context building) | Query embeddings lịch sử rules đã approved/rejected trên cùng dataset type → cung cấp few-shot context cho LLM. |
| **Rule Proposer → Vector DB** | Node 2 (post-persist) | Embed rule mới vào Vector DB sau khi HITL approve — enriching future queries. |
| **Test Generator → Snowflake/BigQuery** | Node 3 | Thực thi Great Expectations checkpoints hoặc `dbt test` trực tiếp trên warehouse. |
| **Anomaly Detector → DB** | Node 4 | Query historical test results để tính Z-score trend, train Isolation Forest. |

---

## 3. DANH MỤC 11 RULE CHUẨN HÓA & TEST COMPILER

### 3.1 Bảng quy chuẩn 11 DQ Rules

| # | Rule Type | DQ Dimension | Input Parameters | Great Expectations Target | dbt Test Target |
|---|-----------|-------------|------------------|--------------------------|-----------------|
| **R1** | `NULLNESS` | Completeness | `{column}` | `expect_column_values_to_not_be_null` | `not_null` |
| **R2** | `UNIQUENESS` | Uniqueness | `{column}` | `expect_column_values_to_be_unique` | `unique` |
| **R3** | `RANGE` | Validity | `{column, min?, max?}` — ít nhất 1 required | `expect_column_values_to_be_between(min_value, max_value)` | `dbt_utils.accepted_range(min_value, max_value)` |
| **R4** | `FORMAT_REGEX` | Validity | `{column, regex: str}` | `expect_column_values_to_match_regex(regex)` | Custom dbt test `assert_regex_match` |
| **R5** | `FRESHNESS` | Freshness | `{column, max_age_hours: float}` | `expect_column_max_to_be_between(min_value=now()-max_age)` | `dbt_utils.recency(datepart, field, interval)` |
| **R6** | `CROSS_COLUMN` | Consistency | `{column_a, column_b, operator}` — operator ∈ {`<=`, `<`, `>=`, `>`, `=`, `!=`} | `expect_column_pair_values_A_to_be_greater_than_B` (hoặc variant) | Custom dbt test `assert_column_comparison` |
| **R7** | `SET_MEMBERSHIP` | Validity | `{column, accepted_values: list[str]}` — non-empty, max 50 | `expect_column_values_to_be_in_set(value_set)` | `accepted_values(values)` |
| **R8** | `STRING_LENGTH` | Validity | `{column, min_length?, max_length?}` | `expect_column_value_lengths_to_be_between(min, max)` | Custom dbt test `assert_string_length` |
| **R9** | `DISTRIBUTION_QUANTILES` | Accuracy | `{column, method: "zscore"\|"iqr", threshold: float}` | `expect_column_stdev_to_be_between` + custom expectation | Custom dbt test `assert_distribution_bound` |
| **R10** | `SCHEMA_CONFORMANCE` | Consistency | `{expected_columns: list[{name, type}]}` | `expect_table_columns_to_match_set` + `expect_column_values_to_be_of_type` | `dbt_utils.equal_rowcount` + schema contract |
| **R11** | `ANOMALY_OUTLIER` | Accuracy | `{column, contamination: float, n_estimators: int}` | Custom GE expectation wrapping Isolation Forest | Custom dbt test triggering scikit-learn |

### 3.2 Input Parameter Schema (Pydantic v2)

```python
class RuleType(StrEnum):
    NULLNESS = "NULLNESS"
    UNIQUENESS = "UNIQUENESS"
    RANGE = "RANGE"
    FORMAT_REGEX = "FORMAT_REGEX"
    FRESHNESS = "FRESHNESS"
    CROSS_COLUMN = "CROSS_COLUMN"
    SET_MEMBERSHIP = "SET_MEMBERSHIP"
    STRING_LENGTH = "STRING_LENGTH"
    DISTRIBUTION_QUANTILES = "DISTRIBUTION_QUANTILES"
    SCHEMA_CONFORMANCE = "SCHEMA_CONFORMANCE"
    ANOMALY_OUTLIER = "ANOMALY_OUTLIER"


class RuleParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # R3: RANGE
    min: float | None = None
    max: float | None = None
    # R4: FORMAT_REGEX
    regex: str | None = None
    # R5: FRESHNESS
    max_age_hours: float | None = None
    # R6: CROSS_COLUMN
    column_b: str | None = None
    operator: Literal["<=", "<", ">=", ">", "=", "!="] | None = None
    # R7: SET_MEMBERSHIP
    accepted_values: list[str] | None = None
    # R8: STRING_LENGTH
    min_length: int | None = None
    max_length: int | None = None
    # R9: DISTRIBUTION_QUANTILES
    method: Literal["zscore", "iqr"] | None = None
    threshold: float | None = None
    # R10: SCHEMA_CONFORMANCE
    expected_columns: list[dict] | None = None
    # R11: ANOMALY_OUTLIER
    contamination: float | None = None
    n_estimators: int | None = None
```

### 3.3 Test Compiler Logic

Test Generator Node sử dụng deterministic mapping — **KHÔNG** để LLM tự viết SQL/YAML:

| Rule Type | Compiler Action |
|-----------|----------------|
| R1–R8 | Map trực tiếp sang built-in Great Expectations expectation hoặc built-in dbt test. Template rendering với bind parameters. |
| R9 (Distribution) | Sinh custom GE expectation class wrapping `scipy.stats` Z-score/IQR calculation. |
| R10 (Schema) | Sinh `expect_table_columns_to_match_set` + per-column type assertions. |
| R11 (Anomaly Outlier) | Sinh custom GE expectation wrapping `sklearn.ensemble.IsolationForest`. Fit trên column values, predict outliers. |

**Output format:**
- **Great Expectations:** JSON expectation suite file → chạy qua `checkpoint.run()` trên warehouse connection.
- **dbt tests:** YAML `schema.yml` file → chạy qua `dbt test --target <warehouse_profile>`.

---

## 4. BẢNG PHÂN CÔNG NHIỆM VỤ CHI TIẾT (WBS)

---

### 4.1 KIÊN — AI Infrastructure (Backend & Data Engine)

| Module | Nhiệm vụ (Làm gì?) | Phương pháp (Làm như thế nào?) | Tech Stack | Deliverables |
|--------|---------------------|-------------------------------|------------|-------------|
| **M1: Multi-Dataset & Warehouse** | Thiết kế API đăng ký dataset, kết nối Snowflake/BigQuery, schema registry cho N datasets. | Tạo bảng `dataset_schemas` (JSONB column definitions). Abstract `WarehouseConnector` class với 2 implementations: `SnowflakeConnector`, `BigQueryConnector`. Mỗi connector implement `introspect_schema()`, `run_aggregate_stats()`, `execute_ge_checkpoint()`. Alembic migration backward-compatible. | FastAPI, Snowflake Python SDK, BigQuery Python SDK, SQLAlchemy | `src/services/warehouse_connector.py`, `src/services/schema_registry.py`, `src/models/dataset_schema.py`, API: `POST /datasets/register`, `GET /datasets/{id}/schema` |
| **M4: Test Compiler Engine** | Xây dựng deterministic compiler mapping 11 rule types → Great Expectations expectations / dbt tests. | Dictionary `RuleType → GEExpectationTemplate`. Mỗi template là parameterized JSON config. Compiler validate column identifiers against schema registry trước khi render. Sinh GE `expectation_suite.json` và dbt `schema.yml`. Chạy GE checkpoint hoặc `dbt test` trên warehouse. | Great Expectations, dbt-core, FastAPI | `src/services/test_compiler.py`, `src/services/ge_runner.py`, `src/services/dbt_runner.py`, GE custom expectations cho R9/R10/R11, `tests/unit/test_compiler.py` (22 test cases) |
| **Vector DB Setup** | Cấu hình ChromaDB lưu embeddings lịch sử rules; API tra cứu similar rules. | Embed (rule_type + column_name + parameters + ai_reasoning) thành vector. Collection per dataset_type. CRUD operations: insert on APPROVE, query on context building. | Vector DB (ChromaDB) | `src/services/vector_store.py`, ChromaDB collection schema, API: `GET /rules/similar?column=&type=` |
| **Backend Core** | API endpoints cho toàn bộ lifecycle: propose, review, execute, results, anomalies. | RESTful design. Pydantic v2 request/response models. RBAC middleware (Steward vs Viewer). Job queue management. Audit event logging. | FastAPI | `src/api/routes.py` (updated), `src/models/schemas.py` (updated), `src/api/dependencies.py` |

---

### 4.2 CHIẾN — AI Infrastructure (Anomaly & Profiling)

| Module | Nhiệm vụ (Làm gì?) | Phương pháp (Làm như thế nào?) | Tech Stack | Deliverables |
|--------|---------------------|-------------------------------|------------|-------------|
| **Profiling Engine** | Xây dựng module profiling thống kê metadata chi tiết từ warehouse, cung cấp input cho Profiler Node. | Chạy SQL aggregate queries trên Snowflake/BigQuery: `COUNT`, `COUNT(DISTINCT)`, `AVG`, `STDDEV`, `MIN`, `MAX`, `PERCENTILE_CONT` (p5, p25, p50, p75, p95), `NULL` rate, `APPROX_TOP_COUNT` (top-10 frequent values). Cross-column: Pearson correlation cho numeric pairs. Output: `ProfileResult` Pydantic model per column. | Snowflake/BigQuery SQL, Great Expectations profiler | `src/services/profiling_engine.py`, `src/models/profile_models.py`, SQL template files cho Snowflake và BigQuery dialects |
| **Anomaly Detection Engine** | Xây dựng hybrid anomaly detection: Statistical (Z-score) + ML (Isolation Forest) + Dynamic Thresholding. | **Z-score layer:** Tính Z-score trên violation_rate time-series (≥5 historical runs). Alert khi Z > 2.5 AND rate > 1%. **Isolation Forest layer:** Train trên feature vector (violation_rate, null_rate_delta, row_count_change) per column. Re-train mỗi 50 runs hoặc weekly. **Dynamic Threshold:** Exponential Moving Average (EMA) × 1.5 thay fixed 5% cold-start. **Ensemble:** `score = w1×zscore_flag + w2×iforest_score + w3×ema_breach`. Weights configurable. | scikit-learn (IsolationForest, StandardScaler) | `src/services/anomaly_engine.py`, `src/services/dynamic_threshold.py`, `src/models/anomaly_models.py`, `tests/unit/test_anomaly_engine.py` |
| **M5: DeepEval Offline Validation** | Benchmark pipeline đánh giá chất lượng LLM rule proposals trên 3 trục. | **Faithfulness:** So sánh `ai_reasoning` citations vs actual evidence keys. Score = intersection / cited. **Executability:** Compile mỗi proposed rule qua Test Compiler → binary pass/fail. Score = compiled / total. **Correctness:** Tạo labeled dataset (injected null 5-20%, out-of-range, duplicates, format violations). Run compiled tests → Precision, Recall, F1. **Cross-model:** So sánh OpenAI, Anthropic, Google, Mistral. | scikit-learn (precision/recall/f1 metrics), Great Expectations (compile verification) | `eval/benchmark_pipeline.py`, `eval/metrics/faithfulness.py`, `eval/metrics/executability.py`, `eval/metrics/correctness.py`, `eval/datasets/` (labeled test data), `eval/results/` |
| **Threshold Optimization** | Tối ưu ngưỡng cảnh báo giảm false positive. | Grid search trên threshold parameters (Z-score threshold, IForest contamination, EMA window). Evaluate trên labeled anomaly dataset. Output: optimal config per dataset type. | scikit-learn | `eval/threshold_optimizer.py`, `config/anomaly_thresholds.yml` |

---

### 4.3 PHONG — AI Infrastructure (Orchestration & Deployment)

| Module | Nhiệm vụ (Làm gì?) | Phương pháp (Làm như thế nào?) | Tech Stack | Deliverables |
|--------|---------------------|-------------------------------|------------|-------------|
| **M7: Orchestration Pipeline** | Xây dựng DAG/Pipeline chạy DQ checks định kỳ trên warehouse. | **DAG Definition:** `ridepulse_dq_pipeline` DAG với tasks: `profile_dataset` → `propose_rules` (optional, nếu chưa có active rules) → `execute_tests` → `detect_anomalies` → `send_alerts`. **Schedule:** Configurable per dataset: `MANUAL`, `HOURLY`, `DAILY`, `WEEKLY`. **Retry:** Max 3 retries per task, exponential backoff. **Alerting:** On failure → Slack/Email webhook. | Airflow hoặc Dagster | `dags/ridepulse_dq_dag.py` (Airflow) hoặc `pipelines/ridepulse_dq.py` (Dagster), `dags/config.yml` (schedule config per dataset), Alert integration config |
| **Worker Management** | Quản lý background job execution, liveness monitoring. | FastAPI background tasks cho interactive jobs (user-triggered). Airflow/Dagster workers cho scheduled jobs. Health check endpoint `/health` báo cáo: DB connectivity, warehouse reachability, DAG status. | Airflow/Dagster, FastAPI | `src/api/health.py`, worker monitoring config, DAG health sensors |
| **Containerization** | Multi-stage Docker builds, production-ready containers. | **API Container:** Multi-stage build (builder → runtime). Target < 500MB. Non-root user. Health check instruction. **Worker Container:** Separate Dockerfile cho Airflow/Dagster worker. **Docker Compose:** Services: `api`, `worker`, `db` (PostgreSQL), `scheduler` (Airflow/Dagster). | Docker | `Dockerfile` (optimized), `Dockerfile.worker`, `docker-compose.yml` (updated), `docker-compose.prod.yml` |
| **Cloud Deployment** | Triển khai lên Google Cloud Run, CI/CD automation. | **Cloud Run:** API service (min 0, max 5 instances, 512MB RAM, 1 vCPU). Worker service (min 1). **CI/CD:** GitHub Actions → lint (ruff) → test (pytest) → Docker build → push GCR → deploy Cloud Run (staging → production). Warehouse credentials qua Secret Manager. | Docker, Cloud Run | `.github/workflows/ci.yml`, `.github/workflows/deploy.yml`, `scripts/deploy_cloud_run.sh`, Cloud Run service configs |

---

### 4.4 ĐẠT — AI Application (LangGraph Agent & Frontend UI)

| Module | Nhiệm vụ (Làm gì?) | Phương pháp (Làm như thế nào?) | Tech Stack | Deliverables |
|--------|---------------------|-------------------------------|------------|-------------|
| **M2: Dataset Understanding Agent** | Xây dựng logic trong Profiler Node để LLM tự động sinh Data Dictionary và Semantic Profile. | **Input:** `dataset_profile` (aggregate stats từ Profiler Engine). **LLM Call:** System prompt = "Data Analyst chuyên ride-hailing". Few-shot examples. Output = `DatasetUnderstanding` Pydantic model: per-column `business_name_vi`, `description`, `semantic_type` (CURRENCY/TIMESTAMP/GEO_ID/ENUM/IDENTIFIER/MEASUREMENT/FLAG), `business_rules`. **Governance:** Chỉ aggregate stats, KHÔNG raw data. `with_structured_output()`. Retry max 2. **Cache:** Per (dataset_id, schema_hash) trong Vector DB. | LangGraph, LLM, Vector DB | `src/agents/nodes/understanding_logic.py`, `src/models/understanding_schemas.py`, `src/prompts/understanding_prompt.py` |
| **M3: Dynamic Context Builder** | Auto-assemble LLM context cho Rule Proposer từ multiple sources. | **Assembly pipeline:** Merge 4 nguồn: (a) aggregate profile stats, (b) data dictionary (from Understanding), (c) historical rules (RAG query Vector DB — top-3 similar per column), (d) domain constraints (static config). **Token budget:** ≤ 4000 tokens. Priority: dictionary > profile > constraints > history. Auto-truncate. **Evidence tracking:** Mỗi context element có `evidence_key` cho DeepEval Faithfulness scoring. **PII Guard:** Scan cho email/phone/name patterns → `[REDACTED]`. | LangGraph, LLM, Vector DB | `src/agents/nodes/context_builder.py`, `src/services/rag_query.py`, `src/models/context_schemas.py` |
| **LangGraph 4-Node Graph** | Implement StateGraph với 4 node chính + HITL gate + error handling. | **Graph construction:** `StateGraph(AgentState)` → add 4 nodes + hitl_gate + error handler. Conditional edges: error → END. HITL → interrupt_before. **Node 1 (Profiler):** Call `profiling_engine` (Chiến) + `understanding_logic` (Đạt). **Node 2 (Rule Proposer):** Call `context_builder` → `LLM.with_structured_output(TableRuleProposal)` → validate → persist PENDING. **Node 3 (Test Generator):** Call `test_compiler` (Kiên) → execute via `ge_runner`/`dbt_runner` (Kiên). **Node 4 (Anomaly Detector):** Call `anomaly_engine` (Chiến) → compute dq_score → persist report. | LangGraph, LLM | `src/agents/graph.py` (rewritten), `src/agents/state.py` (updated), `src/agents/nodes/profiler_node.py`, `rule_proposer_node.py`, `test_generator_node.py`, `anomaly_detector_node.py` |
| **Prompt Engineering** | Thiết kế prompt templates cho Understanding Agent và Rule Proposer. | **Rule Proposer prompt:** Mô tả 11 rule types với examples/constraints. Include Data Dictionary context. Few-shot cho DISTRIBUTION_QUANTILES, SCHEMA_CONFORMANCE, ANOMALY_OUTLIER. System prompt enforce: chỉ dùng aggregate stats, output Tiếng Việt cho `rule_description`. **Output parser:** Pydantic `with_structured_output()`. Post-parse: evidence cross-ref, dedup, parameter range guard. Reject ratio < 20%. | LLM | `src/prompts/rule_proposer_prompt.py`, `src/prompts/understanding_prompt.py` |
| **M6: Frontend — Data Catalog** | Giao diện quản lý datasets: đăng ký, xem schema, trigger profiling. | Ant Design `Table` component listing datasets. Status badges (`Tag`): REGISTERED, PROFILED, ACTIVE. Quick action buttons: Profile, Propose Rules, Run DQ. Schema viewer `Modal` hiển thị column list + stats. | React, Ant Design | `frontend/src/components/DatasetCatalog.tsx` |
| **M6: Frontend — HITL Rule Approval Board** | Giao diện Data Steward duyệt/từ chối/chỉnh sửa rules do AI đề xuất. | Ant Design `Table` với filters (`Select`): by dimension, severity, status. Columns: Column Name, Rule Type, AI Reasoning, Confidence %, Parameters, Status, Actions. Inline editing (`Modal` + `Form` + `InputNumber`) cho parameters. Bulk approve/reject (`Checkbox` + `Popconfirm`). Side panel: AI reasoning vs evidence data comparison. | React, Ant Design | `frontend/src/components/RuleReviewBoard.tsx`, `frontend/src/components/RuleEditor.tsx` |
| **M6: Frontend — Anomaly Dashboard** | Dashboard trực quan hóa kết quả DQ và anomalies. | **Time-series chart:** Ant Design `Card` wrapping chart component — DQ score over time với anomaly markers (red dots). **Alert table:** Ant Design `Table` với `Badge` status. Nút "AI Diagnosis" mở `Modal` giải thích root cause. **Drill-down:** Click anomaly → detail view (rule, sample violations, trend). | React, Ant Design | `frontend/src/components/AnomalyDashboard.tsx`, `frontend/src/components/AnomalyDrillDown.tsx` |
| **M6: Frontend — Trend & Evaluation** | Biểu đồ xu hướng DQ score, pass/fail ratio, model evaluation metrics. | Multi-axis chart: DQ score trend + rule pass/fail ratio + anomaly count. `DatePicker.RangePicker` cho time filter. `Select` cho dataset/dimension. `Statistic` cards: Precision, Recall, F1. `Tabs` cho different views. Export button (CSV). | React, Ant Design | `frontend/src/components/TrendAnalysis.tsx`, `frontend/src/components/DQScoreCard.tsx` |
| **M6: Frontend — Semantic Contract Viewer** | UI cho Steward xem/edit Data Dictionary và semantic contracts. | Hiển thị dictionary do Understanding Agent sinh. Steward edit business names/descriptions. Version tracking (AI vs edited). Export: Markdown, JSON, dbt `schema.yml`. | React, Ant Design | `frontend/src/components/SemanticContractViewer.tsx` |
| **Frontend Architecture** | Refactor monolith `App.tsx` (90KB) thành modular components. | Tách thành 12+ components. React Router v6 routing. Typed API client (`axios`). Custom hooks: `useAuth`, `useDQData`, `useJobStatus`. 2 role-based layouts: Steward (full access), Viewer (read-only). | React, Ant Design | `frontend/src/pages/`, `frontend/src/components/`, `frontend/src/hooks/`, `frontend/src/api/client.ts`, `frontend/src/types/index.ts` |

---

## 5. MA TRẬN TÍCH HỢP & DỮ LIỆU (INTEGRATION MATRIX)

### 5.1 Sơ đồ tích hợp

```
   React + Ant Design ◄──── REST API / WebSocket ────► FastAPI
          │                                                │
          │ UI renders data                                │ Dispatch jobs
          │                                                │
          ▼                                                ▼
   [HITL Review Board]                              [LangGraph Agent]
   [Anomaly Dashboard]                                     │
   [Trend Analysis]                          ┌─────────────┼─────────────┐
                                             │             │             │
                                             ▼             ▼             ▼
                                        [Vector DB]    [LLM API]   [Warehouse]
                                        (ChromaDB)                 (Snowflake/
                                                                    BigQuery)
                                                                       │
                                                           ┌───────────┼───────────┐
                                                           ▼                       ▼
                                                   [Great Expectations]      [dbt tests]
                                                   (GE Checkpoints)         (dbt test)
                                                           │                       │
                                                           ▼                       ▼
                                                    [scikit-learn Anomaly Engine]
                                                    (Isolation Forest / Z-score)
                                                           │
                                                           ▼
                                                   [Airflow / Dagster]
                                                   (Scheduled DAG runs)
```

### 5.2 Contract Definitions

#### Interface 1: FastAPI ↔ React + Ant Design

| Endpoint | Method | Request | Response | Vai trò UI |
|----------|--------|---------|----------|-----------|
| `/api/v1/datasets` | GET | `?status=` | `{datasets: [DatasetResponse]}` | DatasetCatalog |
| `/api/v1/datasets/register` | POST | `{name, description, warehouse_type, connection_config}` | `{dataset_id, status}` | DatasetCatalog |
| `/api/v1/datasets/{id}/schema` | GET | — | `{columns: [{name, type, stats}]}` | Schema Modal |
| `/api/v1/dq/propose` | POST | `{dataset_id}` | `{run_id, status: "QUEUED"}` | Trigger button |
| `/api/v1/dq/runs/{run_id}/rules` | GET | `?status=&dimension=` | `{rules: [RuleReviewResponse]}` | RuleReviewBoard |
| `/api/v1/dq/runs/{rid}/rules/{rule_id}` | PATCH | `{status, edited_parameters?, review_note?}` | `{updated: RuleReviewResponse}` | RuleEditor |
| `/api/v1/dq/runs/{run_id}/rules/bulk-review` | POST | `{decisions: [{rule_id, status, ...}]}` | `{updated_count, rules}` | Bulk approve |
| `/api/v1/dq/runs/{run_id}/resume` | POST | — | `{status: "RUNNING"}` | Resume after HITL |
| `/api/v1/dq/runs/{run_id}/results` | GET | — | `{results: [TestResultResponse]}` | Results table |
| `/api/v1/anomalies/{dataset_id}` | GET | `?from=&to=` | `{anomalies, trend, dq_score}` | AnomalyDashboard |
| `/api/v1/datasets/{id}/understanding` | GET | — | `DatasetUnderstanding` | SemanticContractViewer |

#### Interface 2: LangGraph ↔ Vector DB & LLM

| Interaction | Direction | Data Format | Purpose |
|-------------|-----------|-------------|---------|
| Rule Proposer → Vector DB | Query | `{column_name, rule_type, dataset_type}` → top-3 similar embeddings | Retrieve historical rules cho few-shot context |
| HITL Approve → Vector DB | Insert | `{rule_embedding, metadata: {rule_type, column, parameters, outcome}}` | Enrich future rule proposals |
| Rule Proposer → LLM | Request | `ContextPayload` (aggregate stats + dictionary + RAG results) | Sinh `TableRuleProposal` structured output |
| Understanding → LLM | Request | `ProfileResult` (column stats only, no raw data) | Sinh `DatasetUnderstanding` structured output |

#### Interface 3: Airflow/Dagster ↔ Snowflake/BigQuery & GE/dbt

| DAG Task | Input | Execution | Output |
|----------|-------|-----------|--------|
| `profile_dataset` | `dataset_id`, warehouse connection | SQL aggregate queries trên warehouse | `ProfileResult` persisted to DB |
| `execute_ge_tests` | `expectation_suite.json`, warehouse datasource | `checkpoint.run()` trên Snowflake/BigQuery connection | `ValidationResult` JSON |
| `execute_dbt_tests` | `schema.yml`, dbt profile | `dbt test --target warehouse` | Test results JSON |
| `detect_anomalies` | Test results, historical data | scikit-learn Isolation Forest + Z-score | `AnomalyResult` list |
| `send_alerts` | Anomaly results | Webhook (Slack/Email) | Alert delivery confirmation |

#### Interface 4: scikit-learn Anomaly Engine ↔ Warehouse Data

| Data Flow | Source | Processing | Output |
|-----------|--------|-----------|--------|
| Historical violation rates | Query `dq_results` table (PostgreSQL) | Time-series construction per (rule_id, column) | Feature matrix for Z-score |
| Column value distributions | SQL query trên Snowflake/BigQuery | Extract sample + aggregate stats | Feature vectors for Isolation Forest |
| Anomaly scoring | Feature matrix | `IsolationForest.fit_predict()` + Z-score calculation + EMA threshold | Anomaly scores + classifications |

### 5.3 Integration Testing

| Test Type | Scope | Owners | Trigger |
|-----------|-------|--------|---------|
| **Unit Contract Tests** | Pydantic schema serialization tại mỗi boundary | All | Mỗi PR |
| **E2E Smoke Test** | Register → Profile → Propose → HITL → Execute GE/dbt → Anomaly → Report | Kiên + Đạt | Nightly |
| **DAG Integration Test** | Airflow/Dagster DAG renders correctly, task dependencies valid | Phong | Mỗi PR chạm DAG files |
| **DeepEval Regression** | Block merge nếu Faithfulness < 0.7 hoặc Executability < 0.9 | Chiến + Phong | `main` branch |

---

## 6. LỘ TRÌNH TRIỂN KHAI (4 PHASES)

---

### Phase 1: Foundation (Tuần 1–2)

**Mục tiêu:** Warehouse connection, FastAPI core, base UI, Vector DB schema.

| Thành viên | Nhiệm vụ | Deliverables |
|------------|----------|-------------|
| **Kiên** | Warehouse Connector (Snowflake/BigQuery SDK). Schema registry API. Vector DB (ChromaDB) setup + embedding pipeline. FastAPI core routes. | `warehouse_connector.py`, `schema_registry.py`, `vector_store.py`, API endpoints, migrations |
| **Chiến** | Profiling Engine: SQL aggregate queries cho Snowflake/BigQuery. `ProfileResult` Pydantic model. | `profiling_engine.py`, `profile_models.py`, SQL templates per dialect |
| **Phong** | Docker multi-stage build. Docker Compose (api + db + scheduler). CI pipeline (GitHub Actions: lint + test + build). | `Dockerfile`, `docker-compose.yml`, `.github/workflows/ci.yml` |
| **Đạt** | Frontend refactor: tách `App.tsx` → component architecture. DatasetCatalog UI. Base routing + layout. | `frontend/src/components/`, `frontend/src/pages/`, `frontend/src/hooks/`, `api/client.ts` |

**DoD Phase 1:**
- [ ] Kết nối thành công Snowflake hoặc BigQuery từ FastAPI
- [ ] Profiling engine trả về stats cho ≥ 1 dataset
- [ ] ChromaDB insert/query hoạt động
- [ ] CI pipeline green
- [ ] DatasetCatalog render danh sách datasets

---

### Phase 2: Agent Core (Tuần 3–4)

**Mục tiêu:** LangGraph 4 nodes, prompt templates, Understanding Agent, Context Builder.

| Thành viên | Nhiệm vụ | Deliverables |
|------------|----------|-------------|
| **Kiên** | Vector DB RAG query API cho historical rules. Test Compiler: mapping 11 rule types → GE expectations / dbt tests. | `rag_query.py`, `test_compiler.py`, GE custom expectations (R9, R10, R11) |
| **Chiến** | DeepEval baseline: labeled test dataset + Faithfulness + Executability metrics. Anomaly Engine: Z-score + Isolation Forest base implementation. | `eval/benchmark_pipeline.py`, `eval/metrics/`, `eval/datasets/`, `anomaly_engine.py` |
| **Phong** | Airflow/Dagster DAG definition: `ridepulse_dq_pipeline`. Task definitions + schedule config. Health endpoint. | `dags/ridepulse_dq_dag.py`, `dags/config.yml`, `src/api/health.py` |
| **Đạt** | LangGraph StateGraph: 4 nodes + HITL gate. Understanding Agent + Context Builder logic. Prompt templates cho 11 rule types. | `graph.py`, `state.py`, 4 node files, `understanding_logic.py`, `context_builder.py`, prompt files |

**DoD Phase 2:**
- [ ] LangGraph graph chạy end-to-end (mock mode)
- [ ] Understanding Agent sinh dictionary với ≥ 80% semantic accuracy
- [ ] Context Builder output ≤ 4000 tokens, zero PII
- [ ] DeepEval baseline metrics recorded
- [ ] DAG renders và chạy được locally

---

### Phase 3: HITL & Execution Engine (Tuần 5–6)

**Mục tiêu:** GE/dbt test generation + execution, Anomaly model upgrade, HITL UI, scheduled DAGs.

| Thành viên | Nhiệm vụ | Deliverables |
|------------|----------|-------------|
| **Kiên** | GE Runner: execute checkpoint trên warehouse. dbt Runner: `dbt test --target`. 22 compiler unit tests. API: resume, results, anomalies. | `ge_runner.py`, `dbt_runner.py`, `tests/unit/test_compiler.py`, API routes |
| **Chiến** | Anomaly Engine upgrade: Dynamic Threshold (EMA), ensemble scoring, root cause attribution. Threshold optimization grid search. | `dynamic_threshold.py`, updated `anomaly_engine.py`, `threshold_optimizer.py`, `anomaly_thresholds.yml` |
| **Phong** | DAG scheduling: per-dataset cron config. Retry policies (max 3, exponential). Alert webhooks (Slack/Email). Worker monitoring. | Updated DAGs, alert config, monitoring dashboards |
| **Đạt** | HITL Rule Approval Board (Ant Design). Rule Editor modal. Anomaly Dashboard (charts + drill-down). Semantic Contract Viewer. | `RuleReviewBoard.tsx`, `RuleEditor.tsx`, `AnomalyDashboard.tsx`, `AnomalyDrillDown.tsx`, `SemanticContractViewer.tsx` |

**DoD Phase 3:**
- [ ] Tất cả 11 rule types: propose → HITL review → compile GE/dbt → execute → detect anomaly
- [ ] HITL UI: approve/reject/edit rules, bulk operations
- [ ] Airflow/Dagster DAG chạy scheduled DQ checks
- [ ] Anomaly hybrid model (Z-score + IForest + EMA) operational

---

### Phase 4: Integration, Cloud Run & UAT (Tuần 7–8)

**Mục tiêu:** Cloud deployment, full benchmark, integration testing, user acceptance.

| Thành viên | Nhiệm vụ | Deliverables |
|------------|----------|-------------|
| **Kiên** | E2E integration tests. Performance optimization (query caching, connection pooling). API documentation (OpenAPI). | `tests/integration/`, performance benchmarks, API docs |
| **Chiến** | DeepEval full benchmark: cross-model comparison, Correctness F1. Dynamic threshold tuning trên production data. | `eval/results/benchmark_report.md`, model comparison matrix, tuned configs |
| **Phong** | Cloud Run deployment. Secret Manager for warehouse credentials. GitHub Actions deploy pipeline. Production monitoring. | `scripts/deploy_cloud_run.sh`, deploy workflow, Cloud Run configs |
| **Đạt** | Trend Analysis + DQ ScoreCard UI. Frontend polish + responsive design. 2-role testing (Steward/Viewer). | `TrendAnalysis.tsx`, `DQScoreCard.tsx`, responsive CSS, UAT test cases |

**DoD Phase 4 (Gate 3 Complete):**
- [ ] System deployed trên Cloud Run, auto-scaling configured
- [ ] DeepEval: Faithfulness ≥ 0.7, Executability ≥ 0.9, Correctness F1 ≥ 0.8
- [ ] Airflow/Dagster scheduled pipeline chạy ổn định ≥ 3 ngày liên tục
- [ ] Tất cả 6 major frontend views hoạt động (Catalog, HITL, Execution, Anomaly, Trend, Contract)
- [ ] Zero raw PII trong LLM payloads (verified by automated test)
- [ ] Audit log complete cho mọi HITL actions
- [ ] Documentation: Architecture, API docs, Runbook, Deploy guide

---

> **Prepared by:** Senior AI Architect & Technical Project Lead  
> **Review date:** 2026-08-19  
> **Tech Stack Compliance:** ✅ Chỉ sử dụng 10 công nghệ trong danh mục chỉ định
