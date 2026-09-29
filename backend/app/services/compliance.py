"""Responsible-gambling, KYC and jurisdiction enforcement.

This module is the operator's legal shield. Keep every check here rather than
scattering them through routers: a compliance rule that exists in three places
exists in two places.

Rules enforced:
  * Self-exclusion / cool-off  -> no bets, no deposits, ever, until it expires.
  * Jurisdiction policy        -> delegated to services/jurisdiction.py, which
                                  decides per country and per action. The
                                  default accepts every country; the tiers and
                                  what they gate are documented there.
  * Daily deposit limit        -> per-player, rolling UTC day.
  * Daily loss limit           -> net losses per UTC day.
  * KYC before withdrawal      -> required once lifetime withdrawals exceed
                                  KYC_REQUIRED_ABOVE_USD (AML/CTF requirement).
  * Age 18+ (configurable)     -> checked at registration.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from . import jurisdiction
from ..models import (
    Deposit,
    DepositStatus,
    KycStatus,
    LimitUsage,
    User,
    Withdrawal,
    WithdrawalStatus,
    utcnow,
)

MIN_AGE = 18
_TERMINAL_WITHDRAWAL = (
    WithdrawalStatus.requested,
    WithdrawalStatus.under_review,
    WithdrawalStatus.approved,
    WithdrawalStatus.paid,
)


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def usage_row(db: Session, user_id: str) -> LimitUsage:
    day = today()
    row = db.execute(
        select(LimitUsage).where(LimitUsage.user_id == user_id, LimitUsage.day == day)
    ).scalar_one_or_none()
    if row is None:
        row = LimitUsage(user_id=user_id, day=day)
        db.add(row)
        db.flush()
    return row


def add_deposit_usage(db: Session, user_id: str, amount: int) -> None:
    usage_row(db, user_id).deposit_total += amount


def add_wager_usage(db: Session, user_id: str, stake: int, profit: int) -> None:
    row = usage_row(db, user_id)
    row.wager_total += stake
    if profit < 0:
        row.loss_total += -profit


# ---------------------------------------------------------------------------
# gates
# ---------------------------------------------------------------------------
def assert_not_excluded(user: User) -> None:
    now = utcnow()
    if user.self_excluded_until and user.self_excluded_until > now:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You are self-excluded. Gambling activity is blocked until "
            f"{user.self_excluded_until.date().isoformat()}.",
        )
    if user.cool_off_until and user.cool_off_until > now:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Cool-off period active until {user.cool_off_until.date().isoformat()}.",
        )


def assert_can_deposit(db: Session, user: User, amount: int) -> None:
    assert_not_excluded(user)
    if user.is_banned:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account suspended.")
    jurisdiction.assert_allowed(user.country, jurisdiction.DEPOSIT)
    limit = user.deposit_limit_daily
    if limit:
        used = usage_row(db, user.id).deposit_total
        if used + amount > limit:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Daily deposit limit reached ({used / 100:.2f} of {limit / 100:.2f} used). "
                "Limits can only be lowered, never raised, within 24 hours.",
            )


def assert_can_bet(db: Session, user: User, stake: int) -> None:
    assert_not_excluded(user)
    if user.is_banned:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account suspended.")
    jurisdiction.assert_allowed(user.country, jurisdiction.PLAY)
    limit = user.loss_limit_daily
    if limit:
        row = usage_row(db, user.id)
        if row.loss_total >= limit:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Daily loss limit of {limit / 100:.2f} reached. Play resumes tomorrow (UTC).",
            )


def lifetime_withdrawn(db: Session, user_id: str) -> int:
    return int(
        db.execute(
            select(func.coalesce(func.sum(Withdrawal.net_amount), 0)).where(
                Withdrawal.user_id == user_id,
                Withdrawal.status.in_(_TERMINAL_WITHDRAWAL),
            )
        ).scalar_one()
    )


def assert_can_withdraw(db: Session, user: User, amount: int) -> None:
    assert_not_excluded(user)
    if user.is_banned:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account suspended.")
    jurisdiction.assert_allowed(user.country, jurisdiction.WITHDRAW)
    if not user.email_verified:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Verify your email address before withdrawing."
        )
    lifetime = lifetime_withdrawn(db, user.id)
    threshold = int(settings.kyc_required_above_usd * 100)
    if lifetime + amount > threshold and user.kyc_status is not KycStatus.verified:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Identity verification (KYC) is required before this withdrawal. "
            "Upload your documents under Account > Verification.",
        )


def assert_deposit_limits(amount: int) -> None:
    lo = int(settings.min_deposit_usd * 100)
    hi = int(settings.max_deposit_usd * 100)
    if amount < lo:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Minimum deposit is {lo / 100:.2f}.")
    if amount > hi:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Maximum single deposit is {hi / 100:.2f}. Contact support for larger amounts.",
        )


def assert_withdrawal_limits(amount: int) -> None:
    lo = int(settings.min_withdrawal_usd * 100)
    hi = int(settings.max_withdrawal_usd * 100)
    if amount < lo:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Minimum withdrawal is {lo / 100:.2f}.")
    if amount > hi:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Maximum single withdrawal is {hi / 100:.2f}. Split it into tranches.",
        )


def age_from(dob: date) -> int:
    t = datetime.now(timezone.utc).date()
    return t.year - dob.year - ((t.month, t.day) < (dob.month, dob.day))


def assert_age(dob: date) -> None:
    if age_from(dob) < MIN_AGE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"You must be at least {MIN_AGE}.")


def aml_flags(db: Session, user: User) -> list[str]:
    """Signals surfaced to the admin review queue. Not automated decisions -
    a human always approves or rejects a withdrawal."""
    flags: list[str] = []
    deposits_24h = int(
        db.execute(
            select(func.coalesce(func.sum(Deposit.amount), 0)).where(
                Deposit.user_id == user.id,
                Deposit.status == DepositStatus.succeeded,
                Deposit.completed_at >= utcnow() - timedelta(days=1),
            )
        ).scalar_one()
    )
    withdrawals_24h = int(
        db.execute(
            select(func.coalesce(func.sum(Withdrawal.amount), 0)).where(
                Withdrawal.user_id == user.id,
                Withdrawal.created_at >= utcnow() - timedelta(days=1),
            )
        ).scalar_one()
    )
    if user.kyc_status is not KycStatus.verified:
        flags.append("kyc_not_verified")
    if deposits_24h >= 500_000:
        flags.append("deposits_over_5k_24h")
    if withdrawals_24h >= 500_000:
        flags.append("withdrawals_over_5k_24h")
    if user.created_at and (utcnow() - user.created_at) < timedelta(hours=24):
        flags.append("account_under_24h")
    if not user.email_verified:
        flags.append("email_unverified")
    return flags
