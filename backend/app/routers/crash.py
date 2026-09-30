"""Crash endpoints: REST state/join/cashout plus the live websocket feed."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select

from ..db import SessionLocal
from ..games import crash as crash_engine
from ..models import Bet, GameRound, RoundStatus, User, utcnow
from ..rng import verify as rng_verify
from ..schemas import CashoutIn, CrashBetIn, money_field
from ..security import CurrentUser, Db, decode_token
from ..services import bets as bet_svc
from ..config import settings
from ..services.crash_loop import advance, crash_history, hub

router = APIRouter(prefix="/api/crash", tags=["crash"])


def _live_round(db) -> GameRound:
    """Fetch the live round, first moving it to wherever the clock says it is.

    `advance` costs one indexed read plus (rarely) a conditional UPDATE. Paying
    that on every request is what lets the game run with no background process
    at all - which is the only way it works on a serverless platform.
    """
    row = advance(db)
    if row is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Crash is starting up.")
    return row


def _round_payload(db, row: GameRound, user: User | None = None) -> dict:
    bets = db.execute(
        select(Bet).where(Bet.round_id == row.id, Bet.settled.is_(False))
    ).scalars().all()
    players = []
    for b in bets:
        u = db.get(User, b.user_id)
        players.append(
            {
                "username": u.username if u else "?",
                "stake": b.stake,
                "auto_cashout": (b.params or {}).get("auto_cashout"),
                "is_you": bool(user and u and u.id == user.id),
            }
        )

    elapsed = 0.0
    multiplier = 1.0
    if row.status is RoundStatus.running and row.started_at:
        elapsed = (utcnow() - row.started_at).total_seconds()
        multiplier = crash_engine.multiplier_at(elapsed)

    mine = None
    if user is not None:
        b = db.execute(
            select(Bet).where(Bet.round_id == row.id, Bet.user_id == user.id)
        ).scalars().first()
        if b is not None:
            mine = {
                "bet_id": b.id,
                "stake": b.stake,
                "settled": b.settled,
                "payout": b.payout,
                "multiplier": float(b.multiplier or 0),
                "auto_cashout": (b.params or {}).get("auto_cashout"),
                "cashout_multiplier_now": (
                    crash_engine.cashout_multiplier(float(row.crash_point), elapsed, None)
                    if row.status is RoundStatus.running
                    else 0.0
                ),
                "current_value": int(b.stake * multiplier) if not b.settled else b.payout,
            }

    # One vocabulary for clients: a fully settled round is still "crashed" as
    # far as the screen is concerned. Leaking the internal enum here made
    # GET /state and GET /state/me disagree with the websocket feed.
    phase = "crashed" if row.status is RoundStatus.settled else row.status.value
    return {
        "phase": phase,
        "round_number": row.round_number,
        "server_seed_hash": row.server_seed_hash,
        "started_at": row.started_at,
        "elapsed": round(elapsed, 2),
        "multiplier": round(multiplier, 2),
        "player_count": len(players),
        "total_stake": sum(p["stake"] for p in players),
        "players": players,
        "your_bet": mine,
        "crash_point": float(row.crash_point) if row.status in (
            RoundStatus.crashed, RoundStatus.settled
        ) else None,
    }


@router.get("/state")
def state(db: Db):
    row = _live_round(db)
    return _round_payload(db, row)


@router.get("/state/me")
def state_me(user: CurrentUser, db: Db):
    row = _live_round(db)
    return _round_payload(db, row, user)


@router.post("/bet", status_code=status.HTTP_201_CREATED)
def place_bet(payload: CrashBetIn, user: CurrentUser, db: Db):
    row = _live_round(db)
    bet = bet_svc.crash_join(
        db,
        user,
        row,
        stake=money_field(payload.stake),
        auto_cashout=payload.auto_cashout,
        idempotency_key=payload.idempotency_key,
    )
    return {
        "bet_id": bet.id,
        "round_number": row.round_number,
        "stake": bet.stake,
        "auto_cashout": (bet.params or {}).get("auto_cashout"),
        "player_count": None,
    }


@router.post("/cashout")
def cashout(payload: CashoutIn, user: CurrentUser, db: Db):
    row = _live_round(db)
    if row.status is not RoundStatus.running:
        raise HTTPException(status.HTTP_409_CONFLICT, "The round is not running.")
    bet = db.execute(
        select(Bet).where(Bet.round_id == row.id, Bet.user_id == user.id)
    ).scalars().first()
    if bet is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "You have no bet in this round.")
    if bet.settled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Your bet is already settled.")

    elapsed = (utcnow() - row.started_at).total_seconds() if row.started_at else 0.0
    bet = bet_svc.crash_cashout(db, user, bet, float(row.crash_point), elapsed, payload.target)
    return {
        "bet_id": bet.id,
        "multiplier": float(bet.multiplier),
        "payout": bet.payout,
        "profit": bet.profit,
    }


@router.get("/history")
def history(db: Db, limit: int = Query(default=40, ge=1, le=200)):
    return {"rounds": crash_history(db, limit)}


@router.get("/verify/{round_number}")
def verify_round(round_number: int, db: Db):
    """Public honesty check: recompute the bust point from the revealed seed."""
    row = db.execute(
        select(GameRound).where(GameRound.round_number == round_number)
    ).scalars().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Round not found.")
    if row.status not in (RoundStatus.crashed, RoundStatus.settled):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The round is still open - its seed is revealed when it crashes.",
        )
    result = rng_verify(
        server_seed=row.server_seed,
        server_seed_hash=row.server_seed_hash,
        client_seed=row.client_seed,
        nonce=row.nonce,
        house_edge=0.01,
    )
    return {
        "round_number": row.round_number,
        "server_seed": row.server_seed,
        "server_seed_hash": row.server_seed_hash,
        "client_seed": row.client_seed,
        "nonce": row.nonce,
        "reported_crash_point": float(row.crash_point),
        "recomputed_crash_point": result["crash_point"],
        "seed_hash_matches": result["seed_hash_matches"],
        "honest": (
            result["seed_hash_matches"]
            and abs(result["crash_point"] - float(row.crash_point)) < 0.005
        ),
    }


# ---------------------------------------------------------------------------
# live feed
# ---------------------------------------------------------------------------
@router.websocket("/ws")
async def crash_ws(websocket: WebSocket, token: str | None = None):
    """Public state feed. Auth is optional: an anonymous socket still sees the
    multiplier and the player list, but only an authenticated one gets its own
    bet state - and no websocket can ever move money. Cash-out goes through
    POST /api/crash/cashout so it is authenticated and rate-limited like any
    other money endpoint."""
    if settings.serverless:
        # Serverless platforms do not hold long-lived sockets. Say so plainly
        # (1000 + a reason) so the client stops retrying and switches to the
        # REST poll, which drives the same state machine.
        await websocket.accept()
        await websocket.send_json(
            {
                "type": "crash.error",
                "detail": "websockets are not available on this deployment; "
                          "poll GET /api/crash/state instead",
            }
        )
        await websocket.close(code=1000)
        return

    user = None
    if token:
        db = SessionLocal()
        try:
            payload = decode_token(token)
            user = db.get(User, payload.get("sub", ""))
            if user is not None:
                db.expunge(user)
        except HTTPException:
            user = None
        finally:
            db.close()

    await websocket.accept()
    await hub.register(websocket)
    try:
        db = SessionLocal()
        try:
            row = db.execute(
                select(GameRound).order_by(GameRound.round_number.desc())
            ).scalars().first()
            if row is not None:
                await websocket.send_json(
                    {
                        "type": "crash.state",
                        "phase": row.status.value,
                        "round_number": row.round_number,
                        "server_seed_hash": row.server_seed_hash,
                        "multiplier": 1.0,
                        "history": [
                            {"round_number": h["round_number"], "crash_point": h["crash_point"]}
                            for h in crash_history(db, 20)
                        ],
                    }
                )
        finally:
            db.close()

        async def ping_loop() -> None:
            while True:
                await asyncio.sleep(20)
                await websocket.send_json({"type": "ping"})

        ping = asyncio.create_task(ping_loop())
        try:
            while True:
                msg = await websocket.receive_text()
                if msg == "ping":
                    await websocket.send_json({"type": "pong"})
        finally:
            ping.cancel()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await hub.unregister(websocket)
