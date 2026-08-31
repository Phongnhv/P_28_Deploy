"""Mọi phản hồi phải mang bộ header gia cố trình duyệt.

Đặt ở tầng middleware chứ không ở từng route: route thêm sau này thừa hưởng
mặc định, thay vì phải nhớ gắn.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("x-content-type-options", "nosniff"),
        ("x-frame-options", "DENY"),
        ("referrer-policy", "strict-origin-when-cross-origin"),
    ],
)
async def test_security_headers_present(client, header, expected):
    response = await client.get("/health")
    assert response.headers.get(header) == expected


@pytest.mark.asyncio
async def test_csp_is_report_only_outside_production(client):
    """CSP phải ở chế độ báo cáo cho tới khi vi phạm được rà xong.

    Bật cưỡng chế trước khi biết frontend dùng inline style/script ở đâu sẽ làm
    trắng trang mà không có cảnh báo nào.
    """
    response = await client.get("/health")
    assert "content-security-policy-report-only" in response.headers
    assert "content-security-policy" not in response.headers
    assert "frame-ancestors 'none'" in response.headers["content-security-policy-report-only"]


@pytest.mark.asyncio
async def test_hsts_absent_outside_production(client):
    """HSTS trên HTTP local sẽ ghim trình duyệt vào scheme mà dev server không nói."""
    response = await client.get("/health")
    assert "strict-transport-security" not in response.headers


@pytest.mark.asyncio
async def test_headers_present_on_error_responses(client):
    """Phản hồi lỗi cũng phải mang header — đó là nơi hay bị bỏ sót nhất."""
    response = await client.get("/api/v1/dq/runs/khong-ton-tai/rules")
    assert response.status_code in (401, 403, 404)
    assert response.headers.get("x-content-type-options") == "nosniff"
