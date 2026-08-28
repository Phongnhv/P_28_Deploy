---
name: anomaly-investigation
description: Investigate a persisted data quality anomaly decision and produce an evidence-backed diagnostic hypothesis using read-only tools.
---

# anomaly-investigation

## Overview

This skill guides the investigation of data quality anomalies detected by statistical models. The statistical detector decision is authoritative and must never be altered or overridden. The purpose of this investigation is to gather evidence, identify root causes, and produce ranked, evidence-backed diagnostic hypotheses for Data Stewards.

## Instructions

### 1. Parse Context & Anomaly Case
- Read the anomaly ID, execution ID, dataset ID, aggregate decision, signals, and feature metrics provided in the user message.
- Start by invoking `get_anomaly_case` with the `anomaly_run_id`.
- If the anomaly run cannot be loaded or no signals exist, return `INSUFFICIENT_EVIDENCE`.

### 2. Select High-Impact Signals
- Identify and prioritize the top 1 to 3 strongest signals based on anomaly score, reliability score, and severity level.
- Group related signals by affected column, table, or quality dimension.

### 3. Collect Historical & Profiling Evidence
- Call `get_metric_history` for the primary failing rules to classify the pattern (isolated spike, gradual drift, recurring issue, or sudden drop).
- Call `get_related_quality_results` using the `execution_run_id` to inspect correlated failures across other rules in the same execution.
- Call `get_dataset_profile` for dataset-level and column-level baseline metrics (null rates, distinct values, row counts, freshness).

### 4. Query Targeted Read-only Evidence (Optional)
- If a specific ambiguity remains, call `query_readonly_evidence` with bounded operations (`failed_rules` or `rule_summary`).
- Keep queries strictly read-only; never execute arbitrary or mutating queries.

### 5. Formulate & Rank Hypotheses
- Synthesize all collected evidence and classify the root cause into allowed categories:
  - `SYSTEM_BUG`: Implementation bugs or pipeline failures.
  - `SCHEMA_CHANGE`: Column additions, deletions, or data type changes.
  - `UPSTREAM_DATA_DRIFT`: Upstream source data distribution shifts.
  - `ML_MODEL_DRIFT`: Inferences or feature drift from upstream models.
  - `OUTLIER`: Rare extreme values with low global impact.
  - `DATA_QUALITY_VIOLATION`: Specific business rule breaches without schema alterations.
  - `UNKNOWN`: Unexplained anomaly when evidence is inconclusive.
- Return at most 3 ranked hypotheses sorted by confidence.

## Reasoning Guidance

- A single rule failure generally supports `DATA_QUALITY_VIOLATION` or `OUTLIER`, but is insufficient to claim upstream pipeline failures.
- Multiple correlated failures combined with significant volume or null rate changes strongly support `UPSTREAM_DATA_DRIFT` or `SCHEMA_CHANGE`.
- A sudden deviation from established historical baselines is stronger evidence than a minor shift with low reliability.
- Correlation is not causation: always state limitations when evidence is circumstantial.

## Safety Rules

- **Authoritative Decision**: Never change or override the persisted anomaly decision (`decision`, `score`, `severity`).
- **Citation Integrity**: Every supporting and contradicting signal ID must reference a real, existing signal from the case. Never invent synthetic IDs or metric values.
- **Data Privacy**: Never expose raw data rows, database credentials, secrets, or Personally Identifiable Information (PII).
- **Tool Confinement**: Use only the provided read-only tools. Report any missing or conflicting evidence explicitly.
- **Non-Destructive Actions**: All recommended checks (`recommended_checks`) must be safe, actionable, and non-destructive for engineers and stewards.

## Response Format

Return a structured response adhering to `AnomalyInvestigationResponse`:
- `overall_assessment`: High-level executive diagnostic summary.
- `investigation_summary`: Detailed synthesis of findings from tools and metric checks.
- `hypotheses`: A list of 0 to 3 `InvestigationHypothesis` objects containing:
  - `hypothesis_type`: One of the valid categories.
  - `summary`: Concise explanation of the root cause.
  - `confidence`: Numerical score between `0.0` and `1.0`.
  - `supporting_signal_ids`: Array of signal IDs validating the hypothesis.
  - `contradicting_signal_ids`: Array of signal IDs challenging the hypothesis.
  - `evidence_refs`: Specific column names, table names, or rule IDs referenced.
  - `recommended_checks`: Safe verification steps for data engineers/stewards.
  - `missing_evidence`: Any data points or logs required for higher certainty.
  - `limitations`: Uncertainties and assumptions.
