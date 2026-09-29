"""Direct bank / wallet rail: the provider for locally negotiated pathways.

Card schemes have a single global API shape. A Bhutanese wallet, a regional
bank, a domestic transfer network - the rails that actually serve most of the
world's players - do not. They are negotiated per market, they settle in the
local currency, and an operator adds and removes them without a release. That
is what this adapter is for, and it is why it reads its configuration from the
``banking_methods`` row rather than from the environment.

Two ways a payment settles, both real:

1. **Push, when the rail has an API.** ``api_endpoint`` is set on the pathway, so
   ``create_deposit`` POSTs an initiation request to it, signed with the key in
   the variable ``credential_env`` names, and returns whatever the rail says
   the player should do next (approve in their app, scan, transfer).
2. **Pull, when it does not.** No endpoint means the player is shown the
   pathway's ``instructions`` - the account to pay, the reference to quote -
   and the deposit is credited when the rail's *signed webhook* arrives, or
   when an operator confirms it in the back office after the statement shows
   the money. Both paths land in ``apply_deposit_status``, so the credit is
   idempotent and the ledger does not care which one happened.

The second is not a stub or a simulator. It is how domestic rails are actually
integrated where no self-serve API exists, and it is the difference between
"we support mBoB" being true and being a marketing claim.

Webhooks are authenticated with HMAC-SHA256 over the raw body, keyed on
``BANK_TRANSFER_WEBHOOK_SECRET``. Nothing is applied before the signature is
checked - see ``payments/README`` reasoning in ``services/payments.ingest_webhook``.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..models import BankingMethod, Deposit, DepositStatus, PaymentMethod, User, Withdrawal
from .base import DepositIntent, PaymentError, PaymentProvider, PayoutResult

log = logging.getLogger("app.payments.bank_transfer")

#: How long an outbound call to a rail may take. A payment page that hangs is
#: worse than one that fails: the player cannot tell whether they paid.
TIMEOUT_SECONDS = 15.0


def webhook_secret() -> str:
    return (settings.bank_transfer_webhook_secret or "").strip()


def payload_for_signature(raw_body: bytes) -> bytes:
    """Exactly what the signature covers. Kept in one place so the docs, the
    verifier and any future sender cannot drift apart."""
    return raw_body


class BankTransferProvider(PaymentProvider):
    """Instruction-and-confirmation rail. Not a simulator.

    Worth stating plainly because the distinction matters at audit time: a
    simulated deposit credits money nobody paid. This one credits money that a
    rail has confirmed, or that an operator has matched against a bank
    statement - and every such credit is on the ledger with the confirming
    reference attached.
    """

    name = "bank_transfer"
    supports_deposit = True
    supports_withdrawal = True
    #: There is no "pretend the customer paid" endpoint for a real rail.
    supports_simulation = False

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _pathway(db: Session, deposit_or_withdrawal) -> BankingMethod | None:
        method_id = getattr(deposit_or_withdrawal, "banking_method_id", None)
        if not method_id:
            return None
        return db.get(BankingMethod, method_id)

    @staticmethod
    def _credential(method: BankingMethod | None) -> str:
        """Read the rail's API key from the environment, by the name the row gives.

        The name is in the database; the value never is. That is what stops a
        key leaking through a database export, a support screenshot or a
        staging copy - all of which happen, and none of which are attacks.
        """
        import os

        if method is None or not method.credential_env:
            return ""
        return (os.environ.get(method.credential_env) or "").strip()

    # -- deposits ----------------------------------------------------------
    def create_deposit(self, db: Session, user: User, deposit: Deposit) -> DepositIntent:
        method = self._pathway(db, deposit)
        if method is None:
            raise PaymentError(
                "This deposit has no banking pathway attached. Pick a payment "
                "method and try again."
            )

        reference = f"DEP-{deposit.id[:10].upper()}"
        deposit.provider_ref = reference

        instructions: dict = {
            **(method.instructions or {}),
            "reference": reference,
            "bank": method.name,
            "currency": deposit.currency,
            "amount": f"{deposit.amount / 100:.2f}",
        }
        if method.account_id:
            instructions.setdefault("account_id", method.account_id)

        endpoint = (method.api_endpoint or "").strip()
        if not endpoint:
            # Pull mode: the player is told exactly what to do, and the rail's
            # webhook (or an operator) confirms it. Nothing is credited yet.
            instructions.setdefault(
                "note",
                "Complete the transfer, then wait for confirmation. "
                "Your balance updates automatically once it clears.",
            )
            return DepositIntent(
                provider=self.name,
                provider_ref=reference,
                status=DepositStatus.requires_action.value,
                instructions=instructions,
            )

        # Push mode: ask the rail to start the payment.
        body = {
            "merchant_account": method.account_id or settings.merchant_account_id,
            "reference": reference,
            "amount_minor": deposit.amount,
            "currency": deposit.currency,
            "deposit_id": deposit.id,
            "player_reference": user.id,
        }
        reply = self._post(endpoint, body, self._credential(method), reference)
        instructions.update(reply.get("instructions") or {})

        return DepositIntent(
            provider=self.name,
            provider_ref=reply.get("provider_ref") or reference,
            status=reply.get("status") or DepositStatus.requires_action.value,
            redirect_url=reply.get("redirect_url"),
            instructions=instructions,
        )

    def _post(self, endpoint: str, body: dict, key: str, reference: str) -> dict:
        raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
        headers = {"Content-Type": "application/json", "X-Reference": reference}
        if key:
            headers["X-Signature"] = hmac.new(
                key.encode(), raw, hashlib.sha256
            ).hexdigest()
        try:
            response = httpx.post(
                endpoint, content=raw, headers=headers, timeout=TIMEOUT_SECONDS
            )
        except httpx.HTTPError as exc:
            # The deposit stays `pending`; nothing was started, so nothing is
            # owed. Raising here is what tells the player to try again.
            raise PaymentError(f"{endpoint} could not be reached: {exc}") from exc

        if response.status_code >= 400:
            raise PaymentError(
                f"{endpoint} rejected the payment initiation "
                f"({response.status_code}): {response.text[:200]}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise PaymentError(f"{endpoint} returned a non-JSON response") from exc

    # -- payouts -----------------------------------------------------------
    def create_payout(self, db: Session, withdrawal: Withdrawal) -> PayoutResult:
        """Submit an approved payout.

        Where the rail has an API this instructs it to pay out and reports back
        what it said. Where it does not, the withdrawal is left ``approved``:
        the reviewer has approved it, the funds are held, and an operator makes
        the transfer and marks it paid. The hold means the money cannot be
        spent twice while that happens.
        """
        method = self._pathway(db, withdrawal)
        reference = f"WD-{withdrawal.id[:10].upper()}"
        withdrawal.provider_ref = reference

        endpoint = (method.api_endpoint or "").strip() if method else ""
        if not endpoint:
            return PayoutResult(
                provider=self.name,
                provider_ref=reference,
                status="approved",
                detail={
                    "instruction": (
                        "Pay out manually from the rail's own console, then mark "
                        "this withdrawal paid. Funds are already held."
                    ),
                    "destination": withdrawal.destination,
                    "bank": method.name if method else None,
                },
            )

        body = {
            "merchant_account": (method.account_id if method else "")
            or settings.merchant_account_id,
            "reference": reference,
            "amount_minor": withdrawal.net_amount,
            # Withdrawals settle in the pathway's currency, falling back to the
            # deployment default. There is no per-withdrawal currency column
            # because payouts are always made to the rail the player was paid
            # in, not to a currency they choose at request time.
            "currency": (method.currency if method and method.currency != "*" else None)
            or settings.default_currency,
            "destination": withdrawal.payout_ref or withdrawal.destination,
            "withdrawal_id": withdrawal.id,
        }
        reply = self._post(
            endpoint, body, self._credential(method), reference
        )
        return PayoutResult(
            provider=self.name,
            provider_ref=reply.get("provider_ref") or reference,
            status=reply.get("status") or "approved",
            detail=reply,
        )

    # -- webhooks ----------------------------------------------------------
    def verify_webhook(self, raw_body: bytes, signature: str | None) -> tuple[bool, dict]:
        """Constant-time HMAC check, then JSON parse. Never the other way round."""
        secret = webhook_secret()
        if not secret:
            # Refuse rather than accept: a deployment with no signing secret
            # configured must not treat unsigned callbacks as genuine. The
            # alternative credits any stranger who can reach the URL.
            log.error(
                "bank_transfer webhook received but BANK_TRANSFER_WEBHOOK_SECRET "
                "is not set - refusing it"
            )
            return False, {}
        if not signature:
            return False, {}

        expected = hmac.new(secret.encode(), payload_for_signature(raw_body), hashlib.sha256)
        provided = signature.strip()
        # Accept hex or base64, with or without a `sha256=` prefix: rails in the
        # wild differ, and a signature that is correct but formatted differently
        # should not read as a forgery.
        if provided.lower().startswith("sha256="):
            provided = provided.split("=", 1)[1]
        import base64

        candidates = {expected.hexdigest()}
        candidates.add(base64.b64encode(expected.digest()).decode())
        if not any(hmac.compare_digest(provided, c) for c in candidates):
            return False, {}

        try:
            event = json.loads(raw_body)
        except ValueError:
            return False, {}
        if not isinstance(event, dict):
            return False, {}
        return True, event

    def handle_webhook(self, db: Session, event: dict) -> str:
        """Apply a verified rail event. Delegated to the payments service so the
        credit path is the same one every other provider uses."""
        from ..services import payments as pay_svc

        kind = str(event.get("type") or "").strip().lower()
        mapping = {
            "deposit.succeeded": DepositStatus.succeeded,
            "deposit.paid": DepositStatus.succeeded,
            "deposit.failed": DepositStatus.failed,
            "deposit.cancelled": DepositStatus.cancelled,
        }
        if kind in mapping:
            deposit = self._find_deposit(db, event)
            new_status = mapping[kind]

            if new_status is DepositStatus.succeeded:
                self._assert_amount_matches(deposit, event)

            if event.get("provider_ref"):
                deposit.provider_ref = str(event["provider_ref"])[:128]

            pay_svc.apply_deposit_status(db, deposit, new_status)
            return f"{deposit.id}:{new_status.value}"

        if kind in {"payout.paid", "payout.failed"}:
            withdrawal = self._find_withdrawal(db, event)
            reference = str(event.get("provider_ref") or "") or None
            pay_svc.apply_payout_status(
                db,
                withdrawal,
                "paid" if kind == "payout.paid" else "failed",
                detail=reference or f"{self.name}:{kind}",
            )
            return f"{withdrawal.id}:{kind}"

        raise PaymentError(f"unsupported bank_transfer event type {kind!r}")

    @staticmethod
    def _assert_amount_matches(deposit: Deposit, event: dict) -> None:
        """Refuse to credit a deposit whose confirmed amount disagrees.

        A signed webhook is authentic, but authentic is not the same as
        correct. Rails get replayed, references get reused by a clerk copying
        the wrong line off a statement, and an operator changes the amount on
        one side and not the other. Crediting whatever the callback says would
        turn any of those into money the player never paid - or into a balance
        the operator cannot reconcile.

        Raising here surfaces as a 422, so the event is stored with its error,
        an operator can see it, and the rail retries. Nothing is credited.
        """
        reported = event.get("amount_minor")
        if reported is None:
            return
        try:
            reported_int = int(reported)
        except (TypeError, ValueError):
            raise PaymentError(f"amount_minor {reported!r} is not a number")
        if reported_int != deposit.amount:
            raise PaymentError(
                f"amount mismatch: the rail confirmed {reported_int} but deposit "
                f"{deposit.id} is for {deposit.amount}. Refusing to credit."
            )

    @staticmethod
    def _find_deposit(db: Session, event: dict) -> Deposit:
        from sqlalchemy import select

        deposit_id = event.get("deposit_id")
        reference = event.get("reference") or event.get("provider_ref")
        query = select(Deposit)
        row = None
        if deposit_id:
            row = db.execute(query.where(Deposit.id == deposit_id)).scalar_one_or_none()
        if row is None and reference:
            row = db.execute(
                query.where(Deposit.provider_ref == reference)
            ).scalar_one_or_none()
        if row is None:
            raise PaymentError(
                "no deposit matches this event - refusing to credit an "
                "unmatched reference"
            )
        return row

    @staticmethod
    def _find_withdrawal(db: Session, event: dict) -> Withdrawal:
        from sqlalchemy import select

        withdrawal_id = event.get("withdrawal_id")
        reference = event.get("reference") or event.get("provider_ref")
        query = select(Withdrawal)
        row = None
        if withdrawal_id:
            row = db.execute(
                query.where(Withdrawal.id == withdrawal_id)
            ).scalar_one_or_none()
        if row is None and reference:
            row = db.execute(
                query.where(Withdrawal.provider_ref == reference)
            ).scalar_one_or_none()
        if row is None:
            raise PaymentError("no withdrawal matches this event")
        return row

    def health(self) -> dict:
        return {
            "provider": self.name,
            "ok": True,
            "simulation": False,
            "webhook_secret_configured": bool(webhook_secret()),
            "note": "Local bank/wallet rails configured per pathway in the admin panel.",
        }


def new_reference() -> str:
    """Reference for rails that need one before an id exists."""
    return "DEP-" + secrets.token_hex(5).upper()


#: Registered under the name an admin types in the pathway form.
bank_transfer_provider = BankTransferProvider()
