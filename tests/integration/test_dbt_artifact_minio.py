from __future__ import annotations

import os
import socket
from urllib.parse import urlparse

import pytest

from src.config import Settings
from src.services.dbt_artifact_store import DbtArtifactStore


def _is_minio_reachable() -> bool:
    endpoint = os.getenv("OBJECT_STORAGE_ENDPOINT_URL")
    if not endpoint:
        return False
    parsed = urlparse(endpoint)
    host = parsed.hostname or "localhost"
    port = parsed.port or 9000
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


@pytest.mark.skipif(
    not _is_minio_reachable(),
    reason="MinIO endpoint is not reachable",
)
def test_minio_upload_download_round_trip():
    settings = Settings(_env_file=None)
    store = DbtArtifactStore(settings)
    content = b"version: 2\nmodels: []\n"

    artifact = store.upload_yaml("docker-live-test", content, dataset_id="docker-test")

    assert artifact.object_key == "dbt-tests/runs/docker-live-test/generated_dq_tests.yml"
    assert store.download_yaml(artifact) == content
