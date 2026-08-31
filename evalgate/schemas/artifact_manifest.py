"""Closed, versioned contracts for provenance-bound EvalGate evidence."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _safe_relative(value: str) -> str:
    normal = value.replace("\\", "/")
    if (not normal or normal.startswith(("/", "../")) or "/../" in normal
            or normal.endswith("/..") or ":" in normal.split("/", 1)[0]):
        raise ValueError("artifact paths must stay below the manifest directory")
    return normal


class ArtifactDigest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("path")
    @classmethod
    def relative_safe_path(cls, value: str) -> str:
        return _safe_relative(value)


class ArtifactManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    run_id: str = Field(min_length=1)
    git_sha: str = Field(pattern=r"^[0-9a-f]{7,40}$")
    workspace_dirty: bool
    created_at: datetime
    dataset_id: str = Field(min_length=1)
    dataset_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    workflow: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifacts: list[ArtifactDigest] = Field(min_length=1)


class ModelIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: str = Field(min_length=1)
    name: str = Field(min_length=1)
    mode: Literal["deterministic-test", "live"]


class ArtifactRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: str = Field(min_length=1)
    producer: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    created_at: datetime

    @field_validator("relative_path")
    @classmethod
    def relative_safe_path(cls, value: str) -> str:
        return _safe_relative(value)


class ArtifactManifestV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["2.0"]
    finalized: Literal[True]
    run_id: str = Field(min_length=1)
    git_sha: str = Field(pattern=r"^[0-9a-f]{7,40}$")
    workspace_dirty: bool
    created_at: datetime
    dataset_id: str = Field(min_length=1)
    #: Which corpus the bundle was generated from, e.g. ``corpus-nyc-taxi-50k``.
    #:
    #: Distinct from ``dataset_id`` on purpose. The product mints a fresh
    #: ``dataset-import-<uuid>`` on every upload, so the runtime id identifies *this
    #: run* and can never be written down in advance. Ground truth has to bind to
    #: the corpus, which is stable across runs; a golden case keyed on the runtime
    #: id matches nothing, forever, and turns the whole suite off without a word.
    #:
    #: Optional so manifests written before this field remain readable.
    corpus_id: str | None = None
    dataset_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    model: ModelIdentity
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    workflow: str = Field(min_length=1)
    product_version: str = Field(min_length=1)
    artifacts: tuple[ArtifactRecord, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def consistent_lineage(self):
        names: set[str] = set()
        paths: set[str] = set()
        for artifact in self.artifacts:
            if artifact.name in names:
                raise ValueError(f"duplicate artifact name: {artifact.name}")
            if artifact.relative_path in paths:
                raise ValueError(f"duplicate artifact path: {artifact.relative_path}")
            if artifact.run_id != self.run_id:
                raise ValueError(f"artifact {artifact.name} has a different run_id")
            if artifact.dataset_id != self.dataset_id:
                raise ValueError(f"artifact {artifact.name} has a different dataset_id")
            names.add(artifact.name)
            paths.add(artifact.relative_path)
        return self
