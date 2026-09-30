"""Game catalogue, instant games, Mines, Blackjack, history and verification."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from ..games import blackjack as bj
from ..games import instant
from ..models import Bet, GameRound, RoundStatus, User
from ..rng import ProvablyFair, commit
from ..schemas import (
    BlackjackActionIn,
    BlackjackDealIn,
    InstantBetIn,
    MinesOpenIn,
    MinesStartIn,
    VerifySeedIn,
    money_field,
)
from ..security import CurrentUser, Db
from ..services import bets as bet_svc
from ..services.bonus import WAGER_WEIGHT

router = APIRouter(prefix="/api/games", tags=["games"])

#: games that span multiple requests and therefore have their own endpoints
STATEFUL_GAMES = {"mines", "blackjack", "crash"}


@router.get("/catalog")
def catalog():
    return {
        "games": [g for g in instant.GAME_META if g["slug"] != "crash"],
        "live": [g for g in instant.GAME_META if g["slug"] == "crash"],
        "wager_weights": WAGER_WEIGHT,
    }


@router.get("/rules/{slug}")
def rules(slug: str):
    """Published game rules. These strings are the contract with the player -
    keep them in sync with the engines, not with marketing."""
    docs = {
        "dice": {
            "how_to_play": "Choose a target and whether the roll must land over or under it. "
                           "Roll granularity is 0.01 (0.00-99.99).",
            "payout": "multiplier = 99% / win_chance, floored to 4 decimals.",
            "house_edge": "1.00%",
        },
        "limbo": {
            "how_to_play": "Pick a target multiplier of 1.01x or higher and press play.",
            "payout": "result = 99% / (1 - u), floored to 2 decimals; win if result >= target.",
            "house_edge": "1.00%",
        },
        "mines": {
            "how_to_play": "Pick 1-24 mines on a 5x5 grid, then reveal tiles. Cash out any time.",
            "payout": "fair odds from the exact hypergeometric probability of k safe reveals, "
                      "minus the edge, floored to 4 decimals.",
            "house_edge": "1.00%",
            "cashout_rules": "Clearing the whole board auto-cashes at the maximum multiplier.",
        },
        "plinko": {
            "how_to_play": "Choose 8, 12 or 16 rows and a risk profile. The ball walks "
                           "left/right per row and lands in a bucket.",
            "payout": "published per-bucket table; each step is 49.5% right instead of 50%.",
            "house_edge": "~1.00%",
        },
        "wheel": {
            "how_to_play": "Pick a risk profile and spin the 54-segment wheel.",
            "payout": "published per-segment table.",
            "house_edge": "~4.00%",
        },
        "keno": {
            "how_to_play": "Pick 1-10 numbers from 80; 20 are drawn.",
            "payout": "published hit-count table per number of picks.",
            "house_edge": "~4.00%",
        },
        "coinflip": {
            "how_to_play": "Call heads or tails.",
            "payout": "1.98x on a correct call.",
            "house_edge": "1.00%",
        },
        "roulette": {
            "how_to_play": "European single-zero wheel (0-36). Place 1-12 bets in one spin.",
            "payout": "straight 36x, split 18x, street 12x, corner 9x, line 6x, column/dozen 3x, "
                      "even-money 2x (all inclusive of stake).",
            "house_edge": "2.70%",
        },
        "slots": {
            "how_to_play": "20 fixed lines. Wild substitutes for everything except scatter. "
                           "3+ scatters anywhere pay 2x/10x/100x the total bet.",
            "payout": "published per-symbol line table, paid on the line bet (total bet / 20).",
            "house_edge": "~4.00%",
            "rounding": "All payouts are floored to the cent in the house's favour.",
        },
        "blackjack": {
            "how_to_play": "6-deck shoe, dealer stands on all 17s. Hit, stand, double or split.",
            "payout": "Blackjack pays 3:2, any other win 1:1, push returns the stake. "
                      "Split up to 4 hands; split aces receive one card only.",
            "house_edge": "~0.50% with basic strategy",
        },
        "crash": {
            "how_to_play": "Join during the betting window, then cash out before the curve busts. "
                           "Auto cash-out is available.",
            "payout": "P(crash >= m) = 99% / m, so cashing at m pays m x stake.",
            "house_edge": "1.00%",
            "note": "Cash-out requests are timed by the server clock. Requests that arrive after "
                    "the bust point are not honoured.",
        },
    }
    if slug not in docs:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no rules published for {slug!r}")
    return {"game": slug, **docs[slug]}


# ---------------------------------------------------------------------------
# instant games
# ---------------------------------------------------------------------------
@router.post("/play")
def play(payload: InstantBetIn, user: CurrentUser, db: Db):
    if payload.game in STATEFUL_GAMES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{payload.game} needs its own endpoints (it spans multiple actions).",
        )
    stake = money_field(payload.stake)
    bet = bet_svc.play_instant(
        db,
        user,
        game=payload.game,
        stake=stake,
        params=payload.params,
        idempotency_key=payload.idempotency_key,
    )
    return _bet_payload(bet)


# ---------------------------------------------------------------------------
# mines
# ---------------------------------------------------------------------------
@router.post("/mines/start")
def mines_start(payload: MinesStartIn, user: CurrentUser, db: Db):
    bet = bet_svc.mines_start(
        db, user, stake=money_field(payload.stake), mines=payload.mines,
        idempotency_key=payload.idempotency_key,
    )
    return _bet_payload(bet)


@router.post("/mines/{bet_id}/open")
def mines_open(bet_id: str, payload: MinesOpenIn, user: CurrentUser, db: Db):
    bet = _own_bet(db, user, bet_id, expect_game="mines")
    bet = bet_svc.mines_open_tile(db, user, bet, payload.index)
    return _bet_payload(bet)


@router.post("/mines/{bet_id}/cashout")
def mines_cashout(bet_id: str, user: CurrentUser, db: Db):
    bet = _own_bet(db, user, bet_id, expect_game="mines")
    bet = bet_svc.mines_cashout(db, user, bet)
    return _bet_payload(bet)


# ---------------------------------------------------------------------------
# blackjack
# ---------------------------------------------------------------------------
@router.post("/blackjack/deal")
def blackjack_deal(payload: BlackjackDealIn, user: CurrentUser, db: Db):
    bet, state = bet_svc.blackjack_deal(
        db, user, stake=money_field(payload.stake), idempotency_key=payload.idempotency_key
    )
    return {**_bet_payload(bet), "table": bj.summary(state)}


@router.post("/blackjack/{bet_id}/action")
def blackjack_action(bet_id: str, payload: BlackjackActionIn, user: CurrentUser, db: Db):
    bet = _own_bet(db, user, bet_id, expect_game="blackjack")
    bet, state = bet_svc.blackjack_action(db, user, bet, payload.action)
    return {**_bet_payload(bet), "table": bj.summary(state)}


@router.get("/blackjack/{bet_id}")
def blackjack_state(bet_id: str, user: CurrentUser, db: Db):
    bet = _own_bet(db, user, bet_id, expect_game="blackjack")
    return {**_bet_payload(bet), "table": bj.summary(bet.result or {})}


# ---------------------------------------------------------------------------
# history + fairness
# ---------------------------------------------------------------------------
@router.get("/history")
def history(
    user: CurrentUser,
    db: Db,
    game: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
):
    stmt = select(Bet).where(Bet.user_id == user.id)
    if game:
        stmt = stmt.where(Bet.game == game)
    bets = db.execute(stmt.order_by(Bet.created_at.desc()).limit(limit)).scalars().all()
    return {"bets": [_bet_payload(b) for b in bets]}


@router.get("/history/{bet_id}")
def bet_detail(bet_id: str, user: CurrentUser, db: Db):
    return _bet_payload(_own_bet(db, user, bet_id))


@router.get("/leaderboard")
def leaderboard(db: Db, limit: int = Query(default=10, ge=1, le=50)):
    """Biggest multipliers hit in the last 24h. Public - this is the social
    proof that people really do win."""
    from datetime import timedelta

    from ..models import utcnow

    rows = db.execute(
        select(Bet, User.username)
        .join(User, Bet.user_id == User.id)
        .where(
            Bet.settled.is_(True),
            Bet.multiplier > 1,
            Bet.created_at >= utcnow() - timedelta(days=1),
        )
        .order_by(Bet.multiplier.desc())
        .limit(limit)
    ).all()
    return {
        "top_multipliers": [
            {
                "username": _mask_name(u),
                "game": b.game,
                "multiplier": float(b.multiplier),
                "stake": b.stake,
                "payout": b.payout,
                "at": b.settled_at or b.created_at,
            }
            for b, u in rows
        ]
    }


def _mask_name(name: str) -> str:
    if len(name) <= 3:
        return name[0] + "**"
    return name[:2] + "***" + name[-1]


def _own_bet(db, user: User, bet_id: str, expect_game: str | None = None) -> Bet:
    bet = db.get(Bet, bet_id)
    if bet is None or bet.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bet not found.")
    if expect_game and bet.game != expect_game:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"bet is not a {expect_game} hand")
    return bet


def _bet_payload(bet: Bet) -> dict:
    return {
        "id": bet.id,
        "game": bet.game,
        "round_id": bet.round_id,
        "stake": bet.stake,
        "payout": bet.payout,
        "profit": bet.profit,
        "multiplier": float(bet.multiplier or 0),
        "settled": bet.settled,
        "result": bet.result or {},
        "params": bet.params or {},
        "stake_source": bet.stake_source.value,
        "fair": {
            "server_seed_hash": bet.server_seed_hash,
            "client_seed": bet.client_seed,
            "nonce": bet.nonce,
        },
        "created_at": bet.created_at,
        "settled_at": bet.settled_at,
        "balance_after": None,
    }


@router.post("/verify")
def verify(payload: VerifySeedIn, user: CurrentUser):
    """Recompute any past outcome from the revealed seed.

    For crash, the round's seed is public once the round has crashed, so this
    endpoint is the tool players use to check the operator did not move the
    goalposts. For player-nonce games, the player supplies their revealed seed.
    """
    if payload.game == "crash":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Crash verification uses the round seed: GET /api/crash/verify/{round_number}.",
        )
    pf = ProvablyFair(payload.server_seed, payload.client_seed, payload.nonce)
    engine = instant.INSTANT_GAMES.get(payload.game)
    if engine is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"cannot verify {payload.game!r}")
    stake = int(payload.params.pop("_stake", 100))
    outcome = engine(stake, payload.params, pf)
    return {
        "game": payload.game,
        "recomputed_multiplier": outcome.multiplier,
        "recomputed_payout": outcome.payout(stake),
        "detail": outcome.detail,
    }
