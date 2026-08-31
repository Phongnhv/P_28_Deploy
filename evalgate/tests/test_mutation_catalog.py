"""Keep every CRITICAL mutation mapped to an executable detector test."""

from __future__ import annotations

from pathlib import Path

import yaml

from evalgate.gates.gate6_governance import hitl_integrity

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "evalgate" / "mutations" / "catalog.yaml"


def test_every_critical_mutation_has_a_real_detector_test():
    document = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))
    test_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "evalgate" / "tests").glob("test_*.py")
    )
    critical = [item for item in document["mutations"] if item["severity"] == "CRITICAL"]
    assert critical
    for mutation in critical:
        assert f"def {mutation['detector_test']}(" in test_source, mutation["id"]


def test_critical_mutation_ids_are_unique():
    document = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))
    ids = [item["id"] for item in document["mutations"]]
    assert len(ids) == len(set(ids))


def test_hitl_probe_detects_unaudited_transitions(monkeypatch):
    probes = [
        hitl_integrity.TransitionProbe(
            transition="APPROVE",
            performed=True,
            audit_events=0,
            reviewer_recorded="steward-1",
            detail="mutation fixture",
        ),
        hitl_integrity.TransitionProbe(
            transition="PUBLISH",
            performed=True,
            audit_events=0,
            reviewer_recorded=None,
            detail="mutation fixture",
        ),
    ]
    monkeypatch.setattr(hitl_integrity, "_run_probe", lambda _tmp_path: probes)

    result = hitl_integrity.evaluate(write_evidence=False)

    assert result.status == "FAIL"
    assert any(finding.id == "HG-G2" for finding in result.critical_findings)
