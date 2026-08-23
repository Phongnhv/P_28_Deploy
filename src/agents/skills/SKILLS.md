# Anomaly Investigation Agent Skills

## `investigate_anomaly`

Use this skill to investigate a persisted anomaly decision. Start with
`get_anomaly_case`, then gather only the evidence needed to explain the
strongest signals. Use read-only tools, cite returned identifiers, and never
change the detector decision.

## Evidence rules

- Prefer independent evidence from history, related rule failures, and profile data.
- Do not invent signal IDs, rule IDs, metrics, or causes.
- State `INSUFFICIENT_EVIDENCE` when available data cannot support a cause.
- Never return raw PII or unrestricted database rows.

## Output rules

Return at most three hypotheses with confidence, supporting and contradicting
signal IDs, evidence references, limitations, and safe recommended checks.
