# Gate 2 implementation guide and Agent guardrails

## 1. Component responsibilities

| Component | Responsibility |
|---|---|
| Vercel frontend | Access screen, dataset/profile, rule review, results/audit; renders API state only |
| Cloud Run API | Validates requests, owns session/CSRF/error mapping, creates jobs and returns API data |
| Cloud Run Job | Claims a job, runs ingest → dbt → profile or proposal/DQ work, persists outcome |
| Supabase PostgreSQL | System of record for datasets, jobs, profiles, rules, results, sessions and audit |
| Supabase Storage | Private approved 50k artifact only |
| dbt Core | Fixed staging/profile-input transform and data-contract tests |
| OpenAI/LangGraph | Proposes structured rules from approved aggregate evidence only |

Routes coordinate services; business logic stays in reusable services so the API,
Cloud Run Job and tests share exactly the same behavior.

## 2. Jobs and data lifecycle

All asynchronous actions use one `jobs` table. A job has a type
(`INGEST_PROFILE`, `PROPOSE_RULES` or `RUN_DQ`), idempotency key, linked entity,
correlation ID, attempt count and one status:

```text
PENDING → RUNNING → SUCCEEDED
                  ↘ FAILED_RETRYABLE → PENDING (manual retry only)
                  ↘ FAILED
```

The API creates a `PENDING` row and invokes Cloud Run Job with that job ID. The Job
claims the row under a lease, performs its allowed work and persists the final status.
Duplicate active requests return `409`; a stale lease becomes `FAILED_RETRYABLE`.
Cloud Run infrastructure does not silently replay paid LLM work.

`INGEST_PROFILE` reads the approved private artifact, validates manifest/checksum,
ingests immutable raw rows, runs `dbt build`, then persists aggregate profile evidence.
No browser path, URL, upload or arbitrary dbt command is accepted.

## 3. Database and DQ rules

Migrations create `datasets`, `jobs`, `trips_raw`, `dataset_profiles`,
`column_profiles`, `rule_proposals`, `dq_rules`, `dq_runs`, `dq_results`,
`audit_logs`, `sessions` and `rate_limit_events`.

The five supported rule types are `not_null`, `numeric_range`, `accepted_values`,
`cross_field_comparison` and `duplicate_fingerprint`. An approved typed rule becomes
an immutable `dq_rule`. The compiler resolves table/column identifiers from metadata
allow-lists and produces one parameterized `SELECT`; it never accepts SQL text from the
browser or LLM. The runner uses `RUNNER_DATABASE_URL`, statement timeout and a limit of
20 failed IDs without raw values.

## 4. Agent guardrails

| Boundary | Enforced rule |
|---|---|
| Browser input | No chat box or free prompt. User can only request proposals for a completed profile. |
| Dataset | One server-side manifest, fixed Parquet schema/checksum/50k count and private bucket. |
| Evidence | Allow-listed aggregate profile fields and stable evidence keys; never raw rows, IDs, location/time tuples or artifact manifest. |
| LLM request | Backend-only API key, approved model, bounded payload, 30-second timeout and no automatic paid retry. |
| LLM response | Parse 2–5 Pydantic-validated typed rules; reject unknown fields, oversized text, unsupported type/parameters or fabricated evidence. |
| HITL | All proposals start `PROPOSED`; only Steward approve/edit/reject transitions create executable rules and audit records. |
| Execution | Pending/rejected proposals never compile/run. Runner is read-only and DDL/DML, comments and multi-statements are rejected. |
| Abuse/logging | Password session, CSRF, quotas, correlation ID, redacted errors/logs; never return secrets or raw rows. |

The Agent recommends rules only. It cannot repair source data, choose deployment
settings, issue arbitrary database commands or mark its proposal approved.

## 5. Public API contract

All product endpoints are under `/api/v1`; `/health` and `/ready` remain outside the
versioned prefix for platform checks.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/session` | Validate shared password and start a session |
| DELETE | `/api/v1/session` | End current session |
| GET | `/api/v1/datasets` | List the single registered dataset and readiness |
| POST | `/api/v1/datasets/{dataset_id}/ingestions` | Start idempotent ingest/dbt/profile job |
| GET | `/api/v1/jobs/{job_id}` | Poll persisted job state/error guidance |
| GET | `/api/v1/datasets/{dataset_id}/profile` | Read completed aggregate profile |
| POST | `/api/v1/datasets/{dataset_id}/rule-proposals` | Start guarded real-LLM proposal job |
| GET/PATCH | `/api/v1/rule-proposals` and `/{proposal_id}` | List and review proposals |
| POST | `/api/v1/dq-runs` | Start approved-rule execution job |
| GET | `/api/v1/dq-runs/{run_id}` and `/results` | Read DQ job and bounded results |
| GET | `/api/v1/audit-logs` | Read limited audit history |
| GET | `/health`, `/ready` | Liveness and dependency readiness |

Every create-job endpoint returns `202` plus `job_id`; bad authentication is `401`,
duplicate active work is `409`, quota is `429`, and validation is `422`. Exact request
and response shapes live in `docs/API_CONTRACT.md` before routes are implemented.

## 6. Verification and Gate evidence

- Unit: generator determinism, dbt configuration, profile/evidence privacy, schemas,
  compiler, session/quota and state transitions.
- Integration: migration, 50k ingest, `dbt build`, read-only runner, job lease/retry
  and audit persistence against PostgreSQL.
- UI: authenticated public flow plus loading, empty, API failure, LLM failure and job
  retry states.
- Manual real-LLM: five valid profile-driven cases; each records aggregate input,
  model output, human decision, result/audit IDs and screenshot.
- Public smoke: hosted URL → session → ingest/profile → proposal → review → DQ result,
  run twice after a database export.
