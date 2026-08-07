# Gate 2 MVP — Setup chung

## 1. Điều kiện cần trước khi code

- Python 3.11+ và Git.
- Một OpenAI API key hợp lệ, có quota/billing hoạt động cho demo call.
- Không cần PostgreSQL, Docker, Node.js hay database server trong MVP vòng đầu.

Không gửi API key trong chat, source code, test fixture, issue hoặc video demo.

## 2. Tạo môi trường local

Từ repository root trên Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Nếu PowerShell chặn activation:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
```

## 3. Cấu hình `.env`

Chỉ sửa file `.env` local; file này phải được `.gitignore`.

```dotenv
APP_ENV=development
OPENAI_API_KEY=<your-valid-key>
MODEL_NAME=gpt-4o-mini
DATABASE_URL=sqlite:///./data/gate2_mvp.db
CORS_ORIGINS=http://localhost:8000
```

`OPENAI_API_KEY` là blocker cho yêu cầu “LLM thực tế”. Key placeholder hoặc key hết
quota sẽ làm endpoint proposal trả lỗi rõ ràng; UI phải hiển thị lỗi và không tự thay
bằng output giả.

Khi bạn đã đặt key, chỉ cần báo trong task là **“đã cấu hình API key”**; không cần
dán giá trị key.

## 4. Chạy app sau khi các PR MVP được merge

```powershell
.\.venv\Scripts\python.exe -m uvicorn src.main:app --reload --port 8000
```

Mở:

- UI: <http://localhost:8000/ui/>
- Swagger: <http://localhost:8000/docs>
- Liveness: <http://localhost:8000/health>

## 5. Verification cục bộ

```powershell
.\.venv\Scripts\python.exe -m pytest tests -v
.\.venv\Scripts\python.exe -m ruff check src tests
```

Automated tests mock LLM/external calls. Một manual live test riêng cần chạy với key
hợp lệ để ghi evaluation evidence.

## 6. Khi nào mới cần PostgreSQL

Chỉ đưa PostgreSQL vào sau Gate 2 hoặc khi nhóm quyết định chạy dataset lớn hơn đáng
kể, cần concurrent user, SQL pushdown, migration hoặc phân quyền database. Khi đó ưu
tiên Docker Compose thay vì cài database trực tiếp trên từng máy.
