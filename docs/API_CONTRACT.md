# RidePulse DQ — API contract

> **Status:** Gate 2 endpoints below are proposed; only the starter `/health`,
> `/api/v1/status` and `/api/v1/chat` currently exist. Do not present proposed endpoints
> as implemented before their tests land.

## Common rules

- Product API base is `/api/v1`; `/health` is liveness and `/ready` is hosted readiness.
- JSON errors use `{ "code": "STABLE_CODE", "message": "safe message", "request_id": "uuid" }`.
- State-changing calls require the demo session and CSRF header. Browser never submits
  a filesystem path, URL, artifact content, SQL text or LLM prompt.
- Create-work endpoints return `202` with `{ "job_id": "uuid", "status": "PENDING" }`.
- Poll `GET /api/v1/jobs/{job_id}`; active duplicate work returns `409`, quota `429`,
  invalid input `422` and missing/unauthenticated session `401`.

## Planned endpoints

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
| POST | `/api/v1/dq-runs` | Approved rule IDs + idempotency key | DQ `job_id` and `run_id` |
| GET | `/api/v1/dq-runs/{run_id}` | Current session | Run status/summary |
| GET | `/api/v1/dq-runs/{run_id}/results` | Current session | Counts and max 20 failed IDs, no raw values |
| GET | `/api/v1/audit-logs` | Bounded filters/limit | Audit list |
| GET | `/health`, `/ready` | None | Liveness/readiness |

## State rules

Jobs use `PENDING`, `RUNNING`, `SUCCEEDED`, `FAILED_RETRYABLE` or `FAILED`.
Proposals start `PROPOSED`; they may be approved, edited or rejected only through the
review state machine. Only the resulting approved typed `dq_rule` can be included in a
DQ run. The compiled SQL is not a public API field.

Before implementing an endpoint, add its Pydantic request/response schemas, happy-path
and failure-path API tests, and any newly public error code to this document.
