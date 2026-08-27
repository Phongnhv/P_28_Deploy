-- Workspace-scoped read policies for the v2 data-access contract.
-- This migration is exercised only against the disposable local contract DB.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ridepulse_contract_reader') THEN
        CREATE ROLE ridepulse_contract_reader
            NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
END
$$;

ALTER ROLE ridepulse_contract_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;

CREATE OR REPLACE FUNCTION public.app_can_dataset_permission(
    requested_dataset_id TEXT,
    requested_dataset_version_id TEXT,
    requested_permission TEXT
)
RETURNS BOOLEAN
LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM workspace_memberships membership
        JOIN dataset_governance governance
          ON governance.workspace_id = membership.workspace_id
         AND governance.dataset_id = requested_dataset_id
        WHERE membership.workspace_id = NULLIF(current_setting('app.workspace_id', TRUE), '')
          AND membership.user_id = NULLIF(current_setting('app.user_id', TRUE), '')
          AND membership.status = 'ACTIVE'
          AND (
              membership.role = 'ADMIN'
              OR governance.owner_user_id = membership.user_id
              OR EXISTS (
                  SELECT 1
                  FROM dataset_stewards steward
                  WHERE steward.dataset_id = governance.dataset_id
                    AND steward.user_id = membership.user_id
                    AND steward.revoked_at IS NULL
              )
              OR EXISTS (
                  SELECT 1
                  FROM dataset_grants grant_row
                  WHERE grant_row.workspace_id = membership.workspace_id
                    AND grant_row.dataset_id = governance.dataset_id
                    AND (
                        requested_dataset_version_id IS NULL
                        OR grant_row.dataset_version_id IS NULL
                        OR grant_row.dataset_version_id = requested_dataset_version_id
                    )
                    AND grant_row.revoked_at IS NULL
                    AND (grant_row.expires_at IS NULL OR grant_row.expires_at > CURRENT_TIMESTAMP)
                    AND (
                        (grant_row.grantee_type = 'USER' AND grant_row.grantee_id = membership.user_id)
                        OR (
                            grant_row.grantee_type = 'WORKSPACE'
                            AND grant_row.grantee_id = membership.workspace_id
                        )
                        OR (
                            grant_row.grantee_type = 'GROUP'
                            AND EXISTS (
                                SELECT 1
                                FROM data_group_memberships group_member
                                JOIN data_groups data_group ON data_group.id = group_member.group_id
                                WHERE group_member.group_id = grant_row.grantee_id
                                  AND group_member.user_id = membership.user_id
                                  AND group_member.status = 'ACTIVE'
                                  AND data_group.workspace_id = membership.workspace_id
                            )
                        )
                    )
                    AND (
                        grant_row.permission = 'MANAGE'
                        OR grant_row.permission = requested_permission
                        OR (
                            requested_permission = 'DISCOVER'
                            AND grant_row.permission IN (
                                'DISCOVER', 'VIEW_PROFILE', 'VIEW_REPORTS',
                                'VIEW_ROWS', 'RUN_ANALYSIS'
                            )
                        )
                    )
              )
          )
    );
$$;

REVOKE ALL ON FUNCTION public.app_can_dataset_permission(TEXT, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.app_can_dataset_permission(TEXT, TEXT, TEXT)
    TO ridepulse_contract_reader;

GRANT USAGE ON SCHEMA public TO ridepulse_contract_reader;
GRANT SELECT ON
    datasets,
    dataset_governance,
    dataset_versions,
    profile_runs,
    rule_review_snapshots,
    analysis_summaries,
    governed_artifacts,
    governance_audit_events
TO ridepulse_contract_reader;

ALTER TABLE datasets ENABLE ROW LEVEL SECURITY;
ALTER TABLE dataset_governance ENABLE ROW LEVEL SECURITY;
ALTER TABLE dataset_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE profile_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE rule_review_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE analysis_summaries ENABLE ROW LEVEL SECURITY;
ALTER TABLE governed_artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE governance_audit_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS datasets_discover_policy ON datasets;
CREATE POLICY datasets_discover_policy ON datasets
    FOR SELECT TO ridepulse_contract_reader
    USING (public.app_can_dataset_permission(id, NULL, 'DISCOVER'));

DROP POLICY IF EXISTS dataset_governance_discover_policy ON dataset_governance;
CREATE POLICY dataset_governance_discover_policy ON dataset_governance
    FOR SELECT TO ridepulse_contract_reader
    USING (public.app_can_dataset_permission(dataset_id, NULL, 'DISCOVER'));

DROP POLICY IF EXISTS dataset_versions_discover_policy ON dataset_versions;
CREATE POLICY dataset_versions_discover_policy ON dataset_versions
    FOR SELECT TO ridepulse_contract_reader
    USING (public.app_can_dataset_permission(dataset_id, id, 'DISCOVER'));

DROP POLICY IF EXISTS profile_runs_view_policy ON profile_runs;
CREATE POLICY profile_runs_view_policy ON profile_runs
    FOR SELECT TO ridepulse_contract_reader
    USING (public.app_can_dataset_permission(dataset_id, dataset_version_id, 'VIEW_PROFILE'));

DROP POLICY IF EXISTS rule_snapshots_view_policy ON rule_review_snapshots;
CREATE POLICY rule_snapshots_view_policy ON rule_review_snapshots
    FOR SELECT TO ridepulse_contract_reader
    USING (public.app_can_dataset_permission(dataset_id, dataset_version_id, 'VIEW_REPORTS'));

DROP POLICY IF EXISTS analysis_summaries_view_policy ON analysis_summaries;
CREATE POLICY analysis_summaries_view_policy ON analysis_summaries
    FOR SELECT TO ridepulse_contract_reader
    USING (public.app_can_dataset_permission(dataset_id, dataset_version_id, 'VIEW_REPORTS'));

DROP POLICY IF EXISTS governed_artifacts_view_policy ON governed_artifacts;
CREATE POLICY governed_artifacts_view_policy ON governed_artifacts
    FOR SELECT TO ridepulse_contract_reader
    USING (public.app_can_dataset_permission(dataset_id, dataset_version_id, 'VIEW_REPORTS'));

DROP POLICY IF EXISTS governance_audit_view_policy ON governance_audit_events;
CREATE POLICY governance_audit_view_policy ON governance_audit_events
    FOR SELECT TO ridepulse_contract_reader
    USING (
        (
            dataset_id IS NOT NULL
            AND public.app_can_dataset_permission(dataset_id, dataset_version_id, 'DISCOVER')
        )
        OR (
            dataset_id IS NULL
            AND actor_id = NULLIF(current_setting('app.user_id', TRUE), '')
        )
    );

CREATE OR REPLACE FUNCTION public.prevent_governance_audit_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'governance audit events are append-only';
END;
$$;

DROP TRIGGER IF EXISTS governance_audit_append_only ON governance_audit_events;
CREATE TRIGGER governance_audit_append_only
    BEFORE UPDATE OR DELETE ON governance_audit_events
    FOR EACH ROW EXECUTE FUNCTION public.prevent_governance_audit_mutation();
