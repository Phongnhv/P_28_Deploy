-- 1. Role: migration (schema changes)
CREATE ROLE ridepulse_migration WITH LOGIN PASSWORD 'YOUR_MIGRATION_PASSWORD_HERE';
GRANT CREATE ON SCHEMA public TO ridepulse_migration;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO ridepulse_migration;

-- 2. Role: app (FastAPI service)
CREATE ROLE ridepulse_app WITH LOGIN PASSWORD 'YOUR_APP_PASSWORD_HERE';
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO ridepulse_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE ON TABLES TO ridepulse_app;

-- 3. Role: dbt (analytics transforms)
CREATE ROLE ridepulse_dbt WITH LOGIN PASSWORD 'YOUR_DBT_PASSWORD_HERE';
CREATE SCHEMA IF NOT EXISTS analytics;
GRANT ALL ON SCHEMA analytics TO ridepulse_dbt;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO ridepulse_dbt;

-- 4. Role: runner (read-only DQ execution)
CREATE ROLE ridepulse_runner WITH LOGIN PASSWORD 'YOUR_RUNNER_PASSWORD_HERE';
GRANT SELECT ON ALL TABLES IN SCHEMA public TO ridepulse_runner;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO ridepulse_runner;
