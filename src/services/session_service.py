import hashlib
import hmac
import ipaddress
import logging
import os
import secrets
import uuid
from datetime import timedelta
from hashlib import pbkdf2_hmac

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from src.config import get_settings
from src.models.database import (
    LoginAttemptModel,
    SessionModel,
    UserAccountModel,
    WorkspaceMembershipModel,
    WorkspaceModel,
)
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
DEMO_STEWARD_WORKSPACE_ID = (os.getenv("DEMO_WORKSPACE_ID") or "ws-browser").strip()
LOGIN_WINDOW_SECONDS = 15 * 60
MAX_IP_ACCOUNT_ATTEMPTS = 5
MAX_ACCOUNT_ATTEMPTS = 10

#: Khoá HMAC dự phòng khi `RATE_LIMIT_HASH_KEY` chưa được cấu hình.
#: `validate_security_settings()` bắt buộc phải có khoá thật ở production,
#: nhưng staging và dev thì không — và một hằng số nằm trong mã nguồn khiến
#: HMAC tương đương hash trần: ai đọc được `login_attempts` là dựng được bảng
#: tra ngược ra username và IP. Khoá ngẫu nhiên theo tiến trình đánh đổi việc
#: mất bộ đếm khi restart để lấy lại tính chất đó.
_EPHEMERAL_RATE_LIMIT_KEY = secrets.token_hex(32)

#: Số vòng của định dạng hash cũ (không mang tham số). Hash cũ vẫn xác thực
#: được và sẽ được nâng cấp âm thầm ở lần đăng nhập thành công kế tiếp.
_LEGACY_PBKDF2_ITERATIONS = 120_000

_HASH_SCHEME = "pbkdf2"


def pbkdf2_iterations() -> int:
    """Số vòng dùng cho hash MỚI. Mặc định 600 000 theo khuyến nghị OWASP."""
    return get_settings().password_hash_iterations


def hash_password(password: str, salt: bytes | None = None) -> str:
    """Return a PBKDF2 hash suitable for persisted local demo accounts.

    Định dạng mang theo tham số: ``pbkdf2$<iterations>$<salt>$<digest>``. Không
    ghi số vòng vào chuỗi thì mọi lần nâng số vòng đều khoá hết tài khoản cũ ra
    ngoài, và trên thực tế điều đó khiến số vòng không bao giờ được nâng.
    """
    iterations = pbkdf2_iterations()
    actual_salt = salt or secrets.token_bytes(16)
    digest = pbkdf2_hmac("sha256", password.encode("utf-8"), actual_salt, iterations)
    return f"{_HASH_SCHEME}${iterations}${actual_salt.hex()}${digest.hex()}"


def _parse_encoded(encoded: str) -> tuple[bytes, bytes, int]:
    """Tách (salt, digest, iterations) từ cả định dạng cũ lẫn mới."""
    parts = encoded.split("$")
    if len(parts) == 4 and parts[0] == _HASH_SCHEME:
        return bytes.fromhex(parts[2]), bytes.fromhex(parts[3]), int(parts[1])
    if len(parts) == 2:
        # Định dạng cũ, không mang tham số — mặc định số vòng của thời điểm đó.
        return bytes.fromhex(parts[0]), bytes.fromhex(parts[1]), _LEGACY_PBKDF2_ITERATIONS
    raise ValueError("Unrecognised password hash format")


def verify_password(password: str, encoded: str) -> bool:
    try:
        salt, expected, iterations = _parse_encoded(encoded)
        actual = pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    except (TypeError, ValueError):
        return False
    return secrets.compare_digest(actual, expected)


def needs_rehash(encoded: str) -> bool:
    """True khi hash dùng tham số yếu hơn tiêu chuẩn hiện hành."""
    try:
        _salt, _digest, iterations = _parse_encoded(encoded)
    except (TypeError, ValueError):
        return True
    return iterations < pbkdf2_iterations()


#: Hash "mồi" dùng cho đường đăng nhập thất bại. Băm một mật khẩu ngẫu nhiên
#: một lần lúc import, để mỗi lần đăng nhập đều tiêu tốn đúng một lần PBKDF2
#: kể cả khi username không tồn tại. Không đầu vào nào khớp được với nó.
_DUMMY_PASSWORD_HASH = hash_password(secrets.token_hex(16))


def ensure_default_users(db: Session) -> None:
    """Seed demo accounts from secrets, never from production source defaults."""
    # Đọc qua Settings chứ không phải os.getenv: `app_env` là một Literal đã
    # được pydantic kiểm tra, nên một giá trị viết sai (`prod`, `Production`)
    # bị từ chối ngay lúc khởi động. Đọc thẳng biến môi trường sẽ đi vòng qua
    # tầng kiểm tra đó và lặng lẽ coi giá trị sai là "không phải production".
    production = get_settings().app_env == "production"
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


def ensure_default_workspace(db: Session, *, created_by: str | None = None) -> WorkspaceModel | None:
    """Create the default workspace and seat every managing account in it.

    The versioned import route (``POST /workspaces/{id}/datasets/import``)
    refuses any caller without an ACTIVE membership. Nothing else in the
    application creates a workspace, so without this seed the route answers
    404 for every request and the UI reports the upload as a server outage.
    """
    workspace = db.get(WorkspaceModel, DEMO_STEWARD_WORKSPACE_ID)
    if not workspace:
        owner = created_by
        if not owner:
            admin = db.query(UserAccountModel).filter(UserAccountModel.role == "ADMIN").first()
            owner = admin.id if admin else None
        if not owner:
            # The workspace row requires a real owner; the caller seeds users first.
            return None
        workspace = WorkspaceModel(
            id=DEMO_STEWARD_WORKSPACE_ID,
            name="Default workspace",
            status="ACTIVE",
            created_by=owner,
        )
        db.add(workspace)
        db.flush()

    # Any account that may manage datasets needs a seat, because the UI signs in
    # as whichever demo account the operator chooses, not only ``demo-steward``.
    managers = db.query(UserAccountModel).filter(UserAccountModel.role.in_(("STEWARD", "ADMIN"))).all()
    for manager in managers:
        membership = db.query(WorkspaceMembershipModel).filter_by(
            workspace_id=DEMO_STEWARD_WORKSPACE_ID,
            user_id=manager.id,
        ).first()
        if not membership:
            db.add(
                WorkspaceMembershipModel(
                    id=f"wm-{manager.username}-{DEMO_STEWARD_WORKSPACE_ID}"[:64],
                    workspace_id=DEMO_STEWARD_WORKSPACE_ID,
                    user_id=manager.id,
                    role=manager.role,
                    status="ACTIVE",
                )
            )
        elif membership.status != "ACTIVE":
            membership.status = "ACTIVE"
    db.commit()
    return workspace


def ensure_demo_steward(db: Session) -> None:
    """Seed the explicitly enabled, quota-protected public demo Steward."""
    settings = get_settings()
    if not settings.enable_public_demo:
        return
    password = (settings.demo_steward_password or "").strip()
    if not password:
        raise RuntimeError("DEMO_STEWARD_PASSWORD is required when ENABLE_PUBLIC_DEMO=true")
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
    elif account.created_by == "system-seed-demo":
        account.password_hash = hash_password(password)
        account.status = "ACTIVE"

    db.commit()
    try:
        workspace = ensure_default_workspace(db, created_by=account.id)
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


def reconcile_public_demo_security(db: Session) -> None:
    """Deactivate public demo identities whenever the explicit flag is disabled."""
    settings = get_settings()
    if settings.enable_public_demo:
        return
    accounts = db.query(UserAccountModel).filter(UserAccountModel.created_by == "system-seed-demo").all()
    usernames = [account.username for account in accounts]
    for account in accounts:
        account.status = "DISABLED"
    if usernames:
        db.query(SessionModel).filter(SessionModel.username.in_(usernames)).delete(synchronize_session=False)
    db.flush()


def validate_security_settings() -> None:
    settings = get_settings()
    if settings.app_env == "production" and not settings.rate_limit_hash_key:
        raise RuntimeError("RATE_LIMIT_HASH_KEY is required in production")
    if settings.app_env == "production" and not settings.trusted_proxy_cidrs.strip():
        # Sau load balancer, `_client_ip` luôn trả IP của LB nếu không có CIDR
        # tin cậy nào được khai báo — chiều IP trong giới hạn đăng nhập biến mất
        # mà không có cảnh báo nào.
        raise RuntimeError("TRUSTED_PROXY_CIDRS is required in production")


def _trusted_proxy_networks() -> list[ipaddress._BaseNetwork]:
    networks = []
    for raw in get_settings().trusted_proxy_cidrs.split(","):
        value = raw.strip()
        if value:
            networks.append(ipaddress.ip_network(value, strict=False))
    return networks


def _client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    try:
        trusted = any(ipaddress.ip_address(peer) in network for network in _trusted_proxy_networks())
    except ValueError:
        trusted = False
    if trusted:
        forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        if forwarded:
            try:
                return str(ipaddress.ip_address(forwarded))
            except ValueError:
                pass
    return peer


def _attempt_hash(scope: str, value: str) -> str:
    settings = get_settings()
    key = settings.rate_limit_hash_key or _EPHEMERAL_RATE_LIMIT_KEY
    return hmac.new(key.encode(), f"{scope}:{value}".encode(), hashlib.sha256).hexdigest()


def _rate_limit_keys(request: Request, username: str) -> dict[str, tuple[str, int]]:
    return {
        "IP_ACCOUNT": (_attempt_hash("IP_ACCOUNT", f"{_client_ip(request)}:{username}"), MAX_IP_ACCOUNT_ATTEMPTS),
        "ACCOUNT": (_attempt_hash("ACCOUNT", username), MAX_ACCOUNT_ATTEMPTS),
    }


def audit_security_event(
    action_code: str,
    entity_id: str,
    *,
    entity_type: str = "account",
    actor_role: str = "ANONYMOUS",
    detail: dict | None = None,
) -> None:
    """Ghi một sự kiện bảo mật vào nhật ký kiểm toán.

    `login_attempts` là BỘ ĐẾM để chặn, không phải nhật ký để điều tra: nó bị
    xoá khi hết cửa sổ 15 phút và khi đăng nhập thành công. Không có những dòng
    audit này thì một đợt brute-force đã diễn ra không để lại dấu vết nào.

    Dùng SESSION RIÊNG có chủ đích. `add_audit_event` gọi `commit()`, nên chia
    sẻ session của caller sẽ commit luôn phần việc dang dở của request đó —
    ngay trước một lệnh `raise`. Session riêng cũng đảm bảo dòng audit tồn tại
    kể cả khi transaction chính bị rollback, đúng thứ ta cần cho sự kiện từ chối.

    Lỗi ghi audit không bao giờ được làm hỏng đường xác thực — nuốt và log lại.
    """
    try:
        from src.services.job_runner import add_audit_event
        from src.services.rule_store import get_engine

        with Session(get_engine()) as audit_db:
            add_audit_event(
                audit_db,
                session_id=None,
                actor_role=actor_role,
                action_code=action_code,
                entity_type=entity_type,
                entity_id=entity_id,
                detail=detail or {},
            )
    except Exception:
        logger.warning("Không ghi được sự kiện bảo mật %s", action_code, exc_info=True)


def _enforce_login_rate_limit(
    db: Session, keys: dict[str, tuple[str, int]], *, username: str = "", ip: str = ""
) -> None:
    now = utc_now()
    cutoff = now - timedelta(seconds=LOGIN_WINDOW_SECONDS)
    db.query(LoginAttemptModel).filter(LoginAttemptModel.attempted_at < cutoff).delete(synchronize_session=False)
    for scope, (key_hash, limit) in keys.items():
        attempts = (
            db.query(LoginAttemptModel)
            .filter_by(scope=scope, key_hash=key_hash)
            .filter(LoginAttemptModel.attempted_at >= cutoff)
            .order_by(LoginAttemptModel.attempted_at.asc())
            .all()
        )
        if len(attempts) >= limit:
            retry_after = max(1, int((attempts[0].attempted_at + timedelta(seconds=LOGIN_WINDOW_SECONDS) - now).total_seconds()))
            audit_security_event("LOGIN_RATE_LIMITED", username, detail={"ip": ip})
            db.commit()
            raise HTTPException(
                status_code=429,
                detail={"code": "LOGIN_RATE_LIMITED", "message": "Too many sign-in attempts. Try again later."},
                headers={"Retry-After": str(retry_after)},
            )
    db.flush()


def _record_failed_login(
    db: Session, keys: dict[str, tuple[str, int]], *, username: str = "", ip: str = ""
) -> None:
    now = utc_now()
    for scope, (key_hash, _limit) in keys.items():
        db.add(LoginAttemptModel(id=f"login-{uuid.uuid4().hex}", scope=scope, key_hash=key_hash, attempted_at=now))
    audit_security_event("LOGIN_FAILED", username, detail={"ip": ip})
    db.commit()


def create_user_session(request: Request, username: str, password: str, db: Session) -> SessionModel:
    """Authenticate an active persisted account and create its cookie session."""
    normalized_username = username.strip().lower()
    client_ip = _client_ip(request)
    attempt_keys = _rate_limit_keys(request, normalized_username)
    account = db.query(UserAccountModel).filter(UserAccountModel.username == normalized_username).with_for_update().first()
    _enforce_login_rate_limit(db, attempt_keys, username=normalized_username, ip=client_ip)

    # Always spend the KDF, even when no account matched. Short-circuiting past
    # verify_password for an unknown username answers in about a millisecond
    # while a real one costs the full PBKDF2 run -- a gap that is measurable
    # over the network and enumerates every valid username. Compute the result
    # before branching; folding this into the `or` chain restores the leak.
    account_ok = account is not None and account.status == "ACTIVE"
    password_ok = verify_password(
        password, account.password_hash if account_ok else _DUMMY_PASSWORD_HASH
    )
    if not (account_ok and password_ok):
        _record_failed_login(db, attempt_keys, username=normalized_username, ip=client_ip)
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "Invalid username or password"})

    # Nâng cấp âm thầm: mật khẩu vừa được xác thực nên đây là lần duy nhất ta
    # cầm bản rõ và có thể băm lại theo tham số hiện hành.
    if needs_rehash(account.password_hash):
        account.password_hash = hash_password(password)

    hashes = [key_hash for key_hash, _limit in attempt_keys.values()]
    db.query(LoginAttemptModel).filter(LoginAttemptModel.key_hash.in_(hashes)).delete(synchronize_session=False)

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
