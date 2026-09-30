"""Which payment pathway carries this transaction.

The rule this module exists to enforce: *the operator decides where money goes,
in the admin panel, not in a deploy.* Before this, every deposit went to
whichever single provider `PAYMENT_PROVIDER` named, which meant adding a
Bhutanese wallet or a local bank meant a code change and a release. Now it is a
row in `banking_methods`, and this module is the only place that reads it.

Resolution
----------
A pathway matches when all of these hold:

  * it is `active`
  * its `country_code` is the player's country or ``*``
  * its `currency` is the transaction currency or ``*``
  * the direction is enabled (`deposits_enabled` / `withdrawals_enabled`)
  * the amount is inside its own bounds, where those are set

Among the matches, the winner is the most *specific*, then the lowest
`priority`, then the lowest id. Specificity is the number of fields that
matched exactly rather than by wildcard, so ``BT``/``BTN`` beats ``BT``/``*``
beats ``*``/``*``. Ties break on id so the same inputs always produce the same
answer - an operator asking "why did that player get that bank" needs a reason,
not "whichever row the database returned first".

The wildcard is what keeps global access open: a ``*``/``*`` row at a high
priority number serves every market nobody has configured yet, so adding a
Bhutanese rail does not accidentally close the rest of the world.

Nothing here raises when no pathway matches. The caller decides: the cashier
shows an empty list, and a deposit says plainly that no route exists. Silently
falling back to a different provider would move a player's money to an account
the operator did not choose for that market, which is worse than refusing.
"""
from __future__ import annotations

import logging
import re

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import BankingMethod, PaymentMethod, utcnow

log = logging.getLogger("app.routing")

DEPOSIT = "deposit"
WITHDRAWAL = "withdrawal"
DIRECTIONS = (DEPOSIT, WITHDRAWAL)

#: `country_code` / `currency` value meaning "every one".
ANY = "*"

_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")

#: Providers an operator may name on a pathway. Kept here rather than imported
#: from the registry so this module stays importable without pulling in every
#: adapter (and its HTTP client) just to validate a form.
#:
#: The empty string is a deliberate sentinel meaning "whatever PAYMENT_PROVIDER
#: is set to in this deployment". It is what the global fallback row uses, so
#: the fallback keeps working when an operator promotes a provider from
#: sandbox to live without editing every row.
PROVIDER_NAMES = ("", "adyen", "stripe", "cryptopay", "bank_transfer", "sandbox")

#: Beyond this, a priority value is almost certainly a typo.
MAX_PRIORITY = 10_000


def normalise_country(value: str | None) -> str:
    """ISO-3166 alpha-2 upper case, or ``*``. Empty means ``*``."""
    raw = (value or "").strip().upper()
    if not raw or raw == ANY:
        return ANY
    if not _COUNTRY_RE.match(raw):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{value!r} is not a country code. Use ISO-3166 alpha-2 (BT) or * for all.",
        )
    return raw


def normalise_currency(value: str | None) -> str:
    raw = (value or "").strip().upper()
    if not raw or raw == ANY:
        return ANY
    if not _CURRENCY_RE.match(raw):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{value!r} is not a currency code. Use ISO-4217 (BTN) or * for all.",
        )
    return raw


def validate_endpoint(value: str | None) -> str:
    """An empty endpoint is allowed; a plaintext one is not.

    A deposit endpoint reached over http discloses the amount, the destination
    account and the API key to anything on the path. Loopback is exempt because
    that traffic never leaves the host - it is how a sidecar or a local
    aggregator is reached.
    """
    raw = (value or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    if lowered.startswith("https://"):
        return raw
    if lowered.startswith("http://") and settings.is_production:
        host = raw.split("//", 1)[1].split("/", 1)[0].split(":")[0]
        if host not in {"localhost", "127.0.0.1", "::1"}:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "API endpoints must use https in production - a plaintext "
                "payment endpoint leaks the amount, the account and the key.",
            )
        return raw
    if lowered.startswith("http://"):
        return raw
    raise HTTPException(
        status.HTTP_400_BAD_REQUEST,
        "API endpoint must be a full URL beginning with https:// (or be empty).",
    )


def validate(
    *,
    name: str,
    country_code: str,
    currency: str,
    provider: str,
    min_amount_minor: int,
    max_amount_minor: int,
    fee_bps: int,
    priority: int,
    account_id: str = "",
    credential_env: str = "",
) -> dict:
    """Validate an admin submission, returning the cleaned values.

    Every rule here exists because the alternative is a saved row that looks
    fine in the panel and then fails - or, worse, succeeds wrongly - on a real
    transaction. Failing at the form is cheap; failing at 3am on a payout is
    not.
    """
    name = (name or "").strip()
    if len(name) < 2:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Give the pathway a recognisable name."
        )
    if len(name) > 120:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Name is too long (120 max).")

    provider = (provider or "").strip().lower()
    if provider not in PROVIDER_NAMES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown provider {provider!r}. Valid: "
            f"{', '.join(p or 'deployment default' for p in PROVIDER_NAMES)}.",
        )

    account_id = (account_id or "").strip()
    if len(account_id) > 120:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Account id is too long (120 max).")

    credential_env = (credential_env or "").strip()
    if credential_env:
        # The value never touches this database - only the name of the variable
        # that holds it. Reject anything that already looks like a secret, so a
        # pasted key cannot be stored "by accident" and leaked by every export.
        if not re.match(r"^[A-Z][A-Z0-9_]{2,63}$", credential_env):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Credential field takes the NAME of an environment variable "
                "(e.g. MBOB_API_KEY), not the key itself.",
            )

    if not 0 <= fee_bps <= 10_000:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Fee must be between 0 and 10000 basis points."
        )
    if min_amount_minor < 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Minimum cannot be negative.")
    if max_amount_minor < 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Maximum cannot be negative.")
    if max_amount_minor and min_amount_minor > max_amount_minor:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Minimum is above maximum, so no transaction could ever qualify.",
        )
    if not 0 <= priority <= MAX_PRIORITY:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Priority must be 0-{MAX_PRIORITY}, lower wins."
        )

    return {
        "name": name,
        "country_code": normalise_country(country_code),
        "currency": normalise_currency(currency),
        "provider": provider,
        "account_id": account_id,
        "credential_env": credential_env,
        "min_amount_minor": min_amount_minor,
        "max_amount_minor": max_amount_minor,
        "fee_bps": fee_bps,
        "priority": priority,
    }


def _specificity(method: BankingMethod) -> int:
    """How many of (country, currency) matched exactly rather than by wildcard."""
    return (0 if method.country_code == ANY else 1) + (
        0 if method.currency == ANY else 1
    )


def _matches_amount(method: BankingMethod, amount_minor: int | None) -> bool:
    if amount_minor is None:
        return True
    if method.min_amount_minor and amount_minor < method.min_amount_minor:
        return False
    if method.max_amount_minor and amount_minor > method.max_amount_minor:
        return False
    return True


def candidates(
    db: Session,
    *,
    country: str | None,
    currency: str | None,
    direction: str,
    amount_minor: int | None = None,
) -> list[BankingMethod]:
    """Every active pathway that could serve this transaction, best first."""
    if direction not in DIRECTIONS:
        raise ValueError(f"direction must be one of {DIRECTIONS}")

    country_code = normalise_country(country)
    currency_code = normalise_currency(currency)
    column = (
        BankingMethod.deposits_enabled
        if direction == DEPOSIT
        else BankingMethod.withdrawals_enabled
    )

    rows = db.execute(
        select(BankingMethod).where(
            BankingMethod.active.is_(True),
            column.is_(True),
            BankingMethod.country_code.in_([country_code, ANY]),
            BankingMethod.currency.in_([currency_code, ANY]),
        )
    ).scalars().all()

    matches = [m for m in rows if _matches_amount(m, amount_minor)]
    matches.sort(key=lambda m: (-_specificity(m), m.priority, m.id))
    return matches


def resolve(
    db: Session,
    *,
    country: str | None,
    currency: str | None,
    direction: str,
    amount_minor: int | None = None,
) -> BankingMethod | None:
    """The single best pathway, or None when the operator has not configured one."""
    found = candidates(
        db,
        country=country,
        currency=currency,
        direction=direction,
        amount_minor=amount_minor,
    )
    return found[0] if found else None


def effective_provider(method: BankingMethod | None) -> str:
    """The provider that will actually be called for this pathway.

    An empty `provider` on the row means "the deployment default", so promoting
    a deployment from sandbox to live does not require editing every pathway.
    """
    if method is None or not method.provider:
        return settings.payment_provider.lower()
    return method.provider


def payload(method: BankingMethod, *, reveal_instructions: bool = True) -> dict:
    """JSON shape for the API.

    `credential_env` is included: it is the *name* of a variable, not a secret,
    and telling an operator which variable a pathway is waiting for turns "it
    does not work" into a one-line fix. Its value is never readable from here,
    because it is never stored.
    """
    body = {
        "id": method.id,
        "name": method.name,
        "country_code": method.country_code,
        "currency": method.currency,
        "account_id": method.account_id,
        "api_endpoint": method.api_endpoint,
        "credential_env": method.credential_env,
        "provider": method.provider,
        "effective_provider": effective_provider(method),
        "method": method.method.value,
        "deposits_enabled": method.deposits_enabled,
        "withdrawals_enabled": method.withdrawals_enabled,
        "active": method.active,
        "priority": method.priority,
        "min_amount_minor": method.min_amount_minor,
        "max_amount_minor": method.max_amount_minor,
        "fee_bps": method.fee_bps,
        "notes": method.notes,
        "created_at": method.created_at.isoformat() if method.created_at else None,
        "updated_at": method.updated_at.isoformat() if method.updated_at else None,
    }
    if reveal_instructions:
        body["instructions"] = method.instructions or {}
    return body


def credential_present(method: BankingMethod) -> bool:
    """Whether the secret this pathway needs is actually in the environment.

    Surfaced in the admin panel next to the row. A pathway that is `active` but
    has no credential is the single most common cause of "deposits work in
    staging and not in production", and it is invisible unless something asks.
    """
    import os

    if not method.credential_env:
        return True
    return bool(os.environ.get(method.credential_env, "").strip())


# ---------------------------------------------------------------------------
# starter configuration
# ---------------------------------------------------------------------------
#: Pathways seeded on first boot. Deliberately modest and deliberately
#: idempotent: a row is inserted only when no row with that name exists, so an
#: operator who switches a rail off does not find it switched back on by the
#: next deploy.
STARTER_PATHWAYS: tuple[dict, ...] = (
    {
        "name": "mBoB (Bank of Bhutan)",
        "country_code": "BT",
        "currency": "BTN",
        "provider": "bank_transfer",
        "method": PaymentMethod.ewallet,
        "account_id": settings.merchant_account_id,
        "credential_env": "MBOB_API_KEY",
        "deposits_enabled": True,
        "withdrawals_enabled": True,
        "priority": 10,
        "min_amount_minor": 10_000,        # Nu.100.00
        "max_amount_minor": 5_000_000,     # Nu.50,000.00
        "fee_bps": 0,
        "instructions": {
            "pay_to": "Bank of Bhutan - mBoB merchant account",
            "account_id": settings.merchant_account_id,
            "reference": "Use the reference shown on the payment page",
            "note": "Approve the request in your mBoB app. Credited on confirmation.",
        },
        "notes": "Bhutanese domestic wallet. Settles in BTN.",
    },
    {
        "name": "eTeeru Wallet",
        "country_code": "BT",
        "currency": "BTN",
        "provider": "bank_transfer",
        "method": PaymentMethod.ewallet,
        "account_id": settings.merchant_account_id,
        "credential_env": "ETEERU_API_KEY",
        "deposits_enabled": True,
        "withdrawals_enabled": True,
        "priority": 20,
        "min_amount_minor": 10_000,
        "max_amount_minor": 5_000_000,
        "fee_bps": 0,
        "instructions": {
            "pay_to": "eTeeru wallet transfer",
            "account_id": settings.merchant_account_id,
            "reference": "Use the reference shown on the payment page",
            "note": "Send from your eTeeru wallet, then confirm on the payment page.",
        },
        "notes": "Bhutanese national wallet. Settles in BTN.",
    },
    {
        "name": "International cards and wallets",
        "country_code": ANY,
        "currency": ANY,
        "provider": "",                     # whatever PAYMENT_PROVIDER is set to
        "method": PaymentMethod.card,
        "account_id": settings.merchant_account_id,
        "credential_env": "",
        "deposits_enabled": True,
        "withdrawals_enabled": True,
        "priority": 900,                    # only wins where nothing specific does
        "min_amount_minor": 500,
        "max_amount_minor": 1_000_000,
        "fee_bps": 0,
        "instructions": {},
        "notes": (
            "Global fallback. Serves every country and currency, so adding a "
            "local rail never closes the rest of the world."
        ),
    },
)


def seed_starter_pathways(db: Session) -> list[str]:
    """Insert any starter pathway that is not already there. Returns what it added.

    A local rail is seeded **active only when its credential is actually in the
    environment**. That is not caution for its own sake: a pathway that is
    enabled while its key is missing is a market where every deposit fails, and
    it fails *instead of* the working fallback, because routing prefers the more
    specific match. Seeding it enabled would take a deployment that works and
    break one country in it.

    So a fresh deployment starts with the global fallback carrying every market,
    and the Bhutanese rails sit there configured, listed and switched off, with
    a note saying which variable to set. Adding `MBOB_API_KEY` and flipping the
    switch is the whole go-live step - and an operator who would rather not
    wait can do it from the panel without a redeploy.
    """
    import os

    added: list[str] = []
    for spec in STARTER_PATHWAYS:
        exists = db.execute(
            select(BankingMethod.id).where(BankingMethod.name == spec["name"])
        ).first()
        if exists:
            continue

        row = dict(spec)
        needs_credential = bool(row.get("credential_env"))
        credential_ready = bool(
            os.environ.get(row.get("credential_env") or "", "").strip()
        )
        if needs_credential and not credential_ready:
            row["active"] = False
            row["notes"] = (
                (row.get("notes") or "")
                + f" Seeded inactive: {row['credential_env']} is not set in this "
                "deployment. Set it, then enable this pathway here."
            ).strip()

        db.add(BankingMethod(**row, created_by="system:seed"))
        # Say only what is true of this row: a pathway that needs no credential
        # is ready, and reporting it as held back for a missing key would send
        # an operator looking for a variable that does not exist.
        if needs_credential and not credential_ready:
            added.append(f"{spec['name']} (inactive: set {spec['credential_env']})")
        else:
            added.append(spec["name"])

    if added:
        db.flush()
        log.info("seeded banking pathways: %s", ", ".join(added))
    return added


def startup_banner() -> str:
    """One line for the boot log: how the operator has configured routing."""
    return (
        "payment routing: banking_methods table drives pathway selection; "
        f"fallback provider={settings.payment_provider}"
    )


def touch(db: Session, method: BankingMethod) -> None:
    method.updated_at = utcnow()
    db.flush()
