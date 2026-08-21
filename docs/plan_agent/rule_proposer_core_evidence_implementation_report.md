# Rule Proposer Core Evidence Implementation Report

Date: 2026-08-17

## Summary

Rule Proposer now emits a steward-facing name, separate business rationale,
proposal basis, allow-listed evidence references, parameter provenance,
assumptions, and a confidence breakdown. Numeric evidence is resolved by the
node from the profiler digest instead of being accepted from model output.

No SQL impact scan was added. UI rendering of the new fields remains out of
scope, but the public API and frontend types now expose the data.

## Contract And Ownership

Before this change, the structured proposal contained the rule condition,
severity, dimension, one confidence score, a description, and free-text AI
reasoning. It also received HITL defaults during proposer stamping.

The new ownership boundary is:

- Rule Proposer: `rule_name`, `business_rationale`, `proposal_basis`, selected
  evidence references, parameter provenance, assumptions, confidence
  breakdown, condition, description, and concise rationale.
- Orchestration: `rule_id`, `run_id`, and `table_name`.
- HITL/persistence: status, edited parameters, reviewer, review note, review
  timestamps, and creation timestamp.

The Pydantic JSON Schema requires the new semantic fields. A pre-validation
adapter still upgrades legacy payloads containing `confidence_score`, so old
artifacts and rollout-era mocks remain readable without weakening the JSON
Schema presented to the LLM.

## Deterministic Evidence

Candidate checklist entries now expose stable `evidence_items` with an ID,
source type, metric name, and digest-derived value. The LLM may select only
those IDs. `_stamp_rule` rejects references outside the matched candidate and
rejects parameter provenance that points outside the selected evidence.

The resolved snapshot contains:

- `sample_row_count`
- `sample_rate`
- `sampling_caveat`
- `observed_metrics`
- `source_refs`

Dashboard aggregate profiles are marked as full-table evidence (`sample_rate =
1.0`) rather than as an empty sample.

Example validated output:

```json
{
  "rule_id": "source_rows.amount.RANGE",
  "run_id": "run-1",
  "table_name": "source_rows",
  "column": "amount",
  "rule_type": "RANGE",
  "parameters": {"min": 0.0},
  "rule_name": "Amount must be non-negative",
  "business_rationale": "Negative amounts distort financial totals.",
  "proposal_basis": "MIXED",
  "selected_evidence_refs": ["policy.nonnegative_column.amount"],
  "parameter_provenance": [
    {
      "parameter_name": "min",
      "source_type": "POLICY",
      "source_ref": "policy.nonnegative_column.amount",
      "derivation_method": "configured non-negative policy"
    }
  ],
  "assumptions": [],
  "confidence": {
    "overall": 0.9,
    "evidence_strength": 1.0,
    "business_support": 0.9,
    "sample_representativeness": 0.8,
    "explanation": "Policy-backed threshold with supporting profile evidence."
  },
  "confidence_score": 0.9,
  "evidence": {
    "sample_row_count": 100,
    "sample_rate": 1.0,
    "sampling_caveat": null,
    "observed_metrics": {"policy.nonnegative_column.amount": null},
    "source_refs": ["policy.nonnegative_column.amount"]
  },
  "severity": "HIGH",
  "dimension": "VALIDITY",
  "rule_description": "Amount must be greater than or equal to zero.",
  "ai_reasoning": "Dataset policy defines amount as non-negative."
}
```

Review fields are intentionally absent from this raw proposer output.

## Prompt Review

The proposer prompts were updated to:

- Request every new structured field.
- Use evidence IDs instead of repeating or inventing metric values.
- Separate rule name, condition description, business rationale, and concise
  rationale.
- Require provenance for every active parameter.
- Distinguish authoritative policy/dictionary constraints from observed
  profile patterns.
- Prevent observed maxima or sampled categories from silently becoming hard
  business constraints.
- Avoid regex inference from a column name alone.
- Calibrate confidence based on evidence, business support, and sample
  representativeness instead of defaulting to 1.0.
- Remove requests for long internal reasoning.

The contradictory NULL_RATE example was corrected: an observed null rate of
15.3% with a proposed maximum of 10% is now explicitly described as currently
failing and requiring Steward confirmation.

## Persistence And Compatibility

Both proposal storage paths now retain the new contract. The PostgreSQL
migration `006_rule_proposal_core_evidence.sql` adds relational semantic fields
and JSONB evidence fields to `proposed_rules` and `rule_proposals`, backfills
legacy rows, and creates a GIN index for evidence queries.

Local SQLite initialization adds equivalent text-serialized JSON columns to
existing development databases. Existing `confidence_score`/`confidence`
columns remain populated from `confidence.overall` during compatibility rollout.

The public proposal API returns the new fields. Manual Steward rules receive
explicit manual/policy provenance defaults. Existing frontend mock objects
remain compatible because the newly exposed frontend fields are optional until
the UI begins rendering them.

## Verification

Completed successfully:

- Ruff checks for all modified Python modules and new tests.
- Frontend TypeScript and Vite production build.
- 68 targeted regression tests covering proposer schema, deterministic
  evidence, HITL ownership, persistence, dashboard workflow, proposal API, and
  HITL routes.
- JSON Schema inspection confirmed the new core semantic fields are required.

The full repository test suite was attempted separately. It first encountered
a Windows-locked SQLite temp fixture; after switching temp roots it ran longer
than the available 10-minute command timeout and did not produce a final result.
No full-suite pass is claimed. The targeted suites covering every changed
subsystem completed successfully.

## Deliberately Out Of Scope

- SQL impact evaluation and projected violation counts.
- UI components for evidence, provenance, assumptions, or confidence details.
- Large profiler artifacts or violating row samples.
- Model, prompt, policy, and data-dictionary generation-version metadata beyond
  the core evidence contract.
