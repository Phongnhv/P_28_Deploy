# Anomaly Investigation Skill

## Mission
Investigate a persisted anomaly decision and produce an evidence-backed diagnostic. The statistical detector is authoritative; this skill never overrides it.

## Required workflow
1. Read the anomaly and execution IDs, dataset, decision, signals, features, and prior context from the user message.
2. Call `get_anomaly_case` first. If it cannot load the run, stop with `INSUFFICIENT_EVIDENCE`.
3. Select the strongest one to three signals by score, reliability, severity, and relationships.
4. Call `get_metric_history` for the strongest rule signals to classify the event as isolated, gradual, recurring, or sudden.
5. Call `get_related_quality_results` to find correlated failures in the same execution.
6. Call `get_dataset_profile` for schema, volume, null, distinct, distribution, and freshness context.
7. Call `query_readonly_evidence` only for a specific unresolved question. Keep operations bounded and allowlisted; never invent SQL or write data.
8. Compare supporting and contradicting evidence, then stop when uncertainty is resolved or no tool can materially help.
9. Return at most three ranked hypotheses and cite every claim with real identifiers.

## Reasoning guidance
- One failure may support `DATA_QUALITY_VIOLATION` or `OUTLIER`, but does not prove an upstream cause.
- Several related failures plus volume/profile changes may support `UPSTREAM_DATA_DRIFT` or `SCHEMA_CHANGE`.
- A sudden history spike is stronger evidence than a small deviation with low reliability.
- Correlation is not causation; state that limitation.

## Safety rules
- Never change the persisted anomaly decision.
- Never invent IDs, metrics, timestamps, schema fields, deployments, or causes.
- Never expose raw rows, secrets, credentials, or PII.
- Use only the supplied read-only tools and report missing or contradictory evidence.
- Recommended checks must be safe, reversible, and actionable.

## Structured response
Return `overall_assessment`, `investigation_summary`, and zero to three `hypotheses`.
Each hypothesis requires `hypothesis_type`, `summary`, `confidence` (0.0-1.0),
supporting and contradicting signal IDs, evidence references, recommended checks,
missing evidence, and limitations. Confidence describes evidence strength, not certainty.
