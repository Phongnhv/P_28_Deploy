"""Report Writer Node — LangGraph Node for Graph 3.

Sử dụng LLM để viết báo cáo Markdown tiếng Việt đầy đủ cho Data Steward,
dựa trên kết quả của Graph 2 (test execution) và Graph 3 (anomaly analysis).

Thiết kế:
- Gọi LLM một lần duy nhất với prompt có cấu trúc, temperature thấp để đảm bảo nhất quán.
- Fallback về template deterministic tiếng Việt nếu LLM fail hoặc output trống.
- File output idempotent theo execution_run_id (overwrite khi retry).
- Không gọi lại LLM cho hypotheses (đó là nhiệm vụ của steward_insights_node).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.agents.state import AnomalyGraphState
from src.services.llm import get_llm
from src.services.report_renderer import render_steward_report_vi

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
Bạn là chuyên viên phân tích chất lượng dữ liệu (Data Quality Analyst) chuyên viết báo cáo \
tổng kết cho Data Steward. Nhiệm vụ của bạn là đọc kết quả kiểm tra chất lượng dữ liệu \
và viết một báo cáo Markdown đầy đủ, chuyên nghiệp, hoàn toàn bằng tiếng Việt.

Quy tắc bắt buộc:
1. Viết toàn bộ bằng tiếng Việt — bao gồm cả tiêu đề, mô tả, khuyến nghị.
2. Chỉ được sử dụng các số liệu được cung cấp trong dữ liệu đầu vào, KHÔNG tự bịa thêm.
3. Định dạng Markdown có cấu trúc rõ ràng: headers (##, ###), bảng, bullet points.
4. Giọng văn chuyên nghiệp, súc tích nhưng đủ chi tiết cho người ra quyết định.
5. Sắp xếp, ưu tiên vấn đề theo mức độ nghiêm trọng (từ cao đến thấp).
6. Output PHẢI bắt đầu bằng tiêu đề `# Báo Cáo Data Steward` và không được bọc trong code fences.
"""

_REPORT_STRUCTURE = """\
Báo cáo cần có đủ các mục sau (theo thứ tự):

**Mục 1 — Tóm Tắt Điều Hành (Executive Summary)**
Tóm tắt ngắn gọn (~3-5 câu) về tình trạng chất lượng dữ liệu, kết luận bất thường \
và mức độ cần chú ý ngay.

**Mục 2 — Thông Tin Phiên Chạy**
Bảng metadata: Execution Run ID, Anomaly Run ID, Dataset, Thời gian, Trạng thái.

**Mục 3 — Kết Quả Kiểm Tra Rules**
Bảng tổng hợp (tổng, đạt, thất bại, lỗi). \
Nếu có rules thất bại: bảng chi tiết các rules FAIL/ERROR, \
sắp xếp theo tỷ lệ vi phạm giảm dần. \
Nhận xét về mức độ nghiêm trọng và pattern nếu có.

**Mục 4 — Kết Luận Phát Hiện Bất Thường**
Bảng kết luận (decision, score, confidence, severity). \
Diễn giải ý nghĩa của kết quả này với dữ liệu nghiệp vụ.

**Mục 5 — Phân Tích Tín Hiệu Bất Thường**
Top signals theo điểm số, nhóm thành các pattern nếu có thể.

**Mục 6 — Giả Thuyết Nguyên Nhân**
Trình bày từng giả thuyết AI đề xuất, sắp xếp theo độ tin cậy giảm dần. \
Với mỗi giả thuyết: tóm tắt, độ tin cậy, bằng chứng, khuyến nghị kiểm tra.

**Mục 7 — Đề Xuất Hành Động Ưu Tiên**
Danh sách hành động cần thực hiện, xếp hạng ưu tiên theo mức độ tác động. \
Phân biệt rõ: hành động ngay lập tức, hành động trong 24h, hành động dài hạn.

**Mục 8 — Ghi Chú Kỹ Thuật**
Các lưu ý về giới hạn phân tích, dữ liệu thiếu, điều kiện đặc biệt của phiên chạy.
"""


def _build_data_context(
    execution_run_id: str,
    dataset_id: str,
    anomaly_state: dict,
    dq_run: dict | None,
    dq_results: list[dict],
) -> str:
    """Serialize toàn bộ dữ liệu thành text context cho LLM prompt."""
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    anomaly_decision = anomaly_state.get("anomaly_decision") or {}
    signals = anomaly_state.get("signal_observations") or []
    hypotheses = anomaly_state.get("hypotheses") or []
    hypothesis_status = anomaly_state.get("hypothesis_status", "UNKNOWN")
    anomaly_run_id = anomaly_state.get("anomaly_run_id", "N/A")

    decision = anomaly_decision.get("decision", "UNKNOWN")
    score = anomaly_decision.get("score", 0.0)
    confidence = anomaly_decision.get("confidence", 0.0)
    severity = anomaly_decision.get("severity", "LOW")
    override_reason = anomaly_decision.get("override_reason") or "N/A"

    total_rules = len(dq_results)
    passed = sum(1 for r in dq_results if r["status"] in ("PASS", "PASSED"))
    failed_count = sum(1 for r in dq_results if r["status"] in ("FAIL", "FAILED"))
    errors = sum(1 for r in dq_results if r["status"] == "ERROR")
    total_checked = dq_run["total_checked"] if dq_run else 0

    sections: list[str] = [
        "=== DỮ LIỆU ĐẦU VÀO CHO BÁO CÁO ===",
        "",
        "--- METADATA ---",
        f"Thời gian tạo: {now_str}",
        f"Execution Run ID: {execution_run_id}",
        f"Anomaly Run ID: {anomaly_run_id}",
        f"Dataset: {dataset_id}",
        f"Trạng thái thực thi: {dq_run['status'] if dq_run else 'KHÔNG TÌM THẤY'}",
        "",
        "--- KẾT QUẢ KIỂM TRA RULES (Graph 2) ---",
        f"Tổng số rules: {total_rules}",
        f"Đạt (PASS): {passed}",
        f"Thất bại (FAIL): {failed_count}",
        f"Lỗi kỹ thuật (ERROR): {errors}",
        f"Tổng số hàng kiểm tra: {total_checked:,}",
    ]

    failed_results = [r for r in dq_results if r["status"] in ("FAIL", "FAILED", "ERROR")]
    if failed_results:
        sections.append("\nRules thất bại (sắp xếp theo tỷ lệ vi phạm giảm dần):")
        for r in sorted(failed_results, key=lambda x: x.get("violation_rate") or 0.0, reverse=True):
            vr = r.get("violation_rate")
            vr_str = f"{vr * 100:.2f}%" if vr is not None else "N/A"
            sections.append(
                f"  - {r['rule_id']}: {r['status']} | "
                f"Kiểm tra {r['checked_count']:,} hàng | "
                f"Vi phạm {r['failed_count']:,} hàng ({vr_str})"
            )

    sections += [
        "",
        "--- KẾT QUẢ PHÂN TÍCH BẤT THƯỜNG (Graph 3) ---",
        f"Kết luận: {decision}",
        f"Điểm số bất thường: {score:.3f}/1.000",
        f"Độ tự tin: {confidence:.3f}/1.000",
        f"Mức độ nghiêm trọng: {severity}",
        f"Lý do điều chỉnh (nếu có): {override_reason}",
        f"Trạng thái tạo giả thuyết: {hypothesis_status}",
        "",
    ]

    # Top 15 signals
    top_signals = sorted(signals, key=lambda s: s.get("score", 0.0), reverse=True)[:15]
    if top_signals:
        sections.append(f"Tín hiệu bất thường ({len(top_signals)}/{len(signals)} tín hiệu hàng đầu):")
        for sig in top_signals:
            sections.append(
                f"  - [{sig.get('signal_id', 'N/A')}] "
                f"Nhóm: {sig.get('family', '')} | "
                f"Đối tượng: {sig.get('target_id', '')} | "
                f"Điểm: {sig.get('score', 0.0):.3f} | "
                f"Tin cậy: {sig.get('reliability', 0.0):.3f} | "
                f"Giải thích: {sig.get('explanation_code', '')}"
            )
        sections.append("")

    # Hypotheses
    if hypotheses:
        sections.append(f"Giả thuyết nguyên nhân ({len(hypotheses)} giả thuyết, sắp xếp theo độ tin cậy):")
        for idx, h in enumerate(sorted(hypotheses, key=lambda x: x.get("confidence", 0.0), reverse=True), 1):
            # Parse JSON strings safely
            def _parse(val):
                if isinstance(val, list):
                    return val
                try:
                    return json.loads(val) if val else []
                except Exception:
                    return []

            checks = _parse(h.get("recommended_checks"))
            evidence = _parse(h.get("evidence_refs"))
            supporting = _parse(h.get("supporting_signal_ids"))

            sections.append(
                f"\n  Giả thuyết {idx}: {h.get('hypothesis_type', 'UNKNOWN')} "
                f"(Độ tin cậy: {h.get('confidence', 0.0):.0%})"
            )
            sections.append(f"    Tóm tắt: {h.get('summary', '')}")
            if supporting:
                sections.append(f"    Tín hiệu hỗ trợ: {', '.join(supporting)}")
            if evidence:
                sections.append(f"    Bằng chứng: {', '.join(evidence)}")
            if h.get("missing_evidence"):
                sections.append(f"    Bằng chứng còn thiếu: {h['missing_evidence']}")
            if h.get("limitations"):
                sections.append(f"    Hạn chế: {h['limitations']}")
            if checks:
                sections.append("    Khuyến nghị kiểm tra:")
                for c in checks:
                    sections.append(f"      • {c}")
        sections.append("")
    elif hypothesis_status == "NOT_REQUIRED":
        if decision == "INSUFFICIENT_HISTORY":
            sections.append(
                "Giả thuyết: Chưa tạo vì chưa đủ lịch sử dữ liệu để suy luận đáng tin cậy."
            )
        else:
            sections.append(f"Giả thuyết: Không cần thiết — kết luận là {decision}.")

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Output post-processing
# ---------------------------------------------------------------------------


def _strip_code_fences(text: Any) -> str:
    """Loại bỏ code fences (```markdown ... ```) nếu LLM bọc output, lọc bỏ các khối reasoning."""
    if isinstance(text, list):
        parts: list[str] = []
        for part in text:
            if isinstance(part, dict):
                # Bỏ qua các khối metadata/reasoning nội bộ của LLM (ví dụ encrypted_content của OpenAI reasoning)
                if part.get("type") in ("reasoning", "thought", "metadata") or "encrypted_content" in part:
                    continue
                if "text" in part and part["text"]:
                    parts.append(str(part["text"]))
            elif hasattr(part, "text"):
                if getattr(part, "type", "") not in ("reasoning", "thought") and part.text:
                    parts.append(str(part.text))
            elif isinstance(part, str):
                parts.append(part)
        text = "".join(parts)
    text_str = str(text or "").strip()
    # Loại bỏ các chuỗi metadata reasoning dạng text nếu bị lọt vào
    text_str = re.sub(r"^\[?\{'id':\s*'[^']+',.*?'encrypted_content':\s*'[^']+'\s*\}?\]?\s*", "", text_str, flags=re.DOTALL)
    # Match ```markdown, ```md, or ``` at start
    pattern = r"^```(?:markdown|md)?\s*\n(.*?)\n```\s*$"
    match = re.match(pattern, text_str, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text_str


def _report_matches_anomaly_decision(content: str, decision: str) -> bool:
    """Reject an LLM report that contradicts the canonical cold-start decision."""
    if decision != "INSUFFICIENT_HISTORY":
        return True
    has_cold_start_marker = (
        "INSUFFICIENT_HISTORY" in content or "Chưa đủ lịch sử" in content
    )
    contradicts_cold_start = bool(
        re.search(r"\bNORMAL\b|Bình thường", content, flags=re.IGNORECASE)
    )
    return has_cold_start_marker and not contradicts_cold_start


def _write_report_file(
    execution_run_id: str,
    content: str,
    output_dir: str | None = None,
) -> str:
    """Ghi file Markdown với timestamp, trả về path."""
    try:
        from src.config import get_settings

        settings = get_settings()
        base_dir = Path(output_dir or getattr(settings, "output_dir", None) or "./output")
    except Exception:
        base_dir = Path(output_dir or "./output")

    report_dir = base_dir / "steward_reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    # Tên file chứa cả timestamp để lưu trace chính xác theo thời gian chạy:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = report_dir / f"steward_report_{timestamp}_{execution_run_id}.md"
    tmp_path = out_path.with_suffix(".md.tmp")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(out_path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise
    return str(out_path)


# ---------------------------------------------------------------------------
# LangGraph Node
# ---------------------------------------------------------------------------


async def report_writer_node(state: AnomalyGraphState) -> dict:
    """LangGraph Node: Viết báo cáo Markdown tiếng Việt bằng LLM cho Data Steward.

    Là node cuối cùng trong Graph 3, chạy sau persist_analysis_node.
    Trả về path, nội dung Markdown và nguồn LLM/FALLBACK trong state.
    """
    execution_run_id = state.get("execution_run_id") or state.get("anomaly_run_id", "unknown")
    dataset_id = state.get("dataset_id", "unknown")
    canonical_decision = (state.get("anomaly_decision") or {}).get("decision", "UNKNOWN")

    # Load DB data
    def _load():
        from src.services.report_renderer import _load_execution_data

        return _load_execution_data(execution_run_id)

    dq_run, dq_results = await asyncio.to_thread(_load)

    # Build context
    data_context = _build_data_context(execution_run_id, dataset_id, dict(state), dq_run, dq_results)

    user_prompt = (
        f"{data_context}\n\n"
        f"=== YÊU CẦU BÁO CÁO ===\n\n"
        f"{_REPORT_STRUCTURE}\n\n"
        "Hãy viết báo cáo đầy đủ theo cấu trúc trên. "
        "Bắt đầu ngay với tiêu đề `# Báo Cáo Data Steward`."
    )

    # LLM call
    md_content: str | None = None
    llm_used = False
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        # Dùng provider đã cấu hình (settings.llm_provider) như mọi node LLM khác.
        # Hardcode "openai" khiến node phớt lờ cấu hình: khi đội chuyển sang Gemini/Mistral
        # hoặc không có OPENAI_API_KEY, exception bị nuốt ở dưới và báo cáo LLM âm thầm
        # rơi về template fallback mà người dùng không hề biết.
        from src.config import get_settings

        llm = get_llm(get_settings().llm_provider, temperature=0.2)
        messages = [
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ]
        logger.info("Gọi LLM để viết báo cáo Data Steward cho execution_run_id=%s", execution_run_id)
        response = await llm.ainvoke(messages)
        raw_output = response.content if hasattr(response, "content") else str(response)
        cleaned = _strip_code_fences(raw_output)

        if (
            cleaned
            and "# Báo Cáo Data Steward" in cleaned
            and _report_matches_anomaly_decision(cleaned, canonical_decision)
        ):
            md_content = cleaned
            llm_used = True
            logger.info(
                "LLM viết báo cáo thành công cho execution_run_id=%s (%d ký tự)", execution_run_id, len(md_content)
            )
        else:
            logger.warning(
                "LLM output không hợp lệ, thiếu tiêu đề hoặc mâu thuẫn decision canonical. Dùng fallback. "
                "Output snippet: %.200s", cleaned
            )
    except Exception as exc:
        logger.warning("LLM gặp lỗi khi viết báo cáo, dùng fallback template: %s", exc)

    # Fallback nếu LLM fail
    if not md_content:
        md_content = render_steward_report_vi(execution_run_id, dataset_id, dict(state))
        logger.info("Dùng fallback template tiếng Việt cho execution_run_id=%s", execution_run_id)

    # Versioned runs publish through ``governed_artifacts`` in the durable
    # analysis orchestrator.  They must not depend on a local filesystem path
    # that is unavailable to another worker/revision.  Legacy dashboard runs
    # retain their compatibility trace file.
    report_path = ""
    if not state.get("dataset_version_id"):
        try:
            report_path = await asyncio.to_thread(_write_report_file, execution_run_id, md_content)
            log_prefix = "LLM" if llm_used else "FALLBACK"
            logger.info("[%s] Báo cáo Steward đã ghi: %s", log_prefix, report_path)
        except Exception as write_exc:
            logger.error("Không thể ghi file báo cáo: %s", write_exc, exc_info=True)

    metadata = dict(state.get("metadata") or {})
    metadata["steward_report_path"] = report_path
    metadata["steward_report_llm_used"] = llm_used
    metadata["report_source"] = "LLM" if llm_used else "FALLBACK"

    return {
        "steward_report_path": report_path,
        "steward_report_markdown": md_content,
        "report_source": metadata["report_source"],
        "metadata": metadata,
    }
