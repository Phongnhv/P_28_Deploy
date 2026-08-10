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

## Các signal quan trọng từ digest (ưu tiên đọc kỹ)

- `has_pk_constraint` / `has_unique_constraint`: ràng buộc schema thật — dùng được cho rule UNIQUE.
- `unique_full_table`: COUNT(DISTINCT) trên toàn bảng đã xác nhận unique — đáng tin 100%.
- `unique_in_sample_only`: unique chỉ trong mẫu — KHÔNG đủ bằng chứng, KHÔNG tạo rule UNIQUE cứng.
- `has_extreme_outliers`: min/max xa p1/p99 — nên dùng `typical_range` thay vì `range` khi đặt RANGE.
- `has_negative_values`: tồn tại giá trị âm — xem `negative_pct` để quyết định rule có cần min >= 0 không.
- `fixed_length`: độ dài chuỗi cố định — gợi ý mạnh cho REGEX_FORMAT hoặc length constraint.
- `cross_column_hints` (cấp bảng): gợi ý quan hệ liên cột (e.g., pickup <= dropoff) — xem xét rule RANGE hoặc custom.

## Hướng dẫn quan trọng

1. **Chỉ đề xuất rule cho các cột có tên xuất hiện trong digest đầu vào.** \
   Không được tự bịa tên cột.
2. **`ai_reasoning` là suy luận logic của bạn — không phải bản tóm tắt số liệu.** \
   Viết như cách một chuyên gia DQ thực sự giải thích quyết định của mình: \
   bắt đầu từ điều bạn quan sát trong dữ liệu, kết nối với ý nghĩa nghiệp vụ của cột (từ Data Dictionary), \
   sau đó lý giải tại sao threshold/constraint đó là hợp lý — bao gồm cả các edge case bạn đã cân nhắc. \
   Văn phong tự nhiên, mạch lạc, không dùng dấu bullet hay template cứng nhắc.
3. **RANGE không được ghim sát vào giá trị quan sát.** \
   Ưu tiên dùng `typical_range` [p5, p95] nếu có, mở rộng ≥ 10% mỗi phía. \
   Nếu có signal `has_extreme_outliers`, đặt threshold dựa trên typical_range, không dùng min/max tuyệt đối.
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
   - Nếu không chắc → dimension = VALIDITY \
9. **`rule_description`**: Một câu tiếng Việt dễ hiểu, tự nhiên dành cho Data Steward. \
   Nêu tên cột bằng tiếng Việt (nếu có trong Data Dictionary) kèm tên kỹ thuật trong ngoặc, \
   sau đó mô tả điều kiện một cách có ngữ cảnh. \
   VD: "Cước phí cơ bản (fare_amount) không được mang giá trị âm, vì đây là tiền tính theo đồng hồ chứ không phải hoàn tiền." \
   KHÔNG viết kiểu template máy móc như "Cột X không được để trống." hay "Cột X phải có giá trị từ A đến B."

**⚠️ NHẮC LẠI:** Trường `rule_type` CHỈ được nhận 8 giá trị sau, không hơn không kém: \
NOT_NULL, UNIQUE, RANGE, ACCEPTED_VALUES, REGEX_FORMAT, FRESHNESS, ROW_COUNT, NULL_RATE.
"""

_RULE_PROPOSER_FEW_SHOT = """\
## Ví dụ few-shot — Chất lượng viết `rule_description` và `ai_reasoning`

Các ví dụ dưới đây minh hoạ văn phong và mức độ lập luận kỳ vọng. \
Đây KHÔNG phải rule cho dataset hiện tại — chỉ dùng để học style.

### Ví dụ 1 — NOT_NULL trên cột quan trọng

```json
{
  "column": "tpep_pickup_datetime",
  "rule_type": "NOT_NULL",
  "rule_description": "Thời điểm bắt đầu chuyến đi (tpep_pickup_datetime) phải luôn có giá trị, vì đây là mốc thời gian thiết yếu để tính cước và kiểm tra tính liên tục của dữ liệu.",
  "ai_reasoning": "Profiler xác nhận null_pct = 0.0 trên toàn bộ 50,000 bản ghi quan sát, không có ngoại lệ nào. Điều này phù hợp hoàn toàn với đặc điểm nghiệp vụ: một chuyến taxi không thể tồn tại trong hệ thống nếu không có thời điểm đón khách — đây là sự kiện khởi tạo toàn bộ giao dịch. Bởi vậy rule NOT_NULL với severity CRITICAL là bắt buộc, không có vùng xám nào cần cân nhắc."
}
```

### Ví dụ 2 — RANGE trên cột số tiền có outlier

```json
{
  "column": "fare_amount",
  "rule_type": "RANGE",
  "parameters": {"min": 0.0, "max": 63.0},
  "rule_description": "Cước phí cơ bản (fare_amount) phải nằm trong khoảng từ 0 đến 63 đô — giá trị âm là bất hợp lý, còn các giá trị cực lớn thường là lỗi nhập liệu hơn là chuyến thực tế.",
  "ai_reasoning": "Digest cho thấy fare_amount có negative_pct = 4.7%, tức gần 5% chuyến có cước âm. Theo Data Dictionary, fare_amount là cước tính theo thời gian và quãng đường qua đồng hồ tính cước — không có kịch bản hợp lệ nào tạo ra cước âm; đây gần như chắc chắn là lỗi hoặc nhầm trường với bản ghi hoàn tiền. Về phía trên, signal has_extreme_outliers cho thấy max tuyệt đối lên đến hàng nghìn dollar, nhưng typical_range (p5–p95) là [3.0, 57.0]. Tôi mở rộng thêm 10% ở phía trên để có max = 63.0 — đủ phủ chuyến dài đặc biệt (ví dụ vùng ngoại ô) mà vẫn loại được các giá trị cực đoan rõ ràng là lỗi. Chọn min = 0 thay vì min > 0 để không loại các chuyến miễn phí hợp lệ."
}
```

### Ví dụ 3 — ACCEPTED_VALUES trên cột categorical

```json
{
  "column": "payment_type",
  "rule_type": "ACCEPTED_VALUES",
  "parameters": {"accepted_values": ["Credit card", "Cash", "No charge", "Dispute"]},
  "rule_description": "Phương thức thanh toán (payment_type) chỉ được nhận các giá trị đã định nghĩa trong hệ thống TLC — mọi mã ngoài danh sách này đều cho thấy dữ liệu bị lỗi hoặc chưa được chuẩn hoá.",
  "ai_reasoning": "Cột này có signal low_cardinality với 4 giá trị distinct quan sát được, hoàn toàn khớp với bảng mã trong Data Dictionary (1=Credit card, 2=Cash, 3=No charge, 4=Dispute). Đây là trường enum cứng do TLC quy định — tài xế chọn từ màn hình thiết bị, không nhập tự do. Bất kỳ giá trị nào ngoài 4 mã này đều là bằng chứng dữ liệu từ nguồn không chuẩn hoặc bug trong pipeline ánh xạ mã về label."
}
```

### Ví dụ 4 — NULL_RATE trên cột có missing data cao bất thường

```json
{
  "column": "passenger_count",
  "rule_type": "NULL_RATE",
  "parameters": {"max_null_pct": 10.0},
  "rule_description": "Số hành khách (passenger_count) không nên bị thiếu quá 10% — mức null cao hơn cho thấy thiết bị tài xế đang gặp vấn đề ghi nhận hoặc dữ liệu không được đồng bộ đúng cách.",
  "ai_reasoning": "Profiler ghi nhận null_pct = 15.3%, đây là con số đáng chú ý vì passenger_count là trường tài xế nhập thủ công khi bắt đầu chuyến. Null trong trường hợp này không có nghĩa là 'không có hành khách' — mà là tài xế quên nhập hoặc thiết bị mất kết nối. Tôi không đặt ngưỡng quá chặt (5%) vì dữ liệu quan sát đã cho thấy 15.3% là baseline hiện tại, khả năng cao do đặc thù thiết bị của một vendor. Ngưỡng 10% được chọn để cảnh báo nếu tình trạng này lan rộng thêm, nhưng không tạo alert ồ ạt ngay lập tức."
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

{few_shot_examples}

Hãy trả về JSON structured output theo schema TableRuleProposal. \
Điền trường "table" = "{table_name}". \
Điền trường "dimension" theo bảng phân loại DQ dimension đã hướng dẫn. \
Điền trường "rule_description": một câu tiếng Việt tự nhiên, có ngữ cảnh nghiệp vụ, tránh template máy móc. \
Điền trường "ai_reasoning": suy luận logic mạch lạc, nêu rõ quá trình suy nghĩ từ dữ liệu → nghiệp vụ → quyết định threshold. \
Đề xuất đầy đủ và chính xác.
"""

rule_proposer_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", _RULE_PROPOSER_SYSTEM),
        ("user", _RULE_PROPOSER_USER),
    ]
)
