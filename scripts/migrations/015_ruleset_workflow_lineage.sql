-- Additive production migration for durable workflow-owned rulesets.
--
-- Migration 007 creates ruleset_versions before the durable workflow existed.
-- Migration 011 adds lineage to the other runtime tables but missed this table.
-- This migration is safe to run after either the pre-split public schema or the
-- post-split rules schema and is intentionally idempotent.

BEGIN;

DO $$
DECLARE
    target_schema text;
BEGIN
    IF to_regclass('rules.ruleset_versions') IS NOT NULL THEN
        target_schema := 'rules';
    ELSIF to_regclass('public.ruleset_versions') IS NOT NULL THEN
        target_schema := 'public';
    ELSE
        RAISE EXCEPTION 'ruleset_versions table does not exist; run migration 007 first';
    END IF;

    EXECUTE format(
        'ALTER TABLE %I.ruleset_versions ADD COLUMN IF NOT EXISTS workflow_run_id VARCHAR(64)',
        target_schema
    );
    EXECUTE format(
        'ALTER TABLE %I.ruleset_versions ADD COLUMN IF NOT EXISTS stale BOOLEAN NOT NULL DEFAULT FALSE',
        target_schema
    );
    EXECUTE format(
        'ALTER TABLE %I.ruleset_versions ALTER COLUMN proposal_run_id TYPE VARCHAR(512)',
        target_schema
    );
    EXECUTE format(
        'CREATE INDEX IF NOT EXISTS ix_ruleset_versions_workflow_run_id ON %I.ruleset_versions(workflow_run_id)',
        target_schema
    );
END
$$;

COMMIT;
