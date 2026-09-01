import hashlib
import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.models.database import (
    Base,
    DatasetModel,
    DatasetVersionModel,
    GovernedArtifactModel,
    UserAccountModel,
    WorkspaceModel,
)
from src.services.job_runner import _materialize_versioned_dataset_path, execute_uploaded_rule
from src.services.versioned_dataset import inspect_upload


def test_graph2_executes_a_versioned_dataset_from_its_governed_source(tmp_path):
    source = tmp_path / "vehicles.csv"
    source.write_text("base,active_vehicles\nB001,12\nB002,-1\n", encoding="utf-8")
    content = source.read_bytes()
    checksum = hashlib.sha256(content).hexdigest()
    schema = inspect_upload(content, "vehicles.csv").schema
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(UserAccountModel(id="admin", username="admin", display_name="Admin", password_hash="x", role="ADMIN"))
        db.add(WorkspaceModel(id="ws", name="Workspace", created_by="admin"))
        db.add(DatasetModel(id="vehicles", name="Vehicles", description="Fleet activity", status="PROFILE_READY", row_count=2, source_label="vehicles.csv", manifest_version="v1", checksum=checksum))
        db.add(
            DatasetVersionModel(
                id="vehicles-v1", workspace_id="ws", dataset_id="vehicles", version_number=1,
                status="READY", checksum=checksum, schema_hash="schema", row_count=2,
                source_metadata_json=json.dumps({
                    "source_artifact_id": "artifact-1", "size_bytes": len(content), "format": "csv",
                    "filename": "vehicles.csv", "schema": schema,
                }),
                created_by="admin",
            )
        )
        db.add(
            GovernedArtifactModel(
                id="artifact-1", workspace_id="ws", dataset_id="vehicles", dataset_version_id="vehicles-v1",
                artifact_type="SOURCE_DATASET", storage_locator=f"local:{source}", checksum=checksum,
                created_by="admin",
            )
        )
        db.commit()

        execution_path, temporary = _materialize_versioned_dataset_path(db, "vehicles")
        checked, failed_ids, failed = execute_uploaded_rule(
            execution_path,
            "numeric_range",
            {"type": "numeric_range", "column": "active_vehicles", "min_value": 0},
        )

    assert temporary is False
    assert checked == 2
    assert failed == 1
    assert failed_ids == ["2"]


def test_postgres_migrations_cast_legacy_dq_result_ids_to_text():
    startup = Path("src/services/rule_store.py").read_text(encoding="utf-8")
    release = Path("scripts/migrations/007_graph2_3_models.sql").read_text(encoding="utf-8")
    statement = "ALTER TABLE dq_results ALTER COLUMN id TYPE VARCHAR(36) USING id::text"

    assert statement in startup
    assert "ALTER TABLE public.dq_results" in release
    assert "ALTER COLUMN id TYPE VARCHAR(36) USING id::text" in release
