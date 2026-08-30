"""HG-G1: can the system resolve a rule policy for a dataset at all?

This is the gate that would have caught commit ``ac4b663``, which deleted
``src/resources/rule_policies.json`` along with the demo manifest and fixture CSV.
The consequence is larger than "unknown datasets are unsupported": the policy
document is loaded before the per-dataset lookup, so the loader raises for
*every* dataset, including the one the product ships with.
"""

from __future__ import annotations

import json
from pathlib import Path

from evalgate.normalizers import normalizers as norm
from evalgate.schemas.eval_result import (
    DatasetBreakdown,
    EvalResult,
    EvalStatus,
    Evidence,
    Finding,
    MetricValue,
    Severity,
    Threshold,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = PROJECT_ROOT / "evalgate" / "evidence" / "gate6"

GATE = "governance"
EVALUATOR = "policy_resolution_v1"

#: Assets the product needs in order to serve any dataset at all.
REQUIRED_ASSETS = {
    "rule_policy_document": "src/resources/rule_policies.json",
    "demo_manifest": "src/resources/manifest.json",
    "demo_fixture": "src/resources/nyc_yellow_demo.csv",
}

PROBE_DATASETS = [
    "dataset-nyc-yellow-taxi-50k",  # the dataset the product ships with
    "corpus-synth-retail",
    "corpus-synth-clinical",
    "corpus-synth-hr",
    "corpus-synth-iot",
    "corpus-synth-wide",
    "corpus-synth-tiny",
]


def _probe(dataset_id: str) -> tuple[str, str]:
    """Return (outcome, detail) for one dataset without importing at module load."""
    try:
        from src.services.dashboard_agent_workflow import get_dataset_rule_policy
    except Exception as exc:  # noqa: BLE001 - import failure is itself the finding
        return "IMPORT_ERROR", f"{type(exc).__name__}: {exc}"
    try:
        policy = get_dataset_rule_policy(dataset_id)
    except Exception as exc:  # noqa: BLE001 - the raise is the finding
        return "RAISES", f"{type(exc).__name__}: {exc}"
    if policy is None:
        return "NONE", "resolver returned None (no default policy for unknown datasets)"
    return "RESOLVED", "policy resolved"


def evaluate(*, write_evidence: bool = True) -> EvalResult:
    asset_state = {
        name: (PROJECT_ROOT / path).exists() for name, path in REQUIRED_ASSETS.items()
    }
    assets_present = sum(asset_state.values())
    asset_presence_rate = assets_present / len(REQUIRED_ASSETS) * 100.0

    outcomes = {ds: _probe(ds) for ds in PROBE_DATASETS}
    resolved = sum(1 for outcome, _ in outcomes.values() if outcome == "RESOLVED")

    # Success is "the resolver answered", not "the dataset had a hand-written entry".
    #
    # The two were the same thing while _load_rule_policy_document raised on a missing
    # file: no entry meant no answer. They stopped being the same on 2026-08-22, when
    # the loader was changed to treat an absent policy file as the optional override
    # it always was -- every caller already handled a None policy, and a dataset a user
    # uploads will never have a hand-written entry.
    #
    # Counting entries after that change penalises the product for the exact thing it
    # is supposed to support. A dataset that resolves cleanly to None is working; only
    # a resolver that raises, or one that cannot be imported, is a governance failure.
    #
    # The entry count is kept below as context, because a shipped dataset losing its
    # policy is still worth seeing -- it is just not a release blocker.
    failed = sum(1 for outcome, _ in outcomes.values() if outcome in {"RAISES", "IMPORT_ERROR"})
    success_rate = (len(PROBE_DATASETS) - failed) / len(PROBE_DATASETS) * 100.0

    breakdown = [
        DatasetBreakdown(
            dataset_id=dataset_id,
            status=(
                EvalStatus.FAIL
                if outcome in {"RAISES", "IMPORT_ERROR"}
                else EvalStatus.PASS
            ),
            score=100.0 if outcome == "RESOLVED" else 0.0,
            reason=f"{outcome}: {detail}",
        )
        for dataset_id, (outcome, detail) in outcomes.items()
    ]

    evidence: list[Evidence] = []
    if write_evidence:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        target = EVIDENCE_DIR / "policy_resolution_matrix.json"
        target.write_text(
            json.dumps(
                {
                    "required_assets": REQUIRED_ASSETS,
                    "asset_present": asset_state,
                    "probe_results": {k: {"outcome": v[0], "detail": v[1]} for k, v in outcomes.items()},
                    "policy_resolution_success_rate": success_rate,
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        evidence.append(Evidence(type="file", path=str(target.relative_to(PROJECT_ROOT))))

    findings = []
    missing = [name for name, present in asset_state.items() if not present]
    if success_rate < 100.0 or missing:
        shipped_outcome = outcomes.get("dataset-nyc-yellow-taxi-50k", ("?", ""))
        findings.append(
            Finding(
                id="HG-G1",
                severity=Severity.CRITICAL,
                title="Rule policy resolution fails or a governance asset is missing",
                detail=(
                    f"{failed}/{len(PROBE_DATASETS)} dataset(s) made the resolver raise. "
                    f"{resolved}/{len(PROBE_DATASETS)} carry a hand-written policy entry "
                    f"(context, not a failure). Missing governance assets: "
                    f"{missing or 'none'}. The dataset the product ships with returns "
                    f"{shipped_outcome[0]}: {shipped_outcome[1]}"
                ),
                root_cause_hint=(
                    "a raising resolver takes every caller down at once; a missing asset "
                    "leaves the shipped dataset without the policy it was profiled against. "
                    "A dataset resolving to None is not a failure -- policy is an optional "
                    "per-dataset override"
                ),
                evidence_ref="evalgate/evidence/gate6/policy_resolution_matrix.json",
                blocks_release=True,
            )
        )

    return EvalResult(
        gate=GATE,
        evaluator=EVALUATOR,
        status=EvalStatus.FAIL if findings else EvalStatus.PASS,
        score=norm.ratio(success_rate / 100.0),
        metrics={
            "datasets_with_policy_entry": MetricValue(
                raw=resolved, unit="count", normalized=None,
                note=(
                    f"{resolved} of {len(PROBE_DATASETS)} probed datasets have a "
                    "hand-written entry. Context only: an absent entry is a supported "
                    "state, not a defect"
                ),
            ),
            "policy_resolution_success_rate": MetricValue(
                raw=success_rate, unit="ratio", normalized=success_rate
            ),
            "required_asset_presence": MetricValue(
                raw=asset_presence_rate, unit="ratio", normalized=asset_presence_rate
            ),
        },
        per_dataset_breakdown=breakdown,
        thresholds={
            "policy_resolution_success_rate": Threshold(**{"pass": 100.0, "warn": 100.0}),
            "required_asset_presence": Threshold(**{"pass": 100.0, "warn": 100.0}),
        },
        evidence=evidence,
        critical_findings=findings,
        metadata={"missing_assets": missing},
    )
