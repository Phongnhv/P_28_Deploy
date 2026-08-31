"""HG-S6: is a credential committed to the repository?

Only files git actually tracks are scanned.  A key sitting in an ignored ``.env``
is a local-hygiene question; a key in a tracked file is a disclosure.  Matches are
reported by location and prefix only -- never the secret itself.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from evalgate.core import scope
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
EVIDENCE_DIR = PROJECT_ROOT / "evalgate" / "evidence" / "gate2"

GATE = "ai_security"
EVALUATOR = "secret_scan_v1"

PATTERNS: dict[str, re.Pattern[str]] = {
    "openai_key": re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    "anthropic_key": re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "google_api_key": re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    "langfuse_secret": re.compile(r"sk-lf-[0-9a-f\-]{20,}"),
    "private_key_block": re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "postgres_url_with_password": re.compile(
        r"postgres(?:ql)?://[^:\s]+:(?P<password>[^@\s]{6,})@"
    ),
}

SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".parquet", ".db", ".ico"}
#: Credential values that are self-evidently illustrative. Matching a real secret
#: against this list is impossible in practice, and the alternative -- flagging every
#: tutorial connection string -- makes HG-S6 fire constantly and stop being read.
PLACEHOLDER_PASSWORDS = frozenset(
    {
        "password",
        "passwd",
        "agentpass",
        "localpassword",
        "mypassword",
        "test_password",
        "miniopassword",
        "postgres",
        "your_runner_password_here",
        # docs/guide/chapter-09.md:311, printed under the marker "# SAI: Hardcoded
        # API key" -- the chapter teaches the anti-pattern by showing it.
        "password123",
    }
)


def _is_exact_placeholder(pattern_name: str, match: re.Match[str]) -> bool:
    if pattern_name != "postgres_url_with_password":
        return False
    return match.group("password").lower() in PLACEHOLDER_PASSWORDS


def tracked_files() -> list[Path]:
    try:
        output = subprocess.run(
            ["git", "ls-files"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    paths = [PROJECT_ROOT / line for line in output.splitlines() if line.strip()]
    # EvalGate's own fixtures and documentation are not product secrets. See
    # evalgate/core/scope.py for why this exclusion is absolute rather than a
    # suppression with a TTL.
    return scope.product_only(paths)


def evaluate(*, write_evidence: bool = True) -> EvalResult:
    hits: list[dict[str, object]] = []
    scanned = 0

    for path in tracked_files():
        if path.suffix.lower() in SKIP_SUFFIXES or not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        scanned += 1
        for number, line in enumerate(text.splitlines(), start=1):
            for name, pattern in PATTERNS.items():
                match = pattern.search(line)
                if not match:
                    continue
                if _is_exact_placeholder(name, match):
                    continue
                hits.append(
                    {
                        "file": str(path.relative_to(PROJECT_ROOT)),
                        "line": number,
                        "pattern": name,
                        # Prefix only: the report must never carry the credential.
                        "redacted": match.group(0)[:6] + "..." + f"[{len(match.group(0))} chars]",
                    }
                )

    evidence: list[Evidence] = []
    if write_evidence:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        target = EVIDENCE_DIR / "secret_scan.json"
        target.write_text(
            json.dumps({"files_scanned": scanned, "findings": hits}, indent=2),
            encoding="utf-8",
        )
        evidence.append(Evidence(type="file", path=str(target.relative_to(PROJECT_ROOT))))

    findings = []
    if hits:
        findings.append(
            Finding(
                id="HG-S6",
                severity=Severity.CRITICAL,
                title=f"{len(hits)} credential-shaped strings in tracked files",
                detail="; ".join(f"{h['file']}:{h['line']} ({h['pattern']})" for h in hits[:6]),
                evidence_ref="evalgate/evidence/gate2/secret_scan.json",
                blocks_release=True,
            )
        )

    return EvalResult(
        gate=GATE,
        evaluator=EVALUATOR,
        status=EvalStatus.FAIL if hits else EvalStatus.PASS,
        score=norm.zero_tolerance(len(hits)),
        metrics={
            "secret_findings": MetricValue(
                raw=len(hits), unit="count", normalized=norm.zero_tolerance(len(hits))
            ),
            "tracked_files_scanned": MetricValue(
                raw=scanned, unit="count", normalized=None
            ),
        },
        evidence=evidence,
        critical_findings=findings,
    )
