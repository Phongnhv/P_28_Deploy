# Supabase dataset contract and MVP rule execution

## Decision

`public.trips_raw` is the immutable ingestion boundary. Its stable shape is:

| Column | Type | Purpose |
|---|---|---|
| `source_row_id` | `varchar` | Stable row identifier |
| `dataset_id` | `varchar` | Dataset/version boundary |
| `values` | `json` | Source values exactly as ingested |

Application code, dbt and DQ rules must not assume typed business columns exist
on `trips_raw`. Migration `005_canonical_dataset_contract.sql` creates
`public.trips_canonical`, a typed and null-safe projection used by all analytical
consumers. Failed numeric/timestamp casts become `NULL`; they do not abort the
whole profile or rule run.

This is intentionally additive. The migration does not drop, rename or rewrite
raw rows and does not replace the existing dashboard tables.

## Representation policy

The semantic taxi dataset stores category labels, not TLC numeric codes. The
versioned policy therefore accepts these `payment_type` values:

- `Flex Fare trip`
- `Credit card`
- `Cash`
- `No charge`
- `Dispute`
- `Unknown`
- `Voided trip`

`Invalid Payment (Dispute/Test)` is deliberately outside the governed set so the
agent and runner can surface the four known invalid sample rows. Agent evidence,
profile validity and rule execution now read the same policy file.

## Connection and execution plan

The MVP flow is:

1. Ingest source records once into `trips_raw`; never clean them in place.
2. Read typed fields through `trips_canonical`.
3. Compute full-table aggregate profiles in PostgreSQL. Raw values are not
   returned to the browser or the LLM.
4. Generate candidate rules from aggregate evidence plus the versioned policy.
5. Require steward approval before execution.
6. Compile approved rule specs through fixed, allow-listed SQL templates.
7. Execute parameterized queries with a statement timeout and cap returned
   failure IDs at 20.
8. Persist aggregate results in the existing `dataset_profiles`, `dq_runs` and
   `dq_results` product contract when the Supabase API adapter is enabled.

For local verification:

```powershell
python scripts/supabase_rule_eval.py
python scripts/supabase_rule_eval.py --persist-profile
```

The first command is read-only. The second upserts aggregate profile data and
updates the dataset row count/status. Neither command changes `trips_raw`.

## Controlled test-data procedure

Integration fixtures must use a unique `source_row_id`, run inside an explicit
transaction and rollback. After rollback, verify that the fixture ID has zero
rows. This exercises live Supabase inserts and view/rule behavior without
polluting the 500-row sample.

## Does the project need data cleaning?

Cleaning is not required for the Gate 2 local MVP. The stated goal is to profile,
propose and approve DQ rules, execute checks, detect anomalies and show evidence.
Automatically changing source data would weaken the audit story and can hide the
defects the project is designed to detect.

For an advanced scope, add cleaning as a separate steward-approved remediation
stage:

```text
immutable raw -> canonical/staging -> DQ issues -> approved remediation spec
              -> versioned cleaned dataset -> re-run DQ checks
```

Never overwrite `trips_raw`. Produce a new cleaned table or dataset version and
record the source row, transformation, actor, timestamp and before/after quality
metrics. A small demo can safely show label normalization and quarantine of
invalid rows, but automatic imputation or deletion should remain out of the MVP.

## Current boundary

The direct Supabase adapter and CLI prove the canonical contract and live rule
execution. The existing local UI/API still uses the SQLite `source_rows` model.
Switching the dashboard API to Supabase should be a separate adapter change so
local test mode remains deterministic and ORM table creation is never run against
the managed Supabase database.
