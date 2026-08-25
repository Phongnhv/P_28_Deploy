# TODO Artifact — Database & Authorization Redesign

Status: `BACKLOG`
Priority: `HIGH` after the current demo/release
Owner: `TBD`

## Why this is needed

The current demo works end to end, but local and production can connect to the
same Supabase control-plane database. Browser sessions provide authentication
and roles, but they do not isolate workflow data by user, workspace, or dataset
version. This has already allowed two unsafe situations:

- a local startup could update production `system-seed` account passwords;
- rules generated from one uploaded dataset could be reused after the logical
  dataset slot pointed at different data.

The password overwrite is guarded in code now, and Graph 2 consumes the Graph 1
snapshot. Those are release fixes, not a replacement for database isolation.

## Target design

Introduce explicit ownership and immutable lineage:

```text
organization/workspace
  -> user membership + role
  -> dataset
  -> immutable dataset_version
  -> graph1_run
  -> approved_rule_snapshot
  -> analysis_run (Graph 2 + Graph 3)
  -> test/anomaly/report artifacts
```

Every workflow query and mutation must be scoped by workspace and the immutable
`dataset_version_id`. A session must authorize access; it must not determine
data identity implicitly.

## Implementation checklist

- [ ] Separate local/test and production databases and credentials.
- [ ] Add `organizations`/`workspaces` and membership tables.
- [ ] Add immutable `dataset_versions`; never overwrite an uploaded dataset in
      an existing logical slot.
- [ ] Add `workspace_id`, `dataset_version_id`, and `created_by` lineage to
      Graph 1, rule snapshots, Graph 2/3 runs, reports, and artifact metadata.
- [ ] Replace shared active-rule lookup with a run-scoped approved-rule
      snapshot referenced by foreign key.
- [ ] Enforce ownership and roles in API queries, not only in the frontend.
- [ ] Add Supabase/Postgres row-level security policies where applicable.
- [ ] Move demo-user seeding to an explicit deployment/migration command;
      application startup must never rotate production credentials.
- [ ] Define retention, cascade/delete, and audit-log policies.
- [ ] Review connection-pool and SSE lifecycle so long-lived streams do not
      exhaust Supabase connections.
- [ ] Write a backwards-compatible migration and data backfill plan.
- [ ] Add concurrency tests for two users, two tabs, and two dataset versions.
- [ ] Add authorization tests proving cross-workspace reads/writes return 403 or
      404 and cannot leak rules, reports, or artifacts.

## Acceptance criteria

1. Two users can run different uploaded datasets concurrently without sharing
   profiles, rules, executions, reports, or GCS artifact paths.
2. A Graph 2/3 run can execute only the approved snapshot and dataset version
   produced by its referenced Graph 1 run.
3. Starting a local backend cannot mutate production users or workflow state.
4. Every persisted result has traceable workspace, dataset version, run, actor,
   and artifact lineage.
5. Existing demo data is migrated without breaking the current production
   workflow.

## Out of scope for the current demo

Do not perform this redesign immediately before recording. The deployed Graph
1 -> Graph 2 -> Graph 3 workflow is currently operational; schedule this work
as a dedicated schema migration and authorization release.
