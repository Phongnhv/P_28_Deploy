"""EvalGate self-tests.

The scoring machinery has to be trustworthy before its numbers mean anything, so
the aggregator, the normalizers and SDIH's determinism are tested directly rather
than through the CLI.
"""

from __future__ import annotations

import pandas as pd
import pytest

from evalgate.aggregator import (
    Decision,
    aggregate,
    collapse_per_dataset,
    evaluate_hard_gates,
    re_normalize_weights,
)
from evalgate.corpus.generator import ARCHETYPES, generate
from evalgate.normalizers import normalizers as norm
from evalgate.schemas.eval_result import (
    DatasetBreakdown,
    EvalResult,
    EvalStatus,
    Finding,
    MetricValue,
    Severity,
)
from evalgate.sdih.injector import build_plan, inject
from evalgate.sdih.profiler import profile_dataframe
from evalgate.sdih.verifier import verify

SEED = 20260819
SMALL = 400


# ---------------------------------------------------------------------------
# Normalizers
# ---------------------------------------------------------------------------

def test_ratio_and_inverse_are_complementary():
    assert norm.ratio(0.0) == 0.0
    assert norm.ratio(1.0) == 100.0
    assert norm.inverse_ratio(0.0) == 100.0
    assert norm.inverse_ratio(1.0) == 0.0


def test_normalizers_clamp_out_of_band_input():
    assert norm.ratio(1.5) == 100.0
    assert norm.ratio(-0.2) == 0.0
    assert norm.variance(10.0) == 0.0


def test_zero_tolerance_does_not_interpolate():
    assert norm.zero_tolerance(0) == 100.0
    assert norm.zero_tolerance(1) == 0.0
    assert norm.zero_tolerance(50) == 0.0


def test_none_propagates_rather_than_scoring_zero():
    for fn in (norm.ratio, norm.inverse_ratio, norm.variance, norm.boolean,
               norm.zero_tolerance, norm.latency_band, norm.psi_band, norm.time_band):
        assert fn(None) is None


def test_latency_and_psi_bands():
    assert norm.latency_band(900) == 100.0
    assert norm.latency_band(2999) == 70.0
    assert norm.latency_band(99999) == 0.0
    assert norm.psi_band(0.05) == 100.0
    assert norm.psi_band(0.2) == 60.0
    assert norm.psi_band(0.9) == 0.0


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------

def test_re_normalize_scales_remaining_weights_to_one():
    weights = {"a": 0.5, "b": 0.3, "c": 0.2}
    result = re_normalize_weights(weights, {"c"})
    assert "c" not in result
    assert pytest.approx(sum(result.values())) == 1.0
    assert pytest.approx(result["a"], abs=1e-6) == 0.625


def test_collapse_uses_min_for_hard_gates_and_p25_for_scores():
    values = [0.0, 80.0, 90.0, 95.0]
    assert collapse_per_dataset(values, is_hard_gate_metric=True) == 0.0
    # P25 must stay well below the mean so one broken dataset still shows.
    p25 = collapse_per_dataset(values, is_hard_gate_metric=False)
    assert p25 < sum(values) / len(values)


def _result(gate: str, score: float, **metrics) -> EvalResult:
    return EvalResult(
        gate=gate, evaluator=f"{gate}_test", status=EvalStatus.PASS, score=score,
        metrics={k: MetricValue(raw=v, unit="count") for k, v in metrics.items()},
    )


def test_hard_gate_failure_blocks_release_despite_a_perfect_score():
    results = [
        _result("ai_quality", 100.0, min_recall_per_class=0.0),
        _result("ai_security", 100.0),
    ]
    outcome = aggregate(results)
    assert outcome.decision == Decision.RELEASE_BLOCKED
    assert outcome.exit_code == 3
    assert any(h.id == "HG-A1" and h.status == "FAIL" for h in outcome.hard_gates)


def test_clean_run_passes():
    results = [
        _result("ai_quality", 95.0, min_recall_per_class=1.0, schema_violation_rate=0.0),
        _result("ai_security", 95.0, unauthenticated_mutating_endpoints=0,
                cross_tenant_violations=0, raw_or_pii_egress_violations=0,
                malicious_upload_accepted_count=0, indirect_injection_pass_rate=1.0,
                secret_findings=0, default_credentials_active=0),
        _result("input_data", 95.0, row_fidelity=100.0),
        _result("governance", 95.0, policy_resolution_success_rate=100.0, hitl_integrity=100.0),
    ]
    outcome = aggregate(results)
    assert outcome.decision == Decision.PASS
    assert outcome.exit_code == 0


def test_blocking_finding_alone_blocks_release():
    result = _result("ai_security", 100.0)
    result.critical_findings = [
        Finding(id="HG-S3", severity=Severity.CRITICAL, title="t", detail="d",
                blocks_release=True)
    ]
    assert aggregate([result]).decision == Decision.RELEASE_BLOCKED


def test_unmeasured_gate_is_excluded_not_scored_zero():
    results = [
        _result("ai_quality", 90.0),
        EvalResult(gate="business", evaluator="b", status=EvalStatus.NOT_MEASURED),
    ]
    outcome = aggregate(results)
    assert "business" in outcome.excluded_gates
    assert "business" not in outcome.effective_weights
    assert outcome.gate_scores["ai_quality"] == 90.0


def test_blocked_by_system_capability_dataset_does_not_drag_the_score():
    result = EvalResult(
        gate="ai_quality", evaluator="e", status=EvalStatus.FAIL,
        per_dataset_breakdown=[
            DatasetBreakdown(dataset_id="a", status=EvalStatus.WARN, score=80.0),
            DatasetBreakdown(
                dataset_id="b", status=EvalStatus.BLOCKED_BY_SYSTEM_CAPABILITY,
                score=None, reason="no upload endpoint",
            ),
        ],
    )
    outcome = aggregate([result])
    assert outcome.gate_scores["ai_quality"] == 80.0


def test_hard_gate_absent_metric_is_not_evaluated_rather_than_passed():
    outcomes = evaluate_hard_gates([_result("ai_quality", 50.0)])
    assert all(o.status in {"NOT_EVALUATED", "PASS", "FAIL"} for o in outcomes)
    assert any(o.status == "NOT_EVALUATED" for o in outcomes)


# ---------------------------------------------------------------------------
# SDIH
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dataset_id", sorted(ARCHETYPES))
def test_sdih_is_schema_agnostic(dataset_id):
    archetype = ARCHETYPES[dataset_id]
    try:
        frame = generate(dataset_id, rows=min(archetype.rows, SMALL))
    except FileNotFoundError:
        pytest.skip(f"{dataset_id} fixture not present")

    profile = profile_dataframe(frame)
    plan = build_plan(frame, dataset_id=dataset_id, seed=SEED, n_per_class=5, profile=profile)
    dirty, store = inject(frame, plan, id_column=archetype.id_column)

    assert len(store.injected_classes) >= 3, (
        f"{dataset_id} supports only {store.injected_classes}"
    )
    # A class with no eligible column must be declared, never silently scored 0.
    assert set(store.injected_classes) & set(store.not_applicable_classes) == set()

    domains = {
        name: set(column["domain"])
        for name, column in profile["columns"].items()
        if column.get("domain")
    }
    report = verify(
        dirty, store, id_column=archetype.id_column, original_domains=domains,
        ordered_pairs=profile["ordered_pairs"],
    )
    assert report.passed, f"{dataset_id}: {report.failures[:3]}"


def test_sdih_labels_are_reproducible():
    frame = generate("corpus-synth-retail", rows=SMALL)
    stores = []
    for _ in range(2):
        plan = build_plan(frame, dataset_id="corpus-synth-retail", seed=SEED, n_per_class=5)
        _, store = inject(frame, plan, id_column="order_id")
        stores.append(store)
    assert stores[0].fingerprint() == stores[1].fingerprint()


def test_sdih_different_seed_produces_different_labels():
    frame = generate("corpus-synth-retail", rows=SMALL)
    first = inject(
        frame, build_plan(frame, dataset_id="d", seed=1, n_per_class=5), id_column="order_id"
    )[1]
    second = inject(
        frame, build_plan(frame, dataset_id="d", seed=2, n_per_class=5), id_column="order_id"
    )[1]
    assert first.fingerprint() != second.fingerprint()


def test_injected_positions_do_not_overlap_between_classes():
    frame = generate("corpus-synth-retail", rows=SMALL)
    plan = build_plan(frame, dataset_id="d", seed=SEED, n_per_class=5)
    _, store = inject(frame, plan, id_column="order_id")
    seen: dict[int, str] = {}
    for label in store.labels:
        if label.row_pos in seen:
            assert seen[label.row_pos] == label.defect.value, (
                f"row {label.row_pos} carries two different defect classes"
            )
        seen[label.row_pos] = label.defect.value


def test_verifier_rejects_a_tampered_label_set():
    frame = generate("corpus-synth-retail", rows=SMALL)
    plan = build_plan(frame, dataset_id="d", seed=SEED, n_per_class=5)
    dirty, store = inject(frame, plan, id_column="order_id")
    # Undo one injected defect without touching its label.
    missing = next(
        label for label in store.labels if label.defect.value == "MISSING_VALUE"
    )
    # Restore a real value of the column's own dtype so only the label is now wrong.
    original = frame.iloc[missing.row_pos, frame.columns.get_loc(missing.column)]
    dirty.iloc[missing.row_pos, dirty.columns.get_loc(missing.column)] = original
    report = verify(dirty, store, id_column="order_id")
    assert not report.passed
    assert report.failures


def test_bool_column_does_not_break_profiling():
    frame = pd.DataFrame({"flag": [True, False, True], "n": [1, 2, 3]})
    profile = profile_dataframe(frame)
    assert profile["columns"]["flag"]["is_bool"] is True
    assert profile["columns"]["flag"]["is_numeric"] is False


def test_preexisting_labels_are_kept_and_marked():
    from evalgate.sdih.label_store import CellLabel
    from evalgate.sdih.defect_taxonomy import DefectClass

    frame = generate("corpus-synth-retail", rows=SMALL)
    seeded = [
        CellLabel("ord-000001", "unit_price", DefectClass.SIGN_FLIP,
                  origin="preexisting", row_pos=1)
    ]
    plan = build_plan(frame, dataset_id="d", seed=SEED, n_per_class=5)
    _, store = inject(frame, plan, id_column="order_id", preexisting_labels=seeded)
    assert any(label.origin == "preexisting" for label in store.labels)
    assert store.notes
