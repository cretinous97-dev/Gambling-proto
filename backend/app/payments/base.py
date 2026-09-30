"""Payment provider abstraction.

The rest of the app never imports a PSP SDK directly - it talks to
`PaymentProvider`. That is what lets you swap the sandbox simulator for a live
processor without touching wallet, withdrawal or admin code.

Adding a real processor:
  1. subclass PaymentProvider in this package
  2. register it in `payments/__init__.py::get_provider`
  3. set PAYMENT_PROVIDER=<name> and its credentials in the environment

Contract for every implementation:
  * `create_deposit`  - start a payment, return how the client should continue
  * `verify_webhook`  - validate the signature and return (ok, event)
  * `handle_webhook`  - map a verified provider event onto our Deposit/
                        Withdrawal rows, idempotently
  * `create_payout`   - submit an approved withdrawal for payout
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..models import Deposit, User, Withdrawal


class PaymentError(Exception):
    """Provider-side failure. Surfaced to the player as a generic message and
    logged in full for ops."""


@dataclass
class DepositIntent:
    provider: str
    provider_ref: str
    status: str = "requires_action"          # maps onto DepositStatus
    redirect_url: str | None = None
    client_secret: str | None = None
    instructions: dict = field(default_factory=dict)
    fee: int = 0


@dataclass
class PayoutResult:
    provider: str
    provider_ref: str
    status: str = "approved"                 # approved | paid | failed
    detail: dict = field(default_factory=dict)


class PaymentProvider(ABC):
    name: str = "base"
    supports_deposit = True
    supports_withdrawal = True
    #: whether the client can call /deposits/{id}/simulate (sandbox only!)
    supports_simulation = False

    @abstractmethod
    def create_deposit(self, db: Session, user: User, deposit: Deposit) -> DepositIntent: ...

    @abstractmethod
    def create_payout(self, db: Session, withdrawal: Withdrawal) -> PayoutResult: ...

    def verify_webhook(self, raw_body: bytes, signature: str | None) -> tuple[bool, dict]:
        """Default: refuse everything. A provider that cannot authenticate its
        callbacks must not be trusted with money."""
        return False, {}

    def handle_webhook(self, db: Session, event: dict) -> str:
        raise PaymentError("this provider does not accept webhooks")

    def health(self) -> dict:
        return {"provider": self.name, "ok": True, "simulation": self.supports_simulation}
