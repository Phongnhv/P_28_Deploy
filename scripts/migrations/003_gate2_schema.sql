-- Migration 003: Gate 2 Schema additions

-- 1. Alter datasets table to support manifest mapping
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS manifest_name VARCHAR(256);
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS row_count INT;

-- 2. Alter jobs table to support correlation_id and leasing
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS correlation_id VARCHAR(64);
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;

-- 3. Create sessions table for Steward authentication
CREATE TABLE IF NOT EXISTS sessions (
    id VARCHAR(256) PRIMARY KEY,
    username VARCHAR(256) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

-- 4. Create dataset_profiles table for overall stats
CREATE TABLE IF NOT EXISTS dataset_profiles (
    dataset_id VARCHAR(256) PRIMARY KEY,
    row_count INT NOT NULL,
    profile_data JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5. Create column_profiles table for per-column stats
CREATE TABLE IF NOT EXISTS column_profiles (
    dataset_id VARCHAR(256) NOT NULL,
    column_name VARCHAR(256) NOT NULL,
    null_count INT,
    null_percentage FLOAT,
    distinct_count INT,
    min_value TEXT,
    max_value TEXT,
    mean_value FLOAT,
    std_dev FLOAT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (dataset_id, column_name)
);

-- 6. Create dq_runs table to track approved rule runs
CREATE TABLE IF NOT EXISTS dq_runs (
    id VARCHAR(64) PRIMARY KEY,
    dataset_id VARCHAR(256) NOT NULL,
    status VARCHAR(32) DEFAULT 'PENDING', -- PENDING, RUNNING, SUCCEEDED, FAILED
    rules_count INT DEFAULT 0,
    passed_count INT DEFAULT 0,
    failed_count INT DEFAULT 0,
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 7. Create dq_results table for detailed rule run outcomes
CREATE TABLE IF NOT EXISTS dq_results (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id VARCHAR(64) NOT NULL REFERENCES dq_runs(id) ON DELETE CASCADE,
    rule_id UUID NOT NULL REFERENCES dq_rules(id) ON DELETE CASCADE,
    column_name VARCHAR(256),
    rule_type VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL, -- PASSED, FAILED
    violation_count INT DEFAULT 0,
    violation_details TEXT, -- JSON list of violating IDs
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Grant permissions to roles
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ridepulse_app;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO ridepulse_runner;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO ridepulse_dbt;
