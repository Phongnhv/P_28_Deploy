"""Fast regression of the candidate -> proposal -> file execution contract."""

import json
from types import SimpleNamespace

import pandas as pd
import pytest

from src.agents.nodes.rule_candidate_builder_node import rule_candidate_builder_node
from src.agents.nodes.rule_proposer_node import _bind_proposal_to_candidates, _candidate_batches, _stamp_rule
from src.config import get_settings
from src.services.dashboard_agent_workflow import (
    _build_dashboard_rule_candidates,
    _normalise_graph_rules,
    _proposal_evidence_from_versioned_snapshot,
)
from src.services.job_runner import _uploaded_rule_outcome, execute_uploaded_rule
from src.services.versioned_dataset import DatasetContractError, execute_rule_frame


def profile_evidence(numeric_columns=24):
    columns = [{
        "name": f"measure_{index}", "data_type": "number", "null_rate": 0,
        "distinct_count": 4, "min_value": 0, "max_value": 100,
        "quantiles": {"p95": 20},
    } for index in range(numeric_columns)]
    columns.extend([
        {"name": "optional", "data_type": "string", "null_rate": 0.2, "distinct_count": 3},
        {"name": "show_id", "data_type": "string", "null_rate": 0, "distinct_count": 10,
         "full_distinct_count": 10, "is_unique_full_table": True, "uniqueness_rate": 1},
        {"name": "vendor_id", "data_type": "string", "null_rate": 0, "distinct_count": 4,
         "full_distinct_count": 4, "is_unique_full_table": False, "uniqueness_rate": 0.4},
    ])
    return _proposal_evidence_from_versioned_snapshot(
        SimpleNamespace(id="coverage-fixture", manifest_version="versioned-v1"),
        {"columns": columns, "row_count": 10, "completeness_score": 99,
         "validity_score": 100, "duplicate_rate": 0, "evidence_keys": []},
    )


def test_no_rule_or_column_cap_and_all_numeric_aliases_receive_range(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "output_dir", str(tmp_path))
    evidence = profile_evidence(70)
    checklist = _build_dashboard_rule_candidates(evidence)
    assert sum(c.rule_type == "RANGE" for c in checklist) == 70
    assert sum(c.rule_type == "NOT_NULL" for c in checklist) == 72
    assert len({c.id for c in checklist}) == len(checklist)
    assert {c.column for c in checklist if c.rule_type == "UNIQUE"} == {"show_id"}

    digest = evidence.to_agent_digest()
    candidates = rule_candidate_builder_node({
        "dataset_profile_digest": digest,
        "semantic_contract": {"tables": {evidence.dataset_id: {"columns": []}}},
    })["rule_candidates"]
    assert {c["candidate_id"] for c in candidates} == {c.id for c in checklist}
    assert sum(map(len, _candidate_batches(candidates, 20))) == len(checklist)

    # Simulate narrative output only; real binder/stamper/matcher own execution fields.
    draft = {"table": evidence.dataset_id, "rules": [{
        "candidate_id": c["candidate_id"], "column": c["column"], "rule_type": c["rule_type"],
        "confidence": {"overall": 0.8, "evidence_strength": 0.8, "business_support": 0.8,
                       "sample_representativeness": 1.0, "explanation": "Replay fixture"},
        "rule_name": f"Check {c['column']}", "business_rationale": "Pinned aggregate evidence",
        "proposal_basis": "DATA_PROFILE", "severity": "MEDIUM", "dimension": c["dimension"],
        "rule_description": f"Check {c['column']}", "ai_reasoning": f"Pinned evidence for {c['column']}",
    } for c in candidates]}
    bound = _bind_proposal_to_candidates(evidence.dataset_id, draft, candidates)
    stamped = [_stamp_rule(r, evidence.dataset_id, "replay", requirement=c, table_digest=digest[evidence.dataset_id])
               for r, c in zip(bound.rules, candidates)]
    proposals = _normalise_graph_rules(stamped, evidence)
    assert len(proposals) == len(checklist)
    assert {p.rule_type for p in proposals} == {"not_null", "numeric_range", "unique", "null_rate"}
    frame = pd.DataFrame({c.name: list(range(10)) for c in evidence.columns})
    assert all(execute_rule_frame(frame, p.rule_spec)["status"] in {"PASS", "FAIL"} for p in proposals)

    foreign = {**stamped[0], "column": "netflix_column_absent_from_taxi"}
    assert _normalise_graph_rules([foreign], evidence) == []


@pytest.mark.parametrize(("rule_type", "spec", "failed_ids"), [
    ("unique", {"column": "code"}, ["r2"]),
    ("NULL_RATE", {"column": "amount", "max_null_pct": 10}, ["r3"]),
    ("NULL_RATE", {"column": "amount", "max_null_pct": 50}, []),
    ("range", {"column": "amount", "min": 0}, ["r2", "r3"]),
    ("accepted_values", {"column": "code", "parameters": {"accepted_values": ["A"]}}, ["r3"]),
    ("regex_format", {"column": "code", "regex": "^A$"}, ["r3"]),
    ("cross_field_comparison", {"columns": ["start", "end"], "operator": "<="}, ["r2"]),
    ("duplicate_fingerprint", {"fingerprint_columns": ["code"]}, ["r2"]),
])
def test_uploaded_executor_handles_supported_types_and_datetime(tmp_path, rule_type, spec, failed_ids):
    path = tmp_path / "source.csv"
    path.write_text("source_row_id,code,amount,start,end\nr1,A,2,2026-01-01,2026-01-02\nr2,A,-1,2026-01-03,2026-01-02\nr3,B,,2026-01-01,2026-01-02\n")
    checked, ids, count = execute_uploaded_rule(path, rule_type, spec)
    assert checked == 3
    assert ids == failed_ids
    assert count == len(failed_ids)


@pytest.mark.parametrize(("rule_type", "spec"), [
    ("unsupported", {"column": "amount"}),
    ("not_null", {"column": "duration"}),
    ("unique", {"column": "duration"}),
    ("cross_field_comparison", {"columns": ["amount", "duration"], "operator": "<"}),
    ("duplicate_fingerprint", {"fingerprint_columns": ["amount", "duration"]}),
    ("numeric_range", {"column": "amount", "min": 10, "max": 1}),
    ("numeric_range", {"column": "amount", "min": "nan"}),
    ("null_rate", {"column": "amount", "max_null_pct": -1}),
])
def test_uploaded_executor_rejects_unknown_columns_types_and_invalid_parameters(tmp_path, rule_type, spec):
    path = tmp_path / "taxi.csv"
    path.write_text("amount\n1\n2\n")
    with pytest.raises(DatasetContractError):
        execute_uploaded_rule(path, rule_type, spec)


def test_row_count_preserves_fail_for_empty_source():
    result = _uploaded_rule_outcome(None, "row_count", {"min_row_count": 1}, frame=pd.DataFrame({"amount": []}))
    assert result["status"] == "FAIL"
    assert result["checked_count"] == 0
    assert result["violation_row_ids"] == []


def test_failure_count_not_limited_to_sampled_ids(tmp_path):
    path = tmp_path / "values.csv"
    path.write_text("amount\n" + "-1\n" * 1200)
    checked, ids, failed = execute_uploaded_rule(path, "range", {"column": "amount", "min": 0})
    assert checked == failed == 1200
    assert len(ids) <= failed


def test_dashboard_row_references_do_not_switch_to_business_id(tmp_path):
    path = tmp_path / "source.csv"
    path.write_text("id,amount\nB001,-1\nB002,3\n")
    assert execute_uploaded_rule(path, "range", {"column": "amount", "min": 0}) == (2, ["1"], 1)


def test_graph2_persists_new_types_and_isolates_invalid_columns(monkeypatch, tmp_path):
    from sqlalchemy.orm import Session

    from src.api.routes import RuleSpecSchema
    from src.models.database import DqResultModel, DqRunModel, JobModel, RuleProposalModel, RuleVersionModel
    from src.services.job_runner import run_dq_checks
    from src.services.rule_store import get_engine

    source = tmp_path / "taxi.csv"
    source.write_text("source_row_id,code,amount\nr1,A,1\nr2,A,\nr3,B,3\n")
    dataset_id = "dataset-nyc-yellow-taxi-50k"
    specs = [
        {"type": "unique", "column": "code"},
        {"type": "null_rate", "column": "amount", "max_null_pct": 10},
        {"type": "not_null", "column": "duration"},
        {"type": "numeric_range", "column": "amount", "min_value": 0},
    ]
    # The API must retain the reviewed threshold when serializing/editing a proposal.
    assert RuleSpecSchema.model_validate(specs[1]).model_dump(exclude_none=True) == specs[1]
    monkeypatch.setattr("src.services.job_runner._materialize_versioned_dataset_path", lambda *_a, **_k: (source, False))
    with Session(get_engine()) as db:
        db.add(JobModel(id="coverage-job", type="RUN_DQ", status="PENDING", idempotency_key="coverage-job"))
        for index, spec in enumerate(specs):
            db.add(RuleProposalModel(
                id=f"coverage-proposal-{index}", dataset_id=dataset_id, title=spec["type"], description="Replay",
                severity="MEDIUM", rule_type=spec["type"], rule_spec=json.dumps(spec),
                evidence_refs="[]", evidence_summary="Fixture", confidence=0.8, model_name="test",
            ))
            db.add(RuleVersionModel(id=f"coverage-rule-{index}", rule_proposal_id=f"coverage-proposal-{index}",
                                    dataset_id=dataset_id, rule_spec=json.dumps(spec), status="APPROVED"))
        db.add(DqRunModel(id="coverage-run", job_id="coverage-job", dataset_id=dataset_id,
                          rule_ids=json.dumps([f"coverage-rule-{i}" for i in range(len(specs))])))
        db.commit()
    run_dq_checks("coverage-job", "coverage-run", trigger_anomaly=False)
    with Session(get_engine()) as db:
        rows = {r.rule_id: r for r in db.query(DqResultModel).filter_by(run_id="coverage-run")}
        assert len(rows) == 4
        assert [rows[f"coverage-rule-{i}"].status for i in range(4)] == ["FAIL", "FAIL", "ERROR", "FAIL"]
        assert json.loads(rows["coverage-rule-1"].failed_row_ids) == ["r2"]
        assert "immutable schema" in rows["coverage-rule-2"].error_message
        assert db.get(DqRunModel, "coverage-run").total_failed == 3
