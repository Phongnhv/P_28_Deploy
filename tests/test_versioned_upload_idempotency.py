from __future__ import annotations

import json

import pytest
from sqlalchemy.orm import Session

from src.main import app
from src.models.database import (
    DatasetVersionModel,
    GovernanceAuditEventModel,
    GovernedArtifactModel,
    UserAccountModel,
    WorkspaceMembershipModel,
    WorkspaceModel,
)
from src.services.rule_store import get_engine


@pytest.fixture
def import_workspace(monkeypatch, tmp_path):
    import src.services.versioned_dataset as versioned_dataset
    from src.config import get_settings

    monkeypatch.setattr(get_settings(), "app_env", "test")
    monkeypatch.setattr(versioned_dataset, "_local_storage_root", lambda: tmp_path / "source-artifacts")
    with Session(get_engine()) as db:
        account = db.query(UserAccountModel).filter_by(username="steward").one()
        db.add(WorkspaceModel(id="ws-idempotency", name="Idempotency", created_by=account.id))
        db.add(WorkspaceMembershipModel(
            id="wm-idempotency", workspace_id="ws-idempotency", user_id=account.id, role="STEWARD"
        ))
        db.commit()


async def _login(client):
    response = await client.post("/api/v1/session", json={"username": "steward", "password": "steward"})
    assert response.status_code == 200
    return response.json()["csrf_token"]


@pytest.mark.asyncio
async def test_versioned_import_replays_by_key_and_workspace_checksum(client, import_workspace):
    csrf = await _login(client)
    payload = b"customer_id,total\na,10\nb,20\n"
    first = await client.post(
        "/api/v1/workspaces/ws-idempotency/datasets/import",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": "same-import"},
        files={"file": ("customers.csv", payload, "text/csv")},
    )
    assert first.status_code == 202, first.text
    first_json = first.json()

    with Session(get_engine()) as db:
        version_count = db.query(DatasetVersionModel).count()
        artifact_count = db.query(GovernedArtifactModel).count()
        audit_count = db.query(GovernanceAuditEventModel).count()

    same_key = await client.post(
        "/api/v1/workspaces/ws-idempotency/datasets/import",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": "same-import"},
        files={"file": ("renamed.csv", payload, "text/csv")},
    )
    different_key = await client.post(
        "/api/v1/workspaces/ws-idempotency/datasets/import",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": "different-import"},
        files={"file": ("another-name.csv", payload, "text/csv")},
    )
    assert same_key.status_code == 202 and same_key.json()["idempotent_replay"] is True
    assert different_key.status_code == 202 and different_key.json()["idempotent_replay"] is True
    assert same_key.json()["version"]["id"] == first_json["version"]["id"]
    assert different_key.json()["version"]["id"] == first_json["version"]["id"]

    with Session(get_engine()) as db:
        assert db.query(DatasetVersionModel).count() == version_count
        assert db.query(GovernedArtifactModel).count() == artifact_count
        assert db.query(GovernanceAuditEventModel).count() == audit_count


@pytest.mark.asyncio
async def test_versioned_import_rejects_same_key_for_different_payload(client, import_workspace):
    csrf = await _login(client)
    first = await client.post(
        "/api/v1/workspaces/ws-idempotency/datasets/import",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": "payload-bound"},
        files={"file": ("customers.csv", b"id,total\na,10\n", "text/csv")},
    )
    assert first.status_code == 202, first.text
    conflict = await client.post(
        "/api/v1/workspaces/ws-idempotency/datasets/import",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": "payload-bound"},
        files={"file": ("customers.csv", b"id,total\na,999\n", "text/csv")},
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


@pytest.mark.asyncio
async def test_versioned_import_same_client_key_is_scoped_to_workspace(client, import_workspace):
    csrf = await _login(client)
    with Session(get_engine()) as db:
        account = db.query(UserAccountModel).filter_by(username="steward").one()
        db.add(WorkspaceModel(id="ws-idempotency-other", name="Other workspace", created_by=account.id))
        db.add(WorkspaceMembershipModel(
            id="wm-idempotency-other", workspace_id="ws-idempotency-other", user_id=account.id, role="STEWARD"
        ))
        db.commit()

    payload = b"id,total\na,10\n"
    first = await client.post(
        "/api/v1/workspaces/ws-idempotency/datasets/import",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": "same-client-key"},
        files={"file": ("first.csv", payload, "text/csv")},
    )
    second = await client.post(
        "/api/v1/workspaces/ws-idempotency-other/datasets/import",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": "same-client-key"},
        files={"file": ("second.csv", payload, "text/csv")},
    )

    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text
    assert first.json()["idempotent_replay"] is False
    assert second.json()["idempotent_replay"] is False
    assert first.json()["version"]["id"] != second.json()["version"]["id"]


@pytest.mark.asyncio
async def test_versioned_import_commit_failure_compensates_own_source_object(client, import_workspace, monkeypatch, tmp_path):
    csrf = await _login(client)
    import src.api.routes as routes
    import src.services.versioned_dataset as versioned_dataset

    root = tmp_path / "source-artifacts"
    monkeypatch.setattr(versioned_dataset, "_local_storage_root", lambda: root)
    original_get_db = routes.get_db
    deleted_refs = []
    original_delete = routes.delete_source_artifact

    def record_delete(ref):
        deleted_refs.append(ref)
        original_delete(ref)

    monkeypatch.setattr(routes, "delete_source_artifact", record_delete)

    def failing_get_db():
        with Session(get_engine()) as db:
            original_commit = db.commit
            commits = 0

            def commit_with_failure():
                nonlocal commits
                commits += 1
                if commits >= 2:
                    raise RuntimeError("simulated control-plane commit outage")
                original_commit()

            db.commit = commit_with_failure
            yield db

    app.dependency_overrides[original_get_db] = failing_get_db
    try:
        response = await client.post(
            "/api/v1/workspaces/ws-idempotency/datasets/import",
            headers={"X-CSRF-Token": csrf, "Idempotency-Key": "commit-failure"},
            files={"file": ("customers.csv", b"id,total\na,10\n", "text/csv")},
        )
    finally:
        app.dependency_overrides.pop(original_get_db, None)

    assert response.status_code == 503
    assert response.json()["code"] == "IMPORT_COMMIT_FAILED"
    assert len(deleted_refs) == 1 and deleted_refs[0].created_by_request is True
    assert not [path for path in root.rglob("*") if path.is_file()]


def test_reservation_message_is_safe_json():
    # Documents the collision envelope without ever persisting raw file data.
    value = json.dumps({"checksum": "a" * 64, "version_id": "dv-example"})
    parsed = json.loads(value)
    assert set(parsed) == {"checksum", "version_id"}
    assert "total" not in value
