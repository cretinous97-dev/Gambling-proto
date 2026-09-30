"""Double-entry ledger.

THE ONE RULE: within a single transaction, the signed `amount`s of all entries
must sum to exactly zero. Money is never created or destroyed, only moved
between accounts. If you find yourself wanting to break this rule, you want a
new AccountKind instead.

Because every mutation is a balanced transaction, you can always reconstruct
balances with:

    SELECT kind, SUM(amount) FROM ledger_entries GROUP BY kind

and the total across all accounts must equal zero. `tests/test_flows.py`
asserts exactly that after a randomised sequence of deposits, bets and
withdrawals.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    AccountKind,
    Balance,
    LedgerEntry,
    LedgerTransaction,
    TxStatus,
    TxType,
    User,
    utcnow,
)


#: Owner id used for system (non-player) accounts.
SYSTEM_OWNER = "__system__"


class LedgerError(Exception):
    """Raised when a movement would break the books or overdraw an account."""


class InsufficientFunds(LedgerError):
    pass


#: Player wallets must never go negative - that is the invariant that stops a
#: bug from turning into a player debt. System accounts are accumulators
#: (clearing, revenue, pools) and are EXPECTED to swing negative: a negative
#: `payment_clearing` simply means "the PSP is holding that much of our money".
PLAYER_ACCOUNTS = frozenset(
    {AccountKind.user_available, AccountKind.user_bonus, AccountKind.user_locked}
)


@dataclass(frozen=True)
class Entry:
    kind: AccountKind
    amount: int                      # signed cents
    user_id: str | None = None


def get_balance(db: Session, user_id: str | None, kind: AccountKind) -> Balance:
    """Row-locked fetch-or-create of a balance row.

    System accounts use the sentinel owner ``SYSTEM_OWNER`` so that every
    account in the chart has a row and the per-account sums are always
    reconstructible from `ledger_entries` alone.
    """
    if user_id is None:
        user_id = SYSTEM_OWNER
    stmt = (
        select(Balance)
        .where(Balance.user_id == user_id, Balance.kind == kind)
        .with_for_update()
    )
    bal = db.execute(stmt).scalar_one_or_none()
    if bal is None:
        bal = Balance(user_id=user_id, kind=kind, amount=0)
        db.add(bal)
        db.flush()
    return bal


def system_balance(db: Session, kind: AccountKind) -> int:
    """Balance of an operator account (house revenue, clearing, pools...).

    Sign convention reminder: `payment_clearing` is the external counterparty,
    so a NEGATIVE clearing balance is normal and means "the operator is holding
    this much player money". `house_revenue` positive = the house is up.
    """
    return get_balance(db, None, kind).amount


def available(db: Session, user_id: str) -> int:
    return get_balance(db, user_id, AccountKind.user_available).amount


def total_playable(db: Session, user_id: str) -> int:
    """Cash + bonus. Bonus is playable but not withdrawable until wagered."""
    return (
        get_balance(db, user_id, AccountKind.user_available).amount
        + get_balance(db, user_id, AccountKind.user_bonus).amount
    )


def post(
    db: Session,
    tx_type: TxType,
    entries: list[Entry],
    *,
    user_id: str | None = None,
    reference: str | None = None,
    memo: str | None = None,
    meta: dict | None = None,
    idempotency_key: str | None = None,
    status: TxStatus = TxStatus.posted,
    allow_negative: bool = False,
) -> LedgerTransaction:
    """Apply a balanced set of entries atomically.

    Caller owns the surrounding DB transaction (FastAPI's `get_db` commits on
    success / rolls back on exception), so a failure here leaves zero trace.
    """
    if idempotency_key:
        existing = db.execute(
            select(LedgerTransaction).where(
                LedgerTransaction.idempotency_key == idempotency_key
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing            # replay-safe: return the original event

    if not entries:
        raise LedgerError("a transaction needs at least one entry")

    total = sum(e.amount for e in entries)
    if total != 0:
        raise LedgerError(f"unbalanced ledger transaction: sum={total} (must be 0)")

    # Two passes. Pass 1 resolves balances and validates every leg; pass 2
    # applies. If any leg would fail, NOTHING is written - a half-applied
    # transaction is how a ledger silently loses money.
    resolved: list[tuple[Entry, str | None, Balance]] = []
    for e in entries:
        # Only PLAYER accounts are attributed to a player. System accounts
        # (revenue, clearing, pools) deliberately carry user_id=None so they
        # never show up in a player's statement and never skew their totals.
        uid = (
            e.user_id or user_id
            if e.kind in PLAYER_ACCOUNTS
            else e.user_id
        )
        if uid is None and e.kind in PLAYER_ACCOUNTS:
            raise LedgerError(f"entry for {e.kind} has no user and no tx-level user")
        bal = get_balance(db, uid, e.kind)
        if (
            e.kind in PLAYER_ACCOUNTS
            and not allow_negative
            and bal.amount + e.amount < 0
        ):
            raise InsufficientFunds(
                f"{e.kind.value} balance would go negative ({bal.amount} + {e.amount})"
            )
        resolved.append((e, uid, bal))

    tx = LedgerTransaction(
        type=tx_type,
        status=status,
        user_id=user_id,
        reference=reference,
        memo=memo,
        meta=meta or {},
        idempotency_key=idempotency_key,
    )
    db.add(tx)
    db.flush()

    for e, uid, bal in resolved:
        bal.amount += e.amount
        bal.updated_at = utcnow()
        db.add(
            LedgerEntry(
                transaction_id=tx.id,
                # System legs keep user_id=None: they belong to the operator,
                # not to the player whose action triggered them.
                user_id=None if uid == SYSTEM_OWNER else uid,
                kind=e.kind,
                amount=e.amount,
                balance_after=bal.amount,
            )
        )
    db.flush()
    return tx


# ---------------------------------------------------------------------------
# convenience movers - every one of these is a balanced hand above `post`
# ---------------------------------------------------------------------------
HOUSE = Entry(AccountKind.house_revenue, 0)  # placeholder for readability


def deposit_cleared(
    db: Session,
    user_id: str,
    amount: int,
    *,
    reference: str,
    idempotency_key: str | None = None,
    memo: str = "Deposit cleared",
) -> LedgerTransaction:
    """Money arrives from the PSP: clearing -> player cash. House is unaffected."""

    def _entries() -> list[Entry]:
        return [
            Entry(AccountKind.user_available, +amount, user_id),
            Entry(AccountKind.payment_clearing, -amount),
        ]

    return post(
        db,
        TxType.deposit,
        _entries(),
        user_id=user_id,
        reference=reference,
        memo=memo,
        idempotency_key=idempotency_key,
    )


def grant_bonus(
    db: Session,
    user_id: str,
    amount: int,
    *,
    reference: str,
    idempotency_key: str | None = None,
) -> LedgerTransaction:
    """Promotional credit: funded from bonus_pool -> player's bonus wallet."""
    return post(
        db,
        TxType.bonus_claim,
        [
            Entry(AccountKind.user_bonus, +amount, user_id),
            Entry(AccountKind.bonus_pool, -amount),
        ],
        user_id=user_id,
        reference=reference,
        memo="Bonus granted",
        idempotency_key=idempotency_key,
    )


def hold_withdrawal(
    db: Session, user_id: str, amount: int, *, reference: str, memo: str = "Withdrawal requested"
) -> LedgerTransaction:
    """Cash is debited immediately and parked in user_locked while in review.

    This is the standard casino/PSP pattern: the player cannot spend money that
    is already promised to a withdrawal, and if the request is rejected the
    hold is reversed with `release_withdrawal`.
    """
    return post(
        db,
        TxType.withdrawal_hold,
        [
            Entry(AccountKind.user_locked, +amount, user_id),
            Entry(AccountKind.user_available, -amount, user_id),
        ],
        user_id=user_id,
        reference=reference,
        memo=memo,
    )


def release_withdrawal(
    db: Session, user_id: str, amount: int, *, reference: str, memo: str
) -> LedgerTransaction:
    """Return a held amount to spendable cash (rejection / cancellation)."""
    return post(
        db,
        TxType.withdrawal_refund,
        [
            Entry(AccountKind.user_available, +amount, user_id),
            Entry(AccountKind.user_locked, -amount, user_id),
        ],
        user_id=user_id,
        reference=reference,
        memo=memo,
    )


def settle_withdrawal(
    db: Session,
    user_id: str,
    amount: int,
    fee: int,
    *,
    reference: str,
    idempotency_key: str | None = None,
) -> LedgerTransaction:
    """Provider confirms payout. Locked cash leaves the system for good; the fee
    becomes operator income.

    Sign convention for `payment_clearing`: it is the external counterparty
    account. Deposits push it negative (money came IN from outside the closed
    system), payouts push it positive (money went OUT). Its running balance is
    therefore "net player money we are still holding" - which is exactly what
    the treasury reconciles against the PSP's settlement report.
    """
    entries = [Entry(AccountKind.user_locked, -amount, user_id)]
    if fee > 0:
        entries.append(Entry(AccountKind.fee_income, +fee))
        entries.append(Entry(AccountKind.payment_clearing, +(amount - fee)))
    else:
        entries.append(Entry(AccountKind.payment_clearing, +amount))
    return post(
        db,
        TxType.withdrawal_settled,
        entries,
        user_id=user_id,
        reference=reference,
        memo="Withdrawal paid out",
        idempotency_key=idempotency_key,
    )


def place_bet_hold(
    db: Session, user_id: str, stake: int, *, kind: AccountKind, reference: str
) -> LedgerTransaction:
    """Stake moves out of a spendable wallet into `user_locked` for the duration
    of the round. Never straight to the house - the house only receives the
    losing stake once the round is settled."""
    return post(
        db,
        TxType.bet_stake,
        [
            Entry(AccountKind.user_locked, +stake, user_id),
            Entry(kind, -stake, user_id),
        ],
        user_id=user_id,
        reference=reference,
        memo=f"Stake locked ({reference})",
    )


def settle_bet(
    db: Session,
    user_id: str,
    stake: int,
    payout: int,
    *,
    reference: str,
    settled_entries: list[Entry] | None = None,
) -> LedgerTransaction:
    """Close a round.

    Locked stake + house payout must net to zero:
      * player wins : house pays `payout` from revenue, player receives it
      * player loses: locked stake becomes house revenue
    """
    if stake == 0:
        return post(
            db,
            TxType.bet_payout,
            settled_entries or [],
            user_id=user_id,
            reference=reference,
            memo="Free bet settled",
        )

    fee_entries = settled_entries or []
    if payout >= stake:
        # Player is up (or push): locked stake returns to them plus winnings
        # funded by the house. On a push, payout == stake and house nets zero.
        profit = payout - stake
        entries = [
            Entry(AccountKind.user_locked, -stake, user_id),
            Entry(AccountKind.user_available, +stake, user_id),
        ]
        if profit > 0:
            entries.append(Entry(AccountKind.user_available, +profit, user_id))
            entries.append(Entry(AccountKind.house_revenue, -profit))
    else:
        # Player loses part or all of the stake: the lost part is house revenue
        lost = stake - payout
        entries = [
            Entry(AccountKind.user_locked, -stake, user_id),
            Entry(AccountKind.user_available, +payout, user_id),
            Entry(AccountKind.house_revenue, +lost),
        ]
    return post(
        db,
        TxType.bet_payout,
        entries + fee_entries,
        user_id=user_id,
        reference=reference,
        memo="Bet settled",
    )


def refund_bet(
    db: Session, user_id: str, stake: int, *, kind: AccountKind, reference: str
) -> LedgerTransaction:
    """Cancelled round (server error, void bet): give the stake straight back."""
    return post(
        db,
        TxType.bet_refund,
        [
            Entry(kind, +stake, user_id),
            Entry(AccountKind.user_locked, -stake, user_id),
        ],
        user_id=user_id,
        reference=reference,
        memo="Stake refunded (void bet)",
    )


def admin_adjust(
    db: Session, user_id: str, amount: int, *, reference: str, memo: str
) -> LedgerTransaction:
    """Manual credit/debit by an admin. Always audit-logged by the caller."""
    if amount >= 0:
        entries = [
            Entry(AccountKind.user_available, +amount, user_id),
            Entry(AccountKind.house_revenue, -amount),
        ]
    else:
        entries = [
            Entry(AccountKind.user_available, +amount, user_id),
            Entry(AccountKind.house_revenue, -amount),
        ]
    return post(
        db,
        TxType.admin_adjustment,
        entries,
        user_id=user_id,
        reference=reference,
        memo=memo,
    )


def chargeback(
    db: Session, user_id: str, amount: int, *, reference: str, memo: str = "Chargeback received"
) -> LedgerTransaction:
    """The PSP claws a deposit back. Player balance goes negative if needed -
    that is a legitimate debt, tracked as chargeback_loss, not an error."""
    return post(
        db,
        TxType.chargeback,
        [
            Entry(AccountKind.user_available, -amount, user_id),
            Entry(AccountKind.chargeback_loss, +amount),
        ],
        user_id=user_id,
        reference=reference,
        memo=memo,
        allow_negative=True,
    )


def global_balance_check(db: Session) -> dict[str, int]:
    """Integrity probe: per-account sums must total zero."""
    rows = db.execute(
        select(LedgerEntry.kind, LedgerEntry.amount)
    ).all()
    totals: dict[str, int] = {}
    for kind, amount in rows:
        key = kind.value if hasattr(kind, "value") else str(kind)
        totals[key] = totals.get(key, 0) + amount
    totals["__sum__"] = sum(totals.values())
    return totals


def user_statement(db: Session, user_id: str, limit: int = 100) -> list[dict]:
    rows = (
        db.execute(
            select(LedgerEntry, LedgerTransaction)
            .join(LedgerTransaction, LedgerEntry.transaction_id == LedgerTransaction.id)
            .where(LedgerEntry.user_id == user_id)
            .order_by(LedgerEntry.created_at.desc())
            .limit(limit)
        )
        .all()
    )
    out = []
    for entry, tx in rows:
        out.append(
            {
                "id": entry.id,
                "transaction_id": tx.id,
                "type": tx.type.value,
                "status": tx.status.value,
                "account": entry.kind.value,
                "amount": entry.amount,
                "balance_after": entry.balance_after,
                "memo": tx.memo,
                "reference": tx.reference,
                "created_at": tx.created_at,
            }
        )
    return out


def ensure_user_accounts(db: Session, user: User) -> None:
    for kind in (
        AccountKind.user_available,
        AccountKind.user_bonus,
        AccountKind.user_locked,
    ):
        get_balance(db, user.id, kind)
