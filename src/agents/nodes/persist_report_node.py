"""Persist Report Node — Lưu trữ kết quả thực thi kiểm thử vào Database và lưu file báo cáo.

Thực hiện:
1. Lưu toàn bộ `test_results` vào bảng `test_results`.
2. Cập nhật trạng thái `test_runs` thành DONE.
3. Xuất file kết quả JSON vào thư mục `data/results/`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from src.agents.state import AgentState
from src.services.rule_store import (
    save_test_results,
    update_test_run_status,
)

logger = logging.getLogger(__name__)


def _dump_report_file(test_run_id: str, payload: dict) -> str:
    """Ghi báo cáo kết quả test ra file JSON phục vụ debug / audit."""
    out_dir = Path("output/reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"test_run_{test_run_id}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

    # Ghi thêm bản copy vào data/results để tương thích ngược
    data_dir = Path("data/results")
    data_dir.mkdir(parents=True, exist_ok=True)
    with open(data_dir / f"test_run_{test_run_id}.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

    return str(out_path)


async def persist_report_node(state: AgentState) -> dict:
    """LangGraph Node: Persist test results and finalize run status."""
    test_run_id = state.get("test_run_id") or state.get("rule_run_id") or "test_run"
    test_results = state.get("test_results", [])
    anomalies = state.get("anomalies", [])
    errors = state.get("test_generation_errors", [])

    # Lưu vào database
    await asyncio.to_thread(save_test_results, test_run_id, test_results)

    final_status = "FAILED" if (errors and not test_results) else "DONE"
    err_str = "; ".join(errors) if errors else None
    await asyncio.to_thread(update_test_run_status, test_run_id, final_status, err_str)

    # Ghi file report
    report_payload = {
        "test_run_id": test_run_id,
        "dataset_id": state.get("dataset_id"),
        "status": final_status,
        "total_rules_tested": len(test_results),
        "passed_count": sum(1 for r in test_results if r.get("status") == "PASSED"),
        "failed_count": sum(1 for r in test_results if r.get("status") == "FAILED"),
        "error_count": sum(1 for r in test_results if r.get("status") == "ERROR"),
        "anomalies": anomalies,
        "test_results": test_results,
        "errors": errors,
    }
    report_file_path = await asyncio.to_thread(_dump_report_file, test_run_id, report_payload)

    logger.info("Đã lưu kết quả Test Run %s vào DB và file: %s", test_run_id, report_file_path)

    metadata = dict(state.get("metadata", {}))
    metadata["report_file_path"] = report_file_path
    metadata["test_run_status"] = final_status

    return {
        "metadata": metadata,
    }


# ---------------------------------------------------------------------------
# Standalone Test Harness (Chạy từ file output test_runner)
# ---------------------------------------------------------------------------

async def main():
    """Hàm chạy test độc lập cho persist_report_node từ file output thực tế.

    Run: python -m src.agents.nodes.persist_report_node
    """
    import asyncio
    import glob
    import os
    from src.services.rule_store import create_test_run, get_test_results, get_test_run, init_db

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    print("\n" + "=" * 75)
    print("🚀 CHẠY THỬ ĐỘC LẬP: persist_report_node (Lưu DB & Xuất báo cáo)")
    print("=" * 75)

    init_db()

    # 1. Tìm file test_results
    res_files = glob.glob("output/test_runner/debug_test_results_*.json")
    if not res_files:
        print("❌ Không tìm thấy file trong output/test_runner/.")
        print("💡 Hãy chạy test_runner_node trước: python -m src.agents.nodes.test_runner_node")
        return

    latest_res_file = sorted(res_files, key=os.path.getmtime)[-1]
    print(f"📖 Đọc test results từ: {latest_res_file}")
    with open(latest_res_file, "r", encoding="utf-8") as f:
        test_results = json.load(f)

    # 2. Tìm file anomalies (nếu có)
    anom_files = glob.glob("output/anomaly_detector/debug_anomalies_*.json")
    anomalies = []
    if anom_files:
        latest_anom_file = sorted(anom_files, key=os.path.getmtime)[-1]
        with open(latest_anom_file, "r", encoding="utf-8") as f:
            anomalies = json.load(f)

    test_run_id = f"exec_persist_{uuid.uuid4().hex[:8]}"
    create_test_run(test_run_id, "yellow_tripdata")

    state: AgentState = {
        "dataset_id": "yellow_tripdata",
        "test_run_id": test_run_id,
        "test_results": test_results,
        "anomalies": anomalies,
    }

    res = await persist_report_node(state)

    meta = res.get("metadata", {})
    report_path = meta.get("report_file_path")
    status = meta.get("test_run_status")

    # Kiểm tra DB
    db_run = get_test_run(test_run_id)
    db_results = get_test_results(test_run_id)

    print(f"\n📊 Báo cáo Run 2 đã được lưu:")
    print(f"    Test Run ID    : {test_run_id}")
    print(f"    Trạng thái DB  : {db_run.get('status') if db_run else status}")
    print(f"    Số rules trong DB: {len(db_results)}")
    print(f"    Đường dẫn File : {report_path}")

    print("\n" + "=" * 75 + "\n")


if __name__ == "__main__":
    import asyncio
    import uuid
    asyncio.run(main())

