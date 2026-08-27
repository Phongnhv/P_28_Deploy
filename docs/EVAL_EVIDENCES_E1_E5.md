# Gate 2 MVP — Rule Evaluation E1–E5

> **Moved from `eval/results/E1_E5_EVALUATION.md`.** The `eval/` directory was removed
> because it held only this document and an empty template, while the executable
> evaluation lives in `evalgate/`.
>
> The five cases below are now **executable** as golden cases in
> [`evalgate/golden/tier2_rules/e1_e5.cases.yaml`](../evalgate/golden/tier2_rules/e1_e5.cases.yaml).
> This file remains the human-readable acceptance record; the YAML is what actually
> runs and can fail a build. Where the two disagree, the YAML is authoritative,
> because prose cannot be checked.

This report records a bounded historical local/Supabase MVP verification on the
taxi-shaped E1–E5 fixture. The current product path is generic and versioned, so
these values are a baseline rather than a claim about every uploaded dataset or
the latest cloud deployment. The agent only receives aggregate profile evidence;
rule approval remains a Data Steward action. The runner compiles approved
specifications through fixed, parameterized templates.

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
