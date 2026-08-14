# RidePulse DQ — API contract

> **Status:** The local MVP implements the dashboard endpoints below with FastAPI,
> SQLite and a fixed server-side NYC artifact. The dashboard contract is the only
> product-facing API. Proposal generation is routed through a dashboard-to-agent
> adapter: `AGENT_MODE=mock` supplies deterministic local fixtures; `AGENT_MODE=graph`
> invokes the structured LangGraph proposer with persisted aggregate evidence only.

## Common rules

- Product API base is `/api/v1`; `/health` is liveness and `/ready` is hosted readiness.
- JSON errors use `{ "code": "STABLE_CODE", "message": "safe message", "request_id": "uuid" }`.
- State-changing calls require the demo session and CSRF header. Browser never submits
  a filesystem path, URL, artifact content, SQL text or LLM prompt.
- The agent receives only allow-listed aggregate profile evidence. It cannot receive
  raw rows, sample values, source identifiers, connection strings or browser text.
  The allow-list includes full-table negative rates, numeric quantiles, governed-domain
  violation rates, configured cross-field violation rates and verified uniqueness
  aggregates. Metric definitions and the live eval record are documented in
  [AGENT_IMPROVEMENTS_AND_EVAL.md](./AGENT_IMPROVEMENTS_AND_EVAL.md).
  For `AGENT_MODE=graph`, the backend first creates a small deterministic candidate
  set (required identifier, evidence-backed non-negative measure, governed enum and
  pickup/dropoff ordering when present). The model returns one steward-facing explanation
  for each curated candidate but cannot invent thresholds, values, columns or dashboard
  rule types; normalisation permits at most one proposal per dashboard rule type. If the
  model omits a candidate but returns at least one valid candidate, a deterministic policy
  fallback completes only the already verified missing candidate(s).
- Create-work endpoints return `202` with `{ "job_id": "uuid", "status": "PENDING" }`.
- Poll `GET /api/v1/jobs/{job_id}`; active duplicate work returns `409`, quota `429`,
  invalid input `422` and missing/unauthenticated session `401`.

## Implemented local endpoints

| Method | Path | Request boundary | Success |
|---|---|---|---|
| POST | `/api/v1/session` | Shared password | Secure session + CSRF token |
| DELETE | `/api/v1/session` | Current session | `204` |
| GET | `/api/v1/datasets` | None | Registered dataset list/readiness |
| POST | `/api/v1/datasets/{dataset_id}/ingestions` | Idempotency key | Ingest/profile `job_id` |
| GET | `/api/v1/jobs/{job_id}` | Current session | Status, safe progress and retry guidance |
| GET | `/api/v1/datasets/{dataset_id}/profile` | Completed dataset | Aggregate profile only |
| POST | `/api/v1/datasets/{dataset_id}/rule-proposals` | Idempotency key | Proposal `job_id` |
| GET | `/api/v1/rule-proposals` | Dataset filter, bounded limit | Typed proposals |
| PATCH | `/api/v1/rule-proposals/{proposal_id}` | Approve/edit/reject typed fields | Updated proposal/rule state |
| DELETE | `/api/v1/rule-proposals/{proposal_id}` | Unapproved/rejected proposal | `204`; audit event remains |
| GET | `/api/v1/rule-configurations` | Dataset filter | Execution configuration for approved rules |
| PATCH | `/api/v1/rule-proposals/{proposal_id}/configuration` | Active/paused and schedule fields | Updated execution configuration |
| POST | `/api/v1/dq-runs` | Approved rule IDs + idempotency key | DQ `job_id` and `run_id` |
| GET | `/api/v1/dq-runs/{run_id}` | Current session | Run status/summary |
| GET | `/api/v1/dq-runs/{run_id}/results` | Current session | Counts and max 20 failed IDs, no raw values |
| GET | `/api/v1/audit-logs` | Bounded filters/limit | Audit list |
| GET/POST/PATCH | `/api/v1/admin/users` and `/{username}` | Admin session | Local account directory and provisioning |
| GET/PUT/DELETE | `/api/v1/admin/datasets/{dataset_id}/access` | Admin session | Read/manage dataset grants |
| GET | `/health`, `/ready` | None | Liveness/readiness |

## State rules

Jobs use `PENDING`, `RUNNING`, `SUCCEEDED`, `FAILED_RETRYABLE` or `FAILED`.
Proposals (including manual rules) start `PROPOSED`; they may be approved, edited or
rejected only through the review state machine. Approval creates an active manual
configuration. Paused rules cannot be included in a DQ run. Only the resulting approved
typed rule version can be included in a DQ run. The compiled SQL is not a public API field.
Dashboard DQ execution uses the fixed typed-rule compiler, not the legacy graph's
free-form SQL repair loop. Legacy `/api/v1/dq/*` endpoints remain compatibility/internal
routes and are not consumed by `frontend/src/api/client.ts`.

Before implementing an endpoint, add its Pydantic request/response schemas, happy-path
and failure-path API tests, and any newly public error code to this document.
