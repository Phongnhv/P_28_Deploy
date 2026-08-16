# Kế hoạch Đánh giá Chất lượng Agent & Ứng dụng (Eval Evidences Plan)

Tài liệu này hướng dẫn chi tiết cách thiết lập công cụ giám sát AI (Observability), quy trình chạy đánh giá và ghi nhận bằng chứng chất lượng (Eval Evidences) phục vụ cho đợt nghiệm thu Gate 2 MVP của dự án **RidePulse DQ**.

---

## 1. Mục tiêu & Các Chỉ số Đánh giá (Metrics)

Để đạt tiêu chí nghiệm thu Gate 2, hệ thống cần được đánh giá dựa trên các chỉ số sau:

| Chỉ số                                       | Target (Mục tiêu)                                                                                                                           | Phương pháp kiểm tra                                                                                |
| :--------------------------------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------- | :------------------------------------------------------------------------------------------------------ |
| **Response Accuracy** (Độ chính xác) | **> 80%** đề xuất rule hợp lý và chạy thành công trên 5 loại rule (E1–E5).                                                  | Đối chiếu kết quả chạy rule thực tế với dataset mẫu chứa lỗi nhân tạo (1,250 dòng lỗi). |
| **dbt YAML Validation & Repair Rate**    | **100%** tệp dbt test YAML generated/repaired phải biên dịch thành công.                                                          | Tự động chạy validate YAML/dbt parse và LLM repair (tối đa 3 lượt) trong Execution Graph.      |
| **Response Latency** (Độ trễ)         | **< 3s** đối với các API thông thường; các tác vụ nặng (dbt run, rule proposer) chạy bất đồng bộ thông qua Job Runner. | Đo thời gian phản hồi qua API logs hoặc tracing dashboard.                                         |
| **Data Safety & Governance**             | **100%** không rò rỉ dữ liệu nhạy cảm (không gửi raw rows cho frontend/LLM).                                                   | Kiểm tra output của Agent (chỉ nhận thông tin profile aggregate và đếm số lỗi).               |

---

## 2. Thiết lập AI Observability (AI Log & Tracing)

Việc hiển thị quá trình "suy nghĩ" (Trace) của Agent qua LangGraph sẽ là điểm cộng cực kỳ lớn cho MVP. Bạn có thể chọn 1 trong các giải pháp dưới đây:

### Lựa chọn A: Phoenix by Arize (Khuyên dùng - Chạy Local, Miễn phí, Không giới hạn)

Phoenix là thư viện open-source giúp visualize quá trình LLM suy nghĩ, gọi tool, chạy các node trong LangGraph hoàn toàn tại local.

#### Bước 1: Cài đặt thư viện cần thiết

```bash
pip install arize-phoenix openinference-instrumentation-langchain opentelemetry-sdk opentelemetry-exporter-otlp
```

#### Bước 2: Khởi chạy Phoenix Server tại local

Mở một terminal mới và chạy:

```bash
phoenix start
```

*Mặc định dashboard sẽ chạy tại địa chỉ: `http://localhost:6006`*

#### Bước 3: Tích hợp Code Trace vào dự án

Để tự động gửi toàn bộ trace của LangGraph/LangChain sang Phoenix local, thêm đoạn code sau vào đầu file chạy ứng dụng (ví dụ `src/main.py` hoặc `src/local_worker_api.py`):

```python
import phoenix as px
from openinference.instrumentation.langchain import LangChainInstrumentor
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

# 1. Khởi tạo Tracer Provider hướng tới Phoenix
tracer_provider = TracerProvider()
tracer_provider.add_span_processor(
    SimpleSpanProcessor(OTLPSpanExporter(endpoint="http://localhost:6006/v1/traces"))
)
trace.set_tracer_provider(tracer_provider)

# 2. Kích hoạt LangChain/LangGraph Instrumentation
LangChainInstrumentor().instrument()
```

---

### Lựa chọn B: Langfuse (Giao diện đẹp, Cloud hoặc Docker Self-hosted)

Langfuse hỗ trợ lưu trữ logs và traces rất trực quan. Cách cài đặt nhanh nhất bằng Cloud:

1. Đăng ký tài khoản tại [Langfuse Cloud](https://cloud.langfuse.com).
2. Tạo project mới và lấy bộ API Keys (`Public Key`, `Secret Key`, `Host`).
3. Thêm các biến môi trường sau vào file `.env`:
   ```env
   LANGFUSE_PUBLIC_KEY="pk-lf-..."
   LANGFUSE_SECRET_KEY="sk-lf-..."
   LANGFUSE_HOST="https://cloud.langfuse.com"
   ```
4. SDK của LangGraph sẽ tự động phát hiện các biến này để gửi trace nếu sử dụng callback của Langchain/Langfuse.

---

### Lựa chọn C: LangSmith (Tích hợp sâu nhất với LangGraph)

1. Đăng ký tài khoản tại [LangSmith](https://smith.langchain.com).
2. Tạo API Key và thêm vào `.env`:
   ```env
   LANGCHAIN_TRACING_V2="true"
   LANGCHAIN_API_KEY="lsv2_pt_..."
   LANGCHAIN_PROJECT="ridepulse-dq"
   ```

---

## 3. Quy trình thực hiện đánh giá bằng CLI & API (CLI Evaluation Workflow)

Sau khi cấu hình Tracing Tool, hãy tiến hành chạy test case hoàn toàn qua giao diện dòng lệnh (CLI) và các API endpoints (Sử dụng `curl`).

### Bước 1: Khởi động Tracing Tool

Đảm bảo Phoenix Server đã hoạt động và sẵn sàng đón nhận traces:

```bash
phoenix start
# Truy cập UI tại http://localhost:6006
```

### Bước 2: Kích hoạt Run 1 - Khảo sát và Đề xuất Rules (Proposal Graph)

Chạy command line trực tiếp qua container worker để kích hoạt pha 1:

```bash
docker compose exec worker python -m src.agents.graph proposal dataset-nyc-yellow-taxi-50k
```

*Lưu ý: Output của lệnh sẽ in ra thông tin chạy đề xuất kèm mã `run_id` (ví dụ: `c4d9089e7f8249188c548b0556a6125c`). Hãy lưu lại `run_id` này.*

### Bước 3: Duyệt Rules mẫu (HITL Review) bằng API

Để duyệt (Approve) hoặc từ chối các quy tắc đã đề xuất mà không cần UI, ta gọi API bằng `curl`.

1. **Lấy danh sách các rule đã đề xuất từ `run_id` để lấy `rule_id`**:

```bash
curl -X GET http://localhost:8000/api/v1/dq/runs/<YOUR_RUN_ID>/rules
```

2. **Duyệt hàng loạt (Bulk Review) các rule mong muốn** (Ví dụ duyệt các rule E1–E5):

```bash
curl -X POST http://localhost:8000/api/v1/dq/runs/<YOUR_RUN_ID>/rules/bulk-review \
  -H "Content-Type: application/json" \
  -d '{
    "decisions": [
      {
        "rule_id": "rule_id_e1",
        "status": "APPROVED",
        "reviewer": "Steward-CLI"
      },
      {
        "rule_id": "rule_id_e2",
        "status": "APPROVED",
        "reviewer": "Steward-CLI"
      }
    ]
  }'
```

3. **Xuất bản (Publish/Active) các rules đã approved**:

```bash
curl -X POST http://localhost:8000/api/v1/dq/runs/<YOUR_RUN_ID>/publish
```

### Bước 4: Kích hoạt Run 2 - Chạy Test chất lượng (Execution Graph)

Thực thi các rules đã được active bằng cách gửi yêu cầu chạy test qua API:

```bash
curl -X POST http://localhost:8000/api/v1/dq/runs/<YOUR_RUN_ID>/execute-tests
```

*API sẽ phản hồi ngay lập tức và trả về một mã `test_run_id` (Ví dụ: `{"test_run_id": "aea9ab58b1dc42a698b82f66ba959ff6", "status": "QUEUED"}`).*

### Bước 5: Kiểm tra trạng thái và kết quả chạy test

Theo dõi tiến trình chạy test thông qua `test_run_id`:

```bash
curl -X GET http://localhost:8000/api/v1/dq/test-runs/<YOUR_TEST_RUN_ID>
```

*Kiểm tra kết quả xem có bao nhiêu test passed, failed, điểm số DQ là bao nhiêu và file báo cáo chi tiết.*

### Bước 6: Thu thập bằng chứng đánh giá

- Truy cập vào dashboard Phoenix (`http://localhost:6006`).
- Chụp ảnh màn hình chi tiết trace của các node chính trong Run 1 (`rule_proposer`) và Run 2 (`validate_dbt_project`, `llm_dbt_repair`, `test_runner`).
- Copy link trace và nội dung tệp dbt test YAML đã sinh ra/sửa đổi để ghi vào [EVAL_EVIDENCES.md](file:///d:/ai_thuc_chien/P-028/docs/EVAL_EVIDENCES.md).
