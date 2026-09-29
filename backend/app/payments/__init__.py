"""Provider registry. `get_provider()` is the only thing the app imports."""
from __future__ import annotations

from functools import lru_cache

from ..config import settings
from .base import DepositIntent, PaymentError, PaymentProvider, PayoutResult
from .sandbox import SandboxProvider, sandbox_provider

__all__ = [
    "AdyenProvider",
    "DepositIntent",
    "PaymentError",
    "PaymentProvider",
    "PayoutResult",
    "SandboxProvider",
    "get_provider",
    "provider_status",
]


@lru_cache
def get_provider() -> PaymentProvider:
    name = settings.payment_provider.lower()
    if name == "sandbox":
        return sandbox_provider
    if name == "stripe":
        from .stripe_provider import StripeProvider

        return StripeProvider()
    if name == "adyen":
        from .adyen import AdyenProvider

        return AdyenProvider()
    if name == "cryptopay":
        from .cryptopay import CryptoPayProvider

        return CryptoPayProvider()
    raise RuntimeError(
        f"unknown PAYMENT_PROVIDER={name!r} "
        "(expected sandbox | stripe | adyen | cryptopay)"
    )


def provider_status() -> dict:
    """Boot-time banner + /admin/health payload. Loud about simulation mode."""
    provider = get_provider()
    health = provider.health()
    is_live = settings.payment_provider.lower() != "sandbox"
    return {
        **health,
        "mode": "live" if is_live else "simulation",
        "warning": (
            None
            if is_live
            else "SANDBOX MODE - no real money moves. Set PAYMENT_PROVIDER and "
            "your PSP credentials to go live."
        ),
    }
