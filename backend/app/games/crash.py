"""Crash - the shared live multiplayer round.

Round lifecycle, driven by a single asyncio task in `services.crash_loop`:

    BETTING  (7s)  players join, each with a private auto-cashout target
      -> the server seed commitment for the round was published at creation
    RUNNING  (up to ~40s) multiplier climbs on a fixed clock; players cash out
      -> every tick is broadcast over websocket
    CRASHED  the bust point is revealed, everyone still riding loses their stake
    SETTLED  payouts credited, round history written, next round created

The bust point is computed ONCE from the committed seed before the round opens,
so it is fixed in advance but unpredictable until reveal - the standard
provably-fair crash construction. See `rng.ProvablyFair.crash_point`.
"""
from __future__ import annotations

import math

HOUSE_EDGE = 0.01
MAX_MULT = 100_000.0

# Curve: multiplier(t) = growth ** t, tuned so a 60s round tops out near 1000x.
# A real deployment should keep this identical in server and client code so the
# animation can never disagree with settlement.
GROWTH_BASE = 1.0718


def multiplier_at(elapsed_s: float) -> float:
    """Deterministic curve. The server remains the only source of truth; the
    client animates from its own clock but settlement always uses server time."""
    m = GROWTH_BASE ** max(elapsed_s, 0.0)
    return min(round(m, 2), MAX_MULT)


def elapsed_for(multiplier: float) -> float:
    if multiplier <= 1:
        return 0.0
    return math.log(multiplier) / math.log(GROWTH_BASE)


def cashout_multiplier(crash_point: float, elapsed_s: float, requested: float | None) -> float:
    """Multiplier actually locked in for a cash-out request.

    Uses the server clock, never the client's, and never honours a request above
    the bust point (that would be free money for a laggy client).
    """
    current = multiplier_at(elapsed_s)
    if current >= crash_point:
        return 0.0                       # too late - the round already busted
    take = current if requested is None else min(requested, current)
    return max(round(take, 2), 1.0)
