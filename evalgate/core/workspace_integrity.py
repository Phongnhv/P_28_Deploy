"""Preflight: does this run describe the code that would actually be released?

An EvalGate verdict is a statement about a specific revision.  If product code is
staged or modified but not committed, the evaluators read one thing and the report
names another, and the verdict silently becomes a claim about a revision that does
not exist.  That is not a smaller version of being wrong -- it is being wrong in a
way nobody can detect from the report.

This runs before every gate.  A dirty product tree stops the run at
``EVALGATE_STALE`` instead of publishing a score, unless the operator passes
``--allow-dirty``, in which case the report is stamped so the caveat travels with
the number.

Only *product* paths count as dirty.  EvalGate's own files are excluded on purpose:
adding an evaluator must not make the harness refuse to run.
"""

from __future__ import annotations

import json

from evalgate.core import git_read
from evalgate.schemas.eval_result import (
    EvalResult,
    EvalStatus,
    Evidence,
    Finding,
    MetricValue,
    Severity,
)

PROJECT_ROOT = git_read.PROJECT_ROOT
EVIDENCE_DIR = PROJECT_ROOT / "evalgate" / "evidence" / "preflight"

GATE = "preflight"
EVALUATOR = "workspace_integrity_v1"

#: Paths whose state changes what the product does at runtime.
PRODUCT_PATHSPEC = ["src", "requirements.txt", "dbt_project", "scripts"]


def collect_state() -> dict[str, object]:
    staged = git_read.changed_paths(staged=True, pathspec=PRODUCT_PATHSPEC)
    unstaged = git_read.changed_paths(staged=False, pathspec=PRODUCT_PATHSPEC)
    untracked = git_read.untracked_paths(PRODUCT_PATHSPEC)
    unmerged = git_read.unmerged_paths()
    insertions, deletions = git_read.staged_line_delta(PRODUCT_PATHSPEC)
    return {
        "git_ref": git_read.head_ref(),
        "head_sha": git_read.head_sha(),
        "staged_product_paths": staged,
        "unstaged_product_paths": unstaged,
        "untracked_product_paths": untracked,
        "unmerged_paths": unmerged,
        "staged_insertions": insertions,
        "staged_deletions": deletions,
    }


def _reasons(state: dict[str, object]) -> list[str]:
    reasons: list[str] = []
    staged = state["staged_product_paths"]
    unstaged = state["unstaged_product_paths"]
    untracked = state["untracked_product_paths"]
    unmerged = state["unmerged_paths"]
    if staged:
        reasons.append(
            f"{len(staged)} product file(s) staged but not committed "
            f"(+{state['staged_insertions']}/-{state['staged_deletions']} lines)"
        )
    if unstaged:
        reasons.append(f"{len(unstaged)} product file(s) modified in the working tree")
    if untracked:
        reasons.append(f"{len(untracked)} untracked file(s) under product paths")
    if unmerged:
        reasons.append(f"{len(unmerged)} path(s) left in a conflicted merge state")
    return reasons


def evaluate(
    *, write_evidence: bool = True, baseline_run_id: str | None = None
) -> EvalResult:
    try:
        state = collect_state()
    except git_read.GitUnavailableError as exc:
        return EvalResult(
            gate=GATE,
            evaluator=EVALUATOR,
            status=EvalStatus.BLOCKED_MISSING_CREDENTIAL,
            metadata={"reason": f"git unavailable: {exc}"},
        )

    reasons = _reasons(state)
    dirty = bool(reasons)

    evidence: list[Evidence] = []
    if write_evidence:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        target = EVIDENCE_DIR / "workspace_integrity.json"
        target.write_text(
            json.dumps({**state, "dirty": dirty, "reasons": reasons},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        evidence.append(Evidence(type="file", path=str(target.relative_to(PROJECT_ROOT))))

    findings: list[Finding] = []
    if dirty:
        findings.append(
            Finding(
                # Deliberately not an HG-* id: staleness is a statement about the
                # validity of the run, not a hard gate on the product.
                id="PREFLIGHT-STALE",
                severity=Severity.HIGH,
                title="Workspace does not match any commit",
                detail=(
                    "This run cannot be attributed to a revision. "
                    + "; ".join(reasons)
                    + f". Report would name {state['git_ref']}."
                ),
                root_cause_hint=(
                    "EvalGate records git_ref but reads the working tree. When the two "
                    "disagree, the verdict describes a revision that was never evaluated"
                ),
                evidence_ref="evalgate/evidence/preflight/workspace_integrity.json",
                # The stale-run decision is raised by the runner, not by a blocking
                # finding, so that --allow-dirty stays a usable escape hatch.
                blocks_release=False,
            )
        )

    return EvalResult(
        gate=GATE,
        evaluator=EVALUATOR,
        # Preflight is a gate on the run, not a graded dimension: it deliberately
        # carries no score so it can never move the aggregate in either direction.
        status=EvalStatus.FAIL if dirty else EvalStatus.PASS,
        score=None,
        baseline_run_id=baseline_run_id,
        metrics={
            "workspace_dirty": MetricValue(
                raw=dirty, unit="boolean", normalized=0.0 if dirty else 100.0
            ),
            "staged_product_files": MetricValue(
                raw=len(state["staged_product_paths"]), unit="count", normalized=None
            ),
            "staged_line_delta": MetricValue(
                raw=int(state["staged_insertions"]) + int(state["staged_deletions"]),
                unit="count",
                normalized=None,
            ),
            "unmerged_paths": MetricValue(
                raw=len(state["unmerged_paths"]), unit="count", normalized=None
            ),
        },
        evidence=evidence,
        critical_findings=findings,
        metadata={"reasons": reasons, "git_ref": state["git_ref"]},
    )
