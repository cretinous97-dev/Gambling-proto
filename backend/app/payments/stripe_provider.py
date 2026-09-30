"""Stripe adapter - REAL money, once you supply your own merchant credentials.

Deliberately implemented against the HTTP API with `httpx` rather than the
official SDK so there is one fewer dependency to audit, and so the exact
request/response shape is visible in this file.

⚠️  READ BEFORE ENABLING
----------------------------------------------------------------------------
Card acquiring for gambling is a restricted, licensed activity:

  * Stripe will only approve a gambling merchant with a valid licence in the
    jurisdiction the customers are in. Using this module without that approval
    breaches Stripe's Services Agreement and your funds can be frozen.
  * Card payouts (`/v1/payouts`) require a connected account with a payout
    schedule; a platform cannot simply push money to a player's card.
  * Chargebacks are the operator's liability. `deposit.chargeback` handling is
    implemented, but the reserve you carry is a commercial decision, not a
    code one.

Required environment:
    PAYMENT_PROVIDER=stripe
    STRIPE_SECRET_KEY=sk_live_...        (or sk_test_... for sandbox testing)
    STRIPE_WEBHOOK_SECRET=whsec_...
    APP_BASE_URL=https://your-domain    (used for return/refresh URLs)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Deposit, PaymentMethod, Withdrawal
from .base import DepositIntent, PaymentError, PaymentProvider, PayoutResult

API = "https://api.stripe.com/v1"
# Stripe's minimum charge in USD cents.
MIN_CHARGE = 50
SIGNATURE_TOLERANCE_S = 300


class StripeProvider(PaymentProvider):
    name = "stripe"
    supports_simulation = False

    def __init__(self) -> None:
        self.secret = settings.stripe_secret_key
        self.webhook_secret = settings.stripe_webhook_secret

    # -- helpers -----------------------------------------------------------
    def _post(self, path: str, data: dict) -> dict:
        if not self.secret:
            raise PaymentError("STRIPE_SECRET_KEY is not configured")
        try:
            resp = httpx.post(
                f"{API}/{path}",
                data=data,
                auth=(self.secret, ""),
                timeout=20.0,
            )
        except httpx.HTTPError as exc:
            raise PaymentError(f"stripe transport error: {exc}") from exc
        if resp.status_code >= 400:
            raise PaymentError(f"stripe {path} failed ({resp.status_code}): {resp.text[:400]}")
        return resp.json()

    def _get(self, path: str) -> dict:
        if not self.secret:
            raise PaymentError("STRIPE_SECRET_KEY is not configured")
        resp = httpx.get(f"{API}/{path}", auth=(self.secret, ""), timeout=20.0)
        if resp.status_code >= 400:
            raise PaymentError(f"stripe {path} failed ({resp.status_code}): {resp.text[:400]}")
        return resp.json()

    # -- deposits ----------------------------------------------------------
    def create_deposit(self, db: Session, user: User, deposit: Deposit) -> DepositIntent:
        if deposit.method is not PaymentMethod.card:
            raise PaymentError("the stripe adapter only handles card deposits")
        if deposit.amount < MIN_CHARGE:
            raise PaymentError(f"stripe requires a minimum charge of {MIN_CHARGE} cents")

        session = self._post(
            "checkout/sessions",
            {
                "mode": "payment",
                "success_url": f"{settings.app_name and _base()}/wallet?deposit={deposit.id}&status=success",
                "cancel_url": f"{_base()}/wallet?deposit={deposit.id}&status=cancelled",
                "client_reference_id": deposit.id,
                "customer_email": user.email,
                "payment_method_types[0]": "card",
                "line_items[0][quantity]": 1,
                "line_items[0][price_data][currency]": "usd",
                "line_items[0][price_data][unit_amount]": deposit.amount,
                "line_items[0][price_data][product_data][name]": f"{settings.app_name} deposit",
                "metadata[deposit_id]": deposit.id,
                "metadata[user_id]": user.id,
            },
        )
        deposit.provider_ref = session["id"]
        return DepositIntent(
            provider=self.name,
            provider_ref=session["id"],
            status="requires_action",
            redirect_url=session.get("url"),
            instructions={"checkout_session": session["id"]},
        )

    # -- payouts -----------------------------------------------------------
    def create_payout(self, db: Session, withdrawal: Withdrawal) -> PayoutResult:
        """Pushes an approved withdrawal.

        NOTE: for most gambling merchants payouts are wired through the PSP's
        payouts API against a connected account, or through a separate payout
        rail entirely. This maps to Stripe Payins/Payouts with the destination
        token your account is configured for; `destination` holds the token.
        """
        payload = {
            "amount": withdrawal.net_amount,
            "currency": "usd",
            "metadata[withdrawal_id]": withdrawal.id,
            "metadata[user_id]": withdrawal.user_id,
        }
        if withdrawal.destination.startswith("acct_"):
            payload["destination"] = withdrawal.destination
        payout = self._post("payouts", payload)
        return PayoutResult(
            provider=self.name,
            provider_ref=payout["id"],
            status={"paid": "paid", "pending": "approved", "in_transit": "approved"}.get(
                payout.get("status", ""), "approved"
            ),
            detail={"stripe_status": payout.get("status")},
        )

    # -- webhooks ----------------------------------------------------------
    def verify_webhook(self, raw_body: bytes, signature: str | None) -> tuple[bool, dict]:
        """Verify the `Stripe-Signature` header exactly as documented:
        t=<ts>,v1=<hmac(ts + "." + body)>  with a replay window."""
        if not self.webhook_secret or not signature:
            return False, {}
        parts = dict(
            p.split("=", 1) for p in signature.split(",") if "=" in p
        )
        ts, v1 = parts.get("t"), parts.get("v1")
        if not ts or not v1:
            return False, {}
        try:
            if abs(time.time() - int(ts)) > SIGNATURE_TOLERANCE_S:
                return False, {}
        except ValueError:
            return False, {}
        expected = hmac.new(
            self.webhook_secret.encode(), f"{ts}.".encode() + raw_body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, v1):
            return False, {}
        try:
            return True, json.loads(raw_body or b"{}")
        except json.JSONDecodeError:
            return False, {}

    def handle_webhook(self, db: Session, event: dict) -> str:
        from ..models import DepositStatus
        from ..services import payments as pay_svc

        kind = event.get("type", "")
        obj = (event.get("data") or {}).get("object") or {}
        deposit_id = (obj.get("metadata") or {}).get("deposit_id") or obj.get("client_reference_id")

        if kind == "checkout.session.completed" and deposit_id:
            amount = int(obj.get("amount_total") or 0)
            deposit = db.get(Deposit, deposit_id)
            if not deposit:
                raise PaymentError(f"unknown deposit {deposit_id}")
            if amount and amount != deposit.amount:
                # Never credit an amount the player did not ask for.
                raise PaymentError(
                    f"amount mismatch on {deposit_id}: got {amount}, expected {deposit.amount}"
                )
            pay_svc.apply_deposit_status(db, deposit, DepositStatus.succeeded)
            return f"deposit {deposit_id} succeeded"

        if kind in ("checkout.session.expired", "payment_intent.payment_failed") and deposit_id:
            deposit = db.get(Deposit, deposit_id)
            if deposit:
                pay_svc.apply_deposit_status(db, deposit, DepositStatus.failed)
                return f"deposit {deposit_id} failed"

        if kind in ("charge.dispute.created", "charge.dispute.funds_withdrawn") and deposit_id:
            deposit = db.get(Deposit, deposit_id)
            if deposit:
                pay_svc.apply_deposit_status(db, deposit, DepositStatus.chargeback)
                return f"deposit {deposit_id} charged back"

        if kind in ("payout.paid", "payout.failed"):
            wid = (obj.get("metadata") or {}).get("withdrawal_id")
            if wid:
                wd = db.get(Withdrawal, wid)
                if wd:
                    pay_svc.apply_payout_status(
                        db, wd, "paid" if kind == "payout.paid" else "failed",
                        obj.get("failure_message"),
                    )
                    return f"withdrawal {wid} {kind.split('.')[-1]}"

        raise PaymentError(f"unhandled stripe event {kind!r}")

    def health(self) -> dict:
        ok = False
        detail = "not configured"
        if self.secret:
            try:
                acct = self._get("account")
                ok, detail = True, f"account={acct.get('id')} country={acct.get('country')}"
            except PaymentError as exc:
                detail = str(exc)
        return {"provider": self.name, "ok": ok, "detail": detail, "simulation": False}


def _base() -> str:
    import os

    return os.environ.get("APP_BASE_URL", "http://localhost:5173").rstrip("/")
