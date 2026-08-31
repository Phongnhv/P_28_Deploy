"""Gate 2D: can a known-in-advance password log in to a deployed instance?

``HG-S7`` has existed in the policy since v3 and has reported NOT_EVALUATED on
every run, because nothing produced ``default_credentials_active``.  The policy
file states the rule it was then breaking: *a gate that reports NOT_EVALUATED
forever is not coverage -- it looks like a control while guaranteeing nothing.*
This is that gate's producer.

What it looks for is a seeding routine that creates accounts whose password is
derivable without any access to the system, and a call site that runs it without
first checking the environment.  Both halves matter.  Seeded demo accounts are a
reasonable convenience in local development; the risk is the seeding running
wherever ``init_db()`` runs, which includes production.

The finding is not hypothetical here.  On 2026-08-22 a POST to
``/api/v1/session`` with ``steward`` / ``steward`` returned 200 and a valid
session cookie against the running stack.

Static analysis only: no login is attempted, no password is transmitted, and the
credentials found are reported by *shape* -- "password equals username" -- rather
than by value, so the evidence file never carries a working credential.
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
SRC = PROJECT_ROOT / "src"
EVIDENCE_DIR = PROJECT_ROOT / "evalgate" / "evidence" / "gate2"

GATE = "ai_security"
EVALUATOR = "default_credential_probe_v1"

#: Names that hold seeded account tuples. Matched case-insensitively on the
#: assignment target, so DEFAULT_USERS, SEED_USERS and demo_users all qualify.
_SEED_NAMES = ("default_user", "seed_user", "demo_user", "default_account", "bootstrap_user")

#: Passwords that are weak regardless of what the username is.
_WEAK_PASSWORDS = frozenset(
    {"password", "admin", "changeme", "secret", "test", "123456", "letmein", ""}
)

#: A call site is considered guarded when one of these appears in an enclosing
#: condition. Deliberately broad: the question is whether the author thought about
#: the environment at all, and a false "guarded" is safer to under-report than a
#: false alarm that trains readers to ignore the gate.
_ENV_GUARDS = ("app_env", "environment", "is_local", "is_production", "debug", "testing")


@dataclass
class SeededCredential:
    """One account a seeding routine creates, described by shape not by value."""

    site: str
    username: str
    #: Why this credential is guessable. Never the password itself.
    weakness: str


@dataclass
class SeedCallSite:
    site: str
    function: str
    guarded_by: str | None


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None


def _python_files() -> list[Path]:
    if not SRC.exists():
        return []
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in str(p)]


def _rel(path: Path, line: int) -> str:
    return str(path.relative_to(PROJECT_ROOT)).replace("\\", "/") + ":" + str(line)


def _string_items(node: ast.AST) -> list[str] | None:
    """The string elements of a tuple/list literal, or None if it is not one."""
    if not isinstance(node, ast.Tuple | ast.List):
        return None
    values: list[str] = []
    for element in node.elts:
        if isinstance(element, ast.Constant) and isinstance(element.value, str):
            values.append(element.value)
    return values


def find_seeded_credentials() -> list[SeededCredential]:
    """Accounts created by a seeding constant where the password is guessable."""
    found: list[SeededCredential] = []
    for path in _python_files():
        tree = _parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            names = [t.id.lower() for t in node.targets if isinstance(t, ast.Name)]
            if not any(seed in name for name in names for seed in _SEED_NAMES):
                continue
            rows = node.value.elts if isinstance(node.value, ast.Tuple | ast.List) else []
            for row in rows:
                values = _string_items(row)
                if not values:
                    continue
                username = values[0]
                # Positional guessing across differing tuple shapes would be brittle,
                # so every other string is treated as a password candidate -- except
                # all-uppercase ones. Those are role constants (USER, ADMIN, STEWARD),
                # and reading a role as a password reports every seeded admin account
                # as weak no matter how strong its password is. That false positive
                # was caught by test_a_strong_seeded_password_is_not_reported.
                candidates = [v for v in values[1:] if not (v.isupper() and v.isalpha())]
                if any(v == username for v in candidates):
                    weakness = "password equals username"
                elif any(
                    v.lower() in _WEAK_PASSWORDS and v.lower() != username.lower()
                    for v in candidates
                ):
                    # The second clause drops the display name, which is conventionally
                    # a title-cased echo of the username ("admin" -> "Admin") and would
                    # otherwise be read as the common default "admin". A password that
                    # differs from its username only in case is then missed, which is
                    # the deliberate trade: the exact-match rule above already catches
                    # the case that actually occurs, and a systematic false positive on
                    # every seeded admin account is far more damaging than a rare miss.
                    weakness = "password is a common default"
                else:
                    continue
                found.append(
                    SeededCredential(
                        site=_rel(path, node.lineno), username=username, weakness=weakness
                    )
                )
    return found


def _enclosing_guard(tree: ast.Module, target: ast.AST) -> str | None:
    """The env-check condition a node sits inside, if any."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        if target not in ast.walk(node):
            continue
        condition = ast.dump(node.test).lower()
        for guard in _ENV_GUARDS:
            if guard in condition:
                return guard
    return None


def find_seed_call_sites() -> list[SeedCallSite]:
    """Every call to a seeding routine, and whether an environment check wraps it."""
    sites: list[SeedCallSite] = []
    for path in _python_files():
        tree = _parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else ""
            )
            lowered = name.lower()
            if "user" not in lowered or not any(
                verb in lowered for verb in ("ensure", "seed", "bootstrap", "create_default")
            ):
                continue
            # The definition itself is not a call site worth reporting.
            sites.append(
                SeedCallSite(
                    site=_rel(path, node.lineno),
                    function=name,
                    guarded_by=_enclosing_guard(tree, node),
                )
            )
    return sites


def evaluate(*, write_evidence: bool = True) -> EvalResult:
    credentials = find_seeded_credentials()
    call_sites = find_seed_call_sites()

    if not credentials and not call_sites:
        return EvalResult(
            gate=GATE,
            evaluator=EVALUATOR,
            status=EvalStatus.NOT_APPLICABLE,
            metadata={
                "reason": (
                    "no account-seeding constant or call site found under src/; this "
                    "product does not appear to seed credentials"
                )
            },
        )

    unguarded = [s for s in call_sites if s.guarded_by is None]
    # Active means both halves hold: guessable credentials exist, and something
    # creates them without asking which environment it is running in.
    active = bool(credentials and unguarded)

    findings: list[Finding] = []
    if active:
        findings.append(
            Finding(
                id="HG-S7",
                severity=Severity.CRITICAL,
                title="Guessable seeded accounts are created wherever the database is initialised",
                detail=(
                    str(len(credentials))
                    + " seeded account(s) with a guessable password ("
                    + ", ".join(sorted({c.username for c in credentials}))
                    + "), created from "
                    + str(len(unguarded))
                    + " unguarded call site(s): "
                    + ", ".join(s.site for s in unguarded[:3])
                ),
                root_cause_hint=(
                    "the seeding call sits inside init_db() with no environment check, "
                    "so every deployment that initialises its schema also creates these "
                    "accounts. Verified live on 2026-08-22: POST /api/v1/session with "
                    "steward/steward returned 200"
                ),
                evidence_ref="evalgate/evidence/gate2/default_credential_probe.json",
                blocks_release=True,
            )
        )
    elif credentials:
        findings.append(
            Finding(
                id="SEED-GUARDED",
                severity=Severity.LOW,
                title="Guessable seeded accounts exist but every call site is environment-guarded",
                detail=(
                    "guards found: "
                    + ", ".join(sorted({s.guarded_by or "" for s in call_sites if s.guarded_by}))
                ),
                root_cause_hint=(
                    "acceptable for local development; re-check if a guard is ever removed"
                ),
                evidence_ref="evalgate/evidence/gate2/default_credential_probe.json",
                blocks_release=False,
            )
        )

    evidence: list[Evidence] = []
    if write_evidence:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        target = EVIDENCE_DIR / "default_credential_probe.json"
        target.write_text(
            json.dumps(
                {
                    "seeded_credentials": [asdict(c) for c in credentials],
                    "call_sites": [asdict(s) for s in call_sites],
                    "unguarded_call_sites": [asdict(s) for s in unguarded],
                    "default_credentials_active": active,
                    "note": (
                        "credentials are described by shape only; no password value is "
                        "recorded and no login was attempted"
                    ),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        evidence.append(
            Evidence(type="file", path="evalgate/evidence/gate2/default_credential_probe.json")
        )

    return EvalResult(
        gate=GATE,
        evaluator=EVALUATOR,
        status=EvalStatus.FAIL if active else EvalStatus.PASS,
        score=0.0 if active else 100.0,
        metrics={
            "default_credentials_active": MetricValue(
                raw=active,
                unit="boolean",
                normalized=norm.boolean(not active),
                note=(
                    str(len(credentials))
                    + " guessable account(s), "
                    + str(len(unguarded))
                    + " unguarded call site(s)"
                ),
            ),
            "seeded_credential_count": MetricValue(
                raw=len(credentials),
                unit="count",
                normalized=norm.zero_tolerance(len(credentials)) if active else 100.0,
            ),
        },
        thresholds={"seeded_credential_count": Threshold(**{"pass": 0.0})},
        evidence=evidence,
        critical_findings=findings,
        metadata={
            "credentials_found": len(credentials),
            "call_sites": len(call_sites),
            "unguarded_call_sites": len(unguarded),
        },
    )
