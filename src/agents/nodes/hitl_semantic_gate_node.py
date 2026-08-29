import json
import logging
from pathlib import Path

from src.agents.state import AgentState
from src.config import get_settings

logger = logging.getLogger(__name__)


async def hitl_semantic_gate_node(state: AgentState) -> dict:
    """HITL Semantic Gate Node.

    Nếu Semantic Contract có status = 'confirmed', cho phép đi tiếp.
    Nếu status = 'draft', lưu contract ra file, cập nhật status job thành 'AWAITING_SEMANTIC_REVIEW' và kết thúc lượt chạy.
    """
    contract = state.get("semantic_contract")
    if not contract:
        return {"error": "Không tìm thấy semantic_contract trong state."}

    # Nếu contract đã được confirmed hoặc nếu có flag auto_confirm trong metadata
    auto_confirm = state.get("metadata", {}).get("auto_confirm_semantic", False)
    if contract.get("status") == "confirmed" or auto_confirm:
        logger.info("Semantic contract đã được xác nhận hoặc tự động xác nhận. Đi tiếp sang sinh rule candidates.")
        if contract.get("status") != "confirmed":
            contract["status"] = "confirmed"
        return {"progress_state": "PROPOSING_RULES", "semantic_contract": contract}

    # Nếu là draft, lưu lại và tạm dừng graph
    run_id = state.get("rule_run_id", "test_run")
    settings = get_settings()

    # 1. Lưu contract ra thư mục output/semantic
    try:
        out_dir = getattr(settings, "output_dir", None)
        res_dir = getattr(settings, "results_dir", None)
        base_dir = (
            out_dir
            if isinstance(out_dir, (str, Path))
            else (res_dir if isinstance(res_dir, (str, Path)) else "./output")
        )
        semantic_dir = Path(base_dir) / "semantic"
        semantic_dir.mkdir(parents=True, exist_ok=True)
        out_path = semantic_dir / f"debug_semantic_contract_{run_id}.json"
        out_path.write_text(json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"Đã lưu semantic contract nháp tại {out_path}")
    except Exception as e:
        logger.warning(f"Không thể ghi file semantic contract: {e}")

    # 2. Cập nhật trạng thái run/job và lưu semantic contract vào DB
    try:
        from src.services.rule_store import save_semantic_contract, update_run_status

        dataset_id = state.get("dataset_id", "unknown")
        save_semantic_contract(run_id, dataset_id, contract, status="DRAFT")
        update_run_status(run_id, "AWAITING_SEMANTIC_REVIEW")
        logger.info(
            f"Đã lưu semantic contract vào DB và cập nhật trạng thái run {run_id} thành AWAITING_SEMANTIC_REVIEW"
        )
    except Exception as e:
        logger.warning(f"Không thể cập nhật trạng thái run: {e}")

    # Tạm dừng có chủ đích — KHÔNG phải lỗi. Dùng `pause_reason` để conditional edge dẫn
    # tới END mà runner vẫn phân biệt được "chờ Steward duyệt" với "chạy thất bại".
    return {"pause_reason": "AWAITING_SEMANTIC_REVIEW", "progress_state": "WAITING_FOR_SEMANTIC_REVIEW"}
