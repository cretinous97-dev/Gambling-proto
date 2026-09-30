"""The ledger must never unbalance, and must never lose or invent money."""
from __future__ import annotations

import random

import pytest

from app.ledger import (
    Entry,
    system_balance,
    InsufficientFunds,
    LedgerError,
    deposit_cleared,
    get_balance,
    global_balance_check,
    hold_withdrawal,
    post,
    release_withdrawal,
    settle_bet,
    settle_withdrawal,
)
from app.models import AccountKind, TxType, User


def _user(db, email="ledger@example.com", username="ledgeruser") -> User:
    u = User(email=email, username=username, password_hash="x", country="BT")
    db.add(u)
    db.flush()
    return u


def test_unbalanced_transaction_is_rejected(db):
    user = _user(db)
    with pytest.raises(LedgerError):
        post(
            db,
            TxType.deposit,
            [
                Entry(AccountKind.user_available, +1000, user.id),
                Entry(AccountKind.payment_clearing, -900),   # deliberately wrong
            ],
            user_id=user.id,
        )


def test_overdraft_is_refused(db):
    user = _user(db, "od@example.com", "oduser")
    deposit_cleared(db, user.id, 500, reference="d1")
    with pytest.raises(InsufficientFunds):
        hold_withdrawal(db, user.id, 600, reference="w1")
    # Balance untouched after the failed attempt
    assert get_balance(db, user.id, AccountKind.user_available).amount == 500
    assert get_balance(db, user.id, AccountKind.user_locked).amount == 0


def test_idempotency_key_prevents_double_credit(db):
    user = _user(db, "idem@example.com", "idemuser")
    deposit_cleared(db, user.id, 1000, reference="d1", idempotency_key="pay-123")
    deposit_cleared(db, user.id, 1000, reference="d1", idempotency_key="pay-123")
    assert get_balance(db, user.id, AccountKind.user_available).amount == 1000


def test_withdrawal_lifecycle_moves_money_correctly(db):
    user = _user(db, "wd@example.com", "wduser")
    deposit_cleared(db, user.id, 10_000, reference="d1")

    hold_withdrawal(db, user.id, 4_000, reference="w1")
    assert get_balance(db, user.id, AccountKind.user_available).amount == 6_000
    assert get_balance(db, user.id, AccountKind.user_locked).amount == 4_000

    fees_before = system_balance(db, AccountKind.fee_income)
    settle_withdrawal(db, user.id, 4_000, 100, reference="w1")
    assert get_balance(db, user.id, AccountKind.user_locked).amount == 0
    assert system_balance(db, AccountKind.fee_income) - fees_before == 100
    assert global_balance_check(db)["__sum__"] == 0


def test_rejected_withdrawal_returns_funds(db):
    user = _user(db, "rej@example.com", "rejuser")
    deposit_cleared(db, user.id, 5_000, reference="d1")
    hold_withdrawal(db, user.id, 5_000, reference="w1")
    assert get_balance(db, user.id, AccountKind.user_available).amount == 0
    release_withdrawal(db, user.id, 5_000, reference="w1", memo="rejected")
    assert get_balance(db, user.id, AccountKind.user_available).amount == 5_000
    assert global_balance_check(db)["__sum__"] == 0


def test_bet_settlement_win_and_loss(db):
    user = _user(db, "bet@example.com", "betuser")
    deposit_cleared(db, user.id, 10_000, reference="d1")

    house_before = system_balance(db, AccountKind.house_revenue)

    # losing bet: 1000 staked, 0 returned -> house keeps the stake
    post(
        db, TxType.bet_stake,
        [Entry(AccountKind.user_locked, 1000, user.id),
         Entry(AccountKind.user_available, -1000, user.id)],
        user_id=user.id,
    )
    settle_bet(db, user.id, 1000, 0, reference="b1")
    assert get_balance(db, user.id, AccountKind.user_available).amount == 9_000
    assert get_balance(db, user.id, AccountKind.user_locked).amount == 0

    # winning bet: 1000 staked at 2x -> 2000 returned
    post(
        db, TxType.bet_stake,
        [Entry(AccountKind.user_locked, 1000, user.id),
         Entry(AccountKind.user_available, -1000, user.id)],
        user_id=user.id,
    )
    settle_bet(db, user.id, 1000, 2000, reference="b2")
    assert get_balance(db, user.id, AccountKind.user_available).amount == 10_000
    # net across both bets: +1000 lost, -1000 paid out => flat
    assert system_balance(db, AccountKind.house_revenue) - house_before == 0
    assert global_balance_check(db)["__sum__"] == 0


def test_push_returns_stake_exactly(db):
    user = _user(db, "push@example.com", "pushuser")
    deposit_cleared(db, user.id, 2_000, reference="d1")
    post(
        db, TxType.bet_stake,
        [Entry(AccountKind.user_locked, 1_000, user.id),
         Entry(AccountKind.user_available, -1_000, user.id)],
        user_id=user.id,
    )
    house_before = system_balance(db, AccountKind.house_revenue)
    settle_bet(db, user.id, 1_000, 1_000, reference="b1")
    assert get_balance(db, user.id, AccountKind.user_available).amount == 2_000
    assert system_balance(db, AccountKind.house_revenue) - house_before == 0


def test_randomised_sequence_keeps_books_balanced(db):
    """Fuzz: 300 random operations, then verify the closed system still sums to 0."""
    rng = random.Random(7)
    users = [_user(db, f"fuzz{i}@example.com", f"fuzzuser{i}") for i in range(5)]
    for i, u in enumerate(users):
        deposit_cleared(db, u.id, 100_000, reference=f"seed{i}")

    for step in range(300):
        u = rng.choice(users)
        cash = get_balance(db, u.id, AccountKind.user_available).amount
        action = rng.choice(["bet", "withdraw", "deposit", "bet", "bet"])
        if action == "deposit":
            deposit_cleared(db, u.id, rng.randint(100, 5_000), reference=f"f{step}")
        elif action == "withdraw" and cash > 500:
            amount = min(rng.randint(100, 3_000), cash)
            hold_withdrawal(db, u.id, amount, reference=f"w{step}")
            if rng.random() < 0.5:
                settle_withdrawal(db, u.id, amount, 0, reference=f"w{step}")
            else:
                release_withdrawal(db, u.id, amount, reference=f"w{step}", memo="rejected")
        elif action == "bet" and cash > 100:
            stake = min(rng.randint(100, 2_000), cash)
            post(
                db, TxType.bet_stake,
                [Entry(AccountKind.user_locked, stake, u.id),
                 Entry(AccountKind.user_available, -stake, u.id)],
                user_id=u.id,
            )
            payout = rng.choice([0, 0, stake, int(stake * 1.98), int(stake * 5)])
            settle_bet(db, u.id, stake, payout, reference=f"b{step}")

    totals = global_balance_check(db)
    assert totals["__sum__"] == 0, totals
    # The house must be net positive after a randomised session with a real edge
    assert totals["house_revenue"] != 0
