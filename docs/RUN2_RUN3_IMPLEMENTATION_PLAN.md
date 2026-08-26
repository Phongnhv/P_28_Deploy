# Graph 2 and Graph 3 Implementation Plan

## 1. Purpose

This plan defines the implementation path for the post-approval data-quality workflow:

```text
Approved rules
  -> Graph 2: deterministic rule execution
  -> persisted execution results
  -> Graph 3: anomaly analysis and hypothesis generation
```

The immediate objective is to make execution reproducible, auditable and independent from anomaly-analysis failures. Anomaly detection must be evidence-based and rerunnable without re-executing the dataset.

This document is a planning artifact only. It does not authorize implementation changes by itself.

## 2. Goals and Non-Goals

### Goals

- Execute only approved, immutable ruleset snapshots.
- Keep rule compilation deterministic and free of LLM decisions.
- Separate execution status from data-quality rule status.
- Persist canonical execution results before anomaly analysis.
- Make anomaly analysis independently rerunnable and versioned.
- Consolidate anomaly logic into one canonical service used by Graph 3 and the API.
- Use LLM only for structured hypotheses and explanations after deterministic evidence is calculated.
- Preserve auditability through hashes, versions, evidence references and state transitions.

### Non-goals for the first implementation

- Automatic remediation of source data.
- LLM modification of approved rule semantics at runtime.
- Isolation Forest, seasonal models and advanced distribution drift in the first MVP slice.
- Automatic model training from steward feedback.
- Replacing every existing API/UI surface in the first phase.

## 3. Target Architecture

```text
Steward completes review
  -> publish/create immutable ruleset version
  -> create execution request

Graph 2: Rule Execution
  load snapshot
  -> validate contract
  -> compile SQL/dbt artifacts
  -> validate artifacts
  -> execute tests
  -> normalize results
  -> persist results
  -> finalize execution
  -> dispatch Graph 3

Graph 3: Anomaly Analysis
  load persisted execution and compatible history
  -> build feature frame
  -> detector fan-out
  -> signal quality gate
  -> aggregate deterministic decision
  -> optional hypothesis agent
  -> validate hypotheses
  -> persist analysis
  -> surface/notify
```

The persistence boundary between the graphs is mandatory. Graph 3 must be able to run later using `execution_run_id`, without requiring Graph 2 to run again.

## 4. Design Principles

1. **Approved semantics are immutable during execution.** Runtime systems may reject invalid rules, but may not silently alter them.
2. **Deterministic evidence precedes LLM interpretation.** Numerical detection and aggregation are code/config decisions; LLM output is explanatory.
3. **Rule failure is not infrastructure failure.** A rule can fail because the data violates it while the execution itself succeeds.
4. **Incomplete evidence is explicit.** `INSUFFICIENT_HISTORY`, `PARTIAL`, `NO_DATA` and `RESULT_MISMATCH` are valid states, not hidden fallbacks.
5. **Every decision is replayable.** Store inputs, versions, configuration and evidence references.
6. **One canonical implementation.** Dashboard/API and Graph 3 must consume the same anomaly service and schema.

## 5. Execution Lifecycle

### 5.1 Trigger policy

Graph 2 must not run once per individual approval click. It starts only after a review batch is complete or an explicit execution request is submitted.

Supported trigger types:

- `MANUAL`
- `PUBLISH_AND_RUN`
- `SCHEDULED`

### 5.2 Status model

Execution status:

- `QUEUED`
- `RUNNING`
- `SUCCEEDED`
- `PARTIAL`
- `FAILED`
- `CANCELLED`

An execution with rule-level `FAIL` results can still be `SUCCEEDED`.

Graph 3 status:

- `NOT_REQUESTED`
- `QUEUED`
- `RUNNING`
- `SUCCEEDED`
- `INSUFFICIENT_HISTORY`
- `FAILED_TO_START`
- `FAILED`

Hypothesis status:

- `NOT_REQUIRED`
- `QUEUED`
- `RUNNING`
- `SUCCEEDED`
- `FALLBACK_USED`
- `FAILED`

These statuses must never be collapsed into one field.

## 6. Contracts

### 6.1 Execution request

```python
class ExecutionRequest(BaseModel):
    execution_run_id: str
    dataset_id: str
    dataset_version_id: str
    proposal_run_id: str | None
    ruleset_version_id: str
    requested_by: str
    trigger_type: Literal["MANUAL", "PUBLISH_AND_RUN", "SCHEDULED"]
```

The request references immutable versions. It must not contain mutable rule rows as its canonical input.

### 6.2 Graph 2 state

```python
class ExecutionGraphState(TypedDict, total=False):
    request: dict
    ruleset_snapshot: dict
    validation_errors: list[dict]
    compiled_tests: list[dict]
    dbt_artifact_ref: dict
    artifact_hash: str
    compiler_version: str
    dbt_validation: dict
    execution_results: list[dict]
    normalized_results: list[dict]
    execution_status: str
    error: dict | None
    retry_history: list[dict]
    metadata: dict
```

### 6.3 Graph 3 state

```python
class AnomalyGraphState(TypedDict, total=False):
    anomaly_run_id: str
    execution_run_id: str
    dataset_id: str
    dataset_version_id: str
    ruleset_version_id: str
    detector_config_version: str
    current_features: dict
    historical_features: dict
    signal_observations: list[dict]
    signal_errors: list[dict]
    anomaly_decision: dict
    hypotheses: list[dict]
    hypothesis_validation: dict
    anomaly_status: str
    hypothesis_status: str
    metadata: dict
```

### 6.4 Canonical execution result

```json
{
  "rule_id": "orders.amount.RANGE",
  "rule_version": "rule-v4",
  "table_name": "orders",
  "column": "amount",
  "status": "PASS|FAIL|ERROR|SKIPPED|RESULT_MISMATCH",
  "checked_count": 50000,
  "failed_count": 132,
  "violation_rate": 0.00264,
  "severity": "HIGH",
  "dimension": "VALIDITY",
  "duration_ms": 214.5,
  "dbt_status": "PASS|FAIL|NOT_RUN",
  "metrics_status": "PASS|FAIL|ERROR",
  "sample_refs": [],
  "error": null,
  "evidence_refs": []
}
```

### 6.5 Signal observation

```python
class SignalObservation(BaseModel):
    signal_id: str
    family: Literal["BUSINESS_RULE", "STATISTICAL", "VOLUME", "FRESHNESS", "SCHEMA", "CORRELATION", "EXECUTION", "ML"]
    target_type: Literal["DATASET", "TABLE", "COLUMN", "RULE"]
    target_id: str
    score: float
    reliability: float
    direction: str | None
    observed_value: float | str | None
    baseline: dict | None
    sufficient_history: bool
    evidence_refs: list[str]
    detector_name: str
    detector_version: str
    explanation_code: str
```

### 6.6 Anomaly decision

```json
{
  "decision": "NORMAL|WATCH|ANOMALY|CRITICAL|INSUFFICIENT_HISTORY",
  "score": 0.78,
  "confidence": 0.81,
  "severity": "HIGH",
  "reason_codes": ["VIOLATION_RATE_SPIKE", "ROW_COUNT_DROP"],
  "supporting_signal_ids": ["sig-1", "sig-2"],
  "contradicting_signal_ids": [],
  "detector_config_version": "anomaly-v1",
  "limitations": []
}
```

## 7. Graph 2 Work Plan

### Phase 2.1: Ruleset snapshot

Implement a snapshot boundary before execution.

Tasks:

- Define `ruleset_versions` persistence.
- Select only `APPROVED` or active rules according to the trigger policy.
- Reject pending/rejected/deactivated rules.
- Persist normalized rule JSON, schema hash, semantic contract version and ruleset hash.
- Make snapshot creation idempotent for the same review batch and version.

Deliverable: an execution request can reference a stable `ruleset_version_id`.

### Phase 2.2: Contract validation

Add a dedicated validation step before compilation.

Checks:

- dataset/version exists;
- table and column exist;
- type is compatible;
- required parameters are present;
- cross-field target exists;
- parameter provenance is present;
- rule is active in the selected snapshot;
- schema hash has not drifted: calculate the `schema_signature_hash` (e.g., MD5 of sorted columns and data types) from the live database catalog and compare it with the reference hash stored in the ruleset snapshot. If they do not match, abort compilation;
- no duplicate rule identity exists.

Invalid parameters or schema drift must produce typed validation errors (e.g., `SCHEMA_DRIFT` error type). No text parsing, domain fallback or silent defaulting is allowed.

### Phase 2.3: Deterministic compiler

Tasks:

- Define a compiler version.
- Compile approved rule catalog entries to SQL predicates and dbt YAML.
- Quote identifiers and bind values.
- Persist `rule_id -> compiled_test_id` mapping.
- Compute artifact hash.
- Add select-only and operator/function allow-lists.
- Make rendering deterministic for identical input.

The compiler must not call an LLM.

### Phase 2.4: Artifact validation

Validation must include:

- YAML structural validation;
- dbt parse/compile;
- SQL safety validation;
- artifact scope against the ruleset snapshot;
- artifact hash and trace persistence.

Routing:

```text
valid -> execute
invalid -> persist compiler failure -> finalize FAILED -> END
```

### Phase 2.5: Retry policy

Remove semantic LLM repair from the production graph. However, retain the existing LLM repair logic (`llm_dbt_repair` and `llm_repair_node`) as an offline/design-time interactive tool. When compilation fails, the Steward can trigger an interactive LLM repair session from the Dashboard to propose a fix, which must be manually approved and published as a new version.

Allowed retries:

- bounded database connection retry;
- bounded transient timeout retry;
- idempotent artifact upload retry;
- deterministic re-render if the first local write failed.

Forbidden retries:

- asking an LLM to change approved predicates automatically at runtime;
- changing thresholds to make validation pass;
- adding/removing columns or models at runtime;
- changing the ruleset without a new approval/version.

Every retry must record attempt number, category, timestamp and outcome.

### Phase 2.6: Test execution

Tasks:

- Execute compatibility/dbt checks.
- Execute deterministic metrics queries.
- Bound query duration, result size and failure samples.
- Isolate errors per rule/test batch.
- Capture checked count, failed count, rate, duration and samples.
- Record dbt status separately from metrics status.

If dbt and metrics disagree, produce `RESULT_MISMATCH`; do not silently choose one.

### Phase 2.7: Result normalization

Normalize all runner variants into the canonical execution result contract. Ensure that missing fields, zero-row runs and errors are explicit.

Required behavior:

- one rule error does not discard other results;
- zero checked rows is not automatically a pass;
- incomplete result sets are marked `PARTIAL` or `NO_DATA`;
- `FAIL` is a data-quality result, not an infrastructure status.

### Phase 2.8: Persistence and finalization

Persist atomically:

- execution run;
- ruleset snapshot reference;
- compiler/artifact metadata;
- every normalized result;
- execution errors and retry history.

Finalize only after persistence succeeds.

### Phase 2.9: Dispatch Graph 3

Graph 2 execution terminates cleanly at the persistence boundary. The orchestration layer (e.g., in `job_runner.py` or the API controller) triggers Graph 3 asynchronously (non-blocking) using the generated `execution_run_id`.

If dispatch fails:

- keep execution status unchanged;
- set anomaly status to `FAILED_TO_START`;
- allow a later retry of Graph 3 through a dedicated API/job endpoint;
- do not rerun Graph 2 automatically.

## 8. Graph 3 Work Plan

### Phase 3.1: Canonical anomaly service

Before adding new detectors, remove divergence between the existing graph node and dashboard anomaly implementation.

Tasks:

- Define one anomaly service API.
- Use dataset-aware historical queries.
- Exclude current run from history.
- Exclude failed/incomplete historical runs.
- Exclude historical runs marked as true anomalies by the steward (feedback `TRUE_ANOMALY`) or confirmed `ANOMALY`/`CRITICAL` runs from baseline calculations to prevent baseline contamination.
- Partition history by compatible dataset/schema/rule identity.
- Standardize anomaly output schema.
- Add detector config version.
- Use robust estimators like Median and MAD (Median Absolute Deviation) instead of standard mean/variance when calculating baselines to reduce sensitivity to historical outliers.

The API and Graph 3 must consume this service rather than calculating anomalies independently.

### Phase 3.2: Load anomaly context

Load:

- current persisted execution results;
- compatible historical results;
- dataset and table profile snapshots;
- row-count and freshness metrics;
- schema changes;
- ruleset/rule version and cadence metadata.

Baseline compatibility key should include at least:

```text
dataset_id + dataset_version family + table + rule semantic identity + schema version
```

### Phase 3.3: Build features

Rule-level features:

- violation rate;
- absolute rate delta;
- failed count;
- checked count;
- duration delta;
- rule status.

Dataset/table-level features:

- row count and row-count delta;
- freshness delay;
- null-rate delta;
- distinct-ratio delta;
- schema change count;
- failed-rule ratio;
- correlated failure count.

### Phase 3.4: MVP detector fan-out

Implement these deterministic detectors first:

1. Business threshold/invariant detector.
2. Cold-start threshold detector with minimum checked count.
3. Robust historical detector using median/MAD when history is sufficient.
4. Volume drift detector.
5. Freshness detector.
6. Failure-cluster detector.
7. Execution-health detector.

Do not add Isolation Forest in this phase.

Detector rules:

- no fabricated z-score when standard deviation is zero;
- expose `sufficient_history`;
- expose reliability;
- use absolute delta in addition to relative statistics;
- use severity-aware thresholds;
- distinguish `EXECUTION` findings from data anomalies.

### Phase 3.5: Signal quality gate

Reduce reliability or reject a signal when:

- checked count is too small;
- history is too short;
- history contains failed runs;
- schema is incompatible;
- metric is missing or invalid;
- detector errored;
- multiple signals are duplicates from the same source.

Quality-gate decisions must be persisted for audit.

### Phase 3.6: Deterministic aggregation

Aggregate by signal family before combining families. Do not let five correlated statistical signals count as five independent votes.

Aggregation Policy & Formulas:

1. **Family Representative Score**: For each signal family (e.g., `STATISTICAL`, `VOLUME`, `FRESHNESS`, `BUSINESS_RULE`), compute its representative score as the maximum score within that family:
   $$S_{\text{family}} = \max(S_{\text{signals\_in\_family}})$$
2. **Weighted Family Aggregation**: Combine the active families using a weighted average:
   $$S_{\text{final}} = \frac{\sum (w_{\text{family}} \times S_{\text{family}})}{\sum w_{\text{family}}}$$
   where $w_{\text{family}}$ is the configured weight for each family.
3. **Priority Override (Hard Gate)**: If a high-priority business rule is breached (e.g., `BUSINESS_RULE` family with `severity` of `HIGH` or `CRITICAL`), the aggregate score immediately overrides to trigger `CRITICAL` or `ANOMALY`, ensuring critical data errors are not averaged down by normal values.

Suggested initial classification:

```text
INSUFFICIENT_HISTORY: no hard signal and total reliability is low
NORMAL: score < 0.45
WATCH: 0.45 <= score < 0.70
ANOMALY: score >= 0.70 with corroborating evidence
CRITICAL: hard invariant breach (Priority Override) or very strong corroborated evidence
```

Thresholds and family weights must be versioned configuration, not scattered constants.

One hard business invariant may produce `CRITICAL` without a second family. The two-family requirement should be used for statistical/general anomalies, not as an absolute rule.

### Phase 3.7: Persist anomaly analysis

Persist all decisions, including `NORMAL` and `INSUFFICIENT_HISTORY`:

- anomaly run;
- signal observations;
- quality-gate outcomes;
- aggregate decision;
- detector config/version;
- limitations and evidence refs.

Persistence must be idempotent by `(execution_run_id, detector_config_version)` unless an explicit reanalysis revision is requested.

### Phase 3.8: Hypothesis Agent

Run only for `WATCH`, `ANOMALY` or `CRITICAL`.

Input:

- aggregate decision;
- supporting and contradicting signals;
- rule failures;
- schema and profile metadata;
- recent changes;
- evidence references.

The agent must not receive unnecessary raw rows or PII. It must return structured hypotheses with:

- hypothesis type;
- summary;
- confidence;
- supporting signal IDs;
- contradicting signal IDs;
- evidence refs;
- recommended checks;
- missing evidence;
- limitations.

The agent cannot change the deterministic decision.

### Phase 3.9: Hypothesis validation and fallback

Validate:

- signal IDs exist;
- evidence refs exist;
- hypothesis type is allow-listed;
- confidence is within configured cap;
- recommendations are non-destructive;
- infrastructure hypotheses have execution evidence.

If the LLM fails, persist the anomaly decision and generate deterministic fallback explanation. Hypothesis failure must not fail anomaly analysis.

## 9. Persistence Model

### `ruleset_versions`

Store immutable approved ruleset snapshots:

- `id`;
- `dataset_id`;
- `dataset_version_id`;
- `proposal_run_id`;
- `semantic_contract_version_id`;
- `ruleset_hash`;
- normalized rules;
- creator and timestamps.

### `execution_runs`

Store:

- IDs and trigger type;
- status;
- compiler version;
- artifact reference/hash;
- dbt status;
- metrics status;
- retry history;
- timestamps and errors.

### `execution_results`

Store canonical per-rule results, including status, counts, rates, duration, samples, dbt/metrics status and error.

### `anomaly_runs`

Store:

- `anomaly_run_id`;
- `execution_run_id`;
- detector config version;
- status;
- decision, score, confidence and severity;
- timestamps and error.

### `anomaly_signals`

Store each signal, family, target, score, reliability, observed value, baseline, evidence refs and detector version.

### `anomaly_hypotheses`

Store structured hypothesis output, validation result, model name, prompt version, latency and fallback status.

### `anomaly_feedback`

Allow steward labels:

- `TRUE_ANOMALY`;
- `FALSE_POSITIVE`;
- `EXPECTED_CHANGE`;
- `RULE_MISCONFIGURATION`;
- `UNKNOWN`.

Feedback is initially for evaluation and calibration, not automatic model training.

## 10. API and Job Orchestration

### Execution APIs

- Create/publish immutable ruleset version.
- Create execution request.
- Get execution status.
- Get execution results.
- Get artifact metadata.
- Retry a failed execution only when policy permits.

### Anomaly APIs

- Dispatch or retry anomaly analysis for an execution run.
- Get anomaly run status.
- Get signal details.
- Get hypotheses.
- Submit steward feedback.

The frontend must expose execution and anomaly statuses independently. A successful execution with pending anomaly analysis must not look like a failed execution.

## 11. Testing Strategy

### Graph 2 tests

- empty ruleset rejected;
- pending/rejected/deactivated rules excluded;
- immutable snapshot hash reproducible;
- parameter provenance required;
- schema drift rejected;
- compiler output deterministic;
- unsafe SQL rejected;
- dbt validation failure stops before execution;
- transient retry is bounded and idempotent;
- LLM is never called by compiler/repair path;
- one rule error does not erase other results;
- result mismatch is explicit;
- rule `FAIL` still permits execution `SUCCEEDED`;
- results persist exactly once;
- dispatch failure preserves execution result.

### Graph 3 detector tests

- current run excluded from history;
- cross-dataset contamination prevented;
- failed historical runs excluded;
- cold start with small sample;
- constant baseline;
- stable baseline;
- single spike;
- gradual drift;
- volume drop;
- freshness delay;
- schema change;
- correlated table failures;
- execution errors;
- insufficient history.

### Aggregator tests

- duplicate signals within a family do not double count;
- weak signal produces `NORMAL` or `WATCH`;
- corroborated strong families produce `ANOMALY`;
- hard invariant produces `CRITICAL`;
- low reliability produces `INSUFFICIENT_HISTORY`;
- same config and evidence reproduce the same decision.

### Hypothesis tests

- invalid citations rejected;
- unsupported types rejected;
- confidence cap enforced;
- recommendation safety enforced;
- deterministic decision cannot be overridden;
- LLM failure uses fallback;
- no raw PII leakage;
- prompt/model versions persisted.

## 12. Observability and Audit

Every graph run must produce:

- correlation ID;
- request and trigger metadata;
- state transition timestamps;
- ruleset/schema/compiler/artifact hashes;
- retry history;
- per-node duration;
- error category;
- persisted result counts;
- anomaly detector versions;
- hypothesis model/prompt version;
- links between execution, anomaly and report artifacts.

The system should distinguish:

- data-quality failures;
- compiler failures;
- infrastructure failures;
- anomaly-analysis failures;
- hypothesis-generation failures.

## 13. Rollout Strategy

### Stage 0: Contract and compatibility preparation

- Freeze the target contracts.
- Inventory existing execution and anomaly fields.
- Define adapters from current persistence to target read models.
- Add feature flags for the new Graph 3 path.

### Stage 1: Graph 2 hardening

- Introduce ruleset snapshot.
- Add final rule contract validation.
- Remove production LLM repair routing.
- Add canonical result normalization.
- Persist execution status separately from rule status.

### Stage 2: Shared anomaly service

- Consolidate graph and dashboard anomaly calculations.
- Preserve existing API response fields through an adapter where necessary.
- Add detector config version and dataset-aware history filtering.

### Stage 3: Independent Graph 3 MVP

- Dispatch after persisted execution.
- Implement MVP detectors and deterministic aggregation.
- Persist normal/insufficient/anomalous decisions.
- Add retry endpoint/job for Graph 3 only.

### Stage 4: Hypothesis and UI integration

- Add structured hypothesis agent.
- Add validation and fallback.
- Surface evidence and limitations in the dashboard.
- Add steward feedback.

### Stage 5: Advanced detection

- Add distribution drift, change-point, seasonal and ML detectors only after sufficient clean history and evaluation data.

## 14. Rollback Strategy

- Keep the existing execution endpoint behind a feature flag during migration.
- Do not delete old persisted records.
- Store detector version on every new anomaly analysis.
- If Graph 3 fails, continue serving execution results and allow reanalysis.
- If the new compiler fails validation, route to the old compiler only during controlled migration testing; never silently mix artifacts within one execution run.
- Roll back by disabling dispatch to Graph 3, not by rerunning Graph 2 unnecessarily.

## 15. Acceptance Criteria

### Graph 2

- Only approved/active rules in an immutable snapshot execute.
- Compiler and validation path contain no LLM semantic repair.
- Same input snapshot produces replayable artifacts.
- Artifact/compiler failure stops before execution and records diagnostics.
- Technical retries are bounded and idempotent.
- Rule `FAIL` does not make infrastructure status `FAILED`.
- Rule `ERROR` does not erase unrelated results.
- Results persist before anomaly dispatch.
- Execution status and anomaly status are independent.

### Graph 3

- One canonical anomaly service is used by Graph 3 and API/dashboard.
- Current and incompatible historical runs are excluded from baselines.
- Every signal has score, reliability, evidence and detector version.
- Decisions are deterministic and reproducible.
- `INSUFFICIENT_HISTORY` is a valid persisted outcome.
- Duplicate signals are not double-counted.
- Hypothesis failure cannot erase anomaly decision.
- Hypotheses cite only existing evidence.
- Graph 3 can rerun without rerunning Graph 2.

## 16. Final Recommendation

Adopt the two-graph architecture from `graph-2-3.md` as the target design.

Implement Graph 2 first as a strict deterministic execution boundary. Remove LLM repair from its production path, while retaining bounded technical retries and diagnostics. Then implement Graph 3 as an independently persisted, versioned anomaly workflow. Start with a small deterministic detector set and add the Hypothesis Agent only after the evidence contract is stable.

This ordering delivers the most important safety properties first: approved semantics remain unchanged, execution results are auditable, anomaly analysis is rerunnable, and an LLM outage cannot invalidate a successful data-quality execution.
