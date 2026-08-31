"""Did this change remove something the product could previously do?

Nothing in the existing pipeline answers that question.  ruff sees no syntax error,
pytest has no assertion covering the behaviour, code review sees a file with a
familiar name, and EvalGate itself scores whichever tree happens to be on disk.  A
capability can therefore be deleted by a change that looks like a refactor, and the
system will report a perfect score for work it no longer performs.

The gate compares a declared marker across three refs -- baseline, HEAD and the
index -- and classifies the transition:

    baseline present, now absent  -> REGRESSION   (CRITICAL blocks the release)
    baseline absent,  now absent  -> KNOWN_GAP    (pre-existing; reported, not blocking)
    baseline absent,  now present -> IMPROVEMENT  (reported)

Collapsing KNOWN_GAP into REGRESSION would block every release forever and make the
gate meaningless, so the distinction is load-bearing rather than cosmetic.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from evalgate.core import git_read
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

PROJECT_ROOT = git_read.PROJECT_ROOT
CONFIG = PROJECT_ROOT / "evalgate" / "config" / "capabilities.yaml"
EVIDENCE_DIR = PROJECT_ROOT / "evalgate" / "evidence" / "gate6"

GATE = "governance"
EVALUATOR = "capability_regression_v1"

REGRESSION = "REGRESSION"
KNOWN_GAP = "KNOWN_GAP"
IMPROVEMENT = "IMPROVEMENT"
INTACT = "INTACT"


class BaselineUnavailableError(RuntimeError):
    """The requested baseline ref cannot be read, so no comparison is possible."""


@dataclass
class CapabilityOutcome:
    id: str
    severity: str
    state: str
    present_at_baseline: bool
    present_now: bool
    baseline_ref: str
    matched_by: str | None
    why: str


def _matches(ref: str, detector: dict, files_at_ref: list[str]) -> str | None:
    """Return a human-readable locator when ``detector`` matches at ``ref``."""
    if "path_exists" in detector:
        path = detector["path_exists"]
        return f"{path} exists" if path in files_at_ref else None

    pattern = detector.get("pattern")
    if not pattern:
        return None
    compiled = re.compile(pattern, re.MULTILINE)

    if "file" in detector:
        candidates = [detector["file"]]
    else:
        prefix = detector.get("under", "")
        suffix = detector.get("suffix", "")
        candidates = [
            path
            for path in files_at_ref
            if path.startswith(prefix) and path.endswith(suffix)
        ]

    for path in candidates:
        content = git_read.read_file(ref, path)
        if content and compiled.search(content):
            return path
    return None


def _present(ref: str, capability: dict, files_at_ref: list[str]) -> tuple[bool, str | None]:
    hit: str | None = None
    for detector in capability.get("detect", []):
        hit = _matches(ref, detector, files_at_ref)
        if hit:
            break
    if capability.get("invert", False):
        # An inverted capability is the absence of the marker: "sql_text is not a
        # public field" holds precisely when nothing matches.
        return (hit is None), (None if hit is None else f"violating marker at {hit}")
    return (hit is not None), hit


def load_capabilities(path: Path = CONFIG) -> list[dict]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    return list(document.get("capabilities", []))


def compare(
    *,
    baseline_ref: str = "HEAD",
    current_ref: str = git_read.INDEX_REF,
    capabilities: list[dict] | None = None,
) -> list[CapabilityOutcome]:
    """Classify every declared capability across two refs.

    ``current_ref`` defaults to the index so that work already staged is judged
    before it becomes a commit, which is the last moment a regression is cheap to
    reverse.
    """
    declared = capabilities if capabilities is not None else load_capabilities()
    if not git_read.ref_exists(baseline_ref):
        # Silently comparing against a ref that no longer exists would report every
        # capability as absent at the baseline, i.e. zero regressions. That is the
        # worst possible failure for this gate, so it is refused outright.
        raise BaselineUnavailableError(
            f"baseline ref {baseline_ref!r} does not resolve to a commit"
        )
    baseline_files = git_read.list_files(baseline_ref)
    current_files = git_read.list_files(current_ref)

    outcomes: list[CapabilityOutcome] = []
    for capability in declared:
        before, _ = _present(baseline_ref, capability, baseline_files)
        after, locator = _present(current_ref, capability, current_files)
        if before and not after:
            state = REGRESSION
        elif not before and after:
            state = IMPROVEMENT
        elif before and after:
            state = INTACT
        else:
            state = KNOWN_GAP
        outcomes.append(
            CapabilityOutcome(
                id=capability["id"],
                severity=str(capability.get("severity", "MEDIUM")).upper(),
                state=state,
                present_at_baseline=before,
                present_now=after,
                baseline_ref=baseline_ref,
                matched_by=locator,
                why=str(capability.get("why", "")).strip(),
            )
        )
    return outcomes


def evaluate(
    *,
    write_evidence: bool = True,
    baseline_ref: str = "HEAD",
    baseline_run_id: str | None = None,
) -> EvalResult:
    try:
        outcomes = compare(baseline_ref=baseline_ref)
    except git_read.GitUnavailableError as exc:
        return EvalResult(
            gate=GATE,
            evaluator=EVALUATOR,
            status=EvalStatus.BLOCKED_MISSING_CREDENTIAL,
            metadata={"reason": f"git unavailable: {exc}"},
        )
    except BaselineUnavailableError as exc:
        return EvalResult(
            gate=GATE,
            evaluator=EVALUATOR,
            status=EvalStatus.BLOCKED_MISSING_GROUND_TRUTH,
            metadata={"reason": str(exc)},
        )
    except (OSError, yaml.YAMLError) as exc:
        return EvalResult(
            gate=GATE,
            evaluator=EVALUATOR,
            status=EvalStatus.BLOCKED_MISSING_GROUND_TRUTH,
            metadata={"reason": f"capability registry unreadable: {exc}"},
        )

    regressions = [o for o in outcomes if o.state == REGRESSION]
    critical = [o for o in regressions if o.severity == "CRITICAL"]
    known_gaps = [o for o in outcomes if o.state == KNOWN_GAP]
    improvements = [o for o in outcomes if o.state == IMPROVEMENT]
    intact = [o for o in outcomes if o.state == INTACT]

    # Score reflects retention, not overall product health: a pre-existing gap is
    # already counted by the gate that owns it, and must not be charged twice.
    retained = len(intact) + len(improvements)
    retainable = retained + len(regressions)
    score = norm.ratio(retained / retainable) if retainable else 100.0

    evidence: list[Evidence] = []
    if write_evidence:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        target = EVIDENCE_DIR / "capability_regression.json"
        target.write_text(
            json.dumps(
                {
                    "baseline_ref": baseline_ref,
                    "current_ref": "index (staged)",
                    "outcomes": [asdict(o) for o in outcomes],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        evidence.append(Evidence(type="file", path=str(target.relative_to(PROJECT_ROOT))))

    findings = [
        Finding(
            id="HG-R1",
            severity=Severity.CRITICAL if o.severity == "CRITICAL" else Severity.HIGH,
            title=f"Capability lost: {o.id}",
            detail=(
                f"Present at {o.baseline_ref}, absent in the staged tree. {o.why}"
            ),
            root_cause_hint=(
                "no existing control detects this: it is not a syntax error, no test "
                "asserts the behaviour, and EvalGate scored a different revision"
            ),
            evidence_ref="evalgate/evidence/gate6/capability_regression.json",
            blocks_release=(o.severity == "CRITICAL"),
        )
        for o in regressions
    ]

    if critical:
        status = EvalStatus.FAIL
    elif regressions:
        status = EvalStatus.WARN
    else:
        status = EvalStatus.PASS

    return EvalResult(
        gate=GATE,
        evaluator=EVALUATOR,
        status=status,
        score=score,
        baseline_run_id=baseline_run_id,
        metrics={
            "critical_capability_regressions": MetricValue(
                raw=len(critical), unit="count",
                normalized=norm.zero_tolerance(len(critical)),
            ),
            "capability_regressions": MetricValue(
                raw=len(regressions), unit="count",
                normalized=norm.zero_tolerance(len(regressions)),
            ),
            "capability_known_gaps": MetricValue(
                raw=len(known_gaps), unit="count", normalized=None,
            ),
            "capability_improvements": MetricValue(
                raw=len(improvements), unit="count", normalized=None,
            ),
        },
        thresholds={
            "critical_capability_regressions": Threshold(**{"pass": 0.0, "warn": 0.0}),
        },
        evidence=evidence,
        critical_findings=findings,
        metadata={
            "baseline_ref": baseline_ref,
            "regressions": [o.id for o in regressions],
            "known_gaps": [o.id for o in known_gaps],
            "improvements": [o.id for o in improvements],
            "note": (
                "known gaps are pre-existing and are reported by the gate that owns "
                "them; only a loss relative to the baseline blocks a release"
            ),
        },
    )
