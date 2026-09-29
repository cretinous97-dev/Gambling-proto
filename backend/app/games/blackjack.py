"""Blackjack - stateful table game.

Rules implemented (the "Vegas strip" default, all stated to the player in the
UI so there is no dispute at pay-out time):

  * 6-deck shoe, reshuffled every hand from the provably-fair stream (so every
    hand is independently verifiable rather than depending on shuffle history).
  * Dealer stands on all 17s, including soft 17 (S17).
  * Blackjack pays 3:2, player blackjack beats dealer blackjack, push if both.
  * Double down on any first two cards, double after split allowed.
  * Split up to 4 hands; split aces receive one card each and cannot be hit.
  * No insurance / surrender / even-money (kept out deliberately - they are
    extra house-edge levers and add audit surface for no product benefit here).

The engine is a pure state machine over a JSON-able dict. The router persists
that dict on the Bet row between requests, so nothing about the hand lives only
in memory and a server restart cannot corrupt an open hand.
"""
from __future__ import annotations

from typing import Any

from ..rng import ProvablyFair

RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
SUITS = ["S", "H", "D", "C"]
DECKS = 6
MAX_HANDS = 4
BLACKJACK_PAYS = 1.5          # 3:2 profit
DEALER_STANDS_ON = 17


class BlackjackError(ValueError):
    pass


def _draw(pf: ProvablyFair, draw_index: int) -> dict[str, Any]:
    """Pull the next card off the stream. Each draw consumes 2 floats."""
    rank_f = pf.float(draw_index * 2)
    suit_f = pf.float(draw_index * 2 + 1)
    rank = RANKS[int(rank_f * 13) % 13]
    suit = SUITS[int(suit_f * 4) % 4]
    return {"rank": rank, "suit": suit}


def card_value(rank: str) -> int:
    if rank == "A":
        return 11
    if rank in ("J", "Q", "K", "10"):
        return 10
    return int(rank)


def hand_value(cards: list[dict]) -> tuple[int, bool]:
    """Return (best_total, is_soft). Aces demote from 11 to 1 as needed."""
    total = sum(card_value(c["rank"]) for c in cards)
    aces = sum(1 for c in cards if c["rank"] == "A")
    soft = aces > 0
    while total > 21 and aces:
        total -= 10
        aces -= 1
        soft = aces > 0
    return total, soft


def is_blackjack(cards: list[dict]) -> bool:
    return len(cards) == 2 and hand_value(cards)[0] == 21


def is_bust(cards: list[dict]) -> bool:
    return hand_value(cards)[0] > 21


def _new_hand(stake: int) -> dict[str, Any]:
    return {"cards": [], "stake": stake, "done": False, "doubled": False,
            "from_split": False, "split_aces": False, "result": None, "payout": 0}


def new_hand(pf: ProvablyFair, stake: int) -> dict[str, Any]:
    """Deal the opening hand: player, dealer, player, dealer (US order)."""
    if stake <= 0:
        raise BlackjackError("stake must be positive")
    state: dict[str, Any] = {
        "deck": DECKS,
        "draw_index": 0,
        "player": [_new_hand(stake)],
        "dealer": [],
        "active": 0,
        "finished": False,
        "peeked": False,
    }
    _hit(state, pf, 0)                       # player
    _dealer_hit(state, pf)                   # dealer hole card
    _hit(state, pf, 0)                       # player
    _dealer_hit(state, pf)                   # dealer up card

    if is_blackjack(state["player"][0]["cards"]) or is_blackjack(state["dealer"]):
        state["finished"] = True
        _settle(state, pf)

    return state


def _hit(state: dict, pf: ProvablyFair, hand_idx: int) -> None:
    card = _draw(pf, state["draw_index"])
    state["draw_index"] += 1
    state["player"][hand_idx]["cards"].append(card)
    if is_bust(state["player"][hand_idx]["cards"]):
        state["player"][hand_idx]["done"] = True
        _advance(state, pf)


def _dealer_hit(state: dict, pf: ProvablyFair) -> None:
    state["dealer"].append(_draw(pf, state["draw_index"]))
    state["draw_index"] += 1


def _advance(state: dict, pf: ProvablyFair) -> None:
    """Move to the next hand still in play, or finish the round."""
    for i, hand in enumerate(state["player"]):
        if not hand["done"] and not is_bust(hand["cards"]):
            state["active"] = i
            return
    state["finished"] = True
    _settle(state, pf)


def _settle(state: dict, pf: ProvablyFair) -> None:
    """Dealer plays out, then every hand is graded.

    The dealer's draw cards come from the SAME provably-fair stream as the
    player's, continuing at `draw_index`, so the whole hand is reproducible
    from the seed. Never call this without a stream - a dealer that draws from
    somewhere else is unauditable.
    """
    live = [h for h in state["player"] if not is_bust(h["cards"])]
    if live and not is_blackjack(state["dealer"]):
        while True:
            total, _soft = hand_value(state["dealer"])
            if total >= DEALER_STANDS_ON:
                break
            _dealer_hit(state, pf)
    dealer_total, _ = hand_value(state["dealer"])
    dealer_bj = is_blackjack(state["dealer"])

    for hand in state["player"]:
        cards = hand["cards"]
        total, _ = hand_value(cards)
        stake = hand["stake"]
        if total > 21:
            hand["result"], hand["payout"] = "lose", 0
        elif is_blackjack(cards) and not hand["from_split"]:
            if dealer_bj:
                hand["result"], hand["payout"] = "push", stake
            else:
                hand["result"], hand["payout"] = "blackjack", stake + int(stake * BLACKJACK_PAYS)
        elif dealer_bj:
            hand["result"], hand["payout"] = "lose", 0
        elif dealer_total > 21 or total > dealer_total:
            hand["result"], hand["payout"] = "win", stake * 2
        elif total == dealer_total:
            hand["result"], hand["payout"] = "push", stake
        else:
            hand["result"], hand["payout"] = "lose", 0
    state["finished"] = True


# ---------------------------------------------------------------------------
# player actions
# ---------------------------------------------------------------------------
def _check_open(state: dict, action: str) -> int:
    if state.get("finished"):
        raise BlackjackError(f"hand is already finished, cannot {action}")
    return state["active"]


def hit(state: dict, pf: ProvablyFair) -> dict:
    if state.get("finished"):
        raise BlackjackError("hand is already finished, cannot hit")
    idx = _check_open(state, "hit")
    hand = state["player"][idx]
    if hand["split_aces"]:
        raise BlackjackError("split aces receive one card only")
    _hit(state, pf, idx)
    if not hand["done"] and not state.get("finished"):
        total, _ = hand_value(hand["cards"])
        if total >= 21:
            hand["done"] = True
            _advance(state, pf)
    return state


def stand(state: dict, pf: ProvablyFair) -> dict:
    idx = _check_open(state, "stand")
    state["player"][idx]["done"] = True
    _advance(state, pf)
    return state


def double(state: dict, pf: ProvablyFair) -> dict:
    """Doubles the active hand's stake - returns the EXTRA stake the caller must
    debit from the player's wallet before persisting the new state."""
    idx = _check_open(state, "double")
    hand = state["player"][idx]
    if len(hand["cards"]) != 2:
        raise BlackjackError("double is only allowed on the first two cards")
    if hand["doubled"]:
        raise BlackjackError("hand is already doubled")
    extra = hand["stake"]
    hand["stake"] += extra
    hand["doubled"] = True
    _hit(state, pf, idx)
    if not hand["done"]:
        hand["done"] = True
        _advance(state, pf)
    state["extra_stake_due"] = extra
    return state


def split(state: dict, pf: ProvablyFair) -> dict:
    """Splits the active hand. Returns state with `extra_stake_due` set to the
    amount the caller must move from the wallet into the locked account."""
    idx = _check_open(state, "split")
    hand = state["player"][idx]
    if len(hand["cards"]) != 2:
        raise BlackjackError("split is only allowed on the first two cards")
    if card_value(hand["cards"][0]["rank"]) != card_value(hand["cards"][1]["rank"]):
        raise BlackjackError("split requires two cards of equal value")
    if len(state["player"]) >= MAX_HANDS:
        raise BlackjackError(f"maximum of {MAX_HANDS} hands after splitting")

    split_aces = hand["cards"][0]["rank"] == "A"
    extra = hand["stake"]
    moved = hand["cards"].pop()
    new = _new_hand(hand["stake"])
    new["from_split"] = True
    new["split_aces"] = split_aces
    new["cards"] = [moved]
    hand["from_split"] = True
    hand["split_aces"] = split_aces
    state["player"].insert(idx + 1, new)

    for h in (hand, new):
        h["cards"].append(_draw(pf, state["draw_index"]))
        state["draw_index"] += 1
        if split_aces:
            h["done"] = True
        elif is_bust(h["cards"]):
            h["done"] = True

    state["extra_stake_due"] = extra
    _advance(state, pf)
    return state


def summary(state: dict) -> dict:
    """Everything the UI needs to render the table."""
    dealer_cards = state["dealer"]
    hide_hole = not state.get("finished")
    shown = dealer_cards if not hide_hole else dealer_cards[:1]
    dealer_total, _ = hand_value(shown)
    hands = []
    for h in state["player"]:
        total, soft = hand_value(h["cards"])
        hands.append(
            {
                "cards": h["cards"],
                "total": total,
                "soft": soft,
                "stake": h["stake"],
                "done": h["done"],
                "doubled": h["doubled"],
                "result": h["result"],
                "payout": h["payout"],
                "bust": total > 21,
                "blackjack": is_blackjack(h["cards"]) and not h["from_split"],
            }
        )
    return {
        "dealer": {"cards": showed(dealer_cards, hide_hole), "total": dealer_total,
                   "hidden": hide_hole, "bust": (not hide_hole) and is_bust(dealer_cards),
                   "blackjack": (not hide_hole) and is_blackjack(dealer_cards)},
        "hands": hands,
        "active": state["active"],
        "finished": state.get("finished", False),
        "total_stake": sum(h["stake"] for h in state["player"]),
        "total_payout": sum(h["payout"] for h in state["player"]),
        "actions": available_actions(state),
    }


def showed(cards: list[dict], hide_hole: bool) -> list[dict]:
    if not hide_hole:
        return cards
    return [cards[0], {"rank": "?", "suit": "?"}]


def available_actions(state: dict) -> list[str]:
    if state.get("finished"):
        return ["deal"]
    idx = state["active"]
    hand = state["player"][idx]
    if hand["done"]:
        return []
    acts = ["hit", "stand"]
    if len(hand["cards"]) == 2 and not hand["doubled"]:
        acts.append("double")
        if (
            len(state["player"]) < MAX_HANDS
            and card_value(hand["cards"][0]["rank"]) == card_value(hand["cards"][1]["rank"])
            and not hand["split_aces"]
        ):
            acts.append("split")
    if hand["split_aces"]:
        return ["stand"]
    return acts


def apply_action(state: dict, action: str, pf: ProvablyFair) -> dict:
    action = action.lower()
    if action == "hit":
        return hit(state, pf)
    if action == "stand":
        return stand(state, pf)
    if action == "double":
        return double(state, pf)
    if action == "split":
        return split(state, pf)
    raise BlackjackError(f"unknown action {action!r}")
