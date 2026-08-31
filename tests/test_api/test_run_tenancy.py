"""Tenancy hồi quy cho bảy endpoint /dq gắn `require_run_access`.

`dq_router` chỉ được mount kèm một dependency VAI TRÒ. Nếu `require_run_access`
biến mất khỏi decorator, cả bảy endpoint vẫn trả 200 cho người dùng có đúng vai
trò nhưng thuộc tenant khác — tức là đọc, review và publish được ruleset của
tenant khác chỉ cần biết `run_id`.

Bộ test này khoá đúng hành vi đó lại. Nó phải thất bại nếu ai đó gỡ dependency
để cho hết lỗi import.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

import src.services.rule_store as rs
from src.models.database import DatasetAccessModel, DatasetVersionModel
from src.services.rule_store import create_run

FOREIGN_DATASET = "dataset-thuoc-tenant-khac"


def _seed_foreign_run() -> str:
    """Tạo một proposal run mà tài khoản `steward` KHÔNG có quyền truy cập."""
    run_id = uuid.uuid4().hex
    create_run(run_id, FOREIGN_DATASET)
    with Session(rs._engine) as db:
        # Cố ý KHÔNG tạo DatasetAccessModel cho `steward`. Cấp cho một người khác
        # để chứng minh dataset có tồn tại và có chủ — 403 đến từ việc thiếu quyền,
        # không phải từ việc dataset không tồn tại.
        db.add(
            DatasetAccessModel(
                id=f"access-{uuid.uuid4().hex[:12]}",
                dataset_id=FOREIGN_DATASET,
                username="nguoi-khac",
                access_level="MANAGE",
                granted_by="system",
            )
        )
        db.commit()
    return run_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path_tpl", "payload"),
    [
        ("get", "/api/v1/dq/runs/{run_id}/rules", None),
        ("get", "/api/v1/dq/runs/{run_id}/review-summary", None),
        ("get", "/api/v1/dq/runs/{run_id}/approved-rules", None),
        ("post", "/api/v1/dq/runs/{run_id}/publish", {}),
        (
            "patch",
            "/api/v1/dq/runs/{run_id}/rules/t.vendor_id.NOT_NULL",
            {"status": "APPROVED"},
        ),
        (
            "post",
            "/api/v1/dq/runs/{run_id}/rules/bulk-review",
            {"decisions": []},
        ),
        ("post", "/api/v1/dq/rule-runs/{run_id}/publish", {}),
    ],
)
async def test_cross_tenant_run_access_is_forbidden(steward_client, method, path_tpl, payload):
    """STEWARD của tenant này không được chạm vào run của tenant kia.

    403 chứ không phải 200: có đúng vai trò ở đâu đó không đồng nghĩa có quyền
    trên dataset của run này.
    """
    run_id = _seed_foreign_run()
    path = path_tpl.format(run_id=run_id)

    request = getattr(steward_client, method)
    response = await request(path) if payload is None else await request(path, json=payload)

    assert response.status_code == 403, (
        f"{method.upper()} {path} trả {response.status_code} thay vì 403 — "
        "kiểm tra tenant đã bị gỡ khỏi decorator?"
    )
    assert response.json()["code"] == "DATASET_ACCESS_FORBIDDEN"


@pytest.mark.asyncio
async def test_run_linked_to_dataset_version_resolves_to_owner(steward_client):
    """Run gắn với ID phiên bản (`dv-...`) phải giải tham chiếu về dataset gốc.

    `JobModel.linked_entity` giữ hoặc `dataset_id`, hoặc `dataset_version_id`.
    Không giải tham chiếu thì ID `dv-` không bao giờ khớp `dataset_access` và
    chính chủ sở hữu hợp lệ cũng bị từ chối — lỗi fail-closed, im lặng.
    """
    dataset_id = "dataset-co-phien-ban"
    version_id = f"dv-{uuid.uuid4().hex[:24]}"
    run_id = uuid.uuid4().hex

    create_run(run_id, version_id)  # linked_entity = ID phiên bản, không phải dataset

    with Session(rs._engine) as db:
        db.add(
            DatasetVersionModel(
                id=version_id,
                workspace_id="ws-test",
                dataset_id=dataset_id,
                version_number=1,
                status="READY",
                checksum="sha256:" + "0" * 64,
                schema_hash="sha256:" + "1" * 64,
                created_by="user-steward",
            )
        )
        db.add(
            DatasetAccessModel(
                id=f"access-{uuid.uuid4().hex[:12]}",
                dataset_id=dataset_id,
                username="steward",
                access_level="MANAGE",
                granted_by="system",
            )
        )
        db.commit()

    response = await steward_client.get(f"/api/v1/dq/runs/{run_id}/rules")

    assert response.status_code == 200, (
        f"Chủ sở hữu hợp lệ nhận {response.status_code} — "
        "bước giải tham chiếu ID phiên bản bị thiếu?"
    )
