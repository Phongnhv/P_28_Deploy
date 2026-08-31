"""Nâng số vòng PBKDF2 mà không khoá tài khoản cũ ra ngoài.

Không mang tham số trong chuỗi hash thì mọi lần nâng số vòng đều làm hỏng toàn
bộ hash đang có — và trên thực tế điều đó khiến số vòng không bao giờ được nâng.
"""

from __future__ import annotations

from hashlib import pbkdf2_hmac

import pytest

from src.config import Settings
from src.services.session_service import (
    _LEGACY_PBKDF2_ITERATIONS,
    hash_password,
    needs_rehash,
    pbkdf2_iterations,
    verify_password,
)


def _legacy_hash(password: str, salt: bytes = b"0123456789abcdef") -> str:
    """Tái tạo đúng định dạng cũ: `<salt_hex>$<digest_hex>`, 120 000 vòng."""
    digest = pbkdf2_hmac("sha256", password.encode(), salt, _LEGACY_PBKDF2_ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"


def test_default_iteration_count_meets_current_guidance():
    """Đọc từ MẶC ĐỊNH của Settings, không phải giá trị conftest đã hạ xuống.

    Bộ test chạy với số vòng thấp cho nhanh; khẳng định này là thứ duy nhất giữ
    cho giá trị thật không bị hạ theo.
    """
    assert Settings().password_hash_iterations >= 600_000


def test_new_hash_carries_its_parameters():
    encoded = hash_password("mat-khau-cua-toi")
    scheme, iterations, salt_hex, digest_hex = encoded.split("$")
    assert scheme == "pbkdf2"
    assert int(iterations) == pbkdf2_iterations()
    assert len(salt_hex) == 32
    assert len(digest_hex) == 64


def test_new_hash_round_trips():
    encoded = hash_password("mat-khau-cua-toi")
    assert verify_password("mat-khau-cua-toi", encoded) is True
    assert verify_password("mat-khau-khac", encoded) is False


def test_legacy_hash_still_verifies():
    """9 tài khoản đang tồn tại dùng định dạng cũ — không được khoá họ ra ngoài."""
    encoded = _legacy_hash("mat-khau-cu")
    assert verify_password("mat-khau-cu", encoded) is True
    assert verify_password("sai", encoded) is False


@pytest.fixture
def realistic_iterations():
    """Nâng ngưỡng lên trên số vòng của định dạng cũ.

    conftest hạ số vòng xuống 1 000 cho nhanh; ở mức đó một hash cũ (120 000)
    lại *mạnh hơn* tiêu chuẩn hiện hành nên không bị đánh dấu — che mất đúng
    hành vi mà các test này cần khoá lại.
    """
    from src.config import get_settings

    settings = get_settings()
    original = settings.password_hash_iterations
    settings.password_hash_iterations = _LEGACY_PBKDF2_ITERATIONS + 1_000
    yield
    settings.password_hash_iterations = original


def test_legacy_hash_is_flagged_for_rehash(realistic_iterations):
    assert needs_rehash(_legacy_hash("mat-khau-cu")) is True


def test_current_hash_is_not_flagged(realistic_iterations):
    assert needs_rehash(hash_password("mat-khau-cua-toi")) is False


@pytest.mark.parametrize("garbage", ["", "khong-co-dau-dollar", "a$b$c", "pbkdf2$x$y$z"])
def test_malformed_hash_never_authenticates(garbage):
    assert verify_password("bat-ky", garbage) is False
    assert needs_rehash(garbage) is True


@pytest.mark.asyncio
async def test_login_upgrades_legacy_hash_in_place(client, realistic_iterations):
    """Đăng nhập thành công là lần duy nhất ta cầm bản rõ để băm lại."""
    from sqlalchemy.orm import Session

    import src.services.rule_store as rs
    from src.models.database import UserAccountModel

    with Session(rs._engine) as db:
        account = db.query(UserAccountModel).filter_by(username="steward").first()
        account.password_hash = _legacy_hash("steward")
        db.commit()

    response = await client.post(
        "/api/v1/session", json={"username": "steward", "password": "steward"}
    )
    assert response.status_code == 200

    with Session(rs._engine) as db:
        account = db.query(UserAccountModel).filter_by(username="steward").first()
        assert account.password_hash.startswith("pbkdf2$")
        assert needs_rehash(account.password_hash) is False
