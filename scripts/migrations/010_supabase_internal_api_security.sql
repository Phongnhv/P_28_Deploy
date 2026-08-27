-- Supabase security boundary for the API-owned application schema.
--
-- Authentication currently lives in the FastAPI `sessions` table rather than
-- Supabase Auth. The browser must therefore never query these internal tables
-- through PostgREST. FastAPI performs workspace/grant checks and connects with
-- the trusted server credential; anon/authenticated receive no table access.

DO $$
DECLARE
    app_role TEXT;
BEGIN
    FOREACH app_role IN ARRAY ARRAY['anon', 'authenticated']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = app_role) THEN
            EXECUTE format('REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM %I', app_role);
            EXECUTE format('REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM %I', app_role);
        END IF;
    END LOOP;
END
$$;

ALTER TABLE datasets ENABLE ROW LEVEL SECURITY;
ALTER TABLE datasets FORCE ROW LEVEL SECURITY;
ALTER TABLE dataset_governance ENABLE ROW LEVEL SECURITY;
ALTER TABLE dataset_governance FORCE ROW LEVEL SECURITY;
ALTER TABLE dataset_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE dataset_versions FORCE ROW LEVEL SECURITY;
ALTER TABLE dataset_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE dataset_grants FORCE ROW LEVEL SECURITY;
ALTER TABLE profile_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE profile_runs FORCE ROW LEVEL SECURITY;
ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE profiles FORCE ROW LEVEL SECURITY;
ALTER TABLE rule_review_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE rule_review_snapshots FORCE ROW LEVEL SECURITY;
ALTER TABLE analysis_summaries ENABLE ROW LEVEL SECURITY;
ALTER TABLE analysis_summaries FORCE ROW LEVEL SECURITY;
ALTER TABLE governed_artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE governed_artifacts FORCE ROW LEVEL SECURITY;
ALTER TABLE governance_audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE governance_audit_events FORCE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION public.prevent_governance_audit_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
    RAISE EXCEPTION 'governance audit events are append-only';
END;
$$;

REVOKE ALL ON FUNCTION public.prevent_governance_audit_mutation() FROM PUBLIC;

DROP TRIGGER IF EXISTS governance_audit_append_only ON governance_audit_events;
CREATE TRIGGER governance_audit_append_only
    BEFORE UPDATE OR DELETE ON governance_audit_events
    FOR EACH ROW EXECUTE FUNCTION public.prevent_governance_audit_mutation();
