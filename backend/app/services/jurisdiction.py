"""Jurisdiction policy engine.

One place decides which countries may register, play, deposit and withdraw.
Every gate in the application asks this module rather than comparing against a
hardcoded list, because the answer is a commercial decision that changes per
market and per licence - not something that belongs in the source of a router.

Three modes, set with ``JURISDICTION_MODE``:

===============  ==========================================================
``allow_all``    every country is accepted. The default.
``blocklist``    every country except those in ``JURISDICTION_BLOCKLIST``.
``allowlist``    only the countries in ``JURISDICTION_ALLOWLIST``.
===============  ==========================================================

``RESTRICTED_REGIONS`` is a middle tier, and the one real operators use most:
accounts and gameplay are fine, payment rails are not. A player there can sign
up, log in and play with a balance they already hold; deposits and withdrawals
are refused with a message a support agent can stand behind.

What this module does NOT decide
--------------------------------
Age, KYC, AML flags, deposit/loss limits, self-exclusion and cool-off are
enforced in ``services/compliance.py`` and are independent of this policy.
Opening the jurisdiction switch does not open those, deliberately: they are
what protect a player from you and you from a regulator, and they are cheap to
leave on. They are configured, not hardcoded.

Operating note (not legal advice): accepting players from a market where you
hold no licence is your risk to take, and it is taken in that market's courts
and with your PSP - not here. This module makes the policy *configurable* so
you can run it per market; it does not make any particular policy lawful.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from fastapi import HTTPException, status

from ..config import settings

log = logging.getLogger("app.jurisdiction")

#: Actions a jurisdiction rule can gate.
REGISTER = "register"
PLAY = "play"
DEPOSIT = "deposit"
WITHDRAW = "withdraw"

#: Actions that move money. These are what the restricted tier blocks.
PAYMENT_ACTIONS = frozenset({DEPOSIT, WITHDRAW})

ACTIONS = (REGISTER, PLAY, DEPOSIT, WITHDRAW)
MODES = ("allow_all", "blocklist", "allowlist")

_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")

#: Tier names, reported in the decision so logs and the admin panel can say
#: *why* a country was refused without parsing a message.
OPEN = "open"
BLOCKED = "blocked"
NOT_ALLOWED = "not_allowed"
RESTRICTED = "restricted"


def normalise(country: str | None) -> str:
    """ISO-3166 alpha-2, upper case. Anything malformed becomes empty string,
    which is treated as 'unknown' rather than silently defaulting to allowed
    in allowlist mode."""
    value = (country or "").strip().upper()
    return value if _COUNTRY_RE.match(value) else ""


def mode() -> str:
    value = (settings.jurisdiction_mode or "allow_all").strip().lower()
    if value not in MODES:
        log.warning(
            "unknown JURISDICTION_MODE=%r; falling back to allow_all (valid: %s)",
            settings.jurisdiction_mode,
            ", ".join(MODES),
        )
        return "allow_all"
    return value


@dataclass(frozen=True)
class Decision:
    allowed: bool
    action: str
    country: str
    tier: str
    reason: str | None = None

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "action": self.action,
            "country": self.country or None,
            "tier": self.tier,
            "reason": self.reason,
        }


def evaluate(country: str | None, action: str) -> Decision:
    """Decide one action for one country. Pure: no side effects, no I/O."""
    cc = normalise(country)
    current = mode()

    if current == "blocklist" and cc and cc in settings.blocklist:
        return Decision(False, action, cc, BLOCKED, _message(action, cc, BLOCKED))

    if current == "allowlist" and cc not in settings.allowlist:
        tier = NOT_ALLOWED
        return Decision(False, action, cc, tier, _message(action, cc, tier))

    if cc and cc in settings.restricted and action in PAYMENT_ACTIONS:
        return Decision(False, action, cc, RESTRICTED, _message(action, cc, RESTRICTED))

    return Decision(True, action, cc, OPEN)


def _message(action: str, country: str, tier: str) -> str:
    if tier == RESTRICTED:
        if action == DEPOSIT:
            return (
                f"Card and bank deposits are not available in {country}. "
                "Your account and balance are unaffected."
            )
        return f"Withdrawals are not available in {country}. Contact support."
    if action == REGISTER:
        return f"New accounts are not available in {country}."
    if action == DEPOSIT:
        return f"Deposits are not available in {country}."
    if action == WITHDRAW:
        return f"Withdrawals are not available in {country}."
    return f"Play is not available in {country}."


def assert_allowed(country: str | None, action: str) -> Decision:
    """Raise 403 unless the action is permitted. Returns the decision so a
    caller can log the tier on the way through."""
    decision = evaluate(country, action)
    if not decision.allowed:
        log.info(
            "jurisdiction refused action=%s country=%s tier=%s",
            decision.action,
            decision.country or "??",
            decision.tier,
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, decision.reason or "Not available.")
    return decision


def is_blocked(country: str | None) -> bool:
    """Convenience for callers that only care whether anything is gated."""
    return not evaluate(country, PLAY).allowed or not evaluate(country, DEPOSIT).allowed


def snapshot() -> dict:
    """The resolved policy: for /api/config, the admin panel and the audit log.

    Exposed publicly on purpose. A player refused at signup deserves to know
    the rule that refused them, and an operator debugging "why can't I deposit
    from X" needs the effective configuration, not the raw env string.
    """
    current = mode()
    return {
        "mode": current,
        "blocklist": sorted(settings.blocklist),
        "allowlist": sorted(settings.allowlist),
        "restricted": sorted(settings.restricted),
        "geo_enforcement": bool(settings.geo_enforcement),
        "open_to_every_country": current == "allow_all",
        "warning": _warning(),
    }


def _warning() -> str | None:
    """Say the quiet part out loud in the one place an operator will look."""
    if mode() != "allow_all":
        return None
    if settings.is_production:
        return (
            "JURISDICTION_MODE=allow_all: every country is accepted, including "
            "markets where this platform may hold no licence. Confirm your "
            "licences, your PSP's permitted markets and your tax position "
            "before taking real deposits."
        )
    return (
        "JURISDICTION_MODE=allow_all: all countries accepted (default). "
        "Switch with JURISDICTION_MODE=blocklist|allowlist."
    )


def log_startup_banner() -> None:
    """Logged once at boot so the policy is never a surprise."""
    current = mode()
    log.info(
        "jurisdiction policy: mode=%s blocked=%d allowlisted=%d restricted=%d",
        current,
        len(settings.blocklist),
        len(settings.allowlist),
        len(settings.restricted),
    )
    message = _warning()
    if message and current == "allow_all":
        log.warning(message)
