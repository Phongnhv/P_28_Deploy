# Gate 2 MVP — Three-minute rehearsal

This is a local-MVP walkthrough. Do not display API keys, connection strings, or
raw row values while recording.

## 0:00 – 0:20 — Open the workspace

Show the signed-in **Data Steward Browser** view and the dataset overview.

## 0:20 – 0:45 — Profile the dataset

Show aggregate completeness, validity, uniqueness and governed-domain metrics.

## 0:45 – 1:20 — Generate proposals

Explain that the agent uses aggregate evidence and proposes typed DQ rules only.

## 1:20 – 1:55 — Human review

Approve, edit, or reject a proposal. Explain that approved rules are versioned and
that policy changes require re-review rather than silent replacement.

## 1:55 – 2:30 — Run approved checks

Run the approved rules, show counts and bounded failed IDs, then open the audit
trail. Mention that rule SQL is fixed-template, parameterized and read-only.

## 2:30 – 3:00 — Observe and conclude

Show quality trend, deterministic anomaly signals and the Data Explorer filters.
State that the local MVP has an adapter tested against Supabase PostgreSQL; cloud
deployment and automated remediation are future scope.
