# RidePulse DQ frontend

Local React/Vite implementation for the Gate 2 Data Steward workflow.

## Run locally

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173` and use the local demo password `demo`.

The default local mode uses `src/api/mockApi.ts`. It makes the complete UI flow
testable before the backend exists and is clearly marked in the interface as a
local adapter. The mock is not a production fallback and must not be used as Gate
2 deployment evidence.

## Connect the Gate 2 API

Create a local `.env.local`:

```dotenv
VITE_API_BASE_URL=http://localhost:8000
VITE_USE_MOCK_API=false
```

The real client sends session cookies, the CSRF header, idempotency keys and the
endpoint shapes defined in `docs/API_CONTRACT.md`.

## Verification

```powershell
npm run build
```
