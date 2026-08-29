import asyncio
import json
import logging

from src.agents.nodes.templates import prompt_customizer_prompt
from src.agents.state import AgentState
from src.config import get_settings
from src.services.llm import get_llm

logger = logging.getLogger(__name__)


def _merge_table_business_contexts(state: AgentState) -> dict[str, str]:
    """Merge new and legacy context fields, with the new field taking precedence."""
    legacy = state.get("specialized_system_prompts") or {}
    current = state.get("table_business_contexts") or {}
    return {
        **(legacy if isinstance(legacy, dict) else {}),
        **(current if isinstance(current, dict) else {}),
    }


async def _generate_business_context_for_table(
    table_name: str,
    semantic_contract: dict,
    llm,
    semaphore: asyncio.Semaphore,
) -> str:
    async with semaphore:
        logger.info(f"Generating table business context for table: {table_name}")
        messages = prompt_customizer_prompt.format_messages(
            table_name=table_name, semantic_contract=json.dumps(semantic_contract, ensure_ascii=False)
        )
        res = await llm.ainvoke(messages)
        content = res.content.strip()

        # Dọn dẹp nếu LLM vô tình bọc trong markdown block
        if content.startswith("```"):
            lines = content.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()

        return content


# Alias để giữ tương thích ngược nếu có module gọi trực tiếp
_customize_prompt_for_table = _generate_business_context_for_table


async def prompt_customizer_node(state: AgentState) -> dict:
    """Prompt Customizer Node (Table Business Context Generator).

    Tổng hợp bản tóm tắt Ngữ cảnh Nghiệp vụ (Table Business Context) cho từng bảng
    dựa trên Hợp đồng ngữ nghĩa (Semantic Contract) đã được xác nhận.
    """
    contract = state.get("semantic_contract")
    if not contract:
        logger.warning("Không tìm thấy semantic_contract trong state.")
        return {}

    tables_contract = contract.get("tables", {})
    if not tables_contract:
        return {}

    # 1. Setup LLM
    settings = get_settings()
    llm = get_llm(settings.llm_provider, temperature=0.3)

    # 2. Fan-out
    semaphore = asyncio.Semaphore(settings.rule_proposer_concurrency)
    table_names = list(tables_contract.keys())

    tasks = [
        _generate_business_context_for_table(
            table_name=t, semantic_contract=tables_contract[t], llm=llm, semaphore=semaphore
        )
        for t in table_names
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 3. Tạo dictionary lưu ngữ cảnh nghiệp vụ theo từng bảng
    business_contexts = _merge_table_business_contexts(state)

    for table_name, result in zip(table_names, results):
        if isinstance(result, Exception):
            logger.error(f"Lỗi khi sinh ngữ cảnh nghiệp vụ cho bảng {table_name}: {result}")
        else:
            business_contexts[table_name] = result
            logger.info(f"Đã tạo business context dài {len(result)} ký tự cho bảng {table_name}")

    # Xuất trace JSON
    from datetime import datetime
    from pathlib import Path

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
        prompts_dir = Path(base_dir) / "prompts"
        prompts_dir.mkdir(parents=True, exist_ok=True)
        dump_file = prompts_dir / f"debug_business_contexts_{timestamp}_{run_id}.json"
        dump_file.write_text(json.dumps(business_contexts, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"Đã xuất trace business contexts ra {dump_file}")
    except Exception as e:
        logger.warning(f"Không thể ghi file trace business contexts: {e}")

    return {
        "table_business_contexts": business_contexts,
        "specialized_system_prompts": business_contexts,
    }
