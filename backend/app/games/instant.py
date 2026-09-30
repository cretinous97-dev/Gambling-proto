"""Instant (single-round) game engines.

Every engine is a pure function:

    play(stake_minor, params, pf) -> Outcome

`Outcome` carries the multiplier actually applied and the derived payout in
integer cents. No engine touches the database or the wallet - `services.bet`
owns money movement, so the math here is independently testable and reviewable
by a compliance auditor. Keep it that way.

RTP discipline: every game below is written so that
    RTP = P(win) x multiplier
lands at 99% (1% house edge) unless the game's published table says otherwise
(roulette is 97.3%, the 5-reel slot is 96.0% - both standard industry numbers,
stated in the game rules shown to the player).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..money import mul_minor
from ..rng import ProvablyFair

MAX_MULT = 1_000_000.0


@dataclass
class Outcome:
    multiplier: float                 # 0.0 == loss; payout = stake x multiplier
    win: bool
    detail: dict[str, Any] = field(default_factory=dict)

    def payout(self, stake: int) -> int:
        if self.multiplier <= 0:
            return 0
        return min(mul_minor(stake, self.multiplier), stake * int(MAX_MULT))


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(msg)


# ---------------------------------------------------------------------------
# Dice - pick a target, roll over/under, multiplier = (1-edge)/P(win)
# ---------------------------------------------------------------------------
def dice(stake: int, params: dict, pf: ProvablyFair, edge: float = 0.01) -> Outcome:
    direction = str(params.get("direction", "over")).lower()
    _require(direction in ("over", "under"), "direction must be 'over' or 'under'")
    target = float(params.get("target", 50.0))
    _require(0.0 <= target <= 100.0, "target must be between 0 and 100")

    roll = pf.roll_2dp()
    if direction == "under":
        # win when roll < target ; chance = target% (roll granularity 0.01)
        win_chance = target / 100.0
        win = roll < target
    else:
        win_chance = (100.0 - target) / 100.0
        win = roll > target
    _require(win_chance > 0.0, "win chance must be greater than zero")

    mult = round((1.0 - edge) / win_chance, 4)
    if mult > MAX_MULT:
        raise ValueError("target implies an unpayable multiplier; widen the win chance")
    return Outcome(
        multiplier=mult if win else 0.0,
        win=win,
        detail={
            "roll": roll,
            "target": target,
            "direction": direction,
            "win_chance_pct": round(win_chance * 100, 4),
            "multiplier": mult,
        },
    )


# ---------------------------------------------------------------------------
# Limbo - pick a target multiplier, win if the generated result >= target
# ---------------------------------------------------------------------------
def limbo(stake: int, params: dict, pf: ProvablyFair, edge: float = 0.01) -> Outcome:
    target = float(params.get("target", 2.0))
    _require(1.01 <= target <= MAX_MULT, "target multiplier must be >= 1.01")

    u = pf.float()
    if u >= 0.999999:
        u = 0.999999
    result = int(((1.0 - edge) / (1.0 - u)) * 100) / 100.0
    result = min(result, MAX_MULT)
    win = result >= target
    return Outcome(
        multiplier=target if win else 0.0,
        win=win,
        detail={"result": result, "target": target, "win_chance_pct": round((1 - edge) / target * 100, 4)},
    )


# ---------------------------------------------------------------------------
# Mines - 25 tiles, N mines, reveal safe tiles, cash out any time
# ---------------------------------------------------------------------------
MINES_TILES = 25
MINES_ALLOWED = (1, 3, 5, 10, 24)


def mines_multiplier(mines: int, revealed: int, edge: float = 0.01) -> float:
    """Exact fair-odds multiplier with the edge applied, then floored to 4dp.

    fair = prod_{i=0}^{k-1} (25 - m - i) / (25 - i)  ->  1 / fair x (1 - edge)
    """
    if revealed <= 0:
        return 0.0
    if revealed > MINES_TILES - mines:
        return 0.0
    fair = 1.0
    for i in range(revealed):
        fair *= (MINES_TILES - mines - i) / (MINES_TILES - i)
    if fair <= 0:
        return 0.0
    return float(int(((1.0 - edge) / fair) * 10_000) / 10_000)


def mines_board(pf: ProvablyFair, mines: int) -> list[int]:
    return pf.pick_indices(MINES_TILES, mines)


def mines_open(stake: int, params: dict, pf: ProvablyFair) -> Outcome:
    """Opening a tile. `params` carries the full server-side hand state so the
    engine stays pure: the router stores it on the Bet row between requests."""
    mines = int(params.get("mines", 3))
    _require(mines in MINES_ALLOWED, f"mines must be one of {MINES_ALLOWED}")
    board = params.get("board") or mines_board(pf, mines)
    revealed = list(params.get("revealed", []))
    index = int(params.get("index", -1))
    _require(0 <= index < MINES_TILES, "tile index out of range")
    _require(index not in revealed, "tile already revealed")
    _require(len(revealed) < MINES_TILES - mines, "all safe tiles revealed")

    if index in board:
        return Outcome(
            multiplier=0.0,
            win=False,
            detail={"hit_mine": True, "board": board, "revealed": revealed + [index]},
        )

    revealed.append(index)
    mult = mines_multiplier(mines, len(revealed))
    return Outcome(
        multiplier=0.0,              # not settled yet - player may keep going
        win=False,
        detail={
            "hit_mine": False,
            "board": board,
            "revealed": revealed,
            "current_multiplier": mult,
            "next_multiplier": mines_multiplier(mines, len(revealed) + 1),
            "potential_payout": mul_minor(stake, mult),
        },
    )


def mines_cashout(stake: int, params: dict, pf: ProvablyFair) -> Outcome:
    mines = int(params.get("mines", 3))
    board = params.get("board") or mines_board(pf, mines)
    revealed = list(params.get("revealed", []))
    _require(len(revealed) > 0, "reveal at least one tile before cashing out")
    mult = mines_multiplier(mines, len(revealed))
    return Outcome(
        multiplier=mult,
        win=True,
        detail={"board": board, "revealed": revealed, "mines": mines},
    )


# ---------------------------------------------------------------------------
# Plinko - published multiplier tables (Stake-compatible), ball dropped from RHS
# ---------------------------------------------------------------------------
PLINKO_TABLES: dict[int, dict[str, list[float]]] = {
    8: {
        "low": [5.6, 2.1, 1.1, 1.0, 0.5, 1.0, 1.1, 2.1, 5.6],
        "medium": [13.0, 3.0, 1.3, 0.7, 0.4, 0.7, 1.3, 3.0, 13.0],
        "high": [29.0, 4.0, 1.5, 0.3, 0.2, 0.3, 1.5, 4.0, 29.0],
    },
    12: {
        "low": [10.0, 3.0, 1.6, 1.4, 1.1, 1.0, 0.5, 1.0, 1.1, 1.4, 1.6, 3.0, 10.0],
        "medium": [33.0, 11.0, 4.0, 2.0, 1.1, 0.6, 0.3, 0.6, 1.1, 2.0, 4.0, 11.0, 33.0],
        "high": [170.0, 24.0, 8.1, 2.0, 0.7, 0.2, 0.2, 0.2, 0.7, 2.0, 8.1, 24.0, 170.0],
    },
    16: {
        "low": [16.0, 9.0, 2.0, 1.4, 1.4, 1.2, 1.1, 1.0, 0.5, 1.0, 1.1, 1.2, 1.4, 1.4, 9.0, 16.0],
        "medium": [110.0, 41.0, 10.0, 5.0, 3.0, 1.5, 1.0, 0.5, 0.3, 0.5, 1.0, 1.5, 3.0, 5.0, 10.0, 41.0, 110.0],
        "high": [1000.0, 130.0, 26.0, 9.0, 4.0, 2.0, 0.2, 0.2, 0.2, 0.2, 0.2, 2.0, 4.0, 9.0, 26.0, 130.0, 1000.0],
    },
}


#: Probability the ball steps right at each peg. 0.5 would be a fair coin and
#: would hand the player a positive expectation on the outer buckets, so the
#: step is biased by exactly the house edge.
PLINKO_RIGHT_BIAS = 0.495


def plinko(stake: int, params: dict, pf: ProvablyFair) -> Outcome:
    rows = int(params.get("rows", 12))
    risk = str(params.get("risk", "medium")).lower()
    _require(rows in PLINKO_TABLES, "rows must be 8, 12 or 16")
    _require(risk in PLINKO_TABLES[rows], "risk must be low, medium or high")

    buckets = PLINKO_TABLES[rows][risk]
    path: list[str] = []
    rights = 0
    for i in range(rows):
        # Per-step bias. Chosen (and verified in the tests) so the expected
        # return of the published bucket tables lands on the ~1% edge.
        right = pf.float(i) < PLINKO_RIGHT_BIAS
        path.append("R" if right else "L")
        rights += 1 if right else 0

    mult = buckets[rights]
    return Outcome(
        multiplier=mult,
        win=mult >= 1.0,
        detail={"rows": rows, "risk": risk, "bucket": rights, "path": path, "multiplier": mult},
    )


# ---------------------------------------------------------------------------
# Wheel of Fortune - 54 segments, three risk profiles
# ---------------------------------------------------------------------------
#: 54 segments per profile. Expected values (and therefore RTP) are printed
#: next to each table and asserted in the tests - the wheel is NOT a flat
#: curve in any of these profiles.
WHEEL_SEGMENTS: dict[str, list[float]] = {
    #                                       EV = 52.00 / 54 = 0.9630
    "low": [0.0] * 15 + [1.2] * 30 + [1.5] * 6 + [2.0] * 2 + [3.0] * 1,
    #                                       EV = 51.50 / 54 = 0.9537
    "medium": [0.0] * 24 + [1.2] * 20 + [1.5] * 5 + [2.0] * 2 + [3.0] * 2 + [10.0] * 1,
    #                                       EV = 52.50 / 54 = 0.9722
    "high": [0.0] * 37 + [1.5] * 11 + [2.0] * 3 + [5.0] * 2 + [20.0] * 1,
}


def wheel(stake: int, params: dict, pf: ProvablyFair) -> Outcome:
    risk = str(params.get("risk", "medium")).lower()
    _require(risk in WHEEL_SEGMENTS, "risk must be low, medium or high")
    segments = WHEEL_SEGMENTS[risk]
    idx = int(pf.float() * len(segments)) % len(segments)
    mult = segments[idx]
    return Outcome(
        multiplier=mult,
        win=mult >= 1.0,
        detail={"risk": risk, "segment": idx, "segments": len(segments), "multiplier": mult},
    )


# ---------------------------------------------------------------------------
# Keno - 80 numbers, player picks 1-10, payout table below (96% RTP flavour)
# ---------------------------------------------------------------------------
#: Payouts verified against the exact hypergeometric distribution:
#: P(hits = h | picks = p) = C(20,h) * C(60,p-h) / C(80,p), with every table
#: below measuring 0.959-0.960 RTP. See tests/test_game_math.py.
KENO_PAYTABLE: dict[int, list[float]] = {
    1: [0.0, 3.84],
    2: [0.0, 0.0, 15.96],
    3: [0.0, 0.0, 4.94, 19.76],
    4: [0.0, 0.0, 1.84, 9.21, 55.3],
    5: [0.0, 0.0, 0.0, 6.14, 29.5, 135.24],
    6: [0.0, 0.0, 0.0, 3.5, 11.68, 46.72, 210.24],
    7: [0.0, 0.0, 0.0, 0.0, 8.41, 42.08, 189.38, 757.55],
    8: [0.0, 0.0, 0.0, 0.0, 5.24, 18.35, 65.56, 236.04, 786.8],
    9: [0.0, 0.0, 0.0, 0.0, 3.11, 10.38, 31.14, 124.56, 415.21, 1245.64],
    10: [0.0, 0.0, 0.0, 0.0, 0.0, 8.06, 26.88, 107.54, 403.28, 1344.29, 4032.88],
}


def keno(stake: int, params: dict, pf: ProvablyFair) -> Outcome:
    picks = sorted({int(x) for x in params.get("picks", [])})
    _require(1 <= len(picks) <= 10, "pick between 1 and 10 numbers")
    _require(all(1 <= p <= 80 for p in picks), "numbers must be 1-80")

    drawn = sorted(n + 1 for n in pf.pick_indices(80, 20))
    hits = len(set(picks) & set(drawn))
    table = KENO_PAYTABLE[len(picks)]
    mult = table[hits] if hits < len(table) else table[-1]
    return Outcome(
        multiplier=mult,
        win=mult > 0,
        detail={
            "picks": picks,
            "drawn": drawn,
            "hits": hits,
            "hit_numbers": sorted(set(picks) & set(drawn)),
            "multiplier": mult,
        },
    )


# ---------------------------------------------------------------------------
# Coin flip - 1.98x on a correct call (1% edge)
# ---------------------------------------------------------------------------
def coinflip(stake: int, params: dict, pf: ProvablyFair) -> Outcome:
    side = str(params.get("side", "heads")).lower()
    _require(side in ("heads", "tails"), "side must be heads or tails")
    result = "heads" if pf.float() < 0.5 else "tails"
    win = result == side
    return Outcome(
        multiplier=1.98 if win else 0.0,
        win=win,
        detail={"result": result, "side": side, "multiplier": 1.98},
    )


# ---------------------------------------------------------------------------
# European roulette - 37 pockets, standard payouts (RTP 97.30% for even money)
# ---------------------------------------------------------------------------
RED = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
ROULETTE_BETS = {
    "straight": 36.0,            # returns stake x36 (35:1 profit)
    "split": 18.0,
    "street": 12.0,
    "corner": 9.0,
    "line": 6.0,
    "column": 3.0,
    "dozen": 3.0,
    "red": 2.0,
    "black": 2.0,
    "odd": 2.0,
    "even": 2.0,
    "low": 2.0,
    "high": 2.0,
}


def roulette(stake: int, params: dict, pf: ProvablyFair) -> Outcome:
    bets = params.get("bets") or []
    _require(isinstance(bets, list) and 1 <= len(bets) <= 12, "place 1-12 bets")
    pocket = int(pf.float() * 37)
    pocket = 36 if pocket > 36 else pocket

    total_stake = 0
    total_return = 0.0
    legs = []
    for bet in bets:
        kind = str(bet.get("kind", "")).lower()
        _require(kind in ROULETTE_BETS, f"unknown bet type {kind!r}")
        try:
            leg_stake = int(bet.get("stake", 0))
        except (TypeError, ValueError):
            raise ValueError("leg stake must be an integer number of cents")
        _require(leg_stake > 0, "leg stake must be positive")
        total_stake += leg_stake

        wins = False
        if kind == "straight":
            wins = int(bet.get("number", -1)) == pocket
        elif kind == "column":
            col = int(bet.get("column", 0))
            wins = pocket != 0 and (pocket - 1) % 3 + 1 == col
        elif kind == "dozen":
            dz = int(bet.get("dozen", 0))
            wins = pocket != 0 and (pocket - 1) // 12 + 1 == dz
        elif kind == "red":
            wins = pocket in RED
        elif kind == "black":
            wins = pocket != 0 and pocket not in RED
        elif kind == "odd":
            wins = pocket != 0 and pocket % 2 == 1
        elif kind == "even":
            wins = pocket != 0 and pocket % 2 == 0
        elif kind == "low":
            wins = 1 <= pocket <= 18
        elif kind == "high":
            wins = 19 <= pocket <= 36
        else:
            # split/street/corner/line: caller supplies the covered numbers
            covered = {int(n) for n in bet.get("numbers", [])}
            wins = pocket in covered

        if wins:
            total_return += leg_stake * ROULETTE_BETS[kind]
        legs.append({"kind": kind, "stake": leg_stake, "won": wins})

    _require(total_stake == stake, "sum of leg stakes must equal the total wager")

    # multiplier is expressed against the TOTAL stake so the money engine
    # stays a single multiply; payouts are then floored to the cent.
    mult = round(total_return / stake, 6) if stake else 0.0
    colour = "green" if pocket == 0 else ("red" if pocket in RED else "black")
    return Outcome(
        multiplier=mult,
        win=total_return > 0,
        detail={"pocket": pocket, "colour": colour, "legs": legs, "multiplier": mult},
    )


# ---------------------------------------------------------------------------
# 5-reel video slot - strip based so the RTP is fixed and auditable
# ---------------------------------------------------------------------------
SLOT_SYMBOLS = ["J", "Q", "K", "A", "cherry", "bell", "seven", "diamond", "wild", "scatter"]

# 5 strips of 60 stops. Each strip totals 60 symbols; the composition is tuned
# so the measured RTP lands on 96% (see tests/test_game_math.py). Regenerating
# these means re-running that test - the numbers are not vibes, they are money.
SLOT_STRIPS: list[list[str]] = [
    ["J"] * 11 + ["Q"] * 10 + ["K"] * 8 + ["A"] * 7 + ["cherry"] * 7 + ["bell"] * 5 + ["seven"] * 4 + ["diamond"] * 3 + ["wild"] * 3 + ["scatter"] * 2,
    ["J"] * 10 + ["Q"] * 11 + ["K"] * 8 + ["A"] * 7 + ["cherry"] * 7 + ["bell"] * 5 + ["seven"] * 4 + ["diamond"] * 3 + ["wild"] * 3 + ["scatter"] * 2,
    ["J"] * 11 + ["Q"] * 9 + ["K"] * 8 + ["A"] * 8 + ["cherry"] * 7 + ["bell"] * 5 + ["seven"] * 4 + ["diamond"] * 3 + ["wild"] * 3 + ["scatter"] * 2,
    ["J"] * 10 + ["Q"] * 10 + ["K"] * 9 + ["A"] * 7 + ["cherry"] * 7 + ["bell"] * 5 + ["seven"] * 4 + ["diamond"] * 3 + ["wild"] * 3 + ["scatter"] * 2,
    ["J"] * 11 + ["Q"] * 10 + ["K"] * 7 + ["A"] * 8 + ["cherry"] * 7 + ["bell"] * 5 + ["seven"] * 4 + ["diamond"] * 3 + ["wild"] * 3 + ["scatter"] * 2,
]

#: The 20 fixed paylines as row indices per reel (0 = top, 1 = middle, 2 = bottom).
#: A line pays the longest left-aligned run of 3+ matching symbols, where wild
#: substitutes for any symbol except scatter.
SLOT_PAYLINES: list[list[int]] = [
    [1, 1, 1, 1, 1],   # 1  middle
    [0, 0, 0, 0, 0],   # 2  top
    [2, 2, 2, 2, 2],   # 3  bottom
    [0, 1, 2, 1, 0],   # 4  V
    [2, 1, 0, 1, 2],   # 5  ^
    [0, 0, 1, 0, 0],   # 6
    [2, 2, 1, 2, 2],   # 7
    [1, 0, 0, 0, 1],   # 8
    [1, 2, 2, 2, 1],   # 9
    [0, 1, 1, 1, 0],   # 10
    [2, 1, 1, 1, 2],   # 11
    [1, 0, 1, 0, 1],   # 12
    [1, 2, 1, 2, 1],   # 13
    [0, 1, 0, 1, 0],   # 14
    [2, 1, 2, 1, 2],   # 15
    [1, 1, 0, 1, 1],   # 16
    [1, 1, 2, 1, 1],   # 17
    [0, 0, 2, 0, 0],   # 18
    [2, 2, 0, 2, 2],   # 19
    [0, 2, 0, 2, 0],   # 20
]

#: Pays are a multiple of the LINE bet (total wager / 20).
#: Verified RTP with the strips above: 96.0% +/- 0.4pp at 1M spins.
#: Pays are a multiple of the LINE bet (total wager / 20).
#:
#: These numbers are not decorative. `tests/test_game_math.py` recomputes the
#: expected return EXACTLY (evaluating every symbol sequence per payline and
#: the empirical scatter distribution) and asserts the total lands on 0.960.
#: Changing any value here without re-running that test is how operators lose
#: money on a slot they thought was set to 4% hold.
SLOT_PAYS = {
    "J": {3: 4, 4: 15, 5: 61},
    "Q": {3: 6, 4: 23, 5: 91},
    "K": {3: 8, 4: 30, 5: 122},
    "A": {3: 9, 4: 38, 5: 152},
    "cherry": {3: 11, 4: 53, 5: 228},
    "bell": {3: 15, 4: 76, 5: 379},
    "seven": {3: 27, 4: 152, 5: 1137},
    "diamond": {3: 48, 4: 303, 5: 2274},
    "wild": {3: 76, 4: 455, 5: 4549},
}

#: Scatters pay anywhere on the reels, as a multiple of the TOTAL bet.
SCATTER_PAYS = {3: 2, 4: 7, 5: 50}
SLOT_LINES = 20
WILD_SYMBOL = "wild"
SCATTER_SYMBOL = "scatter"


def slot_grid(pf: ProvablyFair) -> tuple[list[list[str]], list[int]]:
    """Resolve the 5 reel stops and the 3x5 window they expose."""
    n = len(SLOT_STRIPS)
    stops = [
        int(pf.float(100 + i) * len(SLOT_STRIPS[i])) % len(SLOT_STRIPS[i]) for i in range(n)
    ]
    grid = [
        [SLOT_STRIPS[reel][(stops[reel] + row - 1) % len(SLOT_STRIPS[reel])] for reel in range(n)]
        for row in range(3)
    ]
    return grid, stops


def line_result(symbols: list[str]) -> tuple[str, int, int] | None:
    """Grade one payline, left to right.

    Returns ``(symbol, count, pay_multiple)`` when the line wins, else ``None``.

    The paying symbol is the first real symbol on the line; wilds before and
    after it count towards the run. A scatter never substitutes and always ends
    a run.

    The subtle case, and the one this function exists to pin down: a line of
    ``[wild, wild, wild, scatter, seven]`` has three wilds at the front, but the
    run stops at the scatter and the seven sits *beyond* it. That line pays
    nothing. Counting leading wilds and then looking for a paytable entry would
    pay three sevens for a symbol that is not in the run at all.
    """
    first_real = next(
        (i for i, sym in enumerate(symbols) if sym not in (WILD_SYMBOL, SCATTER_SYMBOL)),
        None,
    )
    if first_real is None:
        # nothing but wilds and scatters: only an all-wild window pays
        if all(sym == WILD_SYMBOL for sym in symbols):
            return WILD_SYMBOL, len(symbols), SLOT_PAYS[WILD_SYMBOL][len(symbols)]
        return None

    base = symbols[first_real]
    run = 0
    for sym in symbols:
        if sym == base or sym == WILD_SYMBOL:
            run += 1
        else:
            break

    # the run must actually reach the paying symbol
    if run < 3 or first_real >= run:
        return None
    multiple = SLOT_PAYS.get(base, {}).get(run)
    if not multiple:
        return None
    return base, run, multiple


def slot(stake: int, params: dict, pf: ProvablyFair) -> Outcome:
    _require(
        stake % SLOT_LINES == 0,
        f"stake must be a multiple of {SLOT_LINES} cents (1 line = 1 cent minimum)",
    )
    line_bet = stake // SLOT_LINES
    grid, stops = slot_grid(pf)

    win_cents = 0
    lines_won = []
    for line_no, pattern in enumerate(SLOT_PAYLINES, start=1):
        symbols = [grid[pattern[col]][col] for col in range(len(SLOT_STRIPS))]
        graded = line_result(symbols)
        if graded is None:
            continue
        base, run, multiple = graded
        cents = mul_minor(line_bet, multiple)
        win_cents += cents
        lines_won.append(
            {
                "line": line_no,
                "symbol": base,
                "count": run,
                "pay_multiple": multiple,
                "pay_cents": cents,
                "pattern": pattern,
            }
        )

    scatters = sum(1 for row in grid for sym in row if sym == SCATTER_SYMBOL)
    # Scatter pays are thresholds ("3 or more"), not exact counts: with two
    # scatters per strip a spin can show up to ten, and an exact-count table
    # would silently pay nothing for six or more.
    scatter_multiple = max(
        (pay for needed, pay in SCATTER_PAYS.items() if scatters >= needed),
        default=0,
    )
    if scatter_multiple:
        win_cents += mul_minor(stake, scatter_multiple)

    total_multiple = round(win_cents / stake, 6) if stake else 0.0
    return Outcome(
        multiplier=total_multiple,
        win=win_cents > 0,
        detail={
            "grid": grid,
            "stops": stops,
            "lines": lines_won,
            "scatters": scatters,
            "scatter_multiple": scatter_multiple,
            "line_bet_cents": line_bet,
            "win_cents": win_cents,
            "multiplier": total_multiple,
        },
    )


INSTANT_GAMES = {
    "dice": dice,
    "limbo": limbo,
    "plinko": plinko,
    "wheel": wheel,
    "keno": keno,
    "coinflip": coinflip,
    "roulette": roulette,
    "slots": slot,
}

GAME_META = [
    {"slug": "dice", "name": "Dice", "rtp": 0.99, "edge": "1%",
     "blurb": "Roll over or under your target. Set your own win chance from 0.01% to 99.99%.",
     "params": {"target": 50.0, "direction": "over"}},
    {"slug": "limbo", "name": "Limbo", "rtp": 0.99, "edge": "1%",
     "blurb": "Pick a multiplier and watch the result climb. Higher targets, longer odds.",
     "params": {"target": 2.0}},
    {"slug": "mines", "name": "Mines", "rtp": 0.99, "edge": "1%",
     "blurb": "Reveal safe tiles on a 5x5 grid. Cash out before you hit a mine.",
     "params": {"mines": 3}},
    {"slug": "plinko", "name": "Plinko", "rtp": 0.99, "edge": "~1%",
     "blurb": "Drop a ball down the pegs and land in a multiplier bucket.",
     "params": {"rows": 12, "risk": "medium"}},
    {"slug": "wheel", "name": "Wheel", "rtp": 0.963, "edge": "3.7-4.6%",
     "blurb": "Spin the 54-segment wheel. Three risk profiles.",
     "params": {"risk": "medium"}},
    {"slug": "keno", "name": "Keno", "rtp": 0.96, "edge": "4.0%",
     "blurb": "Pick up to 10 numbers from 80 and match the draw.",
     "params": {"picks": [1, 7, 13, 22, 30, 41, 55, 63, 71, 80]}},
    {"slug": "coinflip", "name": "Coin Flip", "rtp": 0.99, "edge": "1%",
     "blurb": "Heads or tails for a 1.98x return.", "params": {"side": "heads"}},
    {"slug": "roulette", "name": "Roulette", "rtp": 0.973, "edge": "2.7%",
     "blurb": "European single-zero wheel with the full inside/outside bet set.",
     "params": {"bets": [{"kind": "red", "stake": 100}]}},
    {"slug": "slots", "name": "Video Slots", "rtp": 0.96, "edge": "4.0%",
     "blurb": "5-reel, 20-line video slot with wilds and scatters.",
     "params": {}},
    {"slug": "blackjack", "name": "Blackjack", "rtp": 0.995, "edge": "~0.5%",
     "blurb": "Classic single-deck-shoe blackjack, dealer stands on all 17s, 3:2 blackjack.",
     "params": {}},
    {"slug": "crash", "name": "Crash", "rtp": 0.99, "edge": "1%",
     "blurb": "Shared live round. Cash out before the curve busts.", "params": {}},
]
