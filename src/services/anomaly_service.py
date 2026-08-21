"""Canonical anomaly detection service with Median/MAD and historical baseline calculations.
Used by Graph 3 and Dashboard API.
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, UTC
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from src.models.database import (
    DqResultModel,
    DqRunModel,
    AnomalyRunModel,
    AnomalySignalModel,
    AnomalyFeedbackModel,
    ProfileModel,
    ColumnProfileModel,
)

logger = logging.getLogger(__name__)

# Số đợt chạy gần nhất dùng làm baseline cho mỗi rule (cửa sổ trượt).
# Không giới hạn cửa sổ thì median/MAD bị pha loãng bởi toàn bộ lịch sử từ đầu,
# khiến detector ngày càng chai lì với drift mới.
_HISTORY_WINDOW = 30

# Số bản ghi profile gần nhất dùng làm baseline cho VOLUME_DRIFT_DETECTOR.
_VOLUME_HISTORY_WINDOW = 20

# Khi MAD = 0 (lịch sử hoàn toàn phẳng), dùng thang đo dự phòng thay cho hằng số cứng.
_MAD_ZERO_FLOOR = 0.005  # 0.5 điểm phần trăm
_MAX_ROBUST_Z = 10.0


def compute_median(values: list[float]) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    mid = n // 2
    if n % 2 == 0:
        return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0
    return sorted_vals[mid]


def compute_mad(values: list[float], median: float) -> float:
    if not values:
        return 0.0
    deviations = [abs(x - median) for x in values]
    return compute_median(deviations)


def calculate_robust_zscore(current: float, history: list[float]) -> tuple[float, float, float]:
    """Calculate robust Z-score using Median and MAD.
    
    Formula: Robust Z = 0.6745 * (current - median) / MAD
    Returns:
        (robust_zscore, median, mad)
    """
    if not history:
        return 0.0, current, 0.0
    median = compute_median(history)
    mad = compute_mad(history, median)
    
    if mad == 0.0:
        # MAD = 0 nghĩa là lịch sử hoàn toàn phẳng — rất phổ biến khi mọi đợt chạy đều 0% vi phạm.
        # Trả về hằng số 3.0 cho mọi sai lệch khiến lệch 0.001% và lệch 100% nhận cùng một điểm.
        # Thay bằng thang đo dự phòng theo độ lớn baseline để phản hồi có phân cấp.
        if current == median:
            return 0.0, median, 0.0
        fallback_scale = max(abs(median) * 0.1, _MAD_ZERO_FLOOR)
        robust_z = 0.6745 * (current - median) / fallback_scale
        return max(-_MAX_ROBUST_Z, min(_MAX_ROBUST_Z, robust_z)), median, 0.0


    robust_z = 0.6745 * (current - median) / mad
    return robust_z, median, mad


def get_excluded_execution_run_ids(db: Session) -> set[str]:
    """Get all execution run IDs that are marked as true anomalies by the steward."""
    subquery = (
        db.query(AnomalyFeedbackModel.anomaly_run_id)
        .filter(AnomalyFeedbackModel.feedback_label == "TRUE_ANOMALY")
        .subquery()
    )
    runs = (
        db.query(AnomalyRunModel.execution_run_id)
        .filter(AnomalyRunModel.id.in_(subquery))
        .all()
    )
    return {r.execution_run_id for r in runs}


def detect_anomalies(db: Session, execution_run_id: str, detector_config_version: str = "anomaly-v1") -> dict[str, Any]:
    """Canonical function to calculate signals, aggregate decisions, and return anomaly outcomes."""
    # 1. Load current run context
    current_run = db.query(DqRunModel).filter(DqRunModel.id == execution_run_id).first()
    if not current_run:
        raise LookupError(f"Execution run {execution_run_id} not found")

    current_results = db.query(DqResultModel).filter(DqResultModel.run_id == execution_run_id).all()
    
    # Get excluded execution runs (failed runs + TRUE_ANOMALY feedback)
    excluded_run_ids = get_excluded_execution_run_ids(db)
    
    signals: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    # 1.1 Nạp lịch sử của TẤT CẢ rules trong một query duy nhất (trước đây là N+1: mỗi
    # rule một query). Sắp xếp mới nhất trước rồi cắt cửa sổ trượt _HISTORY_WINDOW cho
    # từng rule — baseline chỉ phản ánh giai đoạn gần đây thay vì toàn bộ lịch sử.
    # Loại trừ: đợt chạy hiện tại, đợt chạy lỗi, và đợt đã được Steward gán TRUE_ANOMALY.
    history_by_rule: dict[str, list[float]] = {}
    rule_ids = [res.rule_id for res in current_results]
    if rule_ids:
        history_rows = (
            db.query(DqResultModel)
            .join(DqRunModel, DqRunModel.id == DqResultModel.run_id)
            .filter(
                DqResultModel.rule_id.in_(rule_ids),
                DqResultModel.run_id != execution_run_id,
                DqRunModel.dataset_id == current_run.dataset_id,
                or_(DqRunModel.status == "SUCCEEDED", DqRunModel.status == "DONE"),
            )
            .order_by(DqRunModel.created_at.desc())
            .all()
        )
        for row in history_rows:
            if row.run_id in excluded_run_ids:
                continue
            bucket = history_by_rule.setdefault(row.rule_id, [])
            if len(bucket) < _HISTORY_WINDOW:
                bucket.append(
                    row.failed_count / row.checked_count if row.checked_count > 0 else 0.0
                )

    # 2. Iterate through rules and run detectors
    for res in current_results:
        rule_id = res.rule_id
        checked_count = res.checked_count
        failed_count = res.failed_count
        current_rate = failed_count / checked_count if checked_count > 0 else 0.0

        history_rates = history_by_rule.get(rule_id, [])

        sufficient_history = len(history_rates) >= 5
        
        # Detector 1 & 2 & 3: Invariant / Cold-start / Robust historical on Rule Level
        score = 0.0
        reliability = 1.0
        observed_value = current_rate
        baseline_stats = {}
        detector_name = ""
        explanation_code = ""
        
        # Low checked count reduces reliability
        if checked_count < 100:
            reliability = 0.5
            
        if sufficient_history:
            # Warm start: Robust MAD historical
            robust_z, median, mad = calculate_robust_zscore(current_rate, history_rates)
            baseline_stats = {"median": median, "mad": mad, "history_size": len(history_rates)}
            detector_name = "ROBUST_MAD_DETECTOR"
            
            # Translate Z-score to score (0 to 1)
            # z >= 3.0 maps to score >= 0.8
            if current_rate > 0.01:
                if robust_z >= 3.0:
                    score = min(1.0, 0.8 + (robust_z - 3.0) * 0.05)
                elif robust_z >= 2.0:
                    score = 0.5 + (robust_z - 2.0) * 0.3
                else:
                    score = max(0.0, robust_z * 0.25)
            else:
                score = 0.0
                
            if score >= 0.7:
                explanation_code = f"Tỷ lệ vi phạm hiện tại ({current_rate:.2%}) vượt quá ngưỡng baseline lịch sử (median={median:.2%}, MAD={mad:.2%}) với Robust Z-Score = {robust_z:.2f}."
            else:
                explanation_code = "Hoạt động bình thường theo baseline lịch sử."
        else:
            # Cold start: Static threshold
            detector_name = "COLD_START_STATIC_DETECTOR"
            baseline_stats = {"static_threshold": 0.05}
            reliability = 0.6 if history_rates else 0.4  # lower reliability for absolute cold start
            
            # Static rule: if failed and rate >= 0.05, it is an anomaly
            if res.status == "FAIL" or res.status == "FAILED":
                if current_rate >= 0.05:
                    score = 0.8
                    explanation_code = f"Tỷ lệ vi phạm ({current_rate:.2%}) vượt quá ngưỡng Cold-Start tĩnh 5%."
                else:
                    score = 0.4
                    explanation_code = "Tỷ lệ vi phạm nằm dưới ngưỡng Cold-Start tĩnh 5%."
            else:
                score = 0.0
                explanation_code = "Quy tắc kiểm thử ĐẠT."
                
        # Business rule override check
        is_business_rule = res.rule_title.startswith("BUSINESS_") or "invariant" in res.rule_title.lower() or res.rule_id.endswith(".BUSINESS_RULE")
        if is_business_rule and res.status in ("FAIL", "FAILED"):
            score = 1.0
            detector_name = "BUSINESS_INVARIANT_DETECTOR"
            explanation_code = f"Vi phạm nghiêm trọng luật nghiệp vụ (Business Invariant): {res.rule_title}."
            
        signals.append({
            "signal_id": f"sig-{uuid.uuid4().hex[:12]}",
            "family": "BUSINESS_RULE" if is_business_rule else "STATISTICAL",
            "target_type": "RULE",
            "target_id": rule_id,
            "score": round(score, 4),
            "reliability": round(reliability, 4),
            "observed_value": str(observed_value),
            "baseline": baseline_stats,
            "sufficient_history": sufficient_history,
            "detector_name": detector_name,
            "detector_version": "1.0.0",
            "explanation_code": explanation_code,
            "evidence_refs": [f"dq_results.id={res.id}"]
        })

    # 3. Table/Dataset Level Detectors (Volume & Freshness)
    # Fetch row count from profile
    profile = db.query(ProfileModel).filter(ProfileModel.dataset_id == current_run.dataset_id).order_by(ProfileModel.generated_at.desc()).first()
    if profile:
        current_rows = profile.row_count
        # Fetch historical row counts
        # LIMIT không kèm ORDER BY là hành vi không xác định trong SQL — DB được quyền
        # trả về 20 dòng bất kỳ, khiến baseline thay đổi giữa các lần chạy trên cùng dữ liệu.
        hist_profiles = (
            db.query(ProfileModel)
            .filter(
                ProfileModel.dataset_id == current_run.dataset_id,
                ProfileModel.generated_at < profile.generated_at
            )
            .order_by(ProfileModel.generated_at.desc())
            .limit(_VOLUME_HISTORY_WINDOW)
            .all()
        )
        hist_rows = [p.row_count for p in hist_profiles]
        sufficient_vol_history = len(hist_rows) >= 5
        
        vol_score = 0.0
        vol_reliability = 1.0
        vol_explanation = ""
        vol_baseline = {}
        
        if sufficient_vol_history:
            vol_z, vol_median, vol_mad = calculate_robust_zscore(float(current_rows), [float(x) for x in hist_rows])
            vol_baseline = {"median": vol_median, "mad": vol_mad, "history_size": len(hist_rows)}
            if abs(vol_z) >= 3.0:
                vol_score = min(1.0, 0.8 + (abs(vol_z) - 3.0) * 0.05)
                vol_explanation = f"Số lượng dòng ({current_rows}) đột biến so với baseline lịch sử (median={vol_median:.0f}, MAD={vol_mad:.0f}) với Robust Z = {vol_z:.2f}."
            else:
                vol_score = 0.0
                vol_explanation = "Số lượng dòng bình thường."
        else:
            vol_baseline = {"static_change_threshold": 0.5}
            vol_reliability = 0.5
            vol_explanation = "Không đủ lịch sử để đánh giá đột biến số lượng dòng."
            
        signals.append({
            "signal_id": f"sig-{uuid.uuid4().hex[:12]}",
            "family": "VOLUME",
            "target_type": "DATASET",
            "target_id": current_run.dataset_id,
            "score": round(vol_score, 4),
            "reliability": round(vol_reliability, 4),
            "observed_value": str(current_rows),
            "baseline": vol_baseline,
            "sufficient_history": sufficient_vol_history,
            "detector_name": "VOLUME_DRIFT_DETECTOR",
            "detector_version": "1.0.0",
            "explanation_code": vol_explanation,
            "evidence_refs": []
        })

    # Freshness Check
    freshness_signals = [s for s in signals if s["target_id"].endswith(".FRESHNESS")]
    for fs in freshness_signals:
        fs["family"] = "FRESHNESS"
        fs["detector_name"] = "FRESHNESS_DETECTOR"

    # Execution Health Check (failures in tests or runtime error)
    exec_errors = [res for res in current_results if res.status == "ERROR"]
    if exec_errors or current_run.error_message:
        signals.append({
            "signal_id": f"sig-{uuid.uuid4().hex[:12]}",
            "family": "EXECUTION",
            "target_type": "DATASET",
            "target_id": current_run.dataset_id,
            "score": 1.0,
            "reliability": 1.0,
            "observed_value": "ERROR",
            "baseline": {},
            "sufficient_history": True,
            "detector_name": "EXECUTION_HEALTH_DETECTOR",
            "detector_version": "1.0.0",
            "explanation_code": f"Phát hiện {len(exec_errors)} lỗi kiểm thử hệ thống hoặc lỗi thực thi chạy pipeline.",
            "evidence_refs": [f"dq_results.id={e.id}" for e in exec_errors]
        })

    # 4. Deterministic Aggregation (Phase 3.6)
    # 4.1 Group signals by family
    family_scores: dict[str, list[float]] = {}
    family_weights = {
        "BUSINESS_RULE": 1.0,
        "EXECUTION": 1.0,
        "VOLUME": 0.8,
        "FRESHNESS": 0.8,
        "STATISTICAL": 0.6,
        "ML": 0.5,
    }
    
    for sig in signals:
        fam = sig["family"]
        family_scores.setdefault(fam, []).append(sig["score"])
        
    # Represent family: Max score in family
    family_reps: dict[str, float] = {}
    for fam, scs in family_scores.items():
        family_reps[fam] = max(scs) if scs else 0.0

    # Family "chủ đạo": điểm cao nhất, hoà điểm thì ưu tiên family đáng tin cậy hơn.
    dominant_family = ""
    if family_reps:
        dominant_family = max(
            family_reps,
            key=lambda fam: (family_reps[fam], family_weights.get(fam, 0.5)),
        )


    # Check for priority overrides
    has_critical_override = False
    override_reason = ""
    # If BUSINESS_RULE has score >= 0.8, or EXECUTION has score 1.0
    if family_reps.get("BUSINESS_RULE", 0.0) >= 0.8:
        has_critical_override = True
        override_reason = "Vi phạm nghiêm trọng luật nghiệp vụ (Business Invariant)."
    elif family_reps.get("EXECUTION", 0.0) >= 1.0:
        has_critical_override = True
        override_reason = "Lỗi thực thi kiểm thử hệ thống (Execution Health)."
        
    if has_critical_override:
        final_score = max(family_reps.values())
        decision = "CRITICAL"
        confidence = 0.95
        severity = "HIGH"
    else:
        # Family aggregation — MAX, không phải trung bình.
        #
        # Bất thường là quan hệ HOẶC: chỉ cần MỘT family báo động là đợt chạy đáng ngờ.
        # Bản cũ lấy trung bình có trọng số nên một family khỏe mạnh (score 0.0) kéo tụt
        # điểm của family đang báo động — ví dụ STATISTICAL=0.80 + VOLUME=0.00 cho ra
        # 0.3429 (NORMAL) thay vì 0.80 (ANOMALY). Với dataset đã ingest, signal VOLUME
        # luôn tồn tại nên trần điểm của family STATISTICAL chỉ còn 0.6/1.4 = 0.4286,
        # thấp hơn cả ngưỡng WATCH → detector thống kê bị vô hiệu hoá hoàn toàn.
        #
        # MAX đảm bảo tính đơn điệu: thêm một signal không bao giờ làm giảm điểm tổng hợp.
        final_score = max(family_reps.values()) if family_reps else 0.0

        # Reliability sum
        rel_sum = sum(sig["reliability"] for sig in signals)
        avg_reliability = (rel_sum / len(signals)) if signals else 0.0
        
        # Classify decision
        if not signals or (avg_reliability < 0.5 and final_score < 0.5):
            decision = "INSUFFICIENT_HISTORY"
            confidence = avg_reliability
            severity = "LOW"
        elif final_score >= 0.70:
            decision = "ANOMALY"
            confidence = 0.80
            severity = "HIGH"
        elif final_score >= 0.45:
            decision = "WATCH"
            confidence = 0.70
            severity = "MEDIUM"
        else:
            decision = "NORMAL"
            confidence = 0.90
            severity = "LOW"
            
    # Normalize score
    final_score = round(final_score, 4)
    
    return {
        "decision": decision,
        "score": final_score,
        "confidence": round(confidence, 4),
        "severity": severity,
        "signals": signals,
        "errors": errors,
        "override_reason": override_reason,
        "dominant_family": dominant_family,
    }
