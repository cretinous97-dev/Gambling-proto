"""Bonuses, wager requirements, rakeback and VIP tiering.

Wager mechanics (how every licensed operator does it):
  * A bonus is granted into the SEPARATE bonus wallet. It is playable but not
    withdrawable.
  * Every settled bet adds its stake to the outstanding wager requirement of
    each active grant, weighted by `WAGER_WEIGHT` per game (slots 100%,
    blackjack 10%, ...) so bonus abuse on low-edge games is not profitable.
  * Once a grant's requirement is met the leftover bonus converts to cash and
    the grant closes. If the bonus is lost first, the grant closes with no
    conversion.
  * While a wager requirement is open, stakes drain the bonus wallet BEFORE
    cash. That is what players expect and what the published T&Cs state.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..ledger import Entry, get_balance, grant_bonus, post
from ..models import (
    AccountKind,
    Bet,
    BonusCode,
    BonusGrant,
    Notification,
    TxType,
    User,
    utcnow,
)

WAGER_WEIGHT = {
    "slots": 1.00,
    "dice": 0.50,
    "limbo": 0.50,
    "plinko": 0.50,
    "wheel": 0.50,
    "keno": 0.50,
    "coinflip": 0.25,
    "roulette": 0.20,
    "blackjack": 0.10,
    "mines": 0.50,
    "crash": 0.50,
}

VIP_TIERS = [
    {"tier": 0, "name": "Bronze", "wagered": 0, "rakeback_pct": 0.5},
    {"tier": 1, "name": "Silver", "wagered": 500_000, "rakeback_pct": 0.7},       # $5,000
    {"tier": 2, "name": "Gold", "wagered": 2_500_000, "rakeback_pct": 1.0},       # $25,000
    {"tier": 3, "name": "Platinum", "wagered": 10_000_000, "rakeback_pct": 1.5},  # $100,000
    {"tier": 4, "name": "Diamond", "wagered": 50_000_000, "rakeback_pct": 2.0},   # $500,000
]


def notify(db: Session, user_id: str, title: str, body: str) -> None:
    db.add(Notification(user_id=user_id, title=title, body=body))


def active_grants(db: Session, user_id: str) -> list[BonusGrant]:
    return list(
        db.execute(
            select(BonusGrant).where(
                BonusGrant.user_id == user_id, BonusGrant.converted.is_(False)
            )
        ).scalars()
    )


def open_wager_requirement(db: Session, user_id: str) -> int:
    return sum(max(g.wager_required - g.wagered, 0) for g in active_grants(db, user_id))


def bonus_stake_source(db: Session, user_id: str) -> AccountKind:
    """Bonus-first while a requirement is open, otherwise cash."""
    if open_wager_requirement(db, user_id) > 0:
        return AccountKind.user_bonus
    return AccountKind.user_available


def grant(
    db: Session,
    user: User,
    amount: int,
    *,
    code: str,
    wager_multiplier: float,
    source: str = "promo",
    max_cashout: int | None = None,
    days_valid: int = 30,
) -> BonusGrant:
    if amount <= 0:
        raise ValueError("bonus amount must be positive")
    row = BonusGrant(
        user_id=user.id,
        code=code,
        amount=amount,
        wager_required=int(amount * wager_multiplier),
        max_cashout=max_cashout,
        expires_at=utcnow() + timedelta(days=days_valid),
        source=source,
    )
    db.add(row)
    db.flush()
    grant_bonus(db, user.id, amount, reference=f"bonus:{row.id}")
    notify(
        db,
        user.id,
        f"Bonus credited: {code}",
        f"{amount / 100:.2f} is now in your bonus balance. Play through "
        f"{row.wager_required / 100:.2f} to convert it to cash.",
    )
    return row


def signup_bonus(db: Session, user: User) -> BonusGrant | None:
    amount = int(settings.signup_bonus_usd * 100)
    if amount <= 0:
        return None
    return grant(
        db,
        user,
        amount,
        code="WELCOME",
        wager_multiplier=settings.first_deposit_bonus_wager_x,
        source="signup",
    )


def first_deposit_bonus(
    db: Session, user: User, deposit_amount: int, code: str | None
) -> BonusGrant | None:
    already = db.execute(
        select(BonusGrant).where(
            BonusGrant.user_id == user.id, BonusGrant.source == "first_deposit"
        )
    ).scalars().first()
    if already is not None:
        return None

    if code:
        promo = db.execute(
            select(BonusCode).where(BonusCode.code == code.upper(), BonusCode.active.is_(True))
        ).scalar_one_or_none()
        if promo and promo.uses_left > 0 and deposit_amount >= promo.min_deposit:
            bonus = (
                deposit_amount
                if promo.bonus_type == "fixed"
                else int(deposit_amount * float(promo.value) / 100)
            )
            if promo.max_amount:
                bonus = min(bonus, promo.max_amount)
            promo.uses_left -= 1
            return grant(
                db,
                user,
                bonus,
                code=promo.code,
                wager_multiplier=float(promo.wager_multiplier),
                source="first_deposit",
            )
        # An explicit invalid code must never silently fall back to the default.
        return None

    pct = settings.first_deposit_bonus_pct
    if pct <= 0:
        return None
    bonus = min(
        int(deposit_amount * pct / 100), int(settings.first_deposit_bonus_max_usd * 100)
    )
    if bonus <= 0:
        return None
    return grant(
        db,
        user,
        bonus,
        code=f"FIRST{pct:.0f}",
        wager_multiplier=settings.first_deposit_bonus_wager_x,
        source="first_deposit",
    )


def apply_wager(db: Session, user: User, game: str, stake: int) -> None:
    weight = WAGER_WEIGHT.get(game, 0.5)
    contribution = int(stake * weight)
    if contribution <= 0:
        return
    for g in active_grants(db, user.id):
        if g.wager_required <= 0:
            continue
        g.wagered = min(g.wager_required, g.wagered + contribution)
        if g.wagered >= g.wager_required:
            _convert_grant(db, user, g)


def _convert_grant(db: Session, user: User, g: BonusGrant) -> None:
    bal = get_balance(db, user.id, AccountKind.user_bonus)
    remaining = min(bal.amount, g.amount)
    if g.max_cashout is not None:
        remaining = min(remaining, g.max_cashout)
    if remaining > 0:
        post(
            db,
            TxType.bonus_claim,
            [
                Entry(AccountKind.user_available, remaining, user.id),
                Entry(AccountKind.user_bonus, -remaining, user.id),
            ],
            user_id=user.id,
            reference=f"bonus-convert:{g.id}",
            memo=f"Bonus converted to cash ({g.code})",
        )
        notify(
            db,
            user.id,
            "Bonus converted to cash",
            f"Your {g.code} bonus passed its playthrough requirement. "
            f"{remaining / 100:.2f} is now withdrawable cash.",
        )
    g.converted = True
    g.wager_required = g.wagered


def expiry_sweep(db: Session) -> int:
    stale = db.execute(
        select(BonusGrant).where(
            BonusGrant.converted.is_(False),
            BonusGrant.expires_at.is_not(None),
            BonusGrant.expires_at < utcnow(),
        )
    ).scalars().all()
    for g in stale:
        g.converted = True
        g.wager_required = g.wagered
    return len(stale)


def rakeback_for(db: Session, user: User) -> int:
    net_loss = int(
        db.execute(
            select(func.coalesce(func.sum(Bet.stake - Bet.payout), 0)).where(
                Bet.user_id == user.id
            )
        ).scalar_one()
    )
    if net_loss <= 0:
        return 0
    rate = next(
        (t["rakeback_pct"] for t in reversed(VIP_TIERS) if user.wagered_lifetime >= t["wagered"]),
        settings.rakeback_pct,
    )
    return max(int(net_loss * rate / 100) - user.rakeback_claimed, 0)


def refresh_vip(db: Session, user: User) -> None:
    tier = 0
    for t in VIP_TIERS:
        if user.wagered_lifetime >= t["wagered"]:
            tier = t["tier"]
    if tier != user.vip_tier:
        user.vip_tier = tier
        notify(
            db,
            user.id,
            "VIP level up",
            f"You reached {VIP_TIERS[tier]['name']} - rakeback is now "
            f"{VIP_TIERS[tier]['rakeback_pct']}%.",
        )


def vip_progress(user: User) -> dict:
    current = VIP_TIERS[min(user.vip_tier, len(VIP_TIERS) - 1)]
    nxt = VIP_TIERS[user.vip_tier + 1] if user.vip_tier + 1 < len(VIP_TIERS) else None
    progress = 100.0
    if nxt:
        span = max(nxt["wagered"] - current["wagered"], 1)
        progress = round(
            max(0.0, min(100.0, (user.wagered_lifetime - current["wagered"]) / span * 100)), 2
        )
    return {
        "tier": user.vip_tier,
        "name": current["name"],
        "rakeback_pct": current["rakeback_pct"],
        "wagered_lifetime": user.wagered_lifetime,
        "next_name": nxt["name"] if nxt else None,
        "next_at": nxt["wagered"] if nxt else None,
        "progress_pct": progress,
    }
