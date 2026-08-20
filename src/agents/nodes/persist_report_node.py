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
from datetime import datetime
from pathlib import Path

from src.agents.state import AgentState
from src.services.rule_store import (
    save_test_results,
    update_test_run_status,
)

logger = logging.getLogger(__name__)


def _dump_report_file(test_run_id: str, payload: dict, steward_summary: str | None = None) -> tuple[str, str | None]:
    """Ghi báo cáo kết quả test ra file JSON và Markdown phục vụ debug / audit."""
    from src.config import get_settings
    settings = get_settings()
    base_dir = Path(getattr(settings, "output_dir", None) or "./output")
    res_dir = Path(getattr(settings, "results_dir", None) or "./data/results")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = base_dir / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"test_run_{timestamp}_{test_run_id}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

    # Ghi thêm bản copy vào results để tương thích ngược
    data_dir = res_dir if res_dir != base_dir else (base_dir / "results")
    data_dir.mkdir(parents=True, exist_ok=True)
    with open(data_dir / f"test_run_{timestamp}_{test_run_id}.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

    # Ghi file Markdown tổng kết cho Data Steward
    md_path = None
    if steward_summary:
        md_file = out_dir / f"steward_report_{timestamp}_{test_run_id}.md"
        with open(md_file, "w", encoding="utf-8") as f:
            f.write(steward_summary)
        md_path = str(md_file)

    return str(out_path), md_path


async def persist_report_node(state: AgentState) -> dict:
    """LangGraph Node: Persist test results, steward insights and finalize run status."""
    test_run_id = state.get("test_run_id") or state.get("rule_run_id") or "test_run"
    test_results = state.get("test_results", [])
    anomalies = state.get("anomalies", [])
    errors = state.get("test_generation_errors", [])
    dq_score = state.get("dq_score")
    dq_grade = state.get("dq_grade")
    dq_dimensions = state.get("dq_dimensions", {})
    steward_summary = state.get("steward_summary")
    remediation_actions = state.get("remediation_actions", [])

    # Lưu vào database
    await asyncio.to_thread(save_test_results, test_run_id, test_results)

    final_status = "FAILED" if (errors and not test_results) else "DONE"
    err_str = "; ".join(errors) if errors else None
    await asyncio.to_thread(update_test_run_status, test_run_id, final_status, err_str)

    # 1.1 Also persist to decoupled Graph 2/3 tables (dq_runs and dq_results)
    def _save_decoupled_run():
        from src.services.rule_store import get_engine
        from sqlalchemy.orm import Session
        from src.models.database import DqRunModel, DqResultModel
        
        engine = get_engine()
        with Session(engine) as session:
            # Idempotent: delete existing run and results
            session.query(DqResultModel).filter_by(run_id=test_run_id).delete()
            session.query(DqRunModel).filter_by(id=test_run_id).delete()
            
            # Map statuses
            dq_status = "SUCCEEDED" if final_status == "DONE" else "FAILED"
            
            # Count details
            failed_count = sum(1 for r in test_results if r.get("status") in ("FAIL", "FAILED"))
            checked_count = sum(int(r.get("checked_count") or r.get("total_rows") or 0) for r in test_results)
            
            dq_run = DqRunModel(
                id=test_run_id,
                job_id=state.get("job_id") or test_run_id,
                dataset_id=state.get("dataset_id") or "unknown",
                rule_ids=json.dumps([r.get("rule_id") for r in test_results if r.get("rule_id")]),
                status=dq_status,
                total_failed=failed_count,
                total_checked=checked_count,
                created_at=datetime.now(),
                completed_at=datetime.now(),
                ruleset_version_id=state.get("ruleset_version_id"),
                compiler_version=state.get("metadata", {}).get("compiler_version"),
                artifact_hash=state.get("metadata", {}).get("artifact_hash"),
                retry_history_json=json.dumps(state.get("metadata", {}).get("retry_history", [])),
                error_message=err_str,
                dbt_status=state.get("metadata", {}).get("dbt_status", "SUCCESS"),
                metrics_status=state.get("metadata", {}).get("metrics_status", "SUCCESS"),
            )
            session.add(dq_run)
            session.flush()
            
            for res in test_results:
                r_status = res.get("status", "PASS")
                if r_status == "PASSED":
                    r_status = "PASS"
                elif r_status == "FAILED":
                    r_status = "FAIL"
                
                # Extract counts
                c_count = int(res.get("checked_count") or res.get("total_rows") or 0)
                f_count = int(res.get("failed_count") or res.get("violation_count") or 0)
                
                dq_res = DqResultModel(
                    run_id=test_run_id,
                    rule_id=res.get("rule_id", ""),
                    rule_title=res.get("rule_id", "").split(".")[-1] or "Rule",
                    status=r_status,
                    checked_count=c_count,
                    failed_count=f_count,
                    failed_row_ids=json.dumps(res.get("sample_refs") or res.get("sample_failures") or []),
                    violation_rate=float(res.get("violation_rate") or 0.0),
                    duration_ms=float(res.get("duration_ms") or 0.0),
                    dbt_status=res.get("dbt_status") or ("SUCCESS" if r_status == "PASS" else "FAIL"),
                    metrics_status=res.get("metrics_status") or "SUCCESS",
                    error_message=res.get("error"),
                )
                session.add(dq_res)
                
            session.commit()
            logger.info("Đã lưu decoupled run và %d results cho run_id=%s vào dq_runs/dq_results", len(test_results), test_run_id)

    try:
        await asyncio.to_thread(_save_decoupled_run)
    except Exception as db_exc:
        logger.warning("Không thể lưu decoupled run thông tin vào dq_runs/dq_results: %s", db_exc)

    # Ghi file report
    report_payload = {
        "test_run_id": test_run_id,
        "dataset_id": state.get("dataset_id"),
        "status": final_status,
        "dq_score": dq_score,
        "dq_grade": dq_grade,
        "dq_dimensions": dq_dimensions,
        "total_rules_tested": len(test_results),
        "passed_count": sum(1 for r in test_results if r.get("status") == "PASSED"),
        "failed_count": sum(1 for r in test_results if r.get("status") == "FAILED"),
        "error_count": sum(1 for r in test_results if r.get("status") == "ERROR"),
        "anomalies": anomalies,
        "remediation_actions": remediation_actions,
        "test_results": test_results,
        "errors": errors,
    }
    report_file_path, steward_md_path = await asyncio.to_thread(
        _dump_report_file, test_run_id, report_payload, steward_summary
    )

    logger.info("Đã lưu kết quả Test Run %s vào DB và file: %s", test_run_id, report_file_path)

    metadata = dict(state.get("metadata", {}))
    metadata["report_file_path"] = report_file_path
    if steward_md_path:
        metadata["steward_report_path"] = steward_md_path
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
    with open(latest_res_file, encoding="utf-8") as f:
        test_results = json.load(f)

    # 2. Tìm file anomalies (nếu có)
    anom_files = glob.glob("output/anomaly_detector/debug_anomalies_*.json")
    anomalies = []
    if anom_files:
        latest_anom_file = sorted(anom_files, key=os.path.getmtime)[-1]
        with open(latest_anom_file, encoding="utf-8") as f:
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

    print("\n📊 Báo cáo Run 2 đã được lưu:")
    print(f"    Test Run ID    : {test_run_id}")
    print(f"    Trạng thái DB  : {db_run.get('status') if db_run else status}")
    print(f"    Số rules trong DB: {len(db_results)}")
    print(f"    Đường dẫn File : {report_path}")

    print("\n" + "=" * 75 + "\n")


if __name__ == "__main__":
    import asyncio
    import uuid
    asyncio.run(main())

