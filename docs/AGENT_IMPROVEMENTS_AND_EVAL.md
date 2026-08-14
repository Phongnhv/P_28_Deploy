# Dashboard proposal agent: implementation and evaluation

> Scope: Gate 2 local MVP on `codex/agent-candidate-diversity`. This document
> describes the dashboard-facing proposal flow, not the legacy free-form chat or
> SQL-repair agents.

## 1. Current workflow

```text
immutable local dataset
  -> full-table aggregate profile
  -> dataset rule policy
  -> deterministic candidate checklist
  -> structured LLM selection/ranking
  -> server-side canonicalisation and evidence validation
  -> PROPOSED rules
  -> Steward review
  -> deterministic typed-rule compiler and read-only DQ execution
```

The LLM is deliberately a constrained selector and ranker. It does not invent
columns, thresholds, enum values, relationships, SQL or executable rule specs.
This division keeps semantic and execution authority in deterministic backend code
while retaining model judgment for prioritisation.

## 2. Profile evidence improvements

All dashboard metrics below are computed over the complete local table. Rates use a
fraction in `[0, 1]`; the API may render them as percentages.

| Metric | Definition | Agent use |
|---|---|---|
| `negative_rate` | negative non-null numeric values / non-null numeric values | Rank a policy-defined non-negative rule using observed defect prevalence |
| `quantiles` | full-table `p05`, `p25`, `p50`, `p75`, `p95` | Describe the typical distribution without turning observed extrema into invented thresholds |
| `out_of_domain_rate` | non-null values outside the governed policy set / non-null values | Rank an accepted-values rule and distinguish an active defect from a preventive check |
| cross-field violation rate | rows failing a configured relationship / rows where both operands are non-null | Rank cross-field rules using measured violations |
| full-table uniqueness | non-null distinct count, `distinct/non-null`, and an exact unique flag | Distinguish sample uniqueness from verified full-table uniqueness |

`is_unique_full_table` is true only when the column has no null values and its
full-table distinct count equals the dataset row count. Out-of-domain and cross-field
metrics are evaluated only for constraints declared in
`src/resources/rule_policies.json`; the profiler does not infer business semantics
from raw values.

The SQLite startup migration adds the new aggregate fields to existing local
databases. The profile API change is additive, so existing frontend fields remain
valid.

## 3. Evidence and privacy boundary

The proposal graph receives only `ProposalEvidence`:

- dataset/manifest identity and row count;
- aggregate quality scores;
- column name/type, null and distinct aggregates;
- full-table negative, quantile, domain and uniqueness metrics;
- configured cross-field aggregate metrics;
- stable policy and profile evidence references;
- the deterministic candidate checklist.

It never receives raw rows, `source_row_id`, sample values, failed row values,
credentials, database connection strings, browser prompts or SQL. Automated tests
serialize the real digest and assert that excluded fields are absent.

## 4. Candidate and response contract

The policy adapter currently creates at most one candidate for each dashboard rule
type used by the model:

- `NOT_NULL` for a policy-required identifier;
- `RANGE` for a policy-defined non-negative measure;
- `ACCEPTED_VALUES` for a governed code set;
- `CROSS_FIELD_COMPARISON` for a configured relationship.

Each candidate contains an opaque `candidate_id`, exact column, rule type,
parameters, canonical rule spec, evidence references, priority and confidence
ceiling. The structured response must copy `candidate_id`, column, type and parameters
exactly. A mismatched ID, fabricated column, changed threshold, unsupported type,
duplicate rule category or unknown evidence reference is rejected.

The backend persists canonical policy text/spec/severity rather than model-generated
semantics. Model prose is validated for shape but cannot change the executable rule.
This prevents contradictions such as describing a maximum of 80 while persisting only
`min_value = 0`.

## 5. Diversity, ranking and fallback

The candidate order currently favours:

1. observed policy-defined non-negative rule;
2. configured cross-field relationship;
3. required identifier completeness;
4. governed enum validity.

Normalisation accepts at most one proposal per dashboard rule type. When the model
returns one valid candidate, the server adds only enough canonical policy fallback to
reach two proposals. When it returns two or more valid candidates, no fallback is
added. Fallback proposals use `agent-policy-fallback-v1` so they cannot be confused
with a model selection.

`AGENT_MODE=mock` remains deterministic for UI/offline tests. `AGENT_MODE=graph`
performs one structured provider request for the dashboard table, with paid retries
disabled by the adapter.

## 6. Evaluation protocol

Live evaluation uses a fixed aggregate profile and the production dashboard adapter.
It must never print the API key or raw evidence.

- Maximum live calls per manual run: 8.
- Default live calls: 5.
- Provider retries: 0.
- Pass criteria per call:
  - provider request completes;
  - two to five proposals survive validation;
  - no duplicate dashboard rule type;
  - every proposal has allow-listed evidence;
  - persisted spec/text remain canonical;
  - fallback use is reported separately;
  - latency and selected rule-type distribution are recorded.

The same representative input must be reused when comparing prompts or models.
Track success, schema validity, diversity, fallback rate, latency and token/cost data
when available; fewer calls or tokens count as an improvement only if quality still
passes.

## 7. Automated verification

Current verification covers:

- full-table profile metric persistence and API serialization;
- aggregate-only agent digest privacy;
- candidate diversity and evidence-reference validity;
- candidate-ID and parameter-drift rejection;
- canonical text/spec/severity/confidence enforcement;
- minimal fallback behaviour;
- proposal persistence and complete backend regression.

## 8. Remaining limitations

- Domain sets and cross-field relationships must still be configured per dataset.
- The dashboard path loads the 50k local artifact into pandas; cloud-scale profiling
  should push aggregates into SQL or a distributed engine.
- The LLM selects from verified candidates; it is not an autonomous business-rule
  discovery engine.
- A multi-dataset labelled eval set is still required before comparing models or
  claiming generalised proposal precision.

## 9. Latest live evaluation

Date: 2026-08-14. Provider/model: OpenAI `gpt-4o-mini`.

The primary evaluation ran five identical full-table profile cases. A sixth provider
call was used only to verify that the reusable Windows harness closes its temporary
SQLite database cleanly. Total calls remained below the eight-call hard limit.

| Result | Value |
|---|---:|
| Primary calls passed | 5 / 5 |
| Structured proposal success rate | 100% |
| Proposals per call | 4 |
| Duplicate rule types | 0 |
| Policy fallback proposals | 0 |
| Mean provider latency | 8.473 s |
| Observed latency range | 6.457–10.787 s |

Every primary call selected the same diverse set in canonical priority order:
`numeric_range`, `cross_field_comparison`, `not_null`, and `accepted_values`.
All outputs survived candidate-ID, parameter, evidence and canonicalisation checks.
The one-call harness verification also passed with the same four rule types and no
fallback, at 9.104 seconds.

Interpretation: the constrained dashboard proposal flow is stable for the fixed local
NYC Taxi profile and no longer exhibits repeated `not_null` output in this bounded
sample. This result does not establish generalisation to unseen datasets; that needs a
labelled multi-dataset eval set.
