"""Sự kiện bảo mật phải để lại dấu vết trong `audit_events`.

`login_attempts` là bộ đếm để chặn, không phải nhật ký để điều tra: nó bị xoá
khi hết cửa sổ 15 phút và khi đăng nhập thành công. Nếu không ghi audit thì một
đợt brute-force đã diễn ra không để lại dấu vết nào.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.orm import Session

import src.services.rule_store as rs
from src.models.database import AuditEventModel


def _events(action_code: str) -> list[AuditEventModel]:
    with Session(rs._engine) as db:
        return db.query(AuditEventModel).filter_by(action_code=action_code).all()


@pytest.mark.asyncio
async def test_failed_login_writes_audit_event(client):
    response = await client.post(
        "/api/v1/session",
        json={"username": "steward", "password": "mat-khau-sai"},
    )
    assert response.status_code == 401

    rows = _events("LOGIN_FAILED")
    assert len(rows) == 1
    assert rows[0].entity_id == "steward"
    assert "ip" in json.loads(rows[0].detail_json)


@pytest.mark.asyncio
async def test_unknown_username_also_writes_audit_event(client):
    """Username không tồn tại vẫn phải để lại dấu vết.

    Đây chính là hình dạng của một đợt liệt kê tài khoản — bỏ qua nó thì phần
    tấn công nguy hiểm nhất lại là phần vô hình nhất.
    """
    response = await client.post(
        "/api/v1/session",
        json={"username": "khong-ton-tai", "password": "bat-ky"},
    )
    assert response.status_code == 401
    assert len(_events("LOGIN_FAILED")) == 1


@pytest.mark.asyncio
async def test_rate_limit_writes_audit_event(client):
    """Chạm ngưỡng throttle là tín hiệu tấn công rõ ràng nhất."""
    for _ in range(6):
        await client.post(
            "/api/v1/session",
            json={"username": "steward", "password": "sai"},
        )

    rows = _events("LOGIN_RATE_LIMITED")
    assert rows, "Chạm ngưỡng 429 nhưng không có dòng audit nào"


@pytest.mark.asyncio
async def test_audit_survives_when_login_transaction_fails(client):
    """Dòng audit ghi bằng session riêng, không phụ thuộc transaction chính.

    Nếu dùng chung session của caller, `add_audit_event` sẽ `commit()` luôn phần
    việc dang dở của request ngay trước một lệnh `raise`.
    """
    await client.post("/api/v1/session", json={"username": "steward", "password": "sai"})
    assert len(_events("LOGIN_FAILED")) == 1
