# RidePulse DQ — Runbook

> Các lệnh trong phần **Current** chạy với starter template hiện tại. Các phần ghi
> **Proposed / Not implemented** chỉ là target và chưa thể chạy.

## 1. Yêu cầu hiện tại

- Windows PowerShell.
- Python 3.11.
- Git.
- Docker Desktop chỉ cần nếu chạy container.

## 2. Setup current backend

```powershell
Set-Location C:\path\to\P-028
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Mở `.env` và thay placeholder cần thiết. Không commit `.env`; không paste file này vào
issue/chat/log. Current graph không gọi LLM, nhưng future Agent task sẽ cần provider key.
Team cần xác nhận credential AI logging đang có trong `.env.example` là giá trị public
của chương trình; nếu không, phải rotate thay vì tiếp tục copy giá trị đó.

Nếu PowerShell chặn activation:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
```

## 3. Chạy backend hiện tại

```powershell
.\.venv\Scripts\python.exe -m uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

Kiểm tra:

```powershell
curl.exe http://localhost:8000/health
curl.exe http://localhost:8000/api/v1/status
curl.exe -X POST http://localhost:8000/api/v1/chat `
  -H "Content-Type: application/json" `
  -d '{"message":"Hello"}'
```

Swagger UI: <http://localhost:8000/docs>

OpenAPI JSON: <http://localhost:8000/openapi.json>

## 4. Chạy static UI prototype hiện tại

`ui_test` là prototype độc lập, không phải React app và chưa kết nối backend.

```powershell
.\.venv\Scripts\python.exe -m http.server 5173 --directory ui_test
```

Mở <http://localhost:5173>. Không dùng số liệu trong prototype làm evaluation evidence.

## 5. Test và lint hiện tại

```powershell
.\.venv\Scripts\python.exe -m pytest tests -v
.\.venv\Scripts\python.exe -m ruff check src tests
```

Test hẹp:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_api\test_routes.py -v
.\.venv\Scripts\python.exe -m pytest tests\test_agents\test_graph.py -v
```

Ruff format chỉ chạy khi task cho phép thay đổi formatting:

```powershell
.\.venv\Scripts\python.exe -m ruff format src tests
```

## 6. Docker hiện tại

Compose hiện chỉ có backend service và mount `./data`.

```powershell
docker compose config
docker compose build backend
docker compose up backend
```

Theo dõi/dừng:

```powershell
docker compose logs -f backend
docker compose down
```

Không dùng `docker compose down -v` trừ khi đã xác nhận volume được phép xóa.

## 7. AI usage logging

Codex/Claude Code/Cursor/Gemini CLI và tool được hỗ trợ đã auto-log. Không chạy script
manual sau task. Đọc `.agents/rules/ai-log-hook.md`.

Với web tool không có hook, làm theo `.agents/workflows/log.md`. Không sửa/xóa `.ai-log/`
và không bypass pre-push hook bằng `--no-verify` nếu hook lỗi.

## 8. Troubleshooting hiện tại

### `ModuleNotFoundError`

- Kiểm tra `.venv` đúng repo và chạy `python -m pip install -r requirements.txt`.
- Dùng `.\.venv\Scripts\python.exe` để tránh nhầm Python global.

### Port 8000 đang được dùng

```powershell
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue
```

Dừng đúng process do bạn quản lý hoặc chạy Uvicorn ở port khác; cập nhật URL test.

### `/api/v1/chat` trả 422

Payload phải có `message` string dài 1–5000 ký tự và header JSON.

### `/api/v1/status` báo ready nhưng LLM chưa cấu hình

Đây là limitation hiện tại: endpoint trả response tĩnh. Không dùng nó làm readiness
evidence trước khi `DEMO-001` được implement.

### Docker healthcheck fail

- Đọc `docker compose logs backend`.
- Kiểm tra `.env`, dependency install và `/health` trên port 8000.
- Không sửa healthcheck để luôn pass.

## 9. Proposed MVP runtime — Not implemented

Sau `MVP-003/MVP-004`, runbook phải bổ sung migration commands:

```powershell
docker compose up -d postgres
alembic upgrade head
```

Sau `MVP-018`, bổ sung frontend commands thực tế từ `frontend/package.json`.
Sau `MVP-021`, bổ sung Dagster webserver/daemon commands. Không dùng các lệnh này như
verification trước khi file/dependency tương ứng tồn tại.

## 10. Demo preparation checklist

- [ ] Checkout đúng commit và working tree đã được review.
- [ ] `.env` không có placeholder bắt buộc; không hiển thị secret khi quay màn hình.
- [ ] Data source đã cache local, checksum đúng; không download live.
- [ ] Migration và startup pass từ clean state.
- [ ] Full pytest, Ruff, frontend checks và MVP smoke test pass.
- [ ] Core flow đã rehearsal hai lần.
- [ ] Có deterministic LLM fallback được gắn nhãn nếu live provider unavailable.
- [ ] Không trình bày prototype metric/hard-coded data như kết quả thật.
- [ ] Có backup screenshot/video và recovery steps.

## 11. Deployment

Current repository có backend Docker image và GitHub Actions lint/test, chưa có cloud
deployment config hoặc full stack deployment. Cloud deploy là post-MVP; chỉ thêm hướng
dẫn khi provider, secrets, database, healthcheck và rollback đã được team duyệt.
