"""Provider callbacks: the endpoints a PSP posts to.

This is the half of a payment integration that is easiest to get wrong and
hardest to notice, because the deposit flow *looks* fine without it: the player
is redirected to the provider, pays, comes back to a success page, and the
money never appears - because nothing credited it. The provider's webhook is
what actually settles a payment. Everything before it only starts one.

Two routes are published, both unauthenticated by design (a PSP has no account
here) and both defended by signature verification instead:

    POST /api/payments/webhooks/{provider}     canonical
    POST /api/wallet/webhooks/{provider}       kept: documented in DEPLOY-CHECKLIST

Behaviour that matters in production
-----------------------------------
* The raw body is verified **before** it is parsed as JSON. Signature schemes
  sign bytes; re-serialising a parsed body changes them.
* A duplicate delivery returns 200 and changes nothing. PSPs retry on any
  non-2xx, so a non-idempotent handler turns one payment into several credits.
* An invalid signature returns 400 and is recorded. It is never retried into a
  success.
* A handler failure returns 422 *and leaves the event stored with its error*,
  so the retry can be replayed by an operator and the reason is auditable.
* Responses are small and fast: no work happens here that could be deferred
  without risking the provider's timeout.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Request, status

from ..config import settings
from ..payments import get_provider
from ..security import Db
from ..services import payments as pay_svc

log = logging.getLogger("app.routers.payments")

router = APIRouter(prefix="/api", tags=["payments"])

#: Providers that may post here. Anything else is rejected before any work,
#: so a stray POST cannot reach a code path meant for a specific processor.
KNOWN_PROVIDERS = {"sandbox", "stripe", "cryptopay", "adyen"}

#: Header names providers use for the signature, checked in this order. A
#: provider that carries its signature inside the body (Adyen) is handled by
#: the adapter itself and simply arrives with no header.
SIGNATURE_HEADERS = (
    "stripe-signature",
    "x-signature",
    "x-adyen-signature",
    "x-cryptopay-signature",
    "x-webhook-signature",
)


def _signature_from(headers) -> str | None:
    for name in SIGNATURE_HEADERS:
        value = headers.get(name)
        if value:
            return value
    return None


@router.post("/payments/webhooks/{provider}", include_in_schema=False)
@router.post("/wallet/webhooks/{provider}", include_in_schema=False)
async def provider_webhook(
    provider: str,
    request: Request,
    db: Db,
    stripe_signature: str | None = Header(default=None),
    x_signature: str | None = Header(default=None),
):
    """Ingest one provider callback.

    Deliberately not `async def ... await request.json()`: the raw bytes are
    what gets verified, so the body is read once and passed through untouched.
    """
    name = (provider or "").strip().lower()
    if name not in KNOWN_PROVIDERS:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Unknown payment provider {name!r}. Known: {', '.join(sorted(KNOWN_PROVIDERS))}",
        )

    configured = (settings.payment_provider or "sandbox").strip().lower()
    if name != configured:
        # A valid signature for a provider that is not the live one is still
        # not something to act on: it would move money through a rail this
        # deployment is not configured to settle.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This deployment is configured for {configured!r}; callbacks for "
            f"{name!r} are not processed.",
        )

    raw = await request.body()
    if not raw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty webhook body")

    signature = stripe_signature or x_signature or _signature_from(request.headers)

    try:
        result = pay_svc.ingest_webhook(db, raw, signature)
    except HTTPException as exc:
        # Signature problems are the caller's fault (400) and are terminal;
        # processing problems are ours (422) and the provider will retry.
        if exc.status_code == status.HTTP_400_BAD_REQUEST:
            log.warning("rejected %s webhook: %s", name, exc.detail)
        else:
            log.error("failed to process %s webhook: %s", name, exc.detail)
        raise

    log.info("processed %s webhook: %s", name, result)
    return {"received": True, **result}


@router.get("/payments/providers", include_in_schema=False)
def webhook_providers():
    """Where to point a provider, and whether we can verify its callbacks.

    Operators register a URL in a dashboard once and then never think about it
    again; this answers "which URL, and will it be accepted" without reading
    the code.
    """
    active = (settings.payment_provider or "sandbox").strip().lower()
    verifiable = {
        "sandbox": bool(settings.secret_key),
        "stripe": bool(settings.stripe_webhook_secret),
        "adyen": bool(settings.adyen_hmac_key),
        "cryptopay": bool(settings.cryptopay_webhook_secret),
    }
    base = "/api/payments/webhooks"
    return {
        "active_provider": active,
        "url": f"{base}/{active}",
        "legacy_url": f"/api/wallet/webhooks/{active}",
        "signature_verified": verifiable.get(active, False),
        "providers": [
            {
                "name": name,
                "url": f"{base}/{name}",
                "configured": name == active,
                "signature_verified": verifiable.get(name, False),
            }
            for name in sorted(KNOWN_PROVIDERS)
        ],
    }
