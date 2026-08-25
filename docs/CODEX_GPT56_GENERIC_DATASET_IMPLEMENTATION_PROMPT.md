# Codex GPT-5.6 Luna Implementation Prompt — Generic Versioned Datasets

You are Codex running on GPT-5.6 Luna in the RidePulse DQ repository. Implement the plan below completely, safely, and incrementally. Continue until the definition of done is satisfied or a genuine blocker requires user authority. Do not stop after analysis or after writing a plan.

## Repository checkpoint

- Workspace: `C:\Users\ADMIN\WorkPlace\Vinuni\AssignmentProject\P-028-deploy-fresh`
- Starting branch: `codex/deploy-feature-testing`
- Required baseline commit in branch history: `7de716c` (`feat: add governed dataset access contract`)
- Preserve existing user changes and do not rewrite history.
- Keep the configured OpenAI default model as `gpt-5.6-luna`.

Before editing, verify the branch, commit, worktree status, runtime configuration, and relevant migrations. If the worktree contains new user changes, preserve them and work around them.

## Objective

Remove the remaining NYC Yellow Taxi/single-dataset assumptions from the upload, versioning, profiling, Graph 1, Graph 2, Graph 3, dbt, and Data Explorer paths. A user must be able to upload an arbitrary CSV or Parquet dataset, profile an immutable version, approve rules that reference only real columns, execute those rules against the exact same authorized version, and view version-correct results without any query to a taxi-specific table.

Maintain backward compatibility for the existing taxi demo while making the canonical runtime version-aware and dataset-agnostic.

## Confirmed defects

Treat these as verified facts, not hypotheses:

1. `src/agents/nodes/test_runner_node.py` executes PostgreSQL rules against hardcoded `public.trips_canonical` and validates columns against a fixed taxi allowlist.
2. Generic upload in `src/api/routes.py` stores a local/object-storage file and computes a profile, but does not establish a versioned execution resource used by Graph 2.
3. `src/agents/nodes/persist_report_node.py` can mark Graph 2 successful when every rule result is `ERROR`.
4. One failed PostgreSQL statement leaves the transaction aborted, causing later rules to fail with `InFailedSqlTransaction`.
5. `SourceRowModel`, `src/services/supabase_dataset.py`, the existing dbt staging model, the row API, and the current Data Explorer UI assume taxi columns.
6. `get_dataset_rule_policy()` can fall back to the NYC Taxi policy for an unknown dataset when columns are not supplied.
7. The legacy proposer still contains taxi domain context and a taxi dictionary fallback.
8. `src/worker.py` contains a legacy taxi manifest path and a non-dataset-scoped `DELETE FROM trips_raw`; Docker Compose can still invoke this worker.
9. Schema-split migration `008` moves canonical resources away from `public`, while several Python/dbt references explicitly use the `public` schema.
10. Existing governance models already provide `dataset_versions`, `profile_runs`, `governed_artifacts`, workspace membership, grants, audit events, and version-aware authorization. Do not create duplicate concepts without proving the existing model is insufficient.

## Non-negotiable architecture decisions

### Version identity and lineage

- Every new upload creates or resolves:
  - `workspace_id`
  - logical `dataset_id`
  - immutable `dataset_version_id`
  - immutable checksum
  - source artifact reference
  - normalized schema manifest and schema hash
  - row count
- Every profile, Graph 1 run, ruleset/review snapshot, Graph 2 execution, Graph 3 analysis, report, and artifact must resolve to the same `dataset_version_id`.
- Never silently substitute the latest version or profile when a request specifies an ID.
- Reject cross-dataset, cross-version, and cross-profile lineage mismatches before execution.

### Reuse the governance schema

- Use `DatasetVersionModel.source_metadata_json` for the normalized source manifest unless a small additive field is demonstrably necessary.
- Use `GovernedArtifactModel` with an explicit source artifact type such as `SOURCE_DATASET` for the immutable CSV/Parquet object.
- Use `ProfileRunSnapshotModel.schema_json` and `metrics_json` for immutable profile evidence.
- Extend existing run models with additive version/profile lineage fields only where needed. Do not replace the existing authorization model.
- Any migration must be additive, idempotent, reversible where practical, and must not update/delete user data indiscriminately.

### Source storage and execution

- Do not force arbitrary datasets into the fixed taxi `source_rows` shape.
- Do not create one PostgreSQL table per upload in this implementation.
- The canonical first implementation must execute against the immutable CSV/Parquet artifact through a backend-owned adapter.
- Production must load the artifact from object storage. Local development may use a verified local file fallback only when the environment is explicitly local/development/test.
- Object keys must be versioned and non-overwriting, for example:
  `datasets/{workspace_id}/{dataset_id}/versions/{dataset_version_id}/{checksum}/{safe_filename}`.
- Object-storage upload failures must fail closed. Do not log success or persist a usable version when the source artifact was not stored.
- Downloaded content must be verified against stored size/checksum before profiling or execution.
- Never return storage credentials, database URLs, internal object keys, or unrestricted signed URLs to the frontend.

### Deterministic rule execution

- The LLM must never choose a physical table, object path, SQL source, or database credential.
- Resolve the execution resource server-side after workspace membership and `RUN_ANALYSIS` authorization.
- Build the column allowlist from the immutable schema manifest for the requested dataset version.
- Validate `column`, cross-field `target_column`, fingerprint columns, and all rule parameters against that manifest.
- Compile and execute supported rules deterministically. Do not accept arbitrary SQL from the browser or model.
- Use a per-rule savepoint/isolated execution boundary so one rule error does not poison later rules.
- Bound failure samples and persist only row identifiers or masked/sanitized samples allowed by policy.

### Execution status semantics

- Rule `PASS`: rule executed and found no violations.
- Rule `FAIL`: rule executed and found data violations. This is not an execution failure.
- Rule `ERROR`: the rule could not execute or its result could not be trusted.
- Graph 2 `SUCCEEDED`: every selected rule executed to `PASS` or `FAIL`.
- Graph 2 `PARTIAL`: at least one rule produced usable `PASS`/`FAIL` evidence and at least one rule produced `ERROR`/`SKIPPED` because of execution health.
- Graph 2 `FAILED`: no selected rule produced usable evidence, or orchestration/validation/resource resolution failed.
- Never report zero checked rows as a successful execution unless a genuine, verified empty dataset version was intentionally executed and the rule semantics permit it.
- Graph 3 may run after `SUCCEEDED`; it may run after `PARTIAL` only with explicit execution-health signals and clear evidence limitations.
- If Graph 2 is `FAILED`, do not ask the LLM to invent data hypotheses. Persist a deterministic execution diagnostic and stop or produce a clearly marked execution-health-only report.

### Authorization and artifact inheritance

- Use the existing workspace membership and dataset grant service for all new endpoints and internal resource resolution.
- Logical dataset grants inherit to versions; a version-specific grant applies only to that version, following the current effective-permission model.
- Source artifact access is operation-specific:
  - discovering metadata requires `DISCOVER`;
  - profile data requires `VIEW_PROFILE`;
  - reports/dbt/report artifacts require `VIEW_REPORTS`;
  - raw/sample rows require `VIEW_ROWS` plus masking/audit;
  - execution requires `RUN_ANALYSIS`;
  - grants/version management requires `MANAGE`.
- Do not use one blanket `VIEW_REPORTS` check for every governed artifact type.
- Backend database access can remain trusted/bypass-RLS, but application-level authorization and RLS contracts must agree. Frontend hiding is never authorization.

## Implementation phases

Implement each phase, run its tests, inspect the diff, and create a focused commit before continuing. Use the existing branch unless the user has explicitly requested another one.

### Phase 1 — Correctness and containment

1. Fix Graph 2 status aggregation and persistence according to the status semantics above.
2. Ensure rule-level execution errors do not abort later rules.
3. Reject a missing source resource, checksum mismatch, missing required column, or unexplained zero-row source before reporting success.
4. Remove unknown-dataset fallback to the NYC policy. If no explicit policy exists, infer only from supplied immutable profile/schema evidence; otherwise return no domain policy.
5. Make every deletion in the legacy worker dataset-scoped, or disable the unsafe obsolete worker path in favor of the current job runner. Preserve a documented compatibility entrypoint if Docker Compose still needs one.
6. Add regression tests for all status combinations and transaction recovery.

Phase 1 acceptance:

- all `ERROR` results produce Graph 2 `FAILED`;
- mixed usable/error results produce `PARTIAL`;
- all `PASS`/`FAIL` results produce `SUCCEEDED`;
- one SQL error does not change subsequent independent rule results;
- no worker operation can delete rows belonging to another dataset.

### Phase 2 — Immutable versioned upload contract

1. Add a canonical workspace/version-aware upload service and endpoint. Prefer:
   `POST /api/v1/workspaces/{workspace_id}/datasets/import`.
2. Keep the legacy upload endpoint as a thin compatibility adapter if tests/UI still depend on it; do not maintain two separate implementations.
3. Validate extension, MIME/format, size, filename, checksum, and readable schema before committing the version.
4. Upload the source object to a versioned immutable key and return a typed internal artifact reference.
5. Create `DatasetVersionModel` and `GovernedArtifactModel(SOURCE_DATASET)` records atomically with ownership/governance linkage.
6. Persist a normalized schema manifest in `source_metadata_json`, including at least column name, logical type, physical type, nullable, ordinal, semantic role when known, and sensitivity/masking classification when known.
7. Compute schema hash deterministically from canonical JSON.
8. Queue profiling using `dataset_version_id`, not only `dataset_id`.
9. Make retries idempotent by workspace, dataset, checksum, and idempotency key. Do not duplicate versions/artifacts/jobs.
10. Add governance audit events for upload, version creation, profile start/completion/failure, and source artifact registration without storing raw PII or secrets.

Phase 2 acceptance:

- two files under one logical dataset create two immutable versions;
- retrying the same idempotency key does not duplicate state;
- a failed object upload leaves no READY/executable version;
- every READY version resolves to exactly one verified source artifact;
- legacy taxi import still works through the same canonical service.

### Phase 3 — Versioned profiling

1. Refactor uploaded-file profiling to consume the resolved source artifact for a specific version.
2. Persist an immutable `ProfileRunSnapshotModel` with version-correct schema and aggregate metrics.
3. Do not overwrite historical profile runs.
4. Keep the legacy `profiles`/`column_profiles` records only as a compatibility projection if needed by existing screens; make the versioned snapshot authoritative.
5. Ensure Graph 1 receives the explicitly selected `profile_run_id` and version schema.
6. Sanitize/mask sample values before persistence. Do not expose raw values by default.

Phase 3 acceptance:

- profiles for version 1 and version 2 remain independently retrievable;
- specifying profile run A never returns profile run B;
- schema changes between versions can be computed from stored manifests;
- profiling does not depend on taxi columns.

### Phase 4 — Generic Graph 1 contract with minimal agent changes

1. Thread `workspace_id`, `dataset_version_id`, and `profile_run_id` through Graph 1 creation, state, persistence, serialization, audit, and API contracts.
2. Replace the taxi fallback domain context with context derived from the selected semantic contract/data dictionary.
3. Use taxi-specific context only when the selected dataset/version is explicitly classified as the taxi domain.
4. Always provide the model with the exact allowed table alias and columns from the immutable version schema.
5. Server-validate every proposed rule and edited rule. Reject nonexistent columns/targets; never silently repair or substitute a different column.
6. Keep GPT-5.6 Luna and structured output. Do not add heuristic post-generation behavior that changes the model's approved rule meaning.
7. Preserve immutable rule review snapshots linked to the selected version/profile.

Phase 4 acceptance:

- an e-commerce dataset produces no taxi-only columns or rationale;
- a hallucinated column is rejected with a safe actionable error;
- approved rules cannot be reused against a different dataset version without explicit compatibility validation/new snapshot;
- existing taxi Graph 1 regression remains valid.

### Phase 5 — Generic Graph 2 execution adapter

1. Introduce a small source adapter interface that can:
   - load/inspect a verified version artifact;
   - expose schema and row count;
   - execute the supported deterministic rule types;
   - return bounded failure identifiers and timing/evidence;
   - clean up temporary resources.
2. Implement CSV and Parquet adapters using already-supported repository dependencies where practical. Avoid loading unbounded data into memory; use projection/chunking/columnar reads when available.
3. Refactor `test_runner_node.py` to resolve the adapter by dataset version instead of PostgreSQL dialect or `trips_canonical`.
4. Remove the taxi `CANONICAL_COLUMNS` execution allowlist. Build it from the version schema manifest.
5. Maintain supported rule types: not-null, uniqueness, numeric range, accepted values, row count, freshness, cross-field comparison, and duplicate fingerprint where present in current contracts.
6. Make null, timestamp, numeric, categorical, and comparison behavior deterministic and covered by tests.
7. Ensure Graph 2 records lineage: workspace, dataset, version, profile, ruleset/review snapshot, source checksum, compiler version, and artifact hash.
8. Preserve legacy taxi support through a compatibility adapter, not a global default.

Phase 5 acceptance:

- repository runtime code performs no generic-upload execution query against `public.trips_canonical`;
- taxi and non-taxi datasets both produce trusted nonzero checked counts;
- all approved rules execute against the exact checksum/version reviewed in Graph 1;
- cross-version and cross-dataset execution attempts are rejected;
- results remain immutable and reproducible from recorded lineage.

### Phase 6 — Generic dbt artifacts

1. Remove the requirement that every generated project use `stg_trips` or source `public.trips_canonical`.
2. Generate deterministic per-run dbt source/model metadata from the immutable schema manifest and a safe internal alias.
3. Continue validating generated dbt YAML/project structure before execution.
4. Do not let the LLM generate arbitrary source SQL.
5. Store dbt artifacts as governed, version-linked artifacts with checksums.
6. If dbt CLI is unavailable in local/test, preserve a clearly labeled deterministic adapter fallback; never label `dbt_status=SUCCESS` when dbt did not run.

Phase 6 acceptance:

- generated dbt artifacts contain no taxi model/source unless running the explicit taxi compatibility fixture;
- `dbt_status`, metrics status, and execution mode accurately describe what ran;
- artifact retrieval enforces `VIEW_REPORTS` and version lineage.

### Phase 7 — Dynamic Data Explorer

1. Replace the taxi-specific row DTO and frontend table with schema-driven fields.
2. Require `workspace_id`, `dataset_id`, `dataset_version_id`, and optional `profile_run_id` in the canonical explorer contract.
3. Return schema, metadata, selected immutable profile, history, rules/reports/artifact summaries, and only authorized bounded rows.
4. Generate filters and sorting from allowlisted schema/type metadata. Do not accept arbitrary SQL/filter expressions.
5. Remove frontend quality heuristics such as negative fare/distance and invalid payment. Show quality status from persisted rule results/evidence.
6. Require `VIEW_ROWS` for row retrieval, enforce a hard maximum, mask sensitive columns, and append an audit event for row/sample access.
7. Do not implement a broad visual redesign. Preserve the current design language and focus on correctness and dynamic rendering.

Phase 7 acceptance:

- the explorer correctly renders both taxi and e-commerce schemas;
- a user with `VIEW_PROFILE` but not `VIEW_ROWS` sees profile/schema but no rows;
- sorting/filtering by a nonexistent or unauthorized column is rejected;
- sensitive values are masked and access is audited.

### Phase 8 — Migration, backfill, compatibility, and cleanup

1. Add an idempotent migration/backfill for existing logical datasets and the taxi demo:
   - attach them to the intended workspace/owner;
   - create a version only when a verified source artifact exists;
   - never fabricate READY source lineage from profile metadata alone;
   - preserve historical IDs where APIs depend on them.
2. Reconcile schema-qualified SQL with migration `008`. Prefer a centralized, safely resolved relation strategy; do not scatter replacements across the codebase.
3. Remove or quarantine obsolete taxi-only runtime paths after compatibility tests prove they are unused.
4. Taxi fixtures, demos, and mock API data may remain explicitly labeled fixtures. They must not be imported by canonical production execution paths.
5. Update focused architecture and operational documentation. Do not rewrite unrelated reports.

## Required tests

Create a layered test suite, not only mocked happy paths.

### Unit tests

- canonical schema serialization and schema hash stability;
- storage key safety and non-overwrite behavior;
- object upload/download checksum verification and fail-closed behavior;
- inferred policy never falls back to taxi for an unknown schema;
- proposed/edited rule column validation;
- deterministic execution for every supported rule type;
- per-rule error isolation;
- Graph 2 run status matrix;
- artifact-type permission mapping;
- masking and row-limit enforcement.

### Integration tests

Use disposable PostgreSQL/local object storage where available. Never run destructive tests against production.

- taxi fixture and a non-taxi e-commerce fixture;
- upload → version → profile → Graph 1 lineage → approved rules → Graph 2 → Graph 3/report;
- two versions of one dataset with a schema change;
- checksum mismatch and missing object;
- cross-dataset/profile/version substitution rejection;
- private/share/revoke behavior across source, profile, rule, report, and artifact;
- one dataset's cleanup cannot affect another dataset;
- migration/backfill idempotency.

### API/UI smoke tests

- run local FE and BE against the configured Supabase dev environment, never production;
- verify session/workspace authorization and CSRF behavior;
- verify Data Explorer dynamic rendering for taxi and non-taxi data;
- verify Graph 2 displays honest `SUCCEEDED`, `PARTIAL`, or `FAILED` state;
- verify Graph 3/report labels execution-health limitations correctly;
- use the repository's required browser harness for actual browser interaction.

### Regression suite

- run the full existing test suite;
- preserve the prior baseline of 248 passing tests and explain any intentional skip/count change;
- add focused tests for every corrected defect;
- do not weaken assertions or delete tests merely to obtain green output.

## Security and data-safety constraints

- Never print or commit `.env` values, database URLs, API keys, object-storage credentials, or raw sensitive rows.
- Never execute destructive SQL against an unverified target.
- Use transaction rollback for integration fixtures and verify zero residue.
- Do not expose internal tables directly to the frontend.
- Keep raw/sample-row access separate from profile/report access.
- Audit uploads, version creation, profiling, rule review, analysis, report access/download, row/sample access, sharing, revoke, and permission changes.
- Audit details must be sanitized and append-only.
- A revoked grant must immediately block all inherited profile/rule/report/artifact/row operations for the affected version scope.

## Scope boundaries

- In scope: backend contracts, version/resource lineage, generic profiling/execution, minimal agent cleanup, generic dbt artifacts, dynamic Data Explorer behavior, authorization/audit integration, migrations, tests, and compatibility.
- Do not build a new public-internet publishing mechanism.
- Do not redesign the Overview or Audit Logs visual pages beyond changes strictly required for contract compatibility.
- Do not introduce arbitrary user SQL or LLM-generated physical schemas.
- Do not replace the existing workspace/grant model with a boolean `public` flag.

## Work and communication protocol

1. Start with a concise implementation plan derived from this prompt and keep it updated.
2. Inspect existing code before editing; reuse services, models, migrations, tests, and object-storage infrastructure.
3. Make small coherent changes and validate each phase before moving on.
4. Create one focused git commit per completed phase. Do not amend the starting checkpoint.
5. Report material findings early, especially if a current schema field cannot safely represent required lineage.
6. Do not ask for confirmation for ordinary reversible implementation steps. Ask only if a required action would affect production, destroy data, require new external credentials, or materially expand scope.
7. If Supabase dev is configured, verify it is not production before any migration or integration write. Use rollback-only contract tests where possible.

## Definition of done

The task is complete only when all of the following are true:

- arbitrary CSV and Parquet uploads create immutable authorized dataset versions and verified source artifacts;
- versioned profiles are immutable and explicitly selectable;
- Graph 1 proposes only schema-valid rules without taxi contamination for non-taxi data;
- approved rules execute against the exact selected source version without `trips_canonical` hardcoding;
- Graph 2 status accurately distinguishes data failures from execution failures;
- Graph 3/report behavior is evidence-safe for success, partial execution, and failure;
- dbt artifacts and Data Explorer are schema-driven;
- authorization, masking, audit, revoke, and artifact inheritance tests pass;
- taxi regression and at least one non-taxi end-to-end integration pass;
- the full test suite passes with no unexplained regressions;
- local FE/BE smoke testing against Supabase dev succeeds;
- the worktree is clean, commits are listed, migrations and rollback considerations are documented, and no secrets or generated test artifacts were committed.

At handoff, provide: outcome, commit list, migrations applied or intentionally not applied, exact tests and counts, end-to-end run IDs, remaining limitations, and safe deployment/rollback instructions.
