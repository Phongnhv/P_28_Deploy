"""Unit tests for multi_dataset_readiness_v1 and workspace_integrity_v1 evaluators.

These 2 evaluators were the only ones without dedicated unit tests in evalgate/tests/.
"""

from __future__ import annotations

from evalgate.core import git_read
from evalgate.core import workspace_integrity as wi
from evalgate.gates.readiness import multi_dataset_readiness as mdr
from evalgate.schemas.eval_result import EvalResult, EvalStatus, Severity

# ---------------------------------------------------------------------------
# multi_dataset_readiness_v1 tests
# ---------------------------------------------------------------------------


def test_multi_dataset_readiness_evaluate_contract():
    """evaluate() must return an EvalResult matching the registry spec."""
    res = mdr.evaluate(write_evidence=False)
    assert isinstance(res, EvalResult)
    assert res.gate == "input_data"
    assert res.evaluator == "multi_dataset_readiness_v1"
    assert res.status in {EvalStatus.WARN, EvalStatus.FAIL}
    assert res.score is not None
    assert 0.0 <= res.score <= 100.0

    # Verify all expected metric names exist
    expected_metrics = {
        "upload_surface_exists",
        "schema_agnostic_row_storage",
        "dataset_has_owner_or_schema",
        "domain_not_hardcoded_in_prompt",
        "dataset_deletion_endpoint",
        "evidence_column_cap_sufficient",
        "low_single_domain_coupling",
        "multi_dataset_readiness_score",
        "single_domain_coupled_files",
    }
    assert expected_metrics.issubset(res.metrics.keys())
    assert res.metrics["multi_dataset_readiness_score"].raw == res.score


def test_multi_dataset_readiness_all_false_gives_zero_score(monkeypatch):
    """When all multi-dataset criteria fail, the score must be 0.0 and status FAIL."""
    monkeypatch.setattr(mdr, "_has_upload_surface", lambda: False)
    monkeypatch.setattr(mdr, "_has_generic_row_storage", lambda: False)
    monkeypatch.setattr(mdr, "_dataset_owner_present", lambda: False)
    monkeypatch.setattr(mdr, "_domain_in_system_prompt", lambda: True)  # domain hardcoded
    monkeypatch.setattr(mdr, "_delete_dataset_endpoint", lambda: False)
    monkeypatch.setattr(mdr, "_evidence_column_cap", lambda: None)
    monkeypatch.setattr(mdr, "_scan_hardcoded", lambda: {"count": 99, "files": ["f"] * 99})

    res = mdr.evaluate(write_evidence=False)
    assert res.score == 0.0
    assert res.status == EvalStatus.FAIL


def test_multi_dataset_readiness_all_true_gives_perfect_score(monkeypatch):
    """When all multi-dataset criteria pass, the score must be 100.0 and status WARN."""
    monkeypatch.setattr(mdr, "_has_upload_surface", lambda: True)
    monkeypatch.setattr(mdr, "_has_generic_row_storage", lambda: True)
    monkeypatch.setattr(mdr, "_dataset_owner_present", lambda: True)
    monkeypatch.setattr(mdr, "_domain_in_system_prompt", lambda: False)
    monkeypatch.setattr(mdr, "_delete_dataset_endpoint", lambda: True)
    monkeypatch.setattr(mdr, "_evidence_column_cap", lambda: 500)
    monkeypatch.setattr(mdr, "_scan_hardcoded", lambda: {"count": 2, "files": ["f1", "f2"]})

    res = mdr.evaluate(write_evidence=False)
    assert res.score == 100.0
    assert res.status == EvalStatus.WARN


def test_multi_dataset_readiness_evidence_cap_boundary(monkeypatch):
    """evidence_column_cap_sufficient requires cap >= 200."""
    monkeypatch.setattr(mdr, "_evidence_column_cap", lambda: 199)
    res_under = mdr.evaluate(write_evidence=False)
    assert res_under.metrics["evidence_column_cap_sufficient"].raw is False

    monkeypatch.setattr(mdr, "_evidence_column_cap", lambda: 200)
    res_exact = mdr.evaluate(write_evidence=False)
    assert res_exact.metrics["evidence_column_cap_sufficient"].raw is True


def test_multi_dataset_readiness_hardcoded_coupling_boundary(monkeypatch):
    """low_single_domain_coupling is True when hits <= 10."""
    monkeypatch.setattr(mdr, "_scan_hardcoded", lambda: {"count": 10, "files": []})
    res_pass = mdr.evaluate(write_evidence=False)
    assert res_pass.metrics["low_single_domain_coupling"].raw is True

    monkeypatch.setattr(mdr, "_scan_hardcoded", lambda: {"count": 11, "files": []})
    res_fail = mdr.evaluate(write_evidence=False)
    assert res_fail.metrics["low_single_domain_coupling"].raw is False


def test_multi_dataset_readiness_writes_evidence_when_requested(monkeypatch):
    """write_evidence=True must write evidence JSON to target directory."""
    test_evidence_dir = mdr.PROJECT_ROOT / "evalgate" / "evidence" / "_test_readiness"
    monkeypatch.setattr(mdr, "EVIDENCE_DIR", test_evidence_dir)
    try:
        res = mdr.evaluate(write_evidence=True)
        target = test_evidence_dir / "multi_dataset_readiness.json"
        assert target.exists()
        assert len(res.evidence) == 1
    finally:
        if test_evidence_dir.exists():
            for f in test_evidence_dir.iterdir():
                f.unlink()
            test_evidence_dir.rmdir()


# ---------------------------------------------------------------------------
# workspace_integrity_v1 tests
# ---------------------------------------------------------------------------


def test_workspace_integrity_clean_state_passes(monkeypatch):
    """A clean repository state must yield PASS with no score and no findings."""
    clean_state = {
        "git_ref": "main",
        "head_sha": "abc1234",
        "staged_product_paths": [],
        "unstaged_product_paths": [],
        "untracked_product_paths": [],
        "unmerged_paths": [],
        "staged_insertions": 0,
        "staged_deletions": 0,
    }
    monkeypatch.setattr(wi, "collect_state", lambda: clean_state)

    res = wi.evaluate(write_evidence=False)
    assert res.gate == "preflight"
    assert res.evaluator == "workspace_integrity_v1"
    assert res.status == EvalStatus.PASS
    assert res.score is None  # Preflight does not grade
    assert len(res.critical_findings) == 0
    assert res.metrics["workspace_dirty"].raw is False
    assert res.metrics["workspace_dirty"].normalized == 100.0


def test_workspace_integrity_dirty_state_fails_with_non_blocking_finding(monkeypatch):
    """A dirty workspace yields FAIL and PREFLIGHT-STALE finding with blocks_release=False."""
    dirty_state = {
        "git_ref": "main",
        "head_sha": "abc1234",
        "staged_product_paths": ["src/api/routes.py"],
        "unstaged_product_paths": ["src/config.py"],
        "untracked_product_paths": ["src/services/new_svc.py"],
        "unmerged_paths": [],
        "staged_insertions": 10,
        "staged_deletions": 2,
    }
    monkeypatch.setattr(wi, "collect_state", lambda: dirty_state)

    res = wi.evaluate(write_evidence=False)
    assert res.status == EvalStatus.FAIL
    assert res.score is None
    assert res.metrics["workspace_dirty"].raw is True
    assert res.metrics["workspace_dirty"].normalized == 0.0
    assert res.metrics["staged_product_files"].raw == 1
    assert res.metrics["staged_line_delta"].raw == 12

    assert len(res.critical_findings) == 1
    f = res.critical_findings[0]
    assert f.id == "PREFLIGHT-STALE"
    assert f.severity == Severity.HIGH
    assert f.blocks_release is False  # Runner handles STALE via CLI option, finding does not block


def test_workspace_integrity_git_unavailable_handles_gracefully(monkeypatch):
    """When git is unavailable, evaluator reports BLOCKED_MISSING_CREDENTIAL."""
    def _raise():
        raise git_read.GitUnavailableError("git not installed")

    monkeypatch.setattr(wi, "collect_state", _raise)
    res = wi.evaluate(write_evidence=False)
    assert res.status == EvalStatus.BLOCKED_MISSING_CREDENTIAL
    assert "git unavailable" in res.metadata.get("reason", "")


def test_workspace_integrity_reasons_formatting():
    """_reasons() must format each dirty category accurately."""
    state = {
        "staged_product_paths": ["a.py", "b.py"],
        "unstaged_product_paths": ["c.py"],
        "untracked_product_paths": ["d.py"],
        "unmerged_paths": ["conflict.py"],
        "staged_insertions": 5,
        "staged_deletions": 1,
    }
    reasons = wi._reasons(state)
    assert len(reasons) == 4
    assert "2 product file(s) staged" in reasons[0]
    assert "(+5/-1 lines)" in reasons[0]
    assert "1 product file(s) modified" in reasons[1]
    assert "1 untracked file(s)" in reasons[2]
    assert "1 path(s) left in a conflicted merge state" in reasons[3]


def test_workspace_integrity_product_pathspec():
    """PRODUCT_PATHSPEC must only include product directories, excluding evalgate."""
    assert "src" in wi.PRODUCT_PATHSPEC
    assert "requirements.txt" in wi.PRODUCT_PATHSPEC
    assert "evalgate" not in wi.PRODUCT_PATHSPEC
