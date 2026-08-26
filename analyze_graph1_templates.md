# BÁO CÁO PHÂN TÍCH CHUYÊN SÂU & ĐÁNH GIÁ NGHIÊM KHẮC
## HỆ THỐNG PROMPTS (TEMPLATES.PY) & KIẾN TRÚC GRAPH 1 (RIDE PULSE DQ)

> **Tài liệu tham chiếu:** `src/agents/nodes/templates.py`, `architecture_diagram.md`, `src/agents/nodes/rule_proposer_node.py`, `src/agents/graph.py`  
> **Người thực hiện:** Senior AI Engineer / AI System Architect  
> **Chế độ kiểm tra:** Rà soát mã nguồn thực tế (Code Audit & Reverse-Engineering)

---

## 1. TỔNG QUAN HIỆN TRẠNG & ĐÁNH GIÁ CHUNG

Sau khi kiểm tra chi tiết từng dòng code trong `src/agents/nodes/templates.py`, đối chiếu với thiết kế Graph 1 trong `architecture_diagram.md` và mã thực thi trong `src/agents/nodes/rule_proposer_node.py`, chúng tôi đưa ra kết luận nghiêm khắc như sau:

> ⚠️ **KẾT LUẬN NGHIÊM KHẮC:**
> 1. Hệ thống đang tồn tại **sự mâu thuẫn kiến trúc (Architectural Inconsistency)** giữa thiết kế đồ thị 9-node (đã có Node 3 `data_dictionary_generator`, Node 4 `dataset_understanding`, Node 7 `prompt_customizer` để tổng quát hóa) và phần hiện thực prompt tại `templates.py` + `rule_proposer_node.py` (vẫn đang bị "trói chặt" vào dataset NYC Yellow Taxi bằng các hardcoded strings, hardcoded few-shots và hardcoded column names).
> 2. Có hiện tượng **Prompt Conflict (Xung đột chỉ thị)**: Node 7 sinh ra System Prompt chuyên biệt theo bảng nghiệp vụ (ví dụ: E-commerce, Banking), nhưng Node 8 lại nối chuỗi đó vào đuôi của `_RULE_PROPOSER_SYSTEM` (vốn mở đầu bằng câu *"Bạn là chuyên gia Data Quality cho hệ thống vận tải taxi NYC..."*). Điều này khiến LLM nhận 2 vai trò đối nghịch cùng lúc, làm tăng tỷ lệ hallucination và giảm độ chuẩn xác của rules.
> 3. Tồn tại **Prompt dư thừa / Dead Code**: `profiler_node_prompt` không được sử dụng ở bất kỳ đâu vì Profiler chạy hoàn toàn bằng công cụ thống kê SQL xác định (deterministic SQL profiling).
> 4. `templates.py` đang có 2 prompt đề xuất rule song song (`rule_proposer_prompt` và `generic_rule_proposer_prompt`) nhưng không được thiết kế nhất quán, thiếu generic few-shots chất lượng cao.

---

## 2. KIỂM TRA CHI TIẾT `SRC/AGENTS/NODES/TEMPLATES.PY`

Dưới đây là bảng rà soát chi tiết từng Prompt Template trong `templates.py`:

| STT | Tên Prompt Template | Dòng | Phân loại | Tình trạng hiện tại | Đánh giá & Vấn đề |
| :--- | :--- | :---: | :--- | :--- | :--- |
| 1 | `profiler_node_prompt` | 3–25 | ChatPromptTemplate | **DEAD CODE** | Không được import hay gọi ở bất kỳ file nào. Profiler thực tế chạy bằng SQL/Pandas. |
| 2 | `_RULE_PROPOSER_SYSTEM` | 34–108 | System String | **HARDCODED TAXI** | Mở đầu: *"Bạn là chuyên gia Data Quality (DQ) cho hệ thống vận tải taxi (NYC Yellow Taxi Trip Records)..."*. |
| 3 | `_RULE_PROPOSER_FEW_SHOT`| 111–236| String Examples | **HARDCODED TAXI** | 5 ví dụ few-shot fix cứng tên cột taxi (`tpep_pickup_datetime`, `passenger_count`, `trip_distance`, `tpep_dropoff_datetime`). |
| 4 | `_RULE_PROPOSER_USER` | 239–278| User String | **COUPLED** | Dòng 274 cấm dùng tên biến kỹ thuật nhưng lại ghi rõ: *(như fare_amount, trip_distance, payment_type)*. |
| 5 | `rule_proposer_prompt` | 280–285| ChatPromptTemplate | **HARDCODED TAXI** | Kết hợp 3 mục trên, trở thành prompt mặc định khi `is_taxi` hoặc thiếu contract. |
| 6 | `dashboard_rule_proposer_prompt` | 288–311| ChatPromptTemplate | Generic (English) | Dùng riêng cho chế độ aggregate candidate selector của Dashboard. Không bị dính taxi. |
| 7 | `sql_repair_prompt` | 320–363| ChatPromptTemplate | Generic (SQL) | Tự sửa cú pháp SQL. Độc lập domain, hoạt động tốt. |
| 8 | `dbt_repair_prompt` | 365–393| ChatPromptTemplate | Generic (dbt) | Tự sửa cú pháp dbt schema YAML. Độc lập domain, hoạt động tốt. |
| 9 | `steward_insights_prompt`| 403–468| ChatPromptTemplate | Generic (Advisory)| Báo cáo tổng kết DQ Advisor. Độc lập domain. |
| 10 | `dataset_understanding_prompt` | 474–518| ChatPromptTemplate | **GENERIC (TỐT)** | Dùng trong Node 4 để phân tích Semantic Contract. Hỗ trợ đầy đủ các semantic types tổng quát. |
| 11 | `data_dictionary_generator_prompt` | 521–564| ChatPromptTemplate | **GENERIC (TỐT)** | Dùng trong Node 3 để suy luận Data Dictionary. Thiết kế chuẩn mực, không fix cứng domain. |
| 12 | `_GENERIC_RULE_PROPOSER_SYSTEM` & `generic_rule_proposer_prompt` | 568–616| ChatPromptTemplate | **GENERIC (CHƯA HOÀN THIỆN)** | Đã được viết để thay thế prompt taxi, nhưng **hoàn toàn không có Few-Shot Examples**, khiến chất lượng sinh của LLM không đồng đều như bản taxi prompt. |
| 13 | `prompt_customizer_prompt` | 619–645| ChatPromptTemplate | **GENERIC (TỐT)** | Dùng trong Node 7 để viết System Prompt động theo Semantic Contract. |

---

## 3. KIỂM TRA CHI TIẾT GRAPH 1 TRONG `ARCHITECTURE_DIAGRAM.MD`

Trong `architecture_diagram.md` (Mục 2.3.1), Graph 1 được định nghĩa gồm 9 nodes:

```text
[START]
   │
   ├───────────► (contract confirmed) ───────────┐
   ▼                                             ▼
[raw_profiler] ──► [profiler_digest] ──► [data_dict_gen / dataset_understanding]
                                                         │
                                                         ▼
                                              [dataset_understanding]
                                                         │
                                                         ▼
                                              [hitl_semantic_gate]
                                                         │ (pause if draft)
                                                         ▼
                                            [rule_candidate_builder] ◄───┘
                                                         │
                                                         ▼
                                               [prompt_customizer]
                                                         │
                                                         ▼
                                                  [rule_proposer]
                                                         │
                                                         ▼
                                                   [hitl_gate] ──► [END]
```

### Phân tích vai trò và sự liên kết giữa các Node trong Graph 1:

1. **Node 1 (`raw_profiler`) & Node 2 (`profiler_digest`):**
   - Nhiệm vụ: Đo đạc thống kê (null rate, quantiles, distinct count, min/max, schema constraints) và biến đổi thành các tín hiệu trừu tượng (`signals`: `has_pk_constraint`, `has_extreme_outliers`, `has_negative_values`, `no_nulls`).
   - Đánh giá: **Đã tổng quát.** Không phụ thuộc vào domain taxi.
2. **Node 3 (`data_dictionary_generator`):**
   - Nhiệm vụ: Tự động dùng AI suy luận ra Từ điển dữ liệu (tên tiếng Việt, ý nghĩa, semantic type, governance notes) cho bảng nếu người dùng không cung cấp.
   - Đánh giá: **Đã tổng quát.**
3. **Node 4 (`dataset_understanding`):**
   - Nhiệm vụ: Tạo `TableSemanticContract` (phân loại cột thành `identifier`, `currency`, `timestamp`, `category`, `numeric`, `PII` và quan hệ `relationships`).
   - Đánh giá: **Đã tổng quát.**
4. **Node 5 (`hitl_semantic_gate`):**
   - Nhiệm vụ: Tạm dừng để Data Steward kiểm tra Hợp đồng ngữ nghĩa.
   - Đánh giá: **Đã tổng quát.**
5. **Node 6 (`rule_candidate_builder`):**
   - Nhiệm vụ: Dựa vào Semantic Contract đã duyệt để sinh các candidate rules deterministic (NOT_NULL cho identifier, RANGE [p5, p95] cho numeric/currency, ACCEPTED_VALUES cho category).
   - Đánh giá: **Đã tổng quát.**
6. **Node 7 (`prompt_customizer`):**
   - Nhiệm vụ: Dùng LLM đọc Semantic Contract của bảng để sinh ra một System Prompt chuyên biệt (Custom System Prompt) phản ánh đúng domain của bảng (ví dụ: bảng `e_commerce_orders`, bảng `patient_records`).
   - Đánh giá: **Đã tổng quát.**
7. **Node 8 (`rule_proposer`):**
   - Nhiệm vụ: LLM chọn lọc candidate, gán evidence, viết lập luận `ai_reasoning` có số liệu thực tế, và viết `business_rationale` thuần tiếng Việt.
   - **VẤN ĐỀ NGHIÊM TRỌNG TẠI NODE 8:**
     - Node 8 là nơi phá vỡ tính tổng quát của toàn bộ Graph 1!
     - Trong `rule_proposer_node.py` (dòng 617–651), Node 8 kiểm tra `if is_taxi: messages = rule_proposer_prompt... else: messages = generic_rule_proposer_prompt...`.
     - Sau đó, Node 8 lấy System Prompt tĩnh (vốn khai báo Taxi) và ghép thêm: `\n\n=== NODE 7 DOMAIN CONTEXT ===\n` + `specialized_system_prompt`.
     - Đồng thời, Node 8 truyền biến `domain_context = DOMAIN_CONTEXT` (bị fix cứng taxi) và `data_dictionary = _load_data_dictionary()` (file JSON taxi fix cứng trên ổ đĩa).
8. **Node 9 (`hitl_gate`):**
   - Nhiệm vụ: Lưu proposed rules vào DB với trạng thái PENDING và xuất trace JSON.
   - Đánh giá: **Đã tổng quát.**

---

## 4. HƯỚNG SỬA ĐỔI CHI TIẾT & TOÀN DIỆN (ACTIONABLE REFACTORING PLAN)

Để hệ thống hoàn toàn thích ứng với **MỌI DATASET** mà không làm mất đi độ chi tiết, chặt chẽ và các ràng buộc nghiệp vụ tiếng Việt khắt khe, cần tiến hành tái cấu trúc theo 3 phần sau:

### PHẦN A: SỬA ĐỔI `SRC/AGENTS/NODES/TEMPLATES.PY`

#### 1. Loại bỏ Prompt chết
- Đánh dấu hoặc loại bỏ `profiler_node_prompt` (vì Profiler chạy thuần code thống kê).

#### 2. Viết lại `_RULE_PROPOSER_SYSTEM` thành Domain-Agnostic (Không phụ thuộc Taxi)
Thay đổi câu mở đầu từ:
```python
# CŨ (BỊ KHÓA VÀO TAXI):
_RULE_PROPOSER_SYSTEM = """\
Bạn là chuyên gia Data Quality (DQ) cho hệ thống vận tải taxi (NYC Yellow Taxi Trip Records). Nhiệm vụ của bạn là \
đề xuất các quy tắc kiểm tra chất lượng dữ liệu cho MỘT bảng dựa trên digest profile \
và Từ điển dữ liệu (Data Dictionary) được cung cấp.
...
"""
```
thành:
```python
# MỚI (TỔNG QUÁT CHO MỌI DATASET):
_RULE_PROPOSER_SYSTEM = """\
Bạn là chuyên gia Quản trị và Kiểm định Chất lượng Dữ liệu (Data Quality & Governance Specialist). \
Nhiệm vụ của bạn là phân tích sâu cấu trúc dữ liệu, hồ sơ thống kê (Profile Digest), Từ điển dữ liệu (Data Dictionary) \
và Hợp đồng ngữ nghĩa (Semantic Contract) để đề xuất các quy tắc kiểm tra chất lượng dữ liệu (Data Quality Rules) tối ưu cho MỘT bảng dữ liệu cụ thể.
...
"""
```
*(Giữ nguyên 100% phần định nghĩa rule_type, ý nghĩa signals, phân loại dimension, và các nguyên tắc khắt khe về ngôn ngữ tiếng Việt).*

#### 3. Chuẩn hóa `_RULE_PROPOSER_FEW_SHOT` thành Đa Miền (Multi-Domain Examples)
Thay 5 ví dụ taxi bằng các ví dụ tiêu biểu đại diện cho các miền dữ liệu chuẩn (Thương mại điện tử / Giao dịch / Vận hành) để LLM học được cách áp dụng cho mọi bảng:

1. **Ví dụ 1 (NOT_NULL & PRIMARY KEY):** Áp dụng cho cột `order_id` (hoặc `transaction_id`).
2. **Ví dụ 2 (NULL_RATE với Cảnh báo Ngưỡng):** Áp dụng cho cột `customer_phone` (hoặc `delivery_note`) với tỷ lệ khuyết thiếu thực tế.
3. **Ví dụ 3 (ROW_COUNT cấp bảng):** Áp dụng cho bảng `daily_transactions` dựa trên 80% baseline.
4. **Ví dụ 4 (RANGE cho Cột Số / Tiền tệ):** Áp dụng cho cột `order_total_amount` (hoặc `unit_price`) mở rộng 10% từ typical_range [p5, p95].
5. **Ví dụ 5 (CROSS_FIELD_COMPARISON ràng buộc 2 cột):** Áp dụng cho cặp cột `created_at <= completed_at` (hoặc `start_time <= end_time`).

#### 4. Sửa `_RULE_PROPOSER_USER`
Thay dòng 274:
```python
# CŨ:
"TUYỆT ĐỐI CẤM dùng tên biến kỹ thuật (như fare_amount, trip_distance, payment_type)"

# MỚI:
"TUYỆT ĐỐI CẤM dùng tên biến kỹ thuật (ví dụ: không dùng order_id, total_amount, created_at mà phải dùng 'Mã đơn hàng', 'Tổng tiền', 'Ngày tạo')."
```

#### 5. Hợp nhất `rule_proposer_prompt` và `generic_rule_proposer_prompt`
- Không duy trì 2 prompt riêng biệt gây phân mảnh. Chỉ duy trì 1 bộ `rule_proposer_prompt` duy nhất có đầy đủ System Instructions, Domain-Agnostic Few-Shots, và nhận dynamic domain context.

---

### PHẦN B: SỬA ĐỔI `SRC/AGENTS/NODES/RULE_PROPOSER_NODE.PY`

1. **Xóa bỏ các hằng số tĩnh ở mức module:**
   - Xóa `DOMAIN_CONTEXT = """Hệ thống quản lý dữ liệu vận tải taxi..."""`
   - Xóa `DATA_DICTIONARY_PATH = Path(...) / "data_dictionary_trip_records_yellow.json"`
2. **Truyền Dynamic Context từ State:**
   - `domain_context` lấy từ `state.get("metadata", {}).get("domain_hint")` hoặc mô tả trong `semantic_contract.get("table_purpose")`.
   - `data_dictionary` lấy từ `state.get("normalized_data_dictionary")` (do Node 3 sinh hoặc user nạp).
3. **Xóa bỏ cờ rẽ nhánh `is_taxi`:**
   - Loại bỏ hoàn toàn dòng `is_taxi = dataset_id.lower().startswith("nyc-yellow")...`. Mọi dataset đều đi qua cùng một pipeline thống nhất.
4. **Tích hợp đúng đắn `specialized_system_prompt` từ Node 7:**
   - Thay vì nối thô thiển vào đuôi của một prompt taxi cũ, System Message sẽ là sự kết hợp: `[Core DQ Rules & Principles] + [Node 7 Specialized Table Domain Prompt]`.
5. **Khử Hardcode trong `_build_coverage_requirements()`:**
   - Xóa bỏ các điều kiện fix cứng: `name in {"vendor_id", "rate_code_id", "payment_type"}` và `name not in {"congestion_surcharge", "airport_fee", ...}`.
   - Thay bằng logic dựa 100% trên `role` (`id`, `datetime`, `categorical`, `numeric`) và `signals` từ profile digest.

---

### PHẦN C: ĐỒNG BỘ `ARCHITECTURE_DIAGRAM.MD`

- Cập nhật mục **0.1 & 2.3.1** trong `architecture_diagram.md` để khẳng định: Graph 1 là **Universal Data Quality Proposal Graph**, trong đó:
  - Node 3 + Node 4 trích xuất ngữ nghĩa động cho bất kỳ schema nào.
  - Node 7 tự động sinh System Prompt nghiệp vụ cho từng bảng.
  - Node 8 áp dụng System Prompt động kết hợp bộ quy chuẩn DQ tổng quát để sinh rules.

---

## 5. BẢNG TỔNG KẾT SO SÁNH TRƯỚC VÀ SAU CẢI TIẾN

| Hạng mục | Trước khi sửa (Hiện tại) | Sau khi sửa (Đề xuất cải tiến) |
| :--- | :--- | :--- |
| **Phạm vi Dataset** | Bị khóa cứng vào NYC Yellow Taxi (chạy dataset khác dễ bị lệch prompt). | Thích ứng 100% với **mọi Dataset** (E-commerce, FinTech, Healthcare, Logistics...). |
| **System Prompt Rule Proposer** | Hardcode vai trò *"Chuyên gia vận tải taxi NYC"*. | Trở thành *"Chuyên gia Data Quality & Governance tổng quát"*. |
| **Domain Context** | Hardcode chuỗi mô tả NYC Taxi trong code Python. | Nhận động từ `domain_hint`, `data_dictionary` và `semantic_contract`. |
| **Few-Shot Examples** | 5 ví dụ toàn bộ là cột taxi (`tpep_pickup_datetime`, `fare_amount`). | 5 ví dụ chuẩn mực đa miền (`order_id`, `created_at`, `amount`, `status`). |
| **Sự phối hợp Node 7 & 8** | Nối chuỗi bị xung đột vai trò (Taxi vs Custom Domain). | Tích hợp phân tầng mượt mà: Core Principles + Domain Specifics. |
| **Độ tin cậy & Clean Code** | Tồn tại dead code (`profiler_node_prompt`) và prompt phân mảnh. | Code tinh gọn, 1 single source of truth prompt template. |

---
*Báo cáo được lập phục vụ công tác review kiến trúc và định hướng refactoring cho RidePulse DQ System.*
