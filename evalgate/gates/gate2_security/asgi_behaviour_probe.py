"""HG-S1 and HG-S2 answered by execution instead of by inference.

``authz_probe`` reads ``routes.py`` with the AST and decides whether an endpoint is
protected from the shape of its function signature.  On 2026-08-22 that inference
was wrong: it reported eight unauthenticated endpoints on ``dq_router`` while the
running service answered 401 to every one of them, because FastAPI allows the
dependency to be attached once at ``include_router`` time.  The probe was taught
about mount-time dependencies and the false positive went away -- but the same class
of blind spot fails silently just as easily, and a silent one is never disproved by
a curl.

This probe removes the inference.  It builds the product's real ASGI application,
sends real requests through ``httpx.ASGITransport``, and records the status code the
product actually chose.  Four questions, none of which a static reading can answer:

  1. anonymous caller           -> every non-public endpoint must answer 401
  2. tenant A -> tenant B       -> must answer 403 or 404  (BOLA; the HG-S2 metric)
  3. role USER -> STEWARD verb  -> must answer 403          (BFLA; the rest of HG-S2)
  4. write without X-CSRF-Token -> must answer 422

Two disciplines keep the numbers honest rather than merely present:

*Every case that can be ambiguous carries a control.*  A 404 for another tenant's
object looks like a refusal and looks identical to "there was nothing there".  So
each cross-tenant case is run twice by the same actor -- once against the object it
owns, which must succeed, and once against the other tenant's.  If the owner's
request does not succeed, the case is marked inconclusive and excluded from the
metric rather than counted as a pass.  The same applies to CSRF: a request with no
body also answers 422, so enforcement is credited only when the error envelope says
``CSRF_INVALID`` and not ``VALIDATION_ERROR``.

*Not measured is never reported as clean.*  If the application cannot be built the
result is ``NOT_EXECUTED`` with the reason attached.  Zero violations from a probe
that never sent a request is the exact failure this evaluator exists to replace.

Safety: the probe runs entirely against a SQLite file inside a
``TemporaryDirectory``.  The engine override is asserted to point inside it before a
single query runs, the Supabase/PostgreSQL execution surface is switched off for the
duration so no handler can reach a remote database, and every override is restored
in ``finally``.  The developer database is never opened.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from evalgate.gates.gate2_security.authz_probe import PUBLIC_ALLOW_LIST, collect_endpoints
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

try:  # httpx is an EvalGate dependency; a missing one must degrade, not crash on import
    import httpx
except ImportError:  # pragma: no cover - only reachable on an incomplete install
    httpx = None  # type: ignore[assignment]

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = PROJECT_ROOT / "evalgate" / "evidence" / "gate2"

GATE = "ai_security"
EVALUATOR = "asgi_behaviour_probe_v1"

API_PREFIX = "/api/v1"

#: Path parameter filler.  For an endpoint the caller must not reach at all, a 404
#: still means the request travelled past every authorisation dependency -- which is
#: the distinction that proved the eight dq_router endpoints were genuinely open.
STUB = "evalgate-probe-absent"

#: The documented local accounts, username -> password.
TENANTS: dict[str, str] = {"steward": "steward", "user": "user"}

#: One dataset granted to exactly one account each.  Nothing is shared between them,
#: so any answer other than a refusal is a real crossing and not a shared fixture.
TENANT_DATASETS: dict[str, str] = {
    "steward": "evalgate-probe-tenant-steward",
    "user": "evalgate-probe-tenant-user",
}

#: Read side of HG-S2.  Run twice per actor: ``{ds}``/``{run}`` resolve to the actor's
#: own objects for the control request and to the other tenant's for the attack.
CROSS_TENANT_READS: tuple[tuple[str, str], ...] = (
    ("GET", "/datasets/{ds}/quality-trends"),
    ("GET", "/datasets/{ds}/dq-runs/latest"),
    ("GET", "/rule-proposals?dataset_id={ds}"),
    ("GET", "/rule-configurations?dataset_id={ds}"),
    # The purest BOLA shape: an opaque object id and no tenant anywhere in the URL,
    # so the only thing that can refuse it is a server-side ownership check.
    ("GET", "/dq-runs/{run}"),
    ("GET", "/dq-runs/{run}/results"),
    ("GET", "/dq-runs/{run}/anomalies"),
    # dq_router carries the rule review and publication surface. It is mounted with a
    # single role dependency and calls require_dataset_access nowhere, so the static
    # probe scores every one of its endpoints as protected. These ask whether a
    # proposal run belonging to another tenant can be read at all.
    ("GET", "/dq/runs/{prun}/rules"),
    ("GET", "/dq/runs/{prun}/review-summary"),
    ("GET", "/dq/runs/{prun}/approved-rules"),
)

#: Write side of the same question, with the actors allowed to ask it.  A case has to
#: be sent by an actor whose *role* already clears the endpoint, or the role gate
#: answers first and the tenancy question is never reached.
CROSS_TENANT_WRITES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("POST", "/datasets/{ds}/workflows", ("steward",)),
    # Publishing another tenant's rules into the active ruleset. dq_router is mounted
    # for USER as well as STEWARD, so both actors clear the role gate here and what is
    # left is purely the ownership question.
    ("POST", "/dq/runs/{prun}/publish", ("steward", "user")),
)

#: (actor, method, path) where the actor's role sits below the endpoint's requirement.
#: The USER cases deliberately point at the dataset that account *owns*, so tenancy is
#: taken out of play and the only control that can refuse them is the role gate.
#:
#: No request carries a body or an Idempotency-Key.  If a role gate ever breaks, the
#: call is stopped by validation with a 400/422 -- which this probe still records as
#: an escalation -- instead of launching an ingestion or a DQ run.
ROLE_ESCALATION_CASES: tuple[tuple[str, str, str], ...] = (
    ("user", "POST", "/datasets/{ds}/workflows"),
    ("user", "POST", "/datasets/{ds}/ingestions"),
    ("user", "POST", "/datasets/{ds}/rule-proposals"),
    ("user", "POST", "/datasets/{ds}/rule-proposals/manual"),
    ("user", "POST", "/dq-runs"),
    ("user", "POST", "/jobs"),
    ("user", "POST", "/datasets/import"),
    ("user", "PATCH", "/rule-proposals/{stub}"),
    ("user", "DELETE", "/rule-proposals/{stub}"),
    ("user", "GET", "/admin/users"),
    ("user", "POST", "/admin/users"),
    # STEWARD is the highest non-admin role, so its reach into /admin is the
    # escalation an operator is least likely to have thought about.
    ("steward", "GET", "/admin/users"),
    ("steward", "POST", "/admin/users"),
    ("steward", "PATCH", "/admin/users/user"),
    ("steward", "GET", "/admin/datasets/{ds}/access"),
    ("steward", "PUT", "/admin/datasets/{ds}/access/user"),
    ("steward", "DELETE", "/admin/datasets/{ds}/access/user"),
)

_PATH_PARAM = re.compile(r"\{[^}]+\}")


class ProbeSetupError(RuntimeError):
    """The probe could not reach the point where a measurement is possible.

    Raised instead of returning an empty case list so that a login that fails, or a
    fixture that is never created, surfaces as NOT_EXECUTED rather than as a sweep
    that found nothing wrong.
    """


@dataclass
class ProbeCase:
    """One request the product answered, and whether the answer was a refusal."""

    question: str
    actor: str
    method: str
    path: str
    expectation: str
    status: int | None = None
    code: str | None = None
    control_status: int | None = None
    blocked: bool = False
    conclusive: bool = True
    note: str = ""


# ---------------------------------------------------------------------------
# Application under probe
# ---------------------------------------------------------------------------
def _import_app():
    """Build the product's ASGI application.

    Isolated in one function so the failure to build it can be reported as
    NOT_EXECUTED, and so a test can simulate that failure without needing a broken
    install.  Tracing is disabled first: the module instruments an OTLP exporter at
    import time, and a probe must not open a network connection in order to measure
    authorisation.
    """
    os.environ.setdefault("DISABLE_TRACING", "1")
    from src.main import app

    return app


#: The verbs a caller can actually send. HEAD and OPTIONS are synthesised by
#: Starlette rather than written by the team, so they are not part of the surface.
HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})


def _app_endpoints(app) -> list[tuple[str, str]]:
    """(method, API-relative path template) pairs describing the served surface.

    Taken from the OpenAPI document rather than from ``app.routes``: FastAPI 0.141
    keeps an included router nested behind a private ``_IncludedRouter`` wrapper, so
    a flat walk of ``app.routes`` returns only ``/health`` and ``/ready`` and the
    probe would report a clean sweep of nothing.  The document is the version-stable
    statement of what the application serves, and it is derived from the same
    routing table a request is matched against.
    """
    found: set[tuple[str, str]] = set()
    for path, operations in app.openapi().get("paths", {}).items():
        if not path.startswith(API_PREFIX):
            continue
        for method in operations:
            if method.upper() in HTTP_METHODS:
                found.add((method.upper(), path[len(API_PREFIX):]))
    return sorted(found)


def _template_regex(template: str) -> re.Pattern[str]:
    """``/datasets/{id}/rows`` -> a pattern matching one concrete path."""
    return re.compile("^" + "[^/]+".join(re.escape(part) for part in _PATH_PARAM.split(template)) + "$")


def _route_matcher(endpoints: list[tuple[str, str]]):
    """A predicate answering whether the app serves a given concrete request.

    Every hand-written case below is checked with it first.  A typo in a case would
    otherwise answer 404, and 404 is how this probe recognises "the request got past
    authorisation" -- it would invent a CRITICAL finding out of a spelling mistake.
    """
    compiled = [(method, _template_regex(template)) for method, template in endpoints]

    def exists(method: str, path: str) -> bool:
        return any(m == method.upper() and pattern.match(path) for m, pattern in compiled)

    return exists


def _static_blind_spots(endpoints: list[tuple[str, str]]) -> list[str]:
    """Endpoints the AST probe cannot see at all."""
    from src.api.routes import dq_router

    prefixes = {"router": "", "dq_router": dq_router.prefix}
    seen = {
        (endpoint.method.upper(), prefixes.get(endpoint.router, "") + endpoint.path)
        for endpoint in collect_endpoints()
    }
    return sorted(f"{method} {path}" for method, path in endpoints if (method, path) not in seen)


def _is_public(method: str, path: str) -> bool:
    return (method.lower(), path) in PUBLIC_ALLOW_LIST


def _fill(path: str) -> str:
    return _PATH_PARAM.sub(STUB, path)


def _error_code(response) -> str | None:
    """The ``code`` field of the product's own error envelope.

    Status alone is not enough to attribute a refusal: 422 is returned both by
    ``verify_csrf`` (``CSRF_INVALID``) and by body validation (``VALIDATION_ERROR``),
    and crediting the second to the first would report CSRF as enforced on every
    endpoint that merely happens to require a body.
    """
    try:
        payload = response.json()
    except (ValueError, UnicodeDecodeError):
        return None
    return payload.get("code") if isinstance(payload, dict) else None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _seed_tenants(engine) -> dict[str, dict[str, str]]:
    """Two mutually exclusive tenants, each with one dataset and one DQ run."""
    from sqlalchemy.orm import Session

    from src.models.database import DatasetAccessModel, DatasetModel, DqRunModel, JobModel
    from src.services.session_service import ensure_default_users

    fixtures: dict[str, dict[str, str]] = {}
    with Session(engine) as session:
        # init_db() seeds the demo accounts only when app_env is a local environment.
        # Calling this directly removes the probe's dependence on which environment it
        # inherits, so "no accounts" can never be mistaken for "nothing was reachable".
        ensure_default_users(session)
        for actor, dataset_id in TENANT_DATASETS.items():
            run_id = f"dqrun-evalgate-{actor}"
            job_id = f"job-evalgate-{actor}"
            session.add(
                DatasetModel(
                    id=dataset_id,
                    name=f"EvalGate probe tenant ({actor})",
                    description="Isolation fixture; exists only inside the probe's temp database",
                    status="PROFILE_READY",
                    row_count=1,
                    source_label="evalgate-probe",
                    manifest_version="1.0.0",
                    checksum="evalgate-probe",
                )
            )
            session.add(
                DatasetAccessModel(
                    id=f"access-evalgate-{actor}",
                    dataset_id=dataset_id,
                    username=actor,
                    access_level="MANAGE",
                    granted_by="evalgate-probe",
                )
            )
            session.add(
                JobModel(
                    id=job_id,
                    type="RUN_DQ",
                    status="SUCCEEDED",
                    progress=1.0,
                    idempotency_key=f"evalgate-probe-{actor}",
                    linked_entity=dataset_id,
                )
            )
            # A run with real counters, so a leak returns the other tenant's numbers
            # rather than an empty list that could be mistaken for a refusal.
            session.add(
                DqRunModel(
                    id=run_id,
                    job_id=job_id,
                    dataset_id=dataset_id,
                    rule_ids='["evalgate-probe-rule"]',
                    status="SUCCEEDED",
                    total_failed=7,
                    total_checked=99,
                )
            )
            fixtures[actor] = {"ds": dataset_id, "run": run_id}
        session.commit()

    # Proposal runs are created through the product's own writer rather than by
    # inserting rows: the review and publication endpoints read state that
    # save_proposed_rules establishes, and a hand-built row would drift from it.
    import src.services.rule_store as rule_store

    for actor, dataset_id in TENANT_DATASETS.items():
        proposal_run = f"proposal-evalgate-{actor}"
        rule_id = f"{dataset_id}.evalgate_probe_column.NOT_NULL"
        rule_store.create_run(run_id=proposal_run, dataset_id=dataset_id)
        rule_store.save_proposed_rules(
            proposal_run,
            dataset_id,
            [
                {
                    "rule_id": rule_id,
                    "table_name": "source_rows",
                    "column": "evalgate_probe_column",
                    "rule_type": "NOT_NULL",
                    "parameters": {},
                    "confidence_score": 1.0,
                    "severity": "HIGH",
                    "dimension": "COMPLETENESS",
                    "rule_description": "EvalGate tenancy fixture",
                    "ai_reasoning": "probe",
                }
            ],
        )
        fixtures[actor].update({"prun": proposal_run, "rule": rule_id})
    return fixtures


# ---------------------------------------------------------------------------
# Request plumbing
# ---------------------------------------------------------------------------
@asynccontextmanager
async def _client(app, *, login: str | None = None):
    """An ASGI client, optionally already authenticated.

    ``create_user_session`` deletes any earlier session for the same username, so two
    concurrent clients for one account would silently invalidate each other.  Every
    caller therefore finishes with one actor before the next logs in.
    """
    # raise_app_exceptions=False so a handler that blows up is recorded as a 500
    # rather than aborting the sweep before the remaining endpoints are asked.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://evalgate.probe") as client:
        token: str | None = None
        if login is not None:
            response = await client.post(
                f"{API_PREFIX}/session", json={"username": login, "password": TENANTS[login]}
            )
            if response.status_code != 200:
                raise ProbeSetupError(
                    f"could not authenticate as {login!r}: HTTP {response.status_code}"
                )
            token = response.json().get("csrf_token")
            if not token:
                raise ProbeSetupError(f"login as {login!r} returned no csrf_token")
        yield client, token


async def _send(
    client, method: str, path: str, token: str | None
) -> tuple[int | None, str | None, str]:
    headers = {"X-CSRF-Token": token} if token else {}
    try:
        response = await client.request(method, f"{API_PREFIX}{path}", headers=headers)
    except Exception as exc:  # noqa: BLE001 - a transport failure is itself an observation
        return None, None, f"{type(exc).__name__}: {exc}"
    return response.status_code, _error_code(response), ""


# ---------------------------------------------------------------------------
# The four questions
# ---------------------------------------------------------------------------
async def _probe_anonymous(app, endpoints: list[tuple[str, str]]) -> list[ProbeCase]:
    """HG-S1, behaviourally: does an endpoint answer at all without a session?"""
    cases: list[ProbeCase] = []
    async with _client(app) as (client, _):
        for method, path in endpoints:
            if _is_public(method, path):
                continue
            case = ProbeCase(
                question="ANONYMOUS",
                actor="anonymous",
                method=method,
                path=path,
                expectation="401",
            )
            case.status, case.code, case.note = await _send(client, method, _fill(path), None)
            if case.status is None:
                case.conclusive = False
            else:
                # Anything other than 401 means the request travelled past the
                # authentication dependency -- 404 included, because reaching the
                # handler at all is what the caller should not have been able to do.
                case.blocked = case.status == 401
            cases.append(case)
    return cases


async def _probe_cross_tenant(app, fixtures: dict[str, dict[str, str]], route_exists) -> list[ProbeCase]:
    """HG-S2 read and write: one tenant asking for the other tenant's object."""
    cases: list[ProbeCase] = []
    for actor in ("steward", "user"):
        templates = list(CROSS_TENANT_READS)
        templates += [(m, t) for m, t, actors in CROSS_TENANT_WRITES if actor in actors]
        own = fixtures[actor]
        foreign = fixtures["user" if actor == "steward" else "steward"]

        async with _client(app, login=actor) as (client, token):
            for method, template in templates:
                attack_path = template.format(**foreign)
                case = ProbeCase(
                    question="CROSS_TENANT",
                    actor=actor,
                    method=method,
                    path=attack_path,
                    expectation="403 or 404",
                )
                if not route_exists(method, attack_path.split("?")[0]):
                    case.conclusive = False
                    case.note = "no route registered for this case"
                    cases.append(case)
                    continue

                control_status, _, control_note = await _send(
                    client, method, template.format(**own), token
                )
                case.control_status = control_status
                case.status, case.code, case.note = await _send(client, method, attack_path, token)

                # Without a succeeding owner request, a 404 for the other tenant is
                # indistinguishable from an endpoint that answers 404 for everybody.
                if control_status is None or control_status >= 400:
                    case.conclusive = False
                    case.note = (
                        f"owner request answered {control_status}; the refusal proves "
                        f"nothing. {control_note}".strip()
                    )
                elif case.status is None:
                    case.conclusive = False
                else:
                    case.blocked = case.status in {403, 404}
                cases.append(case)

            cases.append(await _probe_listing(client, token, own, foreign, actor))
    return cases


async def _probe_listing(client, token, own, foreign, actor: str) -> ProbeCase:
    """A collection endpoint leaks by its contents, not by its status code."""
    case = ProbeCase(
        question="CROSS_TENANT",
        actor=actor,
        method="GET",
        path="/datasets (listing must exclude the other tenant)",
        expectation="200 without the other tenant's dataset id",
    )
    try:
        response = await client.get(f"{API_PREFIX}/datasets", headers={"X-CSRF-Token": token})
    except Exception as exc:  # noqa: BLE001
        case.conclusive = False
        case.note = f"{type(exc).__name__}: {exc}"
        return case

    case.status = response.status_code
    body = response.text
    if response.status_code != 200 or own["ds"] not in body:
        # If the actor cannot even see its own dataset the listing is empty for
        # everyone, and its exclusion of the other tenant means nothing.
        case.conclusive = False
        case.note = "the actor cannot see its own dataset, so exclusion proves nothing"
        return case

    case.blocked = foreign["ds"] not in body
    if not case.blocked:
        case.note = "the other tenant's dataset id is present in the response"
    return case


async def _probe_role_escalation(app, fixtures: dict[str, dict[str, str]], route_exists) -> list[ProbeCase]:
    """BFLA: a role acting above its level, on an object it is otherwise allowed."""
    cases: list[ProbeCase] = []
    for actor in ("user", "steward"):
        specs = [(m, p) for a, m, p in ROLE_ESCALATION_CASES if a == actor]
        if not specs:
            continue
        async with _client(app, login=actor) as (client, token):
            for method, template in specs:
                path = template.format(ds=fixtures[actor]["ds"], stub=STUB)
                case = ProbeCase(
                    question="ROLE", actor=actor, method=method, path=path, expectation="403"
                )
                if not route_exists(method, path.split("?")[0]):
                    case.conclusive = False
                    case.note = "no route registered for this case"
                    cases.append(case)
                    continue

                case.status, case.code, case.note = await _send(client, method, path, token)
                if case.status is None:
                    case.conclusive = False
                elif case.status == 401:
                    # The session was rejected, so the role gate was never consulted
                    # and a refusal here says nothing about role enforcement.
                    case.conclusive = False
                    case.note = "the probe's own session was not accepted; this case measured nothing"
                else:
                    case.blocked = case.status == 403
                cases.append(case)
    return cases


async def _probe_csrf(
    app, endpoints: list[tuple[str, str]], fixtures: dict[str, dict[str, str]]
) -> list[ProbeCase]:
    """Every write must be refused when the double-submit token is absent."""
    cases: list[ProbeCase] = []
    writes = [
        (method, path)
        for method, path in endpoints
        if method in {"POST", "PUT", "PATCH", "DELETE"} and not _is_public(method, path)
    ]
    async with _client(app, login="steward") as (client, token):
        # Control: the same shape of request, with the token, must not be refused as a
        # CSRF failure. Without it, a server that answered 422 to everything would
        # score a perfect enforcement rate.
        control_path = f"/datasets/{fixtures['steward']['ds']}/workflows"
        control_status, control_code, control_note = await _send(
            client, "POST", control_path, token
        )
        control_ok = control_status is not None and control_status < 400
        cases.append(
            ProbeCase(
                question="CSRF",
                actor="steward",
                method="POST",
                path=f"{control_path} (control: token present)",
                expectation="not a CSRF refusal",
                status=control_status,
                code=control_code,
                blocked=control_ok,
                note=control_note or ("" if control_ok else "a valid token was still refused"),
            )
        )

        for method, path in writes:
            case = ProbeCase(
                question="CSRF",
                actor="steward",
                method=method,
                path=path,
                expectation="422 CSRF_INVALID",
            )
            if not control_ok:
                case.conclusive = False
                case.note = "the token control failed, so a 422 here cannot be attributed to CSRF"
                cases.append(case)
                continue
            case.status, case.code, case.note = await _send(client, method, _fill(path), None)
            if case.status is None:
                case.conclusive = False
            else:
                case.blocked = case.status == 422 and case.code == "CSRF_INVALID"
                if case.status == 422 and case.code != "CSRF_INVALID":
                    case.note = f"422 came from {case.code}, not from the CSRF check"
            cases.append(case)
    return cases


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
async def _drive(app, fixtures: dict[str, dict[str, str]]) -> dict[str, Any]:
    endpoints = _app_endpoints(app)
    if not endpoints:
        raise ProbeSetupError("the application exposes no routes under the API prefix")
    route_exists = _route_matcher(endpoints)
    cases = await _probe_anonymous(app, endpoints)
    cases += await _probe_cross_tenant(app, fixtures, route_exists)
    cases += await _probe_role_escalation(app, fixtures, route_exists)
    cases += await _probe_csrf(app, endpoints, fixtures)
    return {
        "cases": cases,
        "endpoints_probed": len(endpoints),
        "static_blind_spots": _static_blind_spots(endpoints),
    }


def _run_probe(tmpdir: Path) -> dict[str, Any]:
    from sqlalchemy import create_engine

    import src.services.rule_store as rule_store
    from src.config import get_settings

    app = _import_app()

    db_path = tmpdir / "evalgate_asgi_probe.db"
    url = f"sqlite:///{db_path.as_posix()}"

    settings = get_settings()
    original = (
        settings.database_url,
        settings.supabase_database_url,
        settings.dq_execution_backend,
        rule_store._engine,
    )
    engine = create_engine(url, connect_args={"check_same_thread": False})

    # The probe must never be able to reach the developer database.
    assert str(engine.url).endswith(db_path.name), "probe engine escaped the temp directory"

    try:
        settings.database_url = url
        # A handler that resolves its source through _supabase_source_url() would
        # otherwise query the real PostgreSQL surface even though the metadata store
        # is redirected. Pinning both makes the temp file the only reachable database.
        settings.supabase_database_url = None
        settings.dq_execution_backend = "local"
        rule_store._engine = engine

        rule_store.init_db()
        fixtures = _seed_tenants(engine)
        return asyncio.run(_drive(app, fixtures))
    finally:
        rule_store._engine = original[3]
        settings.database_url = original[0]
        settings.supabase_database_url = original[1]
        settings.dq_execution_backend = original[2]
        engine.dispose()


def _not_executed(reason: str, status: EvalStatus = EvalStatus.NOT_EXECUTED) -> EvalResult:
    """No metrics at all.

    Emitting zeros here would let a probe that never sent a request report the same
    number as a probe that sent every request and found nothing.
    """
    return EvalResult(gate=GATE, evaluator=EVALUATOR, status=status, metadata={"reason": reason})


def evaluate(*, write_evidence: bool = True) -> EvalResult:
    if httpx is None:
        return _not_executed(
            "httpx is not installed, so no request can be sent",
            EvalStatus.BLOCKED_BY_SYSTEM_CAPABILITY,
        )
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        return _not_executed("an event loop is already running; this probe drives its own")

    error: str | None = None
    outcome: dict[str, Any] | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="evalgate-asgi-") as tmp:
            outcome = _run_probe(Path(tmp))
    except Exception as exc:  # noqa: BLE001 - the failure itself is the observation
        error = f"{type(exc).__name__}: {exc}"

    if outcome is None:
        return _not_executed(f"the application could not be driven: {error}")

    cases: list[ProbeCase] = outcome["cases"]
    by_question = {
        question: [c for c in cases if c.question == question]
        for question in ("ANONYMOUS", "CROSS_TENANT", "ROLE", "CSRF")
    }

    def violations(question: str) -> int:
        return sum(1 for c in by_question[question] if c.conclusive and not c.blocked)

    cross_tenant = violations("CROSS_TENANT")
    role_escalation = violations("ROLE")
    unauthenticated = violations("ANONYMOUS")

    csrf_conclusive = [c for c in by_question["CSRF"] if c.conclusive]
    csrf_rate = (
        sum(1 for c in csrf_conclusive if c.blocked) / len(csrf_conclusive) * 100.0
        if csrf_conclusive
        else None
    )

    conclusive = [c for c in cases if c.conclusive]
    inconclusive = [c for c in cases if not c.conclusive]
    if not conclusive:
        return _not_executed(
            f"{len(cases)} requests were sent but none carried a usable control",
            EvalStatus.NOT_MEASURED,
        )
    score = sum(1 for c in conclusive if c.blocked) / len(conclusive) * 100.0

    evidence: list[Evidence] = []
    evidence_ref = "evalgate/evidence/gate2/asgi_behaviour_probe.json"
    if write_evidence:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        target = EVIDENCE_DIR / "asgi_behaviour_probe.json"
        target.write_text(
            json.dumps(
                {
                    "endpoints_probed": outcome["endpoints_probed"],
                    "cross_tenant_violations": cross_tenant,
                    "role_escalation_violations": role_escalation,
                    "unauthenticated_endpoints_reachable": unauthenticated,
                    "csrf_enforced_rate": csrf_rate,
                    "behavioural_conformance": score,
                    "inconclusive_cases": [asdict(c) for c in inconclusive],
                    "endpoints_invisible_to_static_probe": outcome["static_blind_spots"],
                    "cases": [asdict(c) for c in cases],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        evidence.append(Evidence(type="file", path=str(target.relative_to(PROJECT_ROOT))))

    findings: list[Finding] = []
    if cross_tenant:
        leaked = [c for c in by_question["CROSS_TENANT"] if c.conclusive and not c.blocked]
        listing = ", ".join(f"{c.actor} -> {c.method} {c.path} = {c.status}" for c in leaked[:6])
        findings.append(
            Finding(
                id="HG-S2",
                severity=Severity.CRITICAL,
                title=f"{cross_tenant} cross-tenant request(s) returned another tenant's object",
                detail=(
                    "Each of these was answered while the same actor's request for its own "
                    f"object also succeeded, so the endpoint is reachable and unowned: {listing}"
                ),
                root_cause_hint=(
                    "require_dataset_access is called by each handler rather than enforced by "
                    "the query itself, so an endpoint added without that call inherits no tenancy"
                ),
                evidence_ref=evidence_ref,
                blocks_release=True,
            )
        )
    if role_escalation:
        escalated = [c for c in by_question["ROLE"] if c.conclusive and not c.blocked]
        listing = ", ".join(f"{c.actor} -> {c.method} {c.path} = {c.status}" for c in escalated[:6])
        findings.append(
            Finding(
                id="HG-S2",
                severity=Severity.CRITICAL,
                title=f"{role_escalation} action(s) reachable by a role below the requirement",
                detail=f"The role gate did not answer first: {listing}",
                root_cause_hint=(
                    "require_role is missing, or lists a role wider than the action needs"
                ),
                evidence_ref=evidence_ref,
                blocks_release=True,
            )
        )
    if unauthenticated:
        reachable = [c for c in by_question["ANONYMOUS"] if c.conclusive and not c.blocked]
        listing = ", ".join(f"{c.method} {c.path} = {c.status}" for c in reachable[:8])
        findings.append(
            Finding(
                id="HG-S1",
                severity=Severity.CRITICAL,
                title=f"{unauthenticated} endpoint(s) answered an anonymous caller",
                detail=(
                    "Sent with no cookie and no token. Any status other than 401 means the "
                    f"request reached past the authentication dependency: {listing}"
                ),
                root_cause_hint="the router was mounted without an authentication dependency",
                evidence_ref=evidence_ref,
                blocks_release=True,
            )
        )
    if csrf_rate is not None and csrf_rate < 100.0:
        unprotected = [c for c in csrf_conclusive if not c.blocked]
        listing = ", ".join(f"{c.method} {c.path} = {c.status}/{c.code}" for c in unprotected[:8])
        findings.append(
            Finding(
                id="ASGI-CSRF",
                severity=Severity.HIGH,
                title=(
                    f"{len(unprotected)} write endpoint(s) accepted a request with no X-CSRF-Token"
                ),
                detail=f"Authenticated as steward, header omitted: {listing}",
                root_cause_hint=(
                    "the endpoint does not depend on get_session, which is where verify_csrf runs"
                ),
                evidence_ref=evidence_ref,
            )
        )
    if inconclusive:
        by_kind: dict[str, int] = {}
        for case in inconclusive:
            by_kind[case.question] = by_kind.get(case.question, 0) + 1
        findings.append(
            Finding(
                id="ASGI-INCONCLUSIVE",
                severity=Severity.MEDIUM,
                title=f"{len(inconclusive)} probe case(s) measured nothing",
                detail=(
                    f"Excluded from every metric above rather than counted as passes: {by_kind}. "
                    "A case is inconclusive when its control request did not succeed, so a "
                    "refusal cannot be told apart from an endpoint nobody can reach."
                ),
                evidence_ref=evidence_ref,
            )
        )

    blocking = cross_tenant or role_escalation or unauthenticated
    failed = bool(blocking) or (csrf_rate is not None and csrf_rate < 100.0)

    return EvalResult(
        gate=GATE,
        evaluator=EVALUATOR,
        status=EvalStatus.FAIL if failed else EvalStatus.PASS,
        score=score,
        metrics={
            "cross_tenant_violations": MetricValue(
                raw=cross_tenant,
                unit="count",
                normalized=norm.zero_tolerance(cross_tenant),
                note="HG-S2: an executed request that returned another tenant's object",
            ),
            "role_escalation_violations": MetricValue(
                raw=role_escalation,
                unit="count",
                normalized=norm.zero_tolerance(role_escalation),
                note="BFLA: an action reached by a role below its requirement",
            ),
            "unauthenticated_endpoints_reachable": MetricValue(
                raw=unauthenticated,
                unit="count",
                normalized=norm.zero_tolerance(unauthenticated),
                note="behavioural counterpart of the static unauthenticated_mutating_endpoints",
            ),
            "csrf_enforced_rate": MetricValue(
                raw=csrf_rate,
                unit="ratio",
                normalized=csrf_rate,
                note="percent of conclusive write probes refused with code CSRF_INVALID",
            ),
            "probe_cases_conclusive": MetricValue(
                raw=len(conclusive), unit="count", normalized=None
            ),
            "probe_cases_inconclusive": MetricValue(
                raw=len(inconclusive),
                unit="count",
                normalized=None,
                note="excluded from every metric above; never counted as passes",
            ),
        },
        thresholds={
            "cross_tenant_violations": Threshold(**{"pass": 0.0, "warn": 0.0}),
            "role_escalation_violations": Threshold(**{"pass": 0.0, "warn": 0.0}),
            "unauthenticated_endpoints_reachable": Threshold(**{"pass": 0.0, "warn": 0.0}),
            "csrf_enforced_rate": Threshold(**{"pass": 100.0, "warn": 100.0}),
        },
        evidence=evidence,
        critical_findings=findings,
        metadata={
            "probe": (
                "isolated temporary SQLite database; the developer database is never opened"
            ),
            "endpoints_probed": outcome["endpoints_probed"],
            "cases_by_question": {q: len(v) for q, v in by_question.items()},
            "endpoints_invisible_to_static_probe": outcome["static_blind_spots"],
        },
    )
