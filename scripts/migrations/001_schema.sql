CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Core tables
CREATE TABLE IF NOT EXISTS datasets (
    id VARCHAR(256) PRIMARY KEY,
    name VARCHAR(256) NOT NULL,
    description TEXT NOT NULL,
    status VARCHAR(64) NOT NULL DEFAULT 'REGISTERED',
    row_count INT NOT NULL DEFAULT 0,
    source_label VARCHAR(256) NOT NULL,
    manifest_version VARCHAR(64) NOT NULL,
    checksum VARCHAR(256) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS jobs (
    id VARCHAR(64) PRIMARY KEY,
    type VARCHAR(64) NOT NULL,
    status VARCHAR(32) DEFAULT 'PENDING',
    progress FLOAT NOT NULL DEFAULT 0.0,
    message TEXT,
    error TEXT,
    idempotency_key VARCHAR(256) UNIQUE,
    linked_entity VARCHAR(256),
    attempt_count INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS trips_raw (
    source_row_id VARCHAR(128) PRIMARY KEY,
    vendor_id INT,
    pickup_at TIMESTAMPTZ,
    dropoff_at TIMESTAMPTZ,
    passenger_count INT,
    trip_distance FLOAT,
    rate_code_id INT,
    store_and_fwd_flag VARCHAR(1),
    pickup_location_id INT,
    dropoff_location_id INT,
    payment_type INT,
    fare_amount FLOAT,
    extra FLOAT,
    mta_tax FLOAT,
    tip_amount FLOAT,
    tolls_amount FLOAT,
    improvement_surcharge FLOAT,
    total_amount FLOAT,
    congestion_surcharge FLOAT,
    airport_fee FLOAT,
    cbd_congestion_fee FLOAT
);

-- DQ & AI Rules tables
CREATE TABLE IF NOT EXISTS proposed_rules (
    run_id VARCHAR(64),
    rule_id VARCHAR(512),
    dataset_id VARCHAR(256),
    table_name VARCHAR(256),
    column_name VARCHAR(256),
    rule_type VARCHAR(64),
    parameters TEXT,
    edited_parameters TEXT,
    confidence_score FLOAT,
    severity VARCHAR(32),
    dimension VARCHAR(32),
    rule_description TEXT,
    ai_reasoning TEXT,
    status VARCHAR(32) DEFAULT 'PENDING',
    reviewer VARCHAR(256),
    review_note TEXT,
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (run_id, rule_id)
);

CREATE TABLE IF NOT EXISTS proposal_runs (
    run_id VARCHAR(64) PRIMARY KEY,
    dataset_id VARCHAR(256),
    status VARCHAR(32) DEFAULT 'QUEUED',
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS dq_rules (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    dataset_id VARCHAR(256),
    rule_type VARCHAR(64),
    parameters TEXT,
    severity VARCHAR(32),
    dimension VARCHAR(32),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Security & Auditing
CREATE TABLE IF NOT EXISTS audit_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_type VARCHAR(64),
    entity_id VARCHAR(256),
    action VARCHAR(64),
    actor VARCHAR(256),
    details TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rate_limit_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ip_address VARCHAR(64),
    endpoint VARCHAR(256),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
