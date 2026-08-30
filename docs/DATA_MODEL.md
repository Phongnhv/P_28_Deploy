# DataPulse — current data model

> This document describes the SQLAlchemy model and the versioned workflow
> currently used by the application. Ordered SQL migrations in
> `scripts/migrations/` remain the release mechanism for an existing Supabase
> database.

## Core entities

| Entity | Purpose | Essential fields |
|---|---|---|
| `user_accounts` / `sessions` | Authentication and role context | username, role, status, opaque session, expiry, CSRF token |
| `workspaces` / `workspace_memberships` | Tenant boundary | workspace, user, role, active status |
| `datasets` | Logical dataset catalog | ID, name, status, checksum and source metadata |
| `dataset_versions` | Immutable uploaded snapshots | workspace, dataset, version, checksum, schema hash, row count, artifact metadata |
| `dataset_governance` / `dataset_grants` | Dataset authorization | owner, visibility, version scope, permission, expiry/revocation |
| `profile_runs` | Version-scoped profiling snapshot | schema, aggregate metrics, sanitized samples, status and completion time |
| `graph1_runs` / `graph1_node_executions` | Durable profiling/semantic/rule workflow | version/profile context, current node, status, safe node output |
| `rule_proposals` / `rule_review_snapshots` | Typed rules awaiting steward review | rule spec, evidence refs, status, approved version context |
| `analysis_runs` / `analysis_node_executions` | Durable Graph 2/3 execution | model metadata, node status, latency and errors |
| `dq_runs` / `dq_results` | Rule execution outcomes | run, rule, pass/fail/error, bounded failed-row IDs |
| `analysis_summaries` / `governed_artifacts` | Persisted dashboard/report outputs | version, summary JSON, report locator and checksum |
| `jobs` | Shared async lifecycle | type, status, idempotency key, lease, attempts and safe error |
| `audit_events` / `governance_audit_events` | Operational and governance trail | actor, action, entity, workspace, correlation and timestamp |

Source CSV/Parquet content is stored as a versioned object artifact. The
application does not overwrite another dataset's source rows during profiling
or rule execution. Compatibility tables and routes may remain for older local
or taxi-oriented runs, but they are not the canonical generic-upload model.

## Workflow links

```text
workspace
  → dataset → dataset_version → profile_run
                         → graph1_run → semantic/rule review
                                      → analysis_run (Graph 2 + Graph 3)
                                      → analysis_summary + governed report
```

`jobs` stores the transport/lifecycle record for durable work. A job may link to
an ingestion, Graph 1 or analysis run; the domain run stores the version-scoped
business state and node outputs.

## Roles and data boundaries

- `USER` can read data granted to the user.
- `STEWARD` can profile datasets, review typed rules and run approved checks.
- `ADMIN` provisions users and manages dataset access.
- An optional `demo-steward` account is seeded only when public demo mode is
  explicitly enabled with an operator-provided password in non-production.

The agent receives an allow-listed aggregate profile and semantic contract, not
raw rows, private object keys, credentials or arbitrary browser prompts. DQ
results expose counts and bounded identifiers rather than complete failed-row
values. Public API responses use the session/workspace authorization context
before returning profiles, versions, rules, results or reports.

## Persistence and deployment

Production `DATABASE_URL` and `SUPABASE_DATABASE_URL` point to the same Supabase
PostgreSQL target so API and Cloud Run worker observe one durable state. Local
tests may use SQLite. Source, dbt and report artifacts use the configured object
storage adapter (GCS in production, MinIO/S3-compatible storage locally).

Demo quota reservations are stored as `audit_events` with a dedicated internal
entity type and are excluded from the user-facing audit-log list. The reservation
is written before expensive upload/profiling/analysis work, which prevents failed
retries from bypassing the public demo budget.
