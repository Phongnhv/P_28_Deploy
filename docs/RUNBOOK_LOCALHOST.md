# Runbook: Localhost Development and Operations

This runbook provides step-by-step guidance for setting up, running, testing, and managing the RidePulse DQ platform in a **localhost-only** environment.

---

## 1. Prerequisites

Ensure you have the following installed on your host system:
* **Docker** and **Docker Compose**
* **Python 3.11** or **Python 3.12** (matching project venv)
* **curl** (for running smoke tests)

---

## 2. Environment Variables (.env)

Create a `.env` file in the project root:
```dotenv
APP_ENV=local
FRONTEND_ORIGIN=http://localhost:3000
DATABASE_URL=postgresql+psycopg2://postgres:localpassword@localhost:5432/ridepulse
RUNNER_DATABASE_URL=postgresql+psycopg2://ridepulse_runner:YOUR_RUNNER_PASSWORD_HERE@localhost:5432/ridepulse
MINIO_URL=http://localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=miniopassword
OPENAI_API_KEY=your-test-openai-key-here
AI_LOG_SERVER=https://ai-logs.note.transformerlabs.ai/api/ingest
AI_LOG_API_KEY=your-log-api-key-here
AI_LOG_DIR=.ai-log
```

---

## 3. Starting the Services

Start all database, storage, api, and worker containers:
```bash
docker compose up -d
```

Verify that all services are healthy and running:
```bash
docker compose ps
```

Expected services:
* `ridepulse-db` (PostgreSQL on port `5432`)
* `ridepulse-minio` (MinIO on port `9000` & `9001`)
* `ridepulse-api` (FastAPI on port `8000`)
* `ridepulse-worker` (Local worker API on port `8001`)

---

## 4. Running Schema Migrations

Run migrations sequentially in the database container to set up schemas, roles, and tables:

```bash
# 1. Run core schema migration
docker compose exec -T db psql -U postgres -d postgres -f /scripts/migrations/001_schema.sql

# 2. Configure database roles and schemas
docker compose exec -T db psql -U postgres -d postgres -f /scripts/migrations/002_roles.sql

# 3. Create Gate 2 schemas, tables, and alter structures
docker compose exec -T db psql -U postgres -d postgres -f /scripts/migrations/003_gate2_schema.sql
```

---

## 5. Verification: Running Automated Tests

Run the full pytest suite from the host system using the local virtual environment:

```bash
.\venv\Scripts\python.exe -m pytest tests -v
```

All 60+ test cases should pass.

---

## 6. Verification: Running local Smoke Test

Execute the updated Gate 2 smoke test script:

```bash
# On Windows, run in Git Bash or PowerShell:
bash scripts/smoke-test-local.sh
```

Expected output ends with:
```text
✅ ALL SMOKE TESTS PASSED SUCCESSFULLY!
```

---

## 7. Database Backup & Restore Rehearsal

### 7.1. Backup (Export)
Dump the current database schema and data to a backup SQL file:
```bash
docker compose exec db pg_dump -U postgres -d postgres > backup_local.sql
```

### 7.2. Recovery (Restore)
To restore from the SQL backup file:
```bash
# Reset database
docker compose exec db psql -U postgres -d postgres -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
# Reapply privileges
docker compose exec db psql -U postgres -d postgres -c "GRANT ALL ON SCHEMA public TO postgres; GRANT ALL ON SCHEMA public TO public;"
# Restore data
docker compose exec -T db psql -U postgres -d postgres < backup_local.sql
```
