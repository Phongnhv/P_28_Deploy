"""Regression tests for the P0 provenance and fail-closed invariants."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pandas as pd
import pytest

from evalgate import product_run
from evalgate import run as evalgate_run
from evalgate.aggregator import Decision, aggregate
from evalgate.core.artifact_provenance import load_context, verify_manifest
from evalgate.gates.gate1_ai_quality import golden_conformance
from evalgate.schemas.artifact_manifest import ArtifactManifestV2, ArtifactRecord, ModelIdentity
from evalgate.schemas.eval_result import EvalResult, EvalStatus


def _v2(tmp_path, *, run_id: str = "run-one", dataset_id: str = "dataset-one"):
    artifact = tmp_path / "proposals.json"
    artifact.write_text(json.dumps({"run_id": run_id, "dataset_id": dataset_id,
                                    "proposed_rules": []}), encoding="utf-8")
    record = ArtifactRecord(
        name="proposals", type="proposals", relative_path="proposals.json",
        sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(), media_type="application/json",
        producer="test", run_id=run_id, dataset_id=dataset_id, created_at=datetime.now(UTC),
    )
    manifest = ArtifactManifestV2(
        schema_version="2.0", finalized=True, run_id=run_id, git_sha="a" * 40,
        workspace_dirty=False, created_at=datetime.now(UTC), dataset_id=dataset_id,
        dataset_fingerprint="b" * 64, schema_fingerprint="c" * 64,
        model=ModelIdentity(provider="evalgate", name="fake", mode="deterministic-test"),
        prompt_hash="d" * 64, policy_hash="e" * 64, config_hash="f" * 64,
        workflow="served", product_version="1", artifacts=(record,),
    )
    target = tmp_path / "manifest.json"
    target.write_text(manifest.model_dump_json(), encoding="utf-8")
    return target, artifact


def test_v2_context_is_bound_to_exact_bundle(tmp_path):
    target, artifact = _v2(tmp_path)
    context, verification = load_context(target, profile="ci", expected_git_sha="a" * 40)
    assert verification.valid and context is not None
    assert context.path_for("proposals") == artifact.resolve()


def test_nightly_rejects_deterministic_test_model(tmp_path):
    target, _ = _v2(tmp_path)
    context, verification = load_context(target, profile="nightly", expected_git_sha="a" * 40)
    assert context is None and not verification.valid
    assert any("requires a live model" in reason for reason in verification.reasons)


def test_failed_product_step_never_finalizes_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(product_run, "generate", lambda *_args, **_kwargs: pd.DataFrame({"id": [1]}))
    monkeypatch.setattr(product_run, "_served_run", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("product stage failed")
    ))
    with pytest.raises(RuntimeError, match="product stage failed"):
        product_run.create_bundle(tmp_path, profile="local")
    assert list(tmp_path.rglob("manifest.json")) == []


def test_finalized_artifact_tampering_is_stale(tmp_path):
    target, artifact = _v2(tmp_path)
    artifact.write_text("{}", encoding="utf-8")
    result = verify_manifest(target, require_v2=True)
    assert not result.valid
    assert any("checksum mismatch" in reason for reason in result.reasons)


def test_swapped_artifact_lineage_is_rejected_even_with_updated_checksum(tmp_path):
    target, artifact = _v2(tmp_path)
    artifact.write_text(json.dumps({"run_id": "another-run", "dataset_id": "dataset-one"}), encoding="utf-8")
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["artifacts"][0]["sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    target.write_text(json.dumps(payload), encoding="utf-8")
    result = verify_manifest(target, require_v2=True)
    assert not result.valid
    assert any("run_id mismatch" in reason for reason in result.reasons)


def test_global_output_cannot_influence_context_evaluator(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    target, _ = _v2(bundle)
    context, _ = load_context(target, profile="ci", expected_git_sha="a" * 40)
    stale = tmp_path / "output" / "rule_proposer"
    stale.mkdir(parents=True)
    (stale / "perfect.json").write_text(
        json.dumps({"proposed_rules": [{"rule_type": "NOT_NULL", "column": "vendor_id"}]}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    assert golden_conformance.load_proposals(context) == []


@pytest.mark.parametrize("relative", ["../proposal.json", "C:/proposal.json", "/proposal.json"])
def test_manifest_rejects_escaping_paths(tmp_path, relative):
    target, _ = _v2(tmp_path)
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["artifacts"][0]["relative_path"] = relative
    target.write_text(json.dumps(payload), encoding="utf-8")
    assert not verify_manifest(target, require_v2=True).valid


def test_manifest_rejects_duplicate_artifact_name(tmp_path):
    target, _ = _v2(tmp_path)
    payload = json.loads(target.read_text(encoding="utf-8"))
    duplicate = dict(payload["artifacts"][0])
    duplicate["relative_path"] = "other.json"
    payload["artifacts"].append(duplicate)
    target.write_text(json.dumps(payload), encoding="utf-8")
    assert not verify_manifest(target, require_v2=True).valid


def test_requested_revision_prompt_model_and_dataset_must_match(tmp_path):
    target, _ = _v2(tmp_path)
    result = verify_manifest(
        target, expected_git_sha="9" * 40, expected_dataset_fingerprint="9" * 64,
        expected_prompt_hash="9" * 64, expected_model=("other", "model", "live"), require_v2=True,
    )
    assert not result.valid
    assert len(result.reasons) == 4


def test_v1_is_diagnostic_only_for_ci(tmp_path):
    artifact = tmp_path / "result.json"
    artifact.write_text("{}", encoding="utf-8")
    payload = {
        "schema_version": "1.0", "run_id": "historical", "git_sha": "a" * 40,
        "workspace_dirty": False, "created_at": datetime.now(UTC).isoformat(),
        "dataset_id": "old", "dataset_fingerprint": "b" * 64, "workflow": "offline",
        "provider": "none", "model": "old", "prompt_hash": "c" * 64,
        "policy_hash": "d" * 64, "config_hash": "e" * 64,
        "artifacts": [{"path": "result.json", "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}],
    }
    target = tmp_path / "manifest.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    assert verify_manifest(target).valid
    assert not verify_manifest(target, require_v2=True).valid


def test_mandatory_evaluator_error_is_counted_and_blocks_release():
    result = EvalResult(gate="reliability", evaluator="critical-runtime", status=EvalStatus.EVALUATOR_ERROR,
                        metadata={"mandatory": True, "critical": True})
    outcome = aggregate([result], profile="ci")
    assert outcome.decision == Decision.RELEASE_BLOCKED
    assert outcome.mandatory_evidence_coverage == 0.0
    assert outcome.gate_verdicts["reliability"] == "NOT_MEASURED"


def test_orchestrator_keeps_registry_gate_when_evaluator_crashes(monkeypatch):
    def crash():
        raise RuntimeError("sensitive implementation detail")

    monkeypatch.setattr(evalgate_run, "load_profile", lambda _mode: ["contract_conformance_v1"])
    monkeypatch.setattr(evalgate_run, "_registry", lambda *_args, **_kwargs: {
        "contract_conformance_v1": crash,
    })
    results, _ = evalgate_run.collect_results(mode="ci", write_evidence=False)
    result = next(item for item in results if item.evaluator == "contract_conformance_v1")
    assert result.gate == "governance"
    assert result.evaluator_version == "1.0.0"
    assert result.status == EvalStatus.EVALUATOR_ERROR
    assert result.metadata["mandatory"] is True


def test_required_artifact_is_enforced_before_evaluator_call(tmp_path, monkeypatch):
    target, _ = _v2(tmp_path)
    context, _ = load_context(target, profile="ci", expected_git_sha="a" * 40)
    called = False

    def should_not_run():
        nonlocal called
        called = True

    monkeypatch.setattr(evalgate_run, "load_profile", lambda _mode: ["vacuity_probe_v1"])
    monkeypatch.setattr(evalgate_run, "_registry", lambda *_args, **_kwargs: {
        "vacuity_probe_v1": should_not_run,
    })
    results, _ = evalgate_run.collect_results(
        mode="ci", write_evidence=False, context=context,
    )
    result = next(item for item in results if item.evaluator == "vacuity_probe_v1")
    assert not called
    assert result.gate == "ai_quality"
    assert result.status == EvalStatus.MISSING_MANDATORY_EVIDENCE
    assert "input-dataset" in result.metadata["reason"]


def test_registry_identity_mismatch_invalidates_verdict(monkeypatch):
    wrong = EvalResult(gate="ai_quality", evaluator="wrong-name", status=EvalStatus.PASS, score=100)
    monkeypatch.setattr(evalgate_run, "load_profile", lambda _mode: ["contract_conformance_v1"])
    monkeypatch.setattr(evalgate_run, "_registry", lambda *_args, **_kwargs: {
        "contract_conformance_v1": lambda: wrong,
    })
    results, _ = evalgate_run.collect_results(mode="ci", write_evidence=False)
    result = next(item for item in results if item.evaluator == "contract_conformance_v1")
    assert result.gate == "governance"
    assert result.status == EvalStatus.EVALUATOR_ERROR
    assert aggregate(results, profile="ci").decision == Decision.EVALGATE_INVALID


def test_high_quality_score_cannot_override_hard_failure():
    quality = [
        EvalResult(gate=gate, evaluator=f"{gate}-quality", status=EvalStatus.PASS, score=100)
        for gate in ("ai_quality", "ai_security", "input_data", "governance")
    ]
    failure = EvalResult(gate="reliability", evaluator="runtime", status=EvalStatus.FAIL, score=100,
                         metadata={"mandatory": True, "critical": True})
    outcome = aggregate([*quality, failure], profile="local")
    # Reliability is deliberately outside P0's quality-score weights.
    assert outcome.score == 100
    assert outcome.decision == Decision.RELEASE_BLOCKED
