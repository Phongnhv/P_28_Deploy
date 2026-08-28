"""Bounded public demo access for the judge-facing Steward account.

The demo account is intentionally public, so it must never be treated as a
secret. Its write-side budget is persisted in the existing audit table. That
keeps the guard effective across Cloud Run instances without requiring a new
database migration on the deployed Supabase schema.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from src.models.database import AuditEventModel, SessionModel, UserAccountModel
from src.services.session_service import DEMO_STEWARD_USERNAME
from src.time_utils import utc_now

DEMO_QUOTA_EVENT = "DEMO_QUOTA_CONSUMED"
DEMO_QUOTA_WINDOW_HOURS = 24
DEMO_QUOTA_LIMITS = {
    "api": 40,
    "upload": 3,
    "profiling": 3,
    "analysis": 2,
}


def quota_action(request: Request) -> str | None:
    """Map an authenticated write request to the smallest useful quota."""
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return None

    path = request.url.path
    if path.endswith("/session"):
        return None
    if "/analysis-runs" in path:
        return "analysis"
    if "/datasets/import" in path:
        return "upload"
    if "/graph1-runs" in path or path.endswith("/ingestions"):
        return "profiling"
    return "api"


def _quota_query(db: Session, session: SessionModel, cutoff):
    return (
        db.query(AuditEventModel)
        .join(SessionModel, AuditEventModel.session_id == SessionModel.id)
        .filter(
            SessionModel.username == session.username,
            AuditEventModel.entity_type == "demo_quota",
            AuditEventModel.action_code == DEMO_QUOTA_EVENT,
            AuditEventModel.created_at >= cutoff,
        )
    )


def enforce_demo_quota(db: Session, request: Request, session: SessionModel) -> None:
    """Reserve one write budget unit for the public demo account."""
    if session.username != DEMO_STEWARD_USERNAME:
        return

    action = quota_action(request)
    if action is None:
        return

    now = utc_now()
    cutoff = now - timedelta(hours=DEMO_QUOTA_WINDOW_HOURS)
    # Lock the account row where the database supports SELECT FOR UPDATE. This
    # serializes clicks from multiple judge tabs using different sessions.
    db.query(UserAccountModel).filter(UserAccountModel.username == session.username).with_for_update().first()
    events = _quota_query(db, session, cutoff).order_by(AuditEventModel.created_at.asc()).all()
    total_limit = DEMO_QUOTA_LIMITS["api"]
    action_limit = DEMO_QUOTA_LIMITS[action]
    action_events = [event for event in events if event.entity_id == action]

    if len(events) >= total_limit or len(action_events) >= action_limit:
        reset_source = events[0] if len(events) >= total_limit else action_events[0]
        reset_at = reset_source.created_at + timedelta(hours=DEMO_QUOTA_WINDOW_HOURS)
        retry_after = max(1, int((reset_at - now).total_seconds()))
        raise HTTPException(
            status_code=429,
            detail={
                "code": "DEMO_QUOTA_EXCEEDED",
                "message": "Demo quota reached for this action. Please try again later.",
            },
            headers={"Retry-After": str(retry_after)},
        )

    db.add(
        AuditEventModel(
            id=f"evt_demo_quota_{uuid.uuid4().hex}",
            session_id=session.id,
            actor_role=session.role,
            action_code=DEMO_QUOTA_EVENT,
            entity_type="demo_quota",
            entity_id=action,
            detail_json=(
                '{"message":"Demo write quota reserved.",'
                f'"action":"{action}","limit":{action_limit},'
                f'"window_hours":{DEMO_QUOTA_WINDOW_HOURS}}}'
            ),
            created_at=now,
        )
    )
    # Reserve before the route performs expensive storage/LLM work. A failed
    # attempt therefore cannot be retried indefinitely to bypass the guard.
    db.commit()
