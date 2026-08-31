"""Acceptance tests for the production-grade EvalGate trust chain."""

from __future__ import annotations

import hashlib
import json
from datetime import date

from evalgate.aggregator import Decision, aggregate
from evalgate.core.artifact_provenance import verify_manifest
from evalgate.core.evaluator_registry import validate_profile
from evalgate.core.suppression_policy import load_suppressions
from evalgate.gates.gate1_ai_quality.replay_evaluator import score_run
from evalgate.gates.gate2_security import secret_scan
from evalgate.run import load_profile
from evalgate.schemas.eval_result import EvalResult, EvalStatus, MetricValue


def _manifest(tmp_path, *, sha: str = "a" * 40, dirty: bool = False):
    artifact = tmp_path / "result.json"
    artifact.write_text("{}", encoding="utf-8")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    payload = {
        "schema_version": "1.0",
        "run_id": "run-1",
        "git_sha": sha,
        "workspace_dirty": dirty,
        "created_at": "2026-08-25T00:00:00Z",
        "dataset_id": "fixture",
        "dataset_fingerprint": "d" * 64,
        "workflow": "offline",
        "provider": "none",
        "model": "deterministic",
        "prompt_hash": "b" * 64,
        "policy_hash": "c" * 64,
        "config_hash": "e" * 64,
        "artifacts": [{"path": "result.json", "sha256": digest}],
    }
    target = tmp_path / "manifest.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target, artifact


def test_manifest_rejects_wrong_revision(tmp_path):
    target, _ = _manifest(tmp_path)
    result = verify_manifest(target, expected_git_sha="b" * 40)
    assert not result.valid
    assert any("does not match" in reason for reason in result.reasons)


def test_manifest_rejects_checksum_drift(tmp_path):
    target, artifact = _manifest(tmp_path)
    artifact.write_text("changed", encoding="utf-8")
    result = verify_manifest(target, expected_git_sha="a" * 40)
    assert not result.valid
    assert any("checksum mismatch" in reason for reason in result.reasons)


def test_manifest_rejects_dirty_ci_evidence(tmp_path):
    target, _ = _manifest(tmp_path, dirty=True)
    assert not verify_manifest(target, require_clean=True).valid


def test_unknown_profile_evaluator_is_detected():
    assert validate_profile(["secret_scan_v1", "does_not_exist_v1"]) == [
        "does_not_exist_v1"
    ]


def test_unknown_evaluator_makes_the_verdict_invalid():
    result = EvalResult(
        gate="preflight",
        evaluator="profile_validation_v1",
        status=EvalStatus.NOT_EXECUTED,
        metadata={"configuration_error": "unknown evaluators: ghost_v1"},
    )
    outcome = aggregate([result])
    assert outcome.decision == Decision.EVALGATE_INVALID
    assert outcome.exit_code == 6


def test_registry_is_the_single_profile_membership_source():
    nightly = load_profile("nightly")
    pre_release = load_profile("pre_release")
    assert validate_profile(nightly) == []
    assert "promptfoo_injection_v1" in nightly
    assert "k6_load_v1" not in nightly
    assert "k6_load_v1" in pre_release


def test_secret_scanner_does_not_exempt_example_files(tmp_path, monkeypatch):
    sample = tmp_path / ".env.local.example"
    token = "sk-" + "production-shaped-value-1234567890"
    sample.write_text("OPENAI_API_KEY=" + token, encoding="utf-8")
    monkeypatch.setattr(secret_scan, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(secret_scan, "tracked_files", lambda: [sample])
    result = secret_scan.evaluate(write_evidence=False)
    assert result.status == EvalStatus.FAIL
    assert result.metrics["secret_findings"].raw == 1
    assert token not in result.critical_findings[0].detail


def test_secret_scanner_allows_explicit_placeholder(tmp_path, monkeypatch):
    sample = tmp_path / ".env.example"
    sample.write_text("OPENAI_API_KEY=your-api-key", encoding="utf-8")
    monkeypatch.setattr(secret_scan, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(secret_scan, "tracked_files", lambda: [sample])
    result = secret_scan.evaluate(write_evidence=False)
    assert result.status == EvalStatus.PASS


def test_example_comment_cannot_hide_a_real_credential(tmp_path, monkeypatch):
    sample = tmp_path / "README.md"
    token = "sk-" + "credential-shaped-value-1234567890"
    sample.write_text(f"OPENAI_API_KEY={token}  # example config", encoding="utf-8")
    monkeypatch.setattr(secret_scan, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(secret_scan, "tracked_files", lambda: [sample])
    result = secret_scan.evaluate(write_evidence=False)
    assert result.status == EvalStatus.FAIL
    assert token not in result.critical_findings[0].detail


def test_placeholder_substring_cannot_hide_a_database_password(tmp_path, monkeypatch):
    sample = tmp_path / ".env.example"
    sample.write_text(
        "DATABASE_URL=postgresql://user:real-secret-value@db/service",
        encoding="utf-8",
    )
    monkeypatch.setattr(secret_scan, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(secret_scan, "tracked_files", lambda: [sample])
    assert secret_scan.evaluate(write_evidence=False).status == EvalStatus.FAIL


def test_metric_collision_invalidates_verdict():
    first = EvalResult(
        gate="ai_quality", evaluator="first", status=EvalStatus.PASS, score=100,
        metrics={"shared": MetricValue(raw=1, unit="count")},
    )
    second = EvalResult(
        gate="ai_quality", evaluator="second", status=EvalStatus.PASS, score=100,
        metrics={"shared": MetricValue(raw=0, unit="count")},
    )
    assert aggregate([first, second]).decision == Decision.EVALGATE_INVALID


def test_expired_and_non_suppressible_entries_are_invalid(tmp_path):
    policy = tmp_path / "suppressions.yaml"
    policy.write_text(
        """- id: SUP-OLD
  finding_id: HG-G4
  owner: security
  ticket: SEC-1
  reason: known backlog
  created_at: 2026-01-01
  expires_at: 2026-02-01
  baseline_git_sha: aaaaaaa
- id: SUP-SECRET
  finding_id: HG-S6
  owner: security
  ticket: SEC-2
  reason: never suppress secrets
  created_at: 2026-01-01
  expires_at: 2027-01-01
  baseline_git_sha: aaaaaaa
""",
        encoding="utf-8",
    )
    result = load_suppressions(policy, today=date(2026, 8, 25))
    assert len(result.errors) == 2


def test_row_level_detection_does_not_credit_equal_count_wrong_rows():
    run = {
        "test_results": [{
            "rule_id": "t.vendor_id.NOT_NULL",
            "rule_type": "NOT_NULL",
            "column": "vendor_id",
            "status": "FAIL",
            "violation_count": 2,
            "sample_refs": ["wrong-1", "wrong-2"],
        }]
    }
    scored = score_run(
        run,
        {"MISSING_VALUE": {"vendor_id": ["true-1", "true-2"]}},
    )
    assert scored["precision"] == 0.0
    assert scored["recall_row_level"] == 0.0


def test_row_level_detection_computes_tp_fp_fn_from_ids():
    run = {
        "test_results": [{
            "rule_id": "t.vendor_id.NOT_NULL",
            "rule_type": "NOT_NULL",
            "column": "vendor_id",
            "status": "FAIL",
            "violation_count": 2,
            "sample_refs": ["true-1", "wrong-1"],
        }]
    }
    scored = score_run(
        run,
        {"MISSING_VALUE": {"vendor_id": ["true-1", "true-2"]}},
    )
    assert scored["precision"] == 0.5
    assert scored["recall_row_level"] == 0.5
