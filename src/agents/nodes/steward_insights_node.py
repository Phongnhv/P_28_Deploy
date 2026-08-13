"""Steward Insights Node — AI Data Quality & Governance Advisor.

Tính toán chỉ số DQ Health Score (toàn diện & theo 6 Dimensions) và sử dụng LLM
để sinh báo cáo phân tích, đánh giá chất lượng dữ liệu & đề xuất hành động cho Data Steward.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any

from src.agents.nodes.templates import steward_insights_prompt
from src.agents.state import AgentState
from src.services.llm import get_llm

logger = logging.getLogger(__name__)

# Trọng số mức độ nghiêm trọng (Severity Weights)
SEVERITY_WEIGHTS: dict[str, float] = {
    "CRITICAL": 4.0,
    "HIGH": 3.0,
    "MEDIUM": 2.0,
    "LOW": 1.0,
}


def _get_grade(score: float) -> str:
    """Xếp hạng chất lượng dữ liệu dựa trên điểm DQ Score."""
    if score >= 95.0:
        return "A"
    elif score >= 85.0:
        return "B"
    elif score >= 70.0:
        return "C"
    else:
        return "D"


def calculate_dq_metrics(
    test_results: list[dict],
    anomalies: list[dict] | None = None,
) -> dict[str, Any]:
    """Tính toán bộ chỉ số chất lượng dữ liệu (DQ Score & Dimension Breakdown).

    Công thức:
    - Clean Rate của rule i: R_i = 1 - violation_rate_i (ERROR -> R_i = 0)
    - Base DQ Score = sum(w_i * R_i) / sum(w_i) * 100
    - Anomaly Penalty = count(anomalies) * 2.0
    - Final DQ Score = max(0.0, min(100.0, Base Score - Anomaly Penalty))
    - Dimension Scores = trung bình có trọng số của các rules trong từng dimension.
    """
    if not test_results:
        return {
            "dq_score": 100.0,
            "dq_grade": "A",
            "dq_dimensions": {},
            "total_rules": 0,
            "passed_count": 0,
            "failed_count": 0,
            "error_count": 0,
            "anomaly_count": 0,
            "anomaly_penalty": 0.0,
        }

    total_weighted_clean = 0.0
    total_weights = 0.0

    dim_weighted_clean: dict[str, float] = {}
    dim_weights: dict[str, float] = {}

    passed_count = 0
    failed_count = 0
    error_count = 0

    for r in test_results:
        status = r.get("status", "PASSED")
        v_rate = float(r.get("violation_rate", 0.0))
        severity = str(r.get("severity", "MEDIUM")).upper()
        dimension = str(r.get("dimension", "VALIDITY")).upper()

        weight = SEVERITY_WEIGHTS.get(severity, 2.0)

        if status == "PASSED":
            passed_count += 1
            clean_rate = max(0.0, min(1.0, 1.0 - v_rate))
        elif status == "FAILED":
            failed_count += 1
            clean_rate = max(0.0, min(1.0, 1.0 - v_rate))
        else:  # ERROR hoặc trạng thái khác
            error_count += 1
            clean_rate = 0.0

        # Tích luỹ toàn bộ
        total_weighted_clean += weight * clean_rate
        total_weights += weight

        # Tích luỹ theo Dimension
        dim_weighted_clean[dimension] = dim_weighted_clean.get(dimension, 0.0) + (weight * clean_rate)
        dim_weights[dimension] = dim_weights.get(dimension, 0.0) + weight

    # Tính Base Score
    base_score = (total_weighted_clean / total_weights * 100.0) if total_weights > 0 else 100.0

    # Trừ điểm phạt Anomaly
    anom_list = anomalies or []
    anomaly_penalty = round(len(anom_list) * 2.0, 2)
    final_score = max(0.0, min(100.0, base_score - anomaly_penalty))
    final_score = round(final_score, 2)

    # Tính điểm theo từng Dimension
    dq_dimensions: dict[str, float] = {}
    for dim, w_sum in dim_weights.items():
        if w_sum > 0:
            dq_dimensions[dim] = round((dim_weighted_clean[dim] / w_sum) * 100.0, 2)
        else:
            dq_dimensions[dim] = 100.0

    return {
        "dq_score": final_score,
        "dq_grade": _get_grade(final_score),
        "dq_dimensions": dq_dimensions,
        "total_rules": len(test_results),
        "passed_count": passed_count,
        "failed_count": failed_count,
        "error_count": error_count,
        "anomaly_count": len(anom_list),
        "anomaly_penalty": anomaly_penalty,
    }


def _extract_remediation_actions(markdown_text: str) -> list[dict]:
    """Trích xuất danh sách các hành động checklist từ báo cáo Markdown."""
    actions: list[dict] = []
    pattern = re.compile(r"[-*]\s*\[([ xX])\]\s*(.+)")
    for line in markdown_text.splitlines():
        match = pattern.match(line.strip())
        if match:
            is_checked = match.group(1).lower() == "x"
            content = match.group(2).strip()
            actions.append({
                "action": content,
                "completed": is_checked,
            })
    return actions


def _generate_fallback_summary(
    dataset_id: str,
    metrics: dict,
    failed_rules: list[dict],
    anomalies: list[dict],
) -> str:
    """Sinh báo cáo tổng kết dự phòng (Deterministic Fallback) khi không gọi được LLM."""
    dq_score = metrics["dq_score"]
    dq_grade = metrics["dq_grade"]
    dims = metrics.get("dq_dimensions", {})

    dim_lines = [f"- **{dim}**: {score}/100" for dim, score in dims.items()]
    dim_text = "\n".join(dim_lines) if dim_lines else "- Không có dữ liệu dimension."

    failed_lines = []
    for r in failed_rules:
        col = r.get("column") or "N/A"
        r_type = r.get("rule_type", "")
        v_rate = round(r.get("violation_rate", 0.0) * 100, 2)
        v_count = r.get("violation_count", 0)
        failed_lines.append(f"- Rule `{r.get('rule_id')}` ({r_type} trên `{col}`): {v_count} dòng vi phạm ({v_rate}%).")
    failed_text = "\n".join(failed_lines) if failed_lines else "Không có rule nào bị vi phạm."

    anom_lines = []
    for a in anomalies:
        rule_id = a.get("rule_id", "")
        desc = a.get("description", "Đột biến tỷ lệ vi phạm")
        anom_lines.append(f"- Cảnh báo bất thường trên `{rule_id}`: {desc}")
    anom_text = "\n".join(anom_lines) if anom_lines else "Không phát hiện bất thường đáng kể."

    return f"""# 📊 Báo Cáo Chất Lượng Dữ Liệu: {dataset_id}

### 1. 📊 Tổng Quan Sức Khỏe Dữ Liệu (Executive DQ Summary)
- **Điểm DQ Score**: **{dq_score}/100** (Xếp hạng: **Grade {dq_grade}**)
- **Tổng số rules kiểm tra**: {metrics['total_rules']} (✅ {metrics['passed_count']} Passed, ❌ {metrics['failed_count']} Failed, ⚠️ {metrics['error_count']} Errors)
- **Điểm chi tiết theo Dimensions**:
{dim_text}

### 2. 🚨 Phân Tích Lỗi & Cảnh Báo Bất Thường (Failure & Anomaly Drill-Down)
{failed_text}

**Cảnh báo bất thường (Anomalies):**
{anom_text}

### 3. 🎯 Đánh Giá & Gợi Ý Tinh Chỉnh Ruleset (Rule Tuning Recommendations)
- Kiểm tra các rule có tỷ lệ vi phạm thấp (< 1%) xem có phải do ngoại lệ nghiệp vụ hợp lệ hay không để bổ sung điều kiện lọc.
- Xem xét lại các rule bị lỗi thực thi (ERROR) để cập nhật đúng schema cột.

### 4. 🛠️ Kế Hoạch Hành Động Đề Xuất (Actionable Next Steps)
- [ ] Data Steward xem xét và xác nhận các vi phạm trên các rule FAILED.
- [ ] Phối hợp với Data Engineering kiểm tra nguyên nhân gốc rễ nếu phát hiện spike bất thường.
- [ ] Cập nhật hoặc lưu trữ ruleset chính thức cho đợt chạy tiếp theo.
"""


def _clean_markdown(text: str) -> str:
    """Làm sạch markdown nếu LLM bọc toàn bộ nội dung trong code block ```markdown ... ```."""
    stripped = text.strip()
    match = re.match(r"^```(?:markdown)?\s*\n([\s\S]*?)\n```$", stripped, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return stripped


async def steward_insights_node(state: AgentState) -> dict:
    """LangGraph Node: DQ Health Scoring & Steward Insights Advisor."""
    dataset_id = state.get("dataset_id", "dataset")
    test_results = state.get("test_results", [])
    anomalies = state.get("anomalies", [])
    profile_digest = state.get("dataset_profile_digest") or state.get("dataset_profile", {})

    # 1. Tính toán các chỉ số DQ Score chuẩn xác
    metrics = calculate_dq_metrics(test_results, anomalies)
    dq_score = metrics["dq_score"]
    dq_grade = metrics["dq_grade"]
    dq_dimensions = metrics["dq_dimensions"]

    failed_or_error_rules = [
        r for r in test_results if r.get("status") in ("FAILED", "ERROR")
    ]

    test_summary = {
        "total_rules": metrics["total_rules"],
        "passed": metrics["passed_count"],
        "failed": metrics["failed_count"],
        "errors": metrics["error_count"],
        "anomaly_count": metrics["anomaly_count"],
        "anomaly_penalty": metrics["anomaly_penalty"],
    }

    # 2. Gọi LLM để sinh Báo cáo tổng kết chuyên sâu
    steward_summary: str = ""
    remediation_actions: list[dict] = []

    try:
        from src.config import get_settings
        from src.models.steward_insights_schemas import StewardStructuredInsights

        settings = get_settings()
        llm = get_llm(settings.llm_provider, temperature=0.2)

        structured_llm = llm.with_structured_output(StewardStructuredInsights)
        prompt_value = await steward_insights_prompt.ainvoke({
            "dataset_id": dataset_id,
            "dq_score": dq_score,
            "dq_grade": dq_grade,
            "dq_dimensions_json": json.dumps(dq_dimensions, ensure_ascii=False, indent=2),
            "test_summary_json": json.dumps(test_summary, ensure_ascii=False, indent=2),
            "failed_rules_json": json.dumps(failed_or_error_rules[:15], ensure_ascii=False, indent=2, default=str),
            "anomalies_json": json.dumps(anomalies, ensure_ascii=False, indent=2, default=str),
            "profile_digest_json": json.dumps(profile_digest, ensure_ascii=False, indent=2, default=str),
        })
        response = await structured_llm.ainvoke(prompt_value)

        steward_next_steps_str = "\n".join(f"- [ ] {action}" for action in response.steward_next_steps)
        eng_next_steps_str = "\n".join(f"- [ ] {action}" for action in response.engineering_next_steps)

        steward_summary = f"""# 📊 Báo Cáo Chất Lượng Dữ Liệu: {dataset_id}

### 1. 📊 Tổng Quan Sức Khỏe Dữ Liệu (Executive DQ Summary)
{response.executive_dq_summary}

### 2. 🚨 Phân Tích Lỗi & Cảnh Báo Bất Thường (Failure & Anomaly Drill-Down)
{response.failure_anomaly_drill_down}

### 3. 🎯 Đánh Giá & Gợi Ý Tinh Chỉnh Ruleset (Rule Tuning Recommendations)
{response.rule_tuning_recommendations}

### 4. 🛠️ Kế Hoạch Hành Động Đề Xuất (Actionable Next Steps)
#### Dành cho Data Steward:
{steward_next_steps_str}

#### Dành cho Data Engineering / Source Team:
{eng_next_steps_str}
"""

        remediation_actions = [
            {"action": action, "completed": False}
            for action in response.steward_next_steps + response.engineering_next_steps
        ]
        logger.info("Đã tạo Steward Insights Report thành công cho dataset %s (DQ Score: %.2f)", dataset_id, dq_score)

    except Exception as exc:
        logger.warning("Không thể gọi LLM tạo Steward Insights Report (%s), sử dụng fallback template.", exc)
        steward_summary = _clean_markdown(_generate_fallback_summary(dataset_id, metrics, failed_or_error_rules, anomalies))
        remediation_actions = _extract_remediation_actions(steward_summary)

    test_run_id = state.get("test_run_id") or state.get("rule_run_id") or "test_run"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Xuất duy nhất file Markdown (.md) vào output/steward_insights để review
    md_file_path = None
    try:
        from pathlib import Path

        from src.config import get_settings
        settings = get_settings()
        base_dir = getattr(settings, "output_dir", None) or "./output"
        out_dir = Path(base_dir) / "steward_insights"
        out_dir.mkdir(parents=True, exist_ok=True)

        md_file = out_dir / f"steward_report_{timestamp}_{test_run_id}.md"
        with open(md_file, "w", encoding="utf-8") as f:
            f.write(steward_summary)
        md_file_path = str(md_file)
        logger.info("Đã xuất báo cáo Markdown ra: %s", md_file_path)
    except Exception as exc:
        logger.warning("Không thể ghi file báo cáo markdown steward insights: %s", exc)

    metadata = dict(state.get("metadata", {}))
    if md_file_path:
        metadata["steward_report_path"] = md_file_path

    return {
        "dq_score": dq_score,
        "dq_grade": dq_grade,
        "dq_dimensions": dq_dimensions,
        "steward_summary": steward_summary,
        "remediation_actions": remediation_actions,
        "metadata": metadata,
    }


# ---------------------------------------------------------------------------
# Standalone Test Harness
# ---------------------------------------------------------------------------

async def main():
    """Chạy thử độc lập steward_insights_node.

    Run: python -m src.agents.nodes.steward_insights_node
    """
    import glob
    import os

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    print("\n" + "=" * 75)
    print("🚀 CHẠY THỬ ĐỘC LẬP: steward_insights_node (Data Steward DQ Advisor)")
    print("=" * 75)

    res_files = glob.glob("output/test_runner/debug_test_results_*.json")
    test_results = []
    if res_files:
        latest = sorted(res_files, key=os.path.getmtime)[-1]
        print(f"📖 Đọc test results từ: {latest}")
        with open(latest, encoding="utf-8") as f:
            test_results = json.load(f)
    else:
        print("ℹ️ Dùng mock test results để kiểm tra.")
        test_results = [
            {"rule_id": "r1", "severity": "CRITICAL", "dimension": "UNIQUENESS", "status": "PASSED", "violation_rate": 0.0},
            {"rule_id": "r2", "severity": "HIGH", "dimension": "COMPLETENESS", "status": "PASSED", "violation_rate": 0.0},
            {"rule_id": "r3", "severity": "MEDIUM", "dimension": "VALIDITY", "status": "FAILED", "violation_rate": 0.02, "violation_count": 10},
        ]

    anom_files = glob.glob("output/anomaly_detector/debug_anomalies_*.json")
    anomalies = []
    if anom_files:
        latest_anom = sorted(anom_files, key=os.path.getmtime)[-1]
        print(f"📖 Đọc anomalies từ: {latest_anom}")
        with open(latest_anom, encoding="utf-8") as f:
            anomalies = json.load(f)

    mock_state: AgentState = {
        "dataset_id": "yellow_tripdata",
        "test_run_id": "standalone_debug",
        "test_results": test_results,
        "anomalies": anomalies,
    }

    result = await steward_insights_node(mock_state)

    meta = result.get("metadata", {})
    md_path = meta.get("steward_report_path")

    print("\n📊 KẾT QUẢ ĐÁNH GIÁ CHẤT LƯỢNG DỮ LIỆU:")
    print(f"    DQ Score      : {result['dq_score']}/100")
    print(f"    DQ Grade      : Grade {result['dq_grade']}")
    print(f"    Dimensions    : {json.dumps(result['dq_dimensions'], indent=2)}")
    print(f"    Actions found : {len(result['remediation_actions'])}")
    if md_path:
        print(f"    📄 Markdown File: {md_path}")
    print("\n--- [STEWARD SUMMARY PREVIEW] ---\n")
    print(result["steward_summary"][:500] + "...\n")
    print("=" * 75 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
