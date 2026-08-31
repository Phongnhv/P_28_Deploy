"""Infer a normalized data dictionary when the user did not provide one."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from src.agents.nodes.templates import data_dictionary_generator_prompt
from src.agents.state import AgentState
from src.agents.tools.profile_digest import split_digest_by_table
from src.config import get_settings
from src.models.data_dictionary import InferredDictionaryTable
from src.services.llm import get_llm

logger = logging.getLogger(__name__)


def _heuristic_dictionary_for_table(table_name: str, table_digest: dict[str, Any]) -> dict[str, Any]:
    """Sinh data dictionary heuristic từ profile digest khi LLM không khả dụng."""
    cols_meta = []
    for col in table_digest.get("columns", []):
        col_name = str(col.get("name", ""))
        if not col_name:
            continue
        dtype = str(col.get("type", "")).lower()
        null_pct = float(col.get("null_pct") or 0.0)
        is_unique = bool(col.get("is_unique_full_table")) or (col_name.lower() in ("id", f"{table_name}_id", "source_row_id"))

        if is_unique or col_name.lower().endswith("_id"):
            sem_type = "identifier"
            role = "primary_key" if is_unique else "foreign_key"
            nullable = False
        elif any(k in dtype for k in ("time", "date")) or any(col_name.lower().endswith(k) for k in ("_at", "_date", "_time")):
            sem_type = "timestamp"
            role = "event_timestamp"
            nullable = null_pct > 0
        elif any(k in dtype for k in ("int", "float", "numeric", "decimal", "double")):
            if any(k in col_name.lower() for k in ("price", "amount", "fare", "cost", "fee", "tip", "tax", "total")):
                sem_type = "currency"
                role = "transaction_amount"
            else:
                sem_type = "numeric"
                role = "measurement"
            nullable = null_pct > 0
        elif col.get("is_categorical") or any(k in dtype for k in ("char", "text", "str")):
            sem_type = "category" if col.get("is_categorical") else "text"
            role = "category_code" if col.get("is_categorical") else "description"
            nullable = null_pct > 0
        else:
            sem_type = "unknown"
            role = "attribute"
            nullable = null_pct > 0

        cols_meta.append({
            "name": col_name,
            "description": f"Trường dữ liệu {col_name} ({dtype})",
            "semantic_type": sem_type,
            "business_role": role,
            "nullable_expected": nullable,
            "governance_notes": [],
        })

    return {
        "table_name": table_name,
        "description": f"Bảng dữ liệu {table_name}",
        "columns": cols_meta,
        "business_rules": [],
    }


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
    tables: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    try:
        llm = get_llm(settings.llm_provider, temperature=0.1)
        structured = llm.with_structured_output(InferredDictionaryTable)

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
                logger.warning("LLM dictionary inference failed for %s (%s). Sử dụng heuristic fallback.", table_name, exc)
                errors.append(f"Table {table_name}: {exc}")
                tables[table_name] = _heuristic_dictionary_for_table(table_name, table_digest)
    except Exception as general_llm_exc:
        logger.warning("Không thể khởi tạo hoặc gọi LLM để suy luận dictionary (%s). Sử dụng heuristic cho toàn bộ bảng.", general_llm_exc)
        errors.append(str(general_llm_exc))
        for table_name, table_digest in per_table.items():
            tables[table_name] = _heuristic_dictionary_for_table(table_name, table_digest)

    if not tables:
        for table_name, table_digest in per_table.items():
            tables[table_name] = _heuristic_dictionary_for_table(table_name, table_digest)

    is_fallback = bool(errors and len(errors) == len(per_table))
    result_payload = {"tables": tables, "inferred": True, "heuristic_fallback": is_fallback}

    # Xuất trace JSON data dictionary
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = state.get("rule_run_id") or "test_run"
    try:
        out_dir = getattr(settings, "output_dir", None)
        res_dir = getattr(settings, "results_dir", None)
        base_dir = (
            out_dir
            if isinstance(out_dir, (str, Path))
            else (res_dir if isinstance(res_dir, (str, Path)) else "./output")
        )
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
        "data_dictionary_source": "heuristic_fallback" if is_fallback else "inferred",
        "data_dictionary_inference_errors": errors,
    }

