"""Wallet: balances, statement, deposits and withdrawals.

Every mutation here is idempotent-capable: clients send an `Idempotency-Key`
(or the field in the body) so a double-tap on "Deposit" or a retried request
after a flaky connection can never double-charge or double-pay a player.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import select

from ..config import settings
from ..ledger import global_balance_check, user_statement
from ..models import (
    Deposit,
    DepositStatus,
    KycStatus,
    PaymentMethod,
    User,
    Withdrawal,
    WithdrawalStatus,
)
from ..money import to_minor
from ..payments import get_provider, get_provider_by_name, provider_status
from ..schemas import DepositIn, SimulateDepositIn, WithdrawalIn, money_field
from ..security import CurrentUser, Db, audit
from ..services import payments as pay_svc
from ..services import wallet as wallet_svc
from ..services.wallet import balances, ledger_sum_by_account, stats

router = APIRouter(prefix="/api/wallet", tags=["wallet"])

METHOD_LABELS = {
    PaymentMethod.card: "Debit / credit card",
    PaymentMethod.bank_transfer: "Bank transfer",
    PaymentMethod.crypto_btc: "Bitcoin (BTC)",
    PaymentMethod.crypto_eth: "Ethereum (ETH)",
    PaymentMethod.crypto_usdt: "Tether (USDT TRC-20)",
    PaymentMethod.ewallet: "E-wallet",
}


@router.get("/summary")
def summary(user: CurrentUser, db: Db):
    return {
        "balances": balances(db, user),
        "stats": stats(db, user),
        "provider": provider_status(),
        "accounts": ledger_sum_by_account(db, user.id),
    }


@router.get("/statement")
def statement(user: CurrentUser, db: Db, limit: int = Query(default=100, ge=1, le=500)):
    """Raw double-entry rows: every leg, for auditing. See /transactions for
    the player-facing view, which hides internal clearing legs."""
    return {"entries": user_statement(db, user.id, limit)}


@router.get("/transactions")
def transactions(
    user: CurrentUser,
    db: Db,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    kind: str | None = Query(default=None, max_length=32),
):
    """Paginated money history: settled ledger movement plus in-flight items.

    One endpoint for the dashboard because the alternative - the client
    merging a ledger page with a deposits page and a withdrawals page - gets
    the ordering, the pagination and the "is this already in the ledger?"
    question wrong in three different places.
    """
    return wallet_svc.transaction_history(
        db, user.id, limit=limit, offset=offset, kind=kind
    )


@router.get("/methods")
def methods(user: CurrentUser):
    provider = get_provider()
    return {
        "provider": provider.name,
        "simulation": provider.supports_simulation,
        "deposit": [
            {"method": m.value, "label": METHOD_LABELS[m], "enabled": True}
            for m in (
                PaymentMethod.card,
                PaymentMethod.crypto_btc,
                PaymentMethod.crypto_eth,
                PaymentMethod.crypto_usdt,
                PaymentMethod.bank_transfer,
            )
        ],
        "withdrawal": [
            {"method": m.value, "label": METHOD_LABELS[m], "enabled": True}
            for m in (
                PaymentMethod.crypto_btc,
                PaymentMethod.crypto_eth,
                PaymentMethod.crypto_usdt,
                PaymentMethod.bank_transfer,
                PaymentMethod.card,
            )
        ],
        "limits": {
            "min_deposit": int(settings.min_deposit_usd * 100),
            "max_deposit": int(settings.max_deposit_usd * 100),
            "min_withdrawal": int(settings.min_withdrawal_usd * 100),
            "max_withdrawal": int(settings.max_withdrawal_usd * 100),
            "withdrawal_fee_pct": settings.withdrawal_fee_pct,
        },
        "kyc_required": user.kyc_status is not KycStatus.verified,
    }


# ---------------------------------------------------------------------------
# deposits
# ---------------------------------------------------------------------------
@router.post("/deposits", status_code=status.HTTP_201_CREATED)
def create_deposit(payload: DepositIn, user: CurrentUser, db: Db, request: Request):
    amount = money_field(payload.amount)
    deposit = pay_svc.create_deposit(
        db,
        user,
        amount=amount,
        method=payload.method,
        idempotency_key=payload.idempotency_key,
        bonus_code=payload.bonus_code,
        banking_method_id=payload.banking_method_id,
    )
    audit(
        db, user.id, "deposit.create", request,
        {
            "deposit_id": deposit.id,
            "amount": amount,
            "method": payload.method.value,
            "banking_method_id": deposit.banking_method_id,
        },
    )
    return pay_svc.deposit_intent_payload(deposit)


@router.get("/deposits")
def list_deposits(user: CurrentUser, db: Db, limit: int = Query(default=25, ge=1, le=200)):
    rows = db.execute(
        select(Deposit)
        .where(Deposit.user_id == user.id)
        .order_by(Deposit.created_at.desc())
        .limit(limit)
    ).scalars().all()
    return {"deposits": [_deposit_out(d) for d in rows]}


@router.get("/deposits/{deposit_id}")
def get_deposit(deposit_id: str, user: CurrentUser, db: Db):
    deposit = _own_deposit(db, user, deposit_id)
    return _deposit_out(deposit, include_instructions=True)


@router.post("/deposits/{deposit_id}/simulate")
def simulate_deposit(
    deposit_id: str, payload: SimulateDepositIn, user: CurrentUser, db: Db, request: Request
):
    """SANDBOX ONLY. Stands in for the customer completing (or failing) the
    payment on the provider's page or for an on-chain confirmation."""
    deposit = _own_deposit(db, user, deposit_id)

    # Gate on the provider that carries *this deposit*, not on the deployment's
    # default one. With per-pathway routing those are different things: once a
    # deployment settles through a real rail (bank_transfer, adyen) while its
    # default is still `sandbox`, checking the default would hand every player a
    # button that credits a real-rail deposit for free.
    provider = get_provider_by_name(deposit.provider)
    if not provider.supports_simulation:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Simulation is disabled: this payment is being settled by a live "
            "provider. Complete the payment with the provider instead.",
        )
    mapped = {
        "succeed": DepositStatus.succeeded,
        "fail": DepositStatus.failed,
        "chargeback": DepositStatus.chargeback,
    }[payload.outcome]

    if deposit.status is mapped:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"This deposit is already {mapped.value}."
        )
    # A chargeback is a POST-settlement event: the bank reverses money that was
    # already credited, so it is the one transition allowed out of `succeeded`.
    if deposit.status is DepositStatus.succeeded and mapped is not DepositStatus.chargeback:
        raise HTTPException(status.HTTP_409_CONFLICT, "This deposit is already final.")
    if deposit.status is DepositStatus.chargeback:
        raise HTTPException(status.HTTP_409_CONFLICT, "This deposit is already final.")

    if mapped is DepositStatus.chargeback and deposit.status is not DepositStatus.succeeded:
        # A chargeback only makes sense against a settled payment: settle first.
        pay_svc.apply_deposit_status(db, deposit, DepositStatus.succeeded,
                                     bonus_code=deposit.bonus_code)
    pay_svc.apply_deposit_status(
        db, deposit, mapped,
        failure_reason="declined by issuer (simulated)" if mapped is DepositStatus.failed else None,
        bonus_code=deposit.bonus_code,
    )
    audit(db, user.id, "deposit.simulate", request,
          {"deposit_id": deposit.id, "outcome": payload.outcome})
    return _deposit_out(deposit, include_instructions=True)


def _own_deposit(db: Db, user: User, deposit_id: str) -> Deposit:
    deposit = db.get(Deposit, deposit_id)
    if deposit is None or deposit.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Deposit not found.")
    return deposit


def _deposit_out(d: Deposit, include_instructions: bool = False) -> dict:
    out = {
        "id": d.id,
        "amount": d.amount,
        "credited": d.credited,
        "bonus_credited": d.bonus_credited,
        "fee": d.fee,
        "currency": d.currency,
        "method": d.method.value,
        "provider": d.provider,
        "status": d.status.value,
        "reference": d.provider_ref,
        "card_last4": d.card_last4,
        "crypto_address": d.crypto_address,
        "crypto_txid": d.crypto_txid,
        "confirmations": d.confirmations,
        "failure_reason": d.failure_reason,
        "created_at": d.created_at,
        "completed_at": d.completed_at,
    }
    if include_instructions:
        out["instructions"] = d.instructions or {}
    return out


# ---------------------------------------------------------------------------
# withdrawals
# ---------------------------------------------------------------------------
@router.post("/withdrawals", status_code=status.HTTP_201_CREATED)
def create_withdrawal(payload: WithdrawalIn, user: CurrentUser, db: Db, request: Request):
    amount = money_field(payload.amount)
    wd = pay_svc.request_withdrawal(
        db,
        user,
        amount=amount,
        method=payload.method,
        destination=payload.destination,
        idempotency_key=payload.idempotency_key,
        banking_method_id=payload.banking_method_id,
    )
    audit(db, user.id, "withdrawal.request", request,
          {"withdrawal_id": wd.id, "amount": amount, "method": payload.method.value,
           "banking_method_id": wd.banking_method_id})
    return _withdrawal_out(wd)


@router.get("/withdrawals")
def list_withdrawals(user: CurrentUser, db: Db, limit: int = Query(default=25, ge=1, le=200)):
    rows = db.execute(
        select(Withdrawal)
        .where(Withdrawal.user_id == user.id)
        .order_by(Withdrawal.created_at.desc())
        .limit(limit)
    ).scalars().all()
    return {"withdrawals": [_withdrawal_out(w) for w in rows]}


@router.post("/withdrawals/{withdrawal_id}/cancel")
def cancel_withdrawal(withdrawal_id: str, user: CurrentUser, db: Db, request: Request):
    wd = db.get(Withdrawal, withdrawal_id)
    if wd is None or wd.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Withdrawal not found.")
    wd = pay_svc.cancel_withdrawal(db, user, wd)
    audit(db, user.id, "withdrawal.cancel", request, {"withdrawal_id": wd.id})
    return _withdrawal_out(wd)


def _withdrawal_out(w: Withdrawal) -> dict:
    return {
        "id": w.id,
        "amount": w.amount,
        "fee": w.fee,
        "net_amount": w.net_amount,
        "method": w.method.value,
        "destination": w.destination,
        "provider": w.provider,
        "provider_ref": w.provider_ref,
        "status": w.status.value,
        "review_note": w.review_note,
        "rejection_reason": w.rejection_reason,
        "created_at": w.created_at,
        "reviewed_at": w.reviewed_at,
        "paid_at": w.paid_at,
        "cancellable": w.status is WithdrawalStatus.under_review,
    }


# ---------------------------------------------------------------------------
# integrity (also exposed to admins in /api/admin/ledger-check)
# ---------------------------------------------------------------------------
@router.get("/integrity")
def integrity(user: CurrentUser, db: Db):
    """Public, per-user proof that the books balance. Each account total is
    shown so a player can see their own ledger sums match their balance."""
    check = global_balance_check(db)
    mine = ledger_sum_by_account(db, user.id)
    expected = -(mine.get("user_available", 0) + mine.get("user_bonus", 0) + mine.get("user_locked", 0))
    return {
        "books_balanced": check.get("__sum__") == 0,
        "system_sum": check.get("__sum__", 0),
        "accounts": {k: v for k, v in check.items() if k != "__sum__"},
        "your_accounts": mine,
        "your_net_contribution": expected,
    }
