"""Tests cho baseline thống kê: cửa sổ trượt, MAD = 0, và thứ tự xác định."""

from sqlalchemy.orm import Session

from src.models.database import DqResultModel, DqRunModel
from src.services.anomaly_service import (
    _HISTORY_WINDOW,
    calculate_robust_zscore,
    detect_anomalies,
)
from src.time_utils import utc_now


def test_zero_mad_gives_graded_response_not_constant():
    """MAD = 0 phải cho phản hồi có phân cấp, không phải hằng số 3.0 cho mọi sai lệch."""
    flat_history = [0.05] * 6

    z_small, _, mad = calculate_robust_zscore(0.0505, flat_history)
    z_large, _, _ = calculate_robust_zscore(0.90, flat_history)

    assert mad == 0.0
    assert z_small != z_large, "Lệch 0.05% và lệch 85% không được nhận cùng một điểm"
    assert abs(z_small) < abs(z_large)


def test_zero_mad_identical_value_is_not_anomalous():
    z, median, mad = calculate_robust_zscore(0.05, [0.05] * 6)
    assert (z, median, mad) == (0.0, 0.05, 0.0)


def test_baseline_uses_sliding_window_only(test_db):
    """Baseline chỉ dùng _HISTORY_WINDOW đợt chạy gần nhất, không phải toàn bộ lịch sử."""
    total_history = _HISTORY_WINDOW + 10
    with Session(test_db) as session:
        base_time = utc_now()
        for index in range(total_history):
            run_id = f"hist_{index:03d}"
            session.add(
                DqRunModel(
                    id=run_id, job_id="job_w", dataset_id="ds_w", rule_ids="[]",
                    status="SUCCEEDED",
                    # index càng lớn càng mới
                    created_at=base_time.replace(microsecond=0) + __import__("datetime").timedelta(minutes=index),
                )
            )
            session.add(
                DqResultModel(
                    run_id=run_id, rule_id="rule_w", rule_title="W", status="PASS",
                    checked_count=1000, failed_count=index, failed_row_ids="[]",
                )
            )

        session.add(
            DqRunModel(
                id="run_now", job_id="job_w", dataset_id="ds_w", rule_ids="[]",
                status="SUCCEEDED", created_at=base_time + __import__("datetime").timedelta(hours=5),
            )
        )
        session.add(
            DqResultModel(
                run_id="run_now", rule_id="rule_w", rule_title="W", status="PASS",
                checked_count=1000, failed_count=50, failed_row_ids="[]",
            )
        )
        session.commit()

        result = detect_anomalies(session, "run_now")

    signal = next(s for s in result["signals"] if s["target_id"] == "rule_w")
    assert signal["baseline"]["history_size"] == _HISTORY_WINDOW, (
        f"Phải cắt cửa sổ {_HISTORY_WINDOW}, nhận được {signal['baseline']['history_size']}"
    )
