import logging
import uuid
from datetime import datetime, timedelta

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from src.config import get_settings
from src.models.database import SessionModel

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "session_id"
SESSION_DURATION_HOURS = 8

# Seeded credentials
CREDENTIALS = {
    "user": {"password": "user", "role": "USER"},
    "steward": {"password": "steward", "role": "STEWARD"},
    "admin": {"password": "admin", "role": "ADMIN"},
}

def create_user_session(username: str, password: str, db: Session) -> SessionModel:
    """
    Validates credentials and creates a new session.
    """
    settings = get_settings()
    # Read credentials from settings or fallback to default dict
    expected_password = None
    role = None
    if username == "user":
        expected_password = getattr(settings, "demo_user_password", "user")
        role = "USER"
    elif username == "steward":
        expected_password = getattr(settings, "demo_steward_password", "steward")
        role = "STEWARD"
    elif username == "admin":
        expected_password = getattr(settings, "demo_admin_password", "admin")
        role = "ADMIN"

    if not expected_password or password != expected_password:
        raise HTTPException(
            status_code=401,
            detail={"code": "UNAUTHORIZED", "message": "Invalid username or password"}
        )

    # Clean up old sessions for this user
    try:
        db.query(SessionModel).filter(SessionModel.username == username).delete()
    except Exception as e:
        logger.warning("Failed to clean up old sessions: %s", e)

    session_id = str(uuid.uuid4())
    csrf_token = str(uuid.uuid4())
    expires_at = datetime.utcnow() + timedelta(hours=SESSION_DURATION_HOURS)

    session = SessionModel(
        id=session_id,
        username=username,
        role=role,
        csrf_token=csrf_token,
        expires_at=expires_at,
        created_at=datetime.utcnow()
    )
    db.add(session)
    db.commit()
    return session

def get_current_session(request: Request, db: Session) -> SessionModel:
    """
    Retrieves the current session from the session cookie and verifies it.
    """
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        raise HTTPException(
            status_code=401,
            detail="Session cookie is missing"
        )

    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not session:
        raise HTTPException(
            status_code=401,
            detail="Session not found"
        )

    if session.expires_at < datetime.utcnow():
        # Clean up expired session
        try:
            db.delete(session)
            db.commit()
        except Exception:
            pass
        raise HTTPException(
            status_code=401,
            detail="Session has expired"
        )

    return session

def verify_csrf(request: Request, session: SessionModel):
    """
    Verifies the CSRF token for state-changing requests.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return

    csrf_header = request.headers.get("X-CSRF-Token")
    if not csrf_header or csrf_header != session.csrf_token:
        raise HTTPException(
            status_code=422,
            detail="CSRF token is missing or invalid"
        )

def enforce_role(session: SessionModel, allowed_roles: list[str]):
    """
    Enforces that the current session's role is in the allowed roles list.
    """
    if session.role not in allowed_roles:
        raise HTTPException(
            status_code=403,
            detail="Forbidden: insufficient permissions"
        )
