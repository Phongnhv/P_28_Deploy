from langchain_core.prompts import ChatPromptTemplate

profiler_node_prompt = ChatPromptTemplate(
    [
        (
            "system",
            """Bạn là một AI Data Profiler chuyên nghiệp.
            Nhiệm vụ của bạn là phân tích các số liệu thống kê dữ liệu được cung cấp và thực hiện các yêu cầu sau:
            1. Phân tích và xác định kiểu dữ liệu ngữ nghĩa (semantic types) cho từng cột (ví dụ: ID, SĐT, Email, Tọa độ Lat/Long, Số tiền, Thời gian, Trạng thái, v.v.).
            2. Viết một đoạn tóm tắt ngắn gọn (3-4 câu) bằng tiếng Việt mô tả cấu trúc, quy mô và chất lượng dữ liệu của bảng (đặc biệt lưu ý các cột có tỷ lệ null cao hoặc phân phối bất thường).

            Hãy trình bày kết quả phân tích một cách rõ ràng, mạch lạc và có cấu trúc tốt.
            """,
        ),
        (
            "user",
            """
            Dưới đây là thống kê chi tiết của bảng dữ liệu '{dataset_id}':
            ```json
            {profile_data}
            ```
            """
        )
    ]
)


# ---------------------------------------------------------------------------
# Rule Proposer Prompt
# ---------------------------------------------------------------------------
# Input variables: table_name, table_digest, domain_context, historical_rules
# ---------------------------------------------------------------------------

_RULE_PROPOSER_SYSTEM = """\
Bạn là chuyên gia Data Quality (DQ) cho hệ thống vận tải taxi (NYC Yellow Taxi Trip Records). Nhiệm vụ của bạn là \
đề xuất các quy tắc kiểm tra chất lượng dữ liệu cho MỘT bảng dựa trên digest profile \
và Từ điển dữ liệu (Data Dictionary) được cung cấp.

## Các loại rule được hỗ trợ (rule_type)

| rule_type        | Khi nào áp dụng                                                                                                    |
|------------------|--------------------------------------------------------------------------------------------------------------------|
| NOT_NULL         | signal "no_nulls" hoặc cột có vai trò "id" / khóa nghiệp vụ quan trọng                                            |
| UNIQUE           | signal **"has_pk_constraint"** hoặc **"unique_full_table"** (đáng tin 100%). KHÔNG dùng "unique_in_sample_only" (chỉ là gợi ý từ mẫu) |
| RANGE            | role "numeric", có trường "range" / "typical_range" trong digest; pad thêm 10–20% ngoài biên quan sát             |
| ACCEPTED_VALUES  | role "categorical", có trường "values" trong digest; liệt kê đầy đủ enum hợp lệ                                   |
| REGEX_FORMAT     | cột email, phone, mã đơn hàng, zip code — khi pattern rõ ràng từ tên cột; ưu tiên khi signal "fixed_length"       |
| FRESHNESS        | role "datetime" — kiểm tra dữ liệu mới nhất không quá N giờ                                                       |
| ROW_COUNT        | rule cấp bảng — đặt min_row_count dựa trên trường "rows" trong digest                                              |
| NULL_RATE        | null_pct cao bất thường (> 5%) — đặt max_null_pct ngưỡng cảnh báo                                                 |
| CROSS_FIELD_COMPARISON | áp dụng khi digest có `cross_column_hints` (như `datetime_order`) hoặc hai cột có quan hệ thứ tự logic nghiệp vụ (như `pickup_datetime` <= `dropoff_datetime`) |

## Các signal quan trọng từ digest (ưu tiên đọc kỹ)

- `has_pk_constraint` / `has_unique_constraint`: ràng buộc schema thật — dùng được cho rule UNIQUE.
- `unique_full_table`: COUNT(DISTINCT) trên toàn bảng đã xác nhận unique — đáng tin 100%.
- `unique_in_sample_only`: unique chỉ trong mẫu — KHÔNG đủ bằng chứng, KHÔNG tạo rule UNIQUE cứng.
- `has_extreme_outliers`: min/max xa p1/p99 — nên dùng `typical_range` thay vì `range` khi đặt RANGE.
- `has_negative_values`: tồn tại giá trị âm — xem `negative_pct` để quyết định rule có cần min >= 0 không.
- `fixed_length`: độ dài chuỗi cố định — gợi ý mạnh cho REGEX_FORMAT hoặc length constraint.
- `cross_column_hints` (cấp bảng): gợi ý quan hệ liên cột (e.g., pickup <= dropoff) — ưu tiên xem xét rule CROSS_FIELD_COMPARISON.

## Hướng dẫn quan trọng

1. **Chỉ đề xuất rule cho các cột có tên xuất hiện trong digest đầu vào.** \
   Không được tự bịa tên cột.
2. **`ai_reasoning` là rationale ngắn, không phải chain-of-thought.** Chỉ tóm tắt evidence đã chọn, \
   nguồn nghiệp vụ có thật và lý do trực tiếp cho rule. Không bịa policy, metric hoặc edge case.
3. **RANGE không được biến biên quan sát thành hard limit.** \
   Ưu tiên dùng `typical_range` [p5, p95] nếu có, mở rộng ≥ 10% mỗi phía. \
   Nếu có signal `has_extreme_outliers`, đặt threshold dựa trên typical_range, không dùng min/max tuyệt đối. \
   Mỗi parameter phải có provenance riêng; threshold chỉ suy ra từ profile phải dùng DATA_PROFILE và nêu assumption.
4. **Sampling caveat:** Nếu digest có "sample.rate < 1.0" và "sample.caveat", \
   hãy GIẢM confidence_score và NỚI RỘNG ngưỡng hơn nữa — tránh đặt thresholds \
   quá chặt từ mẫu.
5. **Không đề xuất rule trùng lặp** (cùng column + cùng rule_type).
6. ROW_COUNT dùng column = null (rule cấp bảng).
7. Độ ưu tiên severity: NOT_NULL / UNIQUE trên cột id → CRITICAL; \
   RANGE / FRESHNESS → HIGH; ACCEPTED_VALUES / NULL_RATE → MEDIUM; REGEX_FORMAT → LOW.
8. **`dimension`** — ĐÂY LÀ FIELD RIÊNG, KHÔNG PHẢI rule_type. \
   Các giá trị hợp lệ của `dimension` là: COMPLETENESS, UNIQUENESS, VALIDITY, ACCURACY, CONSISTENCY, FRESHNESS. \
   **TUYỆT ĐỐI KHÔNG điền các giá trị này vào trường `rule_type`.** \
   Mapping `rule_type` → `dimension` đề nghị dùng: \
   - NOT_NULL, NULL_RATE → dimension = COMPLETENESS \
   - UNIQUE → dimension = UNIQUENESS \
   - RANGE, ACCEPTED_VALUES, REGEX_FORMAT, ROW_COUNT → dimension = VALIDITY \
   - FRESHNESS → dimension = FRESHNESS \
   - CROSS_FIELD_COMPARISON → dimension = CONSISTENCY \
   - Nếu không chắc → dimension = VALIDITY \
9. **`rule_description`**: Một câu tiếng Việt dễ hiểu, tự nhiên dành cho Data Steward. \
   Nêu tên cột bằng tiếng Việt (nếu có trong Data Dictionary) kèm tên kỹ thuật trong ngoặc, \
   sau đó mô tả điều kiện một cách có ngữ cảnh. \
   VD: "Cước phí cơ bản (fare_amount) không được mang giá trị âm, vì đây là tiền tính theo đồng hồ chứ không phải hoàn tiền." \
   KHÔNG viết kiểu template máy móc như "Cột X không được để trống." hay "Cột X phải có giá trị từ A đến B."
10. **Đề xuất đầy đủ:** Không chỉ trả về vài rule đại diện. Hãy duyệt hết checklist evidence \
    được cung cấp trong user message và tạo rule cho từng ứng viên đủ bằng chứng. \
    Một cột có thể có nhiều rule khác loại; chỉ loại ứng viên khi thực sự mâu thuẫn với ý nghĩa nghiệp vụ. \
    Với bảng dữ liệu giàu evidence, mục tiêu thông thường là 1-3 rule phù hợp cho mỗi cột.
11. Với `CROSS_FIELD_COMPARISON`, sao chép nguyên vẹn `parameters.target_column` và \
    `parameters.operator` từ checklist vào field `parameters` của structured output. \
    Không đặt `target_column` hoặc `operator` ở cấp ngoài của rule và không tự thay đổi toán tử.
12. Khi digest có `dashboard_candidate_mode = true`, đây là product workflow có guardrail nghiêm ngặt: \
    checklist chỉ gồm candidate đã qua evidence/policy filter. Phải trả về MỖI candidate đúng một lần, \
    theo đúng thứ tự checklist, tối đa một rule cho mỗi `rule_type`, và phải sao chép nguyên vẹn `parameters` \
    cho cả RANGE, ACCEPTED_VALUES lẫn CROSS_FIELD_COMPARISON. Không tự tạo threshold, enum, cột hoặc \
    rule_type mới; chỉ viết severity, mô tả và rationale cho Data Steward.
13. Với `REGEX_FORMAT`, phải điền biểu thức chính quy (regular expression) tương ứng vào `parameters.regex` \
    khi digest hoặc Data Dictionary cung cấp bằng chứng format rõ ràng. Không suy ra regex chỉ từ tên cột.
14. Chỉ chọn `selected_evidence_refs` từ `evidence_items[].id` của đúng candidate. Không trả lại giá trị metric; \
    server sẽ resolve metric từ digest. Mọi `parameter_provenance.source_ref` phải nằm trong danh sách đã chọn.
15. `rule_name` là tiêu đề ngắn; `rule_description` mô tả điều kiện; `business_rationale` mô tả tác động nghiệp vụ. \
    Không lặp cùng một câu cho cả ba field.
16. `proposal_basis` phản ánh nguồn thực tế. Pattern chỉ quan sát được phải là DATA_PROFILE; dùng MIXED khi có \
    cả profile và policy/dictionary/schema. `assumptions` phải nêu rõ giới hạn của sample hoặc ngưỡng suy diễn.
17. Confidence rubric: schema constraint/policy authoritative có evidence_strength cao; sample pattern có \
    business_support thấp nếu chưa có tài liệu xác nhận. Không mặc định mọi score bằng 1.0.

**⚠️ NHẮC LẠI:** Trường `rule_type` CHỈ được nhận 9 giá trị sau, không hơn không kém: \
NOT_NULL, UNIQUE, RANGE, ACCEPTED_VALUES, REGEX_FORMAT, FRESHNESS, ROW_COUNT, NULL_RATE, CROSS_FIELD_COMPARISON.
"""

_RULE_PROPOSER_FEW_SHOT = """\
## Ví dụ few-shot — structured proposal mới

Các ví dụ dưới đây minh hoạ văn phong và mức độ lập luận kỳ vọng. \
Đây KHÔNG phải rule cho dataset hiện tại — chỉ dùng để học style.

Các ID dưới đây chỉ minh hoạ format; output thật phải dùng đúng ID từ checklist hiện tại.

### Ví dụ 1 — NOT_NULL có schema/policy support

```json
{
  "column": "tpep_pickup_datetime",
  "rule_type": "NOT_NULL",
  "parameters": {},
  "rule_name": "Thời điểm đón khách phải được ghi nhận",
  "rule_description": "Thời điểm bắt đầu chuyến đi (tpep_pickup_datetime) phải luôn có giá trị, vì đây là mốc thời gian thiết yếu để tính cước và kiểm tra tính liên tục của dữ liệu.",
  "business_rationale": "Thiếu thời điểm đón làm các phép tính thời lượng và kiểm tra thứ tự chuyến đi không đáng tin cậy.",
  "proposal_basis": "MIXED",
  "selected_evidence_refs": ["schema:tpep_pickup_datetime:has_pk_constraint", "profile:tpep_pickup_datetime:no_nulls"],
  "parameter_provenance": [],
  "assumptions": [],
  "confidence": {"overall": 0.9, "evidence_strength": 1.0, "business_support": 0.85, "sample_representativeness": 0.85, "explanation": "Có schema support và profile nhất quán."},
  "severity": "CRITICAL",
  "dimension": "COMPLETENESS",
  "ai_reasoning": "Schema xác định đây là trường bắt buộc và profile không ghi nhận giá trị thiếu."
}
```

### Ví dụ 2 — NULL_RATE sẽ fail trên baseline hiện tại

```json
{
  "column": "passenger_count",
  "rule_type": "NULL_RATE",
  "parameters": {"max_null_pct": 10.0},
  "rule_name": "Tỷ lệ thiếu số hành khách không vượt quá 10%",
  "rule_description": "Tỷ lệ thiếu của passenger_count phải nhỏ hơn hoặc bằng 10%.",
  "business_rationale": "Mức thiếu cao làm giảm độ tin cậy của phân tích nhu cầu hành khách.",
  "proposal_basis": "DATA_PROFILE",
  "selected_evidence_refs": ["profile:passenger_count:null_pct"],
  "parameter_provenance": [{"parameter_name": "max_null_pct", "source_type": "DATA_PROFILE", "source_ref": "profile:passenger_count:null_pct", "derivation_method": "steward-review threshold below observed baseline"}],
  "assumptions": ["Ngưỡng 10% chưa được policy nghiệp vụ xác nhận và sẽ fail trên baseline 15.3% hiện tại."],
  "confidence": {"overall": 0.65, "evidence_strength": 0.9, "business_support": 0.4, "sample_representativeness": 0.65, "explanation": "Metric rõ ràng nhưng threshold chưa có policy xác nhận."},
  "severity": "MEDIUM",
  "dimension": "COMPLETENESS",
  "ai_reasoning": "Profile ghi nhận null_pct 15.3%; threshold 10% là đề xuất cần Steward xác nhận và hiện sẽ làm rule fail."
}
```
"""

_RULE_PROPOSER_USER = """\
## Ngữ cảnh domain
{domain_context}

## Data Dictionary (Từ điển dữ liệu và ý nghĩa các trường)
```json
{data_dictionary}
```

## Lịch sử rule (nếu có)
{historical_rules}

## Digest profile bảng `{table_name}`
```json
{table_digest}
```

## Checklist rule ứng viên sinh tự động từ evidence
```json
{coverage_requirements}
```

Checklist trên là danh sách cần đánh giá đầy đủ, không phải ví dụ. Sau khi đánh giá, đề xuất tất cả các \
ứng viên có evidence mạnh và ý nghĩa để bảo vệ chất lượng dữ liệu (không giới hạn số lượng). Mỗi rule tạo ra phải giữ nguyên đúng tên `column` trong digest và phải \
dẫn chứng evidence tương ứng trong `ai_reasoning`. Với `CROSS_FIELD_COMPARISON`, phải sao chép đúng object \
`parameters` từ checklist vào structured output. Không tạo rule ngoài checklist trừ khi Data Dictionary \
cung cấp bằng chứng nghiệp vụ rõ ràng.

{few_shot_examples}

Hãy trả về JSON structured output theo schema TableRuleProposal. \
Điền trường "table" = "{table_name}". \
Điền trường "dimension" theo bảng phân loại DQ dimension đã hướng dẫn. \
Điền trường "rule_description": một câu tiếng Việt tự nhiên, có ngữ cảnh nghiệp vụ, tránh template máy móc. \
Điền trường "ai_reasoning": rationale ngắn gọn, nêu evidence aggregate và lý do nghiệp vụ mà không trình bày chuỗi suy luận nội bộ. \
Chỉ chọn evidence ID có trong `evidence_items`; không sao chép hoặc tự tạo giá trị metric. \
Điền đầy đủ rule_name, business_rationale, proposal_basis, parameter_provenance, assumptions và confidence. \
Đề xuất đầy đủ và chính xác.
"""

rule_proposer_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", _RULE_PROPOSER_SYSTEM),
        ("user", _RULE_PROPOSER_USER),
    ]
)


dashboard_rule_proposer_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """You are a constrained data-quality candidate selector. The server has already built an allow-listed, evidence-backed candidate checklist. Return every supplied candidate exactly once and copy candidate_id, column, rule_type, and parameters exactly. Select only evidence_items IDs from that candidate and use those same IDs for parameter provenance. Never copy or invent metric values. Write rule_name, rule_description, business_rationale, proposal_basis, assumptions, a calibrated confidence breakdown, and a concise rationale. Do not mention a threshold, enum value, relationship, policy, or business fact absent from the candidate. Use the structured output schema exactly.""",
        ),
        (
            "user",
            """Table: {table_name}

Aggregate-only digest:
```json
{table_digest}
```

Allowed candidate checklist:
```json
{coverage_requirements}
```

Return the strongest diverse candidates in checklist order. Set table to {table_name}.""",
        ),
    ]
)


# ---------------------------------------------------------------------------
# SQL Repair Prompt (Agentic Repair Loop)
# ---------------------------------------------------------------------------
# Input variables: table_name, schema_info, rules_json, error_sql, db_error
# ---------------------------------------------------------------------------

_SQL_REPAIR_SYSTEM = """\
Bạn là một chuyên gia cơ sở dữ liệu SQL (PostgreSQL & SQLite). \
Nhiệm vụ của bạn là sửa một câu lệnh SQL bị lỗi cú pháp hoặc ngữ nghĩa được báo cáo bởi công cụ cơ sở dữ liệu.

Quy tắc BẮT BUỘC:
1. CHỈ TRẢ VỀ CÂU LỆNH SELECT. Tuyệt đối không sinh DDL/DML (UPDATE, DELETE, INSERT, DROP, ALTER, v.v.).
2. Đảm bảo giữ nguyên các bind parameters có dạng `:param_name` thay vì điền cứng giá trị.
3. Không làm thay đổi logic kiểm tra dữ liệu của các rules đã định nghĩa.
4. Trả về DUY NHẤT câu lệnh SQL đã sửa trong một khối mã markdown: ```sql ... ```. Không giải thích dông dài.
"""

_SQL_REPAIR_USER = """\
Bảng mục tiêu: `{table_name}`

Schema cột hiện tại của bảng:
```json
{schema_info}
```

Các quy tắc (rules) được kiểm thử trong câu lệnh này:
```json
{rules_json}
```

Câu lệnh SQL bị lỗi:
```sql
{error_sql}
```

Thông báo lỗi chi tiết từ cơ sở dữ liệu:
```
{db_error}
```

Hãy phân tích nguyên nhân lỗi (ví dụ: sai tên cột, sai hàm regex, sai cú pháp CASE WHEN, thừa/thiếu dấu ngoặc) và trả về câu lệnh SQL đã được sửa hoàn chỉnh.
"""

sql_repair_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", _SQL_REPAIR_SYSTEM),
        ("user", _SQL_REPAIR_USER),
    ]
)


_DBT_REPAIR_SYSTEM = """\
You repair a generated dbt schema YAML file. Return only the complete repaired YAML.
Do not use Markdown fences or explanations. Preserve the approved models, columns, and
test intent. Do not add hooks, vars, exposures, sources, macros, SQL, or arbitrary project
configuration. The root document must contain only `version` and `models`.
"""

_DBT_REPAIR_USER = """\
Approved rules (source of truth):
```json
{approved_rules_json}
```

Generated dbt YAML:
```yaml
{dbt_yaml}
```

Validation error:
```
{validation_error}
```

Return the complete corrected dbt schema YAML and nothing else.
"""

dbt_repair_prompt = ChatPromptTemplate.from_messages(
    [("system", _DBT_REPAIR_SYSTEM), ("user", _DBT_REPAIR_USER)]
)


# ---------------------------------------------------------------------------
# Steward Insights Prompt (DQ Advisor & Executive Summary)
# ---------------------------------------------------------------------------
# Input variables: dataset_id, dq_score, dq_grade, dq_dimensions_json,
#                  test_summary_json, failed_rules_json, anomalies_json, profile_digest_json
# ---------------------------------------------------------------------------

_STEWARD_INSIGHTS_SYSTEM = """\
Bạn là một AI Data Quality & Governance Advisor chuyên nghiệp dành riêng cho Data Steward và Data Management Team.
Nhiệm vụ của bạn là phân tích kết quả kiểm thử chất lượng dữ liệu (DQ Test Run), các cảnh báo bất thường (Anomalies) và hồ sơ dữ liệu (Profile Digest) để tạo ra một bản Báo Cáo Tổng Kết (Steward Insights Report) sâu sắc, khách quan và có tính hành động cao.

## Yêu cầu trình bày:
Báo cáo phải được viết bằng tiếng Việt, định dạng Markdown rõ ràng, chuyên nghiệp với 4 phần chuẩn:

### 1. 📊 Tổng Quan Sức Khỏe Dữ Liệu (Executive DQ Summary)
- Tóm tắt điểm DQ Score, Grade (A/B/C/D) và đánh giá nhanh hiện trạng dữ liệu.
- Phân tích ngắn gọn tình hình phân bổ chất lượng theo các chiều (Completeness, Validity, Uniqueness, Consistency, Freshness).

### 2. 🚨 Phân Tích Lỗi & Cảnh Báo Bất Thường (Failure & Anomaly Drill-Down)
- Đi sâu vào các rule bị FAILED hoặc có cảnh báo ANOMALY (đột biến Z-score).
- Nêu giả thuyết nguyên nhân gốc rễ (Potential Root Cause) kết hợp với ngữ cảnh phân phối dữ liệu (ví dụ: null spike do thiết bị, giá trị âm do nghiệp vụ hoàn tiền, hay lỗi pipeline).
- Đánh giá mức độ rủi ro đối với downstream reports / business metrics.

### 3. 🎯 Đánh Giá & Gợi Ý Tinh Chỉnh Ruleset (Rule Tuning Recommendations)
- Chỉ ra các rule có thể đang quá khắt khe (Overly strict / False positive) và gợi ý điều chỉnh ngưỡng (threshold) hoặc bộ lọc (WHERE condition).
- Gợi ý bổ sung rule mới nếu phát hiện vùng dữ liệu quan trọng chưa được bảo vệ.

### 4. 🛠️ Kế Hoạch Hành Động Đề Xuất (Actionable Next Steps)
- Đưa ra danh sách hành động cụ thể dạng Markdown Checklist (`- [ ] ...`) phân rõ trách nhiệm:
  - Cho Data Steward (Review, approve rule điều chỉnh, nghiệm thu dataset).
  - Cho Data Engineering / Source Team (Kiểm tra pipeline, sửa source bug nếu có).
"""

_STEWARD_INSIGHTS_USER = """\
Dưới đây là thông tin chi tiết của đợt kiểm thử chất lượng dữ liệu cho dataset `{dataset_id}`:

### 1. Điểm số & Xếp hạng:
- **DQ Score**: {dq_score}/100 (Xếp hạng: **{dq_grade}**)
- **Điểm theo từng chiều DQ (Dimensions)**:
```json
{dq_dimensions_json}
```

### 2. Thống kê kết quả kiểm thử:
```json
{test_summary_json}
```

### 3. Chi tiết các Rule bị Vi Phạm hoặc Lỗi (Failed / Error Rules):
```json
{failed_rules_json}
```

### 4. Các cảnh báo bất thường (Anomalies & Z-Score spikes):
```json
{anomalies_json}
```

### 5. Profile Digest của Dataset:
```json
{profile_digest_json}
```

Hãy tạo bản Báo cáo Steward Insights hoàn chỉnh theo đúng cấu trúc 4 phần đã yêu cầu.
"""

steward_insights_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", _STEWARD_INSIGHTS_SYSTEM),
        ("user", _STEWARD_INSIGHTS_USER),
    ]
)

