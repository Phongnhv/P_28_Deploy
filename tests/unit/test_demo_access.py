from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from starlette.requests import Request

from src.models.database import AuditEventModel, Base, SessionModel, UserAccountModel
from src.services.demo_quota import DEMO_QUOTA_LIMITS, enforce_demo_quota, quota_action
from src.services.session_service import (
    DEMO_STEWARD_PUBLIC_PASSWORD,
    DEMO_STEWARD_USERNAME,
    ensure_demo_steward,
    hash_password,
    verify_password,
)
from src.time_utils import utc_now


def request_for(method: str, path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [],
            "client": ("test-client", 1234),
            "scheme": "http",
            "server": ("test", 80),
        }
    )


def test_demo_steward_is_seeded_with_a_workspace_safe_public_credential():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        ensure_demo_steward(db)
        account = db.query(UserAccountModel).filter_by(username=DEMO_STEWARD_USERNAME).one()

        assert account.role == "STEWARD"
        assert account.created_by == "system-seed-demo"
        assert verify_password(DEMO_STEWARD_PUBLIC_PASSWORD, account.password_hash)


def test_demo_quota_counts_writes_but_not_reads_and_limits_uploads():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(
            UserAccountModel(
                id="user-demo-steward",
                username=DEMO_STEWARD_USERNAME,
                display_name="Demo Steward",
                password_hash=hash_password(DEMO_STEWARD_PUBLIC_PASSWORD),
                role="STEWARD",
                status="ACTIVE",
                created_by="system-seed-demo",
            )
        )
        auth_session = SessionModel(
            id="session-demo",
            username=DEMO_STEWARD_USERNAME,
            role="STEWARD",
            csrf_token="csrf",
            expires_at=utc_now() + timedelta(hours=1),
            created_at=utc_now(),
        )
        db.add(auth_session)
        db.commit()

        assert quota_action(request_for("GET", "/api/v1/datasets/import")) is None
        upload_request = request_for("POST", "/api/v1/workspaces/ws-browser/datasets/import")
        for _ in range(DEMO_QUOTA_LIMITS["upload"]):
            enforce_demo_quota(db, upload_request, auth_session)

        with pytest.raises(HTTPException) as raised:
            enforce_demo_quota(db, upload_request, auth_session)
        assert raised.value.status_code == 429
        assert raised.value.detail["code"] == "DEMO_QUOTA_EXCEEDED"
        assert db.query(AuditEventModel).filter_by(entity_type="demo_quota").count() == DEMO_QUOTA_LIMITS["upload"]
