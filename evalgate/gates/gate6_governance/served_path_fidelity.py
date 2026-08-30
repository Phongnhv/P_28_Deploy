"""Gate 6D: is the path users reach the same path this gate is grading?

Every AI-quality number in this report is measured against the LangGraph agent.
That is only meaningful if the agent is what answers a user's request.  If the
served path returns canned output instead, the report describes code nobody
reaches, and a high score would be actively misleading rather than merely
incomplete.

The concrete case this exists for: ``settings.agent_mode`` defaults to ``mock``,
``AGENT_MODE`` is set in neither ``.env`` nor ``docker-compose.yml``, and
``generate_dashboard_proposals`` returns ``_mock_proposals(evidence)`` before any
model is called.  ``GET /api/v1/status`` reports ``"agent_mode":"mock"`` on the
running container.  No other evaluator asks about this, so a system that had
quietly stopped using its own agent would still be graded on the agent.

Two independent conditions are checked, because either alone is enough to make
the served path a fake:

  the mode switch resolves to a mocked branch
  no provider credential reaches the deployed service, so the real branch could
    not run even if it were selected

Everything here is read from files.  Nothing is imported from ``src`` and no
value of any credential is recorded -- only whether one is present, since the
evidence file is written into the repository.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from evalgate.normalizers import normalizers as norm
from evalgate.schemas.eval_result import (
    EvalResult,
    EvalStatus,
    Evidence,
    Finding,
    MetricValue,
    Severity,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC = PROJECT_ROOT / "src"
EVIDENCE_DIR = PROJECT_ROOT / "evalgate" / "evidence" / "gate6"

GATE = "governance"
EVALUATOR = "served_path_fidelity_v1"

#: Files that decide what the deployed service sees. Order is irrelevant: any one
#: of them setting the mode to a live value is enough to clear the finding.
DEPLOYMENT_CONFIGS = (
    "docker-compose.yml",
    "docker-compose.yaml",
    "docker-compose.override.yml",
    ".env",
    ".env.example",
    "Dockerfile",
)

#: The setting that selects between the real agent and the canned one.
MODE_SETTING = "AGENT_MODE"

#: Values of MODE_SETTING that mean "call the real model".
LIVE_MODES = frozenset({"graph"})

#: Any one of these reaching the service is enough for the live branch to work.
PROVIDER_KEYS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "MISTRAL_API_KEY",
    "GOOGLE_API_KEY",
)

#: ``agent_mode: Literal["mock", "graph"] = os.getenv("AGENT_MODE") or "mock"``
_DEFAULT_MODE = re.compile(
    r"agent_mode\s*:\s*[^=]+=\s*os\.getenv\(\s*['\"]AGENT_MODE['\"]\s*\)\s*or\s*['\"](\w+)['\"]"
)

#: ``if settings.agent_mode == "mock":`` and near-variants.
_MOCK_BRANCH = re.compile(r"agent_mode\s*==\s*['\"]mock['\"]")


@dataclass
class ConfigSetting:
    """Where a setting was found, and what it was set to."""

    file: str
    value: str | None
    line: int


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _find_setting(name: str) -> list[ConfigSetting]:
    """Every deployment config that assigns ``name``, with the value it assigns.

    Matches both ``NAME=value`` (env file, Dockerfile ENV) and the compose list
    form ``- NAME=value``. A bare ``- NAME`` with no ``=`` is a pass-through from
    the host environment: it sets nothing by itself, so it is recorded with a
    value of None rather than being treated as a definition.
    """
    assignment = re.compile(
        r"^\s*(?:-\s*|ENV\s+)?" + re.escape(name) + r"\s*(?:=\s*(?P<value>[^\s#]*))?\s*(?:#.*)?$"
    )
    found: list[ConfigSetting] = []
    for filename in DEPLOYMENT_CONFIGS:
        path = PROJECT_ROOT / filename
        if not path.exists():
            continue
        for number, line in enumerate(_text(path).splitlines(), start=1):
            match = assignment.match(line)
            if not match:
                continue
            raw = match.group("value")
            value = raw.strip().strip("'\"") if raw else None
            found.append(ConfigSetting(file=filename, value=value or None, line=number))
    return found


def _grep_src(pattern: re.Pattern[str]) -> list[str]:
    hits: list[str] = []
    if not SRC.exists():
        return hits
    for path in SRC.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        for number, line in enumerate(_text(path).splitlines(), start=1):
            if pattern.search(line):
                hits.append(str(path.relative_to(PROJECT_ROOT)).replace("\\", "/") + ":" + str(number))
    return hits


def inspect() -> dict[str, object]:
    """Resolve what the deployed service will actually do, and why."""
    declared = _find_setting(MODE_SETTING)
    live_settings = [s for s in declared if s.value and s.value.lower() in LIVE_MODES]

    code_default: str | None = None
    default_site: str | None = None
    config_py = SRC / "config.py"
    if config_py.exists():
        for number, line in enumerate(_text(config_py).splitlines(), start=1):
            match = _DEFAULT_MODE.search(line)
            if match:
                code_default = match.group(1)
                default_site = "src/config.py:" + str(number)
                break

    # The mode the service resolves to: an explicit live setting wins, otherwise the
    # in-code default applies. A setting present but empty (compose pass-through)
    # does not select anything.
    effective = live_settings[0].value if live_settings else code_default
    is_mocked = bool(effective and effective.lower() not in LIVE_MODES)

    credentials = {key: _find_setting(key) for key in PROVIDER_KEYS}
    # A key counts as reaching the service only where it is actually assigned a
    # value, or listed for pass-through in compose *and* present in .env.
    env_has = {
        key: any(s.file == ".env" and s.value for s in settings)
        for key, settings in credentials.items()
    }
    compose_passes = {
        key: any(s.file.startswith("docker-compose") for s in settings)
        for key, settings in credentials.items()
    }
    reaching = [
        key
        for key in PROVIDER_KEYS
        if any(
            s.value and not s.file.startswith(".env.example")
            for s in credentials[key]
            if s.file.startswith("docker-compose") or s.file == "Dockerfile"
        )
        or (env_has[key] and compose_passes[key])
    ]

    mock_branches = _grep_src(_MOCK_BRANCH)

    return {
        "mode_setting": MODE_SETTING,
        "declared_in_deployment_config": [
            {"file": s.file, "line": s.line, "value": s.value} for s in declared
        ],
        "code_default": code_default,
        "code_default_site": default_site,
        "effective_mode": effective,
        "served_path_is_mocked": is_mocked,
        "mock_branch_sites": mock_branches,
        # Presence only. Values are never read into evidence.
        "provider_key_present_in_env": {k: v for k, v in env_has.items() if v},
        "provider_key_passed_to_service": reaching,
        "llm_credential_reaches_service": bool(reaching),
    }


def evaluate(*, write_evidence: bool = True) -> EvalResult:
    facts = inspect()
    is_mocked = bool(facts["served_path_is_mocked"])
    has_credential = bool(facts["llm_credential_reaches_service"])
    mock_sites = list(facts["mock_branch_sites"])  # type: ignore[arg-type]

    if facts["effective_mode"] is None and not mock_sites:
        # Nothing in this repository selects between a real and a canned agent, so
        # there is no fidelity question to answer. Saying PASS here would claim a
        # check was made that was not.
        return EvalResult(
            gate=GATE,
            evaluator=EVALUATOR,
            status=EvalStatus.NOT_APPLICABLE,
            metadata={
                "reason": (
                    "no " + MODE_SETTING + " switch and no mock branch found; this "
                    "product does not appear to have a canned agent path"
                )
            },
        )

    findings: list[Finding] = []
    if is_mocked:
        declared = facts["declared_in_deployment_config"]
        where = (
            "set nowhere in " + ", ".join(DEPLOYMENT_CONFIGS[:4])
            if not declared
            else "declared at " + str(declared)
        )
        findings.append(
            Finding(
                id="HG-G5",
                severity=Severity.CRITICAL,
                title="The served path returns canned output instead of invoking the agent",
                detail=(
                    MODE_SETTING + " is " + where + ", so the service falls back to the "
                    "in-code default '" + str(facts["code_default"]) + "' at "
                    + str(facts["code_default_site"]) + ". "
                    + str(len(mock_sites)) + " branch site(s) short-circuit to canned output: "
                    + ", ".join(mock_sites[:4])
                ),
                root_cause_hint=(
                    "every ai_quality metric in this report grades the LangGraph agent; "
                    "while the served path is mocked those numbers describe code no user "
                    "reaches, so the aggregate overstates what is actually deployed"
                ),
                evidence_ref="evalgate/evidence/gate6/served_path_fidelity.json",
                blocks_release=True,
            )
        )

    if not has_credential:
        findings.append(
            Finding(
                id="CRED-UNSEEN",
                severity=Severity.HIGH,
                title="No provider credential is visible in any deployment config",
                detail=(
                    "none of " + ", ".join(PROVIDER_KEYS) + " is passed to the service in "
                    "docker-compose.yml or the Dockerfile. Confirmed against the running "
                    "container on 2026-08-22, where the api service had no OPENAI_API_KEY "
                    "in its environment"
                ),
                root_cause_hint=(
                    "a .env file on the host is not visible to a container unless the "
                    "variable is listed in the service environment or an env_file entry"
                ),
                evidence_ref="evalgate/evidence/gate6/served_path_fidelity.json",
                # Deliberately never blocking, and deliberately not an HG-* id. This
                # evaluator can read the configs in this repository; it cannot see a
                # secret manager or a CI-injected variable, so absence here is not
                # proof of absence at deploy time. Reported as a strong signal to
                # check, not as a fact established.
                blocks_release=False,
            )
        )

    checks = {
        "served_path_invokes_agent": not is_mocked,
        "llm_credential_reaches_service": has_credential,
    }
    score = sum(1 for passed in checks.values() if passed) / len(checks) * 100.0

    evidence: list[Evidence] = []
    if write_evidence:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        target = EVIDENCE_DIR / "served_path_fidelity.json"
        target.write_text(
            json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        evidence.append(
            Evidence(type="file", path="evalgate/evidence/gate6/served_path_fidelity.json")
        )

    return EvalResult(
        gate=GATE,
        evaluator=EVALUATOR,
        status=EvalStatus.FAIL if findings else EvalStatus.PASS,
        score=score,
        metrics={
            "served_path_is_mocked": MetricValue(
                raw=is_mocked,
                unit="boolean",
                normalized=norm.boolean(not is_mocked),
                note="effective mode: " + str(facts["effective_mode"]),
            ),
            "mock_branch_count": MetricValue(
                raw=len(mock_sites),
                unit="count",
                normalized=norm.zero_tolerance(len(mock_sites)) if is_mocked else 100.0,
                note="branch sites that return canned output",
            ),
            "llm_credential_reaches_service": MetricValue(
                raw=has_credential,
                unit="boolean",
                normalized=norm.boolean(has_credential),
            ),
        },
        evidence=evidence,
        critical_findings=findings,
        metadata={
            "effective_mode": facts["effective_mode"],
            "code_default": facts["code_default"],
            "checks": checks,
        },
    )
