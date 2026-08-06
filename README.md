# RidePulse DQ

> MVP trợ lý Data Quality cho Data Steward: profile dataset mobility, đề xuất rule có
> cấu trúc, bắt buộc HITL và chạy kiểm tra read-only có audit.

## Trạng thái repository

**Starter template — product MVP chưa được implement.**

Hiện có:

- FastAPI app với `/health`, `/api/v1/status`, `/api/v1/chat`.
- LangGraph demo `analyze → respond` với logic placeholder.
- `ChatOpenAI` service factory nhưng graph chưa gọi LLM.
- Pydantic settings/request/response schemas.
- Backend Dockerfile/Compose, GitHub Actions, pytest và Ruff.
- Static `ui_test` prototype không kết nối backend.
- 5 automated tests của scaffold.

Chưa có:

- NYC TLC data/manifest/ingestion/profiling.
- PostgreSQL persistence hoặc migration.
- Structured rule proposal, HITL, SQL compiler hoặc DQ runner.
- React frontend và Dagster orchestration.

Mọi capability chưa có được ghi `Proposed / Not implemented` trong tài liệu.

## MVP target

```text
NYC TLC Yellow Taxi Parquet (100k–1M rows)
  -> PostgreSQL ingestion
  -> aggregate profiling
  -> LangGraph/LLM structured rule proposals
  -> Data Steward approve/edit/reject
  -> template SQL compiler
  -> read-only DQ execution
  -> React results dashboard + audit
```

Chicago datasets, streaming, RAG, dbt/Great Expectations, Isolation Forest và
automatic raw-data repair không nằm trong MVP core.

## Bắt đầu nhanh — current scaffold

Yêu cầu Python 3.11. Trên Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn src.main:app --reload --port 8000
```

Mở Swagger UI tại <http://localhost:8000/docs>.

```powershell
curl.exe http://localhost:8000/health
curl.exe http://localhost:8000/api/v1/status
```

Hướng dẫn đầy đủ và troubleshooting: [docs/RUNBOOK.md](docs/RUNBOOK.md).

## Test và lint

```powershell
.\.venv\Scripts\python.exe -m pytest tests -v
.\.venv\Scripts\python.exe -m ruff check src tests
```

Current baseline: 5 tests; chúng chỉ xác nhận scaffold, không chứng minh MVP đã chạy.

## Documentation map

### Đọc trước khi implement

1. [Product Specification](docs/PRODUCT_SPEC.md)
2. [Implementation Plan](docs/IMPLEMENTATION_PLAN.md)
3. [Backlog](docs/BACKLOG.md)
4. [Architecture](ARCHITECTURE.md)
5. [API Contract](docs/API_CONTRACT.md)
6. [Data Model](docs/DATA_MODEL.md)
7. [Test Plan](docs/TEST_PLAN.md)
8. [Architecture Decisions](docs/DECISIONS.md)
9. [Runbook](docs/RUNBOOK.md)

### Làm việc với coding agent

- [Agent rules](AGENTS.md)
- [AI coding guide](docs/ai-coding/README.md)
- [Task template](docs/ai-coding/TASK_TEMPLATE.md)
- [Review checklist](docs/ai-coding/REVIEW_CHECKLIST.md)
- [Testing checklist](docs/ai-coding/TESTING_CHECKLIST.md)
- [Debugging playbook](docs/ai-coding/DEBUGGING_PLAYBOOK.md)

### Tài liệu chương trình

- Technical Guidebook offline: [`docs/guide/`](docs/guide/)
- Deliverables checklist: [`docs/guide/deliverables/checklist.md`](docs/guide/deliverables/checklist.md)
- AI logging rules: [`.agents/rules/ai-log-hook.md`](.agents/rules/ai-log-hook.md)

## Project structure hiện tại

```text
src/
  agents/          # LangGraph scaffold
  api/             # FastAPI routes
  models/          # Pydantic schemas
  services/        # LLM service factory
tests/              # Scaffold API/Agent tests
docs/               # Product, contracts, workflow và guidebook
ui_test/            # Static visual prototype; không phải product frontend
eval/               # Evaluation placeholder
scripts/            # Setup và AI logging helpers
```

Target structure chỉ được tạo dần theo [backlog](docs/BACKLOG.md), không scaffold tất
cả folder trong một task.

## Team workflow

| Role | Responsibility |
|---|---|
| Product/QA owner | Scope, user flow, datasets, acceptance criteria, manual testing |
| Backend owner | API, schemas, services, data access |
| Agent owner | LangGraph, prompts, tools, evaluation |
| UI/Integration owner | UI, API integration, demo flow, deployment support |

- Mỗi task có một owner chính.
- Task P0 cần reviewer khác owner.
- Không merge nếu thiếu acceptance criteria hoặc verification result.
- Product/QA owner chịu trách nhiệm xác nhận scope và manual acceptance.

## AI usage logging

Coding tools được hỗ trợ đã auto-log. Không chạy manual logger sau mỗi task và không
sửa `.ai-log/`. Nếu dùng web tool không có hook, đọc `.agents/workflows/log.md`.

## Known limitations

- Product flow chưa được implement.
- Current `/api/v1/status` trả status tĩnh.
- Current `/api/v1/chat` chỉ echo qua placeholder graph.
- Docker Compose hiện chỉ chạy backend.
- `.env.example` và config vẫn chứa option template chưa phải target architecture.
- Performance, precision/recall và Data Health Score chưa có measured evidence.

## License

MIT — sử dụng cho mục đích giáo dục theo repository template.
