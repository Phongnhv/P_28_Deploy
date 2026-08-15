# Gate 2 MVP — Rule Evaluation E1–E5

This report records the bounded local/Supabase MVP verification. The agent only
receives aggregate profile evidence; rule approval remains a Data Steward action.
The runner compiles approved specifications through fixed, parameterized templates.

| Case | Rule type | Evidence and expected behaviour |
| --- | --- | --- |
| E1 | `numeric_range` | `trip_distance` and `fare_amount` must be non-negative; negative observations are returned as bounded failure IDs. |
| E2 | `not_null` | `vendor_id` must be populated; a missing identifier is a completeness failure. |
| E3 | `accepted_values` | `payment_type` must use the dataset policy's governed semantic values. |
| E4 | `cross_field_comparison` | `pickup_at <= dropoff_at`; null timestamps are excluded from the comparison. |
| E5 | `duplicate_fingerprint` | Duplicate trip fingerprints are detected over the policy-defined tuple. |

## Latest direct Supabase smoke evaluation

The canonical adapter was exercised against `public.trips_canonical` after a
schema preflight and a 5,000-row bounded dataset load. The runner checked every
rule with read-only `SELECT` statements and caps returned failed IDs at 20.
Observed failures are data-quality findings, not a schema conversion failure:
negative distance, negative fare, out-of-policy payment values, and duplicate
fingerprints were detected; `vendor_id` and timestamp ordering passed in that run.

## Acceptance criteria

- The five rule types above compile only from allow-listed columns and operators.
- The browser never receives raw rows from rule results, only counts and capped IDs.
- Every approval and execution action is auditable.
- A changed policy requires steward review of dependent approved rules before a
  changed rule version becomes active.
