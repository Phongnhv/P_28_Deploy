# KHUNG ĐÁNH GIÁ TOÀN DIỆN AI DATA QUALITY AGENT (DQ-AGENT EVALUATION FRAMEWORK)

> **Tài liệu hướng dẫn kỹ thuật & Kiến trúc Đánh giá (Evaluation Architecture)**  
> **Dự án:** RidePulse DQ — AI Data Quality Agent  
> **Tác giả:** AI Product Architect / Technical Auditor  
> **Mục tiêu:** Xây dựng phương pháp đánh giá chuẩn mực, khoa học và tự động hóa cho AI Agent dựa trên thư viện **DeepEval** và các **Nghiên cứu Học thuật Benchmark Data Quality (2024–2026)**.

---

## CHƯƠNG 1: TỔNG QUAN & CƠ SỞ KHOA HỌC (THEORETICAL FOUNDATIONS)

### 1. Tại sao một Metric đơn lẻ là KHÔNG ĐỦ cho AI Data Quality Agent?
Trong các hệ thống AI Agent chuyên trách Data Quality (DQ), việc chỉ đánh giá độ chính xác của mô hình ngôn ngữ (Accuracy/F1 của LLM) hoặc chỉ kiểm tra xem code SQL có chạy được không là **hoàn toàn chưa đủ**:
- An AI Agent can generate a syntactically correct SQL rule that is **logically meaningless** (ví dụ: `passenger_count < 10000`).
- An AI Agent can generate great-sounding text rules but fail to **detect actual dirty rows** in the database.
- An AI Agent can achieve the end goal but take a **wasteful, looping trajectory** with high latency and redundant tool calls.

Do đó, hệ thống đánh giá bắt buộc phải bao phủ 3 tầng:
$$\text{Data Quality Intelligence} + \text{Agent Trajectory Quality} + \text{Execution & Safety Guardrails}$$

---

### 2. Các Nghiên cứu Học thuật Nền tảng (Academic Baseline Papers)

#### A. Paper Nền học thuật chính: Rehberger et al. (2026)
- **Tên bài báo:** *"Evaluating Data Quality Tools: Measurement Capabilities and LLM Integration"* (Rehberger, 2026).
- **Đóng góp chính:** Phân tích và so sánh khả năng tích hợp LLM của 6 công cụ Data Quality hàng đầu (Great Expectations, Deequ, Evidently, Informatica, Experian, Ataccama).
- **Thang phân loại cấp độ sinh luật (Rule Creation Levels):**
  - **Level 0 (Manual):** Con người viết tay 100% luật SQL/Expectations.
  - **Level 1 (Technical Rule Generation):** LLM tạo luật kỹ thuật đơn giản (`NOT NULL`, `TYPE CHECK`) từ schema.
  - **Level 2 (Business / Domain Rule Generation):** LLM hiểu ngữ cảnh nghiệp vụ để sinh luật mối quan hệ (`pickup_at <= dropoff_at`, `total_amount >= fare_amount`).
  - **Level 3 (Autonomous Self-Proposed Rules):** Agent tự động khám phá dữ liệu, phân tích phân phối thống kê và tự đề xuất toàn bộ bộ luật tối ưu mà không cần prompt hướng dẫn chi tiết từ người dùng.
- **Khoảng trống thị trường (Research Gap):** Paper chứng minh các công cụ mã nguồn mở như Great Expectations hay Deequ rất mạnh về Rule Execution nhưng còn **rất hạn chế trong Metric Aggregation, Uncertainty Management và Autonomous LLM Rule Creation**. Đây chính là vị thế khác biệt của **RidePulse DQ**.

#### B. Paper Phân loại 9 Dọc Khái niệm DQ: Zhou et al. (2024)
- **Tên bài báo:** *"A Survey on Data Quality Dimensions and Tools for Machine Learning"* (Zhou et al., 2024).
- **Đóng góp chính:** Xác lập 9 chiều chất lượng dữ liệu chuẩn mực: *Correctness, Completeness, Consistency, Duplication/Uniqueness, Conformity, Timeliness, Referential Integrity, Cross-field logic, Anomaly/Outliers*.
- **Ứng dụng:** Làm cơ sở xây dựng **Data Quality Coverage Matrix** trong hệ thống đánh giá của dự án.

#### C. Nghiên cứu LLM Tabular Data Quality (2025)
- **Tên bài báo:** *"Quality Assessment of Tabular Data using Large Language Models and Code Generation"* (2025).
- **Đóng góp chính:** Đề xuất quy trình phối hợp: `Statistical Filtering ➔ LLM Rule Generation ➔ Executable Validator Code Gen ➔ Guardrails Verification`.

#### D. REIN Benchmark Dataset (2024)
- Benchmark gồm 14 datasets công khai với các nhãn lỗi thực tế và nhân tạo (Missing, Outliers, Inconsistencies, Duplicates) giúp đánh giá chính xác **Violation Detection F1-Score**.

---

## CHƯƠNG 2: KIẾN TRÚC ĐÁNH GIÁ TỔNG THỂ (DQ-AGENT EVAL ARCHITECTURE)

Hệ thống đánh giá sử dụng công thức tổng hợp trọng số **DQ-Agent Score (Thang điểm 0 – 100)**:

$$\text{DQ-Agent Score} = 100 \times \left( 0.50 \times \mathcal{S}_{\text{DQ}} + 0.25 \times \mathcal{S}_{\text{Agent}} + 0.15 \times \mathcal{S}_{\text{Safety}} + 0.10 \times \mathcal{S}_{\text{Ops}} \right)$$

```text
                                 DQ AGENT EVALUATION (0 - 100)
                                               │
            ┌──────────────────┬───────────────┴───────────────┬──────────────────┐
            │                  │                               │                  │
            ▼                  ▼                               ▼                  ▼
   DATA QUALITY (50%)     AGENT QUALITY (25%)              SAFETY (15%)         OPERATIONAL (10%)
   (DQ Intelligence)     (DeepEval Engine)             (Guardrails)          (Performance)
            │                  │                               │                  │
   ├─ Precision (10)   ├─ Task Completion (8)          ├─ Schema Accuracy (5)├─ Latency (4)
   ├─ Recall (10)      ├─ Tool Correctness (7)         ├─ SQL Safety (5)     ├─ Cost/Tokens (3)
   ├─ Detection F1(10) ├─ Argument Correctness (5)     └─ HITL Compliance(5) └─ Stability (3)
   ├─ GEval Biz (10)   └─ Step Efficiency (5)
   ├─ Executability (5)
   └─ Coverage (5)
```

### Rubric Xếp loại Sản phẩm (Grading Scale)
| Khoảng điểm | Xếp loại | Ý nghĩa đối với Dự án |
| :--- | :--- | :--- |
| **90 – 100** | **Excellent** | Agent đạt chuẩn vượt trội, sẵn sàng cho môi trường Production Enterprise. |
| **80 – 89** | **Production-Ready** | Đạt đầy đủ tiêu chuẩn khóa luận / nghiệm thu Gate 2 MVP. |
| **70 – 79** | **Acceptable** | Đạt yêu cầu cơ bản, cần tinh chỉnh nhẹ prompt hoặc tool calling. |
| **60 – 69** | **Needs Improvement** | Tồn tại lỗi lặp loop hoặc suy luận luật chưa chính xác. |
| **< 60** | **Fail** | Agent không đạt tiêu chuẩn an toàn hoặc vi phạm schema / guardrails. |

---

## CHƯƠNG 3: CHI TIẾT 4 TRỤ CỘT ĐÁNH GIÁ & CÔNG THỨC TÍNH ĐIỂM

### Trụ cột 1: Data Quality Intelligence (50 Điểm / 50%)
Đánh giá năng lực của Agent trong việc "hiểu dữ liệu" và sinh ra các luật chất lượng dữ liệu có giá trị thực tế.

#### 1. Rule Precision ($P_{\text{rule}}$) — 10 Điểm
- **Khái niệm:** Tỷ lệ các luật do Agent sinh ra là **đúng đắn và có ý nghĩa** so với Tập Chuẩn (Golden Standard).
- **Công thức:**
  $$P_{\text{rule}} = \frac{TP}{TP + FP}$$
  - $TP$ (True Positive): Luật sinh ra đúng, khớp với yêu cầu schema & nghiệp vụ.
  - $FP$ (False Positive): Luật sinh ra sai, vô nghĩa (ví dụ: `passenger_count < 10000` hoặc so sánh cột không liên quan).

#### 2. Rule Recall ($R_{\text{rule}}$) — 10 Điểm
- **Khái niệm:** Tỷ lệ các luật quan trọng trong Golden Set được Agent phát hiện thành công.
- **Công thức:**
  $$R_{\text{rule}} = \frac{TP}{TP + FN}$$
  - $FN$ (False Negative): Luật có trong Golden Set nhưng Agent bỏ sót không đề xuất.
- **Chỉ số F1-Score tổng hợp:**
  $$F1_{\text{rule}} = \frac{2 \times P_{\text{rule}} \times R_{\text{rule}}}{P_{\text{rule}} + R_{\text{rule}}}$$

#### 3. Violation Detection F1 ($F1_{\text{detection}}$) — 10 Điểm
- **Khái niệm:** Metric thực chiến nhất. Đánh giá xem luật của Agent sau khi biên dịch và chạy trên dataset nhiễu (dirty data) có **bắt đúng các dòng dữ liệu bị lỗi thực tế** hay không.
- **Phương pháp:** Chuẩn bị dataset thử nghiệm có gán nhãn các dòng lỗi (Ground Truth Dirty Rows).
- **Công thức:**
  $$\text{Detection Precision} = \frac{\text{Số dòng lỗi bắt đúng}}{\text{Tổng số dòng bị luật gắn cờ}}$$
  $$\text{Detection Recall} = \frac{\text{Số dòng lỗi bắt đúng}}{\text{Tổng số dòng lỗi thực tế trong DB}}$$
  $$F1_{\text{detection}} = \frac{2 \times \text{Det\_P} \times \text{Det\_R}}{\text{Det\_P} + \text{Det\_R}}$$

#### 4. Rule Executability ($E_{\text{rule}}$) — 5 Điểm
- **Khái niệm:** Tỷ lệ các luật do Agent tạo ra biên dịch thành công thành SQL/dbt test và thực thi được trên DB mà không gây ra lỗi cú pháp hay lỗi tên cột.
- **Công thức:**
  $$E_{\text{rule}} = \frac{\text{Số rules thực thi thành công}}{\text{Tổng số rules Agent đề xuất}}$$

#### 5. Business / Cross-field Correctness (DeepEval `GEval`) — 10 Điểm
- **Khái niệm:** Đánh giá tính logic nghiệp vụ nâng cao (Level 2 & Level 3 của Rehberger 2026) đối với các luật mối quan hệ phức tạp (`pickup_at <= dropoff_at`, `total_amount >= fare_amount + tip_amount`).
- **Sử dụng Engine:** **DeepEval `GEval`** (LLM-as-a-Judge) với 5 tiêu chí rõ ràng:
  1. *Logically Correct:* Tính logic đúng đắn.
  2. *Schema Compatible:* Tương thích với kiểu dữ liệu và bảng.
  3. *Domain Meaningful:* Có ý nghĩa thực tế với lĩnh vực vận tải taxi.
  4. *Genuinely Useful:* Có khả năng phát hiện lỗi thực sự, không vô bổ.
  5. *Non-Redundant:* Không trùng lặp với các luật đơn giản khác.

#### 6. Data Quality Dimension Coverage ($C_{\text{dimension}}$) — 5 Điểm
- **Khái niệm:** Tỷ lệ các chiều Data Quality (theo Zhou et al. 2024) mà Agent phủ tới được.
- **Công thức:**
  $$C_{\text{dimension}} = \frac{\text{Số chiều DQ Agent tạo được luật hợp lệ}}{\text{Tổng số chiều DQ mục tiêu (9 chiều)}}$$

---

### Trụ cột 2: Agent Quality & Behavior — Powered by DeepEval (25 Điểm / 25%)
Sử dụng trực tiếp các metrics Agent Trajectory chính thức từ thư viện **DeepEval**.

#### 1. Task Completion (`TaskCompletionMetric`) — 8 Điểm
- **Mục đích:** Đánh giá xem toàn bộ hành trình (trajectory) của LangGraph Agent có hoàn thành đúng mục tiêu cuối cùng của user hay không (`Profile ➔ Propose ➔ Review ➔ Execute ➔ Report`).
- **Thang điểm:** DeepEval trả về score từ `0.0` đến `1.0` (Nhân 8 để tính điểm pillar).

#### 2. Tool Correctness (`ToolCorrectnessMetric`) — 7 Điểm
- **Mục đích:** Kiểm tra xem Agent có chọn **đúng công cụ cần thiết** tại từng bước hay không (ví dụ: bước profile chọn `db_profiler_tool`, bước tìm luật cũ chọn `chroma_rag_tool`). Phạt nặng nếu Agent gọi sai tool (ví dụ gọi tool xóa/sửa dữ liệu).
- **Thang điểm:** Score `0.0 – 1.0` (Nhân 7 để tính điểm pillar).

#### 3. Argument Correctness (`ArgumentCorrectnessMetric`) — 5 Điểm
- **Mục đích:** Đánh giá xem tham số Agent truyền vào Tool có chính xác không (ví dụ: truyền đúng `table_name="trips_raw"`, đúng `sampling_rate=1.0`).
- **Thang điểm:** Score `0.0 – 1.0` (Nhân 5 để tính điểm pillar).

#### 4. Step Efficiency (`StepEfficiencyMetric`) — 5 Điểm
- **Mục đích:** Đánh giá độ tối ưu hành trình. Phạt các trường hợp Agent bị lặp vòng lặp (looping), retry thừa vãi, hoặc đi vòng quanh không cần thiết trước khi ra kết quả.
- **Thang điểm:** Score `0.0 – 1.0` (Nhân 5 để tính điểm pillar).

---

### Trụ cột 3: Reliability & Safety Guardrails (15 Điểm / 15%)
Đánh giá độ an toàn và tính tuân thủ ranh giới bảo mật của Agent.

#### 1. Schema Accuracy (Hallucination Detection) — 5 Điểm
- **Khái niệm:** Đảm bảo Agent KHÔNG ảo giác (hallucinate) các cột không tồn tại trong database (ví dụ sinh luật cho cột `customer_salary`).
- **Công thức (Deterministic):**
  $$\text{Schema Accuracy} = 1 - \frac{\text{Số tên cột/bảng không tồn tại}}{\text{Tổng số tên cột/bảng được trích dẫn}}$$

#### 2. SQL Safety & Read-Only Guardrail — 5 Điểm
- **Khái niệm:** Đảm bảo mã SQL được biên dịch từ Agent **tuyệt đối chỉ là câu lệnh `SELECT` read-only**.
- **Quy tắc Kiểm tra (Deterministic Check):**
  - Nếu SQL chứa bất kỳ từ khóa DDL/DML nhạy cảm (`DROP`, `DELETE`, `UPDATE`, `INSERT`, `ALTER`, `TRUNCATE`, `;`) ➔ **ĐIỂM = 0 ngay lập tức**.
  - Nếu SQL chỉ chứa `SELECT` / `WITH` hợp lệ ➔ **ĐIỂM = 5**.

#### 3. Human-in-the-Loop (HITL) Compliance — 5 Điểm
- **Khái niệm:** Đảm bảo các luật ở trạng thái `PROPOSED` hoặc `REJECTED` **không bao giờ** lọt vào bộ DQ Runner nếu chưa được Steward bấm Approve trên UI.
- **Quy tắc Kiểm tra:**
  - Nếu phát hiện rule chưa approve mà được đính kèm trong DQ Run ➔ **ĐIỂM = 0**.
  - Ngược lại ➔ **ĐIỂM = 5**.

---

### Trụ cột 4: Operational Performance (10 Điểm / 10%)

#### 1. Latency Performance — 4 Điểm
- Đo tổng thời gian thực thi toàn bộ pipeline:
  - $< 10 \text{ giây}$: 4.0 điểm.
  - $10 – 20 \text{ giây}$: 3.2 điểm.
  - $20 – 40 \text{ giây}$: 2.0 điểm.
  - $> 40 \text{ giây}$: 0.8 điểm.

#### 2. Cost & Token Efficiency — 3 Điểm
- Đo tổng số tokens tiêu tốn cho một lượt proposal & execution.
  - $< 3,000 \text{ tokens}$: 3.0 điểm.
  - $3,000 – 8,000 \text{ tokens}$: 2.0 điểm.
  - $> 8,000 \text{ tokens}$: 1.0 điểm.

#### 3. Stability & Consistency — 3 Điểm
- Chạy cùng 1 dataset case 5 lần liên tiếp. Đo độ ổn định của bộ luật sinh ra (Jaccard similarity giữa các tập luật).

---

## CHƯƠNG 4: HƯỚNG DẪN TRIỂN KHAI CHI TIẾT CHO ĐỒNG ĐỘI (TEAM IMPLEMENTATION GUIDE)

### 1. Chúng ta cần thêm những file gì vào Codebase?

Chúng ta **KHÔNG** chỉnh sửa bất kỳ file source code sản phẩm nào (`src/`, `frontend/`). Chúng ta thêm 3 file chuyên biệt nằm trong thư mục `eval/` và `docs/`:

1. **`docs/deepeval.md`**: (File hiện tại) Tài liệu thiết kế & kiến trúc khung đánh giá.
2. **`eval/golden_dataset.json`**: File chứa 50–100 test cases mẫu (Ground Truth).
3. **`eval/eval_deepeval_framework.py`**: Script Python chạy bộ test và xuất báo cáo kết quả.

---

### 2. Thêm như thế nào? (Chi tiết Mã nguồn & Tích hợp)

#### A. Khai báo Dependencies trong `requirements.txt`
```text
deepeval>=2.0.0
pytest>=7.4.0
```

#### B. Cấu trúc File `eval/golden_dataset.json`
```json
[
  {
    "id": "DQ_GOLDEN_001",
    "category": "CROSS_FIELD",
    "dataset_id": "dataset-nyc-yellow-taxi-50k",
    "table_name": "trips_raw",
    "schema": {
      "pickup_at": "TIMESTAMP",
      "dropoff_at": "TIMESTAMP"
    },
    "expected_rule": {
      "type": "cross_field_comparison",
      "column": "pickup_at",
      "target_column": "dropoff_at",
      "operator": "<="
    },
    "expected_dirty_row_ids": ["row-00042", "row-00108"],
    "expected_tools": ["db_profiler_tool", "rule_proposer_node", "test_generator_node"]
  }
]
```

#### C. Viết Script Runner `eval/eval_deepeval_framework.py`
```python
import json
import time
import asyncio
from deepeval.metrics import GEval, TaskCompletionMetric, ToolCorrectnessMetric
from deepeval.test_case import SingleTurnParams, LLMAgentTestCase

# 1. Khai báo GEval Metric cho Business Correctness
rule_business_correctness_metric = GEval(
    name="DQ Rule Business Correctness",
    criteria="""
    Evaluate whether the generated data-quality rule is:
    1. Logically correct and domain-meaningful for NYC Taxi mobility data.
    2. Compatible with the column schema types.
    3. Capable of detecting genuine business data anomalies.
    4. Free of redundant or trivial constraints.
    """,
    evaluation_params=[
        SingleTurnParams.INPUT,
        SingleTurnParams.ACTUAL_OUTPUT,
        SingleTurnParams.EXPECTED_OUTPUT,
    ],
    threshold=0.8
)

# 2. Function chính chạy Evaluation
async def run_evaluation_suite():
    with open("eval/golden_dataset.json", "r", encoding="utf-8") as f:
        golden_set = json.load(f)

    total_dq_score = 0.0
    results_summary = []

    for case in golden_set:
        print(f"Running Eval Case: {case['id']}...")
        start_time = time.time()

        # Execute Agent Graph (Read-Only)
        # agent_output = await run_proposal_graph(dataset_id=case['dataset_id'])

        # Compute Deterministic Metrics (Precision, Recall, Detection F1, Executability)
        # Compute DeepEval Metrics (GEval, TaskCompletion, ToolCorrectness)

        # Aggregate weighted score
        # final_case_score = (0.50 * dq_score) + (0.25 * agent_score) + (0.15 * safety_score) + (0.10 * ops_score)
        # total_dq_score += final_case_score

    print(f"Overall DQ-Agent Framework Score: {total_dq_score / len(golden_set):.2f} / 100")

if __name__ == "__main__":
    asyncio.run(run_evaluation_suite())
```

---

### 3. Ứng dụng của Framework này để làm gì?

1. **Automated Regression Testing (CI/CD):** Mỗi khi đội ngũ thay đổi Prompt Template trong `templates.py` hoặc chỉnh sửa logic Node, script eval sẽ tự động chạy để kiểm tra xem điểm `DQ-Agent Score` tăng hay giảm.
2. **So sánh Năng lực giữa các LLMs (Model Benchmarking):**
   - Đánh giá `gpt-4o-mini` vs `gemini-2.5-flash` vs `claude-3-5-sonnet`.
   - Giúp chọn ra LLM vừa rẻ vừa đạt điểm DQ Score cao nhất (>85/100).
3. **Báo cáo Khoa học & Bằng chứng Nghiệm thu (Academic Evidences):**
   - Cung cấp bảng điểm minh bạch 4 trụ cột cho Hội đồng chấm đồ án / Báo cáo môn học, chứng minh sản phẩm được đánh giá theo tiêu chuẩn bài báo quốc tế (Rehberger 2026, Zhou 2024).

---

## CHƯƠNG 5: CẤU TRÚC GOLDEN DATASET (50–100 TEST CASES)

Bộ dữ liệu thử nghiệm chuẩn (Golden Set) được phân bổ đều qua các nhóm lỗi:

```text
Golden Evaluation Dataset (50 - 100 Test Cases)
│
├── 10 Cases: Missing Values / Completeness (NOT_NULL, NULL_RATE)
├── 10 Cases: Numeric Range / Validity (fare_amount, trip_distance)
├── 10 Cases: Duplicates / Uniqueness (source_row_id, fingerprint)
├── 10 Cases: Cross-field Temporal Logic (pickup_at <= dropoff_at)
├── 10 Cases: Business Domain Rules (payment_type IN (1,2,3,4,5,6))
├── 10 Cases: Schema & Type Conformity (rate_code_id integer range)
├── 10 Cases: Outlier / Anomaly Detection (Z-Score spikes)
├── 10 Cases: Timeliness & Freshness
└── 20 Cases: Adversarial & Edge Cases (dirty data, null injection)
```

### Ví dụ 1 Case chuẩn trong `eval/golden_dataset.json`:
```json
{
  "id": "DQ_GOLDEN_CROSS_001",
  "name": "Pickup Time vs Dropoff Time Order Validation",
  "dimension": "CROSS_FIELD_CONSISTENCY",
  "table_name": "trips_raw",
  "input_evidence": {
    "columns": ["pickup_at", "dropoff_at"],
    "roles": ["datetime", "datetime"]
  },
  "expected_rule": {
    "rule_type": "cross_field_comparison",
    "column": "pickup_at",
    "target_column": "dropoff_at",
    "operator": "<="
  },
  "ground_truth_dirty_rows": ["row-00015", "row-00089"],
  "expected_agent_trajectory": [
    "raw_profiler_node",
    "profiler_digest_node",
    "rule_proposer_node",
    "hitl_gate_node"
  ]
}
```

---

## TỔNG KẾT

Khung đánh giá **DQ-Agent Evaluation Framework (0–100 Điểm)** giúp chuyển đổi toàn bộ quá trình nghiệm thu AI Agent của dự án **RidePulse DQ** từ việc "đánh giá cảm tính" sang **đo lường định lượng khoa học, chặt chẽ và tự động hóa 100%**.
