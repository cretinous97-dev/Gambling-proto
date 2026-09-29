"""Password hashing, JWT issue/verify, and auth dependencies."""
from __future__ import annotations

import hashlib
import hmac
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
from .db import get_db
from .models import RefreshToken, SessionAudit, User, UserRole, utcnow

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
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


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
        settings.secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return header


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
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
