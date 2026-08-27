import json

from sqlalchemy.orm import Session

from src.models.database import DqResultModel, DqRunModel
from src.services.dashboard_anomaly import detect_dashboard_anomalies

DATASET_ID = "dataset-nyc-yellow-taxi-50k"


def _save_run(session: Session, run_id: str, failed: int, checked: int) -> None:
    session.add(
        DqRunModel(
            id=run_id,
            job_id=f"job-{run_id}",
            dataset_id=DATASET_ID,
            rule_ids=json.dumps(["rule-distance"]),
            status="SUCCEEDED",
            total_failed=failed,
            total_checked=checked,
        )
    )
    session.add(
        DqResultModel(
            run_id=run_id,
            rule_id="rule-distance",
            rule_title="Distance must be non-negative",
            status="FAIL" if failed else "PASS",
            checked_count=checked,
            failed_count=failed,
            failed_row_ids="[]",
        )
    )
    session.commit()


def test_warm_history_uses_z_score(test_db):
    with Session(test_db) as session:
        for index in range(5):
            _save_run(session, f"history-{index}", failed=10, checked=1000)
        _save_run(session, "current", failed=120, checked=1000)

        anomalies = detect_dashboard_anomalies(session, "current")

    assert len(anomalies) == 1
    assert anomalies[0].anomaly_type == "Z_SCORE_SPIKE"
    assert anomalies[0].detection_mode == "HISTORICAL"
    assert anomalies[0].history_size == 5
    assert anomalies[0].historical_mean == 0.01
    assert anomalies[0].z_score == 3.0


def test_small_checks_do_not_raise_unreliable_anomaly(test_db):
    with Session(test_db) as session:
        _save_run(session, "small-current", failed=50, checked=50)
        anomalies = detect_dashboard_anomalies(session, "small-current")

    assert anomalies == []


def test_dq_result_gets_cloud_compatible_string_id(test_db):
    with Session(test_db) as session:
        _save_run(session, "id-contract", failed=0, checked=10)
        result = session.query(DqResultModel).filter_by(run_id="id-contract").one()

    assert isinstance(result.id, str)
    assert len(result.id) == 36
