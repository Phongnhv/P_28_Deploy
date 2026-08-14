h P

# Gate 2 setup and hosting plan

This is a course-project deployment checklist, not a production runbook. One teammate
owns each provider account and invites the other teammates through provider access
controls; credentials are never committed or pasted into chat.

## 1. Accounts to create

| Provider     | Used for                                                                | Owner must configure                                                   |
| ------------ | ----------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| Vercel       | React/Vite frontend and preview URLs                                    | Git repository, production branch and`VITE_API_BASE_URL`             |
| Google Cloud | Artifact Registry, Cloud Run API service, Cloud Run Job, Secret Manager | Billing account, project, region and least-privilege service accounts  |
| Supabase     | PostgreSQL and private artifact bucket                                  | Database project,`ridepulse-gate2` private bucket and database roles |
| OpenAI       | Live proposal generation                                                | Valid project API key and a small spend cap                            |

Google Cloud billing must be enabled before Cloud Run can be used. The team sets a
budget alert and keeps one region for the API and Job. Vercel and Supabase use their
free plans for this short course submission; the owner keeps a manual export before
the final rehearsal.

## 2. Environments and secrets

Local `.env` is developer-only:

```dotenv
APP_ENV=development
DATABASE_URL=postgresql+psycopg://ridepulse_app:<password>@postgres:5432/ridepulse
RUNNER_DATABASE_URL=postgresql+psycopg://ridepulse_runner:<password>@postgres:5432/ridepulse
OPENAI_API_KEY=<valid-key>
MODEL_NAME=<approved-model>
DEMO_ACCESS_PASSWORD=<local-password>
FRONTEND_ORIGIN=http://localhost:5173
```

Vercel receives only this public build setting:

```dotenv
VITE_API_BASE_URL=https://<cloud-run-api-url>
```

Google Secret Manager supplies the API service and Cloud Run Job with
`DATABASE_URL`, `RUNNER_DATABASE_URL`, `DBT_DATABASE_URL`, `OPENAI_API_KEY`,
`DEMO_ACCESS_PASSWORD`,
`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` and `DATA_ARTIFACT_BUCKET`. Non-secret
Cloud Run configuration includes `APP_ENV=production`, `MODEL_NAME`,
`FRONTEND_ORIGIN`, Cloud Run Job name and log level.

`RUNNER_DATABASE_URL` is a separate read-only database credential and
`DBT_DATABASE_URL` is limited to the dbt transform schema. The Vercel build must never
receive any value other than the API base URL.

## 3. Public deployment sequence

1. Create the Supabase project, private bucket and database roles. Upload no data yet.
2. Create Google Cloud project, Artifact Registry, API service account and Job service
   account. The API identity may invoke only this one Job; the Job has no browser URL.
3. Build one backend container image. Deploy it once as the FastAPI Cloud Run Service
   and once as the pipeline Cloud Run Job with different commands.
4. Run the migration command as a controlled one-off release step, then deploy API and
   Job with the secrets above.
5. Generate the 50k Parquet artifact, validate its manifest/checksum and upload it to
   private Supabase Storage.
6. Deploy the Vite frontend on Vercel with the Cloud Run API URL. Set the exact Vercel
   production origin in the API CORS configuration.
7. Run the public smoke test in incognito and from a separate network.

The API creates a database job then asks Cloud Run to execute the Job. The browser polls
the API only; it does not wait for a long-running HTTP request.

## 4. Minimum course-level protection

- The API exposes HTTPS only through Cloud Run. Cloud Run and Supabase credentials are
  stored in Secret Manager, not source code or Vercel.
- A signed opaque session cookie is `Secure`, `HttpOnly` and cross-origin safe. Browser
  state-changing calls send the required CSRF header; CORS permits only the production
  Vercel origin.
- The shared demo password is compared server-side. Proposal and DQ-run calls have a
  small per-session and global daily quota to cap OpenAI usage.
- `migration`, `app`, `dbt` and `runner` database roles have separate responsibilities;
  the runner can issue only approved read-only checks.
- Before the last rehearsal, export PostgreSQL with `pg_dump` and download the private
  artifact. This is a manual course safeguard, not managed production backup.

## 5. Local verification after each relevant PR

```powershell
docker compose config
.\.venv\Scripts\python.exe -m pytest tests -v
.\.venv\Scripts\python.exe -m ruff check src tests
npm --prefix frontend run build
```

Once dbt is added, its PR also runs `dbt parse` and a database-backed `dbt build` test.
Automated tests mock OpenAI. Only the five documented manual evaluation cases call the
real model.
