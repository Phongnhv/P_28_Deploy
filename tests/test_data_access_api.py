from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from src.models.database import (
    DatasetGovernanceModel,
    DatasetVersionModel,
    ProfileRunSnapshotModel,
    WorkspaceMembershipModel,
    WorkspaceModel,
)
from src.services.data_access_service import AccessContext, grant_dataset_permissions

DATASET_ID = "dataset-nyc-yellow-taxi-50k"


@pytest.fixture
def governed_local_dataset(test_db):
    with Session(test_db) as db:
        db.add(WorkspaceModel(id="ws-local", name="Local workspace", created_by="user-steward"))
        db.add_all(
            [
                WorkspaceMembershipModel(
                    id="wm-local-steward",
                    workspace_id="ws-local",
                    user_id="user-steward",
                    role="STEWARD",
                ),
                WorkspaceMembershipModel(
                    id="wm-local-user",
                    workspace_id="ws-local",
                    user_id="user-user",
                    role="USER",
                ),
            ]
        )
        db.add(
            DatasetGovernanceModel(
                dataset_id=DATASET_ID,
                workspace_id="ws-local",
                owner_user_id="user-steward",
            )
        )
        db.add(
            DatasetVersionModel(
                id="local-version-1",
                workspace_id="ws-local",
                dataset_id=DATASET_ID,
                version_number=1,
                checksum="local-version-checksum",
                schema_hash="local-schema-hash",
                row_count=50_000,
                created_by="user-steward",
            )
        )
        db.flush()
        db.add(
            ProfileRunSnapshotModel(
                id="local-profile-1",
                workspace_id="ws-local",
                dataset_id=DATASET_ID,
                dataset_version_id="local-version-1",
                status="COMPLETED",
                triggered_by="user-steward",
                row_count=50_000,
                quality_score=92.5,
                schema_json="[]",
                metrics_json='{"completeness":0.98}',
                sanitized_samples_json="[]",
                completed_at=datetime.now(UTC),
            )
        )
        db.commit()
        grant_dataset_permissions(
            db,
            AccessContext("user-steward", "ws-local"),
            dataset_id=DATASET_ID,
            grantee_type="USER",
            grantee_id="user-user",
            permissions={"VIEW_PROFILE"},
        )


async def _login(client):
    response = await client.post("/api/v1/session", json={"username": "user", "password": "user"})
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_overview_v2_reads_authorized_local_metrics(client, governed_local_dataset):
    await _login(client)

    response = await client.get("/api/v2/workspaces/ws-local/overview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["shared"]["dataset_count"] == 1
    assert payload["shared"]["profiling_runs"] == 1
    assert payload["shared"]["quality_score"] == 92.5


@pytest.mark.asyncio
async def test_explorer_v2_does_not_fallback_for_unknown_profile_run(client, governed_local_dataset):
    await _login(client)

    response = await client.get(
        f"/api/v2/workspaces/ws-local/datasets/{DATASET_ID}/versions/local-version-1/explorer",
        params={"profile_run_id": "profile-from-another-version"},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "RESOURCE_NOT_FOUND"
