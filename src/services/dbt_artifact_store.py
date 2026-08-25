from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any

from src.config import Settings, get_settings

logger = logging.getLogger(__name__)

_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class DbtArtifactRef:
    bucket: str
    object_key: str
    sha256: str
    size_bytes: int
    etag: str | None = None
    version_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_run_id(run_id: str) -> str:
    if not _SAFE_RUN_ID.fullmatch(run_id):
        raise ValueError("run_id must contain only letters, numbers, '.', '_' or '-'")
    return run_id


def artifact_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class DbtArtifactStore:
    def __init__(self, settings: Settings | None = None, client: Any | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            if self.settings.object_storage_provider == "gcs":
                from google.cloud import storage

                # Cloud Run resolves this through the revision's attached
                # service account. Local runs use ADC or a credential file.
                self._client = storage.Client()
                return self._client

            import boto3
            from botocore.config import Config

            kwargs: dict[str, Any] = {
                "service_name": "s3",
                "region_name": self.settings.object_storage_region,
                "config": Config(
                    retries={"max_attempts": self.settings.object_storage_max_attempts, "mode": "standard"},
                    connect_timeout=self.settings.object_storage_connect_timeout_seconds,
                    read_timeout=self.settings.object_storage_read_timeout_seconds,
                    s3={"addressing_style": "path" if self.settings.object_storage_endpoint_url else "auto"},
                ),
            }
            if self.settings.object_storage_endpoint_url:
                kwargs["endpoint_url"] = self.settings.object_storage_endpoint_url
            if self.settings.object_storage_endpoint_url and self.settings.object_storage_access_key_id:
                kwargs["aws_access_key_id"] = self.settings.object_storage_access_key_id
            if self.settings.object_storage_endpoint_url and self.settings.object_storage_secret_access_key:
                kwargs["aws_secret_access_key"] = self.settings.object_storage_secret_access_key
            self._client = boto3.client(**kwargs)
        return self._client

    def object_key(self, run_id: str) -> str:
        safe_run_id = validate_run_id(run_id)
        prefix = self.settings.object_storage_prefix.strip("/")
        return f"{prefix}/runs/{safe_run_id}/generated_dq_tests.yml"

    def upload_yaml(
        self,
        run_id: str,
        content: bytes,
        *,
        dataset_id: str | None = None,
        rule_run_id: str | None = None,
    ) -> DbtArtifactRef:
        digest = artifact_sha256(content)
        object_key = self.object_key(run_id)
        metadata = {"test-run-id": run_id, "sha256": digest}
        if dataset_id:
            metadata["dataset-id"] = dataset_id
        if rule_run_id:
            metadata["rule-run-id"] = rule_run_id
        if self.settings.object_storage_provider == "gcs":
            blob = self.client.bucket(self.settings.object_storage_bucket).blob(object_key)
            blob.metadata = metadata
            blob.upload_from_string(content, content_type="application/yaml")
            etag = blob.etag
            version_id = str(blob.generation) if blob.generation is not None else None
        else:
            response = self.client.put_object(
                Bucket=self.settings.object_storage_bucket,
                Key=object_key,
                Body=content,
                ContentType="application/yaml",
                Metadata=metadata,
            )
            etag = response.get("ETag", "").strip('"') or None
            version_id = response.get("VersionId")
        return DbtArtifactRef(
            bucket=self.settings.object_storage_bucket,
            object_key=object_key,
            sha256=digest,
            size_bytes=len(content),
            etag=etag,
            version_id=version_id,
        )

    def download_yaml(self, artifact: DbtArtifactRef | dict[str, Any]) -> bytes:
        ref = artifact if isinstance(artifact, DbtArtifactRef) else DbtArtifactRef(**artifact)
        if self.settings.object_storage_provider == "gcs":
            blob = self.client.bucket(ref.bucket).blob(
                ref.object_key,
                generation=int(ref.version_id) if ref.version_id else None,
            )
            content = blob.download_as_bytes()
        else:
            request: dict[str, Any] = {"Bucket": ref.bucket, "Key": ref.object_key}
            if ref.version_id:
                request["VersionId"] = ref.version_id
            response = self.client.get_object(**request)
            content = response["Body"].read()
        if len(content) != ref.size_bytes:
            raise ValueError("Downloaded dbt artifact size does not match its metadata")
        if artifact_sha256(content) != ref.sha256:
            raise ValueError("Downloaded dbt artifact checksum does not match its metadata")
        return content

    def upload_dataset_file(
        self,
        dataset_id: str,
        filename: str,
        content: bytes,
    ) -> str:
        object_key = f"datasets/{dataset_id}/{filename}"
        bucket = self.settings.object_storage_bucket
        try:
            if self.settings.object_storage_provider == "gcs":
                blob = self.client.bucket(bucket).blob(object_key)
                blob.metadata = {"dataset-id": dataset_id, "filename": filename}
                blob.upload_from_string(content)
            else:
                try:
                    self.client.head_bucket(Bucket=bucket)
                except Exception:
                    self.client.create_bucket(Bucket=bucket)

                self.client.put_object(
                    Bucket=bucket,
                    Key=object_key,
                    Body=content,
                    Metadata={"dataset-id": dataset_id, "filename": filename},
                )
            logger.info("Successfully uploaded dataset %s to object storage bucket %s at key %s", dataset_id, bucket, object_key)
        except Exception as e:
            logger.warning("Failed to upload dataset %s to object storage: %s", dataset_id, e)
        return object_key


@lru_cache
def get_dbt_artifact_store() -> DbtArtifactStore:
    return DbtArtifactStore()
