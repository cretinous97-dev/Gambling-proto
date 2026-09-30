"""Wallet projections for the API. Read-only - mutations live in `ledger`."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    AccountKind,
    Balance,
    Bet,
    BonusGrant,
    Deposit,
    DepositStatus,
    LedgerEntry,
    LedgerTransaction,
    User,
    Withdrawal,
    WithdrawalStatus,
)
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
        # ALWAYS the settlement currency, never the player's display choice.
        # The amounts above are in this currency; labelling them with the
        # display currency would invite the next reader - client or server - to
        # treat a rendered figure as the money. The display preference is
        # reported separately, and conversion happens at render time only.
        "currency": settings.settlement_currency.upper(),
        "display_currency": user.display_currency,
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

#: Ledger types that are internal plumbing rather than a player-visible event.
#: A player does not need to see payment clearing, only what happened to their
#: money - showing the other leg of every double entry is a support call.
_PLAYER_FACING_EXCLUDE = {"chargeback"}


def transaction_history(
    db: Session,
    user_id: str,
    *,
    limit: int = 50,
    offset: int = 0,
    kind: str | None = None,
) -> dict:
    """A player's money history: what has settled, and what is still in flight.

    Two sources, because they answer different questions:

    * **settled** - the ledger. This is the truth: every movement that changed
      a balance, with the balance it produced, in a fixed order.
    * **pending** - deposits the processor has not confirmed and withdrawals a
      human has not released. These are NOT in the ledger, because no money has
      moved; a player refreshing the page still needs to see them, which is
      exactly why they cannot be read out of the ledger alone.

    Anything that appears in both is a bug, and the tests assert they do not.
    """
    from ..models import Deposit, LedgerEntry, LedgerTransaction, Withdrawal

    base = (
        select(LedgerEntry, LedgerTransaction)
        .join(LedgerTransaction, LedgerEntry.transaction_id == LedgerTransaction.id)
        .where(LedgerEntry.user_id == user_id)
        .order_by(LedgerEntry.created_at.desc(), LedgerEntry.id.desc())
    )
    if kind:
        base = base.where(LedgerTransaction.type == kind)

    total = int(
        db.execute(
            select(func.count(LedgerEntry.id))
            .join(LedgerTransaction, LedgerEntry.transaction_id == LedgerTransaction.id)
            .where(LedgerEntry.user_id == user_id)
            .where(*( [LedgerTransaction.type == kind] if kind else [] ))
        ).scalar_one()
    )

    rows = db.execute(base.limit(limit).offset(offset)).all()
    entries = [
        {
            "id": entry.id,
            "transaction_id": tx.id,
            "kind": tx.type.value,
            "status": tx.status.value,
            "account": entry.kind.value,
            "amount": entry.amount,              # signed, settlement minor units
            "balance_after": entry.balance_after,
            "memo": tx.memo,
            "reference": tx.reference,
            "created_at": tx.created_at,
        }
        for entry, tx in rows
        if tx.type.value not in _PLAYER_FACING_EXCLUDE or entry.amount < 0
    ]

    open_deposits = db.execute(
        select(Deposit)
        .where(
            Deposit.user_id == user_id,
            Deposit.status.in_([DepositStatus.pending, DepositStatus.requires_action]),
        )
        .order_by(Deposit.created_at.desc())
        .limit(20)
    ).scalars().all()

    open_withdrawals = db.execute(
        select(Withdrawal)
        .where(
            Withdrawal.user_id == user_id,
            Withdrawal.status.in_(
                [
                    WithdrawalStatus.requested,
                    WithdrawalStatus.under_review,
                    WithdrawalStatus.approved,
                ]
            ),
        )
        .order_by(Withdrawal.created_at.desc())
        .limit(20)
    ).scalars().all()

    pending = [
        {
            "id": d.id,
            "kind": "deposit",
            "status": d.status.value,
            "amount": d.amount,
            "method": d.method.value,
            "provider": d.provider,
            "created_at": d.created_at,
            "resumable": bool(d.instructions),
        }
        for d in open_deposits
    ] + [
        {
            "id": w.id,
            "kind": "withdrawal",
            "status": w.status.value,
            "amount": -w.amount,        # signed the same way as a settled entry
            "method": w.method.value,
            "provider": w.provider,
            "created_at": w.created_at,
            "resumable": False,
        }
        for w in open_withdrawals
    ]
    pending.sort(key=lambda row: row["created_at"], reverse=True)

    return {
        "entries": entries,
        "pending": pending,
        "total": total,
        "limit": limit,
        "offset": offset,
        "settlement_currency": settings.settlement_currency.upper(),
        # Said in the payload, not just in the docs: these numbers are the
        # money, and any localised figure the client shows is a rendering of
        # them, never a substitute.
        "amounts_are_minor_units": True,
    }

