from __future__ import annotations

import os

import pytest

from src.config import Settings
from src.services.dbt_artifact_store import DbtArtifactStore


@pytest.mark.skipif(
    not os.getenv("OBJECT_STORAGE_ENDPOINT_URL"),
    reason="MinIO endpoint is not configured",
)
def test_minio_upload_download_round_trip():
    settings = Settings(_env_file=None)
    store = DbtArtifactStore(settings)
    content = b"version: 2\nmodels: []\n"

    artifact = store.upload_yaml("docker-live-test", content, dataset_id="docker-test")

    assert artifact.object_key == "dbt-tests/runs/docker-live-test/generated_dq_tests.yml"
    assert store.download_yaml(artifact) == content
