# Gate 2 architecture diagram

This diagram is a course-project architecture: it demonstrates security and operational
boundaries without claiming production availability or enterprise governance.

```mermaid
flowchart TB
    Browser["Data Steward browser"] -->|"HTTPS + credentials"| Vercel["Vercel\nReact/Vite UI"]
    Vercel -->|"HTTPS /api/v1\nexact CORS origin"| API["Google Cloud Run Service\nFastAPI"]
    API -->|"write/read"| PG[("Supabase PostgreSQL")]
    API -->|"invoke persisted job ID"| Worker["Google Cloud Run Job\nPython worker + dbt Core"]
    Worker -->|"claim/update job, profile, rules, results"| PG
    Worker -->|"read approved artifact"| Bucket["Supabase Storage\nprivate bucket"]
    Worker -->|"dbt build"| DBT["analytics schema\nstaging + profile input + tests"]
    DBT --> PG
    Worker -->|"aggregate evidence only"| OpenAI["OpenAI"]
    API -->|"Secret Manager references"| Secrets["Google Secret Manager"]
    Worker -->|"Secret Manager references"| Secrets
```

## Data flow

1. Team generates and checks the private 50k Parquet artifact once.
2. Steward starts a persisted `INGEST_PROFILE` job via the UI.
3. Cloud Run Job validates and ingests data, runs dbt, and stores profile evidence.
4. A separate proposal job sends only aggregate evidence to OpenAI.
5. Human approval creates a typed rule; a run job evaluates it through the read-only
   runner and stores bounded results.

## Trust boundaries

- Browser: only Vercel UI and Cloud Run API; no direct provider credentials.
- API: validates session/CSRF/quota and dispatches only known job types.
- Worker: accesses private artifact, dbt and OpenAI; it never trusts browser SQL/path.
- Database: distinct migration, application, dbt and read-only runner roles.
- Secrets: only Secret Manager and local ignored `.env`; Vercel gets public API URL only.
