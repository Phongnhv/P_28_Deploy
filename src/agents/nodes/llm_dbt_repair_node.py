from __future__ import annotations

import json
import re

import yaml

from src.agents.nodes.dbt_validation import validate_dbt_yaml_structure
from src.agents.nodes.templates import dbt_repair_prompt
from src.agents.state import AgentState
from src.config import get_settings
from src.services.llm import get_llm


def _extract_yaml(value: str) -> str:
    match = re.fullmatch(r"\s*```(?:ya?ml)?\s*([\s\S]*?)```\s*", value, re.IGNORECASE)
    return (match.group(1) if match else value).strip() + "\n"


def _approved_scope(rules: list[dict]) -> set[tuple[str, str | None]]:
    return {(str(rule.get("table_name") or "profile_input"), rule.get("column") or "source_row_id") for rule in rules}


def _yaml_scope(content: str) -> set[tuple[str, str | None]]:
    parsed = validate_dbt_yaml_structure(content)
    scope: set[tuple[str, str | None]] = set()
    for model in parsed["models"]:
        if model.get("tests"):
            scope.add((model["name"], None))
        for column in model.get("columns", []):
            scope.add((model["name"], column["name"]))
    return scope


async def llm_dbt_repair_node(state: AgentState) -> dict:
    attempts = int(state.get("dbt_validation_attempts", 0))
    if state.get("dbt_validation_valid") or attempts >= 3:
        return {}
    attempts += 1
    original = str(state.get("generated_dbt_yaml") or "")
    history = list(state.get("dbt_repair_history", []))
    errors = list(state.get("test_generation_errors", []))
    approved_rules = list(state.get("approved_rules", []))
    entry = {"attempt": attempts, "validation_error": state.get("dbt_validation_error")}
    try:
        llm = get_llm(get_settings().llm_provider, temperature=0.0)
        messages = dbt_repair_prompt.format_messages(
            approved_rules_json=json.dumps(approved_rules, ensure_ascii=False, indent=2),
            dbt_yaml=original,
            validation_error=state.get("dbt_validation_error") or "Unknown validation error",
        )
        response = await llm.ainvoke(messages)
        repaired = _extract_yaml(str(response.content))
        yaml.safe_load(repaired)
        repaired_scope = _yaml_scope(repaired)
        allowed_scope = _approved_scope(approved_rules)
        if not repaired_scope.issubset(allowed_scope):
            raise ValueError("repaired YAML adds models or columns outside approved rules")
        entry["repaired_yaml"] = repaired
        history.append(entry)
        return {
            "generated_dbt_yaml": repaired,
            "dbt_validation_attempts": attempts,
            "dbt_validation_valid": False,
            "dbt_validation_error": None,
            "dbt_repair_history": history,
            "test_generation_errors": errors,
        }
    except Exception as exc:
        message = f"dbt YAML repair attempt {attempts} failed: {exc}"
        entry["repair_error"] = message
        history.append(entry)
        errors.append(message)
        return {
            "dbt_validation_attempts": attempts,
            "dbt_validation_valid": False,
            "dbt_validation_error": message,
            "dbt_repair_history": history,
            "test_generation_errors": errors,
        }
