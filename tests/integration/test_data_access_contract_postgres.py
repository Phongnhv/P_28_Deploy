"""PostgreSQL contract tests for workspace-scoped dataset access.

Run explicitly with CONTRACT_TEST_DATABASE_URL pointing at a disposable local
database. The safety check below refuses to drop/create tables elsewhere.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from src.models.database import (
    AnalysisSummaryModel,
    Base,
    DatasetGovernanceModel,
    DatasetModel,
    DatasetVersionModel,
    GovernanceAuditEventModel,
    GovernedArtifactModel,
    ProfileRunSnapshotModel,
    RuleReviewSnapshotModel,
    UserAccountModel,
    WorkspaceMembershipModel,
    WorkspaceModel,
)
from src.services.data_access_service import (
    AccessContext,
    ResourceNotFoundError,
    get_data_explorer,
    get_governed_artifact,
    get_overview_metrics,
    grant_dataset_permissions,
    list_accessible_datasets,
    list_audit_events,
    revoke_grant_set,
)

CONTRACT_DATABASE_URL = os.getenv("CONTRACT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not CONTRACT_DATABASE_URL,
    reason="Set CONTRACT_TEST_DATABASE_URL to a disposable PostgreSQL database",
)


@pytest.fixture(scope="module")
def contract_engine():
    assert CONTRACT_DATABASE_URL is not None
    parsed = make_url(CONTRACT_DATABASE_URL)
    if parsed.get_backend_name() != "postgresql" or parsed.database != "ridepulse_contract_test":
        pytest.fail("Contract tests only run against PostgreSQL database 'ridepulse_contract_test'")
    engine = create_engine(CONTRACT_DATABASE_URL)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    migration_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "migrations" / "009_workspace_data_access_rls.sql"
    )
    raw_connection = engine.raw_connection()
    try:
        with raw_connection.cursor() as cursor:
            cursor.execute(migration_path.read_text(encoding="utf-8"))
        raw_connection.commit()
    finally:
        raw_connection.close()
    yield engine
    engine.dispose()


@pytest.fixture
def contract_db(contract_engine):
    connection = contract_engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection)
    _seed_contract_data(db)
    try:
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()


def _user(user_id: str, username: str) -> UserAccountModel:
    return UserAccountModel(
        id=user_id,
        username=username,
        display_name=username.title(),
        password_hash="not-used-in-contract-tests",
        role="USER",
        status="ACTIVE",
    )


def _dataset(dataset_id: str, name: str, checksum: str) -> DatasetModel:
    return DatasetModel(
        id=dataset_id,
        name=name,
        description=f"Contract fixture for {name}",
        status="PROFILE_READY",
        row_count=100,
        source_label="local-postgres-fixture",
        manifest_version="v1",
        checksum=checksum,
    )


def _audit(
    event_id: str,
    workspace_id: str,
    actor_id: str,
    dataset_id: str,
    dataset_version_id: str,
) -> GovernanceAuditEventModel:
    return GovernanceAuditEventModel(
        id=event_id,
        workspace_id=workspace_id,
        actor_id=actor_id,
        actor_role="USER",
        action="PROFILE_COMPLETED",
        entity_type="profile_run",
        entity_id=f"profile-{dataset_version_id}",
        dataset_id=dataset_id,
        dataset_version_id=dataset_version_id,
        run_id=f"run-{dataset_version_id}",
        correlation_id=f"corr-{event_id}",
        request_metadata_json="{}",
        detail_json="{}",
        source="WORKER",
        occurred_at=datetime.now(UTC),
    )


def _seed_contract_data(db: Session) -> None:
    users = [
        _user("user-owner-a", "owner-a"),
        _user("user-owner-b", "owner-b"),
        _user("user-viewer", "viewer"),
        _user("user-outsider", "outsider"),
    ]
    db.add_all(users)
    db.flush()
    db.add_all(
        [
            WorkspaceModel(id="ws-a", name="Workspace A", created_by="user-owner-a"),
            WorkspaceModel(id="ws-b", name="Workspace B", created_by="user-outsider"),
        ]
    )
    db.flush()
    db.add_all(
        [
            WorkspaceMembershipModel(id="wm-owner-a", workspace_id="ws-a", user_id="user-owner-a", role="USER"),
            WorkspaceMembershipModel(id="wm-owner-b", workspace_id="ws-a", user_id="user-owner-b", role="USER"),
            WorkspaceMembershipModel(id="wm-viewer", workspace_id="ws-a", user_id="user-viewer", role="USER"),
            WorkspaceMembershipModel(id="wm-outsider", workspace_id="ws-b", user_id="user-outsider", role="ADMIN"),
        ]
    )
    db.add_all(
        [
            _dataset("dataset-a", "Dataset A", "checksum-a"),
            _dataset("dataset-b", "Dataset B", "checksum-b"),
            _dataset("dataset-c", "Dataset C", "checksum-c"),
        ]
    )
    db.flush()
    db.add_all(
        [
            DatasetGovernanceModel(dataset_id="dataset-a", workspace_id="ws-a", owner_user_id="user-owner-a"),
            DatasetGovernanceModel(dataset_id="dataset-b", workspace_id="ws-a", owner_user_id="user-owner-b"),
            DatasetGovernanceModel(dataset_id="dataset-c", workspace_id="ws-b", owner_user_id="user-outsider"),
        ]
    )
    db.add_all(
        [
            DatasetVersionModel(
                id="version-a1",
                workspace_id="ws-a",
                dataset_id="dataset-a",
                version_number=1,
                checksum="version-checksum-a",
                schema_hash="schema-a",
                row_count=100,
                created_by="user-owner-a",
            ),
            DatasetVersionModel(
                id="version-b1",
                workspace_id="ws-a",
                dataset_id="dataset-b",
                version_number=1,
                checksum="version-checksum-b",
                schema_hash="schema-b",
                row_count=200,
                created_by="user-owner-b",
            ),
            DatasetVersionModel(
                id="version-c1",
                workspace_id="ws-b",
                dataset_id="dataset-c",
                version_number=1,
                checksum="version-checksum-c",
                schema_hash="schema-c",
                row_count=300,
                created_by="user-outsider",
            ),
        ]
    )
    db.flush()
    now = datetime.now(UTC)
    db.add_all(
        [
            ProfileRunSnapshotModel(
                id="profile-a1",
                workspace_id="ws-a",
                dataset_id="dataset-a",
                dataset_version_id="version-a1",
                status="COMPLETED",
                triggered_by="user-owner-a",
                row_count=100,
                completeness_score=0.9,
                validity_score=0.8,
                uniqueness_score=0.75,
                duplicate_rate=0.02,
                quality_score=80.0,
                schema_json='[{"name":"trip_id","type":"string"}]',
                metrics_json='{"completeness":0.9}',
                sanitized_samples_json='[{"trip_id":"masked-1"}]',
                completed_at=now,
            ),
            ProfileRunSnapshotModel(
                id="profile-b1",
                workspace_id="ws-a",
                dataset_id="dataset-b",
                dataset_version_id="version-b1",
                status="COMPLETED",
                triggered_by="user-owner-b",
                row_count=200,
                completeness_score=0.3,
                validity_score=0.2,
                uniqueness_score=0.1,
                duplicate_rate=0.4,
                quality_score=20.0,
                schema_json='[{"name":"secret","type":"string"}]',
                metrics_json='{"completeness":0.3}',
                sanitized_samples_json="[]",
                completed_at=now,
            ),
            ProfileRunSnapshotModel(
                id="profile-c1",
                workspace_id="ws-b",
                dataset_id="dataset-c",
                dataset_version_id="version-c1",
                status="COMPLETED",
                triggered_by="user-outsider",
                row_count=300,
                quality_score=99.0,
                schema_json="[]",
                metrics_json="{}",
                sanitized_samples_json="[]",
                completed_at=now,
            ),
        ]
    )
    db.flush()
    db.add_all(
        [
            RuleReviewSnapshotModel(
                id="rule-a-approved",
                workspace_id="ws-a",
                dataset_id="dataset-a",
                dataset_version_id="version-a1",
                profile_run_id="profile-a1",
                status="APPROVED",
            ),
            RuleReviewSnapshotModel(
                id="rule-a-rejected",
                workspace_id="ws-a",
                dataset_id="dataset-a",
                dataset_version_id="version-a1",
                profile_run_id="profile-a1",
                status="REJECTED",
            ),
            RuleReviewSnapshotModel(
                id="rule-b-approved",
                workspace_id="ws-a",
                dataset_id="dataset-b",
                dataset_version_id="version-b1",
                profile_run_id="profile-b1",
                status="APPROVED",
            ),
            AnalysisSummaryModel(
                id="analysis-a1",
                workspace_id="ws-a",
                dataset_id="dataset-a",
                dataset_version_id="version-a1",
                profile_run_id="profile-a1",
                status="COMPLETED",
                tests_passed=7,
                tests_failed=1,
                anomaly_count=2,
                created_by="user-owner-a",
                completed_at=now,
            ),
            AnalysisSummaryModel(
                id="analysis-b1",
                workspace_id="ws-a",
                dataset_id="dataset-b",
                dataset_version_id="version-b1",
                profile_run_id="profile-b1",
                status="COMPLETED",
                tests_passed=1,
                tests_failed=99,
                anomaly_count=50,
                created_by="user-owner-b",
                completed_at=now,
            ),
            GovernedArtifactModel(
                id="artifact-a1",
                workspace_id="ws-a",
                dataset_id="dataset-a",
                dataset_version_id="version-a1",
                run_id="analysis-a1",
                artifact_type="REPORT",
                storage_locator="local://contract/artifact-a1",
                checksum="artifact-checksum-a",
                created_by="user-owner-a",
            ),
        ]
    )
    db.add_all(
        [
            _audit("audit-a", "ws-a", "user-owner-a", "dataset-a", "version-a1"),
            _audit("audit-b", "ws-a", "user-owner-b", "dataset-b", "version-b1"),
            _audit("audit-c", "ws-b", "user-outsider", "dataset-c", "version-c1"),
        ]
    )
    db.commit()


def test_private_dataset_is_hidden_from_other_user(contract_db: Session):
    viewer = AccessContext("user-viewer", "ws-a")

    assert list_accessible_datasets(contract_db, viewer) == []
    with pytest.raises(ResourceNotFoundError):
        get_data_explorer(
            contract_db,
            viewer,
            dataset_id="dataset-a",
            dataset_version_id="version-a1",
        )


def test_dataset_appears_immediately_after_explicit_share(contract_db: Session):
    owner = AccessContext("user-owner-a", "ws-a")
    viewer = AccessContext("user-viewer", "ws-a")

    grant_dataset_permissions(
        contract_db,
        owner,
        dataset_id="dataset-a",
        grantee_type="USER",
        grantee_id=viewer.user_id,
        permissions={"DISCOVER", "VIEW_PROFILE"},
    )

    assert [row["dataset_id"] for row in list_accessible_datasets(contract_db, viewer)] == ["dataset-a"]
    explorer = get_data_explorer(
        contract_db,
        viewer,
        dataset_id="dataset-a",
        dataset_version_id="version-a1",
        profile_run_id="profile-a1",
    )
    assert explorer["selected_profile"]["profile_run_id"] == "profile-a1"
    assert explorer["reports"] == []


def test_revoke_removes_profile_rules_reports_and_artifacts(contract_db: Session):
    owner = AccessContext("user-owner-a", "ws-a")
    viewer = AccessContext("user-viewer", "ws-a")
    grant_set_id = grant_dataset_permissions(
        contract_db,
        owner,
        dataset_id="dataset-a",
        grantee_type="USER",
        grantee_id=viewer.user_id,
        permissions={"VIEW_PROFILE", "VIEW_REPORTS"},
    )
    before = get_data_explorer(
        contract_db,
        viewer,
        dataset_id="dataset-a",
        dataset_version_id="version-a1",
        profile_run_id="profile-a1",
    )
    assert before["ruleset_history"]
    assert before["reports"]
    assert get_governed_artifact(
        contract_db,
        viewer,
        dataset_id="dataset-a",
        dataset_version_id="version-a1",
        artifact_id="artifact-a1",
    )

    assert revoke_grant_set(contract_db, owner, grant_set_id) == 2

    with pytest.raises(ResourceNotFoundError):
        get_data_explorer(
            contract_db,
            viewer,
            dataset_id="dataset-a",
            dataset_version_id="version-a1",
            profile_run_id="profile-a1",
        )
    with pytest.raises(ResourceNotFoundError):
        get_governed_artifact(
            contract_db,
            viewer,
            dataset_id="dataset-a",
            dataset_version_id="version-a1",
            artifact_id="artifact-a1",
        )


def test_overview_excludes_metrics_from_unauthorized_datasets(contract_db: Session):
    owner = AccessContext("user-owner-a", "ws-a")
    viewer = AccessContext("user-viewer", "ws-a")
    grant_dataset_permissions(
        contract_db,
        owner,
        dataset_id="dataset-a",
        grantee_type="USER",
        grantee_id=viewer.user_id,
        permissions={"VIEW_PROFILE", "VIEW_REPORTS"},
    )

    overview = get_overview_metrics(contract_db, viewer)

    assert overview["shared"]["dataset_count"] == 1
    assert overview["shared"]["version_count"] == 1
    assert overview["shared"]["quality_score"] == 80.0
    assert overview["shared"]["rules"] == {"approved": 1, "rejected": 1}
    assert overview["shared"]["tests"] == {"pass": 7, "fail": 1}
    assert overview["shared"]["anomaly_count"] == 2
    assert all(row["dataset_id"] != "dataset-b" for row in overview["recent_runs"])


def test_profile_run_cannot_cross_dataset_or_version(contract_db: Session):
    owner = AccessContext("user-owner-a", "ws-a")
    viewer = AccessContext("user-viewer", "ws-a")
    grant_dataset_permissions(
        contract_db,
        owner,
        dataset_id="dataset-a",
        grantee_type="USER",
        grantee_id=viewer.user_id,
        permissions={"VIEW_PROFILE"},
    )

    no_selection = get_data_explorer(
        contract_db,
        viewer,
        dataset_id="dataset-a",
        dataset_version_id="version-a1",
    )
    assert no_selection["selected_profile"] is None

    with pytest.raises(ResourceNotFoundError):
        get_data_explorer(
            contract_db,
            viewer,
            dataset_id="dataset-a",
            dataset_version_id="version-a1",
            profile_run_id="profile-b1",
        )


def test_audit_logs_do_not_leak_other_dataset_or_workspace(contract_db: Session):
    owner = AccessContext("user-owner-a", "ws-a")
    viewer = AccessContext("user-viewer", "ws-a")
    grant_dataset_permissions(
        contract_db,
        owner,
        dataset_id="dataset-a",
        grantee_type="USER",
        grantee_id=viewer.user_id,
        permissions={"DISCOVER"},
    )

    visible = list_audit_events(contract_db, viewer, limit=100)

    assert visible
    assert {event["dataset_id"] for event in visible} == {"dataset-a"}
    assert list_audit_events(contract_db, viewer, dataset_id="dataset-b") == []


def _set_contract_reader(contract_db: Session, ctx: AccessContext) -> None:
    contract_db.execute(text("SET LOCAL ROLE ridepulse_contract_reader"))
    contract_db.execute(text("SELECT set_config('app.user_id', :user_id, true)"), {"user_id": ctx.user_id})
    contract_db.execute(
        text("SELECT set_config('app.workspace_id', :workspace_id, true)"),
        {"workspace_id": ctx.workspace_id},
    )


def test_postgres_rls_enforces_private_share_and_revoke(contract_db: Session):
    owner = AccessContext("user-owner-a", "ws-a")
    viewer = AccessContext("user-viewer", "ws-a")

    _set_contract_reader(contract_db, viewer)
    assert contract_db.execute(text("SELECT id FROM profile_runs ORDER BY id")).scalars().all() == []
    contract_db.execute(text("RESET ROLE"))

    grant_set_id = grant_dataset_permissions(
        contract_db,
        owner,
        dataset_id="dataset-a",
        grantee_type="USER",
        grantee_id=viewer.user_id,
        permissions={"VIEW_PROFILE"},
    )
    _set_contract_reader(contract_db, viewer)
    assert contract_db.execute(text("SELECT id FROM profile_runs ORDER BY id")).scalars().all() == ["profile-a1"]
    contract_db.execute(text("RESET ROLE"))

    revoke_grant_set(contract_db, owner, grant_set_id)
    _set_contract_reader(contract_db, viewer)
    assert contract_db.execute(text("SELECT id FROM profile_runs ORDER BY id")).scalars().all() == []


def test_governance_audit_events_are_append_only(contract_db: Session):
    connection = contract_db.connection()
    savepoint = connection.begin_nested()
    try:
        with pytest.raises(DBAPIError, match="append-only"):
            connection.execute(text("UPDATE governance_audit_events SET action = 'TAMPERED' WHERE id = 'audit-a'"))
    finally:
        savepoint.rollback()
