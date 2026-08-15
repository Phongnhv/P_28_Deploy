# Runbook: Localhost Development and Operations

This runbook provides step-by-step guidance for setting up, running, testing, and managing the RidePulse DQ platform in a **localhost-only** environment.

---

## 1. Scope and prerequisites

This runbook is for the Gate 2 **local UI MVP**. It starts FastAPI, React/Vite and a
fresh SQLite database on the developer machine. It does not require Docker,
PostgreSQL, MinIO, cloud deployment, or an LLM API key.

Install Python 3.12, Node.js 20+ and npm. From the repository root, install the
Python and frontend dependencies once:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm --prefix frontend install
```

---

## 2. Start the local API

Use a dedicated SQLite file for UI testing so no configured remote database is
touched. The proposal endpoint uses deterministic mock logic and therefore never
calls an LLM.

```powershell
$env:DATABASE_URL="sqlite:///ui_local_mvp.db"
$env:FRONTEND_ORIGIN="http://localhost:5173,http://127.0.0.1:5173"
.\.venv\Scripts\python.exe -m uvicorn src.main:app --host 127.0.0.1 --port 8000
```

Confirm the service in a second PowerShell window:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

---

## 3. Start the frontend against the API

In a second PowerShell window, run:

```powershell
$env:VITE_API_BASE_URL="http://127.0.0.1:8000"
$env:VITE_USE_MOCK_API="false"
npm --prefix frontend run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

Open `http://127.0.0.1:5173`. Seeded test accounts are:

- `user` / `user`: read-only access.
- `steward` / `steward`: profiling, proposal review and rule configuration.
- `admin` / `admin`: create local users and grant/revoke dataset access.

For a frontend-only demonstration, omit `VITE_USE_MOCK_API=false`; Vite then uses
the in-memory adapter in `frontend/src/api/mockApi.ts`.

---

## 4. Verification

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_session.py tests/test_proposals.py tests/test_runner.py tests/test_profile.py tests/test_jobs.py tests/test_audit.py tests/test_admin_config.py -v
npm --prefix frontend run build
ruff check src/api/routes.py src/models/database.py src/services/session_service.py src/services/rule_store.py tests/test_admin_config.py
```

The UI MVP has no migration command: FastAPI creates its local SQLite schema and
seeds the three accounts and default dataset when it starts. Delete
`ui_local_mvp.db` only after stopping the API when a fresh test database is needed.
