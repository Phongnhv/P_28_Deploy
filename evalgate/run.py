"""EvalGate orchestrator.

    python -m evalgate.run --mode local
    python -m evalgate.run --mode ci --out evalgate/reports

Exit codes are the CI contract: 0 PASS, 1 WARNING, 2 FAIL, 3 RELEASE_BLOCKED.

Only evaluators that need neither a network call nor an unavailable dependency are
registered today; the rest are declared so the report shows them as explicitly
not-run rather than silently absent.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from evalgate.aggregator import aggregate
from evalgate.schemas.eval_result import EvalResult, EvalStatus

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = PROJECT_ROOT / "evalgate" / "reports"

SDIH_SEED = 20260819


def _git_ref() -> str:
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
        return f"{branch}@{sha}"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _nyc_ground_truth() -> dict[str, dict[str, int]]:
    """Ground truth for the shipped fixture, including its pre-seeded defects."""
    from evalgate.corpus.generator import generate
    from evalgate.corpus.nyc_preexisting import recover_labels

    frame = generate("corpus-nyc-taxi-50k")
    labels, _ = recover_labels(frame)
    truth: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for label in labels:
        truth[label.defect.value][label.column] += 1
    return {name: dict(columns) for name, columns in truth.items()}


def _declared_but_not_run() -> list[EvalResult]:
    """Evaluators the plan defines that this run cannot execute."""
    blocked = [
        ("input_data", "ingest_fidelity_v1", EvalStatus.BLOCKED_BY_SYSTEM_CAPABILITY,
         "no upload endpoint exists, so ingest fidelity cannot be exercised"),
        ("ai_security", "upload_probe_v1", EvalStatus.BLOCKED_BY_SYSTEM_CAPABILITY,
         "no upload endpoint exists, so malicious files cannot be submitted"),
        ("ai_quality", "generalization_evaluator_v1", EvalStatus.BLOCKED_BY_SYSTEM_CAPABILITY,
         "six of seven corpus datasets cannot be ingested; variance is undefined"),
        ("ai_security", "promptfoo_injection_v1", EvalStatus.NOT_EXECUTED,
         "requires npx promptfoo, network access and a paid model call"),
        ("ai_quality", "geval_domain_v1", EvalStatus.NOT_EXECUTED,
         "requires the deepeval package and a paid model call"),
        ("input_data", "gx_suite_builder_v1", EvalStatus.NOT_IMPLEMENTED,
         "requires the great-expectations package"),
        ("input_data", "evidently_drift_v1", EvalStatus.NOT_IMPLEMENTED,
         "requires the evidently package"),
        ("observability", "trace_coverage_v1", EvalStatus.NOT_IMPLEMENTED,
         "OpenTelemetry deps are commented out and instrumentation is swallowed by except: pass"),
        ("reliability", "k6_load_v1", EvalStatus.NOT_EXECUTED,
         "load testing requires explicit approval and a localhost target"),
        ("business", "steward_behavior_v1", EvalStatus.NOT_MEASURED,
         "fewer than 3 datasets and 20 proposals in the database"),
        ("governance", "hitl_integrity_v1", EvalStatus.NOT_MEASURED,
         "the legacy branch writes no audit events, so integrity cannot be computed"),
    ]
    return [
        EvalResult(gate=gate, evaluator=name, status=status, metadata={"reason": reason})
        for gate, name, status, reason in blocked
    ]


def collect_results(*, write_evidence: bool = True) -> list[EvalResult]:
    from evalgate.gates.gate1_ai_quality import replay_evaluator
    from evalgate.gates.gate2_security import authz_probe, egress_probe, secret_scan
    from evalgate.gates.gate5_reliability import config_static_check
    from evalgate.gates.gate6_governance import policy_resolution
    from evalgate.gates.readiness import multi_dataset_readiness

    results = [
        replay_evaluator.evaluate(_nyc_ground_truth(), write_evidence=write_evidence),
        authz_probe.evaluate(write_evidence=write_evidence),
        egress_probe.evaluate(write_evidence=write_evidence),
        secret_scan.evaluate(write_evidence=write_evidence),
        policy_resolution.evaluate(write_evidence=write_evidence),
        config_static_check.evaluate(write_evidence=write_evidence),
        multi_dataset_readiness.evaluate(write_evidence=write_evidence),
    ]
    results.extend(_declared_but_not_run())

    run_id = f"evalgate-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"
    git_ref = _git_ref()
    stamp = datetime.now(UTC).isoformat()
    for result in results:
        result.run_id = run_id
        result.git_ref = git_ref
        result.timestamp = stamp
        result.sdih_seed = SDIH_SEED
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", default="local", choices=["local", "ci", "pre_release"])
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--dry-run", action="store_true",
                        help="run every evaluator but write no evidence or report")
    args = parser.parse_args(argv)

    results = collect_results(write_evidence=not args.dry_run)
    outcome = aggregate(results)

    from evalgate.reports.renderer import render_json, render_markdown

    if not args.dry_run:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "result.json").write_text(
            json.dumps(render_json(results, outcome, mode=args.mode), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (out_dir / "report.md").write_text(
            render_markdown(results, outcome, mode=args.mode), encoding="utf-8"
        )
        print(f"report  -> {out_dir / 'report.md'}")
        print(f"result  -> {out_dir / 'result.json'}")

    print(f"\ndecision: {outcome.decision}   score: {outcome.score}")
    failed = [h for h in outcome.hard_gates if h.status == "FAIL"]
    if failed:
        print(f"hard gates failed ({len(failed)}): {', '.join(h.id for h in failed)}")
    return outcome.exit_code


if __name__ == "__main__":
    sys.exit(main())
