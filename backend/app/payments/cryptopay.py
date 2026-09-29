"""Crypto adapter (BTC / ETH / USDT) - on-chain deposits, queued payouts.

⚠️  READ BEFORE ENABLING
----------------------------------------------------------------------------
  * Custodying player funds is a regulated activity in most jurisdictions, and
    accepting crypto does NOT remove AML/KYC obligations - it increases them.
  * Real deployments must use a hot/cold wallet split with a KMS/HSM-held key.
    This module NEVER touches private keys: it calls a wallet service you
    operate (or a processor such as a licensed crypto PSP) over HTTP.
  * A deposit is only credited after `CONFIRMATIONS_REQUIRED` confirmations, so
    a chain re-org cannot leave you holding an IOU you cannot reverse.

Required environment:
    PAYMENT_PROVIDER=cryptopay
    CRYPTOPAY_API_KEY=...
    CRYPTOPAY_WEBHOOK_SECRET=...
    CRYPTOPAY_API_BASE=https://your-wallet-service
"""
from __future__ import annotations

import hashlib
import hmac
import os

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Deposit, PaymentMethod, User, Withdrawal
from .base import DepositIntent, PaymentError, PaymentProvider, PayoutResult

CONFIRMATIONS_REQUIRED = 2
NETWORKS = {
    PaymentMethod.crypto_btc: ("bitcoin", "BTC"),
    PaymentMethod.crypto_eth: ("ethereum", "ETH"),
    PaymentMethod.crypto_usdt: ("tron", "USDT"),
}


class CryptoPayProvider(PaymentProvider):
    name = "cryptopay"
    supports_simulation = False

    def __init__(self) -> None:
        self.api_key = settings.cryptopay_api_key
        self.webhook_secret = settings.cryptopay_webhook_secret
        self.base = os.environ.get("CRYPTOPAY_API_BASE", "").rstrip("/")

    def _post(self, path: str, payload: dict) -> dict:
        if not self.api_key or not self.base:
            raise PaymentError("CRYPTOPAY_API_KEY / CRYPTOPAY_API_BASE are not configured")
        try:
            resp = httpx.post(
                f"{self.base}{path}",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=20.0,
            )
        except httpx.HTTPError as exc:
            raise PaymentError(f"wallet service unreachable: {exc}") from exc
        if resp.status_code >= 400:
            raise PaymentError(f"wallet service {path} failed: {resp.text[:300]}")
        return resp.json()

    # -- deposits ----------------------------------------------------------
    def create_deposit(self, db: Session, user: User, deposit: Deposit) -> DepositIntent:
        network, asset = NETWORKS.get(deposit.method, (None, None))
        if not network:
            raise PaymentError("unsupported crypto method")
        ref = f"cp_{deposit.id[:20]}"
        invoice = self._post(
            "/invoices",
            {
                "reference": ref,
                "network": network,
                "asset": asset,
                "amount_usd": deposit.amount / 100,
                "confirmations_required": CONFIRMATIONS_REQUIRED,
                "metadata": {"deposit_id": deposit.id, "user_id": user.id},
            },
        )
        deposit.provider_ref = invoice.get("id", ref)
        deposit.crypto_address = invoice["address"]
        return DepositIntent(
            provider=self.name,
            provider_ref=deposit.provider_ref,
            status="requires_action",
            instructions={
                "address": invoice["address"],
                "network": network,
                "asset": asset,
                "amount_usd": f"{deposit.amount / 100:.2f}",
                "amount_crypto": invoice.get("amount_crypto"),
                "confirmations_required": CONFIRMATIONS_REQUIRED,
                "expires_at": invoice.get("expires_at"),
            },
        )

    # -- payouts -----------------------------------------------------------
    def create_payout(self, db: Session, withdrawal: Withdrawal) -> PayoutResult:
        network, asset = NETWORKS.get(withdrawal.method, (None, None))
        if not network:
            raise PaymentError("unsupported crypto withdrawal method")
        out = self._post(
            "/payouts",
            {
                "network": network,
                "asset": asset,
                "address": withdrawal.destination,
                "amount_usd": withdrawal.net_amount / 100,
                "fee_policy": "subtract",
                "metadata": {"withdrawal_id": withdrawal.id, "user_id": withdrawal.user_id},
            },
        )
        status = {"broadcast": "paid", "queued": "approved", "failed": "failed"}.get(
            out.get("status", ""), "approved"
        )
        return PayoutResult(
            provider=self.name,
            provider_ref=out.get("txid") or out.get("id", ""),
            status=status,
            detail=out,
        )

    # -- webhooks ----------------------------------------------------------
    def verify_webhook(self, raw_body: bytes, signature: str | None) -> tuple[bool, dict]:
        if not self.webhook_secret or not signature:
            return False, {}
        expected = hmac.new(self.webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return False, {}
        import json

        try:
            return True, json.loads(raw_body or b"{}")
        except json.JSONDecodeError:
            return False, {}

    def handle_webhook(self, db: Session, event: dict) -> str:
        from ..models import DepositStatus, WithdrawalStatus
        from ..services import payments as pay_svc

        kind = event.get("type")
        if kind == "invoice.confirmed":
            deposit = db.get(Deposit, event.get("deposit_id", ""))
            if not deposit:
                raise PaymentError("unknown deposit on invoice.confirmed")
            deposit.crypto_txid = event.get("txid")
            deposit.confirmations = int(event.get("confirmations", 0))
            if deposit.confirmations >= CONFIRMATIONS_REQUIRED:
                pay_svc.apply_deposit_status(db, deposit, DepositStatus.succeeded)
                return f"deposit {deposit.id} confirmed"
            return f"deposit {deposit.id} awaiting confirmations"

        if kind == "invoice.expired":
            deposit = db.get(Deposit, event.get("deposit_id", ""))
            if deposit:
                pay_svc.apply_deposit_status(db, deposit, DepositStatus.cancelled)
                return f"deposit {deposit.id} expired"

        if kind == "payout.sent":
            wd = db.get(Withdrawal, event.get("withdrawal_id", ""))
            if wd:
                pay_svc.apply_payout_status(db, wd, "paid", event.get("txid"))
                return f"withdrawal {wd.id} broadcast"
        if kind == "payout.failed":
            wd = db.get(Withdrawal, event.get("withdrawal_id", ""))
            if wd:
                pay_svc.apply_payout_status(db, wd, "failed", event.get("reason"))
                return f"withdrawal {wd.id} failed"

        raise PaymentError(f"unhandled cryptopay event {kind!r}")

    def health(self) -> dict:
        if not (self.api_key and self.base):
            return {"provider": self.name, "ok": False, "detail": "not configured",
                    "simulation": False}
        try:
            resp = httpx.get(f"{self.base}/health", timeout=10.0)
            return {"provider": self.name, "ok": resp.status_code < 400,
                    "detail": resp.text[:120], "simulation": False}
        except httpx.HTTPError as exc:
            return {"provider": self.name, "ok": False, "detail": str(exc), "simulation": False}
