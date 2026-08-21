# Run 2 Graph Review

## Scope

This review covers the Run 2 execution graph:

`test_generator -> validate_dbt_project -> repair loop -> test_runner -> anomaly_detector -> steward_insights -> persist_report`

The focus is correctness, observability, anomaly quality, failure handling, and consistency between the LangGraph execution path and the dashboard/API path.

## Executive Summary

The graph has a reasonable deterministic core: approved rules are compiled into SQL/dbt artifacts, validated, repaired with a bounded loop, executed, and persisted. The weak point is the interpretation layer after execution. `anomaly_detector_node` is currently a small heuristic over per-rule violation rates, while `steward_insights_node` treats every anomaly as an identical two-point score penalty. This makes the output easy to explain, but too coarse for operational data-quality monitoring.

The largest architectural risk is duplication. The graph uses `src/agents/nodes/anomaly_detector_node.py`, while the dashboard API uses `src/services/dashboard_anomaly.py`. They implement similar but different contracts and thresholds. A steward can therefore see different anomaly results depending on which product surface is used.

## Findings

### High: Two anomaly engines produce different answers

The LangGraph node calls `get_rule_history(rule_id)` without filtering by dataset, run success, or rule version. The dashboard implementation filters history by dataset and successful runs. A rule ID reused across datasets can contaminate the graph baseline, and failed/error test runs can become historical evidence.

The output schemas also differ. The graph emits `current_rate`, `historical_mean`, and `z_score`, while the dashboard adds `history_size`, `detection_mode`, `checked_count`, and `failed_count`.

**Recommendation:** create one deterministic anomaly service and call it from both the graph and the API. The service should accept `dataset_id`, `test_run_id`, rule version/fingerprint, and current result; return one typed anomaly schema; and persist the detection mode and baseline metadata.

### High: Cold-start detection is too narrow and hard-coded

The graph only emits a cold-start anomaly when the result status is `FAILED` and `violation_rate >= 5%`. This misses meaningful events such as:

- a new rule failing at 1% when the rule is CRITICAL;
- a sudden first-run failure in a small but important table;
- a large absolute violation count hidden by a low percentage;
- a rule changing from zero violations to a small but material rate.

The 5% threshold is not severity-aware and is not adjusted for sample size.

**Recommendation:** use configurable policies with minimum checked rows, severity-specific thresholds, and both rate and count evidence. Mark the result as `COLD_START` rather than pretending that it is a historical anomaly.

### High: Z-score is statistically fragile for bounded violation rates

Violation rates are bounded in `[0, 1]`, often zero-inflated, and may have very small variance. The current implementation uses a population standard deviation and returns a synthetic z-score of `3.0` whenever the baseline is constant and the current value differs. That is a useful alert heuristic, but it is not a statistically meaningful z-score.

There is also no minimum current sample-size guard in the graph node. A 100% rate over two checked rows can be treated like a 100% rate over two million rows.

**Recommendation:** retain the z-score as one signal, but add:

- minimum `checked_count`;
- robust baseline statistics (median/MAD or percentile bands);
- absolute delta from baseline;
- a binomial or Wilson interval for small samples;
- an explicit `baseline_constant` flag instead of fabricating a z-score.

### Medium: No anomaly deduplication or correlation

Every rule is evaluated independently. A broken upstream partition, schema change, or source outage can cause dozens of rules to alert separately. The graph has no run-level incident grouping.

**Recommendation:** add a correlation stage that groups anomalies by dataset, table, execution timestamp, and shared signals such as identical checked counts, error messages, or failure samples. Produce one incident with contributing rules and a primary suspected cause.

### Medium: Execution errors disappear from anomaly analysis

`ERROR` results are explicitly skipped by the anomaly detector. That is correct for violation-rate statistics, but the run then loses an important operational signal. A broken test query, missing column, timeout, or dbt/runtime failure should be visible as an execution anomaly or incident, not silently absent.

**Recommendation:** emit a separate `EXECUTION_ERROR` finding with error category, affected rule/table, retryability, and remediation owner. Keep it separate from data-violation anomalies so DQ score semantics remain clear.

### Medium: DQ score double-counts and flattens anomaly impact

`steward_insights_node` already scores failed rules through violation rates, then subtracts `2.0` for every anomaly regardless of severity, confidence, checked count, or magnitude. One anomaly on a LOW rule is penalized the same as a CRITICAL incident. Correlated anomalies can also multiply the penalty for one upstream event.

**Recommendation:** make anomaly impact policy-driven. Use severity, confidence, absolute delta, and incident grouping. Consider reporting separate metrics: rule health score, anomaly/observability score, and execution reliability score, rather than folding all signals into one opaque number.

### Medium: Historical baseline is not version-aware

`get_rule_history` keys history only by `rule_id`. If a steward edits a rule threshold, predicate, or scope while retaining the same ID, old results are no longer comparable.

**Recommendation:** persist a rule fingerprint/version and only compare compatible historical results. Start a new baseline after material rule changes, with a warm-up status.

### Low: The graph has no explicit quality gate between execution and reporting

The graph always proceeds from `test_runner` to anomaly detection and insights, even when all results are `ERROR`, no rows were checked, or the result set is incomplete. `persist_report_node` marks the run `DONE` unless errors exist and there are no results, which can make partially failed runs look successful.

**Recommendation:** add a result-quality gate with statuses such as `VALID`, `PARTIAL`, `NO_DATA`, and `EXECUTION_FAILED`. Route partial/invalid results to a distinct report state and expose the reason in the API.

## Recommended Target Graph

```text
test_generator
  -> validate_dbt_project
  -> [bounded repair loop]
  -> test_runner
  -> result_quality_gate
  -> anomaly_baseline_loader
  -> anomaly_detector
  -> anomaly_correlator
  -> steward_insights
  -> persist_report
```

The baseline loader and detector should be deterministic services. LLM usage should remain limited to explanation, prioritization, and remediation wording after the evidence has been calculated.

## Suggested Anomaly Contract

Each finding should contain at least:

```json
{
  "finding_id": "...",
  "dataset_id": "...",
  "test_run_id": "...",
  "rule_id": "...",
  "rule_version": "...",
  "table_name": "...",
  "anomaly_type": "Z_SCORE_SPIKE",
  "severity": "HIGH",
  "confidence": 0.86,
  "current_rate": 0.12,
  "violation_count": 1200,
  "checked_count": 10000,
  "baseline": {"mean": 0.01, "median": 0.01, "history_size": 12},
  "evidence": {"absolute_delta": 0.11, "z_score": 3.4},
  "detection_mode": "HISTORICAL",
  "incident_id": "...",
  "reason": "..."
}
```

## Implementation Roadmap

### Phase 1: Correctness and consistency

1. Extract the dashboard anomaly logic into a shared service.
2. Add dataset, successful-run, and rule-version filtering to all baselines.
3. Standardize the anomaly schema and persist it with the Run 2 report.
4. Add minimum checked-count and no-data guards.
5. Add tests for cross-dataset contamination, failed historical runs, constant baselines, and small samples.

### Phase 2: Better detection

1. Add severity-aware cold-start policies.
2. Add robust statistics and absolute-delta checks.
3. Add execution-error findings.
4. Add rule-change warm-up/reset behavior.
5. Add run-level correlation and incident grouping.

### Phase 3: Better operations

1. Add configurable anomaly policies per dataset/domain.
2. Add alert suppression, acknowledgment, and deduplication state.
3. Track detector precision/false-positive feedback from steward actions.
4. Separate DQ health, anomaly health, and execution reliability in the report/UI.
5. Add replayable evidence snapshots so an anomaly can be audited after the source data changes.

## Test Coverage Needed

- No approved rules.
- Empty result set and zero checked rows.
- All results `ERROR`.
- Cold start with low-rate but high-count failures.
- Warm start with fewer than five valid historical runs.
- Constant baseline followed by a small and a large change.
- Cross-dataset rule IDs.
- Rule threshold/version changes.
- Duplicate anomalies caused by one upstream incident.
- Agreement between graph output and `/dq-runs/{id}/anomalies`.

## Conclusion

Run 2 should remain deterministic through test generation, validation, execution, baseline calculation, and anomaly classification. The LLM should explain evidence, not decide whether a statistical event is an anomaly. The first improvement should be consolidating the two anomaly engines and fixing baseline scoping; after that, add sample-size-aware and severity-aware detection, then correlation and operational feedback.
