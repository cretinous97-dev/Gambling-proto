"""Integer money helpers.

RULES (do not break these):
  * All monetary values stored and computed in the DB are INTEGER minor units
    (cents). Never floats, never Decimal columns.
  * Decimals/floats are only used at the edges: parsing user input and
    formatting for display, immediately converted with `to_minor`.
  * Multiplications (bet * multiplier, etc.) use Decimal and ROUND_DOWN so the
    house can never be short-changed by rounding in the player's favour.
"""
from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation

CENTS_PER_UNIT = 100
SUPPORTED_CURRENCIES = ("USD", "BTN", "EUR", "INR")
CURRENCY_SYMBOL = {"USD": "$", "BTN": "Nu.", "EUR": "€", "INR": "₹"}
MINOR_PER_UNIT = {"USD": 100, "BTN": 100, "EUR": 100, "INR": 100}

# Indicative display-only rates (base USD). Settlement is always in USD cents.
DISPLAY_RATES = {"USD": 1.0, "BTN": 83.0, "EUR": 0.92, "INR": 83.0}


class MoneyError(ValueError):
    pass


def to_minor(value: str | int | float | Decimal, currency: str = "USD") -> int:
    """Convert a human amount ('12.34') to integer minor units (1234)."""
    if currency not in MINOR_PER_UNIT:
        raise MoneyError(f"unsupported currency {currency}")
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MoneyError(f"not a valid amount: {value!r}") from exc
    if not d.is_finite():
        raise MoneyError("amount must be finite")
    factor = Decimal(MINOR_PER_UNIT[currency])
    return int((d * factor).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def from_minor(minor: int, currency: str = "USD") -> Decimal:
    return (Decimal(int(minor)) / Decimal(MINOR_PER_UNIT[currency])).quantize(
        Decimal("0.01")
    )


def fmt(minor: int, currency: str = "USD") -> str:
    sym = CURRENCY_SYMBOL.get(currency, "$")
    return f"{sym}{from_minor(minor, currency):,.2f}"


def amount_from_str(value: str) -> int:
    """User input -> USD cents. Used by deposit/withdraw request schemas."""
    return to_minor(value, "USD")


def mul_minor(minor: int, multiplier: float | Decimal | str) -> int:
    """multiplier applied to an integer minor amount, rounded DOWN.

    ROUND_DOWN on payouts means the operator never overpays by a fraction of a
    cent; players are told this in the game rules.
    """
    mult = Decimal(str(multiplier))
    if mult < 0:
        raise MoneyError("multiplier must be non-negative")
    return int((Decimal(int(minor)) * mult).quantize(Decimal("1"), rounding=ROUND_DOWN))


def pct_of(minor: int, pct: float | Decimal | str) -> int:
    return int(
        (Decimal(int(minor)) * Decimal(str(pct)) / Decimal(100)).quantize(
            Decimal("1"), rounding=ROUND_DOWN
        )
    )


def usd_to_float(minor: int) -> float:
    """Only for config comparisons and display. Never for arithmetic."""
    return float(from_minor(minor))


def bump(x: float | str, currency: str) -> int:
    """Config float (in dollars) -> minor units."""
    return to_minor(x, currency)
