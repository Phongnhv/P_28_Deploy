"""Anomaly Detector Node — Phát hiện bất thường từ chuỗi kết quả kiểm thử DQ.

Cơ chế:
1. Cold start (< 5 historical runs): Sử dụng Static Threshold dựa trên Severity và violation_rate.
2. Warm start (>= 5 historical runs): Sử dụng Z-score trên chuỗi violation_rate để phát hiện đột biến.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime

from src.agents.state import AgentState
from src.services.rule_store import get_rule_history

logger = logging.getLogger(__name__)


def _calculate_zscore(current_val: float, history_vals: list[float]) -> tuple[float, float, float]:
    """Tính toán Z-score từ giá trị hiện tại và danh sách lịch sử.

    Returns:
        (z_score, mean, std_dev)
    """
    if not history_vals:
        return 0.0, current_val, 0.0

    n = len(history_vals)
    mean = sum(history_vals) / n
    variance = sum((x - mean) ** 2 for x in history_vals) / n
    std_dev = math.sqrt(variance)

    if std_dev == 0.0:
        return (0.0 if current_val == mean else 3.0), mean, 0.0

    z_score = (current_val - mean) / std_dev
    return z_score, mean, std_dev


async def anomaly_detector_node(state: AgentState) -> dict:
    """LangGraph Node: Phát hiện các bất thường trong đợt chạy test hiện tại."""
    test_results = state.get("test_results", [])
    anomalies: list[dict] = []

    for res in test_results:
        rule_id = res.get("rule_id", "")
        v_rate = res.get("violation_rate", 0.0)
        status = res.get("status", "PASSED")

        if status == "ERROR":
            continue

        # Lấy lịch sử violation_rate của rule này
        history_rows = get_rule_history(rule_id, limit=20)
        history_rates = [r.get("violation_rate", 0.0) for r in history_rows]

        # 1. Warm start: Đủ lịch sử để tính Z-Score
        if len(history_rates) >= 5:
            z_score, mean, std = _calculate_zscore(v_rate, history_rates)
            if z_score >= 2.5 and v_rate > 0.01:
                anomalies.append({
                    "rule_id": rule_id,
                    "table_name": res.get("table_name"),
                    "column": res.get("column"),
                    "rule_type": res.get("rule_type"),
                    "anomaly_type": "Z_SCORE_SPIKE",
                    "current_rate": v_rate,
                    "historical_mean": round(mean, 4),
                    "z_score": round(z_score, 2),
                    "reason": f"Tỷ lệ vi phạm ({v_rate:.2%}) tăng đột biến so với trung bình lịch sử ({mean:.2%}) với Z-score = {z_score:.2f}.",
                })
        # 2. Cold start: Dùng Static Threshold
        else:
            if status == "FAILED" and v_rate >= 0.05:
                anomalies.append({
                    "rule_id": rule_id,
                    "table_name": res.get("table_name"),
                    "column": res.get("column"),
                    "rule_type": res.get("rule_type"),
                    "anomaly_type": "HIGH_VIOLATION_RATE",
                    "current_rate": v_rate,
                    "historical_mean": None,
                    "z_score": None,
                    "reason": f"Tỷ lệ vi phạm ở mức cao ({v_rate:.2%}) trên tổng số {res.get('total_rows')} bản ghi.",
                })

    test_run_id = state.get("test_run_id") or state.get("rule_run_id") or "test_run"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Xuất trace file
    try:
        import json
        from pathlib import Path

        from src.config import get_settings
        settings = get_settings()
        base_dir = getattr(settings, "output_dir", None) or "./output"
        out_dir = Path(base_dir) / "anomaly_detector"
        out_dir.mkdir(parents=True, exist_ok=True)
        dump_file = out_dir / f"debug_anomalies_{timestamp}_{test_run_id}.json"
        dump_file.write_text(
            json.dumps(anomalies, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Đã xuất trace anomalies ra: %s", dump_file)
    except Exception as exc:
        logger.warning("Không thể ghi file trace anomalies: %s", exc)

    logger.info("Phát hiện %d điểm bất thường (anomalies) trong test run", len(anomalies))
    return {
        "anomalies": anomalies,
    }


# ---------------------------------------------------------------------------
# Standalone Test Harness (Chạy từ file output test_runner)
# ---------------------------------------------------------------------------

async def main():
    """Hàm chạy test độc lập cho anomaly_detector_node từ file output thực tế.

    Run: python -m src.agents.nodes.anomaly_detector_node
    """
    import glob
    import json
    import os

    from src.services.rule_store import init_db

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    print("\n" + "=" * 75)
    print("🚀 CHẠY THỬ ĐỘC LẬP: anomaly_detector_node (Phát hiện dị thường từ test results)")
    print("=" * 75)

    init_db()

    # Tìm file output từ test_runner_node
    files = glob.glob("output/test_runner/debug_test_results_*.json")
    if not files:
        print("❌ Không tìm thấy file trong output/test_runner/.")
        print("💡 Hãy chạy test_runner_node trước: python -m src.agents.nodes.test_runner_node")
        return

    latest_file = sorted(files, key=os.path.getmtime)[-1]
    print(f"📖 Đọc test results từ: {latest_file}")

    with open(latest_file, encoding="utf-8") as f:
        test_results = json.load(f)

    print(f"🎯 Tổng số test results nạp: {len(test_results)}")

    state: AgentState = {
        "dataset_id": "yellow_tripdata",
        "test_results": test_results,
    }

    res = await anomaly_detector_node(state)

    anomalies = res.get("anomalies", [])
    print(f"\n📊 Tổng số dị thường phát hiện: {len(anomalies)}")
    for idx, a in enumerate(anomalies, 1):
        print(f"\n[{idx}] Rule: {a['rule_id']} ➔ Loại: {a['anomaly_type']}")
        print(f"    Tỷ lệ lỗi hiện tại: {a['current_rate']:.2%}")
        if a.get("z_score") is not None:
            print(f"    Z-score: {a['z_score']} (Lịch sử: {a['historical_mean']:.2%})")
        print(f"    Lý giải AI: {a['reason']}")

    print("\n" + "=" * 75 + "\n")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())


