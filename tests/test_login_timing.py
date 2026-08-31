"""Đường đăng nhập thất bại phải tiêu tốn KDF như nhau, dù username có tồn tại hay không.

Bỏ qua `verify_password` khi không tìm thấy tài khoản trả lời trong khoảng một
mili-giây, trong khi username có thật tốn trọn một lần PBKDF2. Chênh lệch đó đo
được qua mạng và liệt kê ra toàn bộ username hợp lệ.

Kiểm bằng SỐ LẦN GỌI chứ không bằng đồng hồ: test đo thời gian chập chờn trên
máy có tải, còn tính chất cần khoá lại là "KDF luôn được chạy" — đó là điều
kiểm đếm được một cách tất định.
"""

from __future__ import annotations

import pytest

import src.services.session_service as ss


@pytest.fixture
def count_kdf(monkeypatch):
    calls: list[str] = []
    original = ss.verify_password

    def counting(password: str, encoded: str) -> bool:
        calls.append(encoded)
        return original(password, encoded)

    monkeypatch.setattr(ss, "verify_password", counting)
    return calls


@pytest.mark.asyncio
async def test_kdf_runs_for_existing_username(client, count_kdf):
    await client.post("/api/v1/session", json={"username": "steward", "password": "sai"})
    assert len(count_kdf) == 1


@pytest.mark.asyncio
async def test_kdf_also_runs_for_unknown_username(client, count_kdf):
    """Đây là tính chất chống liệt kê tài khoản.

    Thất bại ở đây nghĩa là `verify_password` lại bị nhánh tắt bỏ qua — nhiều
    khả năng do gộp lại vào chuỗi `or`.
    """
    await client.post("/api/v1/session", json={"username": "khong-ton-tai", "password": "bat-ky"})
    assert len(count_kdf) == 1, "Username không tồn tại đã bỏ qua KDF — lỗ rò thời gian quay lại"


@pytest.mark.asyncio
async def test_dummy_hash_is_never_a_real_credential(client, count_kdf):
    """Hash mồi phải là hash thật, không phải chuỗi rỗng hay giá trị canh sẵn."""
    await client.post("/api/v1/session", json={"username": "khong-ton-tai", "password": "bat-ky"})
    used = count_kdf[0]
    assert used == ss._DUMMY_PASSWORD_HASH
    assert "$" in used and len(used) > 64
    assert ss.verify_password("bat-ky", used) is False


@pytest.mark.asyncio
async def test_disabled_account_still_spends_kdf(client, count_kdf, monkeypatch):
    """Tài khoản bị vô hiệu hoá cũng không được rẽ nhánh sớm."""
    from sqlalchemy.orm import Session

    import src.services.rule_store as rs
    from src.models.database import UserAccountModel

    with Session(rs._engine) as db:
        account = db.query(UserAccountModel).filter_by(username="steward").first()
        account.status = "DISABLED"
        db.commit()

    await client.post("/api/v1/session", json={"username": "steward", "password": "steward"})
    assert len(count_kdf) == 1
