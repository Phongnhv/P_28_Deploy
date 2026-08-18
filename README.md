# RidePulse DQ - AI Data Quality Agent

## 1. Project Overview
RidePulse DQ là một hệ thống AI Agent chuyên trách đánh giá và kiểm soát chất lượng dữ liệu (Data Quality) tự động. Hệ thống có khả năng tự động phân tích dữ liệu (profiling), đề xuất các luật kiểm tra (Data Quality Rules) thông qua suy luận của LLM, và thực thi các bộ test sinh ra một cách độc lập để phát hiện các điểm dị thường (anomalies) trong dữ liệu.

## 2. Architecture & Data Flow

```mermaid
graph TD
    subgraph Client_Layer["Client / UI Layer"]
        UI[React / Vite Web App]
        CLI[CLI Runner]
    end

    subgraph Backend_Layer["Backend / API Layer"]
        API[FastAPI Gateway]
        Worker[Local Worker API]
    end

    subgraph Agent_Layer["Agent Logic & Orchestration"]
        LG[LangGraph StateGraph]
        Run1[Proposal Graph: Profiler, Proposer, HITL]
        Run2[Execution Graph: Test Gen, Validate, Runner, Anomaly]
        LG --> Run1
        LG --> Run2
    end

    subgraph LLM_Layer["External AI & LLMs"]
        LLM[OpenAI / Anthropic / Google GenAI / MistralAI]
    end

    subgraph Data_Layer["Data & Storage Layer"]
        DB[(PostgreSQL / SQLite)]
        VectorDB[(ChromaDB)]
        MinIO[(MinIO Object Storage)]
    end

    UI -- "HTTP/REST" --> API
    CLI -- "Execute" --> LG
    API --> LG
    Run1 -- "Predict / Repair" --> LLM
    Run2 -- "Predict / Repair" --> LLM
    LG -- "Query / Persist" --> DB
    LG -- "Similarity / Store" --> VectorDB
    Worker -- "Access" --> DB
```

> 📖 **Gate 2 MVP Documentation:** Xem tài liệu Gate 2 MVP tại [`docs/gate2-mvp`](docs/DATABASE_ARCHITECTURE.md), chi tiết Kiến trúc CSDL PostgreSQL tại [`docs/DATABASE_ARCHITECTURE.md`](docs/DATABASE_ARCHITECTURE.md) và Báo cáo Nghiệm thu 5 kịch bản E1-E5 tại [`eval/results/E1_E5_EVALUATION.md`](eval/results/E1_E5_EVALUATION.md).



### Data Flow
- **Flow 1 (Proposal):** `User/CLI -> Backend -> Proposal Graph (LangGraph) -> Profiler -> LLM (Propose Rules) -> HITL Gate -> PostgreSQL -> User`
- **Flow 2 (Execution):** `User/CLI -> Backend -> Execution Graph (LangGraph) -> Generate Tests via LLM -> Validate SQL -> Run on Data -> Detect Anomalies -> Save Report -> User`

## 3. Tech Stack
- **Frontend / UI:** React, Vite, TypeScript
- **Backend / API Gateway:** FastAPI, Uvicorn, Python 3.11+
- **Agent Framework:** LangChain, LangGraph
- **AI / LLMs:** OpenAI, Anthropic, MistralAI, Google GenAI
- **Database & Storage:** PostgreSQL, SQLite (Dev), MinIO, SQLAlchemy, Alembic
- **Vector Database:** ChromaDB
- **Infrastructure:** Docker, Docker Compose

## 4. Environment Variables

Hệ thống yêu cầu các biến môi trường sau. Tạo file `.env` bằng cách sao chép từ `.env.example` và điền đầy đủ giá trị.

### LLM Configuration

| Variable | Required | Description | Example / Placeholder |
|---|---|---|---|
| `OPENAI_API_KEY` | ✅ (nếu dùng OpenAI) | API Key cho OpenAI LLMs | `sk-your-openai-api-key-here` |
| `ANTHROPIC_API_KEY` | ⚪ (nếu dùng Anthropic) | API Key cho Claude | `sk-ant-your-key-here` |
| `MISTRAL_API_KEY` | ⚪ (nếu dùng Mistral) | API Key cho MistralAI | `your-mistral-key-here` |
| `GOOGLE_API_KEY` | ⚪ (nếu dùng Google) | API Key cho Google GenAI | `your-google-key-here` |
| `PROVIDER` | ⚪ | LLM provider đang dùng (`openai`/`anthropic`/`mistral`/`google`) | `openai` |
| `AGENT_MODE` | ⚪ | Chế độ chạy agent (`mock` để test, `graph` để chạy thật) | `graph` |

### Database & Storage

| Variable | Required | Description | Example / Placeholder |
|---|---|---|---|
| `DATABASE_URL` | ✅ | Kết nối Database chính (PostgreSQL hoặc SQLite) | `sqlite:///steward_local.db` |
| `RUNNER_DATABASE_URL` | ⚪ | Kết nối DB cho Worker (tách biệt quyền truy cập) | `sqlite:///steward_local.db` |
| `SUPABASE_DATABASE_URL` | ⚪ | URL Supabase DB (môi trường production) | `postgresql://user:pass@db.supabase.co:5432/postgres` |
| `DQ_EXECUTION_BACKEND` | ⚪ | Backend thực thi DQ (`auto`/`local`/`supabase`) | `auto` |
| `MINIO_URL` | ⚪ | Endpoint MinIO Object Storage | `http://localhost:9000` |
| `MINIO_ACCESS_KEY` | ⚪ | Access key cho MinIO | `minioadmin` |
| `MINIO_SECRET_KEY` | ⚪ | Secret key cho MinIO | `miniopassword` |

### App & Networking

| Variable | Required | Description | Example / Placeholder |
|---|---|---|---|
| `APP_ENV` | ✅ | Môi trường triển khai (`local`/`development`/`production`) | `local` |
| `FRONTEND_ORIGIN` | ✅ | Danh sách domain cho phép CORS (phân tách bằng dấu phẩy) | `http://localhost:3000,http://localhost:5173` |
| `LOCAL_WORKER_URL` | ⚪ | URL của Local Worker API (thay thế Cloud Run khi phát triển) | `http://localhost:8001/run` |

### Observability & Logging

| Variable | Required | Description | Example / Placeholder |
|---|---|---|---|
| `LANGCHAIN_API_KEY` | ⚪ | Key LangSmith tracing | `ls-your-langsmith-key-here` |
| `LANGCHAIN_TRACING_V2` | ⚪ | Bật LangSmith tracing | `true` |
| `LANGCHAIN_PROJECT` | ⚪ | Tên project trên LangSmith | `ridepulse-dq` |
| `AI_LOG_SERVER` | ⚪ | Server nhận AI hook logs (do instructor cung cấp) | `https://ai-logs.example.com/api/ingest` |
| `AI_LOG_API_KEY` | ⚪ | API key cho AI log server | `your-ai-log-key-here` |
| `AI_LOG_DIR` | ⚪ | Thư mục lưu log file cục bộ | `.ai-log` |

> [!CAUTION]
> **Tuyệt đối không commit file `.env` hoặc để lộ bất kỳ token/key thực nào lên git.**

## 5. Setup & Installation Guide

### Backend & Agent
1. **Clone repository và vào thư mục dự án:**
   ```bash
   git clone <repo_url>
   cd <project_dir>
   ```
2. **Thiết lập môi trường Python:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # Trên Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. **Cấu hình môi trường:**
   Tạo file `.env` từ `.env.example`:
   ```bash
   cp .env.example .env
   ```
   Cập nhật `OPENAI_API_KEY` và các biến cần thiết khác.
4. **Khởi chạy hạ tầng qua Docker Compose:**
   ```bash
   docker-compose up -d db minio
   ```
5. **Chạy FastAPI Server (Development):**
   ```bash
   uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
   ```
   *(Hoặc có thể chạy bằng Docker Compose đầy đủ: `docker-compose up --build`)*

### Frontend
1. **Vào thư mục frontend và cài đặt dependencies:**
   ```bash
   cd frontend
   npm install  # hoặc pnpm install
   ```
2. **Khởi chạy ứng dụng Frontend:**
   ```bash
   npm run dev
   ```

## 6. Sample Queries / Test Scenarios

Dưới đây là các kịch bản chạy Agent chính thông qua CLI script (`src/main.py`), thể hiện rõ luồng xử lý Agent tự động:

- **Kịch bản 1: Đề xuất luật Data Quality (Run 1 - Proposal)**
  ```bash
  python src/main.py 1
  ```
  *Luồng xử lý ngầm:* Kích hoạt `Proposal Graph`. Hệ thống sẽ lấy dữ liệu (`yellow_tripdata`), thực hiện profiling bằng Tool, đẩy metadata thu thập được sang LLM để tự động sinh ra các luật kiểm tra (Data Quality Rules) tối ưu nhất, sau đó lưu vào PostgreSQL và chờ con người phê duyệt (Human-in-the-loop).

- **Kịch bản 2: Thực thi kiểm thử tự động (Run 2 - Execution)**
  ```bash
  python src/main.py 2
  ```
  *Luồng xử lý ngầm:* Kích hoạt `Execution Graph`. Agent truy xuất các luật đã được duyệt từ DB, dùng LLM sinh mã SQL test (nếu sinh SQL lỗi hệ thống sẽ tự đưa vào *LLM Repair Node* để sửa lỗi), chạy SQL trực tiếp trên dataset, dùng LLM dò tìm và đánh giá các điểm dị thường (Anomaly Detector), cuối cùng lưu kết quả và xuất báo cáo.

- **Kịch bản 3: Chạy toàn bộ (End-to-End Pipeline)**
  ```bash
  python src/main.py all
  ```
  *Luồng xử lý ngầm:* Tự động kết hợp Run 1, giả lập thao tác phê duyệt (Approve) toàn bộ rules từ AI đề xuất, tiếp tục tự động đẩy vào Run 2 để sinh SQL, thực thi kiểm thử và xuất báo cáo kết quả cuối cùng.
