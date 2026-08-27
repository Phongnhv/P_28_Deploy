-- Additive lineage columns for the generic versioned dataset runtime.
-- Safe to run repeatedly. Existing taxi/demo rows remain valid because all
-- new fields are nullable; new canonical paths populate them explicitly.

BEGIN;

ALTER TABLE graph1_runs
    ADD COLUMN IF NOT EXISTS workspace_id VARCHAR(64),
    ADD COLUMN IF NOT EXISTS dataset_version_id VARCHAR(64),
    ADD COLUMN IF NOT EXISTS profile_run_id VARCHAR(64);

ALTER TABLE analysis_runs
    ADD COLUMN IF NOT EXISTS workspace_id VARCHAR(64),
    ADD COLUMN IF NOT EXISTS dataset_version_id VARCHAR(64),
    ADD COLUMN IF NOT EXISTS profile_run_id VARCHAR(64),
    ADD COLUMN IF NOT EXISTS rule_review_snapshot_id VARCHAR(64);

ALTER TABLE rule_versions
    ADD COLUMN IF NOT EXISTS dataset_version_id VARCHAR(64);

ALTER TABLE dq_runs
    ADD COLUMN IF NOT EXISTS workspace_id VARCHAR(64),
    ADD COLUMN IF NOT EXISTS dataset_version_id VARCHAR(64),
    ADD COLUMN IF NOT EXISTS profile_run_id VARCHAR(64),
    ADD COLUMN IF NOT EXISTS rule_review_snapshot_id VARCHAR(64),
    ADD COLUMN IF NOT EXISTS source_checksum VARCHAR(256);

CREATE INDEX IF NOT EXISTS ix_graph1_runs_version ON graph1_runs(dataset_version_id);
CREATE INDEX IF NOT EXISTS ix_analysis_runs_version ON analysis_runs(dataset_version_id);
CREATE INDEX IF NOT EXISTS ix_rule_versions_version ON rule_versions(dataset_version_id);
CREATE INDEX IF NOT EXISTS ix_dq_runs_version ON dq_runs(dataset_version_id);

COMMIT;

-- Rollback (manual, only after all versioned runs are retired):
-- ALTER TABLE graph1_runs DROP COLUMN IF EXISTS workspace_id, DROP COLUMN IF EXISTS dataset_version_id, DROP COLUMN IF EXISTS profile_run_id;
-- ALTER TABLE analysis_runs DROP COLUMN IF EXISTS workspace_id, DROP COLUMN IF EXISTS dataset_version_id, DROP COLUMN IF EXISTS profile_run_id, DROP COLUMN IF EXISTS rule_review_snapshot_id;
-- ALTER TABLE rule_versions DROP COLUMN IF EXISTS dataset_version_id;
-- ALTER TABLE dq_runs DROP COLUMN IF EXISTS workspace_id, DROP COLUMN IF EXISTS dataset_version_id, DROP COLUMN IF EXISTS profile_run_id, DROP COLUMN IF EXISTS rule_review_snapshot_id, DROP COLUMN IF EXISTS source_checksum;
