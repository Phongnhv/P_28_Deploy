# Kịch Bản Thuyết Trình (Speaker Notes) — RidePulse DQ Tech Deep Dive

> **Đối tượng:** Senior AI Engineer / AI System Architect / Tech Lead  
> **Thời lượng khuyến nghị:** 15 – 20 phút thuyết trình + 10 phút Q&A  
> **File Slide:** `presentation/ridepulse_dq_tech_deepdive.html`

---

## Slide 1: Title & Executive Summary (Thời lượng: ~1.5 phút)

### Key Talking Points:
- "Chào các anh em Senior AI Engineer. Hôm nay tôi muốn chia sẻ về kiến trúc hệ thống **RidePulse DQ** — một nền tảng Autonomous AI Data Quality & Anomaly Intelligence."
- "Trong thực tế dữ liệu dạng bảng (Tabular Data), đội Data Engineering thường mất hàng tuần để khảo sát, viết và bảo trì hàng trăm file test dbt thủ công. Nhưng khi đưa AI Agent vào giải quyết bài toán này, thách thức kỹ thuật lớn nhất không phải là 'prompt gì cho LLM', mà là: **Làm sao để Zero-PII Exposure (không lộ dữ liệu nhạy cảm)**, **Làm sao chống ảo giác tham số (Anti-hallucination)** và **Làm sao để mã thực thi là tất định và an toàn (Deterministic Execution Safety)**."
- "Hệ thống RidePulse DQ giải quyết trọn vẹn bài toán này thông qua việc kết hợp StateGraph của LangGraph, dbt-core, và các thuật toán thống kê Robust."

---

## Slide 2: High-Level Architecture & 3-Graph Separation (Thời lượng: ~2 phút)

### Key Talking Points:
- "Thay vì xây dựng một Monolithic Agent duy nhất, chúng tôi tách hệ thống thành **3 StateGraphs độc lập** có ranh giới chịu lỗi (Failure Boundaries) rõ ràng."
- "**Graph 1 (Proposal):** Khảo sát $\rightarrow$ Hiểu ngữ nghĩa $\rightarrow$ Sinh ứng viên $\rightarrow$ Gọi LLM đề xuất quy tắc $\rightarrow$ Dừng tại chốt chặn HITL để con người duyệt."
- "**Graph 2 (Execution):** Chạy độc lập sau khi có phê duyệt. Sử dụng Template Engine biên dịch tất định sang SQL/dbt YAML và thực thi qua tài khoản Read-Only."
- "**Graph 3 (Anomaly & Hypothesis):** Tính toán thống kê dị thường và gọi LLM chẩn đoán nguyên nhân gốc rễ."
- *Insight kỹ thuật:* "Nếu LLM ở Graph 3 bị timeout hay lỗi quota, kết quả kiểm thử dữ liệu ở Graph 2 vẫn hoàn toàn toàn vẹn và hợp lệ, không bao giờ bị fail oan."

---

## Slide 3: Dual dbt Layers Integration (Thời lượng: ~1.5 phút)

### Key Talking Points:
- "Điểm đặc biệt của RidePulse DQ là sự tích hợp **2 lớp dbt Core** trong cùng một pipeline."
- "**Lớp 1 (Pre-profiling Transformation):** Chạy ngay sau khi ingest dữ liệu thô. Chuyển `trips_raw` thành `stg_trips` và `profile_input`, ép kiểu chuẩn hóa 21 cột để làm contract dữ liệu sạch cho Profiler Agent."
- "**Lớp 2 (Post-HITL Dynamic Test Compiler):** Khi Data Steward duyệt rules trên UI, Agent sẽ biên dịch thành file dbt YAML `generated_dq_tests.yml` lưu lên MinIO S3 và thực thi các câu lệnh kiểm tra vi phạm."

---

## Slide 4: Zero-PII Profiling & Aggregate Digesting (Thời lượng: ~1.5 phút)

### Key Talking Points:
- "Về mặt Governance & Security: Hệ thống tuyệt đối **không gửi bất kỳ dòng dữ liệu thô (raw row) nào sang LLM**."
- "Thay vào đó, tool `db_profiler_tool.py` tính toán các chỉ số nén: tỷ lệ null, giá trị min/max, các phân vị quantiles (p05, p50, p95), số lượng giá trị duy nhất và cross-field metrics."
- "Toàn bộ bảng 50,000 dòng được nén thành một Profile Digest JSON &lt; 2KB. Điều này vừa bảo mật 100% PII, vừa tiết kiệm chi phí token và cho phép scale trên bảng hàng chục triệu dòng."

---

## Slide 5: Graph 1 Deep Dive: Semantic Contract & Guarded Proposer (Thời lượng: ~2 phút)

### Key Talking Points:
- "Đi sâu vào Graph 1: Chúng tôi áp dụng chuỗi 9 nodes."
- "Node `dataset_understanding` suy luận vai trò cột (`id`, `metric`, `categorical_code`, `timestamp`) và tạo ra `TableSemanticContract`."
- "Node `rule_candidate_builder` chạy hoàn toàn bằng **code tất định (Deterministic)** để tạo checklist ứng viên rule từ bằng chứng có sẵn."
- "Node `rule_proposer` gọi GPT-4o-mini với **Pydantic Structured Output** (`extra='forbid'`), ép buộc model chỉ được đề xuất tối đa 5 rules có căn cứ rõ ràng."

---

## Slide 6: Anti-Hallucination & Parameter Provenance (Thời lượng: ~2 phút)

### Key Talking Points:
- "Đây là phần quan trọng nhất về AI Safety: **Parameter Provenance**."
- "Trước đây, nhiều hệ thống AI hay bị ảo giác tham số: tự bịa ra min=0, max=500 hoặc tự parse regex từ đoạn văn do LLM sinh ra."
- "Trong RidePulse DQ, mỗi tham số trong `ProposedRule` bắt buộc phải có `ParameterProvenance` chỉ rõ `source_ref` (ví dụ: `profile.profile_input.passenger_count.p95`). Nếu LLM không cung cấp được nguồn gốc, rule sẽ bị validator từ chối thẳng tay (`REJECTED_BY_VALIDATOR`)."

---

## Slide 7: Human-in-the-Loop (HITL) & Immutable Ruleset Versioning (Thời lượng: ~1.5 phút)

### Key Talking Points:
- "AI chỉ đóng vai trò đề xuất; quyền áp dụng vào production hoàn toàn thuộc về **Data Steward**."
- "Giao diện HITL cho phép Steward thực hiện 3 hành động: `APPROVE`, `EDIT` tham số, hoặc `REJECT`."
- "Cơ chế **Batch Locking & Ruleset Versioning**: Hệ thống không chạy test lẻ tẻ sau từng click duyệt, mà khóa toàn bộ thành một phiên bản `RulesetVersionModel` bất biến có mã băm SHA-256 (`ruleset_hash`) và ghi vết kiểm toán trong `audit_events`."

---

## Slide 8: Graph 2: Deterministic SQL & Read-Only Runner (Thời lượng: ~1.5 phút)

### Key Talking Points:
- "Ở tầng thực thi: Chúng tôi **không cho phép LLM tự viết SQL thực thi tự do** vì rủi ro SQL Injection hoặc câu lệnh phá hoại."
- "Hệ thống sử dụng Template Engine biên dịch rule thành Parameterized SQL."
- "4 Lớp bảo vệ: (1) SELECT-only validator, (2) Tài khoản DB Read-Only `RUNNER_DATABASE_URL`, (3) Statement timeout, (4) Capped Sample IDs (chỉ lấy tối đa 20 ID vi phạm để phân tích)."

---

## Slide 9: Graph 3: Statistical Anomaly Engine (Median / MAD) (Thời lượng: ~2 phút)

### Key Talking Points:
- "Chuyển sang tầng Anomaly Intelligence: Quyết định dị thường được tính toán bằng **Thống kê Robust**, không phụ thuộc vào LLM."
- "Chúng tôi sử dụng công thức **Robust Z-score**: `0.6745 * (Current - Median) / MAD` trên cửa sổ trượt 30 runs lịch sử."
- "Tại sao dùng Median/MAD? Vì nó cực kỳ bền vững trước outliers so với Mean/Std. Chúng tôi cũng xử lý trường hợp đặc biệt $MAD = 0$ khi toàn bộ lịch sử đều có 0% vi phạm."

---

## Slide 10: Root-Cause Hypothesis Agent & Actionable Insights (Thời lượng: ~1.5 phút)

### Key Talking Points:
- "Vậy LLM được dùng ở đâu trong Anomaly Detection? LLM được dùng để **Chẩn đoán Nguyên nhân Gốc rễ (Root-Cause Reasoning)**."
- "Khi hệ thống thống kê phát hiện dị thường (`WATCH` hoặc `ANOMALY`), LLM nhận các tín hiệu (`signals`) và phân loại theo Taxonomy: `PARTIAL_INGESTION`, `LATE_PARTITION`, `SCHEMA_BREAKING_CHANGE`, v.v."
- "LLM bắt buộc phải dùng ngôn ngữ xác suất (*'Khả năng cao...'*) và đưa ra danh sách hành động kiểm tra khắc phục cụ thể cho Data Steward."

---

## Slide 11: Production Engineering & EvalGate 6 Gates Benchmark (Thời lượng: ~1.5 phút)

### Key Talking Points:
- "Hệ thống được kiểm thử qua bộ khung **EvalGate 6 Gates**: AI Quality, Security, Reliability, Governance, Performance, và Data Quality Compliance."
- "Toàn bộ hạ tầng container hóa qua Docker Compose (4 services: db, minio, api, worker) và sẵn sàng deploy serverless trên Google Cloud Run."
- "Hệ thống có Telemetry toàn diện với Phoenix OpenTelemetry và LangSmith."

---

## Slide 12: Key Takeaways & Q&A (Thời lượng: ~1.5 phút)

### Key Talking Points:
- "Tóm lại 4 trụ cột kiến trúc cốt lõi của RidePulse DQ:"
  1. *Separation of Graphs:* Ranh giới trách nhiệm rõ ràng giữa AI suy luận và Mã thực thi.
  2. *Zero-PII & Guarded LLM:* Chỉ nén thống kê, neo chặt nguồn gốc tham số.
  3. *Dual dbt Layer & Deterministic Execution:* An toàn tuyệt đối ở tầng dữ liệu.
  4. *Hybrid Anomaly Intelligence:* Thống kê phát hiện + LLM chẩn đoán nguyên nhân.
- "Cảm ơn các Senior AI Engineers. Mời mọi người cùng thảo luận và đặt câu hỏi!"

---

## Gợi Ý Trả Lời Các Câu Hỏi Kỹ Thuật Thường Gặp (Q&A Strategy)

### Q1: Tại sao không để LLM tự viết câu lệnh SQL kiểm tra trực tiếp mà phải qua Template Engine?
> **Trả lời:** "Để LLM tự sinh SQL tự do trong môi trường Production tiềm ẩn 3 rủi ro lớn: (1) Rủi ro SQL Injection hoặc câu lệnh phá hoại cấu trúc DB; (2) Semantic Drift — câu lệnh SQL có thể bị lệch ý so với quy tắc mà Data Steward đã duyệt; (3) Khó validate và benchmark. Biên dịch qua Template Engine đảm bảo 100% tính tất định, bind parameters an toàn và dễ dàng audit."

### Q2: Khi MAD = 0 (lịch sử hoàn toàn không có lỗi), Robust Z-Score xử lý thế nào để tránh chia cho 0?
> **Trả lời:** "Khi MAD = 0, nếu dùng hằng số cứng gán Z=3.0 thì sai lệch 0.001% và sai lệch 100% sẽ nhận cùng một điểm dị thường. Chúng tôi triển khai cơ chế Fallback Scale: lấy `max(abs(median) * 0.1, 0.005)` làm mẫu số dự phòng. Nhờ đó, điểm dị thường vẫn phản ánh chính xác độ lớn sai lệch so với baseline."

### Q3: Việc tách 3 LangGraph có gây độ trễ (latency) lớn hơn so với chạy 1 graph không?
> **Trả lời:** "Không. Ngược lại, việc tách 3 Graphs giúp tối ưu hóa latency và tài nguyên: Graph 1 chỉ chạy khi có dataset mới hoặc cần sinh luật mới; Graph 2 chạy định kỳ cực nhanh bằng SQL engine; và Graph 3 chỉ kích hoạt khi Graph 2 hoàn tất. Ngoài ra, việc tách rời giúp cách ly lỗi (Failure Domain Isolation) hoàn hảo."
