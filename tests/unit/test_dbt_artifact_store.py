from __future__ import annotations

import io

import pytest

from src.config import Settings
from src.services.dbt_artifact_store import DbtArtifactRef, DbtArtifactStore, artifact_sha256


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.last_put: dict | None = None

    def put_object(self, **kwargs):
        self.last_put = kwargs
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = kwargs["Body"]
        return {"ETag": '"test-etag"', "VersionId": "version-1"}

    def get_object(self, **kwargs):
        content = self.objects[(kwargs["Bucket"], kwargs["Key"])]
        return {"Body": io.BytesIO(content)}


class FakeGcsBlob:
    def __init__(self, objects: dict[tuple[str, str], bytes], bucket: str, key: str, generation=None) -> None:
        self.objects = objects
        self.bucket = bucket
        self.key = key
        self.metadata = None
        self.etag = None
        self.generation = generation

    def upload_from_string(self, content: bytes, content_type: str | None = None) -> None:
        self.objects[(self.bucket, self.key)] = content
        self.etag = "gcs-etag"
        self.generation = 42

    def download_as_bytes(self) -> bytes:
        return self.objects[(self.bucket, self.key)]


class FakeGcsBucket:
    def __init__(self, client, name: str) -> None:
        self.client = client
        self.name = name

    def blob(self, key: str, generation=None) -> FakeGcsBlob:
        return FakeGcsBlob(self.client.objects, self.name, key, generation)


class FakeGcsClient:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def bucket(self, name: str) -> FakeGcsBucket:
        return FakeGcsBucket(self, name)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        object_storage_provider="s3",
        object_storage_bucket="test-bucket",
        object_storage_prefix="dbt-tests",
        object_storage_endpoint_url="http://minio:9000",
        object_storage_access_key_id="test-key",
        object_storage_secret_access_key="test-secret",
    )


def test_upload_and_download_run_scoped_yaml():
    client = FakeS3Client()
    store = DbtArtifactStore(_settings(), client=client)
    content = b"version: 2\nmodels: []\n"

    artifact = store.upload_yaml(
        "run_123",
        content,
        dataset_id="dataset-1",
        rule_run_id="rules-1",
    )

    assert artifact.object_key == "dbt-tests/runs/run_123/generated_dq_tests.yml"
    assert artifact.sha256 == artifact_sha256(content)
    assert artifact.size_bytes == len(content)
    assert artifact.etag == "test-etag"
    assert artifact.version_id == "version-1"
    assert client.last_put["ContentType"] == "application/yaml"
    assert client.last_put["Metadata"]["test-run-id"] == "run_123"
    assert store.download_yaml(artifact) == content


def test_download_rejects_corrupt_content():
    client = FakeS3Client()
    store = DbtArtifactStore(_settings(), client=client)
    content = b"version: 2\n"
    artifact = store.upload_yaml("run_123", content)
    client.objects[(artifact.bucket, artifact.object_key)] = b"corrupted!\n"

    corrupt_ref = DbtArtifactRef(
        bucket=artifact.bucket,
        object_key=artifact.object_key,
        sha256=artifact.sha256,
        size_bytes=len(b"corrupted!\n"),
    )
    with pytest.raises(ValueError, match="checksum"):
        store.download_yaml(corrupt_ref)


def test_gcs_upload_and_download_uses_generation():
    client = FakeGcsClient()
    settings = _settings().model_copy(update={"object_storage_provider": "gcs"})
    store = DbtArtifactStore(settings, client=client)
    content = b"version: 2\nmodels: []\n"

    artifact = store.upload_yaml("gcs_run", content, dataset_id="dataset-1")

    assert artifact.etag == "gcs-etag"
    assert artifact.version_id == "42"
    assert store.download_yaml(artifact) == content


@pytest.mark.parametrize("run_id", ["../other-run", "run/id", "", "run id"])
def test_object_key_rejects_unsafe_run_ids(run_id):
    store = DbtArtifactStore(_settings(), client=FakeS3Client())

    with pytest.raises(ValueError, match="run_id"):
        store.object_key(run_id)
