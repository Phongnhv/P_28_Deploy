"""Tests for DeterministicEvalLLM compatibility with LangChain and DeepAgents."""

from __future__ import annotations

from deepagents._models import resolve_model
from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel

from src.services.deterministic_eval_llm import DeterministicEvalLLM


class DummySchema(BaseModel):
    name: str = "test"


def test_deterministic_llm_is_base_chat_model():
    """DeterministicEvalLLM must be an instance of BaseChatModel."""
    llm = DeterministicEvalLLM()
    assert isinstance(llm, BaseChatModel)


def test_deterministic_llm_resolves_in_deepagents():
    """resolve_model must recognize DeterministicEvalLLM and not crash on string methods."""
    llm = DeterministicEvalLLM()
    resolved = resolve_model(llm)
    assert resolved is llm


def test_deterministic_llm_structured_output_roundtrip():
    """with_structured_output must return structured parser."""
    llm = DeterministicEvalLLM()
    structured = llm.with_structured_output(DummySchema)
    res = structured.invoke("hello")
    assert isinstance(res, DummySchema)
