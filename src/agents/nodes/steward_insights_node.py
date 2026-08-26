"""Steward Insights Node / Hypothesis Agent — LangGraph Node for Graph 3.
Generates structured root-cause hypotheses for anomalies with safety gates and fallbacks.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.agents.state import AnomalyGraphState
from src.config import get_settings
from src.models.database import DqResultModel
from src.services.llm import get_llm
from src.services.rule_store import get_engine

logger = logging.getLogger(__name__)


class HypothesisItem(BaseModel):
    hypothesis_type: Literal[
        "SYSTEM_BUG",
        "SCHEMA_CHANGE",
        "UPSTREAM_DATA_DRIFT",
        "ML_MODEL_DRIFT",
        "OUTLIER",
        "DATA_QUALITY_VIOLATION",
        "UNKNOWN",
    ] = Field(description="Loại giả thuyết chẩn đoán nguyên nhân gốc rễ.")
    summary: str = Field(description="Tóm tắt giả thuyết giải thích nguyên nhân bất thường.")
    confidence: float = Field(description="Độ tin cậy của giả thuyết (0.0 đến 1.0).")
    supporting_signal_ids: list[str] = Field(
        default_factory=list, description="Danh sách signal_id hỗ trợ giả thuyết này."
    )
    contradicting_signal_ids: list[str] = Field(
        default_factory=list, description="Danh sách signal_id mâu thuẫn với giả thuyết này."
    )
    evidence_refs: list[str] = Field(
        default_factory=list, description="Chứng cứ tham chiếu (tên cột, bảng, hoặc rule_id)."
    )
    recommended_checks: list[str] = Field(
        default_factory=list, description="Các đề xuất kiểm tra thủ công hoặc hành động tiếp theo."
    )
    missing_evidence: str | None = Field(
        None, description="Các chứng cứ/metrics còn thiếu chưa có để làm rõ giả thuyết."
    )
    limitations: str | None = Field(None, description="Hạn chế hoặc rủi ro của giả thuyết này.")


class HypothesisResponse(BaseModel):
    hypotheses: list[HypothesisItem] = Field(default_factory=list)


def _generate_fallback_hypotheses(
    dataset_id: str,
    decision: str,
    signals: list[dict],
    failed_rules: list[dict],
) -> list[dict]:
    """Generates a deterministic fallback hypothesis when LLM fails or validation fails."""
    supporting_sigs = [s["signal_id"] for s in signals if s.get("score", 0.0) >= 0.7]
    evidence_refs = list(set([s["target_id"] for s in signals if s.get("score", 0.0) >= 0.7]))
    for r in failed_rules:
        evidence_refs.append(r.get("rule_id", ""))

    # Unique evidence refs
    evidence_refs = sorted(list(set([x for x in evidence_refs if x])))

    fallback_summary = (
        f"Phát hiện sự cố chất lượng dữ liệu bất thường ở mức độ {decision}. "
        f"Có {len(failed_rules)} quy tắc kiểm thử bị vi phạm nghiêm trọng."
    )

    return [
        {
            "hypothesis_type": "DATA_QUALITY_VIOLATION",
            "summary": fallback_summary,
            "confidence": 0.70,
            "supporting_signal_ids": supporting_sigs,
            "contradicting_signal_ids": [],
            "evidence_refs": evidence_refs,
            "recommended_checks": [
                "Kiểm tra lại dữ liệu nguồn ở các cột hoặc bảng bị cảnh báo lỗi.",
                "Xác thực xem có sự thay đổi đột biến ở pipeline tải dữ liệu (upstream ingestion pipeline) hay không.",
            ],
            "missing_evidence": "Lịch sử log chi tiết của hệ thống Ingestion.",
            "limitations": "Đây là phân tích dự phòng tĩnh, không có suy luận ngữ cảnh từ AI.",
        }
    ]


def validate_and_sanitize_hypotheses(
    hypotheses: list[dict],
    valid_signal_ids: set[str],
    valid_evidence_refs: set[str],
) -> list[dict]:
    """Validates generated citations, allowed types, and clamps confidence values."""
    allowed_types = {
        "SYSTEM_BUG",
        "SCHEMA_CHANGE",
        "UPSTREAM_DATA_DRIFT",
        "ML_MODEL_DRIFT",
        "OUTLIER",
        "DATA_QUALITY_VIOLATION",
        "UNKNOWN",
    }

    validated = []
    for h in hypotheses:
        # 1. Type validation
        h_type = h.get("hypothesis_type", "UNKNOWN")
        if h_type not in allowed_types:
            h_type = "UNKNOWN"

        # 2. Clamping confidence
        confidence = float(h.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))

        # 3. Citation validation: filter out non-existent signal IDs
        supporting = [sid for sid in h.get("supporting_signal_ids", []) if sid in valid_signal_ids]
        contradicting = [sid for sid in h.get("contradicting_signal_ids", []) if sid in valid_signal_ids]

        # 4. Safe recommended checks (must be non-empty list of strings)
        checks = [str(c) for c in h.get("recommended_checks", []) if c]
        if not checks:
            checks = ["Liên hệ với đội ngũ kỹ sư dữ liệu để rà soát log chạy."]

        validated.append(
            {
                "hypothesis_type": h_type,
                "summary": str(h.get("summary", "Không có tóm tắt")),
                "confidence": confidence,
                "supporting_signal_ids": supporting,
                "contradicting_signal_ids": contradicting,
                "evidence_refs": [str(e) for e in h.get("evidence_refs", [])],
                "recommended_checks": checks,
                "missing_evidence": h.get("missing_evidence"),
                "limitations": h.get("limitations"),
            }
        )
    return validated


async def steward_insights_node(state: AnomalyGraphState) -> dict:
    """LangGraph Node: AI Root Cause Hypothesis Agent (Steward Insights).

    Generates structured diagnoses if decision indicates anomaly (WATCH, ANOMALY, CRITICAL).
    Fails safe with deterministic output if LLM experiences downtime or schema failure.
    """
    decision_data = state.get("anomaly_decision") or {}
    decision = decision_data.get("decision", "NORMAL")
    signals = state.get("signal_observations", [])

    # 1. Determine if hypothesis is required
    if decision not in ("WATCH", "ANOMALY", "CRITICAL"):
        logger.info("Hypothesis agent skipped. Decision is %s (not WATCH/ANOMALY/CRITICAL)", decision)
        return {"hypotheses": [], "hypothesis_status": "NOT_REQUIRED"}

    execution_run_id = state.get("execution_run_id") or state.get("anomaly_run_id")
    dataset_id = state.get("dataset_id") or "dataset"

    # Load failed rules from current execution
    failed_rules = []
    engine = get_engine()
    try:
        with Session(engine) as session:
            rows = (
                session.query(DqResultModel)
                .filter(DqResultModel.run_id == execution_run_id, DqResultModel.status.in_(["FAIL", "FAILED", "ERROR"]))
                .all()
            )
            for r in rows:
                rule_parts = r.rule_id.split(".")
                col_name = rule_parts[1] if len(rule_parts) > 2 else None
                failed_rules.append(
                    {
                        "rule_id": r.rule_id,
                        "rule_title": r.rule_title,
                        "column": col_name,
                        "status": r.status,
                        "violation_rate": r.violation_rate or 0.0,
                        "violation_count": r.failed_count,
                    }
                )
    except Exception as exc:
        logger.warning("Failed to fetch failed rules for hypothesis context: %s", exc)

    # 2. Build validation inputs
    valid_signal_ids = {s["signal_id"] for s in signals}
    valid_evidence_refs = {s["target_id"] for s in signals}
    for r in failed_rules:
        valid_evidence_refs.add(r["rule_id"])
        if r.get("column"):
            valid_evidence_refs.add(r["column"])

    # 3. Call structured LLM
    settings = get_settings()
    fallback_used = False
    hypotheses_list = []
    latency_ms = 0

    try:
        llm = get_llm(settings.llm_provider, temperature=0.1)
        structured_llm = llm.with_structured_output(HypothesisResponse)

        prompt = (
            "Bạn là một chuyên gia chẩn đoán chất lượng dữ liệu (Data Quality Diagnostics Specialist).\n"
            "Nhiệm vụ của bạn là phân tích đợt kiểm thử dữ liệu bất thường và đề xuất các GIẢ THUYẾT nguyên nhân gốc rễ (root cause hypotheses) cho Data Steward.\n\n"
            f"THÔNG TIN ĐỢT CHẠY HIỆN TẠI:\n"
            f"- Dataset ID: {dataset_id}\n"
            f"- Quyết định tổng hợp: {decision} (Score: {decision_data.get('score')}, Severity: {decision_data.get('severity')})\n"
            f"- Lý do override (nếu có): {decision_data.get('override_reason')}\n\n"
            f"DANH SÁCH TÍN HIỆU CẢNH BÁO (SIGNALS):\n"
            f"{json.dumps(signals, ensure_ascii=False, indent=2)}\n\n"
            f"DANH SÁCH LUẬT BỊ LỖI/VI PHẠM (FAIL/ERROR RULES):\n"
            f"{json.dumps(failed_rules, ensure_ascii=False, indent=2)}\n\n"
            "YÊU CẦU:\n"
            "1. Hãy đề xuất danh sách các giả thuyết nguyên nhân gốc rễ (chọn loại trong: SYSTEM_BUG, SCHEMA_CHANGE, UPSTREAM_DATA_DRIFT, OUTLIER, ML_MODEL_DRIFT, DATA_QUALITY_VIOLATION).\n"
            "2. Đối với mỗi giả thuyết, phải chỉ ra chính xác các `supporting_signal_ids` (signal_id ủng hộ) và `contradicting_signal_ids` (signal_id mâu thuẫn nếu có) trích xuất từ danh sách tín hiệu ở trên. KHÔNG ĐƯỢC TỰ BỊA RA SIGNAL_ID KHÔNG CÓ TRONG DANH SÁCH.\n"
            "3. Chỉ dẫn chứng cứ (`evidence_refs`) là các table/column/rule_id liên quan.\n"
            "4. Đề xuất các hành động kiểm tra (`recommended_checks`) an toàn, không phá hủy hệ thống.\n"
            "5. KHÔNG ĐƯỢC trả về dữ liệu thô nhạy cảm hay thông tin PII.\n"
        )

        logger.info("Invoking LLM for structured hypotheses...")
        llm_started_at = time.perf_counter()
        response = await structured_llm.ainvoke(prompt)
        latency_ms = int((time.perf_counter() - llm_started_at) * 1000)

        # Parse output list of items
        raw_list = [h.model_dump() for h in response.hypotheses]

        # Validate citations and sanitize types
        hypotheses_list = validate_and_sanitize_hypotheses(raw_list, valid_signal_ids, valid_evidence_refs)
        logger.info("Successfully generated and validated %d hypotheses via LLM", len(hypotheses_list))

    except Exception as exc:
        logger.warning("LLM hypothesis generation failed (%s). Triggering fallback generator.", exc)
        hypotheses_list = _generate_fallback_hypotheses(dataset_id, decision, signals, failed_rules)
        fallback_used = True

    # Check if we got empty hypotheses list despite LLM execution
    if not hypotheses_list:
        hypotheses_list = _generate_fallback_hypotheses(dataset_id, decision, signals, failed_rules)
        fallback_used = True

    # Ghi lại model thật và độ trễ thật để persist_analysis_node lưu đúng vào
    # anomaly_hypotheses thay vì hằng số hardcode.
    model_name = str(getattr(settings, f"{settings.llm_provider}_model_name", settings.llm_provider))
    return {
        "hypotheses": hypotheses_list,
        "hypothesis_status": "FALLBACK_USED" if fallback_used else "SUCCEEDED",
        "metadata": {
            **(state.get("metadata") or {}),
            "model_name": model_name,
            "hypothesis_latency_ms": latency_ms,
        },
    }
