from __future__ import annotations

from sqlalchemy.orm import Session

from src.models.database import (
    AnalysisRunModel,
    DatasetModel,
    DatasetVersionModel,
    GovernedArtifactModel,
    Graph1RunModel,
    UserAccountModel,
    WorkspaceModel,
)
from src.services.analysis_workflow import _publish_governed_report
from src.services.dbt_artifact_store import DbtArtifactRef, artifact_sha256


class FakeReportStore:
    def __init__(self):
        self.calls = 0

    def upload_report_markdown(self, analysis_run_id, content, *, dataset_id, dataset_version_id):
        self.calls += 1
        return DbtArtifactRef(
            bucket="reports", object_key=f"reports/{analysis_run_id}.md",
            sha256=artifact_sha256(content), size_bytes=len(content), version_id="1",
        )


def test_governed_report_is_version_scoped_and_retry_deduplicated(test_db, monkeypatch):
    fake = FakeReportStore()
    monkeypatch.setattr("src.services.dbt_artifact_store.get_dbt_artifact_store", lambda: fake)
    with Session(test_db) as db:
        account = db.query(UserAccountModel).filter_by(username="steward").one()
        db.add(WorkspaceModel(id="ws-report", name="Reports", created_by=account.id))
        db.add(DatasetModel(id="report-dataset", name="Report dataset", description="test", source_label="x.csv", manifest_version="versioned-v1", checksum="source"))
        db.flush()
        db.add(DatasetVersionModel(
            id="dv-report", workspace_id="ws-report", dataset_id="report-dataset", version_number=1,
            status="READY", checksum="source", schema_hash="schema", row_count=1,
            source_metadata_json="{}", created_by=account.id,
        ))
        db.add(Graph1RunModel(
            id="g1-report", dataset_id="report-dataset", workspace_id="ws-report", dataset_version_id="dv-report",
            profile_run_id=None, status="COMPLETED", created_by="steward", idempotency_key="g1-report-key", state_json="{}",
        ))
        db.flush()
        run = AnalysisRunModel(
            id="analysis-report", graph1_run_id="g1-report", dataset_id="report-dataset", workspace_id="ws-report",
            dataset_version_id="dv-report", profile_run_id=None, status="RUNNING", phase="REPORT",
            created_by="steward", idempotency_key="analysis-report-key",
        )
        db.add(run)
        db.flush()

        locator, error, uploaded = _publish_governed_report(db, run, "# Báo cáo")
        db.flush()
        assert locator == "object://reports/reports/analysis-report.md"
        assert error is None and uploaded is not None
        assert fake.calls == 1
        db.commit()

        run = db.get(AnalysisRunModel, "analysis-report")
        locator_again, error_again, uploaded_again = _publish_governed_report(db, run, "# Báo cáo")
        assert locator_again == locator
        assert error_again is None and uploaded_again is None
        assert fake.calls == 1
        assert db.query(GovernedArtifactModel).filter_by(run_id="analysis-report", artifact_type="STEWARD_REPORT_MARKDOWN").count() == 1
