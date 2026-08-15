# Plan: Cải thiện Profiler Node cho Rule Proposer Agent

## Bối cảnh

Hệ thống là 1 AI Agent (LangGraph) xây dựng & kiểm tra Data Quality cho dữ liệu vận
hành dạng vận tải (thử nghiệm trên NYC Yellow Taxi). Node Profiler hiện có 2 tool:

- `src/agents/tools/db_profiler_tool.py` — hàm `profile_database`: quét schema +
  thống kê (null, min/max/mean, distinct, top_categories, freshness) cho 1 bảng,
  chiến lược 2 pha (full-table cho null/min/max, sample cho distinct/categorical).
- `src/agents/tools/profile_digest.py` — hàm `generate_profile_digest`: nén profile
  thô thành digest gọn để đưa vào prompt của Rule Proposer Agent (LLM).

Output digest hiện tại (`{name, type, role, null_pct, values?, range?, signals?}`
mỗi cột) đủ cho rule cơ bản (`not_null`, `range` thô, `allowed_values` cho category
phổ biến) nhưng **chưa đủ để LLM sinh rule chính xác, ít false positive** như yêu
cầu của đề bài. Plan này liệt kê các phần cần bổ sung, ưu tiên theo tỷ lệ
impact/effort.

**Nguyên tắc bắt buộc khi sửa (áp dụng cho mọi task bên dưới):**
- Không phá schema JSON hiện có — chỉ **thêm field mới**, không đổi tên/xóa field cũ
  (`profile_digest.py` và các node khác đang phụ thuộc vào shape hiện tại).
  Nếu cần đổi tên, giữ field cũ dạng deprecated + thêm field mới, note rõ trong docstring.
- Mọi aggregate mới thêm vào Pha 1 (full-table) phải gộp chung vào CÙNG 1 câu SQL
  `full_sql` hiện có trong `profile_database`, không thêm round-trip DB riêng —
  tránh regressive về hiệu năng trên bảng lớn (đây là constraint cứng của đề bài).
- Giữ nguyên nguyên tắc: field nào là ước lượng từ sample phải có cờ đánh dấu rõ
  (`*_is_estimate` hoặc note trong `sample.caveat`), không để LLM hiểu nhầm là full-table.
- Sau mỗi task, cập nhật lại 2 file ví dụ `profile_raw_example.json` và
  `profile_digest_example.json` cho khớp schema mới (không bắt buộc, nhưng nên làm
  để làm tài liệu tham chiếu).

---

## P0 — Ưu tiên cao nhất (rẻ, tác động lớn tới độ chính xác rule)

### P0.1 — Lấy PK / FK / Unique constraint từ SQLAlchemy inspector

**Vấn đề:** Hiện tại chỉ gọi `inspector.get_columns(table_name)`. Constraint có sẵn
trong schema (ground-truth, không cần LLM đoán) đang bị bỏ qua hoàn toàn.

**Việc cần làm** (`db_profiler_tool.py`, trong `profile_database`):
1. Gọi thêm `inspector.get_pk_constraint(table_name)`,
   `inspector.get_foreign_keys(table_name)`,
   `inspector.get_unique_constraints(table_name)`.
2. Thêm vào JSON output 1 field mới ở cấp bảng (ngang hàng `table_metadata`,
   `columns`): `"schema_constraints": {"primary_key": [...], "foreign_keys": [...], "unique_constraints": [...]}`.
3. Với FK, giữ đủ thông tin: cột nguồn, bảng đích, cột đích
   (`{"constrained_columns": [...], "referred_table": ..., "referred_columns": [...]}`).
4. SQLite không hỗ trợ đầy đủ FK reflection theo mặc định — cần bật
   `PRAGMA foreign_keys=ON` hoặc kiểm tra fallback, xử lý an toàn (try/except,
   không làm fail toàn bộ profiling nếu không lấy được constraint).

**Việc cần làm** (`profile_digest.py`, trong `generate_profile_digest`):
1. Đưa `schema_constraints` vào digest ở cấp bảng.
2. Với mỗi cột nằm trong PK hoặc unique constraint, thêm signal
   `"has_pk_constraint"` / `"has_unique_constraint"` vào `col_digest["signals"]`.
3. Với cột là FK, thêm field `"references": {"table": ..., "column": ...}` vào
   `col_digest`.

**Acceptance criteria:**
- Chạy trên 1 bảng có PK rõ ràng → digest có `signals: ["has_pk_constraint", ...]`
  đúng cột PK, không cần dựa vào `unique_in_sample` (không đáng tin) nữa.
- Test không có FK (như bảng taxi gốc không có FK thật) vẫn chạy được, trả
  `"foreign_keys": []`, không lỗi.

---

### P0.2 — Sửa lại ý nghĩa signal `unique_in_sample`

**Vấn đề:** Hiện tại `distinct_in_sample == sampled_rows` chỉ đúng trên MẪU. Với
`sampling_rate < 1.0`, 1 cột "unique trong sample 10%" hoàn toàn có thể trùng lặp ở
90% còn lại → nếu LLM tin signal này và đề xuất rule `uniqueness` cứng, rule sẽ
fail ngay khi chạy full table ở production (false alarm).

**Việc cần làm** (`db_profiler_tool.py`):
1. Đổi tên signal tương ứng thành rõ ràng hơn ở tầng dữ liệu thô — ví dụ đảm bảo
   digest downstream biết đây là ước lượng (xem P0.3 bên dưới về full distinct
   cho ứng viên key).
2. KHÔNG tự ý coi `distinct_in_sample == sampled_rows` là bằng chứng unique khi
   `is_sampled = True`.

**Việc cần làm** (`profile_digest.py`):
1. Đổi tên signal: `unique_in_sample` → chỉ giữ nguyên khi `is_sampled = False`
   (lúc đó nó thực sự là full-table nên đáng tin). Khi `is_sampled = True`, đổi
   thành `unique_in_sample_only` kèm chú thích rõ đây chỉ là gợi ý cần verify thêm,
   KHÔNG phải bằng chứng đủ để tạo rule `uniqueness` cứng.
2. Rule Proposer prompt (ngoài phạm vi 2 file này, nhưng cần note lại) phải được
   dặn: chỉ tin `has_pk_constraint`/`has_unique_constraint` (từ P0.1) hoặc
   `unique_full_table` (từ P0.3) khi đề xuất rule `uniqueness`, không dùng
   `unique_in_sample_only`.

**Acceptance criteria:**
- Với `sampling_rate = 0.1` và 1 cột thực tế KHÔNG unique toàn bảng nhưng unique
  trong mẫu nhỏ, digest phải gắn `unique_in_sample_only`, không phải
  `unique_in_sample`.

---

### P0.3 — Full-table distinct count cho các cột ứng viên "key"

**Vấn đề:** P0.2 chỉ ra rủi ro, P0.3 là cách khắc phục triệt để cho các cột nghi
ngờ là khóa (id-like columns) mà KHÔNG có PK constraint chính thức (schema thiếu
ràng buộc, thường gặp ở dữ liệu vận hành thực tế).

**Việc cần làm** (`db_profiler_tool.py`):
1. Xác định "ứng viên key": cột có tên kết thúc bằng `_id`/`id`, hoặc cột đã có
   `distinct_in_sample` gần bằng `sampled_rows` (vd >= 95%) ở Pha 2.
2. Với các cột ứng viên này (dự kiến số lượng nhỏ, không phải tất cả cột), chạy
   thêm 1 aggregate `COUNT(DISTINCT col)` trên **full table** — tách riêng khỏi
   Pha 2 (sample), gộp vào cùng batch nếu có thể để giảm round-trip.
3. Thêm field `"distinct_full_table"` (nullable — chỉ có ở cột ứng viên) và
   `"is_unique_full_table"` (bool) vào `col_stats`.

**Việc cần làm** (`profile_digest.py`):
1. Khi có `is_unique_full_table = True`, gắn signal `"unique_full_table"` (đáng
   tin cậy 100%, LLM có thể dùng trực tiếp cho rule `uniqueness`).

**Acceptance criteria:**
- Cột `trip_id` (giả lập) unique 100% toàn bảng → digest có
  `signals: ["unique_full_table"]`.
- Số lượng cột được full-scan distinct phải giới hạn (không chạy cho tất cả cột,
  chỉ ứng viên) — kiểm tra bằng cách log số cột được full-scan, phải << tổng số cột.

---

## P1 — Ưu tiên trung bình (cần cho rule "range" và "format" chính xác hơn)

### P1.1 — Thêm percentile cho cột numeric

**Vấn đề:** Chỉ có `min/max/mean`, không đủ để phân biệt "outlier hiếm" (rule nên
strict) với "biến động tự nhiên chiếm % đáng kể" (rule nên lỏng hơn hoặc tách rule
khác). Đây là gốc rễ khiến rule `range` hiện tại dễ overfit vào giá trị min/max thô.

**Việc cần làm** (`db_profiler_tool.py`):
1. Với Postgres: dùng `percentile_cont(ARRAY[0.01,0.05,0.25,0.5,0.75,0.95,0.99])
   WITHIN GROUP (ORDER BY col)` — có thể chạy full-table vì Postgres tối ưu tốt.
2. Với SQLite (không có `percentile_cont` native): tính trên **sample** (Pha 2),
   dùng cách xấp xỉ (sort + lấy theo offset, hoặc `NTILE` nếu SQLAlchemy hỗ trợ qua
   raw SQL). Đánh dấu rõ `percentiles_is_estimate: true` khi tính trên SQLite/sample.
3. Thêm field `"percentiles": {"p1": ..., "p5": ..., "p25": ..., "p50": ...,
   "p75": ..., "p95": ..., "p99": ...}` vào `col_stats` cho cột numeric.
4. Cân nhắc chi phí: KHÔNG bắt buộc chạy cho mọi cột numeric nếu bảng quá lớn —
   có thể thêm điều kiện bật/tắt qua tham số (`compute_percentiles: bool = True`).

**Việc cần làm** (`profile_digest.py`):
1. Đưa `percentiles` vào digest, thay vì chỉ `range: [min, max]`. Format gợi ý:
   `"range": [min, max], "typical_range": [p5, p95]`.
2. Thêm signal khi khoảng cách min/max với p1/p99 chênh lệch lớn (gợi ý outlier
   hiếm) — ví dụ `"has_extreme_outliers"` khi `min < p1 * 3` hoặc tương tự (ngưỡng
   cụ thể để agent tự quyết định khi code, không cứng trong plan này).

**Acceptance criteria:**
- Digest của `fare_amount` (có outlier âm hiếm) phải phân biệt được `range` (chứa
  outlier) và `typical_range` (vùng phần lớn dữ liệu nằm trong) — 2 con số khác nhau
  rõ rệt trong ví dụ test.

---

### P1.2 — Thống kê % giá trị âm / bằng 0 cho numeric

**Vấn đề:** Biết `min = -52.0` nhưng không biết có 3 dòng hay 5% dòng âm — 2 tình
huống cần rule khác hẳn nhau (loại lỗi cần fix cứng vs. business case hợp lệ như
refund/cancellation cần rule khác).

**Việc cần làm** (`db_profiler_tool.py`):
1. Thêm vào `full_select` (Pha 1, cùng batch với null_count):
   `SUM(CASE WHEN col < 0 THEN 1 ELSE 0 END)` và
   `SUM(CASE WHEN col = 0 THEN 1 ELSE 0 END)` cho cột numeric.
2. Thêm field `"negative_pct"` và `"zero_pct"` vào `col_stats`.

**Việc cần làm** (`profile_digest.py`):
1. Đưa `negative_pct`/`zero_pct` vào `col_digest` khi > 0.
2. Thêm signal `"has_negative_values"` khi `negative_pct > 0`.

**Acceptance criteria:** Digest của `fare_amount` show rõ `negative_pct` là số nhỏ
(vd 0.02%) để LLM tự tin đề xuất `min: 0` thay vì giữ nguyên min quan sát được.

---

### P1.3 — Thống kê cơ bản cho text/format (freetext & categorical dạng chuỗi)

**Vấn đề:** Không có tín hiệu nào cho rule loại "format" (1 trong 5 loại rule đề
bài yêu cầu: uniqueness, not-null, range, format, freshness).

**Việc cần làm** (`db_profiler_tool.py`):
1. Với cột `is_text = True`, thêm vào Pha 1: `MIN(LENGTH(col))`,
   `MAX(LENGTH(col))`, `AVG(LENGTH(col))`.
2. (Tùy chọn, có thể để P2 nếu effort cao) thêm tỷ lệ match với vài pattern cơ bản
   tính trên sample: numeric-only, alpha-only, chứa khoảng trắng đầu/cuối, chứa ký
   tự đặc biệt ngoài alphanumeric.
3. Thêm field `"length_stats": {"min": ..., "max": ..., "avg": ...}` và (nếu làm)
   `"pattern_hints": {...}` vào `col_stats`.

**Việc cần làm** (`profile_digest.py`):
1. Đưa `length_stats` vào digest cho cột `role: freetext` hoặc `categorical` dạng
   text.
2. Thêm signal `"fixed_length"` khi `min_length == max_length` (gợi ý mạnh cho rule
   format kiểu mã cố định, vd mã bưu điện, mã sản phẩm).

**Acceptance criteria:** Cột dạng mã cố định (giả lập, vd `store_and_fwd_flag` độ
dài luôn = 1) phải có signal `"fixed_length"` trong digest.

---

## P2 — Ưu tiên thấp hơn (giá trị cao nhưng effort/rủi ro lớn hơn, làm sau)

### P2.1 — Tín hiệu liên cột (cross-column) ở cấp bảng

**Vấn đề:** Toàn bộ digest hiện tại là thống kê từng cột độc lập. Rule quan trọng
nhất với dữ liệu vận hành lại là quan hệ liên cột
(`pickup_datetime < dropoff_datetime`, tổng các thành phần tiền = `total_amount`).

**Việc cần làm** (`db_profiler_tool.py` hoặc 1 hàm mới riêng, KHÔNG nhồi vào
`profile_database` để tránh làm phình 1 hàm — cân nhắc tách file mới
`cross_column_profiler_tool.py`):
1. Tự động phát hiện cặp cột datetime trong cùng bảng → tính
   `% dòng vi phạm thứ tự (col_a > col_b)` bằng 1 aggregate full-table riêng.
2. (Nếu còn thời gian) phát hiện heuristic cột tiền tệ liên quan
   (`fare_amount + tip_amount + tolls_amount + ... ≈ total_amount`) dựa theo tên
   cột khớp keyword tài chính phổ biến, tính % dòng lệch quá ngưỡng (vd > 1%).
3. Output dạng field mới cấp bảng: `"cross_column_hints": [{"type": "datetime_order",
   "columns": ["tpep_pickup_datetime", "tpep_dropoff_datetime"],
   "violation_pct": ...}, ...]`.

**Việc cần làm** (`profile_digest.py`):
1. Đưa `cross_column_hints` vào digest ở cấp bảng, giữ nguyên cấu trúc đơn giản
   để LLM đọc trực tiếp thành rule ứng viên.

**Acceptance criteria:** Với data giả lập có vài dòng `dropoff < pickup`, digest
phải show đúng `violation_pct > 0` cho cặp cột đó.

**Lưu ý rủi ro:** Đây là phần dễ nổ số lượng tổ hợp cột (O(n²) cặp datetime nếu
bảng có nhiều cột thời gian) — cần giới hạn số cặp xét (vd chỉ xét khi bảng có
≤ 6 cột datetime) để không làm chậm profiling.

---

### P2.2 — Mở rộng top_categories khi cardinality thấp

**Vấn đề:** Hiện tại luôn cắt `LIMIT 5` cho categorical, có thể làm rớt mất giá
trị hiếm nhưng quan trọng (vd mã lỗi/mã đặc biệt tần suất thấp).

**Việc cần làm** (`db_profiler_tool.py`):
1. Trong câu `cat_query`, đổi `LIMIT 5` thành động: nếu
   `distinct_in_sample <= 20` thì lấy toàn bộ (không giới hạn hoặc `LIMIT 20`),
   ngược lại giữ `LIMIT 5` như cũ (tránh phình response khi cardinality cao).

**Việc cần làm** (`profile_digest.py`):
1. Không cần đổi logic, chỉ cần đảm bảo `values` trong digest phản ánh đúng danh
   sách đầy đủ khi có.

**Acceptance criteria:** Cột có 7 giá trị phân biệt (như `RatecodeID` trong ví dụ
trước) phải trả đủ cả 7 trong `top_categories`, không bị cắt còn 5.

---

## Ngoài phạm vi 2 file này (chỉ note lại, không phải việc của plan này)

- Prompt của Rule Proposer Agent cần được cập nhật để biết cách dùng các field mới
  (percentiles, schema_constraints, cross_column_hints...) — đây là việc của
  `rule_proposer_node.py`, không sửa trong lần này.
- Cần benchmark lại thời gian chạy `profile_database` trên bảng ~3 triệu dòng sau
  khi thêm percentile + full-distinct cho key candidates, để đảm bảo vẫn đáp ứng
  constraint hiệu năng của đề bài.
