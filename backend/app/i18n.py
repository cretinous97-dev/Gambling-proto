"""Localization: languages, currencies, and how amounts are presented.

The decision that matters
-------------------------
There are two different products pretending to be one when someone says
"multi-currency":

1. **Display localization** - the player sees "€18.40" instead of "$20.00".
   One ledger, one settlement currency, one set of limits; the number is
   translated for the eye at render time. Cheap, ships today, no accounting
   risk.
2. **Multi-currency settlement** - the player holds EUR, bets in EUR, the
   house books EUR revenue, and every ledger entry carries its own currency.
   That is an accounting and treasury project: per-currency accounts, FX at
   the moment of deposit, currency-specific limits and bonus terms, and a
   P&L that has to survive an FX revaluation.

This module implements **1** and is explicit about it:

* the ledger stays in one settlement currency (``SETTLEMENT_CURRENCY``, USD by
  default), integer minor units, exactly as before;
* every limit, stake, payout and ledger entry is stored, compared and enforced
  in that currency, never in the displayed one;
* ``display_*`` helpers convert **only** for presentation, and every payload
  that carries a converted amount also carries the settlement amount and a
  ``display_only`` flag so no client can accidentally treat it as money.

If you later want (2), the migration is real work and the ledger is the part
that must not be improvised: it is per-currency accounts, an FX rate captured
per transaction, and a revaluation job. The README says so in the operator
section. Doing (1) while claiming (2) is how an operator finds out at audit
that their books are wrong.

Rates
-----
``FX_RATES`` is a JSON map of *display* rates against the settlement currency.
They are deliberately not used to price anything. Keep them fresh from your
treasury feed (``PUT /api/admin/fx-rates``) - a stale display rate is a
customer-service problem, not a money problem, and the split is what makes
that sentence true.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from .config import settings

log = logging.getLogger("app.i18n")

#: Locales the UI ships translations for (the frontend has matching bundles).
#: `ar` is right-to-left, which is why direction is part of the registry rather
#: than a CSS afterthought.
LOCALES: dict[str, dict] = {
    "en": {"name": "English", "native": "English", "dir": "ltr", "currency": "USD"},
    "es": {"name": "Spanish", "native": "Español", "dir": "ltr", "currency": "EUR"},
    "pt": {"name": "Portuguese", "native": "Português", "dir": "ltr", "currency": "BRL"},
    "de": {"name": "German", "native": "Deutsch", "dir": "ltr", "currency": "EUR"},
    "fr": {"name": "French", "native": "Français", "dir": "ltr", "currency": "EUR"},
    "it": {"name": "Italian", "native": "Italiano", "dir": "ltr", "currency": "EUR"},
    "zh": {"name": "Chinese (Simplified)", "native": "简体中文", "dir": "ltr", "currency": "CNY"},
    "hi": {"name": "Hindi", "native": "हिन्दी", "dir": "ltr", "currency": "INR"},
    "ar": {"name": "Arabic", "native": "العربية", "dir": "rtl", "currency": "AED"},
}

#: Currency metadata. `minor` is the number of decimal places the currency's
#: smallest unit has - JPY and KRW have none, and getting that wrong is a
#: classic 100x display bug.
CURRENCIES: dict[str, dict] = {
    "USD": {"symbol": "$", "name": "US Dollar", "minor": 2},
    "EUR": {"symbol": "€", "name": "Euro", "minor": 2},
    "GBP": {"symbol": "£", "name": "Pound Sterling", "minor": 2},
    "BTN": {"symbol": "Nu.", "name": "Bhutanese Ngultrum", "minor": 2},
    "INR": {"symbol": "₹", "name": "Indian Rupee", "minor": 2},
    "AED": {"symbol": "د.إ", "name": "UAE Dirham", "minor": 2},
    "BRL": {"symbol": "R$", "name": "Brazilian Real", "minor": 2},
    "CAD": {"symbol": "C$", "name": "Canadian Dollar", "minor": 2},
    "AUD": {"symbol": "A$", "name": "Australian Dollar", "minor": 2},
    "CHF": {"symbol": "CHF", "name": "Swiss Franc", "minor": 2},
    "SEK": {"symbol": "kr", "name": "Swedish Krona", "minor": 2},
    "NOK": {"symbol": "kr", "name": "Norwegian Krone", "minor": 2},
    "PLN": {"symbol": "zł", "name": "Polish Zloty", "minor": 2},
    "TRY": {"symbol": "₺", "name": "Turkish Lira", "minor": 2},
    "ZAR": {"symbol": "R", "name": "South African Rand", "minor": 2},
    "NGN": {"symbol": "₦", "name": "Nigerian Naira", "minor": 2},
    "KES": {"symbol": "KSh", "name": "Kenyan Shilling", "minor": 2},
    "JPY": {"symbol": "¥", "name": "Japanese Yen", "minor": 0},
    "KRW": {"symbol": "₩", "name": "South Korean Won", "minor": 0},
    "CNY": {"symbol": "¥", "name": "Chinese Yuan", "minor": 2},
    "HKD": {"symbol": "HK$", "name": "Hong Kong Dollar", "minor": 2},
    "SGD": {"symbol": "S$", "name": "Singapore Dollar", "minor": 2},
    "MYR": {"symbol": "RM", "name": "Malaysian Ringgit", "minor": 2},
    "THB": {"symbol": "฿", "name": "Thai Baht", "minor": 2},
    "IDR": {"symbol": "Rp", "name": "Indonesian Rupiah", "minor": 2},
    "PHP": {"symbol": "₱", "name": "Philippine Peso", "minor": 2},
    "VND": {"symbol": "₫", "name": "Vietnamese Dong", "minor": 0},
    "PKR": {"symbol": "₨", "name": "Pakistani Rupee", "minor": 2},
    "BDT": {"symbol": "৳", "name": "Bangladeshi Taka", "minor": 2},
    "NPR": {"symbol": "₨", "name": "Nepalese Rupee", "minor": 2},
    "LKR": {"symbol": "Rs", "name": "Sri Lankan Rupee", "minor": 2},
    "MXN": {"symbol": "MX$", "name": "Mexican Peso", "minor": 2},
    "ARS": {"symbol": "AR$", "name": "Argentine Peso", "minor": 2},
    "CLP": {"symbol": "CLP$", "name": "Chilean Peso", "minor": 0},
    "COP": {"symbol": "COL$", "name": "Colombian Peso", "minor": 2},
    "PEN": {"symbol": "S/", "name": "Peruvian Sol", "minor": 2},
    "BTC": {"symbol": "₿", "name": "Bitcoin (display)", "minor": 8},
    "ETH": {"symbol": "Ξ", "name": "Ether (display)", "minor": 8},
    "USDT": {"symbol": "₮", "name": "Tether (display)", "minor": 2},
}

#: Which currency a visitor from a country is offered first. Not exhaustive by
#: design - unknown countries fall back to the settlement currency, which is
#: always correct, just not localised.
COUNTRY_CURRENCY: dict[str, str] = {
    "US": "USD", "GB": "GBP", "IE": "EUR", "FR": "EUR", "DE": "EUR", "ES": "EUR",
    "IT": "EUR", "PT": "EUR", "NL": "EUR", "BE": "EUR", "AT": "EUR", "FI": "EUR",
    "GR": "EUR", "SK": "EUR", "SI": "EUR", "LT": "EUR", "LV": "EUR", "EE": "EUR",
    "MT": "EUR", "CY": "EUR", "LU": "EUR", "HR": "EUR",
    "BT": "BTN", "IN": "INR", "NP": "NPR", "BD": "BDT", "PK": "PKR", "LK": "LKR",
    "AE": "AED", "SA": "AED", "QA": "AED", "KW": "AED",
    "BR": "BRL", "MX": "MXN", "AR": "ARS", "CL": "CLP", "CO": "COP", "PE": "PEN",
    "CA": "CAD", "AU": "AUD", "NZ": "AUD", "CH": "CHF", "SE": "SEK", "NO": "NOK",
    "DK": "NOK", "PL": "PLN", "TR": "TRY", "ZA": "ZAR", "NG": "NGN", "KE": "KES",
    "JP": "JPY", "KR": "KRW", "CN": "CNY", "HK": "HKD", "SG": "SGD", "MY": "MYR",
    "TH": "THB", "ID": "IDR", "PH": "PHP", "VN": "VND",
}

#: Language to offer first, from the country. Locale endonyms are not a good
#: guess (a Swiss player is not served German by default here).
COUNTRY_LOCALE: dict[str, str] = {
    "ES": "es", "MX": "es", "AR": "es", "CO": "es", "CL": "es", "PE": "es",
    "BR": "pt", "PT": "pt", "AO": "pt", "MZ": "pt",
    "DE": "de", "AT": "de", "CH": "de", "FR": "fr", "BE": "fr", "IT": "it",
    "CN": "zh", "TW": "zh", "SG": "zh", "HK": "zh", "IN": "hi", "NP": "hi",
    "AE": "ar", "SA": "ar", "QA": "ar", "KW": "ar", "BH": "ar", "OM": "ar",
}

#: Fallback display rates, USD per unit. Only used when FX_RATES is unset, so
#: that a fresh deployment shows *something* sane; every one of them is a
#: placeholder that will drift. Set FX_RATES (or PUT /api/admin/fx-rates).
DEFAULT_FX_RATES: dict[str, float] = {
    "USD": 1.0, "EUR": 0.92, "GBP": 0.79, "BTN": 83.6, "INR": 83.4, "AED": 3.67,
    "BRL": 5.42, "CAD": 1.36, "AUD": 1.52, "CHF": 0.88, "SEK": 10.5, "NOK": 10.7,
    "PLN": 3.98, "TRY": 32.5, "ZAR": 18.4, "NGN": 1450.0, "KES": 129.0,
    "JPY": 157.0, "KRW": 1380.0, "CNY": 7.25, "HKD": 7.81, "SGD": 1.35,
    "MYR": 4.71, "THB": 36.6, "IDR": 16200.0, "PHP": 58.5, "VND": 25400.0,
    "PKR": 278.0, "BDT": 117.0, "NPR": 133.5, "LKR": 303.0, "MXN": 18.2,
    "ARS": 900.0, "CLP": 940.0, "COP": 4100.0, "PEN": 3.79,
    # Crypto display rates move constantly; treat these as "wrong by tomorrow".
    "BTC": 0.0000150, "ETH": 0.00027, "USDT": 1.0,
}


def _parse_rates(raw: str) -> tuple[dict[str, float], list[str]]:
    """Returns the rate table and a list of human-readable problems."""
    problems: list[str] = []
    rates: dict[str, float] = {}
    if raw and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            problems.append(f"FX_RATES is not valid JSON ({exc}); using defaults")
            parsed = {}
        if not isinstance(parsed, dict):
            problems.append("FX_RATES must be a JSON object; using defaults")
            parsed = {}
        for code, value in parsed.items():
            code = str(code).upper()
            try:
                rate = float(value)
            except (TypeError, ValueError):
                problems.append(f"FX_RATES[{code}] is not a number; ignored")
                continue
            if rate <= 0:
                problems.append(f"FX_RATES[{code}] must be > 0; ignored")
                continue
            rates[code] = rate
    merged = {**DEFAULT_FX_RATES, **rates}
    merged[settings.settlement_currency.upper()] = 1.0
    return merged, problems


def rates() -> dict[str, float]:
    """The effective display-rate table (settlement currency pinned to 1.0)."""
    return _parse_rates(settings.fx_rates)[0]


def rates_problems() -> list[str]:
    return _parse_rates(settings.fx_rates)[1]


def set_rates(new_rates: dict[str, float], *, updated_at: str) -> None:
    """Replace the display table at runtime (admin endpoint, audited).

    Mutates the running settings object only; persist it in the environment if
    you want it to survive a restart. A serverless deployment has no writable
    disk, which is why this is not written back to a file.
    """
    cleaned = {str(k).upper(): float(v) for k, v in new_rates.items() if float(v) > 0}
    settings.fx_rates = json.dumps(cleaned)
    settings.fx_rates_updated_at = updated_at
    log.info("fx display rates replaced: %d currencies", len(cleaned))


def available_currencies() -> list[str]:
    """Currencies offered in the switcher: configured, known, and rateable."""
    configured = [c.strip().upper() for c in settings.display_currencies.split(",") if c.strip()]
    table = rates()
    if configured:
        return [c for c in configured if c in table]
    return sorted(code for code in table if code in CURRENCIES)


def currency_meta(code: str) -> dict:
    code = (code or settings.settlement_currency).upper()
    meta = CURRENCIES.get(code, {"symbol": code, "name": code, "minor": 2})
    return {"code": code, **meta, "rate": rates().get(code, 1.0)}


def locale_for(country: str | None, accept_language: str | None = None) -> str:
    """Pick a locale: explicit header first, then the country, then default."""
    supported = settings.locales
    if accept_language:
        for part in accept_language.split(","):
            tag = part.split(";")[0].strip().lower()
            base = tag.split("-")[0]
            for candidate in (tag, base):
                if candidate in supported:
                    return candidate
    cc = (country or "").upper()
    mapped = COUNTRY_LOCALE.get(cc)
    if mapped and mapped in supported:
        return mapped
    return settings.default_locale.lower()


def locale_for_country(country: str | None) -> dict:
    """The locale and language to default a new account to, from its region.

    Used at registration so a player starts in their own language and currency
    rather than in English and dollars. Only a default: anything the player
    states explicitly overrides it.
    """
    locale = locale_for(country)
    return {"locale": locale, "language": locale.split("-")[0] if locale else None}


def currency_for(country: str | None) -> str:
    """The display currency to offer a visitor from this country."""
    code = COUNTRY_CURRENCY.get((country or "").upper(), settings.settlement_currency)
    allowed = available_currencies()
    return code if code in allowed else settings.settlement_currency


@dataclass(frozen=True)
class DisplayAmount:
    """A converted amount, with the settlement truth attached.

    The pairing is the point: any code that receives this can show the player a
    local figure and still reason about the money, because the real amount is
    right there and is the one that must be posted to the ledger.
    """
    settlement_minor: int
    settlement_currency: str
    display_minor: int
    display_currency: str
    rate: float
    display_only: bool = True

    def as_dict(self) -> dict:
        return {
            "settlement": {"minor": self.settlement_minor, "currency": self.settlement_currency},
            "display": {
                "minor": self.display_minor,
                "currency": self.display_currency,
                "rate": self.rate,
                "decimals": currency_meta(self.display_currency)["minor"],
            },
            "display_only": self.display_only,
        }


def to_display(minor: int, currency: str) -> DisplayAmount:
    """Convert a settlement amount into a display amount.

    Rounding is half-up at the display currency's own precision; the result is
    for the eye only and is never written back. Crypto display currencies use
    their full precision, which is why this reads ``minor`` from the table
    instead of assuming two decimals.
    """
    settlement = settings.settlement_currency.upper()
    code = (currency or settlement).upper()
    meta = CURRENCIES.get(code)
    if meta is None or code not in rates():
        return DisplayAmount(minor, settlement, minor, settlement, 1.0)

    rate = rates()[code]
    # settlement minor units are always 10^-2; scale to the target precision.
    value = (minor / 100.0) * rate
    display_minor = int(round(value * (10 ** meta["minor"])))
    return DisplayAmount(minor, settlement, display_minor, code, rate)


def snapshot() -> dict:
    """Everything the frontend needs to localize, in one payload."""
    return {
        "settlement_currency": settings.settlement_currency.upper(),
        "default_locale": settings.default_locale.lower(),
        "locales": [
            {"code": code, **LOCALES[code]}
            for code in settings.locales
            if code in LOCALES
        ],
        "currencies": [
            {"code": code, **currency_meta(code)}
            for code in available_currencies()
        ],
        "country_currency": COUNTRY_CURRENCY,
        "country_locale": COUNTRY_LOCALE,
        "fx": {
            "base": settings.settlement_currency.upper(),
            "rates": rates(),
            "updated_at": settings.fx_rates_updated_at or None,
            "source": "configured" if settings.fx_rates.strip() else "built-in defaults",
            "problems": rates_problems(),
            # Repeated in the payload on purpose: the client renders money from
            # this table, so the caveat travels with it.
            "display_only": True,
            "note": (
                "Presentation rates only. Balances, stakes, limits and payouts "
                "are settled in " + settings.settlement_currency.upper() + "."
            ),
        },
    }
