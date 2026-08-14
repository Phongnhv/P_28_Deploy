# Local MVP Agent — remaining work

> **Scope:** Gate 2 MVP running locally. The Agent must safely propose and execute
> typed data-quality checks against the fixed registered dataset. Cloud hosting,
> production monitoring, RAG and ML-based anomaly detection are deferred.

## 1. Completion target

The local Agent is complete when it supports this bounded workflow:

```text
persisted aggregate profile
  -> guarded structured LLM proposal
  -> Pydantic validation and evidence checks
  -> human review
  -> typed-rule compilation
  -> read-only execution
  -> bounded persisted result and audit
```

It recommends rules only. It never edits raw rows, self-approves a rule, accepts a
browser prompt, or executes SQL written by an LLM.

## 2. Current integration

`main` already has two LangGraph shapes:

- Proposal graph: `raw_profiler -> profiler_digest -> rule_proposer -> hitl_gate`.
- Execution graph: `test_generator -> validate_sql -> repair loop -> test_runner ->
  anomaly_detector -> persist_report`.

The dashboard now remains the public workflow owner. `dashboard_agent_workflow.py`
creates an aggregate-only `ProposalEvidence` payload, invokes the structured proposal
graph in `AGENT_MODE=graph`, validates/maps its output and persists it in
`RuleProposalModel`, which is what the UI reads. `AGENT_MODE=mock` keeps the same
endpoint deterministic for offline UI and automated testing.

Dashboard DQ execution intentionally uses its typed-rule compiler and persists
`DqRunModel`/`DqResultModel`; it does not execute free-form SQL returned by the legacy
execution graph or its LLM repair loop.

## 3. P0: required agent behaviour

### 3.1 Enter the proposal graph only from a completed profile — implemented

The public backend should request proposals only for a dataset with a persisted,
completed profile. The graph input must contain a dataset ID and approved aggregate
evidence, not a user prompt or direct connection details supplied by the browser.

Reject requests when the dataset has not completed ingestion/profile, and create a
safe failed job/audit event rather than attempting a partial proposal.

### 3.2 Define and enforce the evidence allow-list — implemented

Create one explicit `ProposalEvidence` model used to build every LLM request. It may
contain, for example:

- dataset/schema version and stable column names;
- row count, null rate, distinct count and inferred type;
- numeric min/max or aggregate distribution summaries;
- stable evidence keys that the proposal can cite.

It must exclude raw rows, `source_row_id`, failed row identifiers, location/time
tuples, artifact paths/URLs, credentials, full manifest content and free-form browser
text. Add a test that serializes the actual LLM payload and proves excluded fields are
absent.

### 3.3 Use a real structured-output adapter with a mock test mode — partially implemented

`AGENT_MODE=graph` invokes the configured backend-only structured provider, and
`AGENT_MODE=mock` is deterministic. Remaining: provider timeout/request-size limits
and a manually recorded redacted graph-mode smoke run.
The response must be parsed into Pydantic models, not accepted as arbitrary JSON or
SQL. For tests and offline UI development, retain an explicit `LLM_MODE=mock` adapter
that returns deterministic, valid typed rules.

The adapter needs:

- an approved model name, 30-second timeout and bounded request size;
- no automatic paid retry;
- safe handling of provider timeout, malformed output and refusal;
- recorded model metadata without logging secrets or raw evidence.

### 3.4 Validate proposal semantics before persistence — implemented for the five dashboard templates

Accept only two to five rules from the five supported types:

- `not_null`;
- `numeric_range`;
- `accepted_values`;
- `cross_field_comparison`;
- `duplicate_fingerprint`.

For every rule, verify column/table identifiers against metadata, parameter shape and
range, text limits, confidence range, and citations to existing evidence keys. Reject
unknown fields, unsupported types, missing evidence, duplicate rule identities and
oversized descriptions. Persist rejected model output only as a redacted safe error,
not as an executable proposal.

### 3.5 Make HITL the only promotion path — implemented

Every valid agent proposal starts `PROPOSED`. Only a Steward action can create the
approved immutable rule version. Editing must produce a clear steward-owned rule
specification while retaining an audit link to the original proposal. Rejected and
pending rules must be impossible to compile or run.

### 3.6 Replace agent-generated SQL with a deterministic compiler — implemented for dashboard execution

`test_generator` must be treated as a compiler over `RuleSpec`, not an agent that is
free to compose queries. Generated SQL must come from fixed templates with:

- allow-listed table and column identifiers;
- bind parameters for all values;
- one aggregate/result query per supported template;
- a predictable result schema containing counts and bounded IDs only.

This compiler, rather than LLM repair, is the execution source of truth for local MVP.

### 3.7 Enforce execution boundary before the runner — implemented for dashboard execution

Validation must reject anything outside the compiler output contract: non-`SELECT`,
comments, multi-statements, DDL/DML, unapproved identifiers or disallowed functions.
The runner receives only approved rules and safe compiled queries. It persists
success/failure and limited sample IDs, then adds an audit event.

## 4. P1: simplify or constrain existing graph features

### 4.1 Repair loop

The current execution graph includes `llm_repair`. Do not let it repair arbitrary SQL
in the local MVP. Either disable it or restrict it to choosing a new typed compiler
template/parameter set that is revalidated before execution. It must have a fixed
retry maximum, safe error state and audit record.

### 4.2 Anomaly detector and report nodes

The nodes are useful extensions, but they should not block the core DQ flow. Initially
their output can be a persisted aggregate rule-failure summary. A separate anomaly
classification, diagnosis agent and recommendation UI should wait until rule results
and guardrails are stable.

### 4.3 Legacy chat agent

`/api/v1/chat` is unrelated to the Gate 2 primary user journey and introduces a
free-form prompt surface. Keep it disabled from the product UI or clearly separate it
as a developer-only diagnostic endpoint. It must not receive dataset rows or have
access to rule execution.

## 5. Required tests and local evidence

Add tests for:

- proposal graph rejects missing/incomplete profile;
- real adapter is mocked in automated tests, while mock mode remains deterministic;
- serialized LLM payload contains only approved aggregate evidence;
- malformed, unsupported or fabricated-evidence model output is rejected;
- each of the five rule types validates, compiles and has a failure-path test;
- pending/rejected rules cannot reach compiler or runner;
- SQL boundary rejects DDL/DML/comments/multi-statements and unknown identifiers;
- runner returns no raw values and at most 20 IDs;
- provider, validation and runner failures yield stable public errors and audit events;
- an end-to-end local case persists proposal, human review, approved rule, results and
  audit history.

## 6. Definition of done

- The dashboard can request a real structured proposal from a completed local profile.
- Every LLM proposal is validated against aggregate evidence and starts `PROPOSED`.
- Only Steward-approved typed rules compile and execute through the restricted runner.
- DQ output is persisted, bounded and visible through the dashboard API.
- Mock mode gives deterministic automated tests; real-LLM mode has at least one manual
  local smoke record with a redacted evidence summary, proposal, reviewer decision and
  result/audit IDs.
