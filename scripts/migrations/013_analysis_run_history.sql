-- Allow multiple durable Graph 2/3 analysis snapshots for one Graph 1 run.
-- The previous schema used a one-analysis-per-Graph-1 unique constraint,
-- which made a completed analysis impossible to rerun without destroying its
-- evidence.  This migration is safe to run more than once.

DO $$
DECLARE
    constraint_name text;
BEGIN
    SELECT con.conname
      INTO constraint_name
      FROM pg_constraint con
      JOIN pg_class rel ON rel.oid = con.conrelid
      JOIN pg_attribute att ON att.attrelid = rel.oid AND att.attnum = ANY(con.conkey)
     WHERE rel.relname = 'analysis_runs'
       AND con.contype = 'u'
       AND att.attname = 'graph1_run_id'
     LIMIT 1;

    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE analysis_runs DROP CONSTRAINT %I', constraint_name);
    END IF;
END $$;

-- Some deployments created the old uniqueness as a unique index rather than
-- a table constraint.  Drop that index too; IF NOT EXISTS alone would keep it
-- unique and still block reruns.
DO $$
DECLARE
    index_name text;
BEGIN
    SELECT idx.relname
      INTO index_name
      FROM pg_index ind
      JOIN pg_class idx ON idx.oid = ind.indexrelid
      JOIN pg_class tbl ON tbl.oid = ind.indrelid
      JOIN pg_attribute att ON att.attrelid = tbl.oid AND att.attnum = ind.indkey[0]
     WHERE tbl.relname = 'analysis_runs'
       AND ind.indisunique
       AND ind.indnkeyatts = 1
       AND att.attname = 'graph1_run_id'
     LIMIT 1;

    IF index_name IS NOT NULL THEN
        EXECUTE format('DROP INDEX IF EXISTS %I', index_name);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_analysis_runs_graph1_run_id
    ON analysis_runs (graph1_run_id);
