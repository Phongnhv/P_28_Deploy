"""Deterministic LangChain-compatible LLM used only by served-path EvalGate CI."""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

from langchain_core.language_models.chat_models import SimpleChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

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


#: Column ``role`` values the profile digest emits, mapped onto the semantic
#: vocabulary the contract expects.
_ROLE_TO_SEMANTIC = {
    "numeric": ("numeric", "measure"),
    "datetime": ("timestamp", "event_time"),
    "categorical": ("category", "dimension"),
    "identifier": ("identifier", "primary_key"),
}


def _semantic_columns(text: str) -> list[dict]:
    """Rebuild the contract's columns from the profile digest inside the prompt.

    An empty column list is rejected by ``confirm_semantic_contract`` -- correctly,
    since a contract that describes nothing cannot govern anything. So the double has
    to answer with the columns it was actually shown, the same way it already lifts
    rule candidates out of the proposal prompt, rather than inventing a shape the
    product would refuse.
    """
    columns = next(
        (
            items
            for items in _json_lists(text)
            if items
            and all(
                isinstance(item, dict) and "name" in item and "type" in item
                for item in items
            )
        ),
        [],
    )
    rebuilt: list[dict] = []
    for column in columns:
        semantic_type, business_role = _ROLE_TO_SEMANTIC.get(
            str(column.get("role", "")), ("text", "attribute")
        )
        rebuilt.append({
            "name": str(column["name"]),
            "semantic_type": semantic_type,
            "business_role": business_role,
            # The digest carries an observed null percentage; treating a column that
            # was never null as non-nullable is the one inference worth making here,
            # because NOT_NULL candidates depend on it.
            "nullable_expected": float(column.get("null_pct") or 0.0) > 0.0,
            "confidence": 1.0,
            "description": f"Deterministic evaluation column derived from {column.get('type')}",
        })
    return rebuilt


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


#: Schemas whose answer is the same shape. The deep agent asks for
#: ``CandidateTableRuleProposal``; the one-shot structured path asks for
#: ``CandidateTableRuleDraft``; older callers ask for ``TableRuleProposal``.
_RULE_PROPOSAL_SCHEMAS = frozenset(
    {"TableRuleProposal", "CandidateTableRuleDraft", "CandidateTableRuleProposal"}
)


def payload_for(name: str, text: str) -> dict | None:
    """The raw arguments for a schema this double knows, keyed by **name**.

    Name rather than class, because the tool-calling path never sees the class. The
    agent binds the response schema as an ordinary ``StructuredTool`` whose
    ``args_schema`` is not the model, so a class-based lookup found nothing and the
    double answered with prose. Keying on the name also makes the double ignore the
    agent's own tools (``ls``, ``read_file``, ``task``) without having to enumerate them.

    Returns ``None`` for anything unknown, which the callers read as "answer as text".
    """
    if name == "TableSemanticContract":
        return {
            "table_name": "source_rows", "domain": "evaluation",
            "table_purpose": "Deterministic served-path evaluation",
            "columns": _semantic_columns(text),
            "relationships": [], "business_assumptions": [],
        }
    if name == "InferredDictionaryTable":
        return {
            "table_name": "source_rows", "description": "Evaluation dataset",
            "columns": [], "business_rules": [],
        }
    if name == "HypothesisResponse":
        return {"hypotheses": []}
    if name == "AnomalyInvestigationResponse":
        # Deliberately free of digits. report_grounding_probe_v1 resolves every number
        # in the narrative against the execution results, and a double that invented a
        # figure would manufacture the exact defect that probe exists to catch.
        return {
            "overall_assessment": (
                "Deterministic evaluation double: no investigative claim is made."
            ),
            "investigation_summary": (
                "Produced by the deterministic served-path model; hypotheses are left "
                "empty rather than invented."
            ),
            "hypotheses": [],
        }
    if name in _RULE_PROPOSAL_SCHEMAS:
        return {"table": "source_rows", "rules": _rules_from_candidates(text)}
    return None


def _evidence_refs_of(candidate: dict) -> list | None:
    """The candidate's evidence list, under whichever name it arrived.

    ``DashboardRuleCandidate.to_prompt_requirement`` emits ``evidence``; by the time the
    candidate reaches the prompt, ``_dedupe_candidates`` has renamed it to
    ``evidence_items``. This double matched only on ``evidence``, so every candidate list
    failed the filter and it answered with zero rules -- which is exactly what
    "Model covered 0 of 20 candidates" had been reporting since the first CI run, and why
    the product fell through to Heuristic Rule Promotion every time.

    Returns ``None`` when neither key is present, which the caller reads as "this list is
    not a candidate list".
    """
    for key in ("evidence_items", "evidence"):
        value = candidate.get(key)
        if isinstance(value, list):
            return value
    return None


def _rules_from_candidates(text: str) -> list[dict]:
    """Lift the server-owned candidates out of the prompt and dress them as rules."""
    candidates = next(
        (
            items
            for items in reversed(_json_lists(text))
            if items
            and all(
                isinstance(item, dict)
                and "candidate_id" in item
                and "selection_reason" in item
                and _evidence_refs_of(item) is not None
                for item in items
            )
        ),
        [],
    )
    rules: list[dict] = []
    # Every candidate, not the first five. The cap was invisible while the deep-agent
    # path was broken; once it worked the double answered about five of twenty and the
    # proposer logged "covered 5 of 20". A double that silently drops three quarters of
    # the question is not a stand-in for a competent model.
    for candidate in candidates:
        params = candidate.get("parameters") or {}
        refs = [str(ref) for ref in (_evidence_refs_of(candidate) or [])] or ["profile.row_count"]
        provenance = [
            {
                "parameter_name": key,
                "source_type": "POLICY" if refs[0].startswith("policy") else "DATA_PROFILE",
                "source_ref": refs[0],
                "derivation_method": "copied from deterministic candidate",
            }
            for key, val in params.items() if val is not None and val != []
        ]
        rules.append({
            "candidate_id": candidate["candidate_id"], "column": candidate.get("column"),
            "rule_type": candidate["rule_type"], "parameters": params,
            "rule_name": f"Validate {candidate['candidate_id']}",
            "business_rationale": "Candidate is backed by persisted aggregate evidence.",
            "proposal_basis": (
                "POLICY" if any(ref.startswith("policy") for ref in refs) else "DATA_PROFILE"
            ),
            "selected_evidence_refs": refs, "parameter_provenance": provenance,
            "assumptions": [],
            "confidence": {
                "overall": 0.8, "evidence_strength": 0.8, "business_support": 0.8,
                "sample_representativeness": 0.8,
                "explanation": "Deterministic CI structured response",
            },
            "severity": "HIGH", "dimension": candidate.get("dimension", "VALIDITY"),
            "rule_description": f"Validate {candidate['candidate_id']} using approved evidence.",
            "ai_reasoning": (
                "The proposal copies the server-owned candidate and evidence without invention."
            ),
        })
    return rules


class _Structured:
    def __init__(self, schema, include_raw: bool) -> None:
        self.schema = schema
        self.include_raw = include_raw

    def build(self, messages):
        """Construct the structured value synchronously.

        Split out of ``ainvoke`` because the agent's tool-calling loop needs this from
        inside a running event loop, where the old ``asyncio.run`` bridge raises
        RuntimeError. Both entry points now share one implementation.
        """
        name = self.schema.__name__
        payload = payload_for(name, _text(messages))
        if payload is not None:
            value = self.schema.model_validate(payload)
            return (
                {"parsed": value, "raw": None, "parsing_error": None}
                if self.include_raw else value
            )
        text = _text(messages)
        _trace(name)
        if name == "TableSemanticContract":
            value = self.schema(table_name="source_rows", domain="evaluation",
                                table_purpose="Deterministic served-path evaluation",
                                columns=_semantic_columns(text),
                                relationships=[], business_assumptions=[])
        elif name == "InferredDictionaryTable":
            value = self.schema(table_name="source_rows", description="Evaluation dataset",
                                columns=[], business_rules=[])
        elif name == "HypothesisResponse":
            value = self.schema(hypotheses=[])
        # The proposer asks for the draft schema, which restores server-owned fields
        # afterwards; the strict name is kept so an older caller still resolves here.
        elif name in {"TableRuleProposal", "CandidateTableRuleDraft"}:
            candidates = next(
                (
                    items
                    for items in reversed(_json_lists(text))
                    if items
                    and all(
                        isinstance(item, dict)
                        and "candidate_id" in item
                        and "selection_reason" in item
                        and _evidence_refs_of(item) is not None
                        for item in items
                    )
                ),
                [],
            )
            rules = []
            for candidate in candidates[:5]:
                params = candidate.get("parameters") or {}
                refs = [str(ref) for ref in (_evidence_refs_of(candidate) or [])] or ["profile.row_count"]
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
        return self.build(messages)

    async def ainvoke(self, messages):
        return self.build(messages)


def _tool_name(tool) -> str:
    """The name the agent will match a tool call against.

    ``ToolStrategy`` binds the response schema as a ``StructuredTool`` named after the
    model class, and its ``args_schema`` is *not* that class -- which is why an earlier
    attempt to recognise the tool by its schema object found nothing and the double
    answered every deep-agent turn with prose.
    """
    name = getattr(tool, "name", None)
    if isinstance(name, str) and name:
        return name
    if isinstance(tool, type):
        return tool.__name__
    return str(tool)


class DeterministicEvalLLM(SimpleChatModel):
    """A model double that answers through **both** paths LangChain offers.

    ``with_structured_output`` was the only one implemented, which was enough for the
    nodes that call it directly and useless for the two that go through
    ``create_deep_agent``. Those build a tool-calling loop: the response schema is bound
    as a tool (``ToolStrategy``) and the agent waits for an ``AIMessage`` carrying a
    matching ``tool_calls`` entry. This class returned a plain text message and threw the
    bound tools away, so the loop ended immediately with prose where a structured answer
    was expected.

    The consequences were not subtle and not local. ``anomaly_investigation_node`` ran
    ``json.loads`` over "Deterministic EvalGate served-path report." and raised; the rule
    proposer logged *"No LLM narrative for any of the N server candidates"* and fell back
    to Heuristic Rule Promotion. Every ai_quality number EvalGate published was therefore
    measuring the fallback rather than the agent -- while the report named the agent.

    Emitting the tool call closes that. The payload comes from ``_Structured``, which was
    already correct; only the envelope was missing.
    """

    #: Tools bound by the agent loop, remembered rather than discarded.
    bound_tools: list = []

    @property
    def _llm_type(self) -> str:
        return "deterministic-eval"

    def _call(self, messages, stop=None, run_manager=None, **kwargs) -> str:
        _trace("text")
        return "Deterministic EvalGate served-path report."

    def with_structured_output(self, schema, include_raw: bool = False):
        return _Structured(schema, include_raw)

    def bind_tools(self, tools, **kwargs):
        """Remember the tools instead of dropping them.

        Returns a copy: the agent binds different tool sets at different steps, and a
        model that mutated itself would answer a later call with an earlier binding.
        """
        clone = self.__class__()
        clone.bound_tools = list(tools or [])
        return clone

    def _structured_tool_call(self, messages) -> dict | None:
        """A tool call satisfying the bound response schema, or None.

        The agent binds its own tools alongside the response schema, so the name lookup
        does double duty: it builds the answer and it declines to answer as ``ls``.
        """
        text = _text(messages)
        for tool in self.bound_tools:
            name = _tool_name(tool)
            payload = payload_for(name, text)
            if payload is None:
                continue
            _trace(name)
            return {
                "name": name,
                "args": payload,
                "id": f"evalgate-{name}",
                "type": "tool_call",
            }
        return None

    def _answer(self, messages) -> AIMessage:
        call = self._structured_tool_call(messages)
        if call is None:
            _trace("text")
            return AIMessage(content="Deterministic EvalGate served-path report.")
        # Content stays empty: a message carrying both prose and a tool call invites a
        # caller to parse the prose, which is exactly the failure this replaces.
        return AIMessage(content="", tool_calls=[call])

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._answer(messages))])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    async def ainvoke(self, messages, config=None, **kwargs):
        return self._answer(messages)

    def invoke(self, messages, config=None, **kwargs):
        return self._answer(messages)
