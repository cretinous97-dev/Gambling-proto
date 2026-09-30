"""Crash round runner - one asyncio task owns the lifecycle of every round.

    BETTING  (BETTING_SECONDS)  -> join window, seed hash published
    RUNNING  (~up to 60s)       -> multiplier climbs, cash-outs honoured on a
                                   first-come-first-served server clock
    CRASHED  (CRASHED_SECONDS)  -> bust revealed, losers settled, history page
    -> next round

Why a single server-side loop rather than per-round timers: it guarantees one
and only one writer per round, which is the only way crash games avoid the
classic double-payout bug. The loop also broadcasts state to websocket clients.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from ..config import settings
from ..db import session_scope
from ..games import crash as crash_engine
from ..models import AccountKind, Bet, GameRound, RoundStatus, User, utcnow
from ..money import mul_minor
from ..rng import ProvablyFair, commit, new_server_seed
from . import bets as bet_svc
from . import bonus as bonus_svc

log = logging.getLogger("crash")

BETTING_SECONDS = 7.0
CRASHED_SECONDS = 5.0
TICK_S = 0.25
MAX_ROUND_SECONDS = 60.0
HISTORY_LEN = 40


class CrashHub:
    """Websocket fan-out. Kept dependency-free so the loop never blocks on a
    slow client: send failures just drop that socket."""

    def __init__(self) -> None:
        self._clients: set = set()
        self._lock = asyncio.Lock()

    async def register(self, ws) -> None:
        async with self._lock:
            self._clients.add(ws)

    async def unregister(self, ws) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, payload: dict) -> None:
        async with self._lock:
            clients = list(self._clients)
        dead = []
        for ws in clients:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)

    @property
    def client_count(self) -> int:
        return len(self._clients)


hub = CrashHub()


def _create_round(db) -> GameRound:
    last = db.execute(select(func.max(GameRound.round_number))).scalar_one()
    seed = new_server_seed()
    row = GameRound(
        game="crash",
        round_number=int(last or 0) + 1,
        server_seed=seed,
        server_seed_hash=commit(seed),
        client_seed="public",           # public seed: outcome is a pure function
        nonce=int(last or 0) + 1,
        crash_point=0.0,                # filled in at creation, revealed at bust
        status=RoundStatus.betting,
    )
    db.add(row)
    db.flush()

    # Bust point is fixed NOW, from the committed seed, and hidden until crash.
    pf = ProvablyFair(seed, row.client_seed, row.nonce)
    row.crash_point = pf.crash_point(settings.house_edge_default)
    db.flush()
    return row


def crash_history(db, limit: int = HISTORY_LEN) -> list[dict]:
    rows = (
        db.execute(
            select(GameRound)
            .where(GameRound.status.in_((RoundStatus.crashed, RoundStatus.settled)))
            .order_by(GameRound.round_number.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return [
        {
            "round_number": r.round_number,
            "crash_point": float(r.crash_point),
            "server_seed_hash": r.server_seed_hash,
            "server_seed": r.server_seed,
            "client_seed": r.client_seed,
            "nonce": r.nonce,
            "crashed_at": r.crashed_at,
        }
        for r in rows
    ]


def settle_round(db, round_row: GameRound, elapsed_s: float) -> dict:
    """Reveal the bust, pay the winners, take the losers' stakes."""
    point = float(round_row.crash_point)
    round_row.status = RoundStatus.crashed
    round_row.crashed_at = utcnow()

    bets = db.execute(
        select(Bet).where(Bet.round_id == round_row.id, Bet.settled.is_(False))
    ).scalars().all()

    winners: list[dict] = []
    for b in bets:
        user = db.get(User, b.user_id)
        if user is None:
            continue
        auto = (b.params or {}).get("auto_cashout")
        # Auto cash-out fires if the target was at or below the bust point.
        if auto is not None and float(auto) <= point:
            mult = max(float(auto), 1.01)
            bet_svc._settle(
                db, user, b, mul_minor(b.stake, mult),
                multiplier=round(mult, 2),
                result={"cashed_out_at": round(mult, 2), "auto": True},
            )
            winners.append({"user_id": user.id, "username": user.username,
                            "multiplier": round(mult, 2), "payout": b.payout})
        else:
            bet_svc.crash_bust(db, user, b, point)

    round_row.status = RoundStatus.settled
    round_row.settled_at = utcnow()
    db.flush()
    return {"round_number": round_row.round_number, "crash_point": point, "winners": winners}


def bootstrap() -> None:
    """Create the first round synchronously at startup.

    Without this, GET /api/crash/state returns 503 for the second or so before
    the loop's first tick - which reads like an outage during a deploy. On
    serverless there is no lifespan hook to rely on, so requests call
    `advance()` instead and this is only a warm-up.
    """
    with session_scope() as db:
        existing = latest_round(db)
        if existing is None:
            _create_round(db)


async def run_forever(stop: asyncio.Event) -> None:
    """Main loop. Started from the FastAPI lifespan hook."""
    while not stop.is_set():
        try:
            await _run_one_round(stop)
        except asyncio.CancelledError:
            raise
        except Exception:  # never let one bad round kill the game loop
            log.exception("crash round failed; skipping to the next")
            await asyncio.sleep(2)


async def _run_one_round(stop: asyncio.Event) -> None:
    with session_scope() as db:
        round_row = db.execute(
            select(GameRound).order_by(GameRound.round_number.desc())
        ).scalars().first()
        if round_row is None or round_row.status is RoundStatus.settled:
            round_row = _create_round(db)
        round_id = round_row.id

    # ---------------- betting window ----------------
    opened = utcnow()
    while (utcnow() - opened).total_seconds() < BETTING_SECONDS and not stop.is_set():
        with session_scope() as db:
            r = db.get(GameRound, round_id)
            bets = db.execute(
                select(Bet).where(Bet.round_id == round_id, Bet.settled.is_(False))
            ).scalars().all()
            players = []
            for b in bets:
                u = db.get(User, b.user_id)
                players.append(
                    {
                        "username": u.username if u else "?",
                        "stake": b.stake,
                        "auto_cashout": (b.params or {}).get("auto_cashout"),
                    }
                )
            history = crash_history(db, 20)
        await hub.broadcast(
            {
                "type": "crash.state",
                "phase": "betting",
                "round_number": r.round_number,
                "server_seed_hash": r.server_seed_hash,
                "player_count": len(players),
                "players": players,
                "total_stake": sum(p["stake"] for p in players),
                "seconds_left": max(
                    0.0,
                    BETTING_SECONDS - (utcnow() - opened).total_seconds(),
                ),
                "history": [
                    {"round_number": h["round_number"], "crash_point": h["crash_point"]}
                    for h in history
                ],
            }
        )
        await asyncio.sleep(TICK_S)

    # ---------------- running ----------------
    with session_scope() as db:
        r = db.get(GameRound, round_id)
        r.status = RoundStatus.running
        r.started_at = utcnow()
        point = float(r.crash_point)
        seed_hash = r.server_seed_hash
    started = utcnow()

    while True:
        elapsed = (utcnow() - started).total_seconds()
        current = crash_engine.multiplier_at(elapsed)

        if current >= point or elapsed > MAX_ROUND_SECONDS or stop.is_set():
            break

        with session_scope() as db:
            bets = db.execute(
                select(Bet).where(Bet.round_id == round_id, Bet.settled.is_(False))
            ).scalars().all()
            players = []
            for b in bets:
                u = db.get(User, b.user_id)
                players.append(
                    {
                        "username": u.username if u else "?",
                        "stake": b.stake,
                        "auto_cashout": (b.params or {}).get("auto_cashout"),
                    }
                )
        await hub.broadcast(
            {
                "type": "crash.state",
                "phase": "running",
                "round_number": r.round_number,
                "server_seed_hash": seed_hash,
                "multiplier": round(current, 2),
                "elapsed": round(elapsed, 2),
                "player_count": len(players),
                "players": players,
            }
        )
        await asyncio.sleep(TICK_S)

    # ---------------- crashed ----------------
    elapsed = (utcnow() - started).total_seconds()
    with session_scope() as db:
        r = db.get(GameRound, round_id)
        point = float(r.crash_point)
        summary = settle_round(db, r, elapsed)
        history = crash_history(db, 20)

    await hub.broadcast(
        {
            "type": "crash.state",
            "phase": "crashed",
            "round_number": summary["round_number"],
            "crash_point": summary["crash_point"],
            "server_seed": seed_hash and r.server_seed,
            "server_seed_hash": r.server_seed_hash,
            "client_seed": r.client_seed,
            "nonce": r.nonce,
            "winners": summary["winners"],
            "history": [
                {"round_number": h["round_number"], "crash_point": h["crash_point"]}
                for h in history
            ],
        }
    )
    await asyncio.sleep(CRASHED_SECONDS)


# ---------------------------------------------------------------------------
# Clock-driven state machine
# ---------------------------------------------------------------------------
# The loop above is the right design for a long-running process: one writer,
# always ticking. A serverless deployment has no such process - the function is
# frozen between requests and several instances run at once - so the same state
# machine is driven from the clock instead, by whichever request arrives first.
#
# Everything below is therefore idempotent and safe to call concurrently from
# any number of instances: every state change is a conditional UPDATE, so
# exactly one caller wins and the losers simply read the new state.


def latest_round(db) -> GameRound | None:
    return (
        db.execute(select(GameRound).order_by(GameRound.round_number.desc()))
        .scalars()
        .first()
    )


def _claim(db, round_id: str, from_status: RoundStatus, to_status: RoundStatus, **values) -> bool:
    """Move a round between states, but only if it is still in `from_status`.

    Returns True when THIS caller performed the transition. Two instances that
    both decide the round should end will race here, and only one gets True -
    which is what stops a shared round being settled twice.
    """
    result = db.execute(
        update(GameRound)
        .where(GameRound.id == round_id, GameRound.status == from_status)
        .values(status=to_status, **values)
    )
    db.flush()
    return result.rowcount == 1


def advance(db) -> GameRound:
    """Advance the crash round to wherever the wall clock says it should be.

    Called at the top of every crash request. In the always-on deployment the
    loop calls it far more often; nothing here assumes how often it runs.
    """
    row = latest_round(db)
    now = utcnow()

    if row is None:
        return _create_round(db)

    created = row.created_at or now
    if created.tzinfo is None:
        created = created.replace(tzinfo=now.tzinfo)

    # ---- betting window -------------------------------------------------
    if row.status is RoundStatus.betting:
        if (now - created).total_seconds() >= BETTING_SECONDS:
            if _claim(db, row.id, RoundStatus.betting, RoundStatus.running, started_at=now):
                db.refresh(row)
                log.info("crash: round %s in flight", row.round_number)
        return row

    # ---- in flight ------------------------------------------------------
    if row.status is RoundStatus.running:
        started = row.started_at or now
        if started.tzinfo is None:
            started = started.replace(tzinfo=now.tzinfo)
        elapsed = (now - started).total_seconds()
        point = float(row.crash_point)
        if crash_engine.multiplier_at(elapsed) >= point or elapsed > MAX_ROUND_SECONDS:
            if _claim(db, row.id, RoundStatus.running, RoundStatus.crashed, crashed_at=now):
                db.refresh(row)
                settle_round(db, row, elapsed)
                db.refresh(row)
                log.info("crash: round %s busted at %.2fx", row.round_number, point)
        return row

    # ---- bust on screen, then the next round ---------------------------
    marker = row.settled_at or row.crashed_at or created
    if marker.tzinfo is None:
        marker = marker.replace(tzinfo=now.tzinfo)
    if (now - marker).total_seconds() >= CRASHED_SECONDS:
        if latest_round(db).id == row.id:  # nobody else opened one already
            try:
                return _create_round(db)
            except IntegrityError:
                # another instance created round N+1 first; use theirs
                db.rollback()
                return latest_round(db)
    return row


async def ops_loop(stop: asyncio.Event, interval_s: int = 300) -> None:
    """Periodic housekeeping: void abandoned bets, expire bonuses, refresh VIP."""
    while not stop.is_set():
        with contextlib.suppress(Exception):
            with session_scope() as db:
                voided = bet_svc.void_stale_bets(db)
                expired = bonus_svc.expiry_sweep(db)
                if voided or expired:
                    log.info("ops sweep: voided=%s expired_bonuses=%s", voided, expired)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval_s)


def current_round_state(db) -> dict:
    """REST fallback for clients that cannot hold a websocket open.

    Also the heartbeat that drives the game on serverless: reading the state is
    what moves the round forward when no background loop exists.
    """
    r = advance(db)
    if r is None:
        return {"phase": "starting", "round_number": 0}
    # a freshly busted round stays on screen for CRASHED_SECONDS; the client
    # only understands betting/running/crashed
    phase = "crashed" if r.status is RoundStatus.settled else r.status.value
    return {
        "phase": phase,
        "round_number": r.round_number,
        "server_seed_hash": r.server_seed_hash,
        "started_at": r.started_at,
        "history": [
            {"round_number": h["round_number"], "crash_point": h["crash_point"]}
            for h in crash_history(db, 20)
        ],
    }
