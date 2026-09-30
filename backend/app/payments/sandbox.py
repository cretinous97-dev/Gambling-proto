"""Sandbox provider - a fully functional PSP SIMULATOR.

This is what runs by default so you can exercise the entire money path
(deposit -> play -> withdraw -> admin approval -> payout) end to end without a
merchant account and without moving a cent of real money.

The client drives it through:
    POST /api/wallet/deposits            -> creates a pending deposit + a
                                            simulated checkout session
    POST /api/wallet/deposits/{id}/simulate   -> "the customer paid" (or failed)
    POST /api/wallet/withdrawals         -> request
    admin approve -> payout submitted here -> instantly "paid"

Nothing in this module may ever be enabled in production: the simulate endpoint
is gated on PAYMENT_PROVIDER=sandbox.
"""
from __future__ import annotations

import hashlib
import json
import secrets

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Deposit, DepositStatus, PaymentMethod, User, Withdrawal
from ..security import sign_payload
from .base import DepositIntent, PaymentError, PaymentProvider, PayoutResult

FAKE_ADDRESSES = {
    PaymentMethod.crypto_btc: "bc1q",
    PaymentMethod.crypto_eth: "0x",
    PaymentMethod.crypto_usdt: "T",
}


class SandboxProvider(PaymentProvider):
    name = "sandbox"
    supports_simulation = True

    # -- deposits ----------------------------------------------------------
    def create_deposit(self, db: Session, user: User, deposit: Deposit) -> DepositIntent:
        ref = f"sbx_{deposit.id[:16]}"
        deposit.provider_ref = ref
        instructions: dict = {"merchant": settings.app_name, "reference": ref}

        if deposit.method in FAKE_ADDRESSES:
            prefix = FAKE_ADDRESSES[deposit.method]
            address = prefix + hashlib.sha256(f"addr:{ref}".encode()).hexdigest()[:38]
            deposit.crypto_address = address
            instructions.update(
                {
                    "address": address,
                    "network": {
                        PaymentMethod.crypto_btc: "Bitcoin mainnet",
                        PaymentMethod.crypto_eth: "Ethereum (ERC-20)",
                        PaymentMethod.crypto_usdt: "Tron (TRC-20)",
                    }[deposit.method],
                    "amount_display": f"{deposit.amount / 100:.2f} USD",
                    "confirmations_required": 2,
                }
            )
        elif deposit.method is PaymentMethod.card:
            deposit.card_last4 = "4242"
            instructions.update(
                {
                    "card_hint": "4242 4242 4242 4242 (simulated)",
                    "note": "Sandbox mode: no real card data is captured or stored.",
                }
            )
        elif deposit.method is PaymentMethod.bank_transfer:
            instructions.update(
                {
                    "iban": "BT00 SBX 0000 0000 0000 0000",
                    "swift": "SBXBTTXX",
                    "beneficiary": f"{settings.app_name} Clearing",
                    "reference_required": ref,
                }
            )
        else:
            instructions.update({"wallet": ref})

        return DepositIntent(
            provider=self.name,
            provider_ref=ref,
            status="requires_action",
            instructions=instructions,
            redirect_url=f"/checkout/{deposit.id}",
        )

    def confirm_simulated_deposit(
        self, db: Session, deposit: Deposit, *, outcome: str = "succeed"
    ) -> DepositStatus:
        """The sandbox 'webhook'. Called by the simulate endpoint only."""
        if deposit.status is DepositStatus.succeeded:
            return deposit.status
        if outcome == "succeed":
            if deposit.method in FAKE_ADDRESSES:
                deposit.confirmations = 2
            return DepositStatus.succeeded
        if outcome == "chargeback":
            return DepositStatus.chargeback
        return DepositStatus.failed

    # -- payouts -----------------------------------------------------------
    def create_payout(self, db: Session, withdrawal: Withdrawal) -> PayoutResult:
        ref = f"sbxp_{withdrawal.id[:16]}"
        return PayoutResult(provider=self.name, provider_ref=ref, status="paid",
                            detail={"simulated": True, "settled_in": "instant"})

    # -- webhooks ----------------------------------------------------------
    def verify_webhook(self, raw_body: bytes, signature: str | None) -> tuple[bool, dict]:
        """Sandbox webhooks are HMAC-signed with the app secret so the plumbing
        (signature check -> replay store -> idempotent processing) is identical
        in shape to a live processor."""
        expected = sign_payload(raw_body, settings.secret_key)
        if not signature or not secrets.compare_digest(signature, expected):
            return False, {}
        try:
            return True, json.loads(raw_body or b"{}")
        except json.JSONDecodeError:
            return False, {}

    def handle_webhook(self, db: Session, event: dict) -> str:
        from ..services import payments as pay_svc

        kind = event.get("type")
        deposit_id = event.get("deposit_id")
        if kind in ("payment.succeeded", "payment.failed", "payment.chargeback") and deposit_id:
            deposit = db.get(Deposit, deposit_id)
            if not deposit:
                raise PaymentError(f"unknown deposit {deposit_id}")
            mapped = {
                "payment.succeeded": DepositStatus.succeeded,
                "payment.failed": DepositStatus.failed,
                "payment.chargeback": DepositStatus.chargeback,
            }[kind]
            pay_svc.apply_deposit_status(db, deposit, mapped)
            return f"deposit {deposit_id} -> {mapped.value}"
        raise PaymentError(f"unhandled sandbox event {kind!r}")


sandbox_provider = SandboxProvider()
