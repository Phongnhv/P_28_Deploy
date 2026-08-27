-- 012_backfill_legacy_governance.sql
--
-- Safe compatibility backfill for logical datasets that already have an
-- explicit steward ownership/access contract.  This migration deliberately
-- does NOT fabricate a READY dataset version from legacy profile metadata:
-- an immutable SOURCE_DATASET artifact and checksum are required first.
--
-- Run after ORM/governance tables exist and before optional schema relocation.
-- Re-running is harmless.  No user rows are deleted or rewritten.

\set ON_ERROR_STOP on

BEGIN;

DO $$
BEGIN
    IF to_regclass('public.dataset_governance') IS NOT NULL
       AND to_regclass('public.datasets') IS NOT NULL
       AND to_regclass('public.user_accounts') IS NOT NULL
       AND to_regclass('public.dataset_access') IS NOT NULL
       AND to_regclass('public.workspace_memberships') IS NOT NULL THEN
        INSERT INTO public.dataset_governance (
            dataset_id, workspace_id, owner_user_id, visibility, created_at, updated_at
        )
        SELECT
            dataset.id,
            workspace_choice.workspace_id,
            owner.id,
            'PRIVATE',
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        FROM public.datasets AS dataset
        JOIN public.user_accounts AS owner
          ON owner.username = 'steward'
        JOIN public.dataset_access AS access_row
          ON access_row.dataset_id = dataset.id
         AND access_row.username = owner.username
         AND access_row.access_level = 'MANAGE'
        CROSS JOIN LATERAL (
            SELECT membership.workspace_id
            FROM public.workspace_memberships AS membership
            WHERE membership.user_id = owner.id
              AND membership.status = 'ACTIVE'
              AND membership.role IN ('ADMIN', 'STEWARD')
            ORDER BY membership.workspace_id
            LIMIT 1
        ) AS workspace_choice
        WHERE NOT EXISTS (
            SELECT 1
            FROM public.dataset_governance AS existing
            WHERE existing.dataset_id = dataset.id
        )
        ON CONFLICT (dataset_id) DO NOTHING;
    END IF;
END
$$;

-- A legacy dataset is intentionally left without a version when no verified
-- SOURCE_DATASET artifact already exists.  The canonical import endpoint is
-- the only path that creates the version + artifact atomically.

COMMIT;

-- Rollback: no automatic DELETE is provided because the inserted governance
-- rows may be adopted by an operator after this migration.  If this migration
-- was run in an empty disposable database, remove only rows created by the
-- explicitly chosen steward/workspace after verifying ownership manually.
