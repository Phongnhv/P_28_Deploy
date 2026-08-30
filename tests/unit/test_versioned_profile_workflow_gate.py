"""Graph 1A must treat a versioned profile as a real profile.

The canonical import route records an immutable ``profile_runs`` snapshot keyed
by dataset version and never writes a ``ProfileModel`` row. The workflow used to
look only at ``ProfileModel``, so every dataset uploaded through that route was
created with UNDERSTAND_DATA locked and answered "This workflow step is not
ready to run."
"""

import json
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.models.database import (
    Base,
    DatasetModel,
    DatasetVersionModel,
    ProfileRunSnapshotModel,
    UserAccountModel,
    WorkspaceModel,
)
from src.services.dashboard_agent_workflow import build_proposal_evidence
from src.services.rule_proposer_workflow import (
    _has_completed_profile,
    _profile_snapshot,
    _semantic_payload,
)
from src.time_utils import utc_now

DATASET_ID = "dataset-import-versioned"


def seeded_session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = Session(engine)
    db.add(UserAccountModel(id="user-a", username="a", display_name="A", password_hash="x", role="STEWARD"))
    db.add(WorkspaceModel(id="ws-a", name="WS", created_by="user-a"))
    db.add(
        DatasetModel(
            id=DATASET_ID,
            name="Movies",
            description="",
            status="PROFILE_READY",
            row_count=250,
            source_label="movies.csv",
            manifest_version="versioned-v1",
            checksum="abc",
        )
    )
    db.add(
        DatasetVersionModel(
            id="dv-1",
            workspace_id="ws-a",
            dataset_id=DATASET_ID,
            version_number=1,
            status="READY",
            checksum="abc",
            schema_hash="s",
            row_count=250,
            created_by="user-a",
        )
    )
    db.add(
        ProfileRunSnapshotModel(
            id="profile-dv-1",
            workspace_id="ws-a",
            dataset_id=DATASET_ID,
            dataset_version_id="dv-1",
            status="COMPLETED",
            triggered_by="user-a",
            row_count=250,
            completeness_score=95.4,
            validity_score=88.0,
            duplicate_rate=0.0,
            metrics_json=json.dumps(
                {
                    "columns": [
                        {"name": "id", "logical_type": "integer", "null_rate": 0.0, "distinct_count": 250, "non_null_count": 250, "uniqueness_rate": 1.0, "is_unique_full_table": True},
                        {"name": "release_date", "logical_type": "string", "null_rate": 0.02, "distinct_count": 120, "non_null_count": 245},
                        {"name": "rating", "logical_type": "double", "null_rate": 0.0, "distinct_count": 40, "non_null_count": 250},
                    ]
                }
            ),
            completed_at=utc_now() + timedelta(seconds=1),
        )
    )
    db.commit()
    return db


def test_a_versioned_snapshot_counts_as_a_completed_profile():
    db = seeded_session()
    assert _has_completed_profile(db, DATASET_ID) is True


def test_profile_snapshot_is_built_from_the_versioned_run():
    db = seeded_session()
    snapshot = _profile_snapshot(db, DATASET_ID)

    assert snapshot["row_count"] == 250
    assert snapshot["column_count"] == 3
    assert snapshot["completeness_score"] == 95.4
    assert [column["name"] for column in snapshot["columns"]] == ["id", "release_date", "rating"]
    # Evidence keys drive rule grounding, so every column has to contribute one.
    assert "profile.column.rating.null_rate" in snapshot["evidence_keys"]


def test_semantic_payload_infers_roles_without_any_legacy_column_rows():
    db = seeded_session()
    payload = _semantic_payload(db, DATASET_ID)

    roles = {column["name"]: column["semantic_type"] for column in payload["columns"]}
    assert roles["rating"] == "measure"
    # The existing heuristic keys off "date" in the name or "time" in the type;
    # the point here is that it now receives columns at all.
    assert roles["release_date"] == "event_time"
    # Every column reaches the classifier, which is the regression this guards;
    # the classifier's own rules are unchanged and not under test here.
    assert set(roles) == {"id", "release_date", "rating"}
    assert payload["rows"] == 250


def test_graph_1b_builds_evidence_from_the_same_versioned_profile():
    db = seeded_session()
    evidence = build_proposal_evidence(db, DATASET_ID)

    assert evidence.row_count == 250
    assert evidence.manifest_version == "versioned-v1"
    assert [column.name for column in evidence.columns] == ["id", "release_date", "rating"]
    assert "profile.column.rating.null_rate" in evidence.evidence_keys
    assert "sample_value" not in evidence.model_dump_json()


def test_a_dataset_with_neither_profile_is_still_reported_as_unprofiled():
    db = seeded_session()
    db.query(ProfileRunSnapshotModel).delete()
    db.commit()

    assert _has_completed_profile(db, DATASET_ID) is False
