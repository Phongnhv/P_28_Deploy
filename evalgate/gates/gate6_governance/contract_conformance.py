"""Does the system obey the contract it wrote for itself?

This project is unusual in a useful way: it states its own invariants in prose.
``docs/PRODUCT_SPEC.md`` lists six safety rules, ``docs/API_CONTRACT.md`` fixes the
job state machine and declares that compiled SQL is not a public field, and
``docs/DATA_MODEL.md`` separates the runner credential from the application one.

Those sentences are more valuable than any generic AI metric here, because they are
specific, they were agreed by the team, and every one of them can be checked
mechanically.  A violation is not a matter of taste -- it is the system disagreeing
with its own documentation.

Each check is deliberately narrow and reports the file and line it relies on, so a
disputed result can be settled by opening the file rather than by re-running a model.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from evalgate.normalizers import normalizers as norm
from evalgate.schemas.eval_result import (
    EvalResult,
    EvalStatus,
    Evidence,
    Finding,
    MetricValue,
    Severity,
    Threshold,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC = PROJECT_ROOT / "src"
ROUTES = SRC / "api" / "routes.py"
SCHEMAS = SRC / "models" / "schemas.py"
RULE_STORE = SRC / "services" / "rule_store.py"
DATABASE = SRC / "models" / "database.py"
EVIDENCE_DIR = PROJECT_ROOT / "evalgate" / "evidence" / "gate6"

GATE = "governance"
EVALUATOR = "contract_conformance_v1"

#: Fields that describe how the system works internally. docs/API_CONTRACT.md:
#: "The compiled SQL is not a public API field."
INTERNAL_RESPONSE_FIELDS = {
    "sql_text",
    "compiled_sql",
    "connection_string",
    "prompt",
    "system_prompt",
    "traceback",
}

#: docs/API_CONTRACT.md: "Jobs use PENDING, RUNNING, SUCCEEDED, FAILED_RETRYABLE or FAILED."
#: DONE and QUEUED are accepted as *inputs* because rule_store.update_run_status maps
#: them onto the documented vocabulary before writing.
DOCUMENTED_JOB_STATES = {"PENDING", "RUNNING", "SUCCEEDED", "FAILED_RETRYABLE", "FAILED"}
ACCEPTED_JOB_STATE_INPUTS = DOCUMENTED_JOB_STATES | {"DONE", "QUEUED"}

#: Functions that move a rule between review states. PRODUCT_SPEC safety rule 5
#: requires each of them to leave an audit record.
STATE_TRANSITION_FUNCTIONS = ("publish_approved_rules", "review_rule", "bulk_review")


@dataclass
class Check:
    id: str
    source: str
    statement: str
    passed: bool
    detail: str
    evidence: list[str]


def _py_files(base: Path = SRC) -> list[Path]:
    return [p for p in base.rglob("*.py") if "__pycache__" not in str(p)]


def _grep(pattern: str, files: list[Path], *, flags: int = 0) -> list[str]:
    compiled = re.compile(pattern, flags)
    hits: list[str] = []
    for file in files:
        try:
            text = file.read_text(encoding="utf-8")
        except OSError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if compiled.search(line):
                hits.append(f"{file.relative_to(PROJECT_ROOT)}:{number}")
    return hits


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _function_body(source: str, name: str) -> str:
    """Return the source of one top-level function, or '' when absent."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    return ""


# ---------------------------------------------------------------------------
# PRODUCT_SPEC safety rules
# ---------------------------------------------------------------------------

def check_raw_rows_immutable() -> Check:
    """Safety rule 1: raw rows are immutable through the application flow.

    "Immutable" is read the way docs/SUPABASE_DATASET_CONTRACT.md defines it --
    "never clean them in place".  A whole-dataset delete before a re-ingest is
    idempotency, not in-place editing, so only row-level UPDATE counts as a
    violation.  Bulk reloads are still reported, because they are worth seeing.
    """
    files = _py_files()
    updates = _grep(r"UPDATE\s+\"?(source_rows|trips_raw)", files, flags=re.IGNORECASE)
    updates += _grep(r"query\(SourceRowModel\)[^\n]*\.update\(", files)
    reloads = _grep(r"DELETE\s+FROM\s+\"?(source_rows|trips_raw)", files, flags=re.IGNORECASE)
    reloads += _grep(r"query\(SourceRowModel\)[^\n]*\.delete\(", files)
    note = f" ({len(reloads)} whole-dataset reload site(s), which is idempotency rather than mutation)" if reloads else ""
    return Check(
        id="SAFETY-1",
        source="docs/PRODUCT_SPEC.md#safety-rules",
        statement="Raw rows are immutable through the application flow",
        passed=not updates,
        detail=(f"no in-place update of source_rows/trips_raw{note}" if not updates
                else f"{len(updates)} in-place update site(s) found{note}"),
        evidence=(updates or reloads)[:5],
    )


def check_llm_receives_aggregate_only() -> Check:
    """Safety rule 2: the LLM receives aggregate evidence, never raw rows.

    Scoped to ``src/agents``: that is where prompts are assembled.  API modules
    mention the same identifiers as response fields, which is a different boundary
    and is covered by SAFETY-6 instead.
    """
    prompt_modules = [
        p for p in _py_files(SRC / "agents")
        if re.search(r"get_llm\(|structured_llm|\.ainvoke\(", _read(p))
    ]
    leak_pattern = r"(sample_failures|failed_row_ids|sample_refs)"
    hits = _grep(leak_pattern, prompt_modules)
    return Check(
        id="SAFETY-2",
        source="docs/PRODUCT_SPEC.md#safety-rules",
        statement="The LLM receives aggregate evidence, never raw rows",
        passed=not hits,
        detail=(f"no raw-row identifier in the {len(prompt_modules)} prompt-building module(s)"
                if not hits else f"{len(hits)} raw-row reference(s) where prompts are assembled"),
        evidence=hits[:5],
    )


def check_only_approved_rule_runs() -> Check:
    """Safety rule 3: only an approved typed rule can compile and run."""
    from evalgate.gates.gate2_security.authz_probe import collect_endpoints

    guarded_names = re.compile(r"(publish|review|approve|deactivate|execute)", re.IGNORECASE)
    unguarded = [
        f"{e.router} {e.method.upper()} {e.path} ({e.function}) routes.py:{e.line}"
        for e in collect_endpoints()
        if e.mutating and not e.has_auth and guarded_names.search(e.function)
    ]
    return Check(
        id="SAFETY-3",
        source="docs/PRODUCT_SPEC.md#safety-rules",
        statement="Only an approved typed rule can compile and run",
        passed=not unguarded,
        detail="every approval/publication/execution endpoint requires a session"
        if not unguarded
        else f"{len(unguarded)} approval or execution endpoint(s) accept anonymous calls",
        evidence=unguarded[:6],
    )


def check_runner_credential_and_bounds() -> Check:
    """Safety rule 4: separate read-only runner credential and bounded result IDs."""
    files = _py_files()
    runner_credential = _grep(r"runner_database_url|RUNNER_DATABASE_URL", files)
    bounded = _grep(r"SAMPLE_FAILURE_LIMIT|failed_id_limit|\[:\s*20\s*\]", files)
    passed = bool(runner_credential) and bool(bounded)
    missing = []
    if not runner_credential:
        missing.append("no module reads RUNNER_DATABASE_URL; the runner shares the app engine")
    if not bounded:
        missing.append("no bound on returned failed-row identifiers")
    return Check(
        id="SAFETY-4",
        source="docs/PRODUCT_SPEC.md#safety-rules",
        statement="The runner uses a separate read-only credential and bounded result IDs",
        passed=passed,
        detail="; ".join(missing) if missing else "separate credential and bounded IDs present",
        evidence=(runner_credential + bounded)[:5],
    )


def check_transitions_are_audited() -> Check:
    """Safety rule 5: all state transitions and executions create an audit record."""
    source = _read(RULE_STORE)
    unaudited = [
        name for name in STATE_TRANSITION_FUNCTIONS
        if (body := _function_body(source, name)) and "AuditEventModel" not in body
    ]
    return Check(
        id="SAFETY-5",
        source="docs/PRODUCT_SPEC.md#safety-rules",
        statement="All state transitions and executions create an audit record",
        passed=not unaudited,
        detail="every rule state transition writes an audit event" if not unaudited
        else f"{len(unaudited)} transition function(s) write no audit event: {unaudited}",
        evidence=[f"src/services/rule_store.py::{name}" for name in unaudited],
    )


def _public_model_closure(schema_source: str, routes_source: str) -> set[str]:
    """Model names reachable from a ``response_model=``, following nested fields.

    Following the nesting matters: the endpoint that leaks the compiled SQL declares
    ``response_model=TestResultsListResponse``, and the offending field sits one level
    down in ``TestResultResponse``.  A check that only looked at the declared model
    would report a clean result for exactly the case it exists to catch.
    """
    try:
        tree = ast.parse(schema_source)
    except SyntaxError:
        return set()
    field_types: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        referenced: set[str] = set()
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and item.annotation is not None:
                referenced |= set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*",
                                             ast.unparse(item.annotation)))
        field_types[node.name] = referenced

    declared = set(re.findall(r"response_model\s*=\s*([A-Za-z_][A-Za-z0-9_]*)", routes_source))
    reachable: set[str] = set()
    queue = [name for name in declared if name in field_types]
    while queue:
        name = queue.pop()
        if name in reachable:
            continue
        reachable.add(name)
        queue.extend(child for child in field_types.get(name, set()) if child in field_types)
    return reachable


def check_no_internal_fields_public() -> tuple[Check, list[str]]:
    """Safety rule 6 and API_CONTRACT: internal detail must not reach a response."""
    schema_source = _read(SCHEMAS)
    routes_source = _read(ROUTES)
    public_models = _public_model_closure(schema_source, routes_source)
    exposed: list[str] = []
    try:
        tree = ast.parse(schema_source)
    except SyntaxError:
        tree = None
    if tree is not None:
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or node.name not in public_models:
                continue
            fields = {
                item.target.id
                for item in node.body
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
            }
            leaked = sorted(INTERNAL_RESPONSE_FIELDS & fields)
            if leaked:
                exposed.append(f"{node.name}.{'/'.join(leaked)} (schemas.py:{node.lineno})")
    return (
        Check(
            id="SAFETY-6",
            source="docs/API_CONTRACT.md#state-rules",
            statement="The compiled SQL is not a public API field",
            passed=not exposed,
            detail="no response model exposes internal execution detail" if not exposed
            else f"{len(exposed)} response model(s) expose internal fields",
            evidence=exposed[:5],
        ),
        exposed,
    )


# ---------------------------------------------------------------------------
# API_CONTRACT / DATA_MODEL structural invariants
# ---------------------------------------------------------------------------

def check_actor_not_client_supplied() -> tuple[Check, list[str]]:
    """The recorded reviewer must come from the session, not from the request body."""
    # Only identity-of-the-approver fields count. An admin endpoint that creates an
    # account legitimately takes the new username from the body; that is the subject
    # of the action, not the actor performing it.
    source = _read(ROUTES)
    forgeable: list[str] = []
    for match in re.finditer(r"(reviewer|actor|actor_role|approved_by)\s*=\s*body\.\1", source):
        line = source[: match.start()].count("\n") + 1
        forgeable.append(f"routes.py:{line} {match.group(0)}")
    return (
        Check(
            id="AUDIT-ACTOR",
            source="docs/PRODUCT_SPEC.md#safety-rules",
            statement="The recorded actor is taken from the authenticated session",
            passed=not forgeable,
            detail="actor identity is server-derived" if not forgeable
            else f"{len(forgeable)} endpoint(s) accept a client-supplied actor",
            evidence=forgeable[:5],
        ),
        forgeable,
    )


def check_job_state_vocabulary() -> tuple[Check, dict[str, object]]:
    """Job status must stay inside the five documented values."""
    files = _py_files()
    observed: set[str] = set()
    for file in files:
        text = _read(file)
        observed |= set(re.findall(r"update_(?:test_)?run_status\([\s\S]{0,240}?status=[\"']([A-Za-z_]+)[\"']", text))
        observed |= set(re.findall(r"job\.status\s*=\s*[\"']([A-Za-z_]+)[\"']", text))
    out_of_vocabulary = sorted(observed - ACCEPTED_JOB_STATE_INPUTS)

    # The passthrough is the structural problem: whatever a caller supplies is written
    # verbatim, so the column has no vocabulary at all.
    passthrough = _grep(r"job\.status\s*=[^\n]*else\s+status", files)
    dynamic_status = _grep(r"status\s*=\s*str\((?:pause_reason|reason)\)", files)

    passed = not out_of_vocabulary and not passthrough and not dynamic_status
    details = []
    if out_of_vocabulary:
        details.append(f"undocumented literals: {out_of_vocabulary}")
    if passthrough:
        details.append("update_run_status writes any caller-supplied value verbatim")
    if dynamic_status:
        details.append("a runtime-computed pause reason is written into job status")
    return (
        Check(
            id="JOB-STATE",
            source="docs/API_CONTRACT.md#common-rules",
            statement="Jobs use PENDING, RUNNING, SUCCEEDED, FAILED_RETRYABLE or FAILED",
            passed=passed,
            detail="; ".join(details) if details else "job status stays inside the documented set",
            evidence=(passthrough + dynamic_status)[:5],
        ),
        {"observed_inputs": sorted(observed), "out_of_vocabulary": out_of_vocabulary},
    )


def check_single_run_state_owner() -> tuple[Check, list[str]]:
    """DATA_MODEL: dq_runs "does not invent a second job state"."""
    tables: list[str] = []
    for file in (DATABASE, RULE_STORE):
        tables += re.findall(r"__tablename__\s*=\s*[\"']([a-z_]+)[\"']", _read(file))
    run_state_tables = sorted(
        {t for t in tables if t == "jobs" or t.endswith("_runs")}
    )
    passed = len(run_state_tables) <= 2  # jobs plus one business execution record
    return (
        Check(
            id="RUN-STATE",
            source="docs/DATA_MODEL.md#states-and-links",
            statement="jobs owns the asynchronous status; runs do not invent a second one",
            passed=passed,
            detail=f"{len(run_state_tables)} tables carry an execution status: {run_state_tables}",
            evidence=run_state_tables,
        ),
        run_state_tables,
    )


# ---------------------------------------------------------------------------

def collect_checks() -> tuple[list[Check], dict[str, object]]:
    exposed_check, exposed = check_no_internal_fields_public()
    actor_check, forgeable = check_actor_not_client_supplied()
    job_check, job_detail = check_job_state_vocabulary()
    run_check, run_tables = check_single_run_state_owner()

    safety = [
        check_raw_rows_immutable(),
        check_llm_receives_aggregate_only(),
        check_only_approved_rule_runs(),
        check_runner_credential_and_bounds(),
        check_transitions_are_audited(),
        exposed_check,
    ]
    structural = [actor_check, job_check, run_check]
    extras = {
        "internal_fields_exposed": exposed,
        "forgeable_actor_fields": forgeable,
        "job_state": job_detail,
        "run_state_tables": run_tables,
    }
    return safety + structural, extras


def evaluate(*, write_evidence: bool = True) -> EvalResult:
    checks, extras = collect_checks()
    safety_checks = [c for c in checks if c.id.startswith("SAFETY-")]
    safety_passed = sum(1 for c in safety_checks if c.passed)
    safety_ratio = safety_passed / len(safety_checks)
    overall = sum(1 for c in checks if c.passed) / len(checks)

    evidence: list[Evidence] = []
    if write_evidence:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        target = EVIDENCE_DIR / "contract_conformance.json"
        target.write_text(
            json.dumps(
                {"checks": [asdict(c) for c in checks], **extras},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        evidence.append(Evidence(type="file", path=str(target.relative_to(PROJECT_ROOT))))

    findings: list[Finding] = []
    if extras["internal_fields_exposed"]:
        findings.append(
            Finding(
                id="HG-S8",
                severity=Severity.CRITICAL,
                title="Internal execution detail is a public API field",
                detail=(
                    "docs/API_CONTRACT.md states the compiled SQL is not a public API "
                    f"field. Exposed: {extras['internal_fields_exposed']}"
                ),
                root_cause_hint=(
                    "the response model is shared with the internal trace payload, so a "
                    "debugging field became part of the public contract"
                ),
                evidence_ref="evalgate/evidence/gate6/contract_conformance.json",
                blocks_release=True,
            )
        )
    if extras["forgeable_actor_fields"]:
        findings.append(
            Finding(
                id="HG-G4",
                severity=Severity.CRITICAL,
                title="The audited actor is supplied by the caller",
                detail=(
                    "Review decisions record whoever the request body names, so the audit "
                    f"trail cannot establish who approved a rule. Sites: {extras['forgeable_actor_fields']}"
                ),
                root_cause_hint=(
                    "the endpoint reads reviewer from the request model instead of the "
                    "authenticated session"
                ),
                evidence_ref="evalgate/evidence/gate6/contract_conformance.json",
                blocks_release=True,
            )
        )

    failed_safety = [c for c in safety_checks if not c.passed]
    if failed_safety:
        status = EvalStatus.FAIL
    elif overall < 1.0:
        status = EvalStatus.WARN
    else:
        status = EvalStatus.PASS

    return EvalResult(
        gate=GATE,
        evaluator=EVALUATOR,
        status=status,
        score=norm.ratio(overall),
        metrics={
            "safety_rule_conformance": MetricValue(
                raw=safety_ratio, unit="ratio", normalized=norm.ratio(safety_ratio)
            ),
            "internal_field_exposed_count": MetricValue(
                raw=len(extras["internal_fields_exposed"]), unit="count",
                normalized=norm.zero_tolerance(len(extras["internal_fields_exposed"])),
            ),
            "forgeable_actor_fields": MetricValue(
                raw=len(extras["forgeable_actor_fields"]), unit="count",
                normalized=norm.zero_tolerance(len(extras["forgeable_actor_fields"])),
            ),
            "job_state_vocabulary_violations": MetricValue(
                raw=len(extras["job_state"]["out_of_vocabulary"]), unit="count",
                normalized=None,
            ),
            "duplicate_run_state_tables": MetricValue(
                raw=len(extras["run_state_tables"]), unit="count", normalized=None
            ),
        },
        thresholds={
            "safety_rule_conformance": Threshold(**{"pass": 100.0, "warn": 100.0}),
            "internal_field_exposed_count": Threshold(**{"pass": 0.0, "warn": 0.0}),
            "forgeable_actor_fields": Threshold(**{"pass": 0.0, "warn": 0.0}),
        },
        evidence=evidence,
        critical_findings=findings,
        metadata={
            "safety_rules_passed": f"{safety_passed}/{len(safety_checks)}",
            "failed_checks": [c.id for c in checks if not c.passed],
        },
    )
