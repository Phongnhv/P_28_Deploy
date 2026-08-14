# Local MVP Backend — remaining work

> **Scope:** Gate 2 MVP running locally. The deployment-specific Cloud Run, Vercel,
> Supabase Storage and hosted PostgreSQL work is intentionally deferred. The MVP must
> still prove one usable Steward flow with a fixed NYC Yellow Taxi artifact and
> persistent local state.

## 1. Completion target

The local backend is complete when a browser can perform this flow against one
FastAPI process and local persistence:

```text
session -> dataset -> ingest/profile job -> proposal job -> HITL review
        -> approved-rule run -> bounded results + audit history
```

The browser must never supply a file path, URL, SQL statement, artifact content or
LLM prompt. Raw source rows remain immutable.

## 2. Current integration

The public dashboard API owns `RuleProposalModel`, `RuleVersionModel`, `DqRunModel`
and `DqResultModel`. Proposal graph output is mapped into those models by the dashboard
agent adapter; the dashboard's existing typed-rule compiler writes the DQ models
directly. The legacy `/api/v1/dq/*` and `rule_store` run/test-run models remain
compatibility/agent-internal and are not read by the frontend.

## 3. P0: required for an end-to-end local MVP

### 3.1 Adopt one public API contract — implemented

Use the product API consumed by `frontend/src/api/client.ts` as the local MVP boundary:

| Capability | Required endpoint |
|---|---|
| Session | `POST` / `DELETE /api/v1/session` |
| Workspace | `GET /api/v1/datasets` |
| Analysis | `POST /api/v1/datasets/{id}/ingestions`, `GET /api/v1/jobs/{id}`, `GET /api/v1/datasets/{id}/profile` |
| Proposals | `POST /api/v1/datasets/{id}/rule-proposals`, `GET/PATCH /api/v1/rule-proposals` |
| Execution | `POST /api/v1/dq-runs`, `GET /api/v1/dq-runs/{id}`, `GET /api/v1/dq-runs/{id}/results` |
| Audit | `GET /api/v1/audit-logs` |

Do not require the frontend to call the current `/api/v1/dq/*` proposal/test-run
endpoints directly. Those routes may become internal services or compatibility
adapters, but the dashboard must have one contract.

### 3.2 Implement a single local state model — public workflow implemented; legacy cleanup remains

Choose one persistence layer and migrate all workflow operations to it. Required
local records are:

- registered dataset and immutable source rows;
- one common `jobs` record for `INGEST_PROFILE`, `PROPOSE_RULES`, and `RUN_DQ`;
- aggregate dataset/column profiles;
- rule proposals, approved immutable rule versions, and DQ runs/results;
- sessions and append-only audit events.

The current `rule_store` proposal/test runs and the workspace-oriented model from the
testing branch must not become separate sources of truth for the same rule or result.

### 3.3 Provide session and write protection

Port the minimal local session behaviour from `codex/personal-local-testing`:

- a seeded demo Steward credential stored only in configuration;
- HTTP-only session cookie plus CSRF token for every state-changing request;
- `USER` is read-only and `STEWARD` can start jobs/review/run rules;
- session expiry, logout, and stable `401`, `403`, and `CSRF_INVALID` responses.

Admin provisioning, dataset grants, and enterprise RBAC are not required for the
local MVP.

### 3.4 Make jobs match the UI lifecycle

Every create-work endpoint must accept an idempotency key and return `202` with a
`job_id` and `PENDING` status. The UI polls the common job endpoint. A local
background task or local worker is sufficient; Cloud Run dispatch is not required.

Required behaviour:

- state transition: `PENDING -> RUNNING -> SUCCEEDED | FAILED_RETRYABLE | FAILED`;
- duplicate active work returns `409` without creating another run;
- terminal job exposes safe progress/error guidance only;
- a failure creates an audit event and does not leave a run permanently running.

### 3.5 Wire dataset ingestion and profile

Use a fixed server-side NYC artifact. Ingestion must:

1. validate the local manifest/checksum/schema and expected row count;
2. load immutable rows without accepting a browser-controlled location;
3. compute and persist aggregate profile evidence;
4. make profile visible only after the ingest/profile job succeeds.

dbt and cloud artifact storage may be omitted in this local phase, but the boundary
must allow the later pipeline stage to replace the adapter without changing the API.

### 3.6 Connect HITL and execution to the dashboard — implemented

The new proposal/execution services on `main` need adapters behind the dashboard API:

- proposal requests invoke the guarded proposal pipeline after profile completion;
- list/review exposes dashboard `RuleProposal` fields and supports approve, edit,
  reject, manual rule creation, and safe deletion;
- only approved typed rules enter a DQ run;
- DQ results return counts and at most 20 failed identifiers, never raw values or
  generated SQL;
- all job, proposal, review, and execution transitions append an audit event.

## 4. P1: safety and quality before calling the MVP reliable

### 4.1 Stable error mapping

All public failures must use:

```json
{"code":"STABLE_CODE","message":"safe message","request_id":"uuid"}
```

Do not leak Python exception text, SQL, database paths, LLM payloads or stack traces.

### 4.2 Typed-rule compiler boundary

The backend must compile only these templates: `not_null`, `numeric_range`,
`accepted_values`, `cross_field_comparison`, and `duplicate_fingerprint`. It resolves
identifiers from dataset metadata and parameterizes values. Neither the browser nor
the LLM may submit executable SQL.

### 4.3 Local runner guardrails

Even on SQLite, enforce a read-only execution boundary:

- one `SELECT` statement only;
- reject DDL/DML, comments and multiple statements;
- allow-list tables, columns and supported rule templates;
- enforce a statement timeout where supported;
- limit failed IDs to 20 and omit raw values.

### 4.4 Test and lint baseline

Add/restore tests covering login/CSRF, job idempotency, profile privacy, proposal
review transitions, every rule template, runner boundaries, results and audit. The
current `ruff check src/ tests/` failures on `main` must be resolved. Test setup must
use a writable repository-local temporary directory so the suite does not depend on
the inaccessible user Temp directory.

## 5. Explicitly deferred from this document

- Cloud Run, Vercel, Supabase, private object storage, hosted smoke tests and backup;
- dbt as a real external transform stage;
- scheduler, quota/rate-limit persistence, job lease recovery across hosts;
- admin account management and dataset grants;
- anomaly dashboard, diagnosis/recommendation UI, and ML anomaly scoring.

These may be added later without changing the local MVP contract.

## 6. Definition of done

- `VITE_USE_MOCK_API=false` frontend completes the target flow against FastAPI.
- The local API and frontend TypeScript types have no undocumented divergence.
- Proposal, review, DQ execution, result retrieval and audit state persist across a
  process restart.
- Unit and integration tests cover both happy and failure paths, and Ruff passes.
- A short local smoke command demonstrates the flow using only server-side dataset
  configuration and safe demo credentials.
