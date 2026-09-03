"""Offline replay of a pinned audit: candidates -> synthetic narrative -> executor.

Run with python -m scripts.replay_rule_coverage --audit PATH --source PATH --output PATH.
This does not call an LLM or mutate the application database. It checks mechanics,
coverage and column scope; narrative/business quality needs a separate live run.
"""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from src.agents.nodes.rule_candidate_builder_node import rule_candidate_builder_node
from src.agents.nodes.rule_proposer_node import _bind_proposal_to_candidates, _candidate_batches, _stamp_rule
from src.config import get_settings
from src.services.dashboard_agent_workflow import (
    _normalise_graph_rules,
    _proposal_evidence_from_versioned_snapshot,
    _table_keyed_contract,
)
from src.services.job_runner import _uploaded_rule_outcome


def replay(audit_path: Path, source_path: Path, output_path: Path) -> dict:
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    snapshot = next(a["payload"] for a in audit["artifacts"] if a["type"] == "PROFILE_SNAPSHOT")
    checksum = hashlib.sha256(source_path.read_bytes()).hexdigest()
    expected = (snapshot.get("source_binding") or {}).get("checksum")
    if not expected or checksum != expected:
        raise ValueError("Source does not match the pinned profile checksum")
    evidence = _proposal_evidence_from_versioned_snapshot(
        SimpleNamespace(id=audit["dataset"], manifest_version="versioned-v1"), snapshot,
    )
    digest = evidence.to_agent_digest()
    # Preserve the reviewed contract's optional-column filtering when available.
    semantic = next((a["payload"] for a in reversed(audit["artifacts"]) if a["type"] == "SEMANTIC_CONTRACT"), {})
    semantic = _table_keyed_contract(semantic, evidence.dataset_id)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    get_settings().output_dir = str(output_path.parent / "rule-coverage-replay")
    candidates = rule_candidate_builder_node({"dataset_profile_digest": digest, "semantic_contract": semantic})["rule_candidates"]
    stamped = []
    for batch in _candidate_batches(candidates, 20):
        draft = {"table": evidence.dataset_id, "rules": [{
            "candidate_id": c["candidate_id"], "column": c["column"], "rule_type": c["rule_type"],
            "rule_name": f"Check {c['column']}", "business_rationale": "Offline mechanical replay",
            "proposal_basis": "DATA_PROFILE", "severity": "MEDIUM", "dimension": c["dimension"],
            "confidence": {"overall": 0.8, "evidence_strength": 0.8, "business_support": 0.8,
                           "sample_representativeness": 1.0, "explanation": "Synthetic replay narrative"},
            "rule_description": f"Check {c['column']}", "ai_reasoning": f"Pinned evidence for {c['column']}",
        } for c in batch]}
        bound = _bind_proposal_to_candidates(evidence.dataset_id, draft, batch)
        stamped.extend(_stamp_rule(r, evidence.dataset_id, "offline-replay", requirement=c,
                                   table_digest=digest[evidence.dataset_id]) for r, c in zip(bound.rules, batch))
    proposals = _normalise_graph_rules(stamped, evidence)
    if not candidates or len(proposals) != len(candidates):
        raise ValueError(f"Coverage lost: {len(candidates)} candidates -> {len(proposals)} proposals")
    frame = pd.read_parquet(source_path) if source_path.suffix.lower() == ".parquet" else pd.read_csv(source_path)
    if len(frame) != evidence.row_count or set(frame.columns) - {"source_row_id"} != {c.name for c in evidence.columns}:
        raise ValueError("Source row count/schema does not match the pinned profile")
    results = []
    for proposal in proposals:
        outcome = _uploaded_rule_outcome(source_path, proposal.rule_type, proposal.rule_spec, frame=frame)
        results.append({"rule_type": proposal.rule_type, "spec": proposal.rule_spec,
                        **{key: outcome[key] for key in ("status", "checked_count", "failed_count", "violation_rate")}})
    report = {"mode": "offline_synthetic_narratives_no_llm", "dataset_id": evidence.dataset_id,
              "source_checksum": checksum, "row_count": len(frame),
              "checklist_count": len(digest[evidence.dataset_id]["dashboard_rule_candidates"]),
              "builder_count": len(candidates), "accepted_count": len(proposals),
              "executed_count": len(results), "by_type": dict(Counter(p.rule_type for p in proposals)),
              "statuses": dict(Counter(r["status"] for r in results)), "results": results}
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return {key: value for key, value in report.items() if key != "results"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(replay(args.audit, args.source, args.output), indent=2))
