-- Migration script for Graph 2 (Rule Execution) and Graph 3 (Anomaly Detection) Models
--
-- This script creates RulesetVersionModel, AnomalyRunModel, AnomalySignalModel, AnomalyHypothesisModel, and AnomalyFeedbackModel tables.
-- It also alters existing dq_runs and dq_results tables to add extension columns.

CREATE TABLE IF NOT EXISTS public.ruleset_versions (
    id VARCHAR(64) PRIMARY KEY,
    dataset_id VARCHAR(256) NOT NULL REFERENCES public.datasets(id),
    dataset_version_id VARCHAR(64),
    proposal_run_id VARCHAR(64) REFERENCES public.rule_proposals(id),
    semantic_contract_version_id VARCHAR(64),
    ruleset_hash VARCHAR(256) NOT NULL,
    normalized_rules TEXT NOT NULL,
    created_by VARCHAR(256) NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_ruleset_versions_dataset_id ON public.ruleset_versions (dataset_id);

ALTER TABLE public.dq_runs 
    ADD COLUMN IF NOT EXISTS ruleset_version_id VARCHAR(64) REFERENCES public.ruleset_versions(id),
    ADD COLUMN IF NOT EXISTS compiler_version VARCHAR(64),
    ADD COLUMN IF NOT EXISTS artifact_hash VARCHAR(256),
    ADD COLUMN IF NOT EXISTS retry_history_json TEXT,
    ADD COLUMN IF NOT EXISTS error_message TEXT,
    ADD COLUMN IF NOT EXISTS dbt_status VARCHAR(32),
    ADD COLUMN IF NOT EXISTS metrics_status VARCHAR(32);

ALTER TABLE public.dq_results
    ALTER COLUMN id TYPE VARCHAR(36) USING id::text,
    ADD COLUMN IF NOT EXISTS violation_rate FLOAT,
    ADD COLUMN IF NOT EXISTS duration_ms FLOAT,
    ADD COLUMN IF NOT EXISTS dbt_status VARCHAR(32),
    ADD COLUMN IF NOT EXISTS metrics_status VARCHAR(32),
    ADD COLUMN IF NOT EXISTS error_message TEXT;

CREATE TABLE IF NOT EXISTS public.anomaly_runs (
    id VARCHAR(64) PRIMARY KEY,
    execution_run_id VARCHAR(64) NOT NULL REFERENCES public.dq_runs(id),
    detector_config_version VARCHAR(64) NOT NULL DEFAULT 'anomaly-v1',
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    decision VARCHAR(32) NOT NULL,
    score FLOAT NOT NULL DEFAULT 0.0,
    confidence FLOAT NOT NULL DEFAULT 0.0,
    severity VARCHAR(32) NOT NULL DEFAULT 'LOW',
    error_message TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMP WITHOUT TIME ZONE
);
CREATE INDEX IF NOT EXISTS ix_anomaly_runs_execution_run_id ON public.anomaly_runs (execution_run_id);

CREATE TABLE IF NOT EXISTS public.anomaly_signals (
    id VARCHAR(64) PRIMARY KEY,
    anomaly_run_id VARCHAR(64) NOT NULL REFERENCES public.anomaly_runs(id),
    family VARCHAR(64) NOT NULL,
    target_type VARCHAR(32) NOT NULL,
    target_id VARCHAR(256) NOT NULL,
    score FLOAT NOT NULL,
    reliability FLOAT NOT NULL,
    observed_value TEXT,
    baseline TEXT,
    sufficient_history BOOLEAN NOT NULL DEFAULT FALSE,
    detector_name VARCHAR(128) NOT NULL,
    detector_version VARCHAR(32) NOT NULL,
    explanation_code TEXT NOT NULL,
    evidence_refs TEXT NOT NULL DEFAULT '[]',
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_anomaly_signals_anomaly_run_id ON public.anomaly_signals (anomaly_run_id);

CREATE TABLE IF NOT EXISTS public.anomaly_hypotheses (
    id VARCHAR(64) PRIMARY KEY,
    anomaly_run_id VARCHAR(64) NOT NULL REFERENCES public.anomaly_runs(id),
    hypothesis_type VARCHAR(64) NOT NULL,
    summary TEXT NOT NULL,
    confidence FLOAT NOT NULL,
    supporting_signal_ids TEXT NOT NULL DEFAULT '[]',
    contradicting_signal_ids TEXT NOT NULL DEFAULT '[]',
    evidence_refs TEXT NOT NULL DEFAULT '[]',
    recommended_checks TEXT NOT NULL DEFAULT '[]',
    missing_evidence TEXT,
    limitations TEXT,
    model_name VARCHAR(128) NOT NULL,
    prompt_version VARCHAR(64) NOT NULL,
    latency_ms INT NOT NULL DEFAULT 0,
    fallback_used BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_anomaly_hypotheses_anomaly_run_id ON public.anomaly_hypotheses (anomaly_run_id);

CREATE TABLE IF NOT EXISTS public.anomaly_feedback (
    id VARCHAR(64) PRIMARY KEY,
    anomaly_run_id VARCHAR(64) NOT NULL REFERENCES public.anomaly_runs(id),
    username VARCHAR(100) NOT NULL REFERENCES public.user_accounts(username),
    feedback_label VARCHAR(64) NOT NULL,
    comment TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_anomaly_feedback_anomaly_run_id ON public.anomaly_feedback (anomaly_run_id);

DO $$
DECLARE
    role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY['ridepulse_app', 'ridepulse_runner', 'ridepulse_dbt']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format('GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO %I', role_name);
        END IF;
    END LOOP;
END
$$;
