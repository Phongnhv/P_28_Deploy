# RidePulse DQ — Gate 2 backlog

> The repository is a starter template. This backlog is the only active Gate 2 work
> list; detailed acceptance evidence is in [gate2-mvp/TEAM_PLAN.md](./gate2-mvp/TEAM_PLAN.md).

| Order | Work item | Dependency |
|---:|---|---|
| 1 | Cloud/Vercel environment baseline | Provider owners and billing enabled |
| 2 | Deterministic 50k artifact + manifest | Approved NYC source |
| 3 | PostgreSQL schema, roles and migration | 1 |
| 4 | dbt Core transform/tests | 2, 3 |
| 5 | Generic jobs and Cloud Run Job dispatch | 1, 3 |
| 6 | Private ingest/profile API | 2–5 |
| 7 | Guarded real-LLM proposal | 6 |
| 8 | HITL, compiler and read-only runner | 3, 7 |
| 9 | Access/dataset/profile UI | 5, 6 |
| 10 | Review/results/audit UI | 7–9 |
| 11 | Hosted smoke, export and runbook | 1–10 |
| 12 | Five evaluations, architecture and video | 1–11 |

Each item is one reviewable PR with one owner and another reviewer. At least the first
ten must merge to `main`; do not merge placeholder behavior merely to increase PR count.
