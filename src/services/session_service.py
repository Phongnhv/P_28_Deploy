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

from src.models.database import SessionModel, UserAccountModel, WorkspaceMembershipModel, WorkspaceModel
from src.time_utils import utc_now

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "session_id"
SESSION_DURATION_HOURS = 8
DEFAULT_USERS = (
    ("user", "User", "USER", "DEMO_USER_PASSWORD"),
    ("steward", "Steward", "STEWARD", "DEMO_STEWARD_PASSWORD"),
    ("admin", "Admin", "ADMIN", "DEMO_ADMIN_PASSWORD"),
)
DEMO_STEWARD_USERNAME = "demo-steward"
DEMO_STEWARD_DISPLAY_NAME = "Demo Steward"
# This credential is intentionally public in the frontend for judge access.
# It is protected by the backend quota guard in ``demo_quota.py``.
DEMO_STEWARD_PUBLIC_PASSWORD = "ridepulse-demo-2026"
DEMO_STEWARD_WORKSPACE_ID = (os.getenv("DEMO_WORKSPACE_ID") or "ws-browser").strip()
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
        # Secret Manager values supplied through stdin commonly retain a final
        # newline; it is not part of the intended password.
        configured_password = (os.getenv(password_env) or "").strip()
        if production and not configured_password:
            raise RuntimeError(f"{password_env} must be configured in production")
        password = configured_password or username
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
        elif not production and account.created_by == "system-seed" and configured_password:
            # A local process may point at the shared production database. If
            # its demo password env vars are absent, never replace an existing
            # production hash with the development username fallback.
            account.password_hash = hash_password(configured_password)
    db.commit()


def ensure_demo_steward(db: Session) -> None:
    """Seed the bounded, judge-facing Steward account and its workspace seat."""
    configured_password = (os.getenv("DEMO_STEWARD_DEMO_PASSWORD") or "").strip()
    password = configured_password or DEMO_STEWARD_PUBLIC_PASSWORD
    account = db.query(UserAccountModel).filter(UserAccountModel.username == DEMO_STEWARD_USERNAME).first()
    if not account:
        account = UserAccountModel(
            id=f"user-{DEMO_STEWARD_USERNAME}",
            username=DEMO_STEWARD_USERNAME,
            display_name=DEMO_STEWARD_DISPLAY_NAME,
            password_hash=hash_password(password),
            role="STEWARD",
            status="ACTIVE",
            created_by="system-seed-demo",
        )
        db.add(account)
        db.flush()
    elif account.created_by == "system-seed-demo" and configured_password:
        account.password_hash = hash_password(configured_password)

    db.commit()
    try:
        workspace = db.get(WorkspaceModel, DEMO_STEWARD_WORKSPACE_ID)
        if workspace:
            membership = db.query(WorkspaceMembershipModel).filter_by(
                workspace_id=DEMO_STEWARD_WORKSPACE_ID,
                user_id=account.id,
            ).first()
            if not membership:
                db.add(
                    WorkspaceMembershipModel(
                        id=f"wm-{DEMO_STEWARD_USERNAME}-{DEMO_STEWARD_WORKSPACE_ID}",
                        workspace_id=DEMO_STEWARD_WORKSPACE_ID,
                        user_id=account.id,
                        role="STEWARD",
                        status="ACTIVE",
                    )
                )
            elif membership.status != "ACTIVE" or membership.role not in {"STEWARD", "ADMIN"}:
                membership.role = "STEWARD"
                membership.status = "ACTIVE"
        db.commit()
    except Exception:
        # The account remains usable for the legacy routes even if an older
        # database has not received the workspace tables yet.
        db.rollback()
        logger.warning("Demo Steward workspace membership could not be seeded", exc_info=True)


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

    # A user may have the workspace open in multiple tabs/devices.  Creating
    # a new session must not revoke those still-valid sessions, otherwise a
    # login in one tab makes another tab briefly render and then bounce back
    # to the login screen on its next authenticated request.  Expired rows are
    # safe to prune here and keep the session table bounded during sign-in.
    db.query(SessionModel).filter(
        SessionModel.expires_at < utc_now(),
    ).delete(synchronize_session=False)
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
