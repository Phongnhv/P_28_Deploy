"""Report Renderer Service — Deterministic Markdown report generator for Data Steward.

Reads persisted execution data (Graph 2) and anomaly analysis results (Graph 3)
and renders a structured Markdown report. Called by the orchestration layer
(run_anomaly_graph) AFTER Graph 3 completes — NOT a LangGraph node.

Design principles:
- Deterministic: no LLM calls, pure Python templating.
- Idempotent: file name is stable per execution_run_id; retrying Graph 3 overwrites.
- Safe: loads data from DB persistence only, does not depend on in-process Graph 2 state.
- Backward compatible: JSON report and DB persistence are untouched.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_json(value: Any) -> list:
    """Parse a JSON string or return the value as-is if already a list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _pct(rate: float | None) -> str:
    if rate is None:
        return "N/A"
    return f"{rate * 100:.2f}%"


def _load_execution_data(execution_run_id: str) -> tuple[dict | None, list[dict]]:
    """Load DqRunModel and DqResultModel records from DB.

    Returns (dq_run_dict, dq_results_list). Both are plain dicts.
    Returns (None, []) on any failure to keep report generation resilient.
    """
    try:
        from sqlalchemy.orm import Session

        from src.models.database import DqResultModel, DqRunModel
        from src.services.rule_store import get_engine

        engine = get_engine()
        with Session(engine) as session:
            run = session.get(DqRunModel, execution_run_id)
            if run is None:
                return None, []

            run_dict = {
                "id": run.id,
                "dataset_id": run.dataset_id,
                "status": run.status,
                "total_failed": run.total_failed,
                "total_checked": run.total_checked,
                "created_at": str(run.created_at) if run.created_at else None,
                "completed_at": str(run.completed_at) if run.completed_at else None,
                "error_message": run.error_message,
                "rule_ids": _safe_json(run.rule_ids),
            }

            results = session.query(DqResultModel).filter_by(run_id=execution_run_id).all()
            results_list = [
                {
                    "id": r.id,
                    "rule_id": r.rule_id,
                    "rule_title": r.rule_title,
                    "status": r.status,
                    "checked_count": r.checked_count,
                    "failed_count": r.failed_count,
                    "violation_rate": r.violation_rate,
                    "error_message": r.error_message,
                }
                for r in results
            ]
            return run_dict, results_list

    except Exception as exc:
        logger.warning("Failed to load execution data for %s: %s", execution_run_id, exc)
        return None, []


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# (Obsolete English templates removed — now handled by report_writer_node)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Vietnamese deterministic fallback (used by report_writer_node on LLM failure)
# ---------------------------------------------------------------------------

def render_steward_report_vi(
    execution_run_id: str,
    dataset_id: str,
    anomaly_state: dict,
) -> str:
    """Render báo cáo Markdown tiếng Việt theo template deterministic.

    Đây là fallback được dùng khi report_writer_node không thể gọi LLM.
    Đảm bảo luôn có báo cáo ngay cả khi LLM không khả dụng.
    """
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    dq_run, dq_results = _load_execution_data(execution_run_id)

    anomaly_decision = anomaly_state.get("anomaly_decision") or {}
    signals = anomaly_state.get("signal_observations") or []
    hypotheses = anomaly_state.get("hypotheses") or []
    hypothesis_status = anomaly_state.get("hypothesis_status", "UNKNOWN")
    anomaly_run_id = anomaly_state.get("anomaly_run_id", "N/A")

    decision = anomaly_decision.get("decision", "UNKNOWN")
    score = anomaly_decision.get("score", 0.0)
    confidence = anomaly_decision.get("confidence", 0.0)
    severity = anomaly_decision.get("severity", "THẤP")
    override_reason = anomaly_decision.get("override_reason") or ""

    total_rules = len(dq_results)
    passed = sum(1 for r in dq_results if r["status"] in ("PASS", "PASSED"))
    failed = sum(1 for r in dq_results if r["status"] in ("FAIL", "FAILED"))
    errors = sum(1 for r in dq_results if r["status"] == "ERROR")
    total_checked = dq_run["total_checked"] if dq_run else 0

    severity_vi = {"LOW": "Thấp", "MEDIUM": "Trung bình", "HIGH": "Cao", "CRITICAL": "Nghiêm trọng"}.get(severity, severity)
    decision_vi = {
        "NORMAL": "Bình thường", "WATCH": "Cần theo dõi",
        "ANOMALY": "Bất thường", "CRITICAL": "Nghiêm trọng",
        "INSUFFICIENT_HISTORY": "Chưa đủ lịch sử",
    }.get(decision, decision)

    lines: list[str] = [
        "# Báo Cáo Data Steward — Kết Quả Kiểm Tra Chất Lượng Dữ Liệu",
        "",
        f"> **Lưu ý:** Báo cáo này được tạo tự động theo template (không dùng AI). Thời gian: {now_str}",
        "",
        "---",
        "",
        "## 1. Thông Tin Phiên Chạy",
        "",
        "| Trường | Giá trị |",
        "|--------|---------|",
        f"| ID Phiên Thực Thi | `{execution_run_id}` |",
        f"| ID Phiên Phân Tích Bất Thường | `{anomaly_run_id}` |",
        f"| Dataset | `{dataset_id}` |",
        f"| Thời gian tạo báo cáo | {now_str} |",
        f"| Trạng thái thực thi | **{dq_run['status'] if dq_run else 'Không tìm thấy'}** |",
        f"| Kết luận bất thường | **{decision_vi} ({decision})** |",
        "",
        "---",
        "",
        "## 2. Tóm Tắt Chất Lượng Dữ Liệu",
        "",
        "| Chỉ số | Giá trị |",
        "|--------|---------|",
        f"| Tổng số rules kiểm tra | {total_rules} |",
        f"| Đạt | ✅ {passed} |",
        f"| Thất bại | ❌ {failed} |",
        f"| Lỗi kỹ thuật | ⚠️ {errors} |",
        f"| Tổng số hàng kiểm tra | {total_checked:,} |",
    ]
    if dq_run and dq_run.get("error_message"):
        lines += [f"| Thông báo lỗi | {dq_run['error_message']} |"]
    lines += ["", "---", ""]

    # Rules thất bại
    failed_results = [r for r in dq_results if r["status"] in ("FAIL", "FAILED", "ERROR")]
    lines += ["## 3. Chi Tiết Rules Thất Bại", ""]
    if not failed_results:
        if total_rules == 0:
            lines += ["_Chưa có kết quả kiểm tra cho phiên chạy này._", ""]
        else:
            lines += ["✅ Tất cả rules đều đạt. Không phát hiện vi phạm.", ""]
    else:
        lines += [
            f"Có **{len(failed_results)}** rule ghi nhận thất bại hoặc lỗi:",
            "",
            "| Rule ID | Trạng thái | Số hàng kiểm tra | Số hàng vi phạm | Tỷ lệ vi phạm |",
            "|---------|-----------|-----------------|----------------|---------------|",
        ]
        for r in sorted(failed_results, key=lambda x: x.get("violation_rate") or 0.0, reverse=True):
            v_rate = _pct(r.get("violation_rate"))
            lines.append(
                f"| `{r['rule_id']}` | {r['status']} | {r['checked_count']:,} | {r['failed_count']:,} | {v_rate} |"
            )
        lines += [""]
    lines += ["---", ""]

    # Kết luận bất thường
    lines += [
        "## 4. Kết Luận Phát Hiện Bất Thường",
        "",
        "| Trường | Giá trị |",
        "|--------|---------|",
        f"| Kết luận | **{decision_vi}** |",
        f"| Điểm số bất thường | {score:.3f} / 1.000 |",
        f"| Độ tự tin | {confidence:.3f} / 1.000 |",
        f"| Mức độ nghiêm trọng | {severity_vi} |",
    ]
    if override_reason:
        lines += [f"| Lý do điều chỉnh | {override_reason} |"]
    lines += ["", "---", ""]

    # Signals
    lines += ["## 5. Tín Hiệu Bất Thường", ""]
    if not signals:
        lines += ["_Không có tín hiệu bất thường nào được ghi nhận._", ""]
    else:
        top_signals = sorted(signals, key=lambda s: s.get("score", 0.0), reverse=True)[:10]
        lines += [
            f"Top {len(top_signals)} tín hiệu (sắp xếp theo điểm số giảm dần):",
            "",
            "| ID Tín Hiệu | Nhóm | Đối tượng | Điểm số | Độ tin cậy |",
            "|------------|------|----------|---------|-----------|",
        ]
        for sig in top_signals:
            lines.append(
                f"| `{sig.get('signal_id', 'N/A')}` "
                f"| {sig.get('family', '')} "
                f"| {sig.get('target_id', '')} "
                f"| {sig.get('score', 0.0):.3f} "
                f"| {sig.get('reliability', 0.0):.3f} |"
            )
        lines += [""]
    lines += ["---", ""]

    # Giả thuyết
    lines += ["## 6. Giả Thuyết Nguyên Nhân (AI Đề Xuất)", ""]
    if hypothesis_status == "NOT_REQUIRED":
        lines += ["> Phân tích giả thuyết không cần thiết — kết luận là Bình thường.", ""]
    elif not hypotheses:
        lines += [f"> Không có giả thuyết nào được tạo (trạng thái: `{hypothesis_status}`).", ""]
    else:
        if hypothesis_status == "FALLBACK_USED":
            lines += ["> ⚠️ **Chế độ dự phòng:** Giả thuyết được tạo tự động, không dùng AI.", ""]
        for idx, h in enumerate(hypotheses, 1):
            h_conf_pct = f"{h.get('confidence', 0.0) * 100:.0f}%"
            lines += [
                f"### 6.{idx}. {h.get('hypothesis_type', 'UNKNOWN')} — Độ tin cậy: {h_conf_pct}",
                "",
                f"**Tóm tắt:** {h.get('summary', 'N/A')}",
                "",
            ]
            checks = _safe_json(h.get("recommended_checks"))
            if checks:
                lines += ["**Khuyến nghị:**", ""]
                for c in checks:
                    lines += [f"- {c}"]
                lines += [""]
    lines += ["---", ""]

    # Ghi chú
    lines += ["## 7. Ghi Chú Phân Tích", ""]
    notes: list[str] = []
    if hypothesis_status == "NOT_REQUIRED":
        notes.append("Phân tích giả thuyết bị bỏ qua do kết luận là Bình thường.")
    if hypothesis_status == "FALLBACK_USED":
        notes.append("Gọi LLM thất bại; đã dùng giả thuyết dự phòng deterministic.")
    if not dq_run:
        notes.append(
            f"CẢNH BÁO: Không tìm thấy phiên thực thi `{execution_run_id}` trong database. "
            "Báo cáo có thể chưa đầy đủ."
        )
    notes.append("Báo cáo này được tạo tự động bằng template (fallback) do LLM không khả dụng.")
    for note in notes:
        lines += [f"- {note}"]
    lines += ["", "---", ""]
    lines += [f"_Kết thúc báo cáo — {now_str}_", ""]
    return "\n".join(lines)
