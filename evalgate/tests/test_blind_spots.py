"""Self-tests for the two evaluators added to close demonstrated blind spots.

Both were written after a live run on 2026-08-22 proved the gate could not see
something it should have.  The tests are therefore written around the specific
failure each one missed, so that if the evaluator is ever weakened back to its
previous behaviour the reason it exists is what breaks.

The recurring risk in both is the same, and it is the false *negative*: an
evaluator that reports PASS when it simply had nothing to look at is worse than
no evaluator, because it converts an absence of evidence into a claim of health.
Several tests below assert NOT_MEASURED specifically, not PASS.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evalgate.gates.gate1_ai_quality import run_outcome_integrity as roi
from evalgate.gates.gate6_governance import served_path_fidelity as spf
from evalgate.schemas.eval_result import EvalStatus

RUN_A = "a" * 32
RUN_B = "b" * 32


# ---------------------------------------------------------------------------
# run_outcome_integrity
# ---------------------------------------------------------------------------

def _artefact(root: Path, stage: str, run_id: str, clock: str, body: dict | list) -> Path:
    directory = root / stage
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"debug_{stage}_20260822_{clock}_{run_id}.json"
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def output(tmp_path: Path) -> Path:
    return tmp_path / "output"


def test_no_artefacts_reports_not_measured_rather_than_passing(output: Path):
    output.mkdir(parents=True)
    result = roi.evaluate(write_evidence=False, output_dir=output)
    assert result.status == EvalStatus.NOT_MEASURED
    assert result.score is None
    assert "output/" in result.metadata["reason"]


def test_a_run_that_produced_rules_passes(output: Path):
    _artefact(output, "candidates", RUN_A, "100000", [{"c": 1}])
    _artefact(output, "rule_proposer", RUN_A, "100500",
              {"run_id": RUN_A, "total_rules": 19, "total_errors": 0, "errors": []})
    result = roi.evaluate(write_evidence=False, output_dir=output)
    assert result.status == EvalStatus.PASS
    assert result.metrics["latest_run_produced_output"].raw is True
    assert result.score == 100.0


def test_a_run_that_produced_nothing_blocks_the_release(output: Path):
    """The exact failure the aggregate score did not move for."""
    _artefact(output, "candidates", RUN_A, "100000", [])
    _artefact(output, "rule_proposer", RUN_A, "100500",
              {"run_id": RUN_A, "total_rules": 0, "total_errors": 1,
               "errors": [{"table": "t", "error": "1 validation error for TableRuleProposal"}]})
    result = roi.evaluate(write_evidence=False, output_dir=output)
    assert result.status == EvalStatus.FAIL
    assert result.score == 0.0
    blocking = [f for f in result.critical_findings if f.blocks_release]
    assert any(f.id == "HG-A7" for f in blocking)


def test_a_run_that_died_before_its_terminal_stage_is_not_silently_clean(output: Path):
    # Reached the candidate stage and stopped. Keying on the terminal artefact alone
    # would make this run invisible rather than failing.
    _artefact(output, "candidates", RUN_A, "100000", [{"c": 1}])
    result = roi.evaluate(write_evidence=False, output_dir=output)
    assert result.status == EvalStatus.FAIL
    assert result.metadata["latest_verdict"] == "DIED_EARLY"


def test_the_verdict_follows_the_latest_run_not_the_average(output: Path):
    """A healthy history must not pay for a system that is broken now."""
    _artefact(output, "rule_proposer", RUN_B, "080000",
              {"run_id": RUN_B, "total_rules": 40, "total_errors": 0, "errors": []})
    _artefact(output, "rule_proposer", RUN_A, "180000",
              {"run_id": RUN_A, "total_rules": 0, "total_errors": 1, "errors": []})
    result = roi.evaluate(write_evidence=False, output_dir=output)
    assert result.metadata["latest_run_id"] == RUN_A
    assert result.score == 0.0
    assert result.metrics["empty_run_rate"].raw == 0.5


def test_validation_error_counts_are_read_from_the_product_s_own_message(output: Path):
    _artefact(output, "rule_proposer", RUN_A, "100000",
              {"run_id": RUN_A, "total_rules": 5, "total_errors": 1,
               "errors": [{"error": "15 validation errors for TableRuleProposal"}]})
    result = roi.evaluate(write_evidence=False, output_dir=output)

    # One rejected item against five accepted. This assertion previously read
    # 15/20, which mixed units: 15 counts Pydantic field errors while 5 counts
    # rules, so a single badly-shaped proposal reported a 75% violation rate and
    # HG-A2 escalates to CRITICAL above 50%.
    # abs tolerance: the metric is rounded to six decimals before it is published,
    # which is coarser than approx's default relative tolerance at this magnitude.
    assert result.metrics["schema_violation_rate"].raw == pytest.approx(1 / 6, abs=1e-6)

    # The product's own message is still where the number comes from -- the field
    # error total is retained as severity, it just no longer forms a ratio.
    run = roi.collect_runs(output)[0]
    assert run.schema_rejections == 1
    assert run.validation_errors == 15


def test_a_run_that_never_reached_a_validator_has_no_violation_rate(output: Path):
    """No denominator must yield NOT_MEASURED, never a clean 0%."""
    _artefact(output, "candidates", RUN_A, "100000", [{"c": 1}])
    result = roi.evaluate(write_evidence=False, output_dir=output)
    rate = result.metrics["schema_violation_rate"]
    assert rate.raw is None
    assert rate.status == EvalStatus.NOT_MEASURED


def test_uncorrelated_files_are_skipped_rather_than_guessed_at(output: Path):
    directory = output / "rule_proposer"
    directory.mkdir(parents=True)
    (directory / "debug_proposed_rules_nocorrelator.json").write_text("{}", encoding="utf-8")
    result = roi.evaluate(write_evidence=False, output_dir=output)
    assert result.status == EvalStatus.NOT_MEASURED


# ---------------------------------------------------------------------------
# served_path_fidelity
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_repo(tmp_path: Path, monkeypatch) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setattr(spf, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(spf, "SRC", src)
    return tmp_path


def _config_py(repo: Path, default: str = "mock") -> None:
    (repo / "src" / "config.py").write_text(
        'agent_mode: Literal["mock", "graph"] = os.getenv("AGENT_MODE") or "'
        + default
        + '"\n',
        encoding="utf-8",
    )


def _workflow_py(repo: Path) -> None:
    (repo / "src" / "workflow.py").write_text(
        'if settings.agent_mode == "mock":\n    return _mock_proposals(evidence)\n',
        encoding="utf-8",
    )


def test_an_unset_switch_falls_back_to_the_code_default_and_blocks(fake_repo: Path):
    _config_py(fake_repo, "mock")
    _workflow_py(fake_repo)
    (fake_repo / "docker-compose.yml").write_text(
        "services:\n  api:\n    environment:\n      - APP_ENV=local\n", encoding="utf-8"
    )
    result = spf.evaluate(write_evidence=False)
    assert result.metrics["served_path_is_mocked"].raw is True
    assert any(f.id == "HG-G5" and f.blocks_release for f in result.critical_findings)


def test_a_deployment_config_selecting_the_live_mode_clears_the_finding(fake_repo: Path):
    _config_py(fake_repo, "mock")
    _workflow_py(fake_repo)
    (fake_repo / "docker-compose.yml").write_text(
        "services:\n  api:\n    environment:\n"
        "      - AGENT_MODE=graph\n      - OPENAI_API_KEY=NOT-A-REAL-KEY\n",
        encoding="utf-8",
    )
    result = spf.evaluate(write_evidence=False)
    assert result.metrics["served_path_is_mocked"].raw is False
    assert not [f for f in result.critical_findings if f.id == "HG-G5"]


def test_a_bare_passthrough_entry_does_not_count_as_selecting_a_mode(fake_repo: Path):
    """``- AGENT_MODE`` in compose forwards the host value; it sets nothing itself."""
    _config_py(fake_repo, "mock")
    _workflow_py(fake_repo)
    (fake_repo / "docker-compose.yml").write_text(
        "services:\n  api:\n    environment:\n      - AGENT_MODE\n", encoding="utf-8"
    )
    result = spf.evaluate(write_evidence=False)
    assert result.metrics["served_path_is_mocked"].raw is True


def test_a_product_with_no_mock_path_is_not_applicable(fake_repo: Path):
    """No switch and no canned branch means there is no question to answer."""
    (fake_repo / "src" / "config.py").write_text("app_name = 'x'\n", encoding="utf-8")
    result = spf.evaluate(write_evidence=False)
    assert result.status == EvalStatus.NOT_APPLICABLE


def test_a_missing_credential_is_reported_but_never_blocks(fake_repo: Path):
    """This evaluator cannot see a secret manager, so absence is not proof."""
    _config_py(fake_repo, "mock")
    _workflow_py(fake_repo)
    result = spf.evaluate(write_evidence=False)
    credential = [f for f in result.critical_findings if f.id == "CRED-UNSEEN"]
    assert credential, "the missing credential must still be surfaced"
    assert not any(f.blocks_release for f in credential)


def test_no_credential_value_is_ever_written_into_the_facts(fake_repo: Path):
    """The evidence file lands in the repository, so values must not reach it.

    The sentinel is deliberately *not* credential-shaped. This file is tracked, and
    ``secret_scan`` reads tracked files -- an earlier version used a realistic
    ``sk-`` literal here, which matched the openai_key pattern and raised a CRITICAL
    release-blocking finding against the test fixture itself. Test data must never
    manufacture a finding; what this test asserts is that the *value* does not leak,
    and any unique string establishes that.
    """
    _config_py(fake_repo, "mock")
    _workflow_py(fake_repo)
    secret = "CREDENTIAL-VALUE-SENTINEL-MUST-NOT-LEAK"
    (fake_repo / "docker-compose.yml").write_text(
        "services:\n  api:\n    environment:\n      - OPENAI_API_KEY=" + secret + "\n",
        encoding="utf-8",
    )
    serialised = json.dumps(spf.inspect(), ensure_ascii=False)
    assert secret not in serialised


# ---------------------------------------------------------------------------
# default_credential_probe
# ---------------------------------------------------------------------------

from evalgate.gates.gate2_security import default_credential_probe as dcp  # noqa: E402


@pytest.fixture
def cred_repo(tmp_path: Path, monkeypatch) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setattr(dcp, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(dcp, "SRC", src)
    return tmp_path


SEED = (
    "DEFAULT_USERS = (\n"
    '    ("user", "User", "user", "USER"),\n'
    '    ("admin", "Admin", "admin", "ADMIN"),\n'
    ")\n"
)


def test_a_product_that_seeds_no_accounts_is_not_applicable(cred_repo: Path):
    (cred_repo / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    assert dcp.evaluate(write_evidence=False).status == EvalStatus.NOT_APPLICABLE


def test_password_equal_to_username_behind_an_unguarded_call_blocks(cred_repo: Path):
    (cred_repo / "src" / "seed.py").write_text(SEED, encoding="utf-8")
    (cred_repo / "src" / "db.py").write_text(
        "def init_db():\n    ensure_default_users(session)\n", encoding="utf-8"
    )
    result = dcp.evaluate(write_evidence=False)
    assert result.metrics["default_credentials_active"].raw is True
    assert any(f.id == "HG-S7" and f.blocks_release for f in result.critical_findings)


def test_an_environment_guarded_call_site_does_not_block(cred_repo: Path):
    """Seeded demo accounts are legitimate when something checks where it is running."""
    (cred_repo / "src" / "seed.py").write_text(SEED, encoding="utf-8")
    (cred_repo / "src" / "db.py").write_text(
        "def init_db():\n"
        '    if settings.app_env == "local":\n'
        "        ensure_default_users(session)\n",
        encoding="utf-8",
    )
    result = dcp.evaluate(write_evidence=False)
    assert result.metrics["default_credentials_active"].raw is False
    assert not [f for f in result.critical_findings if f.blocks_release]


def test_a_strong_seeded_password_is_not_reported(cred_repo: Path):
    (cred_repo / "src" / "seed.py").write_text(
        "DEFAULT_USERS = (\n"
        '    ("admin", "Admin", "9f3c-Kx2!qR7vTz", "ADMIN"),\n'
        ")\n",
        encoding="utf-8",
    )
    (cred_repo / "src" / "db.py").write_text(
        "def init_db():\n    ensure_default_users(session)\n", encoding="utf-8"
    )
    result = dcp.evaluate(write_evidence=False)
    assert result.metrics["default_credentials_active"].raw is False


def test_no_password_value_is_ever_recorded(cred_repo: Path):
    """The evidence file is written into the repository."""
    (cred_repo / "src" / "seed.py").write_text(
        "DEFAULT_USERS = (\n"
        '    ("admin", "Admin", "password", "ADMIN"),\n'
        ")\n",
        encoding="utf-8",
    )
    (cred_repo / "src" / "db.py").write_text(
        "def init_db():\n    ensure_default_users(session)\n", encoding="utf-8"
    )
    creds = dcp.find_seeded_credentials()
    assert creds and creds[0].weakness == "password is a common default"
    assert "password" not in json.dumps([c.__dict__ for c in creds]).replace(
        '"password is a common default"', ""
    ).replace('"weakness"', "")


def test_the_function_definition_is_not_mistaken_for_a_call_site(cred_repo: Path):
    (cred_repo / "src" / "seed.py").write_text(
        SEED + "\n\ndef ensure_default_users(db):\n    pass\n", encoding="utf-8"
    )
    assert dcp.find_seed_call_sites() == []


def test_a_role_constant_is_not_mistaken_for_a_weak_password(cred_repo: Path):
    """ADMIN is a role, not a password.

    Reading it as one flagged every seeded admin account regardless of password
    strength -- the false positive that trains a team to ignore the gate.
    """
    (cred_repo / "src" / "seed.py").write_text(
        "DEFAULT_USERS = (\n"
        '    ("ops", "Ops", "T7#pQ2vLmZ9x", "ADMIN"),\n'
        '    ("view", "Viewer", "Rw4!nB8kEc1s", "USER"),\n'
        ")\n",
        encoding="utf-8",
    )
    (cred_repo / "src" / "db.py").write_text(
        "def init_db():\n    ensure_default_users(session)\n", encoding="utf-8"
    )
    assert dcp.find_seeded_credentials() == []
    assert dcp.evaluate(write_evidence=False).metrics["default_credentials_active"].raw is False


def test_the_real_repository_still_reports_its_three_seeded_accounts():
    """Anchored to the product as it stands: user, steward and admin all seed weak.

    Written so that it stops failing when the product adds an environment guard,
    rather than having to be deleted.
    """
    result = dcp.evaluate(write_evidence=False)
    if result.status == EvalStatus.NOT_APPLICABLE:
        pytest.skip("no seeding routine in this checkout")
    if not result.metrics["default_credentials_active"].raw:
        pytest.skip("the product now guards its seeding call; the gate is satisfied")
    assert result.metadata["credentials_found"] >= 3
    assert result.metadata["unguarded_call_sites"] >= 1


def test_a_timeout_is_not_reported_as_a_validator_rejection(output: Path):
    """Two different bugs with two different owners.

    A model that answers with the wrong shape is a prompt-versus-schema problem.
    A model that never answers inside the client timeout is a configuration problem.
    Collapsing both into "structured output was rejected" sends the wrong team
    looking -- which is what happened when a 25s client timeout was introduced and
    every proposal call began timing out.
    """
    _artefact(output, "rule_proposer", RUN_A, "100000",
              {"run_id": RUN_A, "total_rules": 0, "total_errors": 1,
               "errors": [{"table": "t", "error": "Request timed out."}]})
    result = roi.evaluate(write_evidence=False, output_dir=output)
    assert result.metadata["latest_failure_kind"] == "TIMEOUT"
    # No validator was ever reached, so there is no denominator and no rate.
    assert result.metrics["schema_violation_rate"].raw is None


def test_a_validator_rejection_is_named_as_one(output: Path):
    _artefact(output, "rule_proposer", RUN_A, "100000",
              {"run_id": RUN_A, "total_rules": 0, "total_errors": 1,
               "errors": [{"error": "6 validation errors for TableRuleProposal"}]})
    result = roi.evaluate(write_evidence=False, output_dir=output)
    assert result.metadata["latest_failure_kind"] == "SCHEMA_REJECTED"


def test_timeout_wins_over_stale_validation_text(output: Path):
    """A retried call can carry both; the timeout is what actually stopped it."""
    _artefact(output, "rule_proposer", RUN_A, "100000",
              {"run_id": RUN_A, "total_rules": 0, "total_errors": 1,
               "errors": [{"error": "2 validation errors for X ... then Request timed out."}]})
    result = roi.evaluate(write_evidence=False, output_dir=output)
    assert result.metadata["latest_failure_kind"] == "TIMEOUT"


def test_a_successful_run_has_no_failure_kind(output: Path):
    _artefact(output, "rule_proposer", RUN_A, "100000",
              {"run_id": RUN_A, "total_rules": 12, "total_errors": 0, "errors": []})
    result = roi.evaluate(write_evidence=False, output_dir=output)
    assert result.metadata["latest_failure_kind"] is None
