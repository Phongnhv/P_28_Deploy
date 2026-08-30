"""Immutable context: the only supported source of runtime evaluation evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from evalgate.schemas.artifact_manifest import ArtifactManifestV2, ArtifactRecord, ModelIdentity


@dataclass(frozen=True)
class EvalRunContext:
    run_id: str
    git_sha: str
    dataset_id: str
    dataset_fingerprint: str
    model: ModelIdentity
    prompt_hash: str
    artifact_root: Path
    manifest: ArtifactManifestV2
    profile: Literal["local", "ci", "nightly", "pre_release"]

    def records(self, artifact_type: str) -> tuple[ArtifactRecord, ...]:
        return tuple(a for a in self.manifest.artifacts if a.type == artifact_type)

    def require_records(self, artifact_type: str) -> tuple[ArtifactRecord, ...]:
        records = self.records(artifact_type)
        if not records:
            raise KeyError(f"manifest has no artifact of type {artifact_type}")
        return records

    def path_for(self, record: ArtifactRecord | str) -> Path:
        if isinstance(record, str):
            records = self.require_records(record)
            if len(records) != 1:
                raise ValueError(f"manifest contains ambiguous {record} artifacts")
            record = records[0]
        root = self.artifact_root.resolve()
        candidate = (root / record.relative_path).resolve()
        candidate.relative_to(root)
        return candidate

    def read_json(self, artifact_type: str, *, many: bool = False):
        records = self.require_records(artifact_type)
        if not many and len(records) != 1:
            raise ValueError(f"manifest contains ambiguous {artifact_type} artifacts")
        payloads = [json.loads(self.path_for(record).read_text(encoding="utf-8"))
                    for record in records]
        return payloads if many else payloads[0]
