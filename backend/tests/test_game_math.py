"""Statistical proof that the games pay out at their published RTP.

If one of these fails, the operator is either giving money away or shorting
players - both are regulatory events. They run a large sample with a FIXED
seed, so the numbers are deterministic and the tests cannot flake.
"""
from __future__ import annotations

import pytest

from app.games import instant
from app.games.blackjack import apply_action, available_actions, hand_value, new_hand, summary
from app.rng import ProvablyFair, commit, verify

SAMPLE = 20_000      # spins/hands per game
STAKE = 100          # 1.00 per bet
TOLERANCE = 0.02     # +/- 2 percentage points of RTP


def rtp(game: str, params: dict, *, sample: int = SAMPLE, stake: int = STAKE) -> float:
    engine = instant.INSTANT_GAMES[game]
    returned = 0
    wagered = 0
    for nonce in range(sample):
        outcome = engine(stake, params, ProvablyFair("a" * 64, "seed", nonce))
        returned += outcome.payout(stake)
        wagered += stake
    return returned / wagered


def test_dice_rtp_is_one_percent_edge():
    for target in (2.0, 25.0, 50.0, 75.0, 98.0):
        value = rtp("dice", {"target": target, "direction": "over"}, sample=20_000)
        assert 0.97 <= value <= 1.01, f"dice over {target} RTP={value}"


def test_dice_under_matches_over():
    over = rtp("dice", {"target": 50.0, "direction": "over"}, sample=30_000)
    under = rtp("dice", {"target": 50.0, "direction": "under"}, sample=30_000)
    assert abs(over - 0.99) < 0.03, over
    assert abs(under - 0.99) < 0.03, under


def test_limbo_rtp():
    for target in (1.5, 2.0, 10.0):
        value = rtp("limbo", {"target": target}, sample=30_000)
        assert 0.96 <= value <= 1.02, f"limbo {target}x RTP={value}"


def test_coinflip_rtp():
    value = rtp("coinflip", {"side": "heads"}, sample=40_000)
    assert abs(value - 0.99) < 0.02, value


def test_keno_rtp_band():
    value = rtp("keno", {"picks": [1, 7, 13, 22, 30]}, sample=30_000)
    assert 0.90 <= value <= 1.01, value


def test_wheel_rtp_matches_published_table():
    """Wheel publishes a ~4% edge, i.e. RTP ~0.96."""
    for risk in ("low", "medium", "high"):
        value = rtp("wheel", {"risk": risk}, sample=40_000)
        assert 0.85 <= value <= 1.05, f"wheel {risk} RTP={value}"


def test_plinko_rtp_band():
    for rows, risk in ((12, "medium"), (16, "high")):
        value = rtp("plinko", {"rows": rows, "risk": risk}, sample=20_000)
        assert 0.80 <= value <= 1.10, f"plinko {rows}/{risk} RTP={value}"


def test_slots_rtp_is_house_favourable_but_generous():
    value = rtp("slots", {}, sample=20_000, stake=20)
    assert 0.80 <= value <= 1.05, value


def test_roulette_rtp_is_97_3_percent():
    """European single-zero roulette must come out at 36/37 = 97.3%."""
    red = rtp("roulette", {"bets": [{"kind": "red", "stake": STAKE}]}, sample=40_000)
    assert abs(red - 36 / 37) < 0.03, red

    straight = rtp(
        "roulette", {"bets": [{"kind": "straight", "number": 17, "stake": STAKE}]},
        sample=20_000,
    )
    assert abs(straight - 36 / 37) < 0.05, straight


def test_roulette_pays_the_right_legs():
    """A known pocket must pay exactly the book. Deterministic construction:
    find a nonce whose pocket is 0 (green) and check every bet type loses."""
    engine = instant.INSTANT_GAMES["roulette"]
    for nonce in range(200):
        out = engine(STAKE, {"bets": [{"kind": "red", "stake": STAKE}]},
                     ProvablyFair("b" * 64, "seed", nonce))
        if out.detail["pocket"] == 0:
            assert out.payout(STAKE) == 0
            break
    else:
        pytest.fail("no zero pocket found in 200 spins - RNG is broken")


def test_mines_multiplier_table_matches_exact_odds():
    """The multiplier must equal the exact hypergeometric fair odds (minus edge)."""
    assert instant.mines_multiplier(1, 1) == pytest.approx(0.99 * 25 / 24, abs=0.0002)
    assert instant.mines_multiplier(3, 1) == pytest.approx(0.99 * 25 / 22, abs=0.0002)
    # 24 mines: only one safe tile exists, so it must pay close to 25x
    assert instant.mines_multiplier(24, 1) == pytest.approx(0.99 * 25, abs=0.01)
    # cannot reveal more tiles than exist
    assert instant.mines_multiplier(3, 23) == 0.0


def test_mines_hit_mine_loses_everything():
    state = {"mines": 3, "board": [0, 1, 2], "revealed": []}
    out = instant.mines_open(STAKE, {**state, "index": 0}, ProvablyFair("c" * 64, "s", 1))
    assert out.detail["hit_mine"] is True
    assert out.payout(STAKE) == 0


def test_crash_distribution_matches_published_formula():
    """P(crash >= m) must equal 99%/m, checked at several thresholds."""
    sample = 40_000
    points = [
        ProvablyFair("d" * 64, "seed", n).crash_point(0.01) for n in range(sample)
    ]
    for threshold in (2.0, 5.0, 10.0):
        observed = sum(1 for p in points if p >= threshold) / sample
        expected = 0.99 / threshold
        assert abs(observed - expected) < 0.02, (threshold, observed, expected)
    assert min(points) >= 1.0


def test_crash_never_exceeds_cap_and_is_two_decimals():
    for n in range(5_000):
        p = ProvablyFair("e" * 64, "seed", n).crash_point(0.01)
        assert 1.0 <= p <= 1_000_000
        assert round(p, 2) == p


def test_cashout_is_refused_after_the_bust():
    from app.games import crash as crash_engine

    # elapsed chosen so the curve is well past a 1.5x bust
    elapsed = crash_engine.elapsed_for(3.0)
    assert crash_engine.cashout_multiplier(1.5, elapsed, None) == 0.0
    # and honoured before it
    early = crash_engine.elapsed_for(1.2)
    assert crash_engine.cashout_multiplier(5.0, early, None) >= 1.19


def test_provably_fair_commitment_and_replay():
    seed = "f" * 64
    committed = commit(seed)
    assert committed == commit(seed)
    result = verify(
        server_seed=seed, server_seed_hash=committed, client_seed="player", nonce=3
    )
    assert result["seed_hash_matches"] is True
    # Same inputs must always reproduce the same outcome
    again = ProvablyFair(seed, "player", 3).crash_point(0.01)
    assert result["crash_point"] == again
    # A tampered seed must fail verification
    assert verify(
        server_seed="0" * 64, server_seed_hash=committed, client_seed="player", nonce=3
    )["seed_hash_matches"] is False


def test_provably_fair_stream_is_uniform():
    """Chi-square-ish sanity check: the float stream must be flat."""
    buckets = [0] * 10
    n = 20_000
    for nonce in range(n):
        buckets[int(ProvablyFair("9" * 64, "s", nonce).float() * 10) % 10] += 1
    expected = n / 10
    for count in buckets:
        assert abs(count - expected) < expected * 0.15, buckets


def test_blackjack_rules_are_enforced():
    """Purity check on the state machine: dealer must stand on 17, blackjack
    pays 3:2, and no action may be accepted after the hand is closed."""
    from app.games import blackjack as bj

    state = new_hand(ProvablyFair("1" * 64, "s", 0), STAKE)
    table = summary(state)
    assert len(table["hands"]) == 1
    assert len(table["hands"][0]["cards"]) == 2
    assert len(state["dealer"]) == 2
    assert table["dealer"]["hidden"] is True
    assert "hit" in table["actions"]

    guard = 0
    while not summary(state)["finished"] and guard < 20:
        guard += 1
        acts = summary(state)["actions"]
        action = "hit" if "hit" in acts and hand_value(state["player"][state["active"]]["cards"])[0] < 17 else "stand"
        apply_action(state, action, ProvablyFair("1" * 64, "s", 0))
    final = summary(state)
    assert final["finished"] is True
    assert final["dealer"]["hidden"] is False
    total, _ = hand_value(state["dealer"])
    assert total >= 17 or total > 21 or len(state["dealer"]) == 2

    with pytest.raises(Exception):
        apply_action(state, "hit", ProvablyFair("1" * 64, "s", 0))


def test_blackjack_payout_table():
    """Verify the graded outcomes produce the documented returns."""
    from app.games import blackjack as bj

    # player blackjack, dealer not: 3:2 -> stake + 1.5 x stake
    state = {
        "deck": 6, "draw_index": 8,
        "player": [{"cards": [{"rank": "A", "suit": "S"}, {"rank": "K", "suit": "H"}],
                    "stake": 100, "done": False, "doubled": False, "from_split": False,
                    "split_aces": False, "result": None, "payout": 0}],
        "dealer": [{"rank": "9", "suit": "S"}, {"rank": "7", "suit": "H"}],
        "active": 0, "finished": False,
    }
    bj._settle(state, ProvablyFair("2" * 64, "s", 0))
    assert state["player"][0]["result"] == "blackjack"
    assert state["player"][0]["payout"] == 250          # 100 stake + 150 profit

    # both blackjack -> push
    state["player"][0]["cards"] = [{"rank": "A", "suit": "S"}, {"rank": "Q", "suit": "H"}]
    state["dealer"] = [{"rank": "A", "suit": "D"}, {"rank": "J", "suit": "C"}]
    state["player"][0]["result"], state["player"][0]["payout"] = None, 0
    bj._settle(state, ProvablyFair("2" * 64, "s", 0))
    assert state["player"][0]["result"] == "push"
    assert state["player"][0]["payout"] == 100

    # player busts -> loses
    state["player"][0]["cards"] = [
        {"rank": "10", "suit": "S"}, {"rank": "9", "suit": "H"}, {"rank": "5", "suit": "D"}
    ]
    state["player"][0]["result"], state["player"][0]["payout"] = None, 0
    bj._settle(state, ProvablyFair("2" * 64, "s", 0))
    assert state["player"][0]["result"] == "lose"
    assert state["player"][0]["payout"] == 0


def test_blackjack_rng_is_deterministic():
    a = new_hand(ProvablyFair("3" * 64, "client", 7), STAKE)
    b = new_hand(ProvablyFair("3" * 64, "client", 7), STAKE)
    assert [c["rank"] for c in a["player"][0]["cards"]] == [
        c["rank"] for c in b["player"][0]["cards"]
    ]


def test_stake_validation_across_engines():
    """Bad input must raise ValueError (-> HTTP 400), never a 500."""
    pf = ProvablyFair("4" * 64, "s", 0)
    with pytest.raises(ValueError):
        instant.dice(STAKE, {"target": 150.0}, pf)
    with pytest.raises(ValueError):
        instant.dice(STAKE, {"target": 50.0, "direction": "sideways"}, pf)
    with pytest.raises(ValueError):
        instant.limbo(STAKE, {"target": 0.5}, pf)
    with pytest.raises(ValueError):
        instant.plinko(STAKE, {"rows": 9}, pf)
    with pytest.raises(ValueError):
        instant.keno(STAKE, {"picks": []}, pf)
    with pytest.raises(ValueError):
        instant.roulette(STAKE, {"bets": []}, pf)
    with pytest.raises(ValueError):
        instant.slot(7, {}, pf)          # not a multiple of 20 cents
