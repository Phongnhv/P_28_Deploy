"""Which endpoints mutate state, trigger an LLM, or run SQL without a session?

The probe is a static AST walk rather than a live HTTP sweep: it needs no running
server, no database, and no credentials, and it cannot accidentally mutate real
data.  Every decorated route in ``src/api/routes.py`` is inspected for an
authentication dependency in its own signature.
"""

from __future__ import annotations

import ast
import json
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
ROUTES = PROJECT_ROOT / "src" / "api" / "routes.py"
#: Where routers are mounted. A dependency attached at mount time protects every
#: endpoint on that router, and reading only the route signatures misses it entirely.
APP_MODULE = PROJECT_ROOT / "src" / "main.py"
EVIDENCE_DIR = PROJECT_ROOT / "evalgate" / "evidence" / "gate2"

GATE = "ai_security"
EVALUATOR = "authz_probe_v1"

MUTATING_METHODS = {"post", "put", "patch", "delete"}
AUTH_MARKERS = ("require_role", "get_session", "require_dataset_access")

#: Endpoints that are public by design.
PUBLIC_ALLOW_LIST = {
    ("post", "/session"),
    # Logging out without a valid session is a no-op, not an object-level breach.
    ("delete", "/session"),
    ("get", "/status"),
    ("get", "/health"),
    ("get", "/ready"),
}


@dataclass
class Endpoint:
    router: str
    method: str
    path: str
    function: str
    line: int
    has_auth: bool
    mutating: bool

    @property
    def is_violation(self) -> bool:
        if (self.method, self.path) in PUBLIC_ALLOW_LIST:
            return False
        return self.mutating and not self.has_auth


def _decorator_info(node: ast.AST) -> tuple[str, str, str] | None:
    """Return (router, method, path) for a FastAPI route decorator."""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    method = node.func.attr.lower()
    if method not in MUTATING_METHODS | {"get"}:
        return None
    router = getattr(node.func.value, "id", "?")
    path = ""
    if node.args and isinstance(node.args[0], ast.Constant):
        path = str(node.args[0].value)
    return router, method, path


def routers_guarded_at_mount(app_path: Path = APP_MODULE) -> set[str]:
    """Routers whose ``include_router`` call carries an auth dependency.

    FastAPI lets a dependency be attached to the whole router at mount time:

        app.include_router(dq_router, dependencies=[Depends(require_role([...]))])

    Every endpoint on that router then requires a session, even though none of their
    own signatures mention it. Reading only ``routes.py`` reports all of them as
    unauthenticated -- a false positive that would block a release for a control that
    is present and working. Verified against the running service: those endpoints
    return 401 while this probe still called them violations.
    """
    if not app_path.exists():
        return set()
    try:
        tree = ast.parse(app_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()

    guarded: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "include_router" or not node.args:
            continue
        router_arg = node.args[0]
        router_name = getattr(router_arg, "id", None)
        if not router_name:
            continue
        for keyword in node.keywords:
            if keyword.arg != "dependencies":
                continue
            rendered = ast.dump(keyword.value)
            if any(marker in rendered for marker in AUTH_MARKERS):
                guarded.add(router_name)
    return guarded


def collect_endpoints(
    source_path: Path = ROUTES, app_path: Path = APP_MODULE
) -> list[Endpoint]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    guarded_routers = routers_guarded_at_mount(app_path)
    endpoints: list[Endpoint] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            info = _decorator_info(decorator)
            if info is None:
                continue
            router, method, path = info
            signature = ast.dump(node.args)
            has_auth = any(marker in signature for marker in AUTH_MARKERS)
            # A router guarded at mount time protects every endpoint it carries.
            if not has_auth and router in guarded_routers:
                has_auth = True
            endpoints.append(
                Endpoint(
                    router=router,
                    method=method,
                    path=path,
                    function=node.name,
                    line=node.lineno,
                    has_auth=has_auth,
                    mutating=method in MUTATING_METHODS,
                )
            )
    return endpoints


def evaluate(*, write_evidence: bool = True) -> EvalResult:
    if not ROUTES.exists():
        return EvalResult(
            gate=GATE, evaluator=EVALUATOR, status=EvalStatus.NOT_APPLICABLE,
            metadata={"reason": f"{ROUTES} not found"},
        )

    endpoints = collect_endpoints()
    violations = [e for e in endpoints if e.is_violation]
    unauth_reads = [
        e
        for e in endpoints
        if not e.mutating
        and not e.has_auth
        and (e.method, e.path) not in PUBLIC_ALLOW_LIST
    ]
    by_router: dict[str, int] = {}
    for endpoint in violations:
        by_router[endpoint.router] = by_router.get(endpoint.router, 0) + 1

    evidence: list[Evidence] = []
    if write_evidence:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        target = EVIDENCE_DIR / "authz_probe_matrix.json"
        target.write_text(
            json.dumps(
                {
                    "source": str(ROUTES.relative_to(PROJECT_ROOT)),
                    "total_endpoints": len(endpoints),
                    "mutating_endpoints": sum(1 for e in endpoints if e.mutating),
                    "violations": [asdict(e) for e in violations],
                    "violations_by_router": by_router,
                    "unauthenticated_read_endpoints": [asdict(e) for e in unauth_reads],
                    "all_endpoints": [asdict(e) for e in endpoints],
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        evidence.append(Evidence(type="file", path=str(target.relative_to(PROJECT_ROOT))))

    findings = []
    if violations:
        listing = ", ".join(f"{e.method.upper()} {e.path}" for e in violations[:8])
        findings.append(
            Finding(
                id="HG-S1",
                severity=Severity.CRITICAL,
                title=f"{len(violations)} mutating endpoints accept unauthenticated calls",
                detail=(
                    f"No require_role / get_session / require_dataset_access dependency "
                    f"in the signature. By router: {by_router}. Examples: {listing}"
                ),
                root_cause_hint=(
                    "dq_router was added without the dependency wiring the main router uses"
                ),
                evidence_ref="evalgate/evidence/gate2/authz_probe_matrix.json",
                blocks_release=True,
            )
        )

    return EvalResult(
        gate=GATE,
        evaluator=EVALUATOR,
        status=EvalStatus.FAIL if violations else EvalStatus.PASS,
        score=norm.zero_tolerance(len(violations)),
        metrics={
            "unauthenticated_mutating_endpoints": MetricValue(
                raw=len(violations), unit="count",
                normalized=norm.zero_tolerance(len(violations)),
            ),
            "unauthenticated_read_endpoints": MetricValue(
                raw=len(unauth_reads), unit="count",
                normalized=norm.zero_tolerance(len(unauth_reads)),
                note="read surface; object-level exposure is scored by HG-S2",
            ),
            "total_endpoints_scanned": MetricValue(
                raw=len(endpoints), unit="count", normalized=None
            ),
        },
        thresholds={
            "unauthenticated_mutating_endpoints": Threshold(**{"pass": 0.0, "warn": 0.0})
        },
        evidence=evidence,
        critical_findings=findings,
        metadata={
            "violations_by_router": by_router,
            "unauthenticated_read_count": len(unauth_reads),
        },
    )
