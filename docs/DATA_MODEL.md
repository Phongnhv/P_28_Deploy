# RidePulse DQ — Gate 2 data model

> **Status:** Proposed schema. It becomes current behavior only through reviewed
> SQLAlchemy/Alembic migrations.

## Core entities

| Entity | Purpose | Essential fields |
|---|---|---|
| `datasets` | One registered artifact | ID, manifest version, checksum, artifact key, readiness |
| `jobs` | Common async lifecycle | ID, type, status, idempotency key, lease, attempt, correlation ID, safe error |
| `trips_raw` | Immutable 50k source-shaped rows | `source_row_id`, dataset ID, fixed typed columns |
| `dataset_profiles` / `column_profiles` | Persisted aggregate evidence | dataset/version, aggregate JSON, stable evidence keys |
| `rule_proposals` | Agent output awaiting review | typed spec, evidence refs, status, model metadata |
| `dq_rules` | Immutable approved rule version | proposal origin, typed spec, compiled version/state |
| `dq_runs` / `dq_results` | Executions and bounded outcomes | run ID, rule ID, counts, status, at most 20 IDs |
| `sessions` / `rate_limit_events` | Demo access and cost control | opaque session, expiry, action/time counters |
| `audit_logs` | Append-only observable history | actor, action, entity, before/after metadata, timestamp |

All primary keys are UUIDs; timestamps are UTC. `source_row_id` is deterministic from
the approved source and sample row position, and remains unique even when business
fingerprints are deliberately duplicated.

## States and links

```text
dataset → jobs(INGEST_PROFILE) → profile → jobs(PROPOSE_RULES) → proposals
                                                    ↓ review
                                                dq_rules → jobs(RUN_DQ) → dq_results
```

`jobs` owns the shared asynchronous status:
`PENDING → RUNNING → SUCCEEDED | FAILED_RETRYABLE | FAILED`. `dq_runs` holds the
business execution record linked to its job; it does not invent a second job state.

## Roles and schemas

- `migration` role changes schema only during controlled release.
- `app` role writes application state and raw ingestion records.
- `dbt` role reads raw data and writes only the `analytics` transform schema.
- `runner` role is read-only for explicitly allow-listed DQ query tables.

Raw rows, private artifact object keys, credentials and full failed-row values never
enter LLM payloads or public API responses.
