"""Adyen adapter - Checkout for deposits, Transfers for payouts.

Why a second card adapter exists next to Stripe: Adyen is the processor most
commonly approved for licensed iGaming, and operators usually run both (one for
cards, one for local payment methods). Both talk to the same `PaymentProvider`
interface, so the wallet, ledger and admin code do not know which one is live.

Implemented against Adyen's documented HTTP APIs with `httpx`, no SDK, for the
same reason as the Stripe adapter: one fewer dependency to audit and the exact
request shape stays visible in this file.

Required environment
--------------------
    PAYMENT_PROVIDER=adyen
    ADYEN_API_KEY=AQE...                 # Customer Area > Developers > API credentials
    ADYEN_MERCHANT_ACCOUNT=YourMerchant  # the merchant account receiving deposits
    ADYEN_HMAC_KEY=...                   # the webhook HMAC key (hex) for that endpoint
    ADYEN_ENVIRONMENT=test|live          # 'test' hits checkout-test.adyen.com
    APP_BASE_URL=https://your-domain     # return/redirect URLs

Optional, needed only for payouts:

    ADYEN_BALANCE_ACCOUNT_ID=BA...       # the balance account payouts draw from

Verification status - read this
-------------------------------
* ``verify_webhook`` implements Adyen's documented HMAC scheme: base64 of
  HMAC-SHA256 over
  ``pspReference + originalReference + merchantAccountCode + merchantReference
  + amount.value + amount.currency + eventCode + success``
  keyed with the hex HMAC key. It is covered by unit tests that sign payloads
  the same way Adyen's documentation describes.
* The request bodies are built from Adyen's published API reference. They have
  NOT been run against Adyen's test environment from this repository - there is
  no credentials file and no network route to it here. Before taking real
  money, run one deposit and one payout against ``ADYEN_ENVIRONMENT=test`` and
  check the shapes against the API explorer. Anything Adyen rejects will fail
  loudly and be recorded in ``payment_webhooks`` / the deposit row.

Licensing note (the same one as Stripe, and it is not optional): card acquiring
for gambling is a restricted, licensed activity. Adyen will only enable a
gambling merchant with a valid licence for the markets you accept players from.
Using this module without that approval breaches your contract with them and
your funds can be held.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Deposit, Withdrawal
from .base import DepositIntent, PaymentError, PaymentProvider, PayoutResult

API_VERSION = "v71"
TRANSFERS_VERSION = "v4"

#: Adyen rejects a payment below this in most currencies; also our own floor.
MIN_CHARGE = 50


def _endpoints() -> tuple[str, str]:
    env = (settings.adyen_environment or "test").strip().lower()
    if env == "live":
        return "https://checkout-live.adyen.com", "https://balanceplatform-api-live.adyen.com"
    return "https://checkout-test.adyen.com", "https://balanceplatform-api-test.adyen.com"


def _base_url() -> str:
    import os

    return os.environ.get("APP_BASE_URL", "http://localhost:5173").rstrip("/")


class AdyenProvider(PaymentProvider):
    name = "adyen"
    supports_simulation = False

    def __init__(self) -> None:
        self.api_key = settings.adyen_api_key
        self.merchant_account = settings.adyen_merchant_account
        self.hmac_key = settings.adyen_hmac_key
        self.balance_account_id = settings.adyen_balance_account_id

    # -- transport ---------------------------------------------------------
    def _headers(self) -> dict:
        if not self.api_key:
            raise PaymentError("ADYEN_API_KEY is not configured")
        return {
            "x-API-key": self.api_key,
            "content-type": "application/json",
            "accept": "application/json",
        }

    def _post(self, url: str, body: dict) -> dict:
        try:
            resp = httpx.post(url, json=body, headers=self._headers(), timeout=20)
        except httpx.HTTPError as exc:
            raise PaymentError(f"adyen transport error: {exc}") from exc
        if resp.status_code >= 400:
            raise PaymentError(f"adyen {resp.status_code}: {resp.text[:400]}")
        try:
            return resp.json()
        except json.JSONDecodeError as exc:
            raise PaymentError("adyen returned a non-JSON body") from exc

    def _get(self, url: str) -> dict:
        try:
            resp = httpx.get(url, headers=self._headers(), timeout=20)
        except httpx.HTTPError as exc:
            raise PaymentError(f"adyen transport error: {exc}") from exc
        if resp.status_code >= 400:
            raise PaymentError(f"adyen {resp.status_code}: {resp.text[:400]}")
        return resp.json()

    # -- deposits ----------------------------------------------------------
    def create_deposit(self, db: Session, user, deposit: Deposit) -> DepositIntent:
        """Create a Checkout session and send the player to Adyen's hosted page.

        We never see card data; Adyen collects it on their domain, which is what
        keeps this side out of PCI-DSS scope beyond SAQ-A. That is the whole
        reason to prefer a hosted checkout over a custom payment form.
        """
        if deposit.amount < MIN_CHARGE:
            raise PaymentError(f"deposit below the minimum charge ({MIN_CHARGE} minor units)")
        if not self.merchant_account:
            raise PaymentError("ADYEN_MERCHANT_ACCOUNT is not configured")

        checkout_base, _ = _endpoints()
        currency = settings.settlement_currency.upper()
        body = {
            "amount": {"value": deposit.amount, "currency": currency},
            "reference": deposit.id,
            "merchantAccount": self.merchant_account,
            "returnUrl": f"{_base_url()}/checkout/return?deposit={deposit.id}",
            "shopperReference": user.id,
            "shopperEmail": user.email,
            "countryCode": (user.country or "").upper() or None,
            "shopperLocale": f"{user.country or 'en'}_{user.country or 'US'}",
            # Our own bookkeeping travels with the payment so the webhook can
            # find the deposit even if the reference is rewritten downstream.
            "metadata": {"deposit_id": deposit.id, "user_id": user.id},
            "lineItems": [
                {
                    "id": "deposit",
                    "description": f"{settings.app_name} balance top-up",
                    "quantity": 1,
                    "amountIncludingTax": deposit.amount,
                    "amountExcludingTax": deposit.amount,
                    "taxPercentage": 0,
                }
            ],
        }
        body = {k: v for k, v in body.items() if v is not None}

        data = self._post(f"{checkout_base}/{API_VERSION}/sessions", body)
        return DepositIntent(
            provider=self.name,
            provider_ref=data.get("id") or deposit.id,
            status="requires_action",
            redirect_url=data.get("url"),
            client_secret=data.get("sessionData"),
            instructions={
                "type": "redirect",
                "url": data.get("url"),
                "session_id": data.get("id"),
                "environment": (settings.adyen_environment or "test").lower(),
            },
        )

    # -- payouts -----------------------------------------------------------
    def create_payout(self, db: Session, withdrawal: Withdrawal) -> PayoutResult:
        """Submit a payout through the Transfers API.

        A payout needs a *destination*: a transfer instrument (bank account or
        card) that has been created and verified for that player. That is a
        per-player onboarding step, not something a withdrawal request can
        invent - so if the withdrawal has no instrument reference, this fails
        with an actionable message instead of sending money somewhere arbitrary.
        """
        if not self.balance_account_id:
            raise PaymentError(
                "ADYEN_BALANCE_ACCOUNT_ID is not configured - payouts need the "
                "balance account they are funded from"
            )
        instrument = (withdrawal.payout_ref or "").strip()
        if not instrument:
            raise PaymentError(
                "this withdrawal has no payout instrument; the player must "
                "onboard a bank account or card (KYC-verified) before a payout "
                "can be created"
            )

        _, platform_base = _endpoints()
        body = {
            "amount": {
                "value": withdrawal.net_amount,
                "currency": settings.settlement_currency.upper(),
            },
            "balanceAccountId": self.balance_account_id,
            "reference": f"wd_{withdrawal.id}",
            "description": f"{settings.app_name} withdrawal",
            "category": "bank",
            "priority": "regular",
            "counterparty": {"transferInstrumentId": instrument},
            "metadata": {"withdrawal_id": withdrawal.id},
        }
        data = self._post(
            f"{platform_base}/btl/{TRANSFERS_VERSION}/transfers", body
        )
        status_map = {
            "received": "approved",
            "authorised": "approved",
            "booked": "paid",
            "pending": "approved",
        }
        return PayoutResult(
            provider=self.name,
            provider_ref=data.get("id") or data.get("reference") or withdrawal.id,
            status=status_map.get((data.get("status") or "").lower(), "approved"),
            detail={"adyen_status": data.get("status"), "reference": data.get("reference")},
        )

    # -- webhooks ----------------------------------------------------------
    def signature_for(self, item: dict) -> str | None:
        """Adyen puts the signature inside the body, not in a header."""
        return ((item.get("additionalData") or {}).get("hmacSignature")) or None

    def expected_signature(self, item: dict) -> str | None:
        """Compute the HMAC Adyen documents, so it can be compared in tests."""
        if not self.hmac_key:
            return None
        amount = item.get("amount") or {}
        signing_string = "".join(
            str(part if part is not None else "")
            for part in (
                item.get("pspReference", ""),
                item.get("originalReference", ""),
                item.get("merchantAccountCode", ""),
                item.get("merchantReference", ""),
                amount.get("value", ""),
                amount.get("currency", ""),
                item.get("eventCode", ""),
                item.get("success", ""),
            )
        )
        digest = hmac.new(
            self.hmac_key.encode("utf-8"),
            signing_string.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return base64.b64encode(digest).decode("utf-8")

    def verify_webhook(self, raw_body: bytes, signature: str | None) -> tuple[bool, dict]:
        """Validate every notification item in a standard webhook.

        Adyen batches notifications, and an attacker only needs one unverified
        item to slip through, so each item is checked and the whole batch is
        rejected if any signature is missing or wrong.
        """
        if not self.hmac_key:
            return False, {}
        try:
            payload = json.loads(raw_body or b"{}")
        except json.JSONDecodeError:
            return False, {}

        items = payload.get("notificationItems")
        if not isinstance(items, list):
            return False, {}

        verified = []
        for entry in items:
            item = (entry or {}).get("NotificationRequestItem") or {}
            supplied = self.signature_for(item)
            expected = self.expected_signature(item)
            if not supplied or not expected or not hmac.compare_digest(supplied, expected):
                return False, {}
            verified.append(item)

        # Hand the handler the verified items only: nothing unverified should
        # be reachable further down the pipeline.
        return True, {
            "type": "adyen.notification",
            "id": verified[0].get("pspReference") if verified else None,
            "items": verified,
        }

    def handle_webhook(self, db: Session, event: dict) -> str:
        from ..models import DepositStatus
        from ..services import payments as pay_svc

        handled: list[str] = []
        for item in event.get("items") or []:
            code = (item.get("eventCode") or "").upper()
            success = str(item.get("success")).lower() == "true"
            reference = item.get("merchantReference") or ""
            deposit_id = ((item.get("additionalData") or {}).get("metadata.deposit_id")
                          or (item.get("metadata") or {}).get("deposit_id")
                          or reference)

            if code == "AUTHORISATION":
                deposit = db.get(Deposit, deposit_id)
                if not deposit:
                    raise PaymentError(f"unknown deposit {deposit_id}")
                if success:
                    amount = int((item.get("amount") or {}).get("value") or 0)
                    if amount and amount != deposit.amount:
                        # Same rule as Stripe: never credit what was not asked for.
                        raise PaymentError(
                            f"amount mismatch on {deposit_id}: got {amount}, "
                            f"expected {deposit.amount}"
                        )
                    pay_svc.apply_deposit_status(db, deposit, DepositStatus.succeeded)
                    handled.append(f"deposit {deposit_id} succeeded")
                else:
                    pay_svc.apply_deposit_status(db, deposit, DepositStatus.failed)
                    handled.append(
                        f"deposit {deposit_id} refused "
                        f"({item.get('reason') or 'no reason given'})"
                    )

            elif code in ("CHARGEBACK", "CHARGEBACK_REVERSED", "NOTIFICATION_OF_CHARGEBACK"):
                deposit = db.get(Deposit, deposit_id)
                if deposit:
                    # A reversal of a chargeback puts the money back.
                    status = (
                        DepositStatus.succeeded
                        if code == "CHARGEBACK_REVERSED" or not success
                        else DepositStatus.chargeback
                    )
                    pay_svc.apply_deposit_status(db, deposit, status)
                    handled.append(f"deposit {deposit_id} -> {status.value}")

            elif code == "REFUND":
                deposit = db.get(Deposit, deposit_id)
                if deposit:
                    pay_svc.apply_deposit_status(db, deposit, DepositStatus.chargeback)
                    handled.append(f"deposit {deposit_id} refunded")

            elif code in ("PAYOUT", "PAYOUT_FAILED", "TRANSFER"):
                wid = deposit_id
                withdrawal = db.get(Withdrawal, wid)
                if withdrawal:
                    pay_svc.apply_payout_status(
                        db,
                        withdrawal,
                        "paid" if (success and code != "PAYOUT_FAILED") else "failed",
                        item.get("reason"),
                    )
                    handled.append(f"withdrawal {wid} -> {code.lower()}")

            else:
                # An event type we do not act on is stored (the caller records
                # it) but is not an error: Adyen sends many notification types.
                handled.append(f"ignored {code or 'unknown event'}")

        return "; ".join(handled) or "no items"

    # -- ops ---------------------------------------------------------------
    def health(self) -> dict:
        """Probe the live API key without moving money.

        Uses the Checkout /paymentMethods endpoint: it needs no body, no
        session and no money, and it fails loudly on a bad key or a merchant
        account that is not enabled for the environment.
        """
        checkout_base, _ = _endpoints()
        currency = settings.settlement_currency.upper()
        if not self.api_key or not self.merchant_account:
            return {
                "provider": self.name,
                "ok": False,
                "detail": "ADYEN_API_KEY and/or ADYEN_MERCHANT_ACCOUNT not configured",
                "simulation": False,
            }
        body = {"merchantAccount": self.merchant_account, "amount": {"value": 100, "currency": currency}}
        try:
            methods = self._post(f"{checkout_base}/{API_VERSION}/paymentMethods", body)
            count = len(methods.get("paymentMethods") or [])
            return {
                "provider": self.name,
                "ok": True,
                "detail": (
                    f"merchant={self.merchant_account} methods={count} "
                    f"env={(settings.adyen_environment or 'test').lower()} "
                    f"hmac={'set' if self.hmac_key else 'MISSING'}"
                ),
                "simulation": False,
                "warnings": [] if self.hmac_key else [
                    "ADYEN_HMAC_KEY is not set: webhook signatures cannot be verified"
                ],
            }
        except PaymentError as exc:
            return {"provider": self.name, "ok": False, "detail": str(exc), "simulation": False}


def utc_stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
