"""Bets - the single place where game outcomes meet the ledger.

Flow for every wager (instant, crash, blackjack, mines):

    validate  -> compliance gates, stake limits, balance check
    open      -> Bet row (pending) + stake held in user_locked
    play      -> pure game engine produces the multiplier
    settle    -> payout credited, house revenue booked, bonus wager progressed
    bookkeep  -> limit usage, lifetime wagering, VIP tier, jackpot contribution

If anything raises between `open` and `settle` the router's transaction rolls
back and the player's money is untouched. Games that span several requests
(blackjack, mines, crash) settle through `settle_multiplier` with the stake that
is already sitting in `user_locked`, so a mid-hand crash can never lose money
silently - a sweeper (`settle_stale_bets`) voids anything orphaned.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..games import instant
from ..ledger import (
    Entry,
    InsufficientFunds,
    place_bet_hold,
    refund_bet,
    settle_bet,
)
from ..models import (
    AccountKind,
    Bet,
    GameRound,
    JackpotPool,
    User,
    utcnow,
)
from ..money import mul_minor
from ..rng import ProvablyFair, commit
from . import bonus as bonus_svc
from . import compliance

MAX_MULT = instant.MAX_MULT


def _fetch_jackpot(db: Session) -> JackpotPool:
    pool = db.execute(
        select(JackpotPool).where(JackpotPool.name == "Grand")
    ).scalar_one_or_none()
    if pool is None:
        pool = JackpotPool(name="Grand", amount=100_000, contribution_pct=0.01)
        db.add(pool)
        db.flush()
    return pool


def _validate_stake(stake: int) -> None:
    lo = int(settings.min_bet_usd * 100)
    hi = int(settings.max_bet_usd * 100)
    if stake < lo:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Minimum bet is {lo / 100:.2f}.")
    if stake > hi:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Maximum bet is {hi / 100:.2f}.")


def _next_nonce_and_pf(db: Session, user: User, bump: int = 1) -> ProvablyFair:
    """Consume the next provably-fair nonce(s) for this player."""
    pf = ProvablyFair(
        server_seed=user.server_seed,
        client_seed=user.client_seed,
        nonce=user.nonce,
    )
    user.nonce += bump
    return pf


def _spendable(db: Session, user: User, source: AccountKind) -> int:
    from ..ledger import get_balance

    return get_balance(db, user.id, source).amount


def _pick_source(db: Session, user: User, stake: int) -> AccountKind:
    source = bonus_svc.bonus_stake_source(db, user.id)
    if _spendable(db, user, source) >= stake:
        return source
    other = (
        AccountKind.user_available
        if source is AccountKind.user_bonus
        else AccountKind.user_bonus
    )
    if _spendable(db, user, other) >= stake:
        return other
    raise HTTPException(
        status.HTTP_402_PAYMENT_REQUIRED,
        "Insufficient balance. Please deposit to continue playing.",
    )


def _open_bet(
    db: Session,
    user: User,
    *,
    game: str,
    stake: int,
    params: dict,
    round_id: str | None = None,
    idempotency_key: str | None = None,
) -> tuple[Bet, ProvablyFair, AccountKind, int]:
    _validate_stake(stake)
    compliance.assert_can_bet(db, user, stake)

    if idempotency_key:
        existing = db.execute(
            select(Bet).where(Bet.idempotency_key == idempotency_key)
        ).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "This bet was already submitted."
            )

    source = _pick_source(db, user, stake)
    nonce_used = user.nonce
    pf = _next_nonce_and_pf(db, user)

    bet = Bet(
        user_id=user.id,
        game=game,
        round_id=round_id,
        stake=stake,
        stake_source=source,
        server_seed_hash=commit(user.server_seed),
        client_seed=user.client_seed,
        nonce=nonce_used,
        params=params,
        idempotency_key=idempotency_key,
    )
    db.add(bet)
    db.flush()

    try:
        place_bet_hold(db, user.id, stake, kind=source, reference=bet.id)
    except InsufficientFunds:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "Insufficient balance.")
    return bet, pf, source, nonce_used


def _settle(
    db: Session,
    user: User,
    bet: Bet,
    payout: int,
    *,
    multiplier: float,
    result: dict,
    stake: int | None = None,
) -> Bet:
    """Credit the payout and book the house's side. Idempotent per bet row."""
    if bet.settled:
        return bet
    locked_stake = stake if stake is not None else bet.stake
    payout = max(int(payout), 0)

    # Cap the payout. House edge only protects the operator over many bets; it
    # does nothing about the tail, where one maximum-stake top-prize hit is a
    # liability the operator has to fund. The cap is applied here, at the single
    # point where every game settles, so no game can bypass it.
    cap = int(settings.max_win_usd * 100)
    capped = False
    if cap > 0 and payout > cap:
        payout = cap
        capped = True

    settle_bet(db, user.id, locked_stake, payout, reference=bet.id)

    bet.payout = payout
    bet.profit = payout - locked_stake
    bet.multiplier = multiplier
    bet.result = result
    if capped:
        # Recorded on the bet so the player, support and the audit trail all
        # see that a cap applied rather than silently receiving less.
        bet.result = {**result, "max_win_capped": True,
                      "max_win_cap_cents": cap}
    bet.wager_contribution = int(
        locked_stake * bonus_svc.WAGER_WEIGHT.get(bet.game, 0.5)
    )
    bet.settled = True
    bet.settled_at = utcnow()

    # -- bookkeeping (all best-effort but transactional) --------------------
    user.wagered_lifetime += locked_stake
    compliance.add_wager_usage(db, user.id, locked_stake, bet.profit)
    bonus_svc.apply_wager(db, user, bet.game, locked_stake)
    bonus_svc.refresh_vip(db, user)

    pool = _fetch_jackpot(db)
    pool.amount += int(locked_stake * float(pool.contribution_pct))

    return bet


def settle_multiplier(
    db: Session, user: User, bet: Bet, multiplier: float, *, result: dict | None = None
) -> Bet:
    """Settle an already-open bet from a computed multiplier."""
    payout = mul_minor(bet.stake, multiplier) if multiplier > 0 else 0
    return _settle(db, user, bet, payout, multiplier=multiplier, result=result or {})


# ---------------------------------------------------------------------------
# instant games (server resolves the whole round in one request)
# ---------------------------------------------------------------------------
def play_instant(
    db: Session,
    user: User,
    *,
    game: str,
    stake: int,
    params: dict,
    idempotency_key: str | None = None,
) -> Bet:
    engine = instant.INSTANT_GAMES.get(game)
    if engine is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown game {game!r}")

    bet, pf, _source, _nonce = _open_bet(
        db, user, game=game, stake=stake, params=params, idempotency_key=idempotency_key
    )

    try:
        outcome = engine(stake, params, pf)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    payout = outcome.payout(stake)
    return _settle(
        db,
        user,
        bet,
        payout,
        multiplier=outcome.multiplier,
        result={
            **outcome.detail,
            "win": outcome.win,
            "correct": outcome.detail,
        },
    )


# ---------------------------------------------------------------------------
# mines (multi-step: the board lives on the bet row)
# ---------------------------------------------------------------------------
def mines_start(db: Session, user: User, *, stake: int, mines: int, idempotency_key: str | None = None) -> Bet:
    if mines not in instant.MINES_ALLOWED:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"mines must be one of {list(instant.MINES_ALLOWED)}"
        )
    bet, pf, _src, _n = _open_bet(
        db,
        user,
        game="mines",
        stake=stake,
        params={"mines": mines},
        idempotency_key=idempotency_key,
    )
    board = instant.mines_board(pf, mines)
    bet.result = {
        "board": board,
        "revealed": [],
        "mines": mines,
        "current_multiplier": 0.0,
        "next_multiplier": instant.mines_multiplier(mines, 1),
        "potential_payout": 0,
    }
    return bet


def mines_open_tile(db: Session, user: User, bet: Bet, index: int) -> Bet:
    if bet.game != "mines" or bet.settled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No open mines hand on this bet.")
    state = dict(bet.result)
    pf = ProvablyFair(user.server_seed, bet.client_seed or user.client_seed, bet.nonce or 0)
    try:
        outcome = instant.mines_open(
            bet.stake,
            {**state, "index": index, "revealed": state.get("revealed", [])},
            pf,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    if outcome.detail.get("hit_mine"):
        # Loss: the stake is already locked, hand it to the house.
        return _settle(
            db, user, bet, 0, multiplier=0.0,
            result={**state, "revealed": outcome.detail["revealed"], "hit_mine": True, "exploded_at": index},
        )

    bet.result = {
        "board": outcome.detail["board"],
        "revealed": outcome.detail["revealed"],
        "mines": state["mines"],
        "current_multiplier": outcome.detail["current_multiplier"],
        "next_multiplier": outcome.detail["next_multiplier"],
        "potential_payout": outcome.detail["potential_payout"],
    }
    if len(bet.result["revealed"]) >= instant.MINES_TILES - state["mines"]:
        # Board cleared - auto cash out at the maximum multiplier.
        return settle_multiplier(
            db, user, bet, bet.result["current_multiplier"], result={**bet.result, "auto_cashout": True}
        )
    return bet


def mines_cashout(db: Session, user: User, bet: Bet) -> Bet:
    if bet.game != "mines" or bet.settled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No open mines hand on this bet.")
    state = dict(bet.result)
    pf = ProvablyFair(user.server_seed, bet.client_seed or user.client_seed, bet.nonce or 0)
    try:
        outcome = instant.mines_cashout(bet.stake, state, pf)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    return _settle(
        db, user, bet, outcome.payout(bet.stake), multiplier=outcome.multiplier,
        result={**state, "cashed_out": True, "multiplier": outcome.multiplier},
    )


# ---------------------------------------------------------------------------
# blackjack (multi-step, multi-hand)
# ---------------------------------------------------------------------------
def blackjack_deal(db: Session, user: User, *, stake: int, idempotency_key: str | None = None) -> tuple[Bet, dict]:
    from ..games import blackjack

    _validate_stake(stake)
    bet, pf, _src, _n = _open_bet(
        db, user, game="blackjack", stake=stake, params={}, idempotency_key=idempotency_key
    )
    state = blackjack.new_hand(pf, stake)
    bet.result = state
    if state.get("finished"):
        payout = int(state["player"][0]["payout"]) if state["player"] else 0
        locked = state["player"][0]["stake"] if state["player"] else stake
        _settle(db, user, bet, payout, multiplier=(payout / locked if locked else 0),
                result=state, stake=locked)
    return bet, state


def blackjack_action(db: Session, user: User, bet: Bet, action: str) -> tuple[Bet, dict]:
    from ..games import blackjack

    if bet.game != "blackjack" or bet.settled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No open blackjack hand.")
    state = dict(bet.result)
    # Nested structures are JSON round-tripped by SQLAlchemy, so normalise them.
    state["player"] = [dict(h) for h in state.get("player", [])]
    state.pop("extra_stake_due", None)

    pf = ProvablyFair(user.server_seed, bet.client_seed or user.client_seed, bet.nonce or 0)
    try:
        state = blackjack.apply_action(state, action, pf)
    except blackjack.BlackjackError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    extra = int(state.pop("extra_stake_due", 0) or 0)
    if extra:
        # Split / double needs more money locked for the same hand.
        source = _pick_source(db, user, extra)
        try:
            place_bet_hold(db, user.id, extra, kind=source, reference=f"{bet.id}:extra")
        except InsufficientFunds:
            raise HTTPException(
                status.HTTP_402_PAYMENT_REQUIRED,
                "Insufficient balance to double or split this hand.",
            )
        bet.stake += extra

    bet.result = state
    if state.get("finished"):
        total_stake = sum(int(h["stake"]) for h in state["player"])
        total_payout = sum(int(h["payout"]) for h in state["player"])
        results = [h["result"] for h in state["player"]]
        mult = (total_payout / total_stake) if total_stake else 0.0
        _settle(
            db, user, bet, total_payout,
            multiplier=round(mult, 6),
            result={**state, "hand_results": results},
            stake=total_stake,
        )
    return bet, state


# ---------------------------------------------------------------------------
# crash
# ---------------------------------------------------------------------------
def crash_join(
    db: Session,
    user: User,
    round_row: GameRound,
    *,
    stake: int,
    auto_cashout: float | None,
    idempotency_key: str | None = None,
) -> Bet:
    from ..models import RoundStatus

    if round_row.status is not RoundStatus.betting:
        raise HTTPException(status.HTTP_409_CONFLICT, "Betting is closed for this round.")
    if auto_cashout is not None and not (1.01 <= float(auto_cashout) <= MAX_MULT):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Auto cash-out must be at least 1.01x")

    existing = db.execute(
        select(Bet).where(Bet.user_id == user.id, Bet.round_id == round_row.id)
    ).scalars().first()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "You already have a bet in this round.")

    bet, _pf, _src, _n = _open_bet(
        db,
        user,
        game="crash",
        stake=stake,
        params={"auto_cashout": auto_cashout},
        round_id=round_row.id,
        idempotency_key=idempotency_key,
    )
    return bet


def crash_cashout(db: Session, user: User, bet: Bet, crash_point: float, elapsed_s: float,
                  requested: float | None = None) -> Bet:
    from ..games import crash as crash_engine

    if bet.settled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This bet is already settled.")
    mult = crash_engine.cashout_multiplier(crash_point, elapsed_s, requested)
    if mult <= 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "Too late - the round already busted.")
    if mult < 1.01:
        mult = 1.01
    return _settle(
        db, user, bet,
        mul_minor(bet.stake, mult),
        multiplier=round(mult, 2),
        result={"cashed_out_at": round(mult, 2)},
    )


def crash_bust(db: Session, user: User, bet: Bet, crash_point: float) -> Bet:
    if bet.settled:
        return bet
    return _settle(
        db, user, bet, 0, multiplier=0.0,
        result={"crashed_at": crash_point, "lost": True},
    )


# ---------------------------------------------------------------------------
# housekeeping
# ---------------------------------------------------------------------------
def void_stale_bets(db: Session, older_than_minutes: int = 15) -> int:
    """Refund any hand left open by a server restart or an abandoned client.

    Called from the ops loop. A stake sitting in `user_locked` with no live
    round is a bug; this makes it a refund instead of a support ticket.
    """
    cutoff = utcnow() - timedelta(minutes=older_than_minutes)
    rows = db.execute(
        select(Bet).where(Bet.settled.is_(False), Bet.created_at < cutoff)
    ).scalars().all()
    for bet in rows:
        # Crash bets are owned by the round loop, not by this sweeper.
        if bet.round_id:
            continue
        refund_bet(db, bet.user_id, bet.stake, kind=bet.stake_source, reference=bet.id)
        bet.settled = True
        bet.settled_at = utcnow()
        bet.result = {**(bet.result or {}), "voided": True}
        bet.payout = 0
        bet.profit = 0
    return len(rows)
