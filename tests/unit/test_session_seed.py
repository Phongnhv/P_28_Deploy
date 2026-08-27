from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.models.database import Base, UserAccountModel
from src.services.session_service import ensure_default_users, hash_password, verify_password


def test_missing_local_secrets_do_not_overwrite_existing_seeded_passwords(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setenv("APP_ENV", "development")
    for key in ("DEMO_USER_PASSWORD", "DEMO_STEWARD_PASSWORD", "DEMO_ADMIN_PASSWORD"):
        monkeypatch.delenv(key, raising=False)

    with Session(engine) as db:
        db.add(
            UserAccountModel(
                id="user-steward",
                username="steward",
                display_name="Steward",
                password_hash=hash_password("production-secret"),
                role="STEWARD",
                status="ACTIVE",
                created_by="system-seed",
            )
        )
        db.commit()

        ensure_default_users(db)
        account = db.query(UserAccountModel).filter_by(username="steward").one()

        assert verify_password("production-secret", account.password_hash)
        assert not verify_password("steward", account.password_hash)


def test_configured_secret_rotates_existing_seeded_password(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DEMO_STEWARD_PASSWORD", "rotated-secret")

    with Session(engine) as db:
        db.add(
            UserAccountModel(
                id="user-steward",
                username="steward",
                display_name="Steward",
                password_hash=hash_password("old-secret"),
                role="STEWARD",
                status="ACTIVE",
                created_by="system-seed",
            )
        )
        db.commit()

        ensure_default_users(db)
        account = db.query(UserAccountModel).filter_by(username="steward").one()

        assert verify_password("rotated-secret", account.password_hash)
        assert not verify_password("old-secret", account.password_hash)
