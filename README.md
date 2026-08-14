# RidePulse DQ

RidePulse DQ is a Gate 2 course project that simulates a production-style
data-quality workflow for a registered NYC Yellow Taxi dataset. The current repository
is a starter template; the complete target and work sequence are in
[docs/gate2-mvp/README.md](./docs/gate2-mvp/README.md).

## Target hosted architecture

```text
Vercel React/Vite → Google Cloud Run API/Job → Supabase PostgreSQL + private Storage
                                      ↓
                                 dbt Core + OpenAI
```

This is not a claim that the current starter endpoints implement the target yet.

## Local prerequisites for implementation

- Python 3.11+, Node.js LTS, Docker Desktop with Compose v2, Git.
- A valid OpenAI project key for manual evaluation only.
- Provider access for Vercel, Google Cloud and Supabase.

Copy `.env.example` to `.env` after the corresponding implementation PR adds it. The
planned environment names are `DATABASE_URL`, `RUNNER_DATABASE_URL`,
`DBT_DATABASE_URL`, `OPENAI_API_KEY`, `MODEL_NAME`, `DEMO_ACCESS_PASSWORD` and
`FRONTEND_ORIGIN`. Secrets never belong in Git or Vercel frontend variables.

## Planned user/API requests

After the relevant PRs merge, the user flow calls:

```text
POST /api/v1/session
GET  /api/v1/datasets
POST /api/v1/datasets/{dataset_id}/ingestions
GET  /api/v1/jobs/{job_id}
POST /api/v1/datasets/{dataset_id}/rule-proposals
PATCH /api/v1/rule-proposals/{proposal_id}
POST /api/v1/dq-runs
GET  /api/v1/dq-runs/{run_id}/results
```

See [API contract](./docs/API_CONTRACT.md), [setup plan](./docs/gate2-mvp/SETUP.md), [team plan](./docs/gate2-mvp/TEAM_PLAN.md) and [E1-E5 Evaluation Report](./eval/results/E1_E5_EVALUATION.md). Current starter endpoints are documented
separately in the API contract and must not be used as evidence for Gate 2 completion.

