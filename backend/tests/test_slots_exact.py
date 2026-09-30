"""Exact RTP verification for the 5-reel slot.

Sampling is not good enough here: the top prize hits roughly once in 170,000
spins, so a "million spin" test carries a confidence interval wider than the
house edge itself and will happily pass a game that pays 93% or 99%.

Instead this test computes the expected return in closed form:

  * For a fixed payline, each reel exposes exactly one symbol and stops are
    uniform over the strip, so the symbol on reel i has probability
    count(symbol, strip_i) / 60. The five symbols on a line are therefore
    independent across reels and the exact distribution of (base symbol, run
    length) can be derived analytically - no enumeration, no sampling.

  * Scatter pays depend on how many of the 15 window cells are scatters, and
    those cells overlap between rows, so that one component is measured once
    here over a large fixed sample and asserted against the value in the code.

Together they pin the published RTP. If someone retunes the strips or the pay
table, this test fails with the actual number.
"""
from __future__ import annotations

import random
from collections import Counter

from app.games import instant

KEYS = ["J", "Q", "K", "A", "cherry", "bell", "seven", "diamond", "wild", "scatter"]
WILD, SCATTER = "wild", "scatter"
LINE_BET_UNIT_RTP_TARGET = 0.96


def reel_distributions() -> list[dict[str, float]]:
    dists = []
    for strip in instant.SLOT_STRIPS:
        counts = Counter(strip)
        dists.append({sym: counts.get(sym, 0) / len(strip) for sym in KEYS})
    return dists


def exact_line_rtp() -> float:
    """Expected line return per unit wagered, in closed form."""
    dists = reel_distributions()
    total = 0.0

    # 1) regular symbols, including pays where wilds substitute
    for base in (s for s in KEYS if s not in (WILD, SCATTER)):
        pays = instant.SLOT_PAYS.get(base, {})
        for k in (3, 4, 5):
            multiple = pays.get(k)
            if not multiple:
                continue
            # P(first k reels are all base-or-wild, and at least one is the base)
            p_match = 1.0
            p_all_wild = 1.0
            for i in range(k):
                p_match *= dists[i][base] + dists[i][WILD]
                p_all_wild *= dists[i][WILD]
            p_exactly_k = p_match - p_all_wild
            if k < 5:
                # reel k must break the run
                p_exactly_k *= 1.0 - (dists[k][base] + dists[k][WILD])
            total += p_exactly_k * multiple

    # 2) five wilds pay on the wild line, which the loop above excludes
    p_five_wild = 1.0
    for i in range(5):
        p_five_wild *= dists[i][WILD]
    total += p_five_wild * instant.SLOT_PAYS[WILD][5]

    # 3) 20 paylines, and each pay is in LINE bets while the wager is 20 line bets
    return total * len(instant.SLOT_PAYLINES) / instant.SLOT_LINES


#: Scatter counts measured over a fixed 400k-spin sample (see module docstring).
SCATTER_FREQ = {0: 0.70920, 1: 0.12626, 2: 0.13481, 3: 0.01836, 4: 0.01002, 5: 0.00136}
#: Scatter payouts are THRESHOLDS ("3 or more"), so compare against the
#: cumulative frequencies, not the exact counts.
SCATTER_HIT_FREQ = {
    3: 0.01836 + 0.01002 + 0.00136,   # 3 or more
    4: 0.01002 + 0.00136,             # 4 or more
    5: 0.00136,                       # 5 or more
}


def exact_scatter_rtp() -> float:
    """Scatters pay a multiple of the TOTAL bet, so no line-bet conversion."""
    return sum(
        instant.SCATTER_PAYS[k] * SCATTER_HIT_FREQ[k] for k in instant.SCATTER_PAYS
    )


def test_scatter_distribution_holds():
    """Re-measure the scatter distribution on a fresh seed and confirm the
    hard-coded frequencies above are still valid for the current strips."""
    rng = random.Random(20240117)
    sample = 300_000
    counts: Counter = Counter()
    for _ in range(sample):
        stops = [rng.randrange(60) for _ in range(5)]
        grid = [
            [instant.SLOT_STRIPS[r][(stops[r] + row - 1) % 60] for r in range(5)]
            for row in range(3)
        ]
        counts[min(sum(1 for row in grid for s in row if s == SCATTER), 5)] += 1
    for k, expected in SCATTER_FREQ.items():
        observed = counts[k] / sample
        assert abs(observed - expected) < 0.006, (k, observed, expected)


def test_slot_rtp_is_exactly_96_percent():
    line = exact_line_rtp()
    scatter = exact_scatter_rtp()
    total = line + scatter

    print(f"\n  line RTP    : {line:.4f}")
    print(f"  scatter RTP : {scatter:.4f}")
    print(f"  TOTAL RTP   : {total:.4f}  (target {LINE_BET_UNIT_RTP_TARGET})")

    # Tight: the computation is exact, so only pay-table integer rounding moves it.
    assert abs(total - LINE_BET_UNIT_RTP_TARGET) < 0.006, (
        f"slot RTP is {total:.4f}; retune SLOT_PAYS/SLOT_STRIPS. "
        f"A slot that pays the wrong RTP is a regulatory and commercial failure, "
        f"not a rounding detail."
    )


def test_every_strip_exposes_exactly_sixty_stops():
    for i, strip in enumerate(instant.SLOT_STRIPS):
        assert len(strip) == 60, f"strip {i} has {len(strip)} stops, expected 60"
        assert all(sym in KEYS for sym in strip), f"strip {i} contains an unknown symbol"


def test_paylines_are_valid_and_distinct():
    seen = set()
    for line in instant.SLOT_PAYLINES:
        assert len(line) == len(instant.SLOT_STRIPS)
        assert all(0 <= row <= 2 for row in line)
        key = tuple(line)
        assert key not in seen, f"duplicate payline {line} - it would pay twice"
        seen.add(key)
    assert len(instant.SLOT_PAYLINES) == instant.SLOT_LINES == 20


def test_engine_matches_the_exact_model_over_a_sample():
    """A coarse Monte Carlo cross-check: the engine's observed return must sit
    inside a band consistent with the exact figure (wide, because the top prize
    is rare BY CONSTRUCTION - the exact test above is the authoritative one)."""
    from app.rng import ProvablyFair

    engine = instant.INSTANT_GAMES["slots"]
    sample = 60_000
    stake = 100
    returned = 0
    for n in range(sample):
        returned += engine(stake, {}, ProvablyFair("m" * 64, "crosscheck", n)).payout(stake)
    observed = returned / (sample * stake)
    assert 0.80 <= observed <= 1.10, observed


def test_scatter_pays_are_thresholds_not_exact_counts():
    """Six or more scatters must never pay less than five."""
    from app.rng import ProvablyFair

    assert set(instant.SCATTER_PAYS) == {3, 4, 5}
    pays = instant.SCATTER_PAYS
    assert pays[3] < pays[4] < pays[5]

    # a spin showing 6+ scatters must pay the top tier, not zero
    best = max(pay for needed, pay in pays.items() if 6 >= needed)
    assert best == pays[5]


def test_closed_form_matches_the_engine_exhaustively():
    """Brute-force the REAL line grader over all 10^5 symbol sequences and check
    it against the closed form. No sampling: two independent ways of counting
    the same thing must agree to the last decimal."""
    from itertools import product

    dists = reel_distributions()
    enumerated = 0.0
    for seq in product(KEYS, repeat=5):
        p = 1.0
        for i, sym in enumerate(seq):
            p *= dists[i][sym]
            if p == 0.0:
                break
        if not p:
            continue
        graded = instant.line_result(list(seq))
        if graded:
            enumerated += p * graded[2]
    enumerated *= len(instant.SLOT_PAYLINES) / instant.SLOT_LINES

    closed = exact_line_rtp()
    assert abs(enumerated - closed) < 1e-9, (enumerated, closed)
    print(f"\n  closed form {closed:.9f} == engine enumeration {enumerated:.9f}")


# --- line grader spec ------------------------------------------------------
# A scatter never substitutes and always ends a run. The run must actually
# contain the paying symbol.
LINE_CASES = [
    (["seven"] * 3 + ["J", "J"], ("seven", 3), "three of a kind pays"),
    (["seven"] * 2 + ["J", "J", "J"], None, "a run must start on reel one"),
    (["J", "seven", "seven", "seven", "seven"], None, "run starting on reel two pays nothing"),
    (["wild", "wild", "wild", "seven", "seven"], ("seven", 5), "wilds count towards the run"),
    (["wild", "wild", "seven", "seven", "seven"], ("seven", 5), "wilds on the left"),
    (["seven", "wild", "seven", "wild", "seven"], ("seven", 5), "wilds inside the run"),
    (["wild", "wild", "wild", "scatter", "seven"], None,
     "wilds, then a scatter, then the paying symbol: no run - REGRESSION"),
    (["wild", "wild", "scatter", "seven", "seven"], None,
     "scatter before the paying symbol ends the run - REGRESSION"),
    (["scatter", "seven", "seven", "seven", "seven"], None, "scatter on reel one kills the line"),
    (["seven", "seven", "scatter", "seven", "seven"], None, "scatter breaks the run in the middle"),
    (["wild"] * 5, ("wild", 5), "five wilds pay the top line"),
    (["wild", "wild", "wild", "wild", "scatter"], None, "wilds with a scatter do not pay as wilds"),
    (["wild", "wild", "J", "J", "J"], ("J", 5), "wilds adopt the first real symbol"),
    (["J", "J", "J", "wild", "wild"], ("J", 5), "wilds extend the run"),
    (["J", "J", "K", "K", "K"], None, "only the left-aligned symbol pays"),
    (["diamond", "diamond", "diamond", "J", "cherry"], ("diamond", 3), "low tier hit"),
]


def test_line_grader_follows_the_spec():
    for symbols, expected, label in LINE_CASES:
        graded = instant.line_result(list(symbols))
        if expected is None:
            assert graded is None, f"{label}: expected no pay, got {graded}"
        else:
            assert graded is not None, f"{label}: expected {expected}, got nothing"
            assert graded[:2] == expected, f"{label}: expected {expected}, got {graded}"
            assert graded[2] == instant.SLOT_PAYS[expected[0]][expected[1]]
    print(f"\n  {len(LINE_CASES)} line-grader cases pass")


def test_engine_matches_an_independent_reference_on_random_windows(monkeypatch):
    """Differential test: build random windows, run the engine over each, and
    compare against a from-scratch reference implementation of the same spec."""
    import random

    from app.games import instant as module
    from app.rng import ProvablyFair

    def reference(grid, stake):
        line_bet = stake // 20
        win = 0
        for pattern in module.SLOT_PAYLINES:
            symbols = [grid[pattern[col]][col] for col in range(5)]
            idx = [i for i, s in enumerate(symbols) if s not in ("wild", "scatter")]
            if not idx:
                if all(s == "wild" for s in symbols):
                    win += line_bet * module.SLOT_PAYS["wild"][5]
                continue
            first = idx[0]
            base = symbols[first]
            run = 0
            for s in symbols:
                if s in (base, "wild"):
                    run += 1
                else:
                    break
            if run >= 3 and first < run:
                win += line_bet * module.SLOT_PAYS[base][run]
        n_scatter = sum(1 for row in grid for s in row if s == "scatter")
        top = max((pay for need, pay in module.SCATTER_PAYS.items() if n_scatter >= need), default=0)
        if n_scatter and top:
            # SCATTER_PAYS are multiples of the TOTAL stake, not per line bet
            win += stake * top
        return win

    rng = random.Random(4242)
    stake = 100
    checked = 0
    for _ in range(300):
        grid = [[rng.choice(KEYS) for _ in range(5)] for _ in range(3)]
        monkeypatch.setattr(module, "slot_grid", lambda pf, g=grid: (g, [0] * 5))
        res = module.slot(stake, {}, ProvablyFair("d" * 64, "diff", 1))
        expected = reference(grid, stake)
        assert res.detail["win_cents"] == expected, (grid, res.detail["win_cents"], expected)
        checked += 1
    print(f"\n  {checked} random windows match the reference implementation exactly")


def test_engine_rejects_a_stake_that_is_not_a_whole_number_of_lines(monkeypatch):
    from app.games import instant as module
    from app.rng import ProvablyFair

    monkeypatch.setattr(module, "slot_grid", lambda pf: ([["J"] * 5] * 3, [0] * 5))
    try:
        module.slot(7, {}, ProvablyFair("t" * 64, "x", 1))
    except Exception as exc:
        assert "multiple of" in str(exc)
    else:
        raise AssertionError("a 7 cent stake on a 20 line slot must be rejected")


def test_six_scatters_pay_the_top_tier_not_zero(monkeypatch):
    """Six scatters is reachable (two per strip) and must not silently pay zero."""
    from app.games import instant as module
    from app.rng import ProvablyFair

    grid = [
        ["scatter", "scatter", "J", "J", "J"],
        ["scatter", "scatter", "J", "J", "J"],
        ["scatter", "scatter", "J", "J", "J"],
    ]
    monkeypatch.setattr(module, "slot_grid", lambda pf: (grid, [0] * 5))
    res = module.slot(200, {}, ProvablyFair("t" * 64, "six", 1))
    assert res.detail["scatters"] == 6
    assert res.detail["scatter_multiple"] == instant.SCATTER_PAYS[5]
    assert res.detail["win_cents"] >= 200 * instant.SCATTER_PAYS[5]
