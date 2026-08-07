# Gate 2 team plan

## 1. Ownership

| Member | Primary ownership |
|---|---|
| Vũ Nguyễn Quốc Đạt | Google Cloud/Vercel setup, migrations and database roles, Cloud Run Job lifecycle, release smoke |
| Lương Trung Chiến | 50k artifact/generator, dbt project, evaluation evidence, README/diagram/video assets |
| Nguyễn Hoàng Vĩnh Phong | React/Vite frontend, Vercel deployment, access/UI state and browser evidence |
| Nguyễn Hữu Kiên | FastAPI product APIs, profile/evidence, LangGraph/OpenAI, HITL/compiler/runner |

Each PR has one named owner and a different named reviewer. The owner records the
acceptance evidence before requesting merge.

## 2. Twelve mergeable PRs

| # | PR scope | Owner | Reviewer | Done when |
|---:|---|---|---|---|
| 1 | Cloud/Vercel environment baseline | Đạt | Phong | GCP project, service identities, Vercel project and no secrets in Git |
| 2 | Deterministic 50k artifact generator | Chiến | Đạt | Manifest, unique IDs, mutation seed and expected counts verified |
| 3 | PostgreSQL schema and roles | Đạt | Kiên | Migration plus app/dbt/runner permission tests |
| 4 | dbt Core project | Chiến | Kiên | `dbt parse` and `dbt build` create fixed analytics outputs |
| 5 | Common job lifecycle and Cloud Run Job dispatch | Đạt | Phong | `202`, poll, idempotency, lease and retry behavior verified |
| 6 | Ingest/profile API service | Kiên | Chiến | Private artifact → raw → dbt → persisted profile integration test |
| 7 | Guarded Agent proposal service | Kiên | Đạt | Evidence/privacy/schema tests plus one controlled live call |
| 8 | HITL, compiler and read-only runner | Kiên | Phong | State/audit/permission/result tests pass |
| 9 | Access and dataset/profile frontend | Phong | Kiên | Vercel preview uses actual API and UI state tests |
| 10 | Review/results/audit frontend | Phong | Chiến | Full browser journey uses no static results |
| 11 | Hosted smoke, export and runbook | Đạt | Chiến | Public HTTPS, Cloud Run logs and manual export/recovery rehearsal |
| 12 | Evaluation, architecture and video assets | Chiến | Phong | Five real cases, diagram, root README and ≤3-minute rehearsal |

PRs 1–10 satisfy the Gate PR count; PRs 11–12 are release evidence, not artificial
splits. Existing placeholder `/chat` behavior is retained or explicitly removed only
through a reviewed contract change.

## 3. Delivery order

| Window | Target |
|---|---|
| Days 1–2 | Provider access, PR 1–4, migration and artifact/dbt proof |
| Days 3–5 | PR 5–8, hosted backend flow and guarded Agent/rule core |
| Days 5–7 | PR 9–10, Vercel UI connected to public API |
| Days 8–9 | PR 11–12, five evaluations, export/recovery rehearsal and video |
| Final day | Two public smoke runs, merge final reviewed PRs and submit |

If the stated Gate deadline is earlier than this sequence permits, remove polish first;
do not remove the real LLM, dbt, persistence, HITL or public deployment requirements.

## 4. Five required manual real-LLM cases

| Case | Expected meaningful output |
|---|---|
| E1 | `numeric_range` proposal grounded in negative fare/distance aggregate evidence |
| E2 | `not_null` proposal grounded in missing required identifier evidence |
| E3 | `accepted_values` proposal for invalid payment category evidence |
| E4 | `cross_field_comparison` proposal for invalid pickup/dropoff chronology evidence |
| E5 | `duplicate_fingerprint` proposal grounded in duplicate-rate evidence |

For every case, save deployed commit, public URL, time, aggregate evidence, model
output, reviewer decision, DQ result/audit ID and screenshot. The simulated malformed
LLM/provider-error scenario is an automated negative test, not a manual live-output
case.

## 5. Three-minute public video

1. **0:00–0:20:** Public Vercel URL and architecture diagram.
2. **0:20–0:45:** Dataset selection and persisted Cloud Run job progress.
3. **0:45–1:20:** Aggregate profile and real guarded proposal.
4. **1:20–1:55:** Approve/edit/reject and audit event.
5. **1:55–2:30:** Approved DQ run, counts and bounded results.
6. **2:30–3:00:** dbt evidence, five-case proof and first-version limits.

Never show localhost, secrets, raw rows, database connection strings or internal stack
traces in the video.
