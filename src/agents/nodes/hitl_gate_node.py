"""HITL Gate Node — chốt Run 1: persist rules vào DB, ghi trace JSON.

Node này KHÔNG pause graph. Nó persist rules vào SQLite và ghi trace debug,
sau đó kết thúc Run 1. Steward sẽ review qua REST API (không dùng graph).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from src.agents.state import AgentState
from src.config import get_settings

logger = logging.getLogger(__name__)


async def hitl_gate_node(state: AgentState) -> dict:
    """Chốt Run 1: persist rules vào DB, ghi trace JSON, đánh dấu chờ Steward duyệt.

    - Đọc state["proposed_rules"], state["rule_run_id"], state["dataset_id"].
    - Persist vào DB qua save_proposed_rules (lazy import, sync → thread).
    - Ghi trace ra data/results/proposed_rules_{run_id}.json (lỗi trace chỉ log warning).
    - Trả metadata với hitl_status, rules_saved, trace_path.
    - Không trả lại proposed_rules để tránh ghi đè thừa vào state.
    """
    from src.services.rule_store import save_proposed_rules  # lazy import

    settings = get_settings()
    proposed_rules: list[dict] = state.get("proposed_rules", [])
    run_id: str = state.get("rule_run_id", "")
    dataset_id: str = state.get("dataset_id", "unknown")
    metadata: dict = state.get("metadata", {})

    if not proposed_rules:
        logger.warning("hitl_gate_node: proposed_rules rỗng — không có gì để persist.")
        return {
            "metadata": {
                **metadata,
                "hitl_status": "AWAITING_REVIEW",
                "rules_saved": 0,
                "trace_path": None,
            }
        }

    # 1. Persist vào DB — lỗi ở bước này PHẢI raise để mark run FAILED
    n_saved = await asyncio.to_thread(
        save_proposed_rules, run_id, dataset_id, proposed_rules
    )
    logger.info(
        "hitl_gate_node: đã persist %d rules | run_id=%s | dataset_id=%s",
        n_saved,
        run_id,
        dataset_id,
    )

    # 2. Ghi trace JSON — lỗi chỉ log warning, không fail run
    trace_path: str | None = None
    try:
        base_dir = (
            settings.output_dir
            if hasattr(settings, "output_dir") and isinstance(settings.output_dir, (str, Path))
            else getattr(settings, "results_dir", "./output")
        )
        hitl_dir = Path(base_dir) / "hitl"
        hitl_dir.mkdir(parents=True, exist_ok=True)
        out_path = hitl_dir / f"proposed_rules_{run_id}.json"
        trace_payload = {
            "run_id": run_id,
            "dataset_id": dataset_id,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "rules_count": n_saved,
            "proposed_rules": proposed_rules,
        }
        out_path.write_text(
            json.dumps(trace_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        trace_path = str(out_path)
        logger.info("hitl_gate_node: trace ghi tại %s", trace_path)
    except Exception as exc:
        logger.warning("hitl_gate_node: không ghi được trace JSON: %s", exc)

    return {
        "metadata": {
            **metadata,
            "hitl_status": "AWAITING_REVIEW",
            "rules_saved": n_saved,
            "trace_path": trace_path,
        }
    }
