# DataPulse — Gate 2 product specification

> **Status:** Implemented Gate 2 MVP. This document describes the current
> versioned workflow; historical planning documents are not normative.
>
> **Canonical Gate 2 plan:** [gate2-mvp/README.md](./gate2-mvp/README.md)

## Product objective

DataPulse helps a Data Steward upload a CSV/Parquet dataset, inspect its immutable
version and profile, receive evidence-grounded data-quality rule proposals, review
those proposals, and run only approved rules. The versioned path supports generic
datasets and is not limited to the NYC Yellow Taxi fixture. Gate 2 is a public,
end-to-end course-project simulation of production practices.

## Primary user journey

1. Steward signs in through the prefilled, quota-bounded demo account on the public Vercel URL.
2. Steward uploads a CSV/Parquet file into the configured workspace.
3. The API creates an immutable dataset version and durable profiling job.
4. Graph 1 profiles the version, derives semantic evidence, pauses for review,
   and proposes typed rules; OpenAI receives aggregate evidence only when
   `AGENT_MODE=graph` is enabled.
5. Steward confirms semantics and approves, edits or rejects each rule.
6. Graph 2 executes approved rules; Graph 3 detects signals, writes hypotheses
   when evidence is sufficient, and persists the Steward report.

## Must-have scope

- Vercel React/Vite UI, Google Cloud Run API and Cloud Run Job.
- Supabase PostgreSQL plus private object-storage artifacts; uploaded versions
  are generic and dataset-scoped.
- dbt Core as a fixed transformation/test stage.
- OpenAI structured proposal, human-in-the-loop review and five typed rule templates.
- Safe read-only runner, durable job persistence, workspace authorization,
  bounded demo access/quota, tests, architecture evidence and browser E2E.
- Validated CSV/Parquet file upload through the authenticated import endpoints,
  with content-type and size limits enforced server-side.
- Server-sent-event progress streams for graph runs, so a reloaded browser can
  recover a run in flight.

## Explicitly outside Gate 2

- Arbitrary URL/path input, arbitrary SQL and source-data mutation. Note that
  *validated* file upload and *server-sent-event* progress streaming were adopted
  deliberately and are listed under must-have scope above; only unconstrained
  URL/path input remains out of scope.
- Dagster, Firebase, Render, VPS hosting, scheduler, ML anomaly model, RAG, dbt Cloud,
  enterprise account/RBAC, HA/SLA and post-course operations.

## Safety rules

1. Raw rows are immutable through the application flow.
2. The LLM receives aggregate evidence, never raw rows or free-form browser prompts.
3. Only an approved typed rule can compile and run.
4. The runner uses a separate read-only credential and bounded result IDs.
5. All state transitions and executions create an audit record.
6. Public errors and logs do not expose backend secrets, raw rows or internal traces.

The optional judge-facing credential is configured by the operator as
`demo-steward` plus `DEMO_STEWARD_PASSWORD` in non-production only. The backend limits this account to 40
write mutations, 3 uploads, 3 profiling starts and 2 analysis starts per rolling
24-hour window; read-only polling is not charged.

The release criteria, hosting details, API paths, data entities, team work and test
evidence are defined in the linked Gate 2 documents and companion contracts.
