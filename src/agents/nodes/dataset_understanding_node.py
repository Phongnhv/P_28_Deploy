import asyncio
import json
import logging
from src.agents.state import AgentState
from src.models.semantic_contract import TableSemanticContract
from src.agents.nodes.templates import dataset_understanding_prompt
from src.agents.tools.profile_digest import split_digest_by_table
from src.services.llm import get_llm
from src.config import get_settings

logger = logging.getLogger(__name__)

async def _understand_table(
    table_name: str,
    table_digest: dict,
    domain_hint: str,
    data_dictionary: str,
    structured_llm,
    semaphore: asyncio.Semaphore,
) -> TableSemanticContract:
    async with semaphore:
        logger.info(f"Analyzing semantic contract for table: {table_name}")
        messages = dataset_understanding_prompt.format_messages(
            table_name=table_name,
            table_digest=json.dumps(table_digest, ensure_ascii=False),
            domain_hint=domain_hint or "None",
            data_dictionary=data_dictionary or "None"
        )
        result: TableSemanticContract = await structured_llm.ainvoke(messages)
        # Gán lại chính xác table_name
        result.table_name = table_name
        return result

async def dataset_understanding_node(state: AgentState) -> dict:    
    """Dataset Understanding Agent Node.
    
    Phân tích digest profile của từng bảng trong dataset và suy luận ra Hợp đồng ngữ nghĩa (Semantic Contract).
    """
    digest = state.get("dataset_profile_digest", {})
    if not digest:
        logger.warning("dataset_profile_digest rỗng trong dataset_understanding_node")
        return {"error": "dataset_profile_digest rỗng"}

    metadata = state.get("metadata", {})
    domain_hint = metadata.get("domain_hint", "")
    data_dictionary = json.dumps(state.get("normalized_data_dictionary") or {}, ensure_ascii=False)

    # 1. Tách digest theo từng bảng
    per_table = split_digest_by_table(digest)
    if not per_table:
        return {"error": "Không tìm thấy bảng hợp lệ trong digest"}

    # 2. Setup structured LLM
    settings = get_settings()
    llm = get_llm(settings.llm_provider, temperature=0.1)
    structured_llm = llm.with_structured_output(TableSemanticContract)

    # 3. Fan-out song song với semaphore
    semaphore = asyncio.Semaphore(settings.rule_proposer_concurrency)
    table_names = list(per_table.keys())

    tasks = [
        _understand_table(
            table_name=t,
            table_digest=per_table[t],
            domain_hint=domain_hint,
            data_dictionary=data_dictionary,
            structured_llm=structured_llm,
            semaphore=semaphore
        )
        for t in table_names
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 4. Tập hợp kết quả thành DatasetSemanticContract
    tables_contract = {}
    errors = []
    for table_name, result in zip(table_names, results):
        if isinstance(result, Exception):
            logger.error(f"Thất bại khi phân tích bảng {table_name}: {result}")
            errors.append(f"Table {table_name}: {str(result)}")
        else:
            tables_contract[table_name] = result.model_dump()

    if errors and not tables_contract:
        return {"error": f"Lỗi toàn bộ khi chạy Dataset Understanding: {'; '.join(errors)}"}

    contract_payload = {
        "dataset_id": state.get("dataset_id", "unknown"),
        "tables": tables_contract,
        "status": "draft"
    }

    logger.info(f"Hoàn thành dataset_understanding_node cho dataset: {state.get('dataset_id')}")

    # Xuất trace JSON
    from datetime import datetime
    from pathlib import Path
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = state.get("rule_run_id") or "test_run"
    try:
        out_dir = getattr(settings, "output_dir", None)
        res_dir = getattr(settings, "results_dir", None)
        base_dir = out_dir if isinstance(out_dir, (str, Path)) else (res_dir if isinstance(res_dir, (str, Path)) else "./output")
        semantic_dir = Path(base_dir) / "semantic"
        semantic_dir.mkdir(parents=True, exist_ok=True)
        dump_file = semantic_dir / f"debug_semantic_understanding_{timestamp}_{run_id}.json"
        dump_file.write_text(json.dumps(contract_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"Đã xuất trace dataset understanding ra {dump_file}")
    except Exception as e:
        logger.warning(f"Không thể ghi file trace dataset understanding: {e}")

    return {
        "semantic_contract": contract_payload,
        "progress_state": "WAITING_FOR_SEMANTIC_REVIEW"
    }
