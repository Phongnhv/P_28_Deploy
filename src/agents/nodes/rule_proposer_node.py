"""Rule Proposer Node — fan-out LLM structured output per table.

Luồng:
  rule_proposer_node(state):
    1. Tách digest thành dict {table: table_digest}
    2. Nếu debug flag: dump mỗi bảng ra file JSON
    3. Fan-out asyncio.gather với semaphore, mỗi table 1 LLM call
    4. Stamp rule_id, flatten thành list[dict], tách errors
    5. Trả {proposed_rules, rule_proposal_errors, rule_run_id}

  persist_rules_node(state):
    Thin node: đọc proposed_rules + rule_run_id, gọi save_proposed_rules,
    trả {metadata: {..., rules_saved: n}}
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from src.agents.nodes.templates import _RULE_PROPOSER_FEW_SHOT, rule_proposer_prompt
from src.agents.state import AgentState
from src.agents.tools.chroma_rag_tool import query_historical_rules
from src.agents.tools.profile_digest import (
    dump_table_digests,
    split_digest_by_table,
)
from src.config import get_settings
from src.models.rule_schemas import ProposedRule, RuleStatus, TableRuleProposal
from src.services.llm import get_llm

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Domain context & Data dictionary injected vào mỗi lần gọi LLM
# ---------------------------------------------------------------------------

DOMAIN_CONTEXT = """\
Hệ thống quản lý dữ liệu vận tải taxi thành phố New York (NYC TLC Yellow Taxi Trip Records).
Dataset ghi nhận chi tiết các chuyến đi taxi: thời gian đón/trả khách, địa điểm đón/trả (Taxi Zone),
số lượng hành khách, khoảng cách di chuyển, các khoản cước phí, phụ phí, thuế, phí cầu đường, tiền tip và tổng tiền thanh toán.
"""

DATA_DICTIONARY_PATH = Path(__file__).resolve().parents[3] / "data" / "data_dictionary_trip_records_yellow.json"


def _load_data_dictionary() -> str:
    """Đọc data dictionary JSON từ file data_dictionary_trip_records_yellow.json."""
    target_path = DATA_DICTIONARY_PATH
    if not target_path.exists():
        target_path = Path("data/data_dictionary_trip_records_yellow.json")

    if target_path.exists():
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return json.dumps(data, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning("Không thể đọc data dictionary từ %s: %s", target_path, exc)
    return "None"


# ---------------------------------------------------------------------------
# Helper: đề xuất rule cho 1 bảng (async, retry với backoff mũ)
# ---------------------------------------------------------------------------

async def _propose_for_table(
    table_name: str,
    table_digest: dict,
    structured_llm,
    semaphore: asyncio.Semaphore,
    max_retries: int,
) -> TableRuleProposal:
    """Gọi LLM một lần cho một bảng, bảo vệ bằng semaphore + retry."""
    columns = [col["name"] for col in table_digest.get("columns", [])]
    historical = query_historical_rules(table_name, columns)

    async with semaphore:
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                entry_ts = datetime.now()
                logger.info(
                    "[%s] Bắt đầu gọi LLM (attempt %d/%d) lúc %s",
                    table_name,
                    attempt + 1,
                    max_retries + 1,
                    entry_ts.isoformat(),
                )
                messages = rule_proposer_prompt.format_messages(
                    table_name=table_name,
                    table_digest=json.dumps(table_digest, ensure_ascii=False),
                    domain_context=DOMAIN_CONTEXT,
                    data_dictionary=_load_data_dictionary(),
                    historical_rules=json.dumps(historical, ensure_ascii=False),
                    few_shot_examples=_RULE_PROPOSER_FEW_SHOT,
                )
                result: TableRuleProposal = await structured_llm.ainvoke(messages)
                exit_ts = datetime.now()
                logger.info(
                    "[%s] Hoàn thành (attempt %d) sau %.2fs — %d rules",
                    table_name,
                    attempt + 1,
                    (exit_ts - entry_ts).total_seconds(),
                    len(result.rules),
                )
                return result

            except (ValidationError, Exception) as exc:
                last_exc = exc
                if attempt < max_retries:
                    wait_seconds = 2 ** attempt  # 1s, 2s, 4s …
                    logger.warning(
                        "[%s] Lỗi attempt %d: %s — thử lại sau %ds",
                        table_name,
                        attempt + 1,
                        exc,
                        wait_seconds,
                    )
                    await asyncio.sleep(wait_seconds)
                else:
                    logger.error(
                        "[%s] Đã thử %d lần, từ bỏ. Lỗi cuối: %s",
                        table_name,
                        max_retries + 1,
                        exc,
                    )

        raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Helper: validate + stamp rule_id cho một ProposedRule
# ---------------------------------------------------------------------------

def _stamp_rule(
    rule: ProposedRule,
    table_name: str,
    run_id: str,
    used_ids: set[str] | None = None,
) -> dict:
    """Chuyển ProposedRule sang dict có rule_id, table_name, run_id đính kèm.

    used_ids: set chỏa các rule_id đã dùng trong run này (dedup bằng suffix #2, #3).
    """
    col_key = rule.column if rule.column else "_table"
    base_id = f"{table_name}.{col_key}.{rule.rule_type.value}"

    # Unique hoá rule_id trong phạm vi 1 run
    rule_id = base_id
    if used_ids is not None:
        counter = 2
        while rule_id in used_ids:
            rule_id = f"{base_id}#{counter}"
            counter += 1
        used_ids.add(rule_id)

    # Lọc validation guardrail — validate lại lần nữa phòng trường hợp LLM bypass
    try:
        ProposedRule.model_validate(rule.model_dump())
    except ValidationError as e:
        logger.warning(
            "Rule bị loại do không qua validation: %s | table=%s | errors=%s",
            rule_id,
            table_name,
            e.errors(),
        )
        return {}  # sentinel

    return {
        "rule_id": rule_id,
        "run_id": run_id,
        "table_name": table_name,
        "column": rule.column,
        "rule_type": rule.rule_type.value,
        "parameters": rule.parameters.model_dump(exclude_none=True),
        "confidence_score": rule.confidence_score,
        "severity": rule.severity.value,
        "dimension": rule.dimension.value,
        "rule_description": rule.rule_description,
        "ai_reasoning": rule.ai_reasoning,
        "status": RuleStatus.PENDING.value,
        "edited_parameters": None,
        "reviewer": None,
        "review_note": None,
        "reviewed_at": None,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }


# ---------------------------------------------------------------------------
# Main node: rule_proposer_node
# ---------------------------------------------------------------------------

async def rule_proposer_node(state: AgentState) -> dict:
    """Rule Proposer Node — fan-out LLM structured output per table.

    Đọc state["dataset_profile_digest"], tách theo bảng, gọi LLM song song,
    trả về proposed_rules, rule_proposal_errors, rule_run_id.
    """
    settings = get_settings()

    digest = state.get("dataset_profile_digest", {})
    if not digest:
        logger.warning("dataset_profile_digest rỗng — không có gì để đề xuất.")
        return {
            "proposed_rules": [],
            "rule_proposal_errors": [{"error": "dataset_profile_digest rỗng"}],
            "rule_run_id": "",
        }

    # 1. Tách digest
    per_table = split_digest_by_table(digest)
    if not per_table:
        logger.warning("Không có bảng hợp lệ sau khi split_digest_by_table.")
        return {
            "proposed_rules": [],
            "rule_proposal_errors": [{"error": "Không có bảng hợp lệ trong digest"}],
            "rule_run_id": "",
        }

    # 2. Debug dump (tuỳ chọn)
    if settings.debug_dump_table_digests:
        paths = dump_table_digests(per_table, "data/results/digest_by_table")
        logger.info("Đã dump %d table digest ra: %s", len(paths), paths)

    # 3. Chuẩn bị LLM với structured output
    llm = get_llm(settings.llm_provider, temperature=0.1)
    structured_llm = llm.with_structured_output(TableRuleProposal)

    # 4. Fan-out
    semaphore = asyncio.Semaphore(settings.rule_proposer_concurrency)
    table_names = list(per_table.keys())

    results = await asyncio.gather(
        *[
            _propose_for_table(
                table_name=t,
                table_digest=per_table[t],
                structured_llm=structured_llm,
                semaphore=semaphore,
                max_retries=settings.rule_proposer_max_retries,
            )
            for t in table_names
        ],
        return_exceptions=True,
    )

    # 5. Xử lý kết quả
    run_id = state.get("rule_run_id") or uuid.uuid4().hex
    flat_rules: list[dict] = []
    errors: list[dict] = []
    used_ids: set[str] = set()

    for table_name, result in zip(table_names, results):
        if isinstance(result, Exception):
            logger.error("Bảng '%s' thất bại: %s", table_name, result)
            errors.append({"table": table_name, "error": str(result)})
            continue

        proposal: TableRuleProposal = result
        for rule in proposal.rules:
            stamped = _stamp_rule(rule, table_name, run_id, used_ids)
            if stamped:  # bỏ qua sentinel rỗng (rule bị loại do validation)
                flat_rules.append(stamped)

    logger.info(
        "rule_proposer_node hoàn thành: run_id=%s | %d rules | %d errors",
        run_id,
        len(flat_rules),
        len(errors),
    )

    # Xuất trace JSON sau khi đề xuất rules xong
    try:
        results_dir = Path(settings.results_dir)
        results_dir.mkdir(parents=True, exist_ok=True)
        dump_file = results_dir / f"debug_proposed_rules_{run_id}.json"
        dump_payload = {
            "run_id": run_id,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "total_rules": len(flat_rules),
            "total_errors": len(errors),
            "proposed_rules": flat_rules,
            "errors": errors,
        }
        dump_file.write_text(json.dumps(dump_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"Đã xuất trace proposed rules ra {dump_file}")
    except Exception as e:
        logger.warning(f"Không thể ghi file trace proposed rules: {e}")

    return {
        "proposed_rules": flat_rules,
        "rule_proposal_errors": errors,
        "rule_run_id": run_id,
    }



# ---------------------------------------------------------------------------
# Persist rules node (thin)
# ---------------------------------------------------------------------------

async def persist_rules_node(state: AgentState) -> dict:
    """Lưu proposed_rules vào DB qua asyncio.to_thread (SQLAlchemy sync).

    Thin node: chỉ gọi save_proposed_rules và cập nhật metadata.
    """
    from src.services.rule_store import save_proposed_rules  # lazy import

    proposed_rules: list[dict] = state.get("proposed_rules", [])
    run_id: str = state.get("rule_run_id", "")
    dataset_id: str = state.get("dataset_id", "unknown")
    metadata: dict = state.get("metadata", {})

    if not proposed_rules:
        logger.warning("persist_rules_node: không có rule nào để lưu.")
        n_saved = 0
    else:
        n_saved = await asyncio.to_thread(
            save_proposed_rules, run_id, dataset_id, proposed_rules
        )
        logger.info("persist_rules_node: đã lưu %d rules (run_id=%s)", n_saved, run_id)

    return {
        "metadata": {
            **metadata,
            "rules_saved": n_saved,
            "rule_run_id": run_id,
        }
    }


# ---------------------------------------------------------------------------
# Debug harness
# ---------------------------------------------------------------------------

async def main():
    """Chạy rule_proposer_node từ file digest đã lưu.

    Run: python -m src.agents.nodes.rule_proposer_node
    """
    import glob

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    # Tìm file digest mới nhất trong data/results/
    pattern = os.path.abspath("./data/results/debug_profile_digest_*.json")
    files = sorted(glob.glob(pattern))
    if not files:
        print("Không tìm thấy file debug_profile_digest_*.json trong data/results/")
        print("Hãy chạy profiler trước: python -m src.agents.nodes.profiler_node")
        return

    latest = files[-1]
    print(f"Đọc digest từ: {latest}")
    with open(latest, "r", encoding="utf-8") as f:
        raw = json.load(f)

    # Xây fake state
    state: AgentState = {
        "dataset_profile_digest": raw,  # split_digest_by_table sẽ unwrap nếu cần
        "dataset_id": "olist_debug",
        "metadata": {},
    }

    print("Bắt đầu rule_proposer_node …")
    result = await rule_proposer_node(state)

    print("Hoàn thành rule_proposer_node.")
    print(f"  Tổng rules   : {len(result.get('proposed_rules', []))}")
    print(f"  Errors       : {len(result.get('rule_proposal_errors', []))}")
    print(f"  run_id       : {result.get('rule_run_id')}")


if __name__ == "__main__":
    asyncio.run(main())
    # Run test syntax: python -m src.agents.nodes.rule_proposer_node

