"""Is this run worse than the last one, and in which specific way?

A gate that only ever judges the current revision in isolation cannot see a
direction of travel.  It reports the same score for a system that has always been
at 40 and for one that was at 90 last week -- and the second is an incident while
the first is a backlog.

Comparison needs somewhere to compare against, so this module also owns the run
history: each completed run is written to ``evalgate/runs/<run_id>/`` and indexed,
and the newest completed run becomes the default baseline for the next one.

Three transitions are treated as regressions:

  a hard gate that used to PASS and now FAILs
  an evaluator whose score drops by more than ``SCORE_DROP_LIMIT`` points
  the aggregate decision moving to a strictly worse band

Score drops are compared evaluator by evaluator, not gate by gate.  A gate score
is a mean over its members, so adding or removing one moves it without anything
having got worse -- and that phantom movement is arithmetically identical to a
real regression.  Only evaluators present in both runs are compared.

Only the first two block, because a decision change is always downstream of one of
them and blocking twice for the same cause would be noise.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from evalgate.aggregator import collapse_result_scores, evaluate_hard_gates, load_policy
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

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = PROJECT_ROOT / "evalgate" / "runs"
INDEX = RUNS_DIR / "index.json"
EVIDENCE_DIR = PROJECT_ROOT / "evalgate" / "evidence" / "governance"

GATE = "governance"
EVALUATOR = "regression_engine_v1"

#: A drop larger than this is a regression rather than measurement noise. Chosen to
#: sit above the swing a single dataset can cause through the P25 collapse.
SCORE_DROP_LIMIT = 10.0

#: Worst to best. Used only to describe a decision change, never to block on one.
DECISION_ORDER = [
    "EVALGATE_STALE",
    "RELEASE_BLOCKED",
    "FAIL",
    "INSUFFICIENT_COVERAGE",
    "WARNING",
    "PASS",
]


@dataclass
class RunRecord:
    run_id: str
    git_ref: str | None
    timestamp: str | None
    decision: str
    score: float | None
    path: str


# ---------------------------------------------------------------------------
# Run history
# ---------------------------------------------------------------------------

def load_index() -> list[dict[str, Any]]:
    if not INDEX.exists():
        return []
    try:
        return list(json.loads(INDEX.read_text(encoding="utf-8")).get("runs", []))
    except (OSError, json.JSONDecodeError):
        return []


def save_run(payload: dict[str, Any], *, keep: int = 30) -> Path | None:
    """Persist a completed run and add it to the index, newest first."""
    run_id = payload.get("run_id")
    if not run_id:
        return None
    target_dir = RUNS_DIR / str(run_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "result.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # Stale runs are still recorded -- the trend line is useful and hiding them would
    # make the history lie by omission -- but they are marked unusable so they can
    # never become the reference a later comparison is measured against.
    #
    # Staleness is the only disqualifier, and it is a narrow one on purpose. A
    # RELEASE_BLOCKED or FAIL run is still attributable to a revision, which is the
    # entire requirement for a comparison point: "was this control holding then, and
    # is it holding now" is answerable against a bad baseline just as well as
    # against a good one. Restricting usability to PASS/WARNING -- as this did --
    # deadlocks the whole HG-R* family on any product that has not passed yet: no
    # run is ever eligible, resolve_baseline always answers None, and both
    # regression_engine_v1 and HG-R3 report NOT_MEASURED forever. That is exactly
    # the state 51 stored runs were in.
    usable = payload.get("decision") != "EVALGATE_STALE"
    entry = {
        "run_id": run_id,
        "git_ref": payload.get("git_ref"),
        "timestamp": payload.get("timestamp") or datetime.now(UTC).isoformat(),
        "decision": payload.get("decision"),
        "score": payload.get("score"),
        "usable_as_baseline": usable,
        "path": str(target.relative_to(PROJECT_ROOT)).replace("\\", "/"),
    }
    runs = [r for r in load_index() if r.get("run_id") != run_id]
    runs.insert(0, entry)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(
        json.dumps({"runs": runs[:keep]}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return target


def resolve_baseline(baseline_run_id: str | None = None) -> dict[str, Any] | None:
    """Return the stored payload for the requested run, or the newest usable one.

    A stale run is never used as a baseline: comparing against a verdict that was
    itself unattributable would launder the problem forward.
    """
    for entry in load_index():
        if baseline_run_id and entry.get("run_id") != baseline_run_id:
            continue
        # An explicitly requested run is honoured even if it was stale: the operator
        # asked for that comparison. The automatic pick never falls back to one.
        if not baseline_run_id and not entry.get("usable_as_baseline", True):
            continue
        path = PROJECT_ROOT / entry.get("path", "")
        if not path.exists():
            continue
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return None


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def current_gate_scores(results: list[EvalResult]) -> dict[str, float]:
    """Per-gate score of the run in progress, using the aggregator's own collapse."""
    weights: dict[str, float] = load_policy("evaluation_policy")["score"]["weights"]
    per_gate: dict[str, list[float]] = {}
    for result in results:
        score = collapse_result_scores(result)
        if not result.counts_toward_aggregate() or score is None:
            continue
        per_gate.setdefault(result.gate, []).append(score)
    return {
        gate: sum(scores) / len(scores)
        for gate, scores in per_gate.items()
        if gate in weights
    }


def current_evaluator_scores(results: list[EvalResult]) -> dict[str, float]:
    """Per-*evaluator* score, keyed by evaluator name.

    A gate score is the mean over whichever evaluators contributed to it, so it
    moves when the set of contributors changes even if every contributor scored
    identically. That is not a regression, but it is indistinguishable from one at
    gate level -- it produced a phantom 14.36-point governance drop the first time
    an evaluator was added to the gate, and blocked a release for it.

    Comparing evaluator by evaluator removes the ambiguity: a number can only fall
    if the thing that produced it got worse.
    """
    return {
        result.evaluator: score
        for result in results
        for score in [collapse_result_scores(result)]
        if result.counts_toward_aggregate() and score is not None
    }


def baseline_evaluator_scores(baseline: dict[str, Any]) -> dict[str, float]:
    """The same mapping, recovered from a stored run payload.

    The stored payload holds full ``EvalResult`` dumps, so each one is rebuilt and
    put through ``collapse_result_scores`` -- the identical function used for the
    current run. Reading the raw ``score`` field instead would compare a collapsed
    P25 against an uncollapsed mean for every multi-dataset evaluator, which is the
    same apples-to-oranges error this module exists to eliminate.

    A dump that no longer validates (an older schema) is skipped: no comparison is
    strictly better than a wrong one.
    """
    scores: dict[str, float] = {}
    for entry in baseline.get("results") or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("evaluator")
        if not name:
            continue
        try:
            result = EvalResult.model_validate(entry)
        except ValidationError:
            continue
        if not result.counts_toward_aggregate():
            continue
        score = collapse_result_scores(result)
        if score is not None:
            scores[str(name)] = float(score)
    return scores


def profile_membership(profile: str | None) -> set[str] | None:
    """Evaluator names a profile selects, or None when the profile is unknown.

    Read from the registry rather than from ``run.load_profile`` to keep this module
    free of an import cycle; the two derive membership from the same SPECS tuple.
    """
    if not profile:
        return None
    from evalgate.core.evaluator_registry import SPECS

    return {spec.name for spec in SPECS if profile in spec.profiles}


def evaluate(
    results: list[EvalResult],
    *,
    baseline_run_id: str | None = None,
    write_evidence: bool = True,
    profile: str | None = None,
) -> EvalResult:
    baseline = resolve_baseline(baseline_run_id)
    if baseline is None:
        return EvalResult(
            gate=GATE,
            evaluator=EVALUATOR,
            status=EvalStatus.NOT_MEASURED,
            metadata={
                "reason": (
                    "no previous run is stored in evalgate/runs; this run becomes the "
                    "baseline for the next one"
                )
            },
        )

    baseline_id = baseline.get("run_id")
    current_versions = {
        "evaluation_schema_version": (
            results[0].evaluation_schema_version if results else None
        ),
        "policy_version": results[0].policy_version if results else None,
        "corpus_version": results[0].corpus_version if results else None,
        "normalizer_version": results[0].normalizer_version if results else None,
    }
    baseline_versions = {key: baseline.get(key) for key in current_versions}
    if baseline_versions != current_versions:
        return EvalResult(
            gate=GATE,
            evaluator=EVALUATOR,
            status=EvalStatus.NOT_MEASURED,
            baseline_run_id=baseline_id,
            metadata={
                "reason": "baseline evaluation contract is incompatible",
                "baseline_versions": baseline_versions,
                "current_versions": current_versions,
            },
        )
    baseline_scores: dict[str, Any] = baseline.get("gate_scores") or {}
    baseline_hard = {h["id"]: h for h in baseline.get("hard_gates", [])}
    current_scores = current_gate_scores(results)

    # Drops are measured per evaluator, never per gate. See current_evaluator_scores
    # for why: a gate mean moves when its membership changes, and that movement is
    # not a regression even though it is arithmetically identical to one.
    current_by_evaluator = current_evaluator_scores(results)
    baseline_by_evaluator = baseline_evaluator_scores(baseline)
    compared = sorted(set(current_by_evaluator) & set(baseline_by_evaluator))

    # ``compared`` is the intersection, so both runs executed every name in it and a
    # drop there is a real regression whatever the profile.
    drops: list[dict[str, Any]] = []
    for evaluator in compared:
        previous = baseline_by_evaluator[evaluator]
        current = current_by_evaluator[evaluator]
        delta = previous - current
        if delta > SCORE_DROP_LIMIT:
            drops.append(
                {
                    "evaluator": evaluator,
                    "gate": next(
                        (r.gate for r in results if r.evaluator == evaluator), None
                    ),
                    "before": round(previous, 2),
                    "after": round(current, 2),
                    "drop": round(delta, 2),
                }
            )

    # Evaluators that ran on only one side of the comparison. Recorded so the report
    # can show that the two runs measured different things, but never scored: an
    # evaluator arriving is new coverage and one leaving is a gap, and neither is a
    # statement about the product getting worse.
    # EVALUATOR is excluded from both sides: this result is appended to ``results``
    # only after the comparison runs, so it is structurally absent from the current
    # mapping and would be reported as "removed" on every run.
    #
    # Membership is scoped to what this profile actually selects. A `local` run
    # compares against a `ci` baseline in the ordinary case, and `ci` selects
    # eleven evaluators `local` never runs -- reporting each as "disappeared"
    # blocked every local run with eleven CRITICAL findings the moment a baseline
    # was first configured. An evaluator this profile does not select is out of
    # scope for the comparison, not missing from it.
    # Presence is "did this evaluator report at all", not "did it produce a score".
    # An evaluator that ran and honestly reported BLOCKED_BY_SYSTEM_CAPABILITY or
    # NOT_MEASURED contributes no score, so scoring it as removed accused the run of
    # deleting an evaluator that is sitting in the results. vacuity_probe_v1 does
    # exactly that whenever there is no input-dataset artifact.
    current_present = {result.evaluator for result in results}
    in_scope = profile_membership(profile)
    if in_scope is None:
        in_scope = current_present | set(baseline_by_evaluator)
    vanished = set(baseline_by_evaluator) - current_present - {EVALUATOR}
    composition_changed = {
        "added": sorted(
            (set(current_by_evaluator) - set(baseline_by_evaluator) - {EVALUATOR}) & in_scope
        ),
        "removed": sorted(vanished & in_scope),
        "out_of_profile": sorted(vanished - in_scope),
        "reported_without_a_score": sorted(
            (set(baseline_by_evaluator) & current_present) - set(current_by_evaluator)
        ),
    }

    findings: list[Finding] = [
        Finding(
            # Deliberately not an HG-* id. HG-R3 is declared in hard_gates.yaml as
            # "a hard gate that used to pass now fails", which is a different claim;
            # borrowing its id for a score drop would make the report describe a gate
            # failure that the policy never defined.
            id="REG-DROP",
            severity=Severity.HIGH,
            title=f"{drop['evaluator']} dropped {drop['drop']} points since {baseline_id}",
            detail=f"{drop['before']:.2f} -> {drop['after']:.2f}",
            root_cause_hint=(
                "the same evaluator scored lower against the same policy; compare its "
                "metrics and evidence file between the two runs"
            ),
            evidence_ref="evalgate/evidence/governance/regression.json",
            blocks_release=True,
        )
        for drop in drops
    ]

    removed_evaluators = composition_changed["removed"]
    findings += [
        Finding(
            id="REG-EVALUATOR-REMOVED",
            severity=Severity.CRITICAL,
            title=f"Evaluator disappeared since {baseline_id}: {name}",
            detail=(
                "Removing a measured evaluator reduces coverage and can hide a "
                "regression; restore it or approve a new policy version explicitly."
            ),
            evidence_ref="evalgate/evidence/governance/regression.json",
            blocks_release=True,
        )
        for name in removed_evaluators
    ]

    # Hard-gate status is read from the aggregator's own evaluation of this run, not
    # inferred from which findings happen to carry a matching id. Inference missed
    # HG-D2, which fails on a metric while its evaluator raises a finding under a
    # different id -- exactly the kind of gate whose regression must not go unseen.
    #
    # Only PASS -> FAIL counts. A gate moving from NOT_EVALUATED to FAIL is new
    # coverage arriving, which is progress rather than decay.
    current_hard = {h.id: h.status for h in evaluate_hard_gates(results)}
    newly_failing = sorted(
        gate_id
        for gate_id, status in current_hard.items()
        if status == "FAIL" and baseline_hard.get(gate_id, {}).get("status") == "PASS"
    )
    findings += [
        Finding(
            id="HG-R3",
            severity=Severity.CRITICAL,
            title=f"Hard gate {gate_id} passed at {baseline_id} and fails now",
            detail="a control that previously held has been broken by this change",
            evidence_ref="evalgate/evidence/governance/regression.json",
            blocks_release=True,
        )
        for gate_id in newly_failing
    ]

    max_drop = max((d["drop"] for d in drops), default=0.0)

    evidence: list[Evidence] = []
    if write_evidence:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        target = EVIDENCE_DIR / "regression.json"
        target.write_text(
            json.dumps(
                {
                    "baseline_run_id": baseline_id,
                    "baseline_git_ref": baseline.get("git_ref"),
                    "baseline_decision": baseline.get("decision"),
                    "baseline_gate_scores": baseline_scores,
                    "current_gate_scores": {k: round(v, 2) for k, v in current_scores.items()},
                    "evaluators_compared": compared,
                    "composition_changed": composition_changed,
                    "score_drops": drops,
                    "hard_gates_newly_failing": newly_failing,
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        evidence.append(Evidence(type="file", path=str(target.relative_to(PROJECT_ROOT))))

    return EvalResult(
        gate=GATE,
        evaluator=EVALUATOR,
        status=EvalStatus.FAIL if findings else EvalStatus.PASS,
        score=norm.inverse_ratio(min(1.0, max_drop / 100.0)),
        baseline_run_id=baseline_id,
        metrics={
            "gate_score_drop_max": MetricValue(
                raw=round(max_drop, 2), unit="count",
                normalized=norm.inverse_ratio(min(1.0, max_drop / 100.0)),
            ),
            "hard_gates_newly_failing": MetricValue(
                raw=len(newly_failing), unit="count",
                normalized=norm.zero_tolerance(len(newly_failing)),
            ),
        },
        thresholds={
            "gate_score_drop_max": Threshold(**{"pass": 0.0, "warn": SCORE_DROP_LIMIT}),
        },
        evidence=evidence,
        critical_findings=findings,
        metadata={
            "baseline_run_id": baseline_id,
            "baseline_git_ref": baseline.get("git_ref"),
            "score_drop_limit": SCORE_DROP_LIMIT,
            "evaluators_compared": len(compared),
            "composition_changed": composition_changed,
            "profile": profile,
            "baseline_profile": baseline.get("mode"),
        },
    )
