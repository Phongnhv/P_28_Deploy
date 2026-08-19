"""Infer a normalized data dictionary when the user did not provide one."""

from __future__ import annotations

import json
import logging
from typing import Any
from datetime import datetime
from pathlib import Path

from src.models.data_dictionary import InferredDictionaryTable
from src.agents.nodes.templates import data_dictionary_generator_prompt
from src.agents.state import AgentState
from src.agents.tools.profile_digest import split_digest_by_table
from src.config import get_settings
from src.services.llm import get_llm

logger = logging.getLogger(__name__)

async def data_dictionary_generator_node(state: AgentState) -> dict[str, Any]:
    """Create conservative metadata from the profile and optional domain hint."""
    if state.get("normalized_data_dictionary"):
        return {"data_dictionary_source": "supplied"}

    digest = state.get("dataset_profile_digest") or {}
    per_table = split_digest_by_table(digest)
    if not per_table:
        return {"error": "Không thể suy luận data dictionary vì profile rỗng"}

    metadata = state.get("metadata") or {}
    domain_hint = metadata.get("domain_hint", "")
    settings = get_settings()
    llm = get_llm(settings.llm_provider, temperature=0.1)
    structured = llm.with_structured_output(InferredDictionaryTable)
    tables: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    
    for table_name, table_digest in per_table.items():
        prompt = data_dictionary_generator_prompt.format_messages(
            table_name=table_name,
            table_digest=json.dumps(table_digest, ensure_ascii=False),
            domain_hint=domain_hint or "None",
        )
        try:
            result = await structured.ainvoke(prompt)
            result.table_name = table_name
            tables[table_name] = result.model_dump()
        except Exception as exc:
            logger.exception("Dictionary inference failed for %s", table_name)
            errors.append(f"Table {table_name}: {exc}")

    if not tables:
        return {"error": "Không thể suy luận data dictionary: " + "; ".join(errors)}

    result_payload = {"tables": tables, "inferred": True}

    # Xuất trace JSON data dictionary
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = state.get("rule_run_id") or "test_run"
    try:
        out_dir = getattr(settings, "output_dir", None)
        res_dir = getattr(settings, "results_dir", None)
        base_dir = out_dir if isinstance(out_dir, (str, Path)) else (res_dir if isinstance(res_dir, (str, Path)) else "./output")
        dict_dir = Path(base_dir) / "dictionary"
        dict_dir.mkdir(parents=True, exist_ok=True)
        dump_file = dict_dir / f"debug_inferred_dictionary_{timestamp}_{run_id}.json"
        dump_payload = {
            "run_id": run_id,
            "generated_at": datetime.now().isoformat(),
            "dictionary": result_payload,
            "errors": errors,
        }
        dump_file.write_text(json.dumps(dump_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"Đã xuất trace inferred dictionary ra {dump_file}")
    except Exception as e:
        logger.warning(f"Không thể ghi file trace inferred dictionary: {e}")

    return {
        "normalized_data_dictionary": result_payload,
        "data_dictionary_source": "inferred",
        "data_dictionary_inference_errors": errors,
    }
