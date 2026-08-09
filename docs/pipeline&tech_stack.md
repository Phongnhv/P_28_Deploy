# RidePulse DQ — Báo cáo Pipeline và Technology Stack

> **Trạng thái:** Bản đề xuất để nhóm review  
> **Dataset:** NYC TLC Yellow Taxi, snapshot 2–3 tháng năm 2024  
> **Mô hình xử lý:** Batch, không streaming  
> **Ràng buộc:** Không train hoặc fine-tune model riêng

## 1. Kết luận điều hành

Project nên dùng pipeline hybrid:

- Dagster điều phối Data Engineering và các batch job.
- PostgreSQL lưu raw data, profile, rules, execution results và anomaly events.
- FastAPI cung cấp API, trigger job và HITL workflow.
- React cung cấp giao diện Steward/Viewer.
- LangGraph điều phối các bước cần LLM reasoning.
- LLM đề xuất rule, giải thích anomaly và recommend action.
- dbt Core hoặc SQL compiler sinh test từ rule đã được duyệt.
- Statistical screening và Isolation Forest tạo anomaly candidates để đưa vào bước phân tích.
- Agent không tự sửa raw data, không chạy arbitrary SQL và không tự kết luận một candidate là lỗi chắc chắn.
- Không cần realtime streaming.

Pipeline tổng quát:

~~~text
TLC Parquet
    ↓
Dagster ingestion
    ↓
PostgreSQL raw tables
    ↓
Profiler + feature builder
    ↓
Profile + group statistics + candidate anomalies
    ↓
Evidence package
    ↓
LangGraph Agent + LLM
    ↓
Rule proposal
    ↓
Data Steward HITL
    ↓
Rule compiler / dbt test
    ↓
Dagster execution
    ↓
DQ results + ML anomaly results
    ↓
Diagnosis và remediation recommendation
    ↓
React dashboard
~~~

Điểm quan trọng: Agent không cần đọc toàn bộ database. Data plane được phép quét dữ liệu để tạo bằng chứng; Agent chỉ nhận profile, thống kê theo nhóm và mẫu dữ liệu có kiểm soát.

## 2. Scope của bài toán

### 2.1. Dataset

Sử dụng NYC TLC Yellow Taxi Trip Records theo tháng:

- [TLC Trip Record Data](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page)
- [Yellow Taxi Data Dictionary](https://www.nyc.gov/assets/tlc/downloads/pdf/data_dictionary_trip_records_yellow.pdf)

Đây là taxi data, không phải Uber/Lyft. Kế hoạch này giả định assignment chấp nhận mobility/taxi data thay vì bắt buộc một platform ride-hailing cụ thể.

Quy mô:

- Development: 100.000–300.000 dòng.
- Demo: vài triệu dòng hoặc 2–3 tháng.
- Benchmark: 100.000, 1 triệu và 5 triệu dòng.
- Không cần dùng toàn bộ dữ liệu nhiều năm.

### 2.2. Batch, không streaming

Bài toán chỉ cần:

- Chạy thủ công khi Steward chọn Run.
- Chạy định kỳ bằng Dagster.
- Chạy mỗi giờ/ngày/tháng tùy cấu hình.
- Gửi cảnh báo sau khi batch hoàn thành.
- Kiểm tra snapshot hoặc batch mới được ingest.

Không cần Kafka, event-by-event processing, realtime latency hoặc streaming anomaly detection.

Freshness chỉ nên được hiểu là batch freshness:

- Batch mới nhất đã được ingest chưa?
- Dữ liệu có thiếu khoảng thời gian không?
- Batch có trễ lịch không?
- Timestamp coverage có đầy đủ không?

### 2.3. Mục tiêu

Mục tiêu không phải để Agent tự thay thế Data Engineer. Mục tiêu là:

> Agent giúp Data Steward khảo sát dataset, phát hiện pattern đáng chú ý, đề xuất rule kiểm tra, sinh executable test sau khi được duyệt, và đưa ra recommendation cho anomaly.

## 3. Đánh giá architecture hiện tại

### Phần nên giữ

- Client/Backend/Agent/Data layer.
- React và FastAPI.
- PostgreSQL.
- Dagster.
- LangGraph.
- HITL.
- Test generation.
- Anomaly detection.
- Dashboard và audit log.

### Phần cần chỉnh

| Thành phần | Vấn đề | Quyết định |
|---|---|---|
| Agent chỉ đọc schema và metadata cơ bản | Không đủ cho anomaly theo quan hệ hoặc theo group | Bổ sung feature, group profile và controlled samples |
| Agent tự đọc raw data | Không scale và tốn token | Profiler quét full data; Agent đọc evidence package |
| LLM sinh arbitrary SQL/dbt code | Có thể sai logic hoặc tạo query nguy hiểm | LLM trả structured rule; compiler sinh test |
| Anomaly và diagnosis gộp chung | ML scanning và LLM explanation là hai việc khác nhau | Tách Anomaly Scanner và Diagnosis Agent |
| Isolation Forest là thành phần bắt buộc | Có thể vượt scope và tạo false positive | Statistical detection là MVP; Isolation Forest là extension |
| Snowflake/BigQuery/PostgreSQL cùng lúc | Không cần thiết cho demo | Chốt PostgreSQL |
| Dagster, dbt và Great Expectations cùng lúc | Có thể chồng chéo | Chọn dbt Core hoặc SQL compiler; Dagster vẫn là orchestrator |
| Agent sinh SQL fix script | Có rủi ro sửa sai dữ liệu | Chỉ recommend action; remediation phải qua HITL |

## 4. Architecture mục tiêu

~~~mermaid
flowchart TD
    A["React Frontend"] --> B["FastAPI"]
    B --> C["Dagster Run and Status"]
    B --> D["LangGraph HITL API"]

    C --> E["Ingestion and Profiling"]
    E --> F[("PostgreSQL")]
    F --> G["Feature Builder"]
    G --> H["Statistical Candidate Screening"]
    F --> I["Group Profiles and Drift Metrics"]
    H --> J["Evidence Builder"]
    I --> J

    J --> K["Optional RAG"]
    K --> L["LangGraph Rule Proposer"]
    L --> M["LLM Service"]
    L --> N["Proposed Rules"]
    N --> D
    D --> O{"Data Steward"}

    O -->|Approve/Edit| P["Rule Compiler"]
    O -->|Reject| Q["Audit Log"]
    P --> R["dbt Test or SQL Test Artifact"]
    R --> C

    C --> S["Dagster DQ and ML Job"]
    S --> F
    S --> T["DQ Results and Anomaly Events"]

    T --> U["Diagnosis Agent"]
    U --> V["Recommendation"]
    T --> W["React Dashboard"]
    V --> W

    X["Synthetic Manifest"] -. "Evaluation only" .-> Y["Evaluation Runner"]
    T --> Y
~~~

### 4.1. Dagster

Dagster quản lý:

- Ingestion.
- Asset dependencies.
- Profiling job.
- Feature generation.
- DQ test execution.
- Statistical/ML scoring.
- Persist kết quả.
- Schedule, retry và run status.

Các asset/job:

~~~text
raw_trips
    ↓
dataset_profile
    ↓
trip_features
    ↓
candidate_anomalies
    ↓
dq_test_run
    ↓
anomaly_events
    ↓
dashboard_snapshot
~~~

### 4.2. LangGraph

LangGraph quản lý:

- Profile interpretation.
- Evidence analysis.
- Rule proposal.
- HITL interrupt/resume.
- Diagnosis explanation.
- Remediation recommendation.

LangGraph không thay Dagster trong việc ETL hoặc scan toàn bộ database.

### 4.3. FastAPI

FastAPI cung cấp:

- Dataset catalog API.
- Trigger Dagster run.
- Run status.
- Profile và result API.
- Rule proposal API.
- Approve/reject/edit API.
- HITL resume API.
- Role checking.
- Audit log.

### 4.4. React

React hiển thị:

- Dataset selection.
- Profiling summary.
- Rule proposal.
- HITL review.
- Execution log.
- DQ result.
- Anomaly dashboard.
- Diagnosis hypothesis.
- Recommendation action.
- Viewer read-only dashboard.

## 5. Technology stack

| Layer | Công nghệ | Vai trò |
|---|---|---|
| Frontend | React + Ant Design | Steward/Viewer UI |
| Backend | FastAPI + Pydantic | REST API, validation, workflow |
| Orchestration | Dagster | Batch jobs, assets, schedule, retry |
| Agent orchestration | LangGraph | LLM workflow, HITL |
| LLM | OpenAI hoặc Gemini | Rule proposal, diagnosis, recommendation |
| Primary database | PostgreSQL | Raw/demo data, profiles, rules, runs, results |
| Raw source | Parquet | TLC monthly source, immutable input |
| Test layer | dbt Core hoặc SQL compiler | Sinh và thực thi DQ test |
| Statistical detection | SQL, pandas/Polars | Percentile, IQR/MAD, z-score, drift |
| ML detection | scikit-learn Isolation Forest | Multivariate anomaly, optional extension |
| RAG | ChromaDB hoặc rule catalog đơn giản | Data dictionary, rule history, playbook |
| Deployment | Docker, optional Cloud Run | Reproducible demo/deployment |
| Testing | pytest | Unit/integration test |
| Quality | Ruff | Linting/formatting |
| Notification | Dashboard trước, webhook/Slack optional | Batch completion/anomaly alert |

Khuyến nghị không implement đầy đủ cả dbt và Great Expectations. Với PostgreSQL và Dagster, lựa chọn đơn giản là:

~~~text
Dagster → dbt test → PostgreSQL
~~~

hoặc:

~~~text
Dagster → SQL rule runner → PostgreSQL
~~~

## 6. Agent, RAG và data access

Agent không chỉ đọc schema, nhưng cũng không đọc toàn bộ raw data.

Evidence package gồm:

- Schema và data dictionary.
- Row count, null rate, distinct count.
- Min, max, median, p95, p99.
- Profile theo ngày, giờ, vendor, zone.
- Distribution/drift summary.
- Derived feature summary.
- Representative samples.
- Candidate rows/groups.

Sample được chọn theo:

- Representative sample.
- Tail percentile.
- Rare category.
- Group có null rate bất thường.
- Group có distribution drift.
- Candidate anomaly score cao.
- Cặp normal/suspicious để so sánh.

Kích thước khuyến nghị: 100–300 rows sau khi chọn lọc.

Candidate package không chỉ gồm top Isolation Forest rows. Nó nên kết hợp:

- Top ML candidates.
- Rows vi phạm deterministic rules.
- Groups có null-rate hoặc distribution drift.
- Normal control samples để so sánh.
- Group baseline và percentile của các feature liên quan.

Nếu chỉ gửi top outlier mà không có context, Agent dễ nhầm legitimate outlier với data error.

### RAG

RAG chỉ dùng cho:

- TLC data dictionary.
- Định nghĩa field.
- Rule catalog đã approve.
- Severity guideline.
- Remediation playbook.
- Các ví dụ rule đã review.

Không dùng RAG để embedding toàn bộ trip records. Null-rate spike, distribution drift và group anomaly phải được tính bởi profiler.

## 7. Tự động sinh test

Tự động sinh test nghĩa là:

> Agent đề xuất rule specification; sau khi Steward approve, hệ thống chuyển rule thành test executable; Dagster chạy test theo batch.

Ví dụ structured rule:

~~~json
{
  "rule_type": "range",
  "table": "yellow_trips",
  "column": "fare_amount",
  "operator": "greater_or_equal",
  "value": 0,
  "severity": "high"
}
~~~

Compiler có thể sinh test:

~~~sql
SELECT *
FROM yellow_trips
WHERE fare_amount < 0;
~~~

Không có dòng kết quả thì PASS. Có dòng kết quả thì FAIL và lưu số dòng lỗi.

Các rule MVP:

- Not-null.
- Range.
- Accepted values.
- Uniqueness/fingerprint.
- Cross-field consistency.
- Conditional group metric.
- Batch distribution check.

LLM không được sinh và chạy trực tiếp:

~~~text
UPDATE
DELETE
DROP
INSERT
ALTER
~~~

Biện pháp an toàn:

1. LLM chỉ trả structured rule.
2. Rule type nằm trong allow-list.
3. Compiler sinh query từ template.
4. Validate table, column, type và operator.
5. Chỉ cho phép read-only query.
6. Chạy thử trên sample/staging.
7. Steward approve.
8. Dagster chạy bằng database read-only role.
9. Lưu version, reviewer và execution log.

## 8. ML Anomaly Detection

### 8.1. Vai trò của Isolation Forest

Isolation Forest khả thi nếu fit trên sample nhỏ, nhưng vai trò của nó phải được giới hạn là anomaly candidate ranking:

- Không cần GPU.
- Không cần deep learning.
- Không cần fine-tune LLM.
- Không cần supervised labels để fit.
- Có thể fit trên 100.000–300.000 rows.
- Score dữ liệu lớn theo batch/chunk.

Đây vẫn là unsupervised model fitting, nhưng nhẹ hơn nhiều so với train model riêng.

Isolation Forest không phải clustering và không tạo normal/anomaly class. Nó tạo random splits trên feature space; điểm nào dễ bị cô lập hơn phần còn lại sẽ có score đáng ngờ hơn. Score này không phải xác suất và không phải kết luận data error.

Output của bước này nên là:

~~~text
anomaly_candidate
anomaly_score
feature_snapshot
candidate_reason
~~~

Agent nhận output này cùng global profile, group profile và normal control samples. Agent có thể phân loại candidate là likely_data_error, likely_valid_outlier hoặc needs_human_review, nhưng rule engine và Data Steward mới xác minh/quyết định.

### 8.2. Feature table

~~~text
trip_duration
trip_distance
fare_amount
total_amount
average_speed
fare_per_mile
tip_ratio
pickup_date
pickup_hour
vendor
pickup_zone
dropoff_zone
~~~

### 8.3. Fit và score

- Fit model trên mostly-normal baseline.
- Loại lỗi obvious trước khi fit.
- Không fit trên corrupted rows nếu đã biết chúng bị inject.
- Score dữ liệu lớn theo batch.
- Lưu anomaly score và top candidates.
- Lưu thêm feature values, group context và baseline comparison.
- Agent đọc top candidates, aggregate summary và control samples.

ML không thay thế rule-based DQ và không tự quyết định remediation.

### 8.4. Anomaly phù hợp

#### Multivariate anomaly

Từng cột hợp lệ nhưng quan hệ bất thường:

~~~text
distance = 0.8
duration = 4 minutes
fare = 120
~~~

Feature bất thường:

- fare_per_mile.
- average_speed.
- tip_ratio.

#### Contextual anomaly

Một vendor, zone hoặc hour có pattern khác nhóm tương tự.

#### Distribution anomaly

Một batch có median, p95 hoặc null rate thay đổi mạnh.

Distribution anomaly nên được xử lý bằng group statistics/drift trước; không tuyên bố Isolation Forest tự phát hiện mọi batch anomaly.

## 9. Synthetic Data và Error Injection

### 9.1. Không tạo nhiều bộ để train

Synthetic data không dùng để train LLM.

Chỉ cần:

| Dataset | Mục đích |
|---|---|
| raw_reference | TLC gốc, không sửa |
| mostly_normal_baseline | Fit ML sau khi loại lỗi obvious |
| corrupted_demo | Known và discovery anomalies |
| untouched_holdout | Đo false positive |
| blind_slice | Kiểm tra rule trên partition khác |

Một corrupted snapshot với deterministic seed là đủ cho MVP.

### 9.2. Known anomalies

| ID | Phạm vi | Cách inject |
|---|---|---|
| K1 | Row | Null ở field bắt buộc |
| K2 | Row | Fare/distance âm hoặc passenger count quá lớn |
| K3 | Row | Total amount không khớp component |
| K4 | Row | Exact duplicate hoặc fingerprint duplicate |

### 9.3. Discovery anomalies

| ID | Phạm vi | Cách inject |
|---|---|---|
| D1 | Row/multivariate | Distance, duration, fare trong range nhưng quan hệ bất thường |
| D2 | Segment | Null rate của vendor trong một ngày tăng mạnh |
| D3 | Distribution | Distance hoặc fare distribution thay đổi trong batch |

Tỷ lệ tham khảo:

- Known row-level: 0,5–1%.
- ML row-level: 0,2–0,5%.
- Segment anomaly: 2–3 vendor/date/zone groups.
- Mọi injection dùng deterministic seed.

### 9.4. Manifest

~~~text
manifest_id
source_row_id
anomaly_type
affected_scope
segment_key
original_value
injected_value
injection_seed
~~~

Manifest chỉ dành cho Evaluation Runner. Không đưa manifest cho Agent/model và không thêm cột is_anomaly vào input.

## 10. Remediation

Test fail hoặc ML candidate không đồng nghĩa hệ thống được phép tự thay đổi dữ liệu.

Agent nên recommend:

| Action | Khi nào dùng |
|---|---|
| quarantine | Giá trị chắc chắn sai hoặc batch đáng ngờ |
| review | Anomaly có thể hợp lệ |
| backfill | Thiếu dữ liệu nhưng có upstream source |
| recompute | Derived field có công thức rõ ràng |
| deduplicate view | Loại duplicate khỏi clean view, giữ raw |
| rerun upstream | Batch có dấu hiệu lỗi ingestion |
| accept as valid | Outlier được Steward xác nhận |

MVP nên dùng recommendation-first:

~~~text
Detect anomaly
    ↓
Agent recommend action
    ↓
Steward approve
    ↓
Dagster thực thi action an toàn nếu cần
~~~

Không tự update/delete raw data. Nếu sau này có auto-remediation, chỉ cho phép thao tác deterministic, reversible và trên clean view/quarantine table.

## 11. PostgreSQL scaling

Các bảng chính:

~~~text
trips_raw
trips_corrupted
dataset_profiles
group_profiles
trip_features
candidate_anomalies
dq_rules
dq_runs
dq_results
anomaly_events
remediation_recommendations
synthetic_manifest
audit_logs
~~~

Partition theo pickup date/tháng:

~~~text
trips_2024_01
trips_2024_02
trips_2024_03
~~~

Aggregate tables/materialized views:

~~~text
profile_by_date
profile_by_vendor_date
profile_by_zone_hour
drift_by_batch
~~~

| Quy mô | Cách xử lý |
|---|---|
| 100k | SQL query hoặc batch đơn |
| 1M | SQL pushdown và feature table |
| 5M | Partition, index, materialized aggregates, chunked scoring |
| Hơn scope assignment | Cân nhắc warehouse/distributed engine |

Không load toàn bộ database vào RAM và không gửi raw data cho LLM.

## 12. Evaluation Plan

### 12.1. Baselines

| Baseline | Thành phần |
|---|---|
| B0 | Static DQ rules K1–K4 |
| B1 | B0 + percentile/IQR/MAD/group drift |
| B2 | B1 + Isolation Forest candidate ranking |
| B3 | B2 + Agent interpretation và recommendation |
| B4 | B3 + Agent-proposed discovery rule được HITL approve |

B3 chỉ tạo giá trị nếu Agent đưa ra interpretation/recommendation có evidence mà B0–B2 không có. B4 chỉ tạo giá trị nếu rule được Agent đề xuất bắt được pattern mà B0–B2 bỏ sót.

### 12.2. Metrics

Row-level:

- Precision.
- Recall.
- F1-score.
- False positive rate.
- Top-k precision.

Segment/distribution:

- Đúng vendor/date/zone bị ảnh hưởng.
- Coverage affected rows/segments.
- False alarm trên untouched holdout.

Agent:

- Candidate interpretation validity.
- Rule proposal validity.
- Rule executable rate.
- Novel rule rate.
- HITL approval rate.
- Discovery recall improvement.
- Evidence citation correctness.
- Recommendation acceptance rate.

Performance:

- Profiling time.
- Feature generation time.
- Dagster job time.
- Rule execution time.
- ML scoring time.
- Peak memory.
- Evidence package size.
- LLM token usage.

Đo performance ở 100k, 1M và 5M rows.

### 12.3. Go/no-go

Giữ Isolation Forest nếu:

1. Bắt được discovery anomaly mà static rules bỏ sót.
2. False positive chấp nhận được.
3. Fit/score trong tài nguyên demo.
4. Kết quả ổn định khi đổi batch.

Giữ Agent ở anomaly discovery nếu:

1. Agent nhận diện được pattern từ evidence.
2. Đề xuất được ít nhất một rule mới hoặc recommendation hữu ích.
3. Có evidence support.
4. False positive không tăng quá nhiều.

Nếu không đạt, Agent vẫn có thể giữ vai trò:

~~~text
Profiling assistant + Rule proposer + Diagnosis/Recommendation assistant
~~~

## 13. Kịch bản demo

1. Steward chọn dataset TLC.
2. FastAPI yêu cầu Dagster chạy ingestion/profiling.
3. Dagster lưu profile và feature vào PostgreSQL.
4. Evidence Builder tạo profile summary và candidate samples.
5. LangGraph đọc evidence và data dictionary.
6. Agent đề xuất rule K1–K4 và discovery hypothesis.
7. Steward approve/edit/reject.
8. Dagster gọi dbt test hoặc SQL compiler.
9. Test chạy trên PostgreSQL read-only.
10. Statistical detector và Isolation Forest tạo anomaly candidates.
11. Diagnosis Agent đọc candidates và đưa recommendation.
12. React hiển thị DQ result, anomaly, explanation và action.
13. Evaluation Runner so sánh kết quả với hidden synthetic manifest.

## 14. Phân công theo công nghệ

| Nhóm việc | Thành phần | Kết quả |
|---|---|---|
| Data Engineering | PostgreSQL + Dagster | Ingestion, assets, batch jobs, profiles, results |
| Backend | FastAPI + Pydantic | API, HITL, run status, validation |
| Agent/AI | LangGraph + LLM + optional RAG | Rule proposal, diagnosis, recommendation |
| DQ/ML | dbt/SQL + scikit-learn/statistics | Tests, feature scoring, anomaly detection |
| Frontend | React + Ant Design | Steward/Viewer screens |
| Evaluation | Python/SQL + manifest | Precision, recall, F1, false positive |
| DevOps | Docker + optional Cloud Run | Reproducible local/demo deployment |

## 15. Scope chốt

### In-scope

- NYC TLC Yellow Taxi.
- Snapshot batch 2–3 tháng.
- PostgreSQL.
- Dagster.
- FastAPI.
- React.
- LangGraph.
- Rule proposal và HITL.
- Auto-generated dbt/SQL tests.
- Statistical anomaly detection.
- Isolation Forest extension.
- Synthetic corrupted snapshot.
- Manifest-based evaluation.
- Recommendation action, không tự sửa raw data.

### Out-of-scope

- Streaming ingestion.
- Kafka.
- Fine-tuning LLM.
- Train supervised anomaly model.
- RAG toàn bộ raw rows.
- Multi-source warehouse integration.
- Snowflake/BigQuery production connector.
- Tự động update/delete raw data.
- Auto-fix anomaly không qua HITL.
- Full production-grade multi-tenant RBAC.
- Khẳng định synthetic benchmark đại diện cho mọi lỗi production.

## 16. Kết luận

Architecture phù hợp nhất là:

> Dagster điều phối batch data pipeline trên PostgreSQL; FastAPI kết nối frontend và workflow; LangGraph dùng LLM để phân tích evidence, đề xuất rule và recommendation; dbt/SQL compiler sinh test an toàn; statistical methods và Isolation Forest phát hiện anomaly; Data Steward duyệt rule/action trước khi áp dụng.

Synthetic data dùng để tạo ground truth cho evaluation, không dùng để train LLM.

Giá trị chính của Agent là:

1. Hiểu profile và sample của dataset.
2. Nhận diện pattern chưa được viết thành rule.
3. Đề xuất structured rule để sinh test.
4. Giải thích anomaly.
5. Recommend action phù hợp.

Agent không trực tiếp quét toàn bộ database, không tự sinh arbitrary SQL và không tự sửa raw data.
