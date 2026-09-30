"""Back-office API. Every mutating endpoint here is audit-logged.

Scope of the admin surface:
  * money in/out review        - deposits, withdrawals, manual adjustments
  * player management          - limits, bans, KYC, VIP, role
  * risk                       - AML queue, RG flags, big-winner feed
  * reporting                  - GGR/NGR, ledger integrity, provider health
  * marketing                  - bonus codes, ad-hoc grants

Nothing here trusts the client for authority: `AdminUser` re-reads the role
from the database on every request.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import case, func, select, text

from .. import i18n
from ..config import settings
from ..ledger import Entry, admin_adjust, global_balance_check
from ..models import (
    AccountKind,
    AuditLog,
    Bet,
    BonusCode,
    Deposit,
    DepositStatus,
    KycDocument,
    KycStatus,
    LedgerTransaction,
    PaymentWebhook,
    TxType,
    User,
    Withdrawal,
    WithdrawalStatus,
    utcnow,
)
from ..money import to_minor
from ..payments import provider_status
from ..schemas import (
    FxRatesIn,
    AdminAdjustIn,
    BonusCodeIn,
    BonusCreateIn,
    PayoutConfirmIn,
    ReviewWithdrawalIn,
    UserAdminUpdateIn,
    money_field,
)
from ..security import AdminUser, Db, audit
from ..services import bonus as bonus_svc
from ..services import compliance, payments as pay_svc
from ..services.crash_loop import hub

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# dashboard
# ---------------------------------------------------------------------------
@router.get("/dashboard")
def dashboard(admin: AdminUser, db: Db):
    day_ago = utcnow() - timedelta(hours=24)
    week_ago = utcnow() - timedelta(days=7)

    def scalar(stmt, default=0):
        return db.execute(stmt).scalar_one() or default

    deposits_24h = scalar(
        select(func.coalesce(func.sum(Deposit.amount), 0)).where(
            Deposit.status == DepositStatus.succeeded, Deposit.completed_at >= day_ago
        )
    )
    withdrawals_24h = scalar(
        select(func.coalesce(func.sum(Withdrawal.amount), 0)).where(
            Withdrawal.status == WithdrawalStatus.paid, Withdrawal.paid_at >= day_ago
        )
    )
    wagered_24h = scalar(
        select(func.coalesce(func.sum(Bet.stake), 0)).where(Bet.created_at >= day_ago)
    )
    returned_24h = scalar(
        select(func.coalesce(func.sum(Bet.payout), 0)).where(Bet.created_at >= day_ago)
    )
    wagered_7d = scalar(
        select(func.coalesce(func.sum(Bet.stake), 0)).where(Bet.created_at >= week_ago)
    )
    returned_7d = scalar(
        select(func.coalesce(func.sum(Bet.payout), 0)).where(Bet.created_at >= week_ago)
    )

    return {
        "players": {
            "total": scalar(select(func.count(User.id))),
            "new_24h": scalar(select(func.count(User.id)).where(User.created_at >= day_ago)),
            "active_24h": scalar(
                select(func.count(func.distinct(Bet.user_id))).where(Bet.created_at >= day_ago)
            ),
            "verified": scalar(
                select(func.count(User.id)).where(User.kyc_status == KycStatus.verified)
            ),
            "self_excluded": scalar(
                select(func.count(User.id)).where(User.self_excluded_until > utcnow())
            ),
            "banned": scalar(select(func.count(User.id)).where(User.is_banned.is_(True))),
        },
        "money_24h": {
            "deposits": int(deposits_24h),
            "withdrawals": int(withdrawals_24h),
            "wagered": int(wagered_24h),
            "returned": int(returned_24h),
            "ggr": int(wagered_24h) - int(returned_24h),
            "net_deposits": int(deposits_24h) - int(withdrawals_24h),
        },
        "money_7d": {
            "wagered": int(wagered_7d),
            "returned": int(returned_7d),
            "ggr": int(wagered_7d) - int(returned_7d),
            "margin_pct": (
                round((1 - int(returned_7d) / int(wagered_7d)) * 100, 3)
                if wagered_7d
                else 0.0
            ),
        },
        "queues": {
            "withdrawals_pending": scalar(
                select(func.count(Withdrawal.id)).where(
                    Withdrawal.status.in_(
                        (WithdrawalStatus.requested, WithdrawalStatus.under_review)
                    )
                )
            ),
            "kyc_pending": scalar(
                select(func.count(KycDocument.id)).where(KycDocument.status == KycStatus.pending)
            ),
            "failed_webhooks": scalar(
                select(func.count(PaymentWebhook.id)).where(PaymentWebhook.error.is_not(None))
            ),
        },
        "realtime": {"crash_sockets": hub.client_count},
        "provider": provider_status(),
        "integrity": {
            "books_balanced": global_balance_check(db).get("__sum__") == 0,
            "totals": global_balance_check(db),
        },
        "jackpot": _jackpot(db),
    }


def _jackpot(db: Db) -> dict:
    from ..models import JackpotPool

    pool = db.execute(select(JackpotPool)).scalars().first()
    if pool is None:
        pool = JackpotPool(name="Grand", amount=100_000, contribution_pct=0.01)
        db.add(pool)
        db.flush()
    return {"name": pool.name, "amount": pool.amount, "contribution_pct": float(pool.contribution_pct)}


@router.get("/revenue")
def revenue(admin: AdminUser, db: Db, days: int = Query(default=14, ge=1, le=90)):
    """Daily GGR/NGR series for the back-office chart."""
    rows = db.execute(
        select(
            func.date(Bet.created_at).label("day"),
            func.coalesce(func.sum(Bet.stake), 0),
            func.coalesce(func.sum(Bet.payout), 0),
            func.count(Bet.id),
            func.count(func.distinct(Bet.user_id)),
        )
        .where(Bet.created_at >= utcnow() - timedelta(days=days))
        .group_by(text("day"))
        .order_by(text("day"))
    ).all()
    return {
        "series": [
            {
                "day": str(day),
                "wagered": int(wagered),
                "returned": int(returned),
                "ggr": int(wagered) - int(returned),
                "bets": int(bets),
                "players": int(players),
            }
            for day, wagered, returned, bets, players in rows
        ]
    }


# ---------------------------------------------------------------------------
# localization / FX
# ---------------------------------------------------------------------------
@router.get("/fx-rates")
def get_fx_rates(admin: AdminUser):
    """The display-rate table currently in force, and where it came from.

    These rates are presentation only - they never value a bet, settle an
    entry or enforce a limit. That is exactly why they are editable here
    without a code change: a stale figure is a customer-service problem, not an
    accounting one.
    """
    return i18n.snapshot()["fx"] | {
        "currencies": [
            {"code": code, **i18n.currency_meta(code)}
            for code in i18n.available_currencies()
        ]
    }


@router.put("/fx-rates")
def put_fx_rates(
    payload: FxRatesIn,
    admin: AdminUser,
    db: Db,
    request: Request = None,
):
    """Replace the display rates (audited).

    Rejects the shapes that silently corrupt a display: a non-positive rate, a
    currency we have no metadata for, and any attempt to re-rate the settlement
    currency itself - a currency cannot be worth something other than 1 of
    itself, and allowing it would let a typo make every balance wrong.
    """
    settlement = settings.settlement_currency.upper()
    cleaned: dict[str, float] = {}
    for code, rate in payload.rates.items():
        code = code.strip().upper()
        if code == settlement:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"{settlement} is the settlement currency and is always 1.0",
            )
        if code not in i18n.CURRENCIES:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"Unknown currency code {code!r}"
            )
        if rate <= 0:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"Rate for {code} must be greater than zero"
            )
        cleaned[code] = float(rate)

    i18n.set_rates(cleaned, updated_at=utcnow().isoformat(timespec="seconds"))
    audit(db, admin.id, "fx.rates_update", request, {"currencies": sorted(cleaned)})
    return {"updated": sorted(cleaned), "fx": i18n.snapshot()["fx"]}


@router.get("/health")
def health(admin: AdminUser, db: Db):
    provider = provider_status()
    check = global_balance_check(db)
    return {
        "environment": settings.environment,
        "payment_provider": provider,
        "ledger": {
            "balanced": check.get("__sum__") == 0,
            "accounts": {k: v for k, v in check.items() if k != "__sum__"},
        },
        "pending_withdrawals": db.execute(
            select(func.count(Withdrawal.id)).where(
                Withdrawal.status == WithdrawalStatus.under_review
            )
        ).scalar_one(),
        "unprocessed_webhooks": db.execute(
            select(func.count(PaymentWebhook.id)).where(PaymentWebhook.processed.is_(False))
        ).scalar_one(),
        "crash_clients": hub.client_count,
    }


# ---------------------------------------------------------------------------
# players
# ---------------------------------------------------------------------------
@router.get("/users")
def list_users(
    admin: AdminUser,
    db: Db,
    q: str | None = None,
    kyc: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
):
    stmt = select(User)
    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(
            func.lower(User.email).like(needle) | func.lower(User.username).like(needle)
        )
    if kyc:
        stmt = stmt.where(User.kyc_status == KycStatus(kyc))
    users = db.execute(stmt.order_by(User.created_at.desc()).limit(limit)).scalars().all()

    out = []
    for u in users:
        from ..ledger import get_balance

        cash = get_balance(db, u.id, AccountKind.user_available).amount
        bonus = get_balance(db, u.id, AccountKind.user_bonus).amount
        locked = get_balance(db, u.id, AccountKind.user_locked).amount
        agg = db.execute(
            select(
                func.count(Bet.id),
                func.coalesce(func.sum(Bet.stake), 0),
                func.coalesce(func.sum(Bet.profit), 0),
            ).where(Bet.user_id == u.id)
        ).one()
        out.append(
            {
                "id": u.id,
                "email": u.email,
                "username": u.username,
                "role": u.role.value,
                "country": u.country,
                "kyc_status": u.kyc_status.value,
                "is_active": u.is_active,
                "is_banned": u.is_banned,
                "vip_tier": u.vip_tier,
                "created_at": u.created_at,
                "last_login_at": u.last_login_at,
                "self_excluded_until": u.self_excluded_until,
                "balances": {"cash": cash, "bonus": bonus, "locked": locked},
                "bets": int(agg[0]),
                "wagered": int(agg[1]),
                "net": int(agg[2]),
            }
        )
    return {"users": out}


@router.get("/users/{user_id}")
def user_detail(user_id: str, admin: AdminUser, db: Db):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")

    deposits = db.execute(
        select(Deposit).where(Deposit.user_id == user_id).order_by(Deposit.created_at.desc()).limit(20)
    ).scalars().all()
    withdrawals = db.execute(
        select(Withdrawal).where(Withdrawal.user_id == user_id)
        .order_by(Withdrawal.created_at.desc()).limit(20)
    ).scalars().all()
    from ..ledger import user_statement

    return {
        "user": {
            "id": user.id,
            "email": user.email,
            "username": user.username,
            "role": user.role.value,
            "country": user.country,
            "phone": user.phone,
            "date_of_birth": user.date_of_birth,
            "kyc_status": user.kyc_status.value,
            "kyc_full_name": user.kyc_full_name,
            "is_active": user.is_active,
            "is_banned": user.is_banned,
            "email_verified": user.email_verified,
            "created_at": user.created_at,
            "last_login_at": user.last_login_at,
            "self_excluded_until": user.self_excluded_until,
            "cool_off_until": user.cool_off_until,
            "loss_limit_daily": user.loss_limit_daily,
            "deposit_limit_daily": user.deposit_limit_daily,
            "vip_tier": user.vip_tier,
            "wagered_lifetime": user.wagered_lifetime,
            "server_seed_hash": user.server_seed_hash,
        },
        "aml_flags": compliance.aml_flags(db, user),
        "lifetime_withdrawn": compliance.lifetime_withdrawn(db, user_id),
        "deposits": [
            {"id": d.id, "amount": d.amount, "method": d.method.value, "status": d.status.value,
             "created_at": d.created_at, "reference": d.provider_ref}
            for d in deposits
        ],
        "withdrawals": [
            {"id": w.id, "amount": w.amount, "method": w.method.value, "status": w.status.value,
             "destination": w.destination, "created_at": w.created_at, "reference": w.provider_ref}
            for w in withdrawals
        ],
        "ledger": user_statement(db, user_id, 50),
    }


@router.patch("/users/{user_id}")
def update_user(user_id: str, payload: UserAdminUpdateIn, admin: AdminUser, db: Db):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")

    before = {
        "is_active": user.is_active,
        "is_banned": user.is_banned,
        "role": user.role.value,
        "kyc_status": user.kyc_status.value,
        "email_verified": user.email_verified,
        "vip_tier": user.vip_tier,
    }
    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.is_banned is not None:
        user.is_banned = payload.is_banned
    if payload.role is not None:
        from ..models import UserRole

        user.role = UserRole(payload.role)
    if payload.kyc_status is not None:
        user.kyc_status = KycStatus(payload.kyc_status)
        for doc in db.execute(
            select(KycDocument).where(KycDocument.user_id == user.id)
        ).scalars():
            doc.status = user.kyc_status
            doc.reviewed_by = admin.id
    if payload.email_verified is not None:
        user.email_verified = payload.email_verified
    if payload.vip_tier is not None:
        user.vip_tier = payload.vip_tier

    db.add(
        AuditLog(
            actor_id=admin.id, actor_email=admin.email, action="user.update",
            target=user.id, before=before,
            after={"note": payload.note, "is_banned": user.is_banned, "role": user.role.value,
                   "kyc_status": user.kyc_status.value, "vip_tier": user.vip_tier},
        )
    )
    db.flush()
    return {"ok": True, "user_id": user.id}


@router.post("/users/{user_id}/adjust")
def adjust_balance(user_id: str, payload: AdminAdjustIn, admin: AdminUser, db: Db):
    """Manual credit/debit. Requires a reason and is always audited.

    Negative adjustments are capped at the player's cash balance: an operator
    should never create a negative wallet by hand (that is what the chargeback
    path is for, and it is visible to the player).
    """
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    amount = money_field(payload.amount)
    if amount == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Adjustment cannot be zero.")
    if amount < 0:
        from ..ledger import get_balance

        if get_balance(db, user.id, AccountKind.user_available).amount + amount < 0:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Adjustment exceeds the player's cash balance. Use a chargeback entry instead.",
            )
    tx = admin_adjust(
        db, user.id, amount,
        reference=f"admin:{admin.id}", memo=f"{payload.reason} (by {admin.email})",
    )
    db.add(
        AuditLog(
            actor_id=admin.id, actor_email=admin.email, action="wallet.adjust",
            target=user.id, before={}, after={"amount": amount, "reason": payload.reason,
                                              "tx": tx.id},
        )
    )
    if amount > 0:
        bonus_svc.notify(
            db, user.id, "Balance adjusted",
            f"{amount / 100:.2f} was credited to your account by support. Reason: {payload.reason}",
        )
    db.flush()
    return {"ok": True, "transaction_id": tx.id, "amount": amount}


# ---------------------------------------------------------------------------
# deposits / withdrawals review
# ---------------------------------------------------------------------------
@router.get("/deposits")
def admin_deposits(
    admin: AdminUser,
    db: Db,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
):
    stmt = select(Deposit)
    if status_filter:
        stmt = stmt.where(Deposit.status == DepositStatus(status_filter))
    rows = db.execute(stmt.order_by(Deposit.created_at.desc()).limit(limit)).scalars().all()
    return {
        "deposits": [
            {
                "id": d.id,
                "user_id": d.user_id,
                "username": (db.get(User, d.user_id).username if db.get(User, d.user_id) else None),
                "amount": d.amount,
                "credited": d.credited,
                "bonus_credited": d.bonus_credited,
                "method": d.method.value,
                "provider": d.provider,
                "status": d.status.value,
                "reference": d.provider_ref,
                "crypto_txid": d.crypto_txid,
                "confirmations": d.confirmations,
                "created_at": d.created_at,
                "completed_at": d.completed_at,
            }
            for d in rows
        ]
    }


@router.get("/withdrawals")
def admin_withdrawals(
    admin: AdminUser,
    db: Db,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
):
    stmt = select(Withdrawal)
    if status_filter:
        stmt = stmt.where(Withdrawal.status == WithdrawalStatus(status_filter))
    rows = db.execute(stmt.order_by(Withdrawal.created_at.desc()).limit(limit)).scalars().all()
    out = []
    for w in rows:
        user = db.get(User, w.user_id)
        out.append(
            {
                "id": w.id,
                "user_id": w.user_id,
                "username": user.username if user else None,
                "email": user.email if user else None,
                "amount": w.amount,
                "fee": w.fee,
                "net_amount": w.net_amount,
                "method": w.method.value,
                "destination": w.destination,
                "status": w.status.value,
                "provider_ref": w.provider_ref,
                "created_at": w.created_at,
                "reviewed_at": w.reviewed_at,
                "paid_at": w.paid_at,
                "review_note": w.review_note,
                "rejection_reason": w.rejection_reason,
                "risk": compliance.aml_flags(db, user) if user else [],
                "lifetime_withdrawn": compliance.lifetime_withdrawn(db, w.user_id),
                "kyc_status": user.kyc_status.value if user else None,
            }
        )
    return {"withdrawals": out}


@router.post("/withdrawals/{withdrawal_id}/review")
def review_withdrawal(
    withdrawal_id: str, payload: ReviewWithdrawalIn, admin: AdminUser, db: Db
):
    wd = db.get(Withdrawal, withdrawal_id)
    if wd is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Withdrawal not found.")
    wd = pay_svc.review_withdrawal(
        db, admin, wd, approve=payload.approve, note=payload.note, reason=payload.reason
    )
    return {
        "id": wd.id,
        "status": wd.status.value,
        "provider_ref": wd.provider_ref,
        "reviewed_at": wd.reviewed_at,
        "paid_at": wd.paid_at,
    }


@router.post("/withdrawals/{withdrawal_id}/mark-paid")
def mark_withdrawal_paid(
    withdrawal_id: str, payload: PayoutConfirmIn, admin: AdminUser, db: Db
):
    """Confirm a payout that was sent by hand from the rail's own console.

    The counterpart to approving: without it a manually paid withdrawal holds
    the player's money in `user_locked` indefinitely, because only a terminal
    state releases the block.
    """
    wd = db.get(Withdrawal, withdrawal_id)
    if wd is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Withdrawal not found.")
    wd = pay_svc.confirm_payout_paid(
        db, wd, admin, reference=payload.reference, note=payload.note
    )
    return {
        "id": wd.id,
        "status": wd.status.value,
        "provider_ref": wd.provider_ref,
        "paid_at": wd.paid_at,
    }


@router.get("/aml-queue")
def aml_queue(admin: AdminUser, db: Db, limit: int = Query(default=50, ge=1, le=200)):
    """Withdrawals over the review threshold or with risk flags."""
    rows = db.execute(
        select(Withdrawal)
        .where(Withdrawal.status.in_((WithdrawalStatus.requested, WithdrawalStatus.under_review)))
        .order_by(Withdrawal.amount.desc())
        .limit(limit)
    ).scalars().all()
    out = []
    for w in rows:
        user = db.get(User, w.user_id)
        flags = compliance.aml_flags(db, user) if user else []
        out.append(
            {
                "id": w.id,
                "username": user.username if user else None,
                "amount": w.amount,
                "method": w.method.value,
                "created_at": w.created_at,
                "flags": flags,
                "priority": len(flags) + (1 if w.amount >= 100_000 else 0),
            }
        )
    return {"queue": sorted(out, key=lambda r: -r["priority"])}


# ---------------------------------------------------------------------------
# marketing / bonuses
# ---------------------------------------------------------------------------
@router.post("/bonuses/grant", status_code=status.HTTP_201_CREATED)
def grant_bonus(payload: BonusCreateIn, admin: AdminUser, db: Db):
    user = db.get(User, payload.user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    row = bonus_svc.grant(
        db, user, money_field(payload.amount), code=payload.code.upper(),
        wager_multiplier=payload.wager_multiplier, source="manual",
    )
    db.add(
        AuditLog(
            actor_id=admin.id, actor_email=admin.email, action="bonus.grant",
            target=user.id, before={}, after={"amount": row.amount, "code": row.code,
                                              "reason": payload.reason},
        )
    )
    db.flush()
    return {"ok": True, "grant_id": row.id, "amount": row.amount,
            "wager_required": row.wager_required}


@router.get("/bonuses/codes")
def list_codes(admin: AdminUser, db: Db):
    rows = db.execute(select(BonusCode).order_by(BonusCode.created_at.desc())).scalars().all()
    return {
        "codes": [
            {
                "id": c.id, "code": c.code, "type": c.bonus_type, "value": float(c.value),
                "max_amount": c.max_amount, "wager_multiplier": float(c.wager_multiplier),
                "uses_left": c.uses_left, "active": c.active, "min_deposit": c.min_deposit,
            }
            for c in rows
        ]
    }


@router.post("/bonuses/codes", status_code=status.HTTP_201_CREATED)
def create_code(payload: BonusCodeIn, admin: AdminUser, db: Db):
    exists = db.execute(
        select(BonusCode).where(BonusCode.code == payload.code.upper())
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "That code already exists.")
    row = BonusCode(
        code=payload.code.upper(),
        bonus_type=payload.bonus_type,
        value=payload.value,
        max_amount=money_field(payload.max_amount),
        wager_multiplier=payload.wager_multiplier,
        uses_left=payload.uses_left,
        min_deposit=money_field(payload.min_deposit),
    )
    db.add(row)
    db.add(
        AuditLog(
            actor_id=admin.id, actor_email=admin.email, action="bonus.code_create",
            target=row.code, before={}, after={"type": row.bonus_type, "value": float(row.value)},
        )
    )
    db.flush()
    return {"ok": True, "id": row.id, "code": row.code}


# ---------------------------------------------------------------------------
# risk / audit
# ---------------------------------------------------------------------------
@router.get("/big-wins")
def big_wins(admin: AdminUser, db: Db, limit: int = Query(default=25, ge=1, le=100)):
    rows = db.execute(
        select(Bet, User.username)
        .join(User, Bet.user_id == User.id)
        .where(Bet.settled.is_(True), Bet.payout > 0)
        .order_by(Bet.payout.desc())
        .limit(limit)
    ).all()
    return {
        "wins": [
            {
                "username": u, "game": b.game, "stake": b.stake, "payout": b.payout,
                "multiplier": float(b.multiplier or 0), "at": b.settled_at or b.created_at,
            }
            for b, u in rows
        ]
    }


@router.get("/audit")
def audit_log(
    admin: AdminUser,
    db: Db,
    action: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action.like(f"%{action}%"))
    rows = db.execute(stmt.order_by(AuditLog.created_at.desc()).limit(limit)).scalars().all()
    return {
        "entries": [
            {
                "id": r.id, "actor": r.actor_email, "action": r.action, "target": r.target,
                "before": r.before, "after": r.after, "created_at": r.created_at,
            }
            for r in rows
        ]
    }


@router.get("/sessions")
def sessions(admin: AdminUser, db: Db, limit: int = Query(default=100, ge=1, le=500)):
    from ..models import SessionAudit

    rows = db.execute(
        select(SessionAudit).order_by(SessionAudit.created_at.desc()).limit(limit)
    ).scalars().all()
    return {
        "sessions": [
            {
                "id": r.id, "user_id": r.user_id, "action": r.action, "ip": r.ip,
                "user_agent": r.user_agent, "meta": r.meta, "created_at": r.created_at,
            }
            for r in rows
        ]
    }


@router.get("/ledger")
def ledger_explorer(admin: AdminUser, db: Db, limit: int = Query(default=100, ge=1, le=500)):
    rows = db.execute(
        select(LedgerTransaction).order_by(LedgerTransaction.created_at.desc()).limit(limit)
    ).scalars().all()
    return {
        "transactions": [
            {
                "id": t.id, "type": t.type.value, "status": t.status.value,
                "user_id": t.user_id, "reference": t.reference, "memo": t.memo,
                "entries": [
                    {"account": e.kind.value, "amount": e.amount, "balance_after": e.balance_after}
                    for e in t.entries
                ],
                "sum": sum(e.amount for e in t.entries),
                "created_at": t.created_at,
            }
            for t in rows
        ]
    }


@router.get("/webhooks")
def webhooks(admin: AdminUser, db: Db, limit: int = Query(default=100, ge=1, le=500)):
    rows = db.execute(
        select(PaymentWebhook).order_by(PaymentWebhook.created_at.desc()).limit(limit)
    ).scalars().all()
    return {
        "webhooks": [
            {
                "id": r.id, "provider": r.provider, "event_id": r.event_id,
                "event_type": r.event_type, "signature_valid": r.signature_valid,
                "processed": r.processed, "error": r.error, "created_at": r.created_at,
            }
            for r in rows
        ]
    }
