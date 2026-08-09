# RidePulse DQ — Gate 2 implementation plan

> **Status:** Proposed / not implemented.
>
> **Canonical detail:** [docs/gate2-mvp/](./gate2-mvp/README.md)

## Fixed architecture

```text
Vercel React/Vite → Cloud Run FastAPI API → Cloud Run Job (dbt + pipeline)
                                               ↓
                               Supabase PostgreSQL + private Storage
                                               ↓
                                           OpenAI API
```

This is deliberately a production-like course simulation. It uses cloud deployment,
secrets, separate database roles, persisted work and repeatable evidence, but does not
promise production availability or enterprise controls.

## Delivery sequence

1. Provision provider accounts/secrets, data manifest and database migrations.
2. Build deterministic 50k artifact and dbt transform/test stage.
3. Implement generic persisted jobs, Cloud Run dispatch, ingestion and profile.
4. Implement guarded Agent, HITL, typed compiler and read-only runner.
5. Implement Vercel UI against the public Cloud Run API.
6. Run public smoke, five real-LLM evaluations, backup export and video rehearsal.

Use [TEAM_PLAN.md](./gate2-mvp/TEAM_PLAN.md) as the mergeable 12-PR backlog and
[IMPLEMENTATION_GUIDE.md](./gate2-mvp/IMPLEMENTATION_GUIDE.md) for contracts,
guardrails and verification. Do not add Dagster, a second host, new dataset or new DE
tool without revising this decision.
