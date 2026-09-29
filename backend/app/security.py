"""Password hashing, JWT issue/verify, and auth dependencies."""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from datetime import timedelta
from typing import Annotated

import bcrypt
import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings

log = logging.getLogger("security")
from .db import get_db
from .models import AppSetting, RefreshToken, SessionAudit, User, UserRole, utcnow

ACCESS = "access"
REFRESH = "refresh"


def hash_password(password: str) -> str:
    if len(password) < settings.password_min_length:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Password must be at least {settings.password_min_length} characters.",
        )
    return bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode()[:72], password_hash.encode())
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Signing key
# ---------------------------------------------------------------------------
# Resolved at CALL time rather than import time, because on a serverless
# platform the key may not exist when the module is first imported.
_key_name = "session_signing_key"
_active_key: str | None = None


def _env_key_is_explicit() -> bool:
    """True when the operator deliberately configured a key."""
    return bool(settings.secret_key) and not settings.ephemeral_secret_key


def ensure_signing_key() -> str:
    """Resolve the token signing key once, outside any request.

    Called from application bootstrap, where there is no concurrent request
    holding a write transaction - important on SQLite, where inserting a row
    from inside a request handler can hit a lock and fail.

    Precedence:
      1. SECRET_KEY from the environment (set this in production)
      2. a key generated once and stored in the database, so sessions survive
         restarts and cold starts
      3. a process-local key, if the database is unreachable
    """
    global _active_key
    if _active_key is not None:
        return _active_key

    if _env_key_is_explicit():
        _active_key = settings.secret_key
        return _active_key

    from sqlalchemy.exc import IntegrityError

    from .db import session_scope
    from .models import AppSetting

    try:
        with session_scope() as db:
            row = db.get(AppSetting, _key_name)
            if row is not None:
                _active_key = row.value
                return _active_key

        # Generate one. Two instances may race here, so the loser re-reads
        # rather than assuming it failed.
        try:
            with session_scope() as db:
                db.add(AppSetting(key=_key_name, value=secrets.token_urlsafe(64)))
                db.flush()
        except IntegrityError:
            pass

        with session_scope() as db:
            row = db.get(AppSetting, _key_name)
            if row is not None:
                _active_key = row.value
                log.warning(
                    "SECRET_KEY is not set: using a generated signing key stored "
                    "in the database. Sessions survive restarts, but set "
                    "SECRET_KEY in the environment for production."
                )
                return _active_key
    except Exception:  # database unavailable or read-only
        log.exception(
            "could not read or store a signing key; falling back to a "
            "process-local key. Sessions will not survive a restart."
        )

    _active_key = settings.secret_key or secrets.token_urlsafe(64)
    return _active_key


def signing_key() -> str:
    """The key used to sign and verify session tokens."""
    return ensure_signing_key()


def signing_key_source() -> str:
    """Where the active signing key came from - surfaced by /api/health so a
    session problem is diagnosable instead of mysterious."""
    if _env_key_is_explicit():
        return "environment"
    from .db import session_scope
    from .models import AppSetting

    try:
        with session_scope() as db:
            if db.get(AppSetting, _key_name) is not None:
                return "database"
    except Exception:
        return "process"
    return "process"


def reset_signing_key_cache() -> None:
    """Test helper: forget the cached key so a new one is resolved."""
    global _active_key
    _active_key = None


def _encode(user: User, kind: str, ttl: timedelta) -> str:
    now = int(time.time())
    payload = {
        "sub": user.id,
        "email": user.email,
        "role": user.role.value,
        "typ": kind,
        "iat": now,
        "exp": now + int(ttl.total_seconds()),
        "jti": secrets.token_hex(8),
    }
    return jwt.encode(payload, signing_key(), algorithm=settings.jwt_algorithm)


def create_access_token(user: User) -> str:
    return _encode(user, ACCESS, timedelta(minutes=settings.access_token_ttl_min))


def create_refresh_token(user: User, db: Session) -> str:
    raw = secrets.token_urlsafe(48)
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            expires_at=utcnow() + timedelta(days=settings.refresh_token_ttl_days),
        )
    )
    # Compact token = jwt header carrying the DB row id + the opaque secret.
    header = jwt.encode(
        {"tid": "pending", "sub": user.id, "typ": REFRESH, "raw": raw[:8]},
        signing_key(),
        algorithm=settings.jwt_algorithm,
    )
    return header


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, signing_key(), algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired, please log in again.")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials token.")


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated.")
    return authorization.split(" ", 1)[1].strip()


def current_user(
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
) -> User:
    payload = decode_token(_bearer(authorization))
    if payload.get("typ") != ACCESS:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong token type.")
    user = db.get(User, payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account not found or disabled.")
    if user.is_banned:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account suspended. Contact support.")
    return user


def optional_user(
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
) -> User | None:
    if not authorization:
        return None
    try:
        return current_user(authorization, db)
    except HTTPException:
        return None


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role is not UserRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin privileges required.")
    return user


CurrentUser = Annotated[User, Depends(current_user)]
AdminUser = Annotated[User, Depends(require_admin)]
MaybeUser = Annotated[User | None, Depends(optional_user)]
Db = Annotated[Session, Depends(get_db)]


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def audit(
    db: Session,
    user_id: str | None,
    action: str,
    request: Request | None = None,
    meta: dict | None = None,
) -> None:
    db.add(
        SessionAudit(
            user_id=user_id,
            action=action,
            ip=client_ip(request) if request else None,
            user_agent=(request.headers.get("user-agent") if request else None),
            meta=meta or {},
        )
    )


def sign_payload(payload: bytes, secret: str) -> str:
    """HMAC-SHA256 hex digest, used for provider webhook verification."""
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)
