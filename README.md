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

Hệ thống yêu cầu các biến môi trường sau để cấu hình kết nối, API key, và môi trường. Bạn có thể xem file `.env.example`.

| Variable | Description | Example / Placeholder |
|---|---|---|
| `OPENAI_API_KEY` | API Key để gọi OpenAI LLMs | `sk-your-openai-api-key-here` |
| `DATABASE_URL` | Chuỗi kết nối Database chính | `postgresql+psycopg2://user:pass@localhost:5432/dbname` |
| `RUNNER_DATABASE_URL` | Chuỗi kết nối Database cho Worker runner | `postgresql+psycopg2://runner:pass@db:5432/dbname` |
| `CHROMA_PERSIST_DIR` | Thư mục lưu trữ vector của ChromaDB | `./data/chroma` |
| `APP_ENV` | Môi trường triển khai (local, dev, prod) | `local` |
| `FRONTEND_ORIGIN` | Cấu hình CORS cho Frontend | `http://localhost:3000` |
| `LANGCHAIN_API_KEY` | Key cho LangSmith tracing | `ls-your-langsmith-key-here` |

*Lưu ý bảo mật: Tuyệt đối không commit file `.env` chứa token thực lên git.*

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
