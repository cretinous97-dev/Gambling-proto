"""Provider registry. `get_provider()` is the only thing the app imports."""
from __future__ import annotations

from functools import lru_cache

from ..config import settings
from .base import DepositIntent, PaymentError, PaymentProvider, PayoutResult
from .sandbox import SandboxProvider, sandbox_provider

# BankTransferProvider, like AdyenProvider and StripeProvider below, is
# deliberately NOT imported here. Each adapter's own module pulls in whatever
# that rail's API needs (httpx, for all four), and this registry is imported
# unconditionally by app/main.py on every cold start - so importing an adapter
# at module level means paying for, and risking the failure of, every rail's
# dependencies on every request, regardless of which PAYMENT_PROVIDER is
# configured. bank_transfer used to be the one exception to this; it is what
# made a missing `httpx` in the deploy requirements take down every
# deployment instead of only ones that configured it. See
# test_payment_adapters_are_imported_lazily_from_the_registry.
__all__ = [
    "AdyenProvider",
    "BankTransferProvider",
    "DepositIntent",
    "PaymentError",
    "PaymentProvider",
    "PayoutResult",
    "SandboxProvider",
    "get_provider",
    "get_provider_by_name",
    "provider_status",
]


@lru_cache
def get_provider_by_name(name: str) -> PaymentProvider:
    """Resolve an adapter by the name a banking pathway names.

    Routing decides *which* provider carries a transaction (see
    ``services/routing.py``); this turns that decision into an object. Cached
    per name: the adapters are stateless and hold no per-request state, so one
    instance per provider is both safe and what keeps the hot path off the
    import machinery.
    """
    return _build_provider((name or settings.payment_provider or "sandbox").lower())


@lru_cache
def get_provider() -> PaymentProvider:
    """The deployment's default provider, used when no pathway overrides it."""
    return _build_provider((settings.payment_provider or "sandbox").lower())


def _build_provider(name: str) -> PaymentProvider:
    if name == "sandbox":
        return sandbox_provider
    if name == "stripe":
        from .stripe_provider import StripeProvider

        return StripeProvider()
    if name == "adyen":
        from .adyen import AdyenProvider

        return AdyenProvider()
    if name == "bank_transfer":
        from .bank_transfer import bank_transfer_provider

        return bank_transfer_provider
    if name == "cryptopay":
        from .cryptopay import CryptoPayProvider

        return CryptoPayProvider()
    raise RuntimeError(
        f"unknown PAYMENT_PROVIDER={name!r} "
        "(expected sandbox | stripe | adyen | cryptopay | bank_transfer)"
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
