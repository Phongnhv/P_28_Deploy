"""Create and verify product-run provenance without trusting artifact contents."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from evalgate.core.context import EvalRunContext
from evalgate.schemas.artifact_manifest import ArtifactManifest, ArtifactManifestV2

SUPPORTED_SCHEMA_VERSION = "2.0"


@dataclass
class ProvenanceVerification:
    valid: bool
    manifest: ArtifactManifest | ArtifactManifestV2 | None = None
    reasons: list[str] = field(default_factory=list)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_manifest(
    manifest_path: Path,
    *,
    expected_git_sha: str | None = None,
    expected_dataset_fingerprint: str | None = None,
    expected_prompt_hash: str | None = None,
    expected_model: tuple[str, str, str] | None = None,
    require_clean: bool = False,
    require_v2: bool = False,
) -> ProvenanceVerification:
    reasons: list[str] = []
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = (ArtifactManifestV2 if payload.get("schema_version") == "2.0" else ArtifactManifest).model_validate(payload)
    except (OSError, AttributeError, TypeError, json.JSONDecodeError, ValidationError) as exc:
        return ProvenanceVerification(False, reasons=[f"invalid manifest: {exc}"])

    if require_v2 and manifest.schema_version != SUPPORTED_SCHEMA_VERSION:
        reasons.append(f"schema_version={manifest.schema_version} is diagnostic-only; version 2.0 is required")
    if expected_git_sha and not (
        expected_git_sha.startswith(manifest.git_sha)
        or manifest.git_sha.startswith(expected_git_sha)
    ):
        reasons.append(
            f"artifact git_sha={manifest.git_sha} does not match {expected_git_sha}"
        )
    if expected_dataset_fingerprint and manifest.dataset_fingerprint != expected_dataset_fingerprint:
        reasons.append("dataset fingerprint does not match the evaluation request")
    if expected_prompt_hash and manifest.prompt_hash != expected_prompt_hash:
        reasons.append("prompt hash does not match the evaluation request")
    if expected_model and isinstance(manifest, ArtifactManifestV2):
        actual_model = (manifest.model.provider, manifest.model.name, manifest.model.mode)
        if actual_model != expected_model:
            reasons.append("model identity does not match the evaluation request")
    if require_clean and manifest.workspace_dirty:
        reasons.append("artifact was produced from a dirty workspace")

    base = manifest_path.resolve().parent
    seen: set[str] = set()
    for artifact in manifest.artifacts:
        relative_path = getattr(artifact, "relative_path", None) or getattr(artifact, "path")
        if relative_path in seen:
            reasons.append(f"duplicate artifact path: {relative_path}")
            continue
        seen.add(relative_path)
        candidate = (base / relative_path).resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            reasons.append(f"artifact escapes manifest directory: {relative_path}")
            continue
        if not candidate.is_file():
            reasons.append(f"artifact is missing: {relative_path}")
        elif sha256_file(candidate) != artifact.sha256:
            reasons.append(f"artifact checksum mismatch: {relative_path}")
        elif isinstance(manifest, ArtifactManifestV2) and artifact.media_type == "application/json":
            try:
                document = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                reasons.append(f"artifact is not valid JSON: {relative_path}")
                continue
            if isinstance(document, dict):
                if document.get("run_id") not in {None, manifest.run_id}:
                    reasons.append(f"artifact run_id mismatch: {relative_path}")
                if document.get("dataset_id") not in {None, manifest.dataset_id}:
                    reasons.append(f"artifact dataset_id mismatch: {relative_path}")

    return ProvenanceVerification(not reasons, manifest=manifest, reasons=reasons)


def load_context(manifest_path: Path, *, profile: str, expected_git_sha: str | None = None):
    verification = verify_manifest(
        manifest_path, expected_git_sha=expected_git_sha,
        require_clean=profile != "local", require_v2=profile != "local",
    )
    if (verification.valid and isinstance(verification.manifest, ArtifactManifestV2)
            and profile in {"nightly", "pre_release"}
            and verification.manifest.model.mode != "live"):
        verification.valid = False
        verification.reasons.append(f"profile {profile} requires a live model identity")
    if not verification.valid or not isinstance(verification.manifest, ArtifactManifestV2):
        return None, verification
    manifest = verification.manifest
    return EvalRunContext(
        run_id=manifest.run_id, git_sha=manifest.git_sha,
        dataset_id=manifest.dataset_id, dataset_fingerprint=manifest.dataset_fingerprint,
        model=manifest.model, prompt_hash=manifest.prompt_hash,
        artifact_root=manifest_path.resolve().parent, manifest=manifest,
        profile=profile,
    ), verification
