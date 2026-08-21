# RidePulse DQ — Các hướng cải thiện đề xuất

## 1. Hỗ trợ upload và quản lý nhiều dataset

- Cho phép người dùng upload dữ liệu CSV hoặc Parquet.
- Tạo thông tin và lịch sử xử lý riêng cho từng dataset.
- Thêm chức năng lựa chọn và chuyển đổi dataset trên giao diện.
- Hiển thị profile, rules, kết quả DQ và visualization theo dataset đang chọn.

Luồng đề xuất:

```text
Upload CSV/Parquet
→ Validate file
→ Tạo dataset_id và dataset version
→ Lưu file nguồn
→ Đọc schema và metadata
→ Chạy profiling
→ Dataset sẵn sàng cho agent
```

Mỗi dataset cần có vùng dữ liệu riêng gồm file nguồn, schema, profile, semantic contract, rule proposals, approved rules và lịch sử DQ runs. Khi người dùng chuyển dataset trên UI, toàn bộ thông tin hiển thị cũng phải chuyển theo `dataset_id`.

## 2. Thêm Dataset Understanding Agent

- Tự động đọc schema, metadata và thống kê của dataset.
- Hiểu ý nghĩa và vai trò nghiệp vụ của từng cột.
- Nhận diện identifier, timestamp, category, currency, PII và quan hệ giữa các cột.
- Tạo semantic contract làm đầu vào cho Rule Proposer.
- Cho phép người dùng kiểm tra và chỉnh sửa kết quả agent hiểu về dataset.

Luồng đề xuất:

```text
Schema + metadata + profile
→ Dataset Understanding Agent
→ Semantic Contract có cấu trúc
→ Người dùng kiểm tra/chỉnh sửa
→ Semantic Contract được xác nhận
```

Trong đó:

- **Schema** cung cấp tên cột và kiểu dữ liệu vật lý.
- **Metadata** cung cấp tên dataset, mô tả, nguồn dữ liệu và domain nếu có.
- **Profile** cung cấp null rate, distinct count, min/max, quantile, frequency và các pattern thống kê.
- **Dataset Understanding Agent** suy luận ý nghĩa nghiệp vụ của từng cột dựa trên các thông tin trên.
- **Semantic Contract** là kết quả có cấu trúc, không phải một đoạn mô tả tự do.

Ví dụ Semantic Contract:

```json
{
  "domain": "e-commerce",
  "columns": [
    {
      "name": "order_id",
      "semantic_type": "identifier",
      "business_role": "primary_business_key",
      "nullable_expected": false,
      "confidence": 0.98
    },
    {
      "name": "order_total",
      "semantic_type": "currency",
      "business_role": "transaction_amount",
      "nullable_expected": false,
      "confidence": 0.93
    }
  ],
  "relationships": [
    {
      "left_column": "created_at",
      "operator": "<=",
      "right_column": "completed_at"
    }
  ]
}
```

Semantic Contract giúp Rule Proposer hiểu `order_id` là identifier và `order_total` là giá trị tiền tệ, thay vì chỉ nhìn thấy tên cột và đoán trực tiếp.

## 3. Cải thiện Rule Proposer

- Sử dụng context động từ schema, metadata, profile và semantic contract của từng dataset.
- Giữ prompt template và guardrail cố định, có version.
- Chỉ cho phép agent lựa chọn từ các loại rule đã được định nghĩa sẵn.
- Yêu cầu mỗi rule đề xuất phải có evidence, confidence và giải thích rõ ràng.
- Tiếp tục sử dụng cơ chế Human-in-the-loop để approve, reject hoặc edit rule.

Luồng đầy đủ đề xuất:

```text
Schema + metadata + profile
→ Dataset Understanding Agent
→ Semantic Contract có cấu trúc
→ Context Builder
→ Prompt template cố định
→ Rule Proposer
→ Rule Validator
→ Human-in-the-loop
```

### Context Builder

Context Builder nên là code deterministic, không phải một agent tự viết lại prompt. Thành phần này ghép dữ liệu của dataset hiện tại vào prompt template:

```text
Prompt template version
+ Semantic Contract
+ Profile evidence
+ Dataset policy
+ Danh sách 11 defined rules
+ Output JSON schema
```

Như vậy, prompt template và guardrail vẫn cố định; chỉ context thay đổi theo từng dataset.

Ví dụ:

```text
Dataset A: order_id được hiểu là identifier
→ Context Builder đưa UNIQUE và NOT_NULL vào nhóm candidate phù hợp

Dataset B: temperature được hiểu là sensor measurement
→ Context Builder đưa RANGE và NULL_RATE vào nhóm candidate phù hợp
```

Rule Proposer chỉ chọn rule và parameters, không tự tạo rule type hoặc SQL mới.

Ví dụ output:

```json
{
  "candidate_id": "numeric_range",
  "column": "order_total",
  "parameters": {
    "min": 0
  },
  "confidence": 0.93,
  "evidence_refs": [
    "semantic.order_total.business_role",
    "profile.order_total.min"
  ],
  "explanation": "order_total là giá trị tiền tệ và không nên nhỏ hơn 0"
}
```

Không cần thêm một Prompt Optimizer Agent tự do ở runtime. Việc tối ưu prompt nên được thực hiện offline bằng DeepEval, sau đó chọn prompt version tốt nhất để sử dụng.

## 4. Chuẩn hóa catalog các rule

- Định nghĩa sẵn 11 nhóm rule có thể kiểm tra và thực thi.
- Chuẩn hóa parameters, validation và compiler cho từng rule.
- Không hard-code rule nghiệp vụ riêng cho một dataset trong logic ứng dụng.
- Cho phép bổ sung dataset policy khi có yêu cầu nghiệp vụ đặc thù.

Luồng sử dụng rule catalog:

```text
11 defined rule types
→ Lọc theo data type và Semantic Contract
→ Rule Proposer chọn candidate
→ Rule Validator kiểm tra parameters
→ Compiler tạo SQL/dbt test an toàn
```

Ví dụ:

```text
order_id + semantic type identifier
→ Candidate: NOT_NULL, UNIQUE

order_total + semantic type currency
→ Candidate: NOT_NULL, RANGE

created_at và completed_at + semantic type timestamp
→ Candidate: CROSS_FIELD_COMPARISON
```

Các defined rules nên hard-code phần định nghĩa, parameter schema, validator và compiler. Các rule nghiệp vụ cụ thể như `order_total >= 0` được tạo động từ context và phải qua HITL.

## 5. Tích hợp DeepEval

- Đánh giá khả năng hiểu dataset của agent.
- Đánh giá độ chính xác và mức độ phù hợp của rule được đề xuất.
- Kiểm tra rule có bám sát schema, metadata và profile evidence hay không.
- So sánh các prompt, model và agent version.
- Theo dõi các chỉ số như correctness, faithfulness, executability, latency và cost.

Luồng đánh giá đề xuất:

```text
Golden datasets có expected semantic contract và expected rules
→ Chạy Dataset Understanding Agent
→ Chạy Rule Proposer
→ DeepEval và deterministic metrics chấm điểm
→ So sánh prompt/model version
→ Chọn version tốt hơn
```

Ví dụ các câu hỏi cần được đánh giá:

- Agent có hiểu đúng `order_id` là identifier không?
- Rule Proposer có đề xuất `UNIQUE` và `NOT_NULL` không?
- Rule có dựa trên evidence thật từ profile không?
- Agent có tự tạo business constraint không có căn cứ không?
- Rule có compile và thực thi được không?

DeepEval nên được dùng để tối ưu prompt offline. Không để agent tự thay đổi prompt trong production mà chưa qua đánh giá.

## 6. Cải thiện UI và Visualization

- Thêm luồng upload dataset và theo dõi tiến trình xử lý.
- Thêm dataset catalog và dataset selector.
- Thêm màn hình review semantic contract.
- Hiển thị evidence và lý do agent đề xuất từng rule.
- Cải thiện biểu đồ completeness, violation rate, quality trend và anomaly.
- Thêm visualization so sánh các DQ runs và điểm DeepEval.

Luồng UI đề xuất:

```text
Dataset Catalog
→ Upload hoặc chọn dataset
→ Xem schema/profile
→ Review Semantic Contract
→ Review Rule Proposals
→ Chạy DQ
→ Xem Results, Anomalies và Visualization
```

Visualization nên thay đổi động theo dataset, không hard-code các cột taxi. Các màn hình quan trọng gồm:

- Column completeness và data-type distribution.
- Semantic map của dataset.
- Rule coverage theo DQ dimension.
- Violation rate theo rule.
- Quality score theo thời gian.
- So sánh hai DQ runs.
- DeepEval score theo prompt/model version.

## 7. Cải thiện khả năng vận hành agent

- Phân biệt rõ mock agent và graph/LLM agent trên UI và API.
- Hiển thị tiến trình xử lý thực tế của agent.
- Bổ sung timeout, retry, logging và theo dõi latency.
- Version hóa prompt, model, semantic contract và rule proposals để dễ đánh giá và audit.

Luồng trạng thái agent cần hiển thị rõ:

```text
QUEUED
→ PROFILING
→ UNDERSTANDING_DATASET
→ BUILDING_CONTEXT
→ PROPOSING_RULES
→ VALIDATING_RULES
→ WAITING_FOR_REVIEW
→ COMPLETED hoặc FAILED
```

Điều này giúp người dùng biết agent đang thực sự làm gì, đồng thời tránh trường hợp job đứng ở một phần trăm cố định trong lúc chờ LLM.
