"""Resolve immutable dataset evidence; never guess a physical table from an ID."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from src.models.database import DatasetModel, DatasetVersionModel, GovernedArtifactModel, ProfileRunSnapshotModel
from src.services.versioned_dataset import SourceIntegrityError


def dataset_source_version(db: Session, dataset_id: str, dataset_version_id: str | None = None) -> DatasetVersionModel:
    """The uploaded source belonging to this dataset; never pick a newer version."""
    dataset = db.get(DatasetModel, dataset_id)
    if not dataset:
        raise SourceIntegrityError("SOURCE_BINDING_INVALID: Dataset not found")
    query = db.query(DatasetVersionModel).filter_by(dataset_id=dataset_id, status="READY")
    # Existing datasets may retain historical versions. Their recorded upload
    # checksum identifies the source to use without changing or deleting history.
    if dataset.checksum:
        query = query.filter_by(checksum=dataset.checksum)
    versions = query.order_by(DatasetVersionModel.version_number.asc()).all()
    if not versions or (not dataset.checksum and len(versions) != 1):
        raise SourceIntegrityError("SOURCE_BINDING_INVALID: Dataset has no unambiguous uploaded source")
    version = versions[0]
    if dataset_version_id and dataset_version_id != version.id:
        raise SourceIntegrityError("SOURCE_BINDING_INVALID: Requested source belongs to another dataset or is no longer its uploaded source")
    return version


def resolve_source_binding(
    db: Session, dataset_id: str, *, dataset_version_id: str | None = None,
    profile_run_id: str | None = None, require_profile: bool = True,
) -> dict[str, Any]:
    dataset = db.get(DatasetModel, dataset_id)
    if not dataset:
        raise SourceIntegrityError("SOURCE_BINDING_INVALID: Dataset not found")
    if dataset.manifest_version != "versioned-v1" and not db.query(DatasetVersionModel).filter_by(dataset_id=dataset_id).first():
        raise SourceIntegrityError("SOURCE_BINDING_REQUIRED: Import the dataset as an immutable version first")
    version = dataset_source_version(db, dataset_id, dataset_version_id)
    metadata = json.loads(version.source_metadata_json or "{}")
    artifacts = db.query(GovernedArtifactModel).filter_by(
        workspace_id=version.workspace_id, dataset_id=dataset_id,
        dataset_version_id=version.id, artifact_type="SOURCE_DATASET",
    )
    if metadata.get("source_artifact_id"):
        artifacts = artifacts.filter_by(id=metadata["source_artifact_id"])
    artifact = artifacts.one_or_none()
    if not artifact or artifact.checksum != version.checksum:
        raise SourceIntegrityError("SOURCE_BINDING_INVALID: Version has no matching source artifact")
    profiles = db.query(ProfileRunSnapshotModel).filter_by(
        workspace_id=version.workspace_id, dataset_id=dataset_id,
        dataset_version_id=version.id, status="COMPLETED",
    )
    profile = (profiles.filter_by(id=profile_run_id).first() if profile_run_id else
               profiles.order_by(ProfileRunSnapshotModel.completed_at.desc()).first()) if require_profile else None
    if require_profile and not profile:
        raise SourceIntegrityError("PROFILE_BINDING_INVALID: No completed profile for the selected dataset version")
    if profile and profile.row_count != version.row_count:
        raise SourceIntegrityError("PROFILE_BINDING_INVALID: Profile row count differs from immutable source")
    locator = artifact.storage_locator
    if not locator.startswith(("local:", "object://")):
        raise SourceIntegrityError("SOURCE_BINDING_INVALID: Unsupported source locator")
    return {
        "dataset_id": dataset_id, "dataset_version_id": version.id,
        "workspace_id": version.workspace_id, "profile_run_id": profile.id if profile else None,
        "source_kind": "object" if locator.startswith("object://") else "local",
        "source_ref": artifact.id, "checksum": version.checksum, "row_count": version.row_count,
        "schema_hash": version.schema_hash,
    }


def workflow_binding(db: Session, run: Any, *, require_profile: bool = True) -> dict[str, Any] | None:
    stage = next((s for s in json.loads(run.steps_json or "[]") if s.get("key") == "UPLOAD_PROFILE"), {})
    binding = stage.get("source_binding")
    dataset = db.get(DatasetModel, run.dataset_id)
    if not binding:
        if dataset and dataset.manifest_version == "versioned-v1":
            raise SourceIntegrityError("SOURCE_BINDING_REQUIRED: Start a new workflow to bind an immutable source")
        return None
    if binding.get("dataset_id") != run.dataset_id or not binding.get("dataset_version_id"):
        raise SourceIntegrityError("SOURCE_BINDING_INVALID: Workflow dataset/version mismatch")
    if require_profile and not binding.get("profile_run_id"):
        raise SourceIntegrityError("PROFILE_BINDING_REQUIRED: Complete fresh profiling first")
    resolved = resolve_source_binding(
        db, run.dataset_id, dataset_version_id=binding["dataset_version_id"],
        profile_run_id=binding.get("profile_run_id"), require_profile=require_profile,
    )
    for field in ("dataset_id", "dataset_version_id", "workspace_id", "source_ref", "checksum", "schema_hash", "row_count", "source_kind"):
        if binding.get(field) != resolved[field]:
            raise SourceIntegrityError(f"SOURCE_BINDING_INVALID: Workflow {field} mismatch")
    return binding
