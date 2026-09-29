"""Registration, login, sessions, profile, KYC and responsible gambling."""
from __future__ import annotations

import hashlib
import logging
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, select

from .. import i18n
from ..config import settings
from ..services import geo, jurisdiction
from ..ledger import ensure_user_accounts, get_balance
from ..models import (
    AccountKind,
    KycDocument,
    KycStatus,
    Notification,
    RefreshToken,
    User,
    UserRole,
    utcnow,
)
from ..money import to_minor
from ..rng import commit, new_client_seed, new_server_seed
from ..schemas import (
    KycSubmitIn,
    LoginIn,
    PasswordChangeIn,
    ProfileUpdateIn,
    RegisterIn,
    ResponsibleGamblingIn,
    SeedRotateIn,
    TokenOut,
    money_field,
)
from ..security import (
    CurrentUser,
    Db,
    audit,
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_password,
)
from ..services import bonus as bonus_svc
from ..services import compliance
from ..services.wallet import balances, stats

log = logging.getLogger("app.routers.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _user_payload(db: Db, user: User) -> dict:
    from ..services.bonus import vip_progress

    return {
        "id": user.id,
        "email": user.email,
        "username": user.username,
        "role": user.role.value,
        "country": user.country,
        "display_currency": user.display_currency,
        "kyc_status": user.kyc_status.value,
        "email_verified": user.email_verified,
        "vip": vip_progress(user),
        "balances": balances(db, user),
        "stats": stats(db, user),
        "created_at": user.created_at,
        "client_seed": user.client_seed,
        "server_seed_hash": user.server_seed_hash,
        "nonce": user.nonce,
        "self_excluded_until": user.self_excluded_until,
        "cool_off_until": user.cool_off_until,
        "loss_limit_daily": user.loss_limit_daily,
        "deposit_limit_daily": user.deposit_limit_daily,
    }


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterIn, request: Request, db: Db):
    compliance.assert_age(payload.date_of_birth)

    # The declared country is checked against the policy. When geo enforcement
    # is on and the edge told us where the caller is, that is what is judged -
    # a country field in a signup form is a claim, not evidence.
    declared = payload.country.upper()
    judging = declared
    if settings.geo_enforcement:
        edge = geo.caller_country(request)
        if edge:
            judging = edge
    decision = jurisdiction.assert_allowed(judging, jurisdiction.REGISTER)
    if decision.country and decision.country != declared:
        # Not a refusal on its own: worth an audit trail, because a mismatch
        # between the signup form and the network is what a fraud review asks
        # about first. (VPN, travel and corporate egress all produce it too.)
        log.info(
            "registration country mismatch declared=%s edge=%s user_agent=%s",
            declared,
            decision.country,
            (request.headers.get("user-agent") or "")[:80],
        )

    email = payload.email.lower().strip()
    exists = db.execute(
        select(User).where(
            (func.lower(User.email) == email)
            | (func.lower(User.username) == payload.username.lower())
        )
    ).scalars().first()
    if exists:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "An account with that email or username already exists."
        )

    server_seed = new_server_seed()
    user = User(
        email=email,
        username=payload.username,
        password_hash=hash_password(payload.password),
        country=payload.country.upper(),
        date_of_birth=payload.date_of_birth,
        phone=payload.phone,
        server_seed=server_seed,
        server_seed_hash=commit(server_seed),
        client_seed=new_client_seed(),
        email_verified=True,   # no mail provider wired in; see README "your part"
    )
    db.add(user)
    db.flush()
    ensure_user_accounts(db, user)

    welcome = bonus_svc.signup_bonus(db, user)
    if payload.bonus_code and welcome is None:
        try:
            bonus_svc.grant(
                db, user, int(settings.signup_bonus_usd * 100) or 500,
                code=payload.bonus_code.upper(), wager_multiplier=30, source="promo",
            )
        except ValueError:
            pass

    audit(
        db,
        user.id,
        "auth.register",
        request,
        {"country": user.country, "jurisdiction": decision.as_dict()},
    )
    return _issue(db, user)


def _issue(db: Db, user: User) -> TokenOut:
    return TokenOut(
        access_token=create_access_token(user),
        refresh_token=create_refresh_token(user, db),
        expires_in=settings.access_token_ttl_min * 60,
    )


@router.post("/login", response_model=TokenOut)
def login(payload: LoginIn, request: Request, db: Db):
    email = payload.email.lower().strip()
    user = db.execute(select(User).where(func.lower(User.email) == email)).scalars().first()
    if not user or not verify_password(payload.password, user.password_hash):
        audit(db, user.id if user else None, "auth.login_failed", request, {"email": email})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password.")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disabled. Contact support.")

    user.last_login_at = utcnow()
    audit(db, user.id, "auth.login", request)
    return _issue(db, user)


@router.post("/refresh", response_model=TokenOut)
def refresh(refresh_token: str, db: Db):
    """Rotate a refresh token. The presented token is revoked on use, so a
    stolen token cannot be replayed after the legitimate client refreshes."""
    token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
    row = db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    ).scalar_one_or_none()
    if row is None or row.revoked or row.expires_at < utcnow():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token invalid or expired.")
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account not available.")
    row.revoked = True
    db.flush()
    return _issue(db, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(user: CurrentUser, db: Db):
    db.execute(
        select(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.revoked.is_(False))
    )
    for row in db.execute(
        select(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.revoked.is_(False))
    ).scalars():
        row.revoked = True


@router.get("/me")
def me(user: CurrentUser, db: Db):
    return _user_payload(db, user)


@router.patch("/me")
def update_profile(payload: ProfileUpdateIn, user: CurrentUser, db: Db):
    if payload.display_currency:
        cur = payload.display_currency.upper()
        # Validated against the live localization table rather than the legacy
        # hardcoded list: a currency the operator has not given a display rate
        # to must not be selectable, or the UI would show a converted amount it
        # cannot compute.
        allowed = i18n.available_currencies()
        if cur not in allowed:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Unsupported display currency {cur!r}. Available: {', '.join(allowed)}",
            )
        user.display_currency = cur
    if payload.phone is not None:
        user.phone = payload.phone
    db.flush()
    return _user_payload(db, user)


@router.post("/password")
def change_password(payload: PasswordChangeIn, user: CurrentUser, db: Db, request: Request):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect.")
    user.password_hash = hash_password(payload.new_password)
    for row in db.execute(
        select(RefreshToken).where(RefreshToken.user_id == user.id)
    ).scalars():
        row.revoked = True
    audit(db, user.id, "auth.password_change", request)
    db.flush()
    return {"ok": True, "message": "Password changed. All other sessions were signed out."}


# ---------------------------------------------------------------------------
# responsible gambling
# ---------------------------------------------------------------------------
@router.post("/responsible-gambling")
def set_limits(payload: ResponsibleGamblingIn, user: CurrentUser, db: Db, request: Request):
    """Tightening applies immediately. Loosening a limit is refused for 24h -
    that cooling-off rule is a licensing requirement in most jurisdictions and
    is enforced here rather than trusted to the UI."""
    before = {
        "loss_limit_daily": user.loss_limit_daily,
        "deposit_limit_daily": user.deposit_limit_daily,
        "self_excluded_until": user.self_excluded_until,
        "cool_off_until": user.cool_off_until,
    }
    changed: list[str] = []

    for field, raw in (
        ("loss_limit_daily", payload.loss_limit_daily),
        ("deposit_limit_daily", payload.deposit_limit_daily),
    ):
        if raw is None:
            continue
        new_value = money_field(raw)
        current = getattr(user, field)
        if current is not None and new_value > current:
            if user.created_at and (utcnow() - user.created_at) > timedelta(hours=24):
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "Limits can be raised at most once every 24 hours and only by support. "
                    "Please contact live chat to increase a limit.",
                )
        setattr(user, field, new_value)
        changed.append(field)

    if payload.self_exclude_days is not None and payload.self_exclude_days > 0:
        user.self_excluded_until = utcnow() + timedelta(days=payload.self_exclude_days)
        changed.append("self_excluded_until")
    if payload.cool_off_hours is not None and payload.cool_off_hours > 0:
        user.cool_off_until = utcnow() + timedelta(hours=payload.cool_off_hours)
        changed.append("cool_off_until")

    audit(db, user.id, "rg.limits_update", request, {"before": before, "changed": changed})
    db.flush()
    return {
        "ok": True,
        "changed": changed,
        "self_excluded_until": user.self_excluded_until,
        "cool_off_until": user.cool_off_until,
        "loss_limit_daily": user.loss_limit_daily,
        "deposit_limit_daily": user.deposit_limit_daily,
    }


# ---------------------------------------------------------------------------
# provably fair
# ---------------------------------------------------------------------------
@router.post("/seeds/rotate")
def rotate_seeds(payload: SeedRotateIn, user: CurrentUser, db: Db, request: Request):
    """Reveal the current server seed and commit to a new one.

    Once revealed, the player can recompute every outcome produced with it -
    that is the whole point of the scheme. The old seed is kept in
    `prev_server_seeds` so the audit trail survives.
    """
    revealed = {
        "server_seed": user.server_seed,
        "server_seed_hash": user.server_seed_hash,
        "nonce_reached": user.nonce,
    }
    history = list(user.prev_server_seeds or [])
    history.append(revealed)
    user.prev_server_seeds = history[-20:]

    new_seed = new_server_seed()
    user.server_seed = new_seed
    user.server_seed_hash = commit(new_seed)
    user.nonce = 0
    if payload.client_seed:
        user.client_seed = payload.client_seed
    audit(db, user.id, "fair.seed_rotate", request, {"hash": revealed["server_seed_hash"]})
    db.flush()
    return {
        "revealed": revealed,
        "previous": history[-20:],
        "next": {
            "server_seed_hash": user.server_seed_hash,
            "client_seed": user.client_seed,
            "nonce": user.nonce,
        },
    }


@router.get("/seeds")
def seeds(user: CurrentUser):
    return {
        "server_seed_hash": user.server_seed_hash,
        "client_seed": user.client_seed,
        "nonce": user.nonce,
        "previous": (user.prev_server_seeds or [])[-20:],
    }


# ---------------------------------------------------------------------------
# KYC
# ---------------------------------------------------------------------------
@router.post("/kyc", status_code=status.HTTP_201_CREATED)
def submit_kyc(payload: KycSubmitIn, user: CurrentUser, db: Db, request: Request):
    """Records a document REFERENCE. The file itself must be uploaded to object
    storage by the client and served through short-lived signed URLs - never
    through this API, and never inside the repo."""
    if user.kyc_status is KycStatus.verified:
        raise HTTPException(status.HTTP_409_CONFLICT, "Account is already verified.")
    doc = KycDocument(
        user_id=user.id, doc_type=payload.doc_type, file_ref=payload.file_ref,
        status=KycStatus.pending,
    )
    db.add(doc)
    user.kyc_full_name = payload.full_name
    user.kyc_document_ref = payload.file_ref
    user.kyc_status = KycStatus.pending
    audit(db, user.id, "kyc.submit", request, {"doc_type": payload.doc_type})
    db.flush()
    return {"ok": True, "status": user.kyc_status.value}


@router.get("/kyc")
def kyc_status(user: CurrentUser, db: Db):
    docs = db.execute(
        select(KycDocument).where(KycDocument.user_id == user.id).order_by(
            KycDocument.created_at.desc()
        )
    ).scalars().all()
    return {
        "status": user.kyc_status.value,
        "documents": [
            {
                "id": d.id,
                "doc_type": d.doc_type,
                "status": d.status.value,
                "created_at": d.created_at,
                "note": d.reviewer_note,
            }
            for d in docs
        ],
    }


# ---------------------------------------------------------------------------
# notifications
# ---------------------------------------------------------------------------
@router.get("/notifications")
def notifications(user: CurrentUser, db: Db, limit: int = 30):
    rows = db.execute(
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc())
        .limit(min(limit, 100))
    ).scalars().all()
    return [
        {
            "id": n.id,
            "title": n.title,
            "body": n.body,
            "read": n.read,
            "created_at": n.created_at,
        }
        for n in rows
    ]


@router.post("/notifications/read", status_code=status.HTTP_204_NO_CONTENT)
def mark_read(user: CurrentUser, db: Db):
    for n in db.execute(
        select(Notification).where(Notification.user_id == user.id, Notification.read.is_(False))
    ).scalars():
        n.read = True


@router.get("/limits")
def limits(user: CurrentUser, db: Db):
    from ..services.compliance import usage_row

    row = usage_row(db, user.id)
    return {
        "loss_limit_daily": user.loss_limit_daily,
        "deposit_limit_daily": user.deposit_limit_daily,
        "today": {
            "deposits": row.deposit_total,
            "losses": row.loss_total,
            "wagered": row.wager_total,
        },
        "kyc_required_above": int(settings.kyc_required_above_usd * 100),
        "lifetime_withdrawn": compliance.lifetime_withdrawn(db, user.id),
        "min_deposit": int(settings.min_deposit_usd * 100),
        "max_deposit": int(settings.max_deposit_usd * 100),
        "min_withdrawal": int(settings.min_withdrawal_usd * 100),
        "max_withdrawal": int(settings.max_withdrawal_usd * 100),
        "min_bet": int(settings.min_bet_usd * 100),
        "max_bet": int(settings.max_bet_usd * 100),
        "blocked_jurisdictions": sorted(settings.blocklist),
    }
