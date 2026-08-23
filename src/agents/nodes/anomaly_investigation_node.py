"""LangGraph node backed by a Deep Agent for anomaly investigation."""

from __future__ import annotations

import json
from typing import Any

from src.agents.nodes.templates import (
    ANOMALY_INVESTIGATION_SYSTEM_PROMPT,
    ANOMALY_INVESTIGATION_USER_PROMPT,
)
from src.agents.state import AnomalyGraphState
from src.agents.tools.anomaly_investigation_tools import ANOMALY_INVESTIGATION_TOOLS
from src.config import get_settings
from src.models.rule_schemas import AnomalyInvestigationResponse
from src.services.llm import get_llm


def _message_content(result: Any) -> Any:
    if isinstance(result, dict) and "structured_response" in result:
        return result["structured_response"]
    messages = result.get("messages", []) if isinstance(result, dict) else []
    return messages[-1].content if messages else result


async def anomaly_investigation_node(state: AnomalyGraphState) -> dict:
    """Investigate detector output while preserving the authoritative decision."""
    decision = state.get("anomaly_decision") or {}
    if decision.get("decision", "NORMAL") not in {"WATCH", "ANOMALY", "CRITICAL"}:
        return {"hypotheses": [], "hypothesis_status": "NOT_REQUIRED"}

    settings = get_settings()
    model = get_llm(settings.llm_provider, temperature=0.1)
    structured_model = model.with_structured_output(AnomalyInvestigationResponse)
    try:
        from deepagents import create_deep_agent
    except ImportError as exc:
        raise RuntimeError("Install deepagents before running anomaly investigation") from exc

    agent = create_deep_agent(
        model=structured_model,
        tools=ANOMALY_INVESTIGATION_TOOLS,
        system_prompt=ANOMALY_INVESTIGATION_SYSTEM_PROMPT,
    )
    prompt = ANOMALY_INVESTIGATION_USER_PROMPT.format(
        anomaly_run_id=state.get("anomaly_run_id", ""),
        execution_run_id=state.get("execution_run_id", ""),
        dataset_id=state.get("dataset_id", ""),
        anomaly_decision=json.dumps(decision, ensure_ascii=False, default=str),
        signal_observations=json.dumps(state.get("signal_observations", []), ensure_ascii=False, default=str),
        current_features=json.dumps(state.get("current_features", {}), ensure_ascii=False, default=str),
        historical_features=json.dumps(state.get("historical_features", {}), ensure_ascii=False, default=str),
        prior_context=json.dumps(state.get("metadata", {}), ensure_ascii=False, default=str),
    )
    result = await agent.ainvoke({"messages": [{"role": "user", "content": prompt}]})
    content = _message_content(result)
    response = content if isinstance(content, AnomalyInvestigationResponse) else AnomalyInvestigationResponse.model_validate(content)
    return {
        "hypotheses": [item.model_dump() for item in response.hypotheses],
        "hypothesis_status": "SUCCEEDED",
        "hypothesis_validation": response.model_dump(),
    }


__all__ = ["AnomalyInvestigationResponse", "anomaly_investigation_node"]
