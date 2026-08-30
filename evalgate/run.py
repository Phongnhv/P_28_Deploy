"""EvalGate orchestrator.

    python -m evalgate.run --mode local
    python -m evalgate.run --mode ci --out evalgate/reports

Exit codes are the CI contract:
0 PASS, 1 WARNING, 2 FAIL, 3 RELEASE_BLOCKED, 4 EVALGATE_STALE,
5 INSUFFICIENT_COVERAGE, 6 EVALGATE_INVALID.

The run has three stages, in this order:

  preflight   is this run attributable to a revision at all?
  evaluators  the gates selected by the profile for this mode
  regression  is this worse than the stored baseline, and where?

Preflight runs first because a verdict about an unattributable tree is not a weaker
verdict, it is a meaningless one.  Evaluators still execute so the developer sees
the numbers, but the decision is replaced with EVALGATE_STALE unless --allow-dirty
is passed, in which case the caveat is carried in the report instead.

Evaluators that need a network call, a paid model or an unavailable dependency are
declared rather than omitted, so the report distinguishes "measured and fine" from
"never looked".
"""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
import os
import re
import shutil
import sys
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import yaml

from evalgate.aggregator import Decision, aggregate
from evalgate.core import git_read, regression_engine, workspace_integrity
from evalgate.core.artifact_provenance import load_context
from evalgate.core.context import EvalRunContext
from evalgate.core.evaluator_registry import REGISTRY, SPECS, validate_profile
from evalgate.core.suppression_policy import apply_suppressions, load_suppressions
from evalgate.schemas.eval_result import EvalResult, EvalStatus

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = PROJECT_ROOT / "evalgate" / "reports"
PROFILES = PROJECT_ROOT / "evalgate" / "config" / "profiles.yaml"
SUPPRESSIONS = PROJECT_ROOT / "evalgate" / "policies" / "suppressions.yaml"
APPROVED_BASELINE = PROJECT_ROOT / "evalgate" / "policies" / "approved_baseline.yaml"

SDIH_SEED = 20260819


def _sanitized_error(exc: Exception) -> str:
    message = str(exc).replace(str(PROJECT_ROOT), "<workspace>")
    message = re.sub(
        r"(?i)(api[_-]?key|password|secret|token)\s*[=:]\s*[^\s,;]+",
        r"\1=***redacted***",
        message,
    )
    return f"{type(exc).__name__}: {message[:400]}"


def _git_ref() -> str:
    try:
        return git_read.head_ref()
    except git_read.GitUnavailableError:
        return "unknown"


def _nyc_ground_truth() -> dict[str, dict[str, list[str]]]:
    """Row-level ground truth for the fixture, including pre-seeded defects."""
    from evalgate.corpus.generator import generate
    from evalgate.corpus.nyc_preexisting import recover_labels

    frame = generate("corpus-nyc-taxi-50k")
    labels, _ = recover_labels(frame)
    truth: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for label in labels:
        truth[label.defect.value][label.column].append(label.row_id)
    return {
        name: {column: sorted(set(row_ids)) for column, row_ids in columns.items()}
        for name, columns in truth.items()
    }


# ---------------------------------------------------------------------------
# Evaluator registry
# ---------------------------------------------------------------------------

def _registry(baseline_ref: str = "HEAD", context: EvalRunContext | None = None) -> dict[str, callable]:
    """Name -> a zero-argument callable returning one EvalResult.

    Imports are deferred into the lambdas so that a broken or heavy evaluator
    cannot stop the whole harness from starting.

    ``baseline_ref`` is the commit the capability comparison is made against. It
    must come from the stored baseline run rather than defaulting to HEAD: once a
    regression is committed, HEAD *is* the damaged revision, and comparing it with
    itself reports nothing wrong.
    """

    def replay() -> EvalResult:
        from evalgate.gates.gate1_ai_quality import replay_evaluator

        return replay_evaluator.evaluate(
            _nyc_ground_truth(), write_evidence=_WRITE[0], context=context
        )

    def capability() -> EvalResult:
        from evalgate.gates.gate6_governance import capability_regression

        return capability_regression.evaluate(
            write_evidence=_WRITE[0], baseline_ref=baseline_ref
        )

    def make(module_path: str, attr: str = "evaluate"):
        def _call() -> EvalResult:
            import importlib

            module = importlib.import_module(module_path)
            function = getattr(module, attr)
            kwargs = {"write_evidence": _WRITE[0]}
            if "context" in inspect.signature(function).parameters:
                kwargs["context"] = context
            return function(**kwargs)

        return _call

    registry = {
        "workspace_integrity_v1": lambda: workspace_integrity.evaluate(
            write_evidence=_WRITE[0]
        ),
        "capability_regression_v1": capability,
        "contract_conformance_v1": make(
            "evalgate.gates.gate6_governance.contract_conformance"
        ),
        "hitl_integrity_v1": make("evalgate.gates.gate6_governance.hitl_integrity"),
        "governed_enum_conformance_v1": make(
            "evalgate.gates.gate1_ai_quality.governed_enum_conformance"
        ),
        "golden_conformance_v1": make(
            "evalgate.gates.gate1_ai_quality.golden_conformance"
        ),
        "vacuity_probe_v1": make("evalgate.gates.gate1_ai_quality.vacuity_probe"),
        "run_outcome_integrity_v1": make(
            "evalgate.gates.gate1_ai_quality.run_outcome_integrity"
        ),
        "served_path_fidelity_v1": make(
            "evalgate.gates.gate6_governance.served_path_fidelity"
        ),
        "ingest_fidelity_v1": make("evalgate.gates.gate4_input_data.ingest_fidelity"),
        "replay_detection_v1": replay,
        "authz_probe_v1": make("evalgate.gates.gate2_security.authz_probe"),
        "egress_probe_v1": make("evalgate.gates.gate2_security.egress_probe"),
        "secret_scan_v1": make("evalgate.gates.gate2_security.secret_scan"),
        "default_credential_probe_v1": make(
            "evalgate.gates.gate2_security.default_credential_probe"
        ),
        "asgi_behaviour_probe_v1": make(
            "evalgate.gates.gate2_security.asgi_behaviour_probe"
        ),
        "policy_resolution_v1": make("evalgate.gates.gate6_governance.policy_resolution"),
        "config_static_check_v1": make(
            "evalgate.gates.gate5_reliability.config_static_check"
        ),
        "multi_dataset_readiness_v1": make(
            "evalgate.gates.readiness.multi_dataset_readiness"
        ),
    }
    for name, spec in REGISTRY.items():
        if name in registry or spec.runner_kind != "module" or not spec.module:
            continue
        registry[name] = make(spec.module, spec.attribute)
    return registry


#: Mutable holder so the registry lambdas can see the current --dry-run setting.
_WRITE = [True]


def load_profile(mode: str) -> list[str]:
    document = yaml.safe_load(PROFILES.read_text(encoding="utf-8"))
    profiles = document["profiles"]
    if mode not in profiles:
        raise ValueError(f"unknown execution profile: {mode}")
    return [spec.name for spec in SPECS if mode in spec.profiles]


def _declared_but_not_run(selected: list[str]) -> list[EvalResult]:
    """Evaluators the plan defines that this run cannot execute."""
    blocked = [
        ("input_data", "gx_corpus_integrity_v1", EvalStatus.NOT_IMPLEMENTED,
         "requires the great-expectations package"),
        ("input_data", "evidently_drift_v1", EvalStatus.NOT_IMPLEMENTED,
         "requires the evidently package"),
        ("ai_security", "upload_probe_v1", EvalStatus.BLOCKED_BY_SYSTEM_CAPABILITY,
         "no upload endpoint exists, so malicious files cannot be submitted"),
        ("ai_quality", "generalization_evaluator_v1", EvalStatus.BLOCKED_BY_SYSTEM_CAPABILITY,
         "six of seven corpus datasets cannot be ingested; variance is undefined"),
        ("ai_quality", "live_sdih_detection_v1", EvalStatus.BLOCKED_BY_SYSTEM_CAPABILITY,
         "get_dataset_rule_policy raises for every dataset, so the agent cannot be invoked"),
        ("ai_security", "promptfoo_injection_v1", EvalStatus.NOT_EXECUTED,
         "requires npx promptfoo, network access and a paid model call"),
        ("ai_quality", "geval_domain_v1", EvalStatus.NOT_EXECUTED,
         "requires the deepeval package and a paid model call"),
        ("observability", "trace_coverage_v1", EvalStatus.NOT_IMPLEMENTED,
         "planned for phase C; OpenTelemetry deps are commented out in requirements.txt"),
        ("reliability", "k6_load_v1", EvalStatus.NOT_EXECUTED,
         "load testing requires explicit approval and a localhost target"),
        ("business", "steward_behavior_v1", EvalStatus.NOT_MEASURED,
         "fewer than 3 datasets and 20 proposals in the database"),
    ]
    return [
        EvalResult(gate=gate, evaluator=name, status=status, metadata={"reason": reason})
        for gate, name, status, reason in blocked
        if name not in selected
    ]


def resolve_baseline_ref(baseline_run_id: str | None = None) -> tuple[str, str | None]:
    """Return (git ref to compare capabilities against, baseline run id).

    Falling back to HEAD is correct only for the very first run, when there is no
    stored baseline yet. It is recorded in the result either way so a reader can
    tell which of the two situations produced the number.
    """
    if not baseline_run_id:
        return "HEAD", None
    baseline = regression_engine.resolve_baseline(baseline_run_id)
    if not baseline:
        return "HEAD", None
    sha = git_read.ref_sha(str(baseline.get("git_ref") or ""))
    if sha and git_read.ref_exists(sha):
        return sha, baseline.get("run_id")
    return "HEAD", baseline.get("run_id")


def approved_baseline_run_id() -> str | None:
    try:
        document = yaml.safe_load(APPROVED_BASELINE.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    value = document.get("run_id")
    return str(value) if value else None


def collect_results(
    *,
    mode: str = "local",
    write_evidence: bool = True,
    baseline_ref: str = "HEAD",
    context: EvalRunContext | None = None,
) -> tuple[list[EvalResult], EvalResult | None]:
    """Run the profile's evaluators. Returns (results, preflight_result)."""
    _WRITE[0] = write_evidence
    # Memoised git output must not survive into a second run inside one process.
    git_read.clear_cache()
    registry = _registry(baseline_ref, context)
    selected = load_profile(mode)

    results: list[EvalResult] = []
    preflight: EvalResult | None = None
    unknown = validate_profile(selected)
    if unknown:
        results.append(
            EvalResult(
                gate="preflight",
                evaluator="profile_validation_v1",
                status=EvalStatus.NOT_EXECUTED,
                metadata={
                    "reason": "profile references unknown evaluators",
                    "configuration_error": "unknown evaluators: " + ", ".join(unknown),
                },
            )
        )
    for name in selected:
        runner = registry.get(name)
        spec = REGISTRY.get(name)
        mandatory = bool(mode != "local" and spec and name != "steward_behavior_v1")
        missing_artifacts = []
        if spec and spec.required_artifacts:
            available_types = {a.type for a in context.manifest.artifacts} if context else set()
            missing_artifacts = sorted(set(spec.required_artifacts) - available_types)
        if missing_artifacts:
            results.append(EvalResult(
                gate=spec.gate, evaluator=name,
                evaluator_version=spec.version,
                status=(EvalStatus.MISSING_MANDATORY_EVIDENCE if mandatory
                        else EvalStatus.NOT_MEASURED),
                metadata={"reason": "missing manifest artifacts: " + ", ".join(missing_artifacts),
                          "mandatory": mandatory, "required_artifacts": list(spec.required_artifacts)},
            ))
            continue
        missing_dependencies = []
        if spec:
            for dependency in spec.required_dependencies:
                if dependency in {"promptfoo", "k6"}:
                    available = bool(shutil.which(dependency))
                else:
                    available = importlib.util.find_spec(dependency) is not None
                if not available:
                    missing_dependencies.append(dependency)
        if missing_dependencies:
            results.append(
                EvalResult(
                    gate=spec.gate,
                    evaluator=name,
                    evaluator_version=spec.version,
                    status=(EvalStatus.MISSING_MANDATORY_EVIDENCE if mandatory
                            else EvalStatus.NOT_EXECUTED),
                    metadata={
                        "reason": "missing dependencies: " + ", ".join(missing_dependencies),
                        "cost_class": spec.cost_class,
                        "mandatory": mandatory,
                    },
                )
            )
            continue
        if runner is None:
            results.append(
                EvalResult(
                    gate=spec.gate if spec else "unknown",
                    evaluator=name,
                    evaluator_version=spec.version if spec else "unknown",
                    status=EvalStatus.NOT_EXECUTED,
                    metadata={
                        "reason": "registered evaluator has no executable runner",
                        "configuration_error": f"evaluator {name} has no runner",
                    },
                )
            )
            continue
        try:
            result = runner()
        except Exception as exc:  # noqa: BLE001 - an evaluator crash is a reportable fact
            result = EvalResult(
                gate=spec.gate if spec else "unknown",
                evaluator=name,
                evaluator_version=spec.version if spec else "unknown",
                status=EvalStatus.EVALUATOR_ERROR,
                metadata={"reason": f"evaluator raised {_sanitized_error(exc)}",
                          "mandatory": mandatory, "critical": bool(spec and spec.critical)},
            )
        if spec:
            if result.gate != spec.gate or result.evaluator != name:
                result = EvalResult(
                    gate=spec.gate,
                    evaluator=name,
                    evaluator_version=spec.version,
                    status=EvalStatus.EVALUATOR_ERROR,
                    metadata={
                        "reason": "evaluator identity does not match its registry contract",
                        "configuration_error": (
                            f"registry mismatch for {name}: returned "
                            f"{result.gate}/{result.evaluator}"
                        ),
                        "mandatory": mandatory,
                        "critical": spec.critical,
                    },
                )
            result.evaluator_version = spec.version
        result.metadata.setdefault("mandatory", mandatory)
        result.metadata.setdefault("critical", bool(spec and spec.critical))
        if name == "workspace_integrity_v1":
            preflight = result
        results.append(result)

    results.extend(_declared_but_not_run(selected))
    return results, preflight


def stamp(results: list[EvalResult], *, run_id: str, git_ref: str) -> None:
    stamped_at = datetime.now(UTC).isoformat()
    for result in results:
        result.run_id = run_id
        result.git_ref = git_ref
        result.timestamp = stamped_at
        result.sdih_seed = SDIH_SEED


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", default="local", choices=["local", "ci", "nightly", "pre_release"]
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--dry-run", action="store_true",
                        help="run every evaluator but write no evidence, report or history")
    parser.add_argument("--allow-dirty", action="store_true",
                        help="publish a decision even though the tree matches no commit")
    parser.add_argument("--baseline", default=None,
                        help="run_id to compare against; defaults to the newest stored run")
    parser.add_argument("--manifest", default=None,
                        help="exact finalized EvalGate manifest v2 path")
    args = parser.parse_args(argv)

    write = not args.dry_run
    approved_baseline = (
        args.baseline
        or os.getenv("EVALGATE_APPROVED_BASELINE_RUN_ID")
        or approved_baseline_run_id()
    )
    git_ref = _git_ref()
    provenance = None
    provenance_data: dict[str, object] | None = None
    manifest_path = Path(args.manifest) if args.manifest else None
    context = None
    if manifest_path:
        context, provenance = load_context(
            manifest_path, profile=args.mode, expected_git_sha=git_read.ref_sha(git_ref)
        )
        provenance_data = {
            "manifest": str(manifest_path),
            "valid": provenance.valid,
            "reasons": provenance.reasons,
            "run_id": getattr(provenance.manifest, "run_id", None),
            "git_sha": getattr(provenance.manifest, "git_sha", None),
            "dataset_id": getattr(provenance.manifest, "dataset_id", None),
            "dataset_fingerprint": getattr(provenance.manifest, "dataset_fingerprint", None),
            "prompt_hash": getattr(provenance.manifest, "prompt_hash", None),
            "model": (
                provenance.manifest.model.model_dump(mode="json")
                if provenance.manifest is not None and hasattr(provenance.manifest.model, "model_dump")
                else getattr(provenance.manifest, "model", None)
            ),
        }
    elif args.mode != "local":
        provenance_data = {
            "manifest": None,
            "valid": False,
            "reasons": ["non-local profiles require --manifest with a finalized v2 manifest"],
        }
    baseline_ref, baseline_run_id = resolve_baseline_ref(approved_baseline)
    results, preflight = collect_results(
        mode=args.mode, write_evidence=write, baseline_ref=baseline_ref, context=context
    )
    if provenance_data is not None:
        results.append(
            EvalResult(
                gate="preflight",
                evaluator="artifact_provenance_v1",
                status=(
                    EvalStatus.PASS
                    if provenance_data["valid"]
                    else EvalStatus.STALE_EVIDENCE
                ),
                score=100.0 if provenance_data["valid"] else None,
                provenance=provenance_data,
                metadata={"reasons": provenance_data["reasons"]},
            )
        )
        for result in results:
            result.provenance = provenance_data

    regression = (
        regression_engine.evaluate(
            results, baseline_run_id=approved_baseline, write_evidence=write
        )
        if approved_baseline
        else EvalResult(
            gate="governance",
            evaluator="regression_engine_v1",
            status=EvalStatus.NOT_MEASURED,
            metadata={
                "reason": (
                    "no approved known-good baseline is configured; set "
                    "EVALGATE_APPROVED_BASELINE_RUN_ID or approved_baseline.yaml"
                )
            },
        )
    )
    if provenance_data is not None:
        regression.provenance = provenance_data
    results.append(regression)

    run_id = context.run_id if context is not None else f"evalgate-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"
    stamp(results, run_id=run_id, git_ref=context.git_sha if context is not None else git_ref)

    outcome = aggregate(results, profile=args.mode)

    if args.mode != "local" and not manifest_path:
        outcome.decision = Decision.EVALGATE_STALE
        outcome.score = None
        outcome.provisional_score = None
        outcome.override_reason = "non-local profiles require --manifest with a finalized v2 manifest"
    if args.mode != "local" and args.allow_dirty:
        outcome.decision = Decision.EVALGATE_INVALID
        outcome.score = None
        outcome.override_reason = "--allow-dirty is restricted to local diagnostics"

    central_policy = yaml.safe_load(
        (PROJECT_ROOT / "evalgate" / "policies" / "evaluation_policy.yaml").read_text(
            encoding="utf-8"
        )
    )
    configured_budget = (
        central_policy["runtime"]["nightly_budget_usd"]
        if args.mode == "nightly"
        else central_policy["runtime"]["pre_release_budget_usd"]
        if args.mode == "pre_release"
        else 0.0
    )
    budget = float(os.getenv("EVALGATE_BUDGET_USD", configured_budget))
    spent = sum(result.cost.llm_usd for result in results)
    if budget > 0 and spent > budget:
        outcome.decision = Decision.RELEASE_BLOCKED
        outcome.override_reason = (
            f"LLM evaluation cost ${spent:.4f} exceeded ${budget:.2f} budget"
        )

    if provenance_data is not None and not provenance_data["valid"]:
        if outcome.decision != Decision.EVALGATE_INVALID:
            outcome.decision = Decision.EVALGATE_STALE
            outcome.score = None
            outcome.provisional_score = None
            outcome.override_reason = "; ".join(provenance_data["reasons"])

    suppression_resolution = load_suppressions(SUPPRESSIONS)
    apply_suppressions(
        outcome,
        results,
        suppression_resolution,
        current_git_sha=git_read.ref_sha(git_ref),
    )

    dirty = bool(
        preflight and preflight.metrics.get("workspace_dirty")
        and preflight.metrics["workspace_dirty"].raw
    )
    if dirty and outcome.decision != Decision.EVALGATE_INVALID:
        if not args.allow_dirty:
            outcome.decision = Decision.EVALGATE_STALE
            outcome.override_reason = "; ".join(preflight.metadata.get("reasons", []))
        else:
            outcome.override_reason = (
                "--allow-dirty: the tree matches no commit and this verdict is advisory"
            )

    from evalgate.reports.renderer import render_json, render_markdown

    payload = render_json(results, outcome, mode=args.mode)
    if write:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "result.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out_dir / "report.md").write_text(
            render_markdown(results, outcome, mode=args.mode), encoding="utf-8"
        )
        print(f"report  -> {out_dir / 'report.md'}")
        print(f"result  -> {out_dir / 'result.json'}")
        # A stale run must never become the baseline for the next comparison.
        if outcome.decision != Decision.EVALGATE_STALE:
            saved = regression_engine.save_run(payload)
            if saved:
                print(f"history -> {saved.relative_to(PROJECT_ROOT)}")

    if outcome.score is None and outcome.score_withheld_reason:
        print(f"\ndecision: {outcome.decision}   score: WITHHELD")
        print(f"  {outcome.score_withheld_reason}")
        print(f"  the number it would have shown is {outcome.provisional_score}")
    else:
        print(f"\ndecision: {outcome.decision}   score: {outcome.score}")
    print(
        f"baseline: {baseline_run_id or 'none stored'}  "
        f"(capabilities compared against {baseline_ref})"
    )
    if outcome.override_reason:
        print(f"note: {outcome.override_reason}")
    if outcome.metric_collisions:
        print(f"WARNING metric name collisions: {outcome.metric_collisions}")
    failed = [h for h in outcome.hard_gates if h.status == "FAIL"]
    if failed:
        print(f"hard gates failed ({len(failed)}): {', '.join(h.id for h in failed)}")
    return outcome.exit_code


if __name__ == "__main__":
    sys.exit(main())
