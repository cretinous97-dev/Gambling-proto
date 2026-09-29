"""Wallet projections for the API. Read-only - mutations live in `ledger`."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import AccountKind, Balance, Bet, BonusGrant, LedgerEntry, User
from ..money import fmt


def balances(db: Session, user: User) -> dict:
    rows = db.execute(select(Balance).where(Balance.user_id == user.id)).scalars().all()
    by_kind = {r.kind: r for r in rows}
    cash = by_kind.get(AccountKind.user_available)
    bonus = by_kind.get(AccountKind.user_bonus)
    locked = by_kind.get(AccountKind.user_locked)
    cash_amt = cash.amount if cash else 0
    bonus_amt = bonus.amount if bonus else 0
    locked_amt = locked.amount if locked else 0

    pending_wager = db.execute(
        select(func.coalesce(func.sum(BonusGrant.wager_required - BonusGrant.wagered), 0)).where(
            BonusGrant.user_id == user.id, BonusGrant.converted.is_(False)
        )
    ).scalar_one()

    return {
        "currency": user.display_currency,
        "cash": cash_amt,
        "bonus": bonus_amt,
        "locked": locked_amt,
        "total": cash_amt + bonus_amt,
        "withdrawable": max(cash_amt, 0),
        "pending_wager": int(pending_wager or 0),
        "cash_display": fmt(cash_amt),
        "bonus_display": fmt(bonus_amt),
        "locked_display": fmt(locked_amt),
        "total_display": fmt(cash_amt + bonus_amt),
        "pending_wager_display": fmt(int(pending_wager or 0)),
    }


def stats(db: Session, user: User) -> dict:
    row = db.execute(
        select(
            func.count(Bet.id),
            func.coalesce(func.sum(Bet.stake), 0),
            func.coalesce(func.sum(Bet.payout), 0),
            func.coalesce(func.sum(Bet.profit), 0),
        ).where(Bet.user_id == user.id)
    ).one()
    bets, wagered, returned, profit = row
    biggest = db.execute(
        select(func.coalesce(func.max(Bet.payout), 0)).where(Bet.user_id == user.id)
    ).scalar_one()
    return {
        "bets": int(bets or 0),
        "wagered": int(wagered or 0),
        "returned": int(returned or 0),
        "net": int(profit or 0),
        "biggest_win": int(biggest or 0),
        "wagered_display": fmt(int(wagered or 0)),
        "net_display": fmt(int(profit or 0)),
        "biggest_win_display": fmt(int(biggest or 0)),
        "rtp_actual": round((returned / wagered) * 100, 2) if wagered else 0.0,
    }


def ledger_sum_by_account(db: Session, user_id: str) -> dict[str, int]:
    rows = db.execute(
        select(LedgerEntry.kind, func.sum(LedgerEntry.amount))
        .where(LedgerEntry.user_id == user_id)
        .group_by(LedgerEntry.kind)
    ).all()
    return {(k.value if hasattr(k, "value") else str(k)): int(v or 0) for k, v in rows}
