# Review So Sánh Và Đề Xuất Cho Graph 2

## Kết Luận Ngắn

Plan trong `graph-2-3.md` tốt hơn review Run 2 ban đầu của mình ở cấp độ kiến trúc. Review ban đầu tập trung vào việc làm `anomaly_detector` tốt hơn bên trong một graph; plan mới giải quyết vấn đề gốc: execution và anomaly analysis có vòng đời, failure domain, persistence và tiêu chí thành công khác nhau.

Đề xuất của mình:

```text
Graph 2 = Rule Execution và Persisted DQ Results
Graph 3 = Anomaly Analysis và Hypothesis
```

Hai graph là lựa chọn đúng. Việc bỏ `llm_dbt_repair` khỏi production path cũng hợp lý, với điều kiện thay thế nó bằng validation, retry kỹ thuật có giới hạn, diagnostic rõ ràng và cơ chế sửa compiler ngoài runtime.

Không nên biến Graph 2 thành một “AI graph” cố gắng tự sửa mọi lỗi. Graph 2 phải là một pipeline có thể lặp lại: cùng ruleset snapshot, dataset version và compiler version thì phải tạo ra cùng kết quả.

## So Sánh Hai Hướng

| Tiêu chí | Review Run 2 ban đầu | Plan `graph-2-3.md` | Đánh giá |
|---|---|---|---|
| Ranh giới execution/anomaly | Vẫn chủ yếu trong cùng Run 2 | Tách Graph 2 và Graph 3 sau persistence | Plan tốt hơn |
| Anomaly detector | Cải thiện threshold, baseline, correlation | Signal fan-out, quality gate, deterministic aggregator | Plan tốt hơn |
| LLM repair | Chưa loại bỏ dứt khoát | Bỏ khỏi production execution path | Plan tốt hơn |
| Persistence | Đề xuất canonical anomaly service | Có execution/anomaly/signal/hypothesis entities | Plan tốt hơn |
| Failure isolation | Nêu rủi ro | Có status riêng và dispatch độc lập | Plan tốt hơn |
| MVP complexity | Tương đối thấp | Có nguy cơ quá rộng nếu triển khai toàn bộ ngay | Review ban đầu thực tế hơn |
| Khả năng audit | Cải thiện một phần | Có snapshot, hash, version và evidence refs | Plan tốt hơn |

Plan có một nhược điểm chính: phạm vi Graph 3 khá lớn. Nếu triển khai đồng thời toàn bộ detector, signal schema, persistence, hypothesis agent, feedback và API mới thì MVP sẽ bị kéo dài. Vì vậy nên giữ kiến trúc mục tiêu như plan, nhưng triển khai theo lát cắt nhỏ.

## Có Nên Chia Thành Hai Graph Không?

Có. Đây là ranh giới phù hợp:

```text
Graph 2
  compile -> validate -> execute -> normalize -> persist -> finalize

Graph 3
  load persisted results -> build features -> detect signals
  -> quality gate -> aggregate decision -> hypothesis -> persist
```

### Lý do nên tách

1. **Khác định nghĩa thành công**

   Rule `FAIL` có thể là kết quả DQ hợp lệ. Ngược lại, compiler failure hoặc database timeout là lỗi execution. Anomaly analysis lỗi không được biến một execution thành `FAILED`.

2. **Có thể chạy lại độc lập**

   Khi thay detector config, threshold hoặc prompt hypothesis, không cần chạy lại SQL/dbt và không tạo thêm dữ liệu execution.

3. **Persistence boundary rõ ràng**

   Graph 3 chỉ đọc execution result đã immutable. Điều này tránh detector đọc dữ liệu đang ghi hoặc dùng current run nhầm vào historical baseline.

4. **Version độc lập**

   Compiler version, ruleset version, detector version và prompt version có thể audit riêng.

5. **Failure isolation tốt hơn**

   Execution có thể `SUCCEEDED` trong khi anomaly analysis là `FAILED_TO_START`, `PARTIAL` hoặc `RETRYABLE`.

### Điều kiện để tách graph không gây phức tạp

Hai graph không nên truyền một `AgentState` mutable khổng lồ qua lại. Nên giao tiếp bằng các ID và persistence contract:

```text
execution_run_id
dataset_version_id
ruleset_version_id
```

Graph 3 load dữ liệu từ database/object storage theo các ID này. Nếu MVP chưa có event bus, Graph 2 có thể dispatch Graph 3 synchronously/asynchronously sau khi persist, nhưng status vẫn phải tách.

## Có Nên Bỏ Repair Node Không?

### Kết luận

Nên bỏ `llm_dbt_repair` khỏi production routing của Graph 2.

Không nên bỏ toàn bộ retry và diagnostic. Cần phân biệt ba loại lỗi:

| Loại lỗi | Hành vi nên có |
|---|---|
| Lỗi transient: connection reset, timeout tạm thời | Retry kỹ thuật có giới hạn, không đổi semantics |
| Lỗi deterministic compiler/validator | Dừng execution, lưu diagnostic, đánh dấu compiler validation failure |
| Lỗi cần thay đổi rule/YAML semantics | Không tự sửa trong runtime; tạo repair task hoặc chờ developer/steward |

### Vì sao LLM repair nguy hiểm ở đây?

1. Rule đã được Data Steward approve. LLM sửa YAML có thể thay đổi phạm vi, column, predicate hoặc semantics mà không có approval mới.
2. Validation scope hiện tại chỉ bảo vệ model/column scope; nó không chứng minh logic test vẫn tương đương.
3. LLM output không deterministic và khó replay chính xác.
4. Compiler bug bị che giấu dưới một workaround riêng lẻ, khiến lỗi lặp lại ở các rule khác.
5. Một execution graph không nên vừa là compiler, vừa là debugger, vừa là rule editor.

### Thiết kế thay thế

```text
compile deterministic
  -> validate structure and semantics
  -> if transient infrastructure error: bounded retry
  -> if artifact/compiler error: persist diagnostic + END
  -> if valid: execute
```

Có thể giữ một **offline repair assistant** ngoài Graph 2 để đề xuất patch cho compiler hoặc artifact template. Nhưng patch đó phải được test, review và release trước khi được dùng trong production.

## Kiến Trúc Graph 2 Mình Đề Xuất

```text
Create Execution Request
  -> load_ruleset_snapshot
  -> validate_rule_contract
  -> compile_test_artifacts
  -> validate_artifacts
  -> execute_tests
  -> normalize_execution_results
  -> persist_execution_results
  -> finalize_execution
  -> dispatch Graph 3
```

### 1. Load immutable ruleset snapshot

Graph 2 không nên đọc trực tiếp các proposal rows mutable. Nó cần snapshot gồm:

- approved rules;
- parameter provenance;
- semantic/rule version;
- dataset version;
- schema hash;
- ruleset hash.

Nếu review batch còn `PENDING`, snapshot phải bị reject hoặc phải chạy explicit subset đã được xác nhận.

### 2. Validate rule contract lần cuối

Approval không thay thế runtime validation. Cần kiểm tra:

- table/column tồn tại;
- type tương thích;
- required parameters đầy đủ;
- cross-field target tồn tại;
- rule chưa deactivate;
- schema hash không drift;
- parameter provenance có evidence.

Không tự điền parameter thiếu. Invalid rule phải có typed validation error.

### 3. Compile deterministic

Compiler tạo SQL/dbt từ rule catalog và template version cố định. LLM không được tham gia bước này.

Artifact cần có:

- compiler version;
- artifact hash;
- rule-to-test mapping;
- bind parameters metadata;
- schema/dataset version.

### 4. Validate artifact và phân biệt dbt/metrics

Nên giữ hai trạng thái riêng:

- `dbt_status`: parse/compile/test compatibility;
- `metrics_status`: deterministic metrics query.

Nếu hai bên không đồng ý, dùng `RESULT_MISMATCH`; không âm thầm chọn một kết quả.

### 5. Normalize results

Mỗi result nên có contract ổn định:

```json
{
  "rule_id": "...",
  "status": "PASS|FAIL|ERROR|SKIPPED|RESULT_MISMATCH",
  "checked_count": 50000,
  "failed_count": 132,
  "violation_rate": 0.00264,
  "severity": "HIGH",
  "dbt_status": "PASS",
  "metrics_status": "PASS",
  "sample_refs": [],
  "error": null
}
```

### 6. Persist trước khi dispatch anomaly

Execution results phải immutable trước khi Graph 3 bắt đầu. Graph 2 cần trả về execution status độc lập:

- `SUCCEEDED`: execution hoàn thành, dù có rule `FAIL`;
- `PARTIAL`: có result hợp lệ nhưng một số rule `ERROR/SKIPPED`;
- `FAILED`: không có result đáng tin do compiler/infrastructure failure.

Sau đó tạo anomaly request. Nếu Graph 3 không start được, execution vẫn giữ `SUCCEEDED` hoặc `PARTIAL`.

## Graph 3 Nên Triển Khai Thế Nào Để Không Over-engineer?

Kiến trúc plan là đúng, nhưng MVP nên chia thành ba mức.

### MVP-1: Canonical deterministic anomaly analysis

Chỉ cần:

- business threshold;
- cold-start threshold có minimum checked count;
- robust baseline khi đủ lịch sử;
- volume/row-count drift;
- failure cluster;
- execution error signal;
- deterministic aggregator;
- persist decision và signal.

Chưa cần Isolation Forest, seasonal model hoặc distribution drift ngay.

### MVP-2: Hypothesis Agent

Chỉ chạy khi decision là `WATCH`, `ANOMALY` hoặc `CRITICAL`. Agent nhận structured evidence và chỉ tạo hypothesis có citation. Agent không được quyết định anomaly.

Nếu LLM lỗi, vẫn persist deterministic decision và dùng fallback explanation.

### P1: Advanced detectors

Chỉ thêm PSI/KS/Wasserstein, change-point, seasonal baseline và Isolation Forest khi history đủ và đã có labeled steward feedback.

## Đánh Giá Cụ Thể Các Ý Tưởng Trong Plan

### Nên giữ nguyên

- Tách Graph 2 và Graph 3.
- Immutable ruleset snapshot.
- `ExecutionRequest` và state riêng.
- Không dùng LLM để quyết định anomaly.
- Bỏ LLM repair khỏi production path.
- Persist execution trước anomaly.
- Signal schema có reliability và evidence refs.
- Signal quality gate.
- Aggregator deterministic, versioned.
- Hypothesis Agent có validator.
- Persist cả `NORMAL` và `INSUFFICIENT_HISTORY`.
- Tách `execution_status`, `anomaly_status`, `hypothesis_status`.

### Nên điều chỉnh

1. **Không bắt buộc toàn bộ detector P0 ngay từ đầu.** Sáu đến tám detector là mục tiêu kiến trúc, không phải acceptance criteria của sprint đầu.
2. **Không dùng “ít nhất hai family” như luật tuyệt đối cho mọi anomaly.** Business invariant CRITICAL có thể đủ một hard signal. Ngược lại, signal yếu từ hai family vẫn chưa chắc đủ.
3. **Thêm calibration theo dataset/domain.** Threshold 5% hoặc weights cố định không phù hợp mọi bảng.
4. **Giữ `INSUFFICIENT_HISTORY` là trạng thái hợp lệ, không phải failure.**
5. **Thêm idempotency cho Graph 3.** Một execution run có thể dispatch anomaly retry nhiều lần nhưng không tạo duplicate decision không kiểm soát.
6. **Thêm baseline compatibility key.** Dataset, rule semantic identity, schema version và cadence phải khớp trước khi so sánh lịch sử.
7. **Đừng để DQ score phụ thuộc trực tiếp vào số anomaly.** Report nên tách rule health, anomaly decision và execution reliability.

## Thứ Tự Ưu Tiên Đề Xuất

```text
1. Chốt execution/result contracts
2. Immutable ruleset snapshot
3. Tách Graph 2 khỏi anomaly path bằng persistence boundary
4. Bỏ LLM repair khỏi production routing
5. Hợp nhất anomaly implementation thành shared service
6. Implement MVP-1 detectors + deterministic aggregator
7. Persist anomaly run/signal/decision
8. Thêm Graph 3 retry độc lập
9. Thêm Hypothesis Agent + validator
10. Sau khi có history/feedback mới thêm advanced ML detectors
```

## Quyết Định Khuyến Nghị

### Về chia graph

**Chọn tách thành Graph 2 và Graph 3.** Đây là thay đổi kiến trúc đáng làm, không chỉ là refactor tên node.

### Về repair node

**Bỏ LLM repair khỏi Graph 2 production path.** Giữ bounded technical retry và diagnostic persistence. Lỗi compiler phải trở thành engineering issue có thể reproduce, không được runtime tự đổi semantics rule đã approve.

### Về phạm vi triển khai

**Dùng plan làm target architecture, nhưng triển khai MVP theo từng phase.** Không triển khai toàn bộ detector/ML/API/persistence mới trong một lần.

### Về lựa chọn giữa hai review

Review ban đầu hữu ích để phát hiện vấn đề anomaly và duplicate implementation. Plan `graph-2-3.md` nên là hướng chính vì nó đặt đúng boundary và failure model. Các cảnh báo về complexity, baseline quality và rollout từ review ban đầu vẫn cần giữ lại khi triển khai.

## Acceptance Criteria Cho Graph 2 Sau Khi Tách

- Chỉ approved/active rules trong immutable snapshot được execute.
- Compiler không gọi LLM.
- Không có runtime sửa semantics rule đã approve.
- Lỗi artifact dừng trước execute và có diagnostic đầy đủ.
- Retry chỉ dành cho lỗi transient, có giới hạn và idempotent.
- Một rule `FAIL` không làm execution infrastructure `FAILED`.
- Một rule `ERROR` không làm mất result các rule khác.
- Results được persist atomically trước Graph 3.
- Execution và anomaly có status độc lập.
- Có thể rerun Graph 3 mà không rerun Graph 2.
- Cùng snapshot, dataset version và compiler version cho kết quả có thể replay/audit.
