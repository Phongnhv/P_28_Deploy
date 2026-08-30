"""Deterministic LangChain-compatible LLM used only by served-path EvalGate CI."""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

_LOCK = threading.Lock()


def _text(messages) -> str:
    if isinstance(messages, str):
        return messages
    return "\n".join(str(getattr(item, "content", item)) for item in messages)


def _json_lists(text: str) -> list[list]:
    decoder = json.JSONDecoder()
    found: list[list] = []
    for index, char in enumerate(text):
        if char != "[":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except (ValueError, json.JSONDecodeError):
            continue
        if isinstance(value, list):
            found.append(value)
    return found


def _trace(schema: str) -> None:
    value = os.getenv("EVALGATE_LLM_TRACE_PATH", "")
    if not value:
        return
    path = Path(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {"timestamp": datetime.now(UTC).isoformat(), "event": "llm_invocation",
             "provider": "evalgate", "model": "structured-fake-v1", "schema": schema}
    with _LOCK, path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")


class _Structured:
    def __init__(self, schema, include_raw: bool) -> None:
        self.schema = schema
        self.include_raw = include_raw

    async def ainvoke(self, messages):
        name = self.schema.__name__
        _trace(name)
        text = _text(messages)
        if name == "TableSemanticContract":
            value = self.schema(table_name="source_rows", domain="evaluation",
                                table_purpose="Deterministic served-path evaluation",
                                columns=[], relationships=[], business_assumptions=[])
        elif name == "InferredDictionaryTable":
            value = self.schema(table_name="source_rows", description="Evaluation dataset",
                                columns=[], business_rules=[])
        elif name == "HypothesisResponse":
            value = self.schema(hypotheses=[])
        elif name == "TableRuleProposal":
            candidates = next(
                (
                    items
                    for items in reversed(_json_lists(text))
                    if items
                    and all(
                        isinstance(item, dict)
                        and "candidate_id" in item
                        and "selection_reason" in item
                        and "evidence" in item
                        for item in items
                    )
                ),
                [],
            )
            rules = []
            for candidate in candidates[:5]:
                params = candidate.get("parameters") or {}
                refs = [str(ref) for ref in candidate.get("evidence", [])] or ["profile.row_count"]
                provenance = [{"parameter_name": key, "source_type": "POLICY" if refs[0].startswith("policy") else "DATA_PROFILE",
                               "source_ref": refs[0], "derivation_method": "copied from deterministic candidate"}
                              for key, val in params.items() if val is not None and val != []]
                rules.append({
                    "candidate_id": candidate["candidate_id"], "column": candidate.get("column"),
                    "rule_type": candidate["rule_type"], "parameters": params,
                    "rule_name": f"Validate {candidate['candidate_id']}",
                    "business_rationale": "Candidate is backed by persisted aggregate evidence.",
                    "proposal_basis": "POLICY" if any(ref.startswith("policy") for ref in refs) else "DATA_PROFILE",
                    "selected_evidence_refs": refs, "parameter_provenance": provenance,
                    "assumptions": [], "confidence": {"overall": 0.8, "evidence_strength": 0.8,
                    "business_support": 0.8, "sample_representativeness": 0.8,
                    "explanation": "Deterministic CI structured response"},
                    "severity": "HIGH", "dimension": candidate.get("dimension", "VALIDITY"),
                    "rule_description": f"Validate {candidate['candidate_id']} using approved evidence.",
                    "ai_reasoning": "The proposal copies the server-owned candidate and evidence without invention.",
                })
            value = self.schema.model_validate({"table": "source_rows", "rules": rules})
        else:
            value = self.schema.model_validate({})
        return {"parsed": value, "raw": None, "parsing_error": None} if self.include_raw else value

    def invoke(self, messages):
        import asyncio
        return asyncio.run(self.ainvoke(messages))


class DeterministicEvalLLM:
    def with_structured_output(self, schema, include_raw: bool = False):
        return _Structured(schema, include_raw)

    async def ainvoke(self, messages):
        _trace("text")
        return SimpleNamespace(content="Deterministic EvalGate served-path report.")

    def invoke(self, messages):
        return SimpleNamespace(content="Deterministic EvalGate served-path report.")
