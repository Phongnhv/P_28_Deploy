# Implementation Plan: DQ-Agent Evaluation Framework (0–100 Points) with DeepEval & Academic Benchmarks

Tài liệu này lập kế hoạch chi tiết để tích hợp **DQ-Agent Evaluation Framework** (thang điểm 100) cho hệ thống **RidePulse DQ**, dựa trên sự kết hợp giữa **DeepEval Engine** (chấm Agent Trajectory & Behavior) và **Data Quality Academic Benchmarks** (Rehberger et al. 2026, Zhou et al. 2024, REIN Benchmark).

---

## 1. User Review Required

> [!IMPORTANT]
> **Nguyên tắc Read-Only:** Tuyệt đối giữ nguyên toàn bộ source code hiện tại của dự án (`src/`, `frontend/`, v.v.). Kế hoạch này tập trung xây dựng module đánh giá độc lập (`eval/`) và tài liệu kiến trúc đánh giá (`docs/deepeval.md`).

> [!TIP]
> Framework phân bổ 100 điểm thành **4 Trụ cột cốt lõi**:
> - **50% — Data Quality Intelligence** (Rule Precision, Recall, Detection F1, Executability, GEval Business Correctness, Dimension Coverage).
> - **25% — Agent Behavior Quality** (DeepEval Task Completion, Tool Correctness, Argument Correctness, Step Efficiency).
> - **15% — Reliability & Guardrails** (Schema Accuracy, SQL Safety Read-Only, HITL Compliance).
> - **10% — Operational Performance** (Latency, Cost/Tokens, Stability variance across 5 runs).

---

## 2. Theoretical & Framework Foundations

### Academic Literature Grounding
1. **Rehberger et al. (2026)** — *"Evaluating Data Quality Tools: Measurement Capabilities and LLM Integration"*:
   - Đánh giá năng lực của Great Expectations, Deequ, Evidently, Informatica, Experian, Ataccama.
   - Thang năng lực Rule Creation with LLM: Level 1 (Technical rule), Level 2 (Business/Domain rule), Level 3 (Autonomous self-propose rules).
   - Chỉ ra khoảng trống của Open-source tools trong Metric Aggregation, Uncertainty và LLM Rule Generation -> Cơ sở khẳng định vị thế của RidePulse DQ.
2. **Zhou et al. (2024)** — *"A Survey on Data Quality Dimensions and Tools for Machine Learning"*:
   - Phân loại chuẩn 9 chiều Data Quality (Correctness, Completeness, Consistency, Duplication, Conformity, Timeliness, Referential Integrity, Cross-field, Anomaly).
3. **Tabular Data LLM Code Gen (2025)** — *"Quality Assessment of Tabular Data using LLMs and Code Generation"*:
   - Mô hình Statistical filtering + LLM rules + LLM validators + RAG + Guardrails.
4. **REIN Benchmark (2024)** — Benchmark đánh giá đốm lỗi thực tế trên dataset nhiễu (dirty) vs sạch (clean).

---

## 3. Proposed Changes & Document Structure

### [Component 1] Framework Documentation

#### [NEW] [docs/deepeval.md](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/docs/deepeval.md)
Tài liệu hướng dẫn chi tiết cho đồng đội, giải thích:
- Cơ sở lý thuyết & các Paper nghiên cứu.
- Công thức tính điểm 0–100 cho 4 Trụ cột.
- Cách tích hợp DeepEval (`GEval`, `TaskCompletionMetric`, `ToolCorrectnessMetric`, `ArgumentCorrectnessMetric`, `StepEfficiencyMetric`).
- Quy trình tạo **Golden Dataset (50–100 Test Cases)** với dữ liệu dirty vs clean.
- Cách chạy lệnh đánh giá regression tự động trong CI/CD.

---

### [Component 2] Evaluation Engine Integration

#### [NEW] [eval/eval_deepeval_framework.py](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/eval/eval_deepeval_framework.py)
Script Python độc lập thực thi việc chấm điểm:
1. Đọc Golden Dataset từ `eval/golden_dataset.json`.
2. Kích hoạt LangGraph Agent (`run_proposal_graph` & `run_execution_graph`).
3. Thu thập Agent Trajectory & Tool Call Logs.
4. Chạy **Deterministic Evaluators** (Precision, Recall, Detection F1, Executability, Schema Accuracy, Read-Only SQL Safety, HITL Compliance, Latency).
5. Chạy **DeepEval Engine** (GEval Business Correctness, Task Completion, Tool Correctness, Argument Correctness, Step Efficiency).
6. Tổng hợp điểm thành **DQ-Agent Score (0–100)** và xuất báo cáo JSON/Markdown tại `eval/results/`.

#### [NEW] [eval/golden_dataset.json](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/eval/golden_dataset.json)
Tập dữ liệu chuẩn chứa 50–100 kịch bản test với các loại lỗi:
- Completeness, Range/Validity, Uniqueness/Duplicate, Cross-field, Business Logic, Schema/Type, Anomaly, Freshness, Adversarial Edge cases.

---

## 4. Verification Plan

### Automated Tests
- Chạy script đánh giá mẫu:
  ```bash
  python eval/eval_deepeval_framework.py --dataset data/yellow_tripdata_2025_semantic_50k.csv --output eval/results/eval_report_v1.json
  ```
- Kiểm tra tính toán điểm số tổng quát (DQ-Agent Score) đảm bảo nằm trong khoảng 0-100 điểm.
- Đảm bảo script eval không can thiệp hay sửa đổi source code chính của dự án.

### Manual Verification
- Review tài liệu `docs/deepeval.md` đảm bảo trình bày chuyên nghiệp, dễ hiểu cho cả AI/ML Engineers và Data Stewards.
