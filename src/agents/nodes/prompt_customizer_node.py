import asyncio
import json
import logging

from src.agents.nodes.templates import prompt_customizer_prompt
from src.agents.state import AgentState
from src.config import get_settings
from src.services.llm import get_llm

logger = logging.getLogger(__name__)

async def _customize_prompt_for_table(
    table_name: str,
    semantic_contract: dict,
    llm,
    semaphore: asyncio.Semaphore,
) -> str:
    async with semaphore:
        logger.info(f"Generating specialized system prompt for table: {table_name}")
        messages = prompt_customizer_prompt.format_messages(
            table_name=table_name,
            semantic_contract=json.dumps(semantic_contract, ensure_ascii=False)
        )
        res = await llm.ainvoke(messages)
        prompt_content = res.content.strip()

        # Dọn dẹp nếu LLM vô tình bọc trong markdown block
        if prompt_content.startswith("```"):
             lines = prompt_content.splitlines()
             if lines[0].startswith("```"):
                  lines = lines[1:]
             if lines and lines[-1].startswith("```"):
                  lines = lines[:-1]
             prompt_content = "\n".join(lines).strip()

        return prompt_content

async def prompt_customizer_node(state: AgentState) -> dict:
    """Prompt Customizer Node.

    Sinh prompt hệ thống chuyên biệt (Specialized System Prompt) cho từng bảng dữ liệu
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
        _customize_prompt_for_table(
            table_name=t,
            semantic_contract=tables_contract[t],
            llm=llm,
            semaphore=semaphore
        )
        for t in table_names
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 3. Tạo dictionary lưu prompt theo từng bảng
    specialized_system_prompts = state.get("specialized_system_prompts") or {}
    specialized_system_prompts = dict(specialized_system_prompts)

    for table_name, result in zip(table_names, results):
        if isinstance(result, Exception):
            logger.error(f"Lỗi khi viết lại prompt cho bảng {table_name}: {result}")
        else:
            specialized_system_prompts[table_name] = result
            logger.info(f"Đã tạo specialized prompt dài {len(result)} ký tự cho bảng {table_name}")

    # Xuất trace JSON
    from datetime import datetime
    from pathlib import Path
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = state.get("rule_run_id") or "test_run"
    try:
        out_dir = getattr(settings, "output_dir", None)
        res_dir = getattr(settings, "results_dir", None)
        base_dir = out_dir if isinstance(out_dir, (str, Path)) else (res_dir if isinstance(res_dir, (str, Path)) else "./output")
        prompts_dir = Path(base_dir) / "prompts"
        prompts_dir.mkdir(parents=True, exist_ok=True)
        dump_file = prompts_dir / f"debug_specialized_prompts_{timestamp}_{run_id}.json"
        dump_file.write_text(json.dumps(specialized_system_prompts, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"Đã xuất trace specialized prompts ra {dump_file}")
    except Exception as e:
        logger.warning(f"Không thể ghi file trace specialized prompts: {e}")

    return {
        "specialized_system_prompts": specialized_system_prompts
    }
