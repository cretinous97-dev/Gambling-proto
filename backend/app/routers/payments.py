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

from fastapi import APIRouter, Header, HTTPException, Query, Request, status

from ..config import settings
from ..payments import get_provider
from ..security import CurrentUser, Db
from ..services import payments as pay_svc

log = logging.getLogger("app.routers.payments")

router = APIRouter(prefix="/api", tags=["payments"])

#: Providers that may post here. Anything else is rejected before any work,
#: so a stray POST cannot reach a code path meant for a specific processor.
KNOWN_PROVIDERS = {"sandbox", "stripe", "cryptopay", "adyen", "bank_transfer"}

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


def _has_active_pathway(db, provider_name: str) -> bool:
    """Is any live banking pathway configured to use this provider?"""
    from sqlalchemy import select

    from ..models import BankingMethod

    row = db.execute(
        select(BankingMethod.id).where(
            BankingMethod.provider == provider_name,
            BankingMethod.active.is_(True),
        )
    ).first()
    return row is not None


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
    if name != configured and not _has_active_pathway(db, name):
        # A provider is accepted when it is the deployment default OR when the
        # operator has a live banking pathway that names it. Without the second
        # half, an operator settling through two rails at once - a global
        # acquirer and a local wallet, posting to their own URLs - would have
        # every callback from the second one rejected, and the deposits would
        # sit unsettled while the provider dutifully retried them.
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
        result = pay_svc.ingest_webhook(db, raw, signature, provider_name=name)
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



@router.get("/payments/methods")
def available_methods(
    user: CurrentUser,
    db: Db,
    direction: str = Query(default="deposit", pattern="^(deposit|withdrawal)$"),
    amount_minor: int = Query(default=0, ge=0),
):
    """The pathways this player can actually use, best first.

    This is what the cashier renders instead of a hardcoded list of card /
    bank / crypto. The list is whatever the operator has enabled for the
    player's country and currency - so adding a Bhutanese wallet makes it
    appear for Bhutanese players and nobody else, without a frontend release.

    Scoped to the authenticated player's own account country. A caller cannot
    ask what is available somewhere else, which matters because the answer
    differs by market and is commercially sensitive.
    """
    from ..services import payments as pay_svc
    from ..services import routing

    country = pay_svc._country_of(user)
    currency = pay_svc.instrument_currency(db, user)
    rows = routing.candidates(
        db,
        country=country,
        currency=currency,
        direction=direction,
        amount_minor=amount_minor or None,
    )
    return {
        "country": country,
        "currency": currency,
        "direction": direction,
        # Player-facing fields only, built by naming what goes out rather than
        # by removing what must not. `routing.payload()` is the admin shape and
        # carries `api_endpoint`, `credential_env` and `notes`; spreading it
        # here would publish internal topology and the name of the environment
        # variable holding each rail's key. Allow-list, never deny-list.
        "methods": [
            {
                "id": row.id,
                "name": row.name,
                "country_code": row.country_code,
                "currency": row.currency,
                "method": row.method.value,
                "deposits_enabled": row.deposits_enabled,
                "withdrawals_enabled": row.withdrawals_enabled,
                "min_amount_minor": row.min_amount_minor,
                "max_amount_minor": row.max_amount_minor,
                "fee_bps": row.fee_bps,
                "instructions": row.instructions or {},
                "available": True,
            }
            for row in rows
        ],
    }


@router.get("/payments/routing/preview", include_in_schema=False)
def routing_preview(
    user: CurrentUser,
    db: Db,
    direction: str = Query(default="deposit", pattern="^(deposit|withdrawal)$"),
    amount_minor: int = Query(default=0, ge=0),
):
    """Which pathway would carry this transaction, and why.

    Small, authenticated, and deliberately read-only: it exists so support can
    answer "why can't I deposit" without a database console, and it exposes the
    reasoning rather than just the winner.
    """
    from ..services import payments as pay_svc
    from ..services import routing

    country = pay_svc._country_of(user)
    currency = pay_svc.instrument_currency(db, user)
    rows = routing.candidates(
        db,
        country=country,
        currency=currency,
        direction=direction,
        amount_minor=amount_minor or None,
    )
    winner = rows[0] if rows else None
    return {
        "country": country,
        "currency": currency,
        "direction": direction,
        "amount_minor": amount_minor,
        "resolved": routing.payload(winner) if winner else None,
        "reason": (
            f"{winner.name} is the most specific active pathway for "
            f"{country}/{currency}."
            if winner
            else f"No active pathway is configured for {country}/{currency} "
            f"{direction}s. An administrator needs to add one."
        ),
    }

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
