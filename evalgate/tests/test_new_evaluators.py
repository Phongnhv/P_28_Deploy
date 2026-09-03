"""Contract tests for the four probes that were rebound to the artifact bundle.

All four used to build their own fixture, ignore the manifest entirely, and return
``PASS`` with a score of 100.0 on every run -- which is what these tests asserted. That
made them tests of the wrong property: they locked in the behaviour that let three
evaluators award ``ai_quality`` 37.5 points without reading a byte of the bundle they were
scoring.

The contract now is the opposite one, and it is the contract worth locking in: **without
the evidence, there is no score.** A probe that cannot see the artefact it grades must say
so, because ``NOT_MEASURED`` drops out of the aggregate while ``PASS`` inflates it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evalgate.gates.gate1_ai_quality import anomaly_logic_probe as alp
from evalgate.gates.gate1_ai_quality import report_grounding_probe as rgp
from evalgate.gates.gate1_ai_quality import sql_compilation_probe as scp
from evalgate.gates.gate4_input_data import profile_accuracy_probe as pap
from evalgate.schemas.eval_result import EvalResult, EvalStatus

PROBES = [
    (alp, "ai_quality", "anomaly_logic_probe_v1"),
    (scp, "ai_quality", "sql_compilation_probe_v1"),
    (rgp, "ai_quality", "report_grounding_probe_v1"),
    (pap, "input_data", "profile_accuracy_probe_v1"),
]


@pytest.mark.parametrize(("module", "gate", "name"), PROBES)
def test_a_probe_without_a_bundle_reports_not_measured(module, gate, name):
    """The regression this file exists to prevent: a probe scoring 100 on no evidence."""
    result = module.evaluate(write_evidence=False, context=None)
    assert isinstance(result, EvalResult)
    assert result.gate == gate
    assert result.evaluator == name
    assert result.status == EvalStatus.NOT_MEASURED
    assert result.score is None
    assert result.metadata.get("reason")


@pytest.mark.parametrize(("module", "gate", "name"), PROBES)
def test_a_probe_without_a_bundle_publishes_no_metric(module, gate, name):
    """No metric means no hard gate reads a number nobody measured."""
    result = module.evaluate(write_evidence=False, context=None)
    assert result.metrics == {}


def _bundle(tmp_path: Path) -> Path:
    """A minimal but honest bundle: profile and frame agree, one rule, one verdict."""
    (tmp_path / "input").mkdir()
    (tmp_path / "profile").mkdir()
    (tmp_path / "proposals").mkdir()
    (tmp_path / "execution").mkdir()
    (tmp_path / "anomaly").mkdir()

    (tmp_path / "input" / "dataset.csv").write_text(
        "amount,label\n10,a\n20,b\n30,a\n", encoding="utf-8"
    )
    (tmp_path / "profile" / "profile.json").write_text(
        json.dumps(
            {
                "dataset_id": "ds-1",
                "row_count": 3,
                "columns": [
                    {"name": "amount", "null_rate": 0.0, "non_null_count": 3,
                     "full_distinct_count": 3, "min_value": 10.0, "max_value": 30.0},
                    {"name": "label", "null_rate": 0.0, "non_null_count": 3,
                     "full_distinct_count": 2},
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "proposals" / "proposals.json").write_text(
        json.dumps(
            {
                "proposed_rules": [
                    {"rule_id": "r1", "column": "amount", "rule_type": "RANGE",
                     "parameters": {"min": 0, "max": 100}}
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "execution" / "results.json").write_text(
        json.dumps(
            {"test_results": [
                {"rule_id": "r1", "status": "FAIL", "failed_count": 7, "checked_count": 3},
            ]}
        ),
        encoding="utf-8",
    )
    (tmp_path / "anomaly" / "report.json").write_text(
        json.dumps(
            {"payload": {"decision": "ANOMALY", "status": "SUCCEEDED", "score": 0.8,
                         "confidence": 0.8, "execution_run_id": "run-1",
                         "hypotheses": [{"summary": "7 rules were violated."}]}}
        ),
        encoding="utf-8",
    )
    return tmp_path


class _FakeContext:
    """The slice of EvalRunContext these probes touch."""

    def __init__(self, root: Path, mapping: dict[str, str]) -> None:
        self.artifact_root = root
        self.dataset_id = "ds-1"
        self.run_id = "run-1"
        self._mapping = mapping

    def records(self, artifact_type: str):
        path = self._mapping.get(artifact_type)
        return (path,) if path else ()

    def path_for(self, record):
        return self.artifact_root / record

    def read_json(self, artifact_type: str, *, many: bool = False):
        return json.loads(self.path_for(self._mapping[artifact_type]).read_text("utf-8"))


@pytest.fixture()
def bundle_context(tmp_path: Path) -> _FakeContext:
    root = _bundle(tmp_path)
    return _FakeContext(
        root,
        {
            "input-dataset": "input/dataset.csv",
            "dataset-profile": "profile/profile.json",
            "proposals": "proposals/proposals.json",
            "execution-results": "execution/results.json",
            "anomaly-report": "anomaly/report.json",
        },
    )


def test_profile_accuracy_recomputes_the_published_figures(bundle_context):
    result = pap.evaluate(write_evidence=False, context=bundle_context)
    assert result.status == EvalStatus.PASS
    assert result.metrics["profile_statistic_fidelity"].raw == 1.0
    # The denominator has to be real, or a fidelity of 1.0 means nothing.
    assert result.metrics["profile_statistics_checked"].raw > 5


def test_profile_accuracy_catches_a_wrong_published_figure(bundle_context, tmp_path):
    profile_path = tmp_path / "profile" / "profile.json"
    document = json.loads(profile_path.read_text(encoding="utf-8"))
    document["columns"][0]["min_value"] = -999.0
    profile_path.write_text(json.dumps(document), encoding="utf-8")

    result = pap.evaluate(write_evidence=False, context=bundle_context)
    assert result.status == EvalStatus.FAIL
    assert result.metrics["profile_statistic_fidelity"].raw < 1.0
    assert any(f.id == "PROFILE-FIDELITY" for f in result.critical_findings)


def test_sql_compilation_scores_the_rules_the_run_proposed(bundle_context):
    result = scp.evaluate(write_evidence=False, context=bundle_context)
    assert result.metrics["rules_compiled"].raw == 1
    assert result.metrics["rule_compile_rate"].raw == 1.0


def test_report_grounding_resolves_a_figure_that_exists(bundle_context):
    result = rgp.evaluate(write_evidence=False, context=bundle_context)
    # "7 rules were violated" resolves to failed_count = 7.
    assert result.metrics["report_figure_grounding_rate"].raw == 1.0
    assert result.metrics["report_figures_checked"].raw >= 1


def test_report_grounding_flags_a_figure_that_resolves_to_nothing(bundle_context, tmp_path):
    report_path = tmp_path / "anomaly" / "report.json"
    document = json.loads(report_path.read_text(encoding="utf-8"))
    document["payload"]["hypotheses"] = [{"summary": "4242 rules were violated."}]
    report_path.write_text(json.dumps(document), encoding="utf-8")

    result = rgp.evaluate(write_evidence=False, context=bundle_context)
    assert result.status == EvalStatus.FAIL
    assert result.metrics["report_figure_grounding_rate"].raw < 1.0
    assert any(f.id == "REPORT-UNGROUNDED" for f in result.critical_findings)


def test_anomaly_probe_requires_abstention_on_a_single_run(bundle_context):
    """One run of history cannot support an ANOMALY verdict."""
    result = alp.evaluate(write_evidence=False, context=bundle_context)
    assert result.status == EvalStatus.FAIL
    assert result.metrics["anomaly_abstains_on_cold_start"].raw is False
    assert any(f.id == "ANOMALY-NO-ABSTENTION" for f in result.critical_findings)


def test_anomaly_probe_flags_a_verdict_with_no_failures_under_it(bundle_context, tmp_path):
    execution_path = tmp_path / "execution" / "results.json"
    execution_path.write_text(
        json.dumps({"test_results": [{"rule_id": "r1", "status": "PASS"}]}), encoding="utf-8"
    )
    result = alp.evaluate(write_evidence=False, context=bundle_context)
    assert any(f.id == "ANOMALY-UNSUPPORTED" for f in result.critical_findings)
