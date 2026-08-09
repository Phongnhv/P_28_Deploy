# RidePulse DQ — Gate 2 product specification

> **Status:** Proposed. The codebase remains a starter template.
>
> **Canonical Gate 2 plan:** [gate2-mvp/README.md](./gate2-mvp/README.md)

## Product objective

RidePulse DQ helps a Data Steward inspect a registered mobility dataset, receive
evidence-grounded data-quality rule proposals from a real LLM, review those proposals,
and run only approved rules. Gate 2 is a public, end-to-end **course-project simulation
of production practices**. It is not a production launch.

## Primary user journey

1. Steward signs in through a shared demo access gate on the public Vercel URL.
2. Steward starts analysis of the registered 50k NYC Yellow Taxi artifact.
3. Cloud Run Job ingests it, runs dbt transform/tests, and persists aggregate profile.
4. Steward requests rule proposals; OpenAI receives aggregate evidence only.
5. Steward approves, edits or rejects each proposal with an audit event.
6. Steward runs approved rules and reads persisted DQ results.

## Must-have scope

- Vercel React/Vite UI, Google Cloud Run API and Cloud Run Job.
- Supabase PostgreSQL plus private Storage artifact; no Chicago source.
- dbt Core as a fixed transformation/test stage.
- OpenAI structured proposal, human-in-the-loop review and five typed rule templates.
- Safe read-only runner, job persistence, basic access/quota, tests, 10+ merged PRs,
  five manual real-LLM cases, architecture diagram and video.

## Explicitly outside Gate 2

- Arbitrary upload/URL/path input, arbitrary SQL, source-data mutation and streaming.
- Dagster, Firebase, Render, VPS hosting, scheduler, ML anomaly model, RAG, dbt Cloud,
  enterprise account/RBAC, HA/SLA and post-course operations.

## Safety rules

1. Raw rows are immutable through the application flow.
2. The LLM receives aggregate evidence, never raw rows or free-form browser prompts.
3. Only an approved typed rule can compile and run.
4. The runner uses a separate read-only credential and bounded result IDs.
5. All state transitions and executions create an audit record.
6. Public errors and logs do not expose secrets, raw rows or internal traces.

The release criteria, hosting details, API paths, data entities, team work and test
evidence are defined in the linked Gate 2 documents and companion contracts.
