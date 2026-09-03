# Netflix UI / API / LangSmith verification — 2026-09-03

## Environment and source

Local FE `http://127.0.0.1:5179` and BE `http://127.0.0.1:8019` were restarted after merge `98c63c5f`. Existing Supabase schema and object storage were used; no upload, source edits, migrations, or seed were performed. User explicitly authorized LLM/DeepAgent processing and LangSmith input/output tracing. API uses `AGENT_MODE=graph`, real `gpt-5.6-luna`, DeepAgent proposer/investigation, with deterministic evaluation and legacy proposer fallback disabled.

Dataset selected via UI: `netflix_titles`, ID `dataset-import-cc2679b2f8b04e0bbef0`, source version `dv-71cde73f588c4ff89760303e`. SHA256: `df1f4ad2027a5a14c3a33932ef0d4054565ff88adf92b7f263601b70fdc6f3f3`. Independent pandas read of the checksum-verified source found 8,807 rows, 12 columns, no duplicate full rows, release years 1925–2021. Null counts: director 2,634; cast 825; country 831; date_added 10; rating 4; duration 3. Other columns have zero nulls.

## Baseline after merge

Workflow `workflow-d759d82eded1cc06f96a69308e3c635a` was created and completed through UI. Its profile ID is `profile-workflow-d759d82eded1cc06f96a69308e3c635a`. Dataset/version/profile/checksum remain consistent through all graph artifacts and execution.

- Graph1A trace `01a066a4-7f79-7760-a768-a3cf6d2abbde`: two successful Luna calls, no foreign dataset ID in model inputs, exactly 12 Netflix columns. However, the digest rounded rating/duration null percentages to zero and falsely tagged every column `no_nulls` when raw `null_count` was absent. Semantic output consequently marked rating/duration as nonnullable. The digest file was unchanged by the merge: this is a pre-existing bug exposed by Netflix, not evidence that merge changed source routing.
- Graph1B trace `01a066a8-a07e-7063-80fb-a47c26592d0d`: DeepAgent middleware and two Luna calls present; no span errors or rule proposal errors. Eight proposals persisted, and UI Approve all approved exactly those eight for this workflow.
- Graph2 execution `run_3e03379b`: 8 × 8,807 = 70,456 checks; six PASS, two FAIL; rating nulls 4 and duration nulls 3. Independently verified row IDs: rating 5990, 6828, 7313, 7538; duration 5542, 5795, 5814. UI displays at most three IDs per rule. This is the file/SQL runner, not evidence of a dbt CLI invocation.
- Graph3 trace `01a066ad-797a-76c3-94fa-16a207e1f7b5`, anomaly `anom-7d8a74852c9a`: `INSUFFICIENT_HISTORY`, score 0.4, LOW, 17 signals. Hypothesis status `NOT_REQUIRED`; report writer used real LLM. Report correctly distinguished 8,807 rows from 70,456 checks and acknowledged two failed rules. It lacked rule names and failure rates because the report prompt omitted available names and did not derive missing rates from measured counts.
- Step6 UI: 6/8 PASS, 70,456 checks, seven failed checks, `INSUFFICIENT_HISTORY`. Aggregate score 92.5 does not mean all data is clean.

## Targeted local corrections

1. `profile_digest.py`: retain nonzero null percentage precision; emit `no_nulls` only when measured count/rate establishes zero with no contradictory positive value. Missing measurements no longer imply zero.
2. `report_writer_node.py`: include persisted rule titles in failed-rule context and derive missing failure rate from positive checked count and measured failed count; do not invent a rate for error/empty execution or mutate caller data.
3. Step6 Graph3 decision label: replace underscores with spaces and permit wrapping so INSUFFICIENT_HISTORY remains readable inside the KPI card. Build passed; canonical decision is unchanged. The optional UX skill's local search dataset was unavailable; ordinary CSS text wrapping was used.

40 focused regression tests passed; Ruff passed. Backend restarted again to load these corrections. Source data, model, detector thresholds and validators were not altered.

## Post-fix UI run

Workflow `workflow-d04b1a7e8b6198ea15e6b151108ca522`, profile `profile-workflow-d04b1a7e8b6198ea15e6b151108ca522`, same Netflix source/checksum.

Graph1A trace `01a066b1-4e08-7272-afba-ccf5796a4dc6` confirms new digest evidence: rating null percentage 0.04541841716816169 and duration 0.034063812876121265; no `no_nulls` signal for any of the six columns with nulls. Fresh Luna output shown on UI marks rating and duration nullable. All 12 columns and dataset scope remain correct.

Graph1B trace `01a066b3-6c91-7bb0-b9eb-ea65f1a65fbb`: six rules, no proposal errors; 18 spans, two Luna calls, no trace errors. Nullable rating/duration no longer receive NOT_NULL proposals. UI bulk approval approved exactly six rules. Generated checks: nonnegative release_year; NOT_NULL show_id, type, title, listed_in, description.

Graph2 `run_9f8dc21a`: six PASS, 52,842 checks, zero failed checks, all on the same Netflix version/checksum and new workflow profile. Independent source statistics agree with every rule outcome. Workflow and all six associated jobs reached COMPLETED/SUCCEEDED.

Graph3 trace `01a066b6-fcb5-76a1-ad2a-9fb9db07b435`, anomaly `anom-1ec492426d6d`: INSUFFICIENT_HISTORY, score 0, confidence 0.4143, LOW, 13 signals, NOT_REQUIRED hypotheses. One successful Luna report call, `report_source=LLM`, no span errors. Report identifies the correct execution and dataset, 8,807 source rows and 52,842 checks, and explicitly says all rules passing does not exclude issues outside their coverage. Investigation DeepAgent is configured, but its hypothesis call is legitimately skipped on this detector outcome.

Final Step6 UI displays Netflix, 6/6 PASS, 52,842 checks, zero failed checks, and INSUFFICIENT_HISTORY. Screenshot: `scratch/netflix-final-results.png`; full UI report: `scratch/netflix-final-graph3-ui.txt`. The final workflow's artifacts and traces have been exported locally.

The report-context correction for failed rules was additionally replayed against the baseline execution: rating/duration titles and derived 0.05% / 0.03% rates are present. This replay plus regression tests verifies that branch of prompt construction; the final live run has no failed rules, so it does not itself exercise that branch.

## Evidence and limits

Local detailed artifacts are stored under ignored `scratch/`: `netflix-independent.json`, `netflix-root-traces.json`, `netflix-understanding-trace.json`, `netflix-rules-trace.json`, per-workflow `*-evidence.json`, `netflix-baseline-graph3-ui.txt`, and `netflix-baseline-results.png`.

Semantic roles and business assumptions remain model inferences. This run does not prove a complete business rule catalog: the source contains rating values `74 min`, `84 min`, `66 min`, which basic null/range checks do not cover. Passing the executed rules must not be represented as certifying every Netflix value. Graph3 may legitimately skip hypothesis LLM calls when detector gating returns insufficient history.
