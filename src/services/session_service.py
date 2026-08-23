import logging
import os
import secrets
import uuid
from collections import defaultdict, deque
from datetime import timedelta
from hashlib import pbkdf2_hmac
from time import monotonic

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from src.models.database import SessionModel, UserAccountModel
from src.time_utils import utc_now

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "session_id"
SESSION_DURATION_HOURS = 8
DEFAULT_USERS = (
    ("user", "User", "USER", "DEMO_USER_PASSWORD"),
    ("steward", "Steward", "STEWARD", "DEMO_STEWARD_PASSWORD"),
    ("admin", "Admin", "ADMIN", "DEMO_ADMIN_PASSWORD"),
)
LOGIN_WINDOW_SECONDS = 15 * 60
MAX_LOGIN_ATTEMPTS = 5
_login_attempts: dict[str, deque[float]] = defaultdict(deque)


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
    """Seed demo accounts from secrets, never from production source defaults."""
    production = os.getenv("APP_ENV") == "production"
    for username, display_name, role, password_env in DEFAULT_USERS:
        password = os.getenv(password_env)
        if production and not password:
            raise RuntimeError(f"{password_env} must be configured in production")
        password = password or username
        account = db.query(UserAccountModel).filter(UserAccountModel.username == username).first()
        if not account:
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
        elif account.created_by == "system-seed":
            account.password_hash = hash_password(password)
    db.commit()


def _login_attempt_key(request: Request, username: str) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    client_host = forwarded_for or (request.client.host if request.client else "unknown")
    return f"{client_host}:{username}"


def _enforce_login_rate_limit(key: str) -> None:
    now = monotonic()
    attempts = _login_attempts[key]
    while attempts and now - attempts[0] >= LOGIN_WINDOW_SECONDS:
        attempts.popleft()
    if len(attempts) >= MAX_LOGIN_ATTEMPTS:
        raise HTTPException(status_code=429, detail={"code": "LOGIN_RATE_LIMITED", "message": "Too many sign-in attempts. Try again later."})


def _record_failed_login(key: str) -> None:
    _login_attempts[key].append(monotonic())


def create_user_session(request: Request, username: str, password: str, db: Session) -> SessionModel:
    """Authenticate an active persisted account and create its cookie session."""
    normalized_username = username.strip().lower()
    attempt_key = _login_attempt_key(request, normalized_username)
    _enforce_login_rate_limit(attempt_key)
    account = db.query(UserAccountModel).filter(UserAccountModel.username == normalized_username).first()
    if not account or account.status != "ACTIVE" or not verify_password(password, account.password_hash):
        _record_failed_login(attempt_key)
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "Invalid username or password"})

    _login_attempts.pop(attempt_key, None)

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
    session_expires = session.expires_at
    current_time = utc_now()
    if session_expires.tzinfo is None and current_time.tzinfo is not None:
        current_time = current_time.replace(tzinfo=None)
    elif session_expires.tzinfo is not None and current_time.tzinfo is None:
        session_expires = session_expires.replace(tzinfo=None)

    if session_expires < current_time:
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
