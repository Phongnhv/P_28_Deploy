import logging
import secrets
import uuid
from datetime import timedelta
from hashlib import pbkdf2_hmac

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from src.models.database import SessionModel, UserAccountModel
from src.time_utils import utc_now

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "session_id"
SESSION_DURATION_HOURS = 8
DEFAULT_USERS = (
    ("user", "User", "user", "USER"),
    ("steward", "Steward", "steward", "STEWARD"),
    ("admin", "Admin", "admin", "ADMIN"),
)


def hash_password(password: str, salt: bytes | None = None) -> str:
    """Return a PBKDF2 hash suitable for persisted local demo accounts."""
    actual_salt = salt or secrets.token_bytes(16)
    digest = pbkdf2_hmac("sha256", password.encode("utf-8"), actual_salt, 120_000)
    return f"{actual_salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        salt_hex, digest_hex = encoded.split("$", 1)
        expected = bytes.fromhex(digest_hex)
        actual = pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), 120_000)
    except (TypeError, ValueError):
        return False
    return secrets.compare_digest(actual, expected)


def ensure_default_users(db: Session) -> None:
    """Seed only the three documented local accounts when the database is empty."""
    for username, display_name, password, role in DEFAULT_USERS:
        if not db.query(UserAccountModel).filter(UserAccountModel.username == username).first():
            db.add(
                UserAccountModel(
                    id=f"user-{username}",
                    username=username,
                    display_name=display_name,
                    password_hash=hash_password(password),
                    role=role,
                    status="ACTIVE",
                    created_by="system-seed",
                )
            )
    db.commit()


def create_user_session(username: str, password: str, db: Session) -> SessionModel:
    """Authenticate an active persisted account and create its cookie session."""
    normalized_username = username.strip().lower()
    account = db.query(UserAccountModel).filter(UserAccountModel.username == normalized_username).first()
    if not account or account.status != "ACTIVE" or not verify_password(password, account.password_hash):
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "Invalid username or password"})

    db.query(SessionModel).filter(SessionModel.username == normalized_username).delete()
    session = SessionModel(
        id=str(uuid.uuid4()),
        username=normalized_username,
        role=account.role,
        csrf_token=str(uuid.uuid4()),
        expires_at=utc_now() + timedelta(hours=SESSION_DURATION_HOURS),
        created_at=utc_now(),
    )
    db.add(session)
    account.last_login_at = utc_now()
    db.commit()
    return session


def get_current_session(request: Request, db: Session) -> SessionModel:
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        raise HTTPException(status_code=401, detail={"code": "SESSION_REQUIRED", "message": "A session is required."})

    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not session:
        raise HTTPException(status_code=401, detail={"code": "SESSION_REQUIRED", "message": "A session is required."})
    if session.expires_at < utc_now():
        db.delete(session)
        db.commit()
        raise HTTPException(status_code=401, detail={"code": "SESSION_REQUIRED", "message": "The session has expired."})
    return session


def verify_csrf(request: Request, session: SessionModel) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    if request.headers.get("X-CSRF-Token") != session.csrf_token:
        raise HTTPException(
            status_code=422, detail={"code": "CSRF_INVALID", "message": "The CSRF token is missing or invalid."}
        )


def enforce_role(session: SessionModel, allowed_roles: list[str]) -> None:
    if session.role not in allowed_roles:
        raise HTTPException(
            status_code=403, detail={"code": "ROLE_FORBIDDEN", "message": "This action requires additional access."}
        )
