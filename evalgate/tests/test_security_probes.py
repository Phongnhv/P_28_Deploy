"""Self-tests for the three evaluators that hold CRITICAL release-blocking gates.

``authz_probe`` (HG-S1), ``egress_probe`` (HG-S3) and ``replay_detection`` (HG-A1)
each decide, on their own, whether a release is allowed.  Until now none of them had
a single test.

The direction that matters is the **false negative**.  A probe that over-reports is
annoying and gets caught: on 2026-08-22 ``authz_probe`` claimed eight unauthenticated
endpoints for a router that had just been protected, and one curl disproved it.  A
probe that under-reports is silent, and it fails in exactly the direction that lets
through what it exists to stop.

So most assertions below are of the form "this must still be reported", and the
recurring shape is: **no data must yield NOT_MEASURED, never a clean zero.**
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evalgate.gates.gate2_security import authz_probe as ap
from evalgate.schemas.eval_result import EvalStatus

# ---------------------------------------------------------------------------
# authz_probe -- HG-S1
# ---------------------------------------------------------------------------

def _routes(body: str) -> str:
    return "from fastapi import APIRouter, Depends\n\nrouter = APIRouter()\n\n" + body


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_a_mutating_endpoint_without_a_dependency_is_a_violation(tmp_path):
    src = _write(tmp_path, "routes.py", _routes(
        '@router.post("/things")\n'
        'def create_thing(body: dict):\n'
        '    return {}\n'
    ))
    endpoints = ap.collect_endpoints(src, tmp_path / "absent_main.py")
    assert [e.is_violation for e in endpoints] == [True]


@pytest.mark.parametrize("marker", ap.AUTH_MARKERS)
def test_every_declared_auth_marker_clears_the_violation(tmp_path, marker):
    """All three markers must work. One silently dropped would re-open the gate."""
    src = _write(tmp_path, "routes.py", _routes(
        '@router.post("/things")\n'
        f'def create_thing(session=Depends({marker}(["STEWARD"]))):\n'
        '    return {}\n'
    ))
    endpoints = ap.collect_endpoints(src, tmp_path / "absent_main.py")
    assert endpoints[0].has_auth is True
    assert endpoints[0].is_violation is False


def test_a_public_endpoint_is_not_a_violation(tmp_path):
    src = _write(tmp_path, "routes.py", _routes(
        '@router.post("/session")\n'
        'def login(body: dict):\n'
        '    return {}\n'
    ))
    endpoints = ap.collect_endpoints(src, tmp_path / "absent_main.py")
    assert endpoints[0].is_violation is False, "POST /session is public by design"


def test_a_read_endpoint_is_not_counted_as_a_mutating_violation(tmp_path):
    src = _write(tmp_path, "routes.py", _routes(
        '@router.get("/things")\n'
        'def list_things():\n'
        '    return []\n'
    ))
    endpoint = ap.collect_endpoints(src, tmp_path / "absent_main.py")[0]
    assert endpoint.mutating is False
    assert endpoint.is_violation is False


def test_an_async_endpoint_is_not_skipped(tmp_path):
    """AsyncFunctionDef is a different AST node. Walking only FunctionDef would
    make every async route invisible -- and most of dq_router is async."""
    src = _write(tmp_path, "routes.py", _routes(
        '@router.post("/things")\n'
        'async def create_thing(body: dict):\n'
        '    return {}\n'
    ))
    endpoints = ap.collect_endpoints(src, tmp_path / "absent_main.py")
    assert len(endpoints) == 1
    assert endpoints[0].is_violation is True


def test_more_than_one_router_is_scanned(tmp_path):
    """Four of the eight real violations lived on the second router.

    A probe that only followed the router named `router` would have reported half
    the problem and been believed.
    """
    src = _write(tmp_path, "routes.py",
        "from fastapi import APIRouter\n\n"
        "router = APIRouter()\ndq_router = APIRouter()\n\n"
        '@router.post("/a")\n'
        'def a():\n    return {}\n\n'
        '@dq_router.post("/b")\n'
        'def b():\n    return {}\n'
    )
    endpoints = ap.collect_endpoints(src, tmp_path / "absent_main.py")
    assert {e.router for e in endpoints} == {"router", "dq_router"}
    assert all(e.is_violation for e in endpoints)


def test_a_router_guarded_at_mount_protects_all_of_its_endpoints(tmp_path):
    """The blind spot that produced a false CRITICAL on 2026-08-22.

    FastAPI allows the dependency on include_router; the route signatures then say
    nothing about auth. Reading only routes.py reported eight violations while the
    live service answered 401 to every one of them.
    """
    src = _write(tmp_path, "routes.py",
        "from fastapi import APIRouter\n\ndq_router = APIRouter()\n\n"
        '@dq_router.post("/publish")\n'
        'def publish():\n    return {}\n'
    )
    app = _write(tmp_path, "main.py",
        "from fastapi import Depends, FastAPI\n"
        "app = FastAPI()\n"
        'app.include_router(dq_router, dependencies=[Depends(require_role(["STEWARD"]))])\n'
    )
    assert ap.routers_guarded_at_mount(app) == {"dq_router"}
    assert ap.collect_endpoints(src, app)[0].is_violation is False


def test_a_mount_without_a_dependency_does_not_protect_anything(tmp_path):
    """The guard must be the dependency, not the mere presence of include_router."""
    src = _write(tmp_path, "routes.py",
        "from fastapi import APIRouter\n\ndq_router = APIRouter()\n\n"
        '@dq_router.post("/publish")\n'
        'def publish():\n    return {}\n'
    )
    app = _write(tmp_path, "main.py",
        "from fastapi import FastAPI\napp = FastAPI()\n"
        'app.include_router(dq_router, prefix="/api/v1")\n'
    )
    assert ap.routers_guarded_at_mount(app) == set()
    assert ap.collect_endpoints(src, app)[0].is_violation is True


def test_a_mount_dependency_without_an_auth_marker_does_not_count(tmp_path):
    """A rate-limit or tracing dependency is not authentication."""
    app = _write(tmp_path, "main.py",
        "from fastapi import Depends, FastAPI\napp = FastAPI()\n"
        "app.include_router(dq_router, dependencies=[Depends(rate_limit)])\n"
    )
    assert ap.routers_guarded_at_mount(app) == set()


def test_a_missing_app_module_is_survived_rather_than_crashing(tmp_path):
    """Reading main.py is best-effort; its absence must not take the probe down."""
    assert ap.routers_guarded_at_mount(tmp_path / "nope.py") == set()


def test_a_syntactically_broken_app_module_is_survived(tmp_path):
    app = _write(tmp_path, "main.py", "def (((\n")
    assert ap.routers_guarded_at_mount(app) == set()


def test_a_missing_routes_file_reports_not_applicable(monkeypatch, tmp_path):
    """No routes file means nothing was inspected -- that is not a clean bill."""
    monkeypatch.setattr(ap, "ROUTES", tmp_path / "absent.py")
    result = ap.evaluate(write_evidence=False)
    assert result.status == EvalStatus.NOT_APPLICABLE
    assert result.metrics == {}


def test_the_real_repository_reports_zero_unauthenticated_mutating_endpoints():
    """Anchored to the product as it stands after the 2026-08-22 fixes.

    Written so it fails loudly if a future endpoint ships unprotected, rather than
    having to be remembered and re-run by hand.
    """
    result = ap.evaluate(write_evidence=False)
    if result.status == EvalStatus.NOT_APPLICABLE:
        pytest.skip("routes.py not present in this checkout")
    assert result.metrics["unauthenticated_mutating_endpoints"].raw == 0
    assert result.metrics["total_endpoints_scanned"].raw > 0, "nothing was scanned"


# ---------------------------------------------------------------------------
# egress_probe -- HG-S3
# ---------------------------------------------------------------------------

from evalgate.gates.gate2_security import egress_probe as ep  # noqa: E402


def _trace(tmp_path: Path, entries: list) -> Path:
    directory = tmp_path / "test_runner"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "debug_test_results_run1.json").write_text(
        json.dumps(entries, ensure_ascii=False), encoding="utf-8"
    )
    return directory


def test_an_artifact_holding_whole_rows_is_a_violation(tmp_path):
    traces = _trace(tmp_path, [{
        "rule_id": "t.col.NOT_NULL",
        "sample_failures": [
            {"vendor_id": "Curb", "fare_amount": 8.6, "pickup_at": "2025-01-18"},
            {"vendor_id": "Curb", "fare_amount": 9.1, "pickup_at": "2025-01-19"},
        ],
    }])
    found = ep._empirical_rows(traces)
    assert found["files_scanned"] == 1
    assert len(found["raw_row_artifacts"]) == 1
    assert found["raw_row_artifacts"][0]["row_count"] == 2


def test_an_aggregate_only_artifact_is_clean(tmp_path):
    """A single-key sample is an identifier reference, not a row."""
    traces = _trace(tmp_path, [{
        "rule_id": "t.col.NOT_NULL",
        "sample_failures": [{"source_row_id": "row-00017"}],
    }])
    assert ep._empirical_rows(traces)["raw_row_artifacts"] == []


def test_an_entry_without_samples_is_clean(tmp_path):
    traces = _trace(tmp_path, [{"rule_id": "t.col.NOT_NULL", "checked_count": 50000}])
    assert ep._empirical_rows(traces)["raw_row_artifacts"] == []


def test_no_trace_directory_reports_nothing_scanned(tmp_path):
    """Zero files scanned is not the same as zero violations found.

    Reporting a clean result from an empty directory is the false negative this
    whole test module exists to prevent.
    """
    found = ep._empirical_rows(tmp_path / "absent")
    assert found["files_scanned"] == 0
    assert found["raw_row_artifacts"] == []


def test_a_malformed_trace_does_not_crash_the_probe(tmp_path):
    directory = tmp_path / "test_runner"
    directory.mkdir(parents=True)
    (directory / "debug_test_results_bad.json").write_text("{not json", encoding="utf-8")
    found = ep._empirical_rows(directory)
    assert found["files_scanned"] == 1
    assert found["raw_row_artifacts"] == []


def test_static_signals_read_from_the_given_source_root(tmp_path):
    nodes = tmp_path / "agents" / "nodes"
    nodes.mkdir(parents=True)
    (nodes / "test_runner_node.py").write_text(
        'q = "SELECT * FROM trips"\npayload = {"sample_failures": samples}\n',
        encoding="utf-8",
    )
    signals = ep._static_signals(tmp_path)
    assert signals["select_star_in_test_runner"] == 1
    assert signals["sample_failures_populated"] == 1


def test_a_source_root_without_the_nodes_reports_no_signals(tmp_path):
    assert ep._static_signals(tmp_path) == {}


def test_the_real_repository_still_reports_its_known_egress(tmp_path):
    """Anchored to today's product. Skips once the leak is fixed."""
    result = ep.evaluate(write_evidence=False)
    total = result.metrics["raw_or_pii_egress_violations"].raw
    if total == 0:
        pytest.skip("egress has been fixed; this anchor is no longer meaningful")
    assert total == (
        result.metrics["raw_row_egress_violations"].raw
        + result.metrics["pii_column_egress_violations"].raw
    ), "the blocking total must equal its two components"


# ---------------------------------------------------------------------------
# replay_detection -- HG-A1
# ---------------------------------------------------------------------------

from evalgate.gates.gate1_ai_quality import replay_evaluator as re_ev  # noqa: E402


def _report(tmp_path: Path, results: list, name: str = "test_run_a.json") -> Path:
    directory = tmp_path / "reports"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(
        json.dumps({"test_run_id": "a" * 32, "test_results": results}, ensure_ascii=False),
        encoding="utf-8",
    )
    return directory


def test_no_archived_runs_blocks_rather_than_scoring_zero(tmp_path):
    """The single most important assertion in this module.

    With no runs to replay there is no recall to report. Returning 0 would fail
    HG-A1 forever for the wrong reason; returning 1.0 would never fail it at all.
    """
    result = re_ev.evaluate({}, reports_dir=tmp_path / "absent", write_evidence=False)
    assert result.status == EvalStatus.BLOCKED_MISSING_GROUND_TRUTH
    assert result.metrics == {}


def test_a_report_without_results_is_not_loaded(tmp_path):
    directory = _report(tmp_path, [])
    assert re_ev.load_archived_runs(directory) == []


def test_a_malformed_report_is_skipped_not_fatal(tmp_path):
    directory = tmp_path / "reports"
    directory.mkdir(parents=True)
    (directory / "test_run_bad.json").write_text("{broken", encoding="utf-8")
    assert re_ev.load_archived_runs(directory) == []


def test_a_valid_report_is_loaded_with_its_path_recorded(tmp_path):
    directory = _report(tmp_path, [{"rule_id": "t.col.NOT_NULL", "status": "FAIL"}])
    runs = re_ev.load_archived_runs(directory)
    assert len(runs) == 1
    assert "__path__" in runs[0], "a finding must be traceable to the file it came from"


# ---------------------------------------------------------------------------
# contract_conformance -- scope boundaries
# ---------------------------------------------------------------------------

from evalgate.gates.gate6_governance import contract_conformance as cc  # noqa: E402


def test_every_out_of_scope_capability_has_a_check():
    checks, _ = cc.collect_checks()
    scope_ids = {c.id for c in checks if c.id.startswith("SCOPE-")}
    assert scope_ids == {cid for cid, _, _ in cc.OUT_OF_SCOPE}


def test_scope_checks_cite_the_section_they_come_from():
    checks, _ = cc.collect_checks()
    for check in checks:
        if check.id.startswith("SCOPE-"):
            assert check.source.endswith("#explicitly-outside-gate-2"), check.id


def test_scope_drift_is_reported_but_never_blocks():
    """EvalGate cannot know whether the spec or the code is the stale one.

    Blocking a release over a sentence in a document that may itself be out of date
    would teach the team to stop reading the gate.
    """
    result = cc.evaluate(write_evidence=False)
    drift = [f for f in result.critical_findings if f.id == "DRIFT-SCOPE"]
    if not drift:
        pytest.skip("no drift in this checkout")
    assert all(not f.blocks_release for f in drift)
    assert result.metrics["contract_drift_count"].raw == len(drift[0].detail.split(";"))


def test_scope_checks_do_not_inflate_the_quality_score():
    """Five absent capabilities passing must not make the product look better.

    Drift lives on its own axis; the score counts defects only.
    """
    checks, _ = cc.collect_checks()
    scored = [c for c in checks if not c.id.startswith("SCOPE-")]
    result = cc.evaluate(write_evidence=False)
    expected = sum(1 for c in scored if c.passed) / len(scored) * 100
    assert result.score == pytest.approx(expected, abs=0.01)


# ---------------------------------------------------------------------------
# config_static_check -- a control is not credited for merely existing (R-10)
# ---------------------------------------------------------------------------

from evalgate.gates.gate5_reliability import config_static_check as csc  # noqa: E402


def test_a_timeout_that_caused_failures_does_not_count_as_configured(monkeypatch):
    """The 25-second timeout that scored 14 points while breaking the product.

    Presence was rewarded and correctness was never asked about. This is the
    assertion that stops that from happening again.
    """
    monkeypatch.setattr(csc, "observed_timeout_failures", lambda: (1, 5))
    control = csc.collect_controls()["llm_timeout_configured"]
    assert control["value"] is False
    assert "failed ON this timeout" in control["note"]


def test_a_timeout_with_clean_observed_runs_counts(monkeypatch):
    monkeypatch.setattr(csc, "observed_timeout_failures", lambda: (0, 5))
    control = csc.collect_controls()["llm_timeout_configured"]
    assert control["value"] is True
    assert "completed within it" in control["note"]


def test_an_unobserved_timeout_is_reported_as_unverified(monkeypatch):
    """No runs means adequacy is unknown. Saying so beats implying it is fine."""
    monkeypatch.setattr(csc, "observed_timeout_failures", lambda: (0, 0))
    control = csc.collect_controls()["llm_timeout_configured"]
    assert "unverified" in control["note"]


def test_observed_timeout_failures_survives_an_empty_history(monkeypatch):
    monkeypatch.setattr(
        "evalgate.gates.gate1_ai_quality.run_outcome_integrity.collect_runs",
        lambda *a, **k: [],
    )
    assert csc.observed_timeout_failures() == (0, 0)


# ---------------------------------------------------------------------------
# asgi_behaviour_probe -- HG-S1 and HG-S2 by execution
# ---------------------------------------------------------------------------
#
# The probe answers by sending requests, so its own failure modes are different in
# kind from the static evaluators above. Three of them are fatal and silent:
#
#   * it touches the developer database instead of a throwaway one;
#   * it cannot build the app and reports zero violations rather than saying so;
#   * it counts a refusal that was never a refusal -- a 404 nobody could ever get
#     past, or a 422 that came from a missing body rather than from the CSRF check.
#
# Most of what follows drives a stand-in application whose every answer the test
# dictates. The real application refuses everything, which is the outcome we want but
# also the outcome under which a probe that reports nothing looks identical to a
# probe that works. To show that a leak would be *noticed*, the leak has to be built.

import asyncio  # noqa: E402

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from evalgate.gates.gate2_security import asgi_behaviour_probe as asgi  # noqa: E402

STUB_FIXTURES = {
    "steward": {"ds": "tenant-alpha", "run": "run-alpha"},
    "user": {"ds": "tenant-beta", "run": "run-beta"},
}
ALWAYS = lambda method, path: True  # noqa: E731 - route existence is not under test here


def _stub_app(responder):
    """A stand-in ASGI app whose every answer comes from ``responder(method, url)``.

    ``responder`` returns ``(status, payload)``. Login always succeeds so the test can
    concentrate on the question being probed.

    FastAPI is imported at module level on purpose: this file uses postponed
    annotation evaluation, so a function-local ``Request`` cannot be resolved from the
    module namespace and every parameter silently becomes a request body.
    """
    app = FastAPI()

    @app.post("/api/v1/session")
    async def _login() -> JSONResponse:
        return JSONResponse(
            {
                "username": "stub",
                "role": "STEWARD",
                "csrf_token": "stub-token",
                "expires_at": "2099-01-01T00:00:00",
            }
        )

    @app.api_route(
        "/api/v1/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"]
    )
    async def _catch_all(rest: str, request: Request) -> JSONResponse:
        status, payload = responder(request.method, str(request.url))
        return JSONResponse(payload, status_code=status)

    return app


# --- safety: the probe must never reach the developer database ---------------

def test_the_probe_runs_against_a_temporary_database_and_restores_the_real_one():
    """The single assertion that makes this probe safe to run on a laptop.

    It rebinds the product's engine, so a mistake here would have the evaluator
    writing probe tenants and fake DQ runs into whatever database the developer is
    actually using.
    """
    import src.services.rule_store as rule_store
    from src.config import get_settings

    settings = get_settings()
    before = (settings.database_url, settings.supabase_database_url, settings.dq_execution_backend)
    engine_before = rule_store._engine
    seen = {}
    original_init_db = rule_store.init_db

    def spy_init_db():
        seen["engine_url"] = str(rule_store._engine.url)
        seen["settings_url"] = get_settings().database_url
        return original_init_db()

    rule_store.init_db = spy_init_db
    try:
        result = asgi.evaluate(write_evidence=False)
    finally:
        rule_store.init_db = original_init_db

    if result.status == EvalStatus.NOT_EXECUTED:
        pytest.skip(f"probe could not run here: {result.metadata.get('reason')}")

    assert "evalgate-asgi-" in seen["engine_url"], "the engine was not inside the probe's temp dir"
    assert seen["engine_url"].endswith("evalgate_asgi_probe.db")
    assert seen["settings_url"] == seen["engine_url"], "settings and engine pointed at different databases"
    assert before[0] not in seen["engine_url"], "the developer database url leaked into the probe engine"

    # Everything the probe rebinds must be handed back, or every evaluator that runs
    # after it in the same process would silently read the probe's temp database.
    assert (settings.database_url, settings.supabase_database_url, settings.dq_execution_backend) == before
    assert rule_store._engine is engine_before


# --- discipline: not measured is never reported as clean ---------------------

def test_an_application_that_cannot_be_built_reports_not_executed_rather_than_zero(monkeypatch):
    """The most important test in this section.

    An import error must not look like a clean security sweep. Zero violations from a
    probe that never sent a request is indistinguishable, in the report, from zero
    violations found by one that sent them all.
    """
    monkeypatch.setattr(
        asgi, "_import_app", lambda: (_ for _ in ()).throw(ImportError("fastapi is not installed"))
    )
    result = asgi.evaluate(write_evidence=False)
    assert result.status == EvalStatus.NOT_EXECUTED
    assert result.metrics == {}, "a probe that did not run must publish no metric at all"
    assert "fastapi is not installed" in result.metadata["reason"]


def test_a_login_that_fails_is_not_reported_as_a_partial_pass(monkeypatch):
    """Without a session, three of the four questions cannot be asked.

    Reporting the anonymous sweep alone would leave cross_tenant_violations at 0 while
    nothing about tenancy had been tested.
    """
    monkeypatch.setitem(asgi.TENANTS, "steward", "not-the-password")
    result = asgi.evaluate(write_evidence=False)
    assert result.status == EvalStatus.NOT_EXECUTED
    assert result.metrics == {}
    assert "could not authenticate" in result.metadata["reason"]


def test_a_missing_httpx_is_blocked_by_capability_rather_than_passing(monkeypatch):
    monkeypatch.setattr(asgi, "httpx", None)
    result = asgi.evaluate(write_evidence=False)
    assert result.status == EvalStatus.BLOCKED_BY_SYSTEM_CAPABILITY
    assert result.metrics == {}


def test_a_running_event_loop_is_declined_rather_than_crashing():
    """``asyncio.run`` refuses to nest. Saying so beats raising inside the runner."""

    async def inside_a_loop():
        return asgi.evaluate(write_evidence=False)

    result = asyncio.run(inside_a_loop())
    assert result.status == EvalStatus.NOT_EXECUTED
    assert result.metrics == {}


# --- question 1: anonymous callers ------------------------------------------

def test_a_404_to_an_anonymous_caller_counts_as_reachable_not_as_blocked():
    """The technique that proved the eight dq_router endpoints were genuinely open.

    A 404 means the request travelled through every authentication dependency and
    reached the handler. Only 401 is a refusal; treating 404 as one would have let
    every path-parameter endpoint report itself protected.
    """
    app = _stub_app(lambda method, url: (404, {"code": "NOT_FOUND"}))
    cases = asyncio.run(asgi._probe_anonymous(app, [("POST", "/things/{id}")]))
    assert len(cases) == 1
    assert cases[0].status == 404
    assert cases[0].conclusive is True
    assert cases[0].blocked is False, "404 means the handler was reached"


def test_a_401_to_an_anonymous_caller_is_the_only_accepted_refusal():
    app = _stub_app(lambda method, url: (401, {"code": "SESSION_REQUIRED"}))
    cases = asyncio.run(asgi._probe_anonymous(app, [("POST", "/things/{id}")]))
    assert cases[0].blocked is True


def test_endpoints_public_by_design_are_not_swept_anonymously():
    """POST /session must answer an anonymous caller; that is what it is for."""
    app = _stub_app(lambda method, url: (200, {}))
    cases = asyncio.run(asgi._probe_anonymous(app, [("POST", "/session"), ("GET", "/status")]))
    assert cases == []


# --- question 2: cross-tenant (HG-S2) ---------------------------------------

def test_a_cross_tenant_leak_is_detected(monkeypatch):
    """Manufacture the breach HG-S2 exists to catch and confirm the probe sees it."""
    monkeypatch.setattr(asgi, "CROSS_TENANT_READS", (("GET", "/datasets/{ds}/quality-trends"),))
    monkeypatch.setattr(asgi, "CROSS_TENANT_WRITES", ())
    # Every dataset answered to everybody, including the listing.
    app = _stub_app(lambda method, url: (200, {"datasets": ["tenant-alpha", "tenant-beta"]}))

    cases = asyncio.run(asgi._probe_cross_tenant(app, STUB_FIXTURES, ALWAYS))
    reads = [c for c in cases if "quality-trends" in c.path]
    assert len(reads) == 2, "both directions must be probed, not just one"
    for case in reads:
        assert case.control_status == 200, "the owner control must have succeeded"
        assert case.conclusive is True
        assert case.blocked is False, "the other tenant's object was returned"

    listings = [c for c in cases if "listing" in c.path]
    assert [c.blocked for c in listings] == [False, False]
    assert all("present in the response" in c.note for c in listings)


def test_a_cross_tenant_refusal_is_recognised(monkeypatch):
    monkeypatch.setattr(asgi, "CROSS_TENANT_READS", (("GET", "/datasets/{ds}/quality-trends"),))
    monkeypatch.setattr(asgi, "CROSS_TENANT_WRITES", ())

    def responder(method, url):
        foreign = "tenant-beta" if "tenant-beta" in url else None
        if url.endswith("/datasets"):
            return 200, {"datasets": ["tenant-alpha", "tenant-beta"]}
        # Alpha is the owner in one direction and the intruder in the other; refusing
        # whichever id is not the caller's is emulated by refusing beta only, so the
        # steward direction is a refusal and the user direction is left to the listing.
        return (403, {"code": "DATASET_ACCESS_FORBIDDEN"}) if foreign else (200, {})

    cases = asyncio.run(asgi._probe_cross_tenant(_stub_app(responder), STUB_FIXTURES, ALWAYS))
    steward_read = next(c for c in cases if c.actor == "steward" and "quality-trends" in c.path)
    assert steward_read.control_status == 200
    assert steward_read.status == 403
    assert steward_read.blocked is True


def test_a_cross_tenant_case_whose_owner_request_fails_is_inconclusive_not_a_pass(monkeypatch):
    """The false negative this probe is most likely to produce.

    If the owner's own request answers 404, the identical 404 for the other tenant is
    not evidence of isolation -- the endpoint answers 404 to everyone. Counting it as
    a refusal would report perfect tenancy on an endpoint that was never reachable.
    """
    monkeypatch.setattr(asgi, "CROSS_TENANT_READS", (("GET", "/datasets/{ds}/quality-trends"),))
    monkeypatch.setattr(asgi, "CROSS_TENANT_WRITES", ())
    app = _stub_app(lambda method, url: (404, {"code": "NOT_FOUND"}))

    cases = asyncio.run(asgi._probe_cross_tenant(app, STUB_FIXTURES, ALWAYS))
    assert cases, "the matrix must not be empty"
    assert all(c.conclusive is False for c in cases)
    assert all("proves nothing" in c.note for c in cases if "quality-trends" in c.path)


def test_a_case_with_no_matching_route_is_inconclusive_rather_than_a_violation(monkeypatch):
    """A typo in a hand-written case answers 404, and 404 means "reached the handler".

    Without the routing-table check, a misspelled path would manufacture a CRITICAL
    finding out of nothing.
    """
    monkeypatch.setattr(asgi, "CROSS_TENANT_READS", (("GET", "/datasets/{ds}/typoo"),))
    monkeypatch.setattr(asgi, "CROSS_TENANT_WRITES", ())
    app = _stub_app(lambda method, url: (200, {"datasets": ["tenant-alpha", "tenant-beta"]}))

    cases = asyncio.run(asgi._probe_cross_tenant(app, STUB_FIXTURES, lambda method, path: False))
    typos = [c for c in cases if "typoo" in c.path]
    assert typos and all(c.conclusive is False for c in typos)
    assert all(c.note == "no route registered for this case" for c in typos)


def test_the_route_matcher_resolves_a_concrete_path_against_a_templated_route():
    exists = asgi._route_matcher([("GET", "/datasets/{id}/rows"), ("POST", "/dq-runs")])
    assert exists("GET", "/datasets/anything-at-all/rows") is True
    assert exists("POST", "/dq-runs") is True
    assert exists("GET", "/datasets/a/b/rows") is False, "a path parameter must not span a slash"
    assert exists("DELETE", "/dq-runs") is False, "the method is part of the route"


# --- question 3: role escalation (BFLA) -------------------------------------

def test_a_role_that_reaches_past_its_level_is_counted(monkeypatch):
    monkeypatch.setattr(
        asgi, "ROLE_ESCALATION_CASES", (("user", "GET", "/admin/users"),)
    )
    app = _stub_app(lambda method, url: (200, {"users": ["admin"]}))
    cases = asyncio.run(asgi._probe_role_escalation(app, STUB_FIXTURES, ALWAYS))
    assert len(cases) == 1
    assert cases[0].blocked is False and cases[0].conclusive is True


def test_a_role_escalation_answered_401_measured_nothing(monkeypatch):
    """A rejected session means the role gate was never consulted.

    Crediting that as a refusal would report role enforcement on a product whose
    sessions had simply stopped working.
    """
    monkeypatch.setattr(asgi, "ROLE_ESCALATION_CASES", (("user", "GET", "/admin/users"),))
    app = _stub_app(lambda method, url: (401, {"code": "SESSION_REQUIRED"}))
    cases = asyncio.run(asgi._probe_role_escalation(app, STUB_FIXTURES, ALWAYS))
    assert cases[0].conclusive is False
    assert cases[0].blocked is False


# --- question 4: CSRF --------------------------------------------------------

def test_a_422_that_came_from_body_validation_is_not_credited_to_csrf():
    """Status alone cannot attribute the refusal.

    Most write endpoints answer 422 to an empty body, so crediting every 422 would
    report CSRF as fully enforced on a server with no CSRF check at all.
    """

    def responder(method, url):
        if url.endswith("/workflows"):
            return 200, {"ok": True}
        return 422, {"code": "VALIDATION_ERROR"}

    cases = asyncio.run(
        asgi._probe_csrf(_stub_app(responder), [("POST", "/things")], STUB_FIXTURES)
    )
    probe = next(c for c in cases if c.path == "/things")
    assert probe.status == 422
    assert probe.conclusive is True
    assert probe.blocked is False
    assert "not from the CSRF check" in probe.note


def test_a_422_carrying_the_csrf_code_is_credited():
    def responder(method, url):
        if url.endswith("/workflows"):
            return 200, {"ok": True}
        return 422, {"code": "CSRF_INVALID"}

    cases = asyncio.run(
        asgi._probe_csrf(_stub_app(responder), [("POST", "/things")], STUB_FIXTURES)
    )
    assert next(c for c in cases if c.path == "/things").blocked is True


def test_when_a_valid_token_is_also_refused_the_whole_csrf_family_is_inconclusive():
    """A server that answers 422 to everything would otherwise score 100%."""
    cases = asyncio.run(
        asgi._probe_csrf(
            _stub_app(lambda method, url: (422, {"code": "CSRF_INVALID"})),
            [("POST", "/things")],
            STUB_FIXTURES,
        )
    )
    control = next(c for c in cases if "control" in c.path)
    assert control.blocked is False
    probe = next(c for c in cases if c.path == "/things")
    assert probe.conclusive is False
    assert "cannot be attributed to CSRF" in probe.note


def test_only_write_endpoints_are_probed_for_csrf():
    """verify_csrf returns early for GET/HEAD/OPTIONS, so a GET proves nothing."""
    cases = asyncio.run(
        asgi._probe_csrf(
            _stub_app(lambda method, url: (200, {})),
            [("GET", "/things"), ("POST", "/things")],
            STUB_FIXTURES,
        )
    )
    assert [c.path for c in cases if "control" not in c.path] == ["/things"]
    assert {c.method for c in cases if "control" not in c.path} == {"POST"}


# --- anchors against the product as it stands -------------------------------

@pytest.fixture(scope="module")
def live_result():
    result = asgi.evaluate(write_evidence=False)
    if result.status in {EvalStatus.NOT_EXECUTED, EvalStatus.BLOCKED_BY_SYSTEM_CAPABILITY}:
        pytest.skip(f"probe could not run here: {result.metadata.get('reason')}")
    return result


def test_hg_s2_is_measured_rather_than_deferred(live_result):
    """The gate hard_gates.yaml lists under `deferred` now has a number behind it.

    The count is asserted together with the cases behind it: zero violations out of
    zero cases is the shape this whole module exists to reject, and it is the shape
    HG-S2 had for every run since v3.
    """
    assert live_result.metrics["cross_tenant_violations"].raw is not None
    assert live_result.metadata["cases_by_question"]["CROSS_TENANT"] > 0
    assert live_result.metrics["probe_cases_conclusive"].raw > 0


def test_the_dq_router_rule_review_surface_is_not_owned_by_anybody(live_result):
    """Anchored to the product as it stands on 2026-08-23. Skips once it is fixed.

    dq_router is mounted with one role dependency covering USER, STEWARD and ADMIN,
    and calls require_dataset_access nowhere. A USER holding no grant on another
    tenant's dataset can list that tenant's proposed rules, approve them and publish
    them into the active ruleset -- PRODUCT_SPEC safety rule 3 by way of a router
    mount. `authz_probe` scores every one of these endpoints as protected, because
    the mount-time dependency it was taught to recognise is genuinely there; what is
    absent is object ownership, which no signature can express.
    """
    violations = live_result.metrics["cross_tenant_violations"].raw
    if violations == 0:
        pytest.skip("tenancy has been fixed; this anchor is no longer meaningful")
    assert live_result.status == EvalStatus.FAIL
    assert [f.id for f in live_result.critical_findings if f.blocks_release] == ["HG-S2"]
    assert live_result.metrics["cross_tenant_violations"].normalized == 0.0


def test_a_leak_is_only_counted_when_its_owner_control_succeeded(live_result):
    """No violation may rest on a case that proved nothing.

    Every reported crossing must have an owner request that answered below 400, or
    the finding is an artefact of an endpoint nobody could reach.
    """
    assert live_result.metrics["probe_cases_inconclusive"].raw == 0, (
        "inconclusive cases exist; check the evidence file before trusting the counts"
    )


def test_the_real_application_refuses_every_anonymous_caller(live_result):
    assert live_result.metrics["unauthenticated_endpoints_reachable"].raw == 0
    assert live_result.metadata["cases_by_question"]["ANONYMOUS"] > 0
    assert live_result.metadata["endpoints_probed"] > 0, "nothing was probed"


def test_the_real_application_refuses_every_role_escalation(live_result):
    assert live_result.metrics["role_escalation_violations"].raw == 0
    assert live_result.metadata["cases_by_question"]["ROLE"] > 0


def test_the_real_application_enforces_csrf_on_every_write(live_result):
    assert live_result.metrics["csrf_enforced_rate"].raw == 100.0
    assert live_result.metadata["cases_by_question"]["CSRF"] > 1, "only the control ran"


def test_the_behaviour_probe_and_the_static_probe_see_the_same_surface(live_result):
    """An endpoint the AST walk cannot see is where an unprotected route hides.

    ``authz_probe`` reads only routes.py; anything mounted elsewhere is invisible to
    it and would never be counted a violation no matter how open it was.
    """
    assert live_result.metadata["endpoints_invisible_to_static_probe"] == []
