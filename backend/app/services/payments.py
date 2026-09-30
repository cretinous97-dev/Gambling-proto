"""Deposits and withdrawals - the business layer.

State machines (enforced here, not in the routers):

  Deposit:    pending -> requires_action -> succeeded
                            \\-> failed | cancelled | chargeback

  Withdrawal: requested -> under_review -> approved -> paid
                    \\-> rejected (hold released)  \\-> failed (hold released)

Guarantees:
  * A deposit is credited EXACTLY ONCE. `apply_deposit_status` checks the
    terminal state and the ledger idempotency key before moving money.
  * A withdrawal debits the player the moment it is requested (moved to
    `user_locked`), so the same cash can never be requested twice while the
    first request is being reviewed.
  * Rejecting or failing a payout always releases the hold back to spendable
    cash, in the same transaction as the status change.
  * Every admin decision is written to `audit_log` with before/after values.
"""
from __future__ import annotations

import hashlib
import logging
import secrets

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..ledger import (
    InsufficientFunds,
    chargeback,
    deposit_cleared,
    get_balance,
    hold_withdrawal,
    post,
    release_withdrawal,
    settle_withdrawal,
)
from ..models import (
    AccountKind,
    AuditLog,
    BankingMethod,
    Deposit,
    DepositStatus,
    KycStatus,
    PaymentMethod,
    PaymentWebhook,
    TxType,
    User,
    Withdrawal,
    WithdrawalStatus,
    utcnow,
)
from ..money import pct_of
from ..payments import PaymentError, get_provider, get_provider_by_name
from . import bonus as bonus_svc
from . import compliance
from . import routing

log = logging.getLogger("app.payments.ledger")

TERMINAL_DEPOSIT = (DepositStatus.succeeded, DepositStatus.chargeback)
CRYPTO_METHODS = (
    PaymentMethod.crypto_btc,
    PaymentMethod.crypto_eth,
    PaymentMethod.crypto_usdt,
)


def _ref(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


# ---------------------------------------------------------------------------
# deposits
# ---------------------------------------------------------------------------
def _country_of(user: User, override: str | None = None) -> str:
    """The country routing is decided on.

    The account's own country, never a caller-supplied one, unless a caller
    explicitly overrides it for a preview. A player cannot make a transaction
    route to a pathway their account is not eligible for by sending a header:
    eligibility is a property of the account, decided at registration, which is
    what the KYC and jurisdiction checks already assume.
    """
    return (override or getattr(user, "country", "") or "").upper()


def _resolve_pathway(
    db: Session,
    *,
    user: User,
    direction: str,
    amount_minor: int,
    country: str | None = None,
    banking_method_id: str | None = None,
) -> BankingMethod | None:
    """Pick the pathway for this transaction, or explain why there is none.

    An explicitly requested pathway is honoured only if it is actually usable:
    active, enabled for this direction, and eligible for the player's country
    and the amount. Otherwise a player could force a deposit through a rail the
    operator has switched off, or through one that does not serve their market,
    by editing the request - and the money would land in an account nobody
    intended.

    Returns None when the operator has configured nothing at all, which keeps an
    unconfigured deployment working exactly as it did before pathways existed.
    """
    currency = instrument_currency(db, user)

    if banking_method_id:
        row = db.get(BankingMethod, banking_method_id)
        if row is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "That payment method no longer exists."
            )
        allowed = routing.candidates(
            db,
            country=_country_of(user, country),
            currency=currency,
            direction=direction,
            amount_minor=amount_minor,
        )
        if row.id not in {m.id for m in allowed}:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{row.name} is not available for this account right now.",
            )
        return row

    return routing.resolve(
        db,
        country=_country_of(user, country),
        currency=currency,
        direction=direction,
        amount_minor=amount_minor,
    )


def instrument_currency(db: Session, user: User) -> str:
    """The currency this player's payments are denominated in.

    The **market's** currency, from the account's country - deliberately not the
    player's `display_currency`. Those answer different questions. Display
    currency is what a figure is converted to on the way to the screen, and a
    player is entitled to read their balance in USD. Payment currency is what
    the rail on the other end settles in: a player in Bhutan pays mBoB in
    ngultrum whatever they prefer to read, and a request denominated in USD to
    a ngultrum rail is one the rail will reject.

    Falls back to the deployment default for a market with no mapping, which is
    what keeps global access working for countries nobody has configured.
    """
    country = _country_of(user)
    if country:
        from ..i18n import currency_for

        resolved = currency_for(country)
        if resolved:
            return resolved
    return settings.default_currency


def create_deposit(
    db: Session,
    user: User,
    *,
    amount: int,
    method: PaymentMethod,
    idempotency_key: str | None = None,
    bonus_code: str | None = None,
    banking_method_id: str | None = None,
    country: str | None = None,
) -> Deposit:
    compliance.assert_deposit_limits(amount)
    compliance.assert_can_deposit(db, user, amount)

    if idempotency_key:
        existing = db.execute(
            select(Deposit).where(Deposit.idempotency_key == idempotency_key)
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    # Which pathway carries this is an operator decision, made in the admin
    # panel, resolved here from the player's country and the transaction
    # currency. `banking_method_id` forces a specific one, which is what the
    # cashier sends when the player picks a bank from the list.
    pathway = _resolve_pathway(
        db,
        user=user,
        direction=routing.DEPOSIT,
        amount_minor=amount,
        country=country,
        banking_method_id=banking_method_id,
    )
    provider = get_provider_by_name(routing.effective_provider(pathway))

    # Denominate the deposit in the currency the rail actually settles in. A
    # pathway pinned to one currency wins; otherwise the player's market
    # currency. Getting this wrong would hand a ngultrum rail a dollar amount
    # and a mismatch the provider rejects - or worse, one it accepts.
    deposit_currency = instrument_currency(db, user)
    if pathway is not None and pathway.currency != routing.ANY:
        deposit_currency = pathway.currency

    deposit = Deposit(
        user_id=user.id,
        amount=amount,
        method=method,
        currency=deposit_currency,
        provider=provider.name,
        banking_method_id=pathway.id if pathway else None,
        status=DepositStatus.pending,
        idempotency_key=idempotency_key,
    )
    db.add(deposit)
    db.flush()

    try:
        intent = provider.create_deposit(db, user, deposit)
    except PaymentError as exc:
        deposit.status = DepositStatus.failed
        deposit.failure_reason = str(exc)[:255]
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Payment provider error: {exc}")

    deposit.fee = intent.fee + (pct_of(amount, pathway.fee_bps / 100) if pathway else 0)
    deposit.bonus_code = bonus_code
    deposit.status = (
        DepositStatus(intent.status)
        if intent.status in {s.value for s in DepositStatus}
        else DepositStatus.requires_action
    )
    # Persisted so a page reload can resume an unfinished checkout.
    deposit.instructions = {
        **(intent.instructions or {}),
        "redirect_url": intent.redirect_url,
        "client_secret": intent.client_secret,
    }
    db.flush()
    return deposit


def deposit_intent_payload(deposit: Deposit) -> dict:
    return {
        "deposit_id": deposit.id,
        "amount": deposit.amount,
        "method": deposit.method.value,
        "provider": deposit.provider,
        "status": deposit.status.value,
        "reference": deposit.provider_ref,
        "instructions": deposit.instructions or {},
    }


def apply_deposit_status(
    db: Session,
    deposit: Deposit,
    new_status: DepositStatus,
    *,
    failure_reason: str | None = None,
    bonus_code: str | None = None,
) -> Deposit:
    """The ONLY function allowed to move a deposit into a terminal state.

    Safe to call twice with the same status: the second call is a no-op, which
    is what makes provider webhook retries harmless.
    """
    if deposit.status == new_status:
        return deposit                                  # webhook retry: no-op
    if deposit.status is DepositStatus.chargeback:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"deposit {deposit.id} is already charged back"
        )
    if deposit.status is DepositStatus.succeeded and new_status is not DepositStatus.chargeback:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"deposit {deposit.id} is already succeeded"
        )

    user = db.get(User, deposit.user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "deposit owner not found")

    if new_status is DepositStatus.succeeded:
        credited = deposit.amount - deposit.fee
        deposit_cleared(
            db,
            user.id,
            credited,
            reference=f"deposit:{deposit.id}",
            idempotency_key=f"deposit-credit:{deposit.id}",
        )
        deposit.credited = credited
        deposit.status = DepositStatus.succeeded
        deposit.completed_at = utcnow()
        compliance.add_deposit_usage(db, user.id, deposit.amount)
        bonus_svc.notify(
            db,
            user.id,
            "Deposit confirmed",
            f"Your {credited / 100:.2f} deposit is available to play. Reference {deposit.provider_ref}.",
        )
        # Promotional match (first deposit / explicit code).
        grant = bonus_svc.first_deposit_bonus(db, user, deposit.amount, bonus_code)
        if grant is not None:
            deposit.bonus_credited = grant.amount

    elif new_status is DepositStatus.chargeback:
        if deposit.credited <= 0:
            deposit.status = DepositStatus.cancelled
            deposit.failure_reason = "chargeback on an uncredited deposit"
            return deposit
        try:
            chargeback(
                db,
                user.id,
                deposit.credited,
                reference=f"chargeback:{deposit.id}",
                memo=f"Chargeback on deposit {deposit.id}",
            )
        except InsufficientFunds:
            # Balance already spent: debt is recorded, account is frozen for
            # manual review rather than letting the balance go negative silently.
            chargeback(
                db,
                user.id,
                deposit.credited,
                reference=f"chargeback:{deposit.id}",
                memo=f"Chargeback (balance insufficient) {deposit.id}",
            )
            user.is_banned = True
            bonus_svc.notify(
                db, user.id, "Account under review",
                "A payment was reversed by your bank. Please contact support.",
            )
        deposit.status = DepositStatus.chargeback
        deposit.completed_at = utcnow()

    elif new_status is DepositStatus.failed:
        deposit.status = DepositStatus.failed
        deposit.failure_reason = failure_reason or "payment declined"

    elif new_status is DepositStatus.cancelled:
        deposit.status = DepositStatus.cancelled

    else:
        deposit.status = new_status

    db.flush()
    return deposit


# ---------------------------------------------------------------------------
# withdrawals
# ---------------------------------------------------------------------------
def request_withdrawal(
    db: Session,
    user: User,
    *,
    amount: int,
    method: PaymentMethod,
    destination: str,
    idempotency_key: str | None = None,
    banking_method_id: str | None = None,
) -> Withdrawal:
    compliance.assert_withdrawal_limits(amount)
    compliance.assert_can_withdraw(db, user, amount)

    if idempotency_key:
        existing = db.execute(
            select(Withdrawal).where(Withdrawal.idempotency_key == idempotency_key)
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    destination = destination.strip()
    _validate_destination(method, destination)

    fee = pct_of(amount, settings.withdrawal_fee_pct)
    net = amount - fee
    if net <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Amount is too small after fees.")

    cash = get_balance(db, user.id, AccountKind.user_available)
    if cash.amount <= 0:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED, "No cash balance available to withdraw."
        )
    if cash.amount < amount:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"Withdrawable cash is {cash.amount / 100:.2f}. Bonus funds must be played "
            f"through before they can be withdrawn.",
        )

    # Which rail pays this out is the operator's decision, same as deposits.
    # Resolved before the hold so an unroutable payout fails without moving
    # money into `user_locked` and needing a release that nobody watches.
    payout_pathway = _resolve_pathway(
        db,
        user=user,
        direction=routing.WITHDRAWAL,
        amount_minor=amount,
        banking_method_id=banking_method_id,
    )

    wd = Withdrawal(
        user_id=user.id,
        amount=amount,
        fee=fee,
        net_amount=net,
        method=method,
        destination=_mask(method, destination),
        provider=get_provider_by_name(routing.effective_provider(payout_pathway)).name,
        banking_method_id=payout_pathway.id if payout_pathway else None,
        status=WithdrawalStatus.requested,
        idempotency_key=idempotency_key,
    )
    db.add(wd)
    db.flush()

    try:
        hold_withdrawal(
            db, user.id, amount, reference=f"withdrawal:{wd.id}",
            memo=f"Withdrawal requested ({_mask(method, destination)})",
        )
    except InsufficientFunds:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "Insufficient cash balance.")

    # Default posture is human review. Auto-approval is opt-in and capped by
    # config, and is skipped entirely when AML flags are present.
    auto_limit = int(settings.auto_approve_withdrawal_under_usd * 100)
    flags = compliance.aml_flags(db, user)
    wd.status = WithdrawalStatus.under_review
    if auto_limit > 0 and amount <= auto_limit and not flags:
        wd.status = WithdrawalStatus.approved
        wd.review_note = "auto-approved (below auto-approval threshold, no AML flags)"
        wd.reviewed_at = utcnow()
        _submit_payout(db, wd, None)

    bonus_svc.notify(
        db,
        user.id,
        "Withdrawal requested",
        f"{amount / 100:.2f} is reserved and pending review. We process payouts within "
        f"24 hours. Reference {wd.id[:12]}.",
    )
    db.flush()
    return wd


def _validate_destination(method: PaymentMethod, destination: str) -> None:
    if len(destination) < 4:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Destination is too short.")
    if method in CRYPTO_METHODS:
        if not all(c.isalnum() for c in destination):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Wallet addresses contain letters and digits only."
            )
        expected = {
            PaymentMethod.crypto_btc: ("1", "3", "bc1"),
            PaymentMethod.crypto_eth: ("0x",),
            PaymentMethod.crypto_usdt: ("T",),
        }[method]
        if not destination.startswith(expected):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"That does not look like a valid {method.value.replace('crypto_', '').upper()} address.",
            )
    elif method is PaymentMethod.bank_transfer:
        if len(destination) < 8:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter a valid IBAN / account number.")
    elif method is PaymentMethod.card:
        digits = destination.replace(" ", "").replace("-", "")
        if not (digits.isdigit() and 12 <= len(digits) <= 19):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter a valid card number.")


def _mask(method: PaymentMethod, destination: str) -> str:
    """Card numbers are never stored beyond the last 4 digits (PCI scope
    reduction). Crypto addresses and IBANs are stored in full because the
    payout rail must submit the exact value - and they belong to the player
    who is looking at them anyway."""
    if method is PaymentMethod.card:
        digits = destination.replace(" ", "").replace("-", "")
        return f"card:****{digits[-4:]}"
    return destination


def display_destination(method: PaymentMethod, destination: str) -> str:
    """Card numbers are stored truncated; wallet/IBAN keep enough to be usable
    by the payout rail. This helper is the single place that decides what the
    admin UI and the player see."""
    return _mask(method, destination) if not destination.startswith("card:") else destination


def review_withdrawal(
    db: Session,
    admin: User,
    withdrawal: Withdrawal,
    *,
    approve: bool,
    note: str | None = None,
    reason: str | None = None,
) -> Withdrawal:
    if withdrawal.status not in (WithdrawalStatus.requested, WithdrawalStatus.under_review):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"withdrawal is {withdrawal.status.value} and can no longer be reviewed",
        )
    before = {"status": withdrawal.status.value}

    if not approve:
        release_withdrawal(
            db, withdrawal.user_id, withdrawal.amount,
            reference=f"withdrawal-reject:{withdrawal.id}",
            memo=f"Withdrawal rejected: {reason or 'no reason given'}",
        )
        withdrawal.status = WithdrawalStatus.rejected
        withdrawal.rejection_reason = reason or "Rejected by compliance"
        withdrawal.reviewed_by = admin.id
        withdrawal.reviewed_at = utcnow()
        bonus_svc.notify(
            db, withdrawal.user_id, "Withdrawal rejected",
            f"{withdrawal.amount / 100:.2f} has been returned to your balance. "
            f"Reason: {withdrawal.rejection_reason}",
        )
    else:
        withdrawal.status = WithdrawalStatus.approved
        withdrawal.reviewed_by = admin.id
        withdrawal.review_note = note
        withdrawal.reviewed_at = utcnow()
        # Submit to the provider immediately - approval means "pay this".
        _submit_payout(db, withdrawal, admin)

    db.add(
        AuditLog(
            actor_id=admin.id,
            actor_email=admin.email,
            action="withdrawal.approve" if approve else "withdrawal.reject",
            target=withdrawal.id,
            before=before,
            after={"status": withdrawal.status.value, "note": note, "reason": reason},
        )
    )
    db.flush()
    return withdrawal


def _submit_payout(db: Session, withdrawal: Withdrawal, admin: User | None) -> None:
    # The rail that was chosen when the payout was requested, not the
    # deployment default: a player who withdrew to a Bhutanese wallet has to be
    # paid out through the Bhutanese rail, whatever else has changed since.
    pathway = (
        db.get(BankingMethod, withdrawal.banking_method_id)
        if withdrawal.banking_method_id
        else None
    )
    provider = get_provider_by_name(routing.effective_provider(pathway))
    try:
        result = provider.create_payout(db, withdrawal)
    except PaymentError as exc:
        withdrawal.status = WithdrawalStatus.approved
        withdrawal.review_note = ((withdrawal.review_note or "") + f" | payout error: {exc}")[:500]
        return

    withdrawal.provider_ref = result.provider_ref
    if result.status == "paid":
        settle_withdrawal(
            db,
            withdrawal.user_id,
            withdrawal.amount,
            withdrawal.fee,
            reference=f"withdrawal-paid:{withdrawal.id}",
            idempotency_key=f"withdrawal-settle:{withdrawal.id}",
        )
        withdrawal.status = WithdrawalStatus.paid
        withdrawal.paid_at = utcnow()
        bonus_svc.notify(
            db, withdrawal.user_id, "Withdrawal paid",
            f"{withdrawal.net_amount / 100:.2f} has been sent. Reference {result.provider_ref}.",
        )
    elif result.status == "failed":
        apply_payout_status(db, withdrawal, "failed", "provider rejected the payout")
    else:
        withdrawal.status = WithdrawalStatus.approved


def apply_payout_status(
    db: Session, withdrawal: Withdrawal, new_status: str, detail: str | None = None
) -> Withdrawal:
    """Provider callback for an approved payout."""
    if new_status == "paid":
        if withdrawal.status is WithdrawalStatus.paid:
            return withdrawal
        if withdrawal.status not in (WithdrawalStatus.approved, WithdrawalStatus.under_review):
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"cannot mark {withdrawal.status.value} as paid"
            )
        settle_withdrawal(
            db,
            withdrawal.user_id,
            withdrawal.amount,
            withdrawal.fee,
            reference=f"withdrawal-paid:{withdrawal.id}",
            idempotency_key=f"withdrawal-settle:{withdrawal.id}",
        )
        withdrawal.status = WithdrawalStatus.paid
        withdrawal.paid_at = utcnow()
        if detail:
            withdrawal.provider_ref = detail
        bonus_svc.notify(
            db, withdrawal.user_id, "Withdrawal paid",
            f"{withdrawal.net_amount / 100:.2f} has been sent. Reference {detail or withdrawal.provider_ref}.",
        )
    elif new_status == "failed":
        if withdrawal.status is WithdrawalStatus.paid:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "cannot fail an already-paid withdrawal"
            )
        release_withdrawal(
            db,
            withdrawal.user_id,
            withdrawal.amount,
            reference=f"withdrawal-fail:{withdrawal.id}",
            memo=f"Payout failed: {detail or 'provider failure'}",
        )
        withdrawal.status = WithdrawalStatus.rejected
        withdrawal.rejection_reason = (detail or "Payout failed at the provider")[:255]
        bonus_svc.notify(
            db, withdrawal.user_id, "Withdrawal could not be sent",
            f"{withdrawal.amount / 100:.2f} has been returned to your balance. "
            f"Reason: {withdrawal.rejection_reason}",
        )
    db.flush()
    return withdrawal


def confirm_payout_paid(
    db: Session, withdrawal: Withdrawal, admin: User, *, reference: str, note: str | None = None
) -> Withdrawal:
    """Mark a payout as sent, for a rail that pays out by hand.

    Not every rail has a payout API, and the ones that do not are usually the
    domestic ones: a bank transfer made from the institution's own console, a
    wallet paid from a phone. The reviewer approves, the operator makes the
    transfer, and this is how the ledger learns that it happened.

    Without it the money is stuck: the withdrawal sits at `approved` and the
    funds stay in `user_locked` forever, because the block on the player's
    balance is only released by a terminal state. An operator has to be able to
    close that loop, and the reference they supply is what reconciles the
    payout against the institution's statement.

    Deliberately requires a reference. "Marked paid" with no evidence is how a
    payout queue becomes unauditable.
    """
    if withdrawal.status is WithdrawalStatus.paid:
        return withdrawal                       # idempotent
    if withdrawal.status not in (WithdrawalStatus.approved, WithdrawalStatus.under_review):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{withdrawal.id} is {withdrawal.status.value}; only an approved "
            f"payout can be confirmed as sent.",
        )
    reference = (reference or "").strip()
    if len(reference) < 3:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Record the bank's transaction reference - it is what reconciles "
            "this payout against the statement.",
        )

    before = withdrawal.status.value
    settle_withdrawal(
        db,
        withdrawal.user_id,
        withdrawal.amount,
        withdrawal.fee,
        reference=f"withdrawal-paid:{withdrawal.id}",
        idempotency_key=f"withdrawal-settle:{withdrawal.id}",
    )
    withdrawal.status = WithdrawalStatus.paid
    withdrawal.provider_ref = reference[:128]
    withdrawal.paid_at = utcnow()
    if note:
        withdrawal.review_note = ((withdrawal.review_note or "") + f" | {note}")[:500]

    db.add(
        AuditLog(
            actor_id=admin.id,
            actor_email=admin.email,
            action="withdrawal.mark_paid",
            target=withdrawal.id,
            before={"status": before},
            after={"status": "paid", "reference": reference},
        )
    )
    bonus_svc.notify(
        db,
        withdrawal.user_id,
        "Withdrawal paid",
        f"{withdrawal.net_amount / 100:.2f} has been sent. Reference {reference}.",
    )
    db.flush()
    log.info(
        "admin %s marked withdrawal %s paid (reference %s)",
        admin.email, withdrawal.id, reference,
    )
    return withdrawal


def cancel_withdrawal(db: Session, user: User, withdrawal: Withdrawal) -> Withdrawal:
    if withdrawal.user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your withdrawal.")
    if withdrawal.status is not WithdrawalStatus.under_review:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This withdrawal is already being processed and can no longer be cancelled.",
        )
    release_withdrawal(
        db,
        user.id,
        withdrawal.amount,
        reference=f"withdrawal-cancel:{withdrawal.id}",
        memo="Withdrawal cancelled by player",
    )
    withdrawal.status = WithdrawalStatus.cancelled
    db.flush()
    return withdrawal


# ---------------------------------------------------------------------------
# webhooks
# ---------------------------------------------------------------------------
def event_fingerprint(raw_body: bytes) -> str:
    """A stable id for a callback that does not carry one.

    Stripe sends `id`, Adyen a `pspReference`, but a provider that sends
    neither would otherwise be processed once per delivery - and PSPs retry.
    Hashing the exact bytes makes "the same callback again" detectable for any
    provider, without trusting a field the caller controls.
    """
    return "sha256:" + hashlib.sha256(raw_body or b"").hexdigest()[:40]


def record_rejected_webhook(
    *,
    provider: str,
    raw_body: bytes,
    error: str,
    signature_valid: bool,
    event: dict | None = None,
) -> None:
    """Persist a refused callback in its own transaction.

    This cannot use the request's session: the request is about to roll back
    (that is what raising does), which would take the audit row with it and
    leave an operator with a signature mismatch, no errors in the log they can
    correlate, and no evidence the provider ever called. One commit of its own.
    """
    from ..db import session_scope

    try:
        with session_scope() as side_db:
            event = event or {}
            side_db.add(
                PaymentWebhook(
                    provider=provider,
                    event_id=event.get("id")
                    or event.get("event_id")
                    or event_fingerprint(raw_body),
                    event_type=str(event.get("type", "unknown")),
                    payload=event or {"unparsed": raw_body[:2000].decode("latin-1")},
                    signature_valid=signature_valid,
                    processed=False,
                    error=error[:2000],
                )
            )
    except Exception:  # pragma: no cover - the audit must never mask the refusal
        log.exception("could not record a rejected %s webhook", provider)


def ingest_webhook(
    db: Session,
    raw_body: bytes,
    signature: str | None,
    provider_name: str | None = None,
) -> dict:
    """Verify and apply one provider callback.

    `provider_name` is the adapter the callback arrived *for*, which is not
    necessarily the deployment default: once pathways exist, an operator can be
    settling through several rails at once, and each one posts to its own URL.
    Falling back to the default keeps single-provider deployments unchanged.
    """
    provider = get_provider_by_name(provider_name) if provider_name else get_provider()
    ok, event = provider.verify_webhook(raw_body, signature)

    # Every provider gets a dedup key, whether it supplied one or not.
    event_id = (
        event.get("id")
        or event.get("event_id")
        or event_fingerprint(raw_body)
    )

    dupe = db.execute(
        select(PaymentWebhook).where(PaymentWebhook.event_id == event_id)
    ).scalar_one_or_none()
    if dupe is not None and dupe.processed:
        # A retry of something already applied. 200, no work, no second credit.
        return {"status": "duplicate", "event_id": event_id}

    if not ok:
        record_rejected_webhook(
            provider=provider.name,
            raw_body=raw_body,
            error="invalid signature",
            signature_valid=False,
        )
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid webhook signature")

    row = PaymentWebhook(
        provider=provider.name,
        event_id=event_id,
        event_type=str(event.get("type", "unknown")),
        payload=event,
        signature_valid=True,
    )
    db.add(row)
    db.flush()

    try:
        result = provider.handle_webhook(db, event)
    except (PaymentError, HTTPException) as exc:
        # The event itself is genuine - we just could not apply it. Record the
        # reason where an operator can replay it, and tell the provider to try
        # again (a non-2xx is a retry).
        db.rollback()
        record_rejected_webhook(
            provider=provider.name,
            raw_body=raw_body,
            error=str(exc),
            signature_valid=True,
            event=event,
        )
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Webhook could not be applied: {exc}",
        ) from exc

    row.processed = True
    db.flush()
    return {"status": "processed", "detail": result, "event_id": event_id}
