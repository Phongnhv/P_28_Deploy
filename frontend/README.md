# DataPulse frontend

Local React/Vite implementation for the Gate 2 Data Steward workflow.

## Run locally

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173` and sign in with one of the seeded local accounts:

- `user` / `user` — read-only dataset access
- `steward` / `steward` — profile, proposal review, and rule configuration
- `admin` / `admin` — user provisioning and dataset-access administration

For a judge-friendly read/write demo, enable `ENABLE_PUBLIC_DEMO` and configure
an explicit `DEMO_STEWARD_PASSWORD` in a non-production environment. Its write-side budget is
enforced by the backend (40 mutations, 3 uploads, 3 profiler starts and 2
analysis starts per rolling 24-hour window). Read-only polling is not charged.
The same quota is shared across browser tabs and devices because reservations
are persisted in the backend database.

The default local mode uses `src/api/mockApi.ts`. It makes the complete UI flow
testable before the backend exists and is clearly marked in the interface as a
local adapter. The mock is not a production fallback and must not be used as Gate
2 deployment evidence.

## Connect the Gate 2 API

Create a local `.env.local`:

```dotenv
VITE_API_BASE_URL=http://localhost:8000
VITE_USE_MOCK_API=false
VITE_WORKSPACE_ID=ws-browser
```

`VITE_WORKSPACE_ID` identifies the application workspace used by versioned
dataset APIs; it is not a browser/device identifier.

The real client sends session cookies, the CSRF header, idempotency keys and the
endpoint shapes defined in `docs/API_CONTRACT.md`.

For the localhost API started from the repository root, set
`FRONTEND_ORIGIN=http://localhost:5173` (or include that origin in the comma-separated
value). The API's deterministic local proposer supplies testable proposal data; it
does not make an LLM call.

## Verification

```powershell
npm run build
```
