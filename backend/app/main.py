"""FastAPI application entry point.

Run from `backend/`:

    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

Serves the API under /api and, when `frontend/dist` exists, the built React SPA
as a single-page app on every other path. In development the Vite dev server
proxies /api to this process instead (see frontend/vite.config.js).
"""
from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import init_db, session_scope
from .models import User, UserRole
from .payments import provider_status
from .routers import admin, auth, crash, games, misc, wallet
from .security import hash_password, signing_key_source
from .services import crash_loop
from .services.crash_loop import ops_loop, run_forever

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
)
log = logging.getLogger("app")

FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


def seed_admin() -> None:
    with session_scope() as db:
        existing = db.query(User).filter(User.email == settings.admin_email.lower()).first()
        if existing:
            return
        from .ledger import ensure_user_accounts
        from .rng import commit, new_client_seed, new_server_seed

        seed = new_server_seed()
        admin_user = User(
            email=settings.admin_email.lower(),
            username="admin",
            password_hash=hash_password(settings.admin_password),
            role=UserRole.admin,
            country="BT",
            email_verified=True,
            server_seed=seed,
            server_seed_hash=commit(seed),
            client_seed=new_client_seed(),
        )
        db.add(admin_user)
        db.flush()
        ensure_user_accounts(db, admin_user)
        log.warning(
            "Seeded admin account %s. CHANGE THE PASSWORD IMMEDIATELY "
            "(ADMIN_PASSWORD env var).",
            settings.admin_email,
        )


# The lifespan hook below is the normal way to boot. Serverless platforms do
# not reliably run it, so the same work is available as an idempotent function
# and re-run (cheaply) by the first request that arrives. Whichever happens
# first wins; both are safe to call more than once.
_bootstrapped = False
_bootstrap_lock = threading.Lock()


def bootstrap_app() -> None:
    """Create tables, seed the operator account, warm the crash round."""
    global _bootstrapped
    if _bootstrapped:
        return
    with _bootstrap_lock:
        if _bootstrapped:
            return
        init_db()
        # Resolve the session signing key here, outside any request: on SQLite
        # a competing write from inside a request can lock, and a token signed
        # with one key must verify with the same key on every instance.
        from .security import ensure_signing_key

        ensure_signing_key()
        seed_admin()
        crash_loop.bootstrap()
        status = provider_status()
        log.info(
            "payments: provider=%s mode=%s %s",
            status.get("provider"),
            status.get("mode"),
            status.get("warning") or "",
        )
        if settings.ephemeral_secret_key:
            log.warning(
                "SECRET_KEY was not set: using a throwaway key. Sessions will "
                "not survive a restart. Set SECRET_KEY in the environment."
            )
        if settings.persistence_is_temporary:
            log.warning(
                "Database is %s - on a serverless platform this file is "
                "ephemeral, so player balances reset. Set DATABASE_URL to a "
                "Postgres URL if the data must persist.",
                settings.database_url,
            )
        _bootstrapped = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    bootstrap_app()
    if settings.serverless:
        # No background loops: shared games advance from the clock on request
        # instead (see services/crash_loop.advance).
        log.info("serverless mode: background loops disabled, clock-driven games")
        yield
        return
    stop = asyncio.Event()
    tasks = [
        asyncio.create_task(run_forever(stop), name="crash-loop"),
        asyncio.create_task(ops_loop(stop), name="ops-loop"),
    ]
    try:
        yield
    finally:
        stop.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description=(
        "Full-stack online casino: player wallet, provably-fair games, deposits, "
        "withdrawals, compliance controls and a back office. See README.md for the "
        "regulatory work that must be completed by the operator before real money "
        "is accepted."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


@app.middleware("http")
async def ensure_ready(request: Request, call_next):
    """First request wins the boot race on platforms with no lifespan hook."""
    if not _bootstrapped:
        # touch the DB in a worker thread: bootstrap does blocking I/O
        await asyncio.to_thread(bootstrap_app)
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if settings.is_production:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


@app.exception_handler(ValueError)
async def value_error_handler(_request: Request, exc: ValueError):
    """Game engine validation errors are player-facing, not 500s."""
    return JSONResponse(status_code=400, content={"detail": str(exc)})


app.include_router(auth.router)
app.include_router(wallet.router)
app.include_router(games.router)
app.include_router(crash.router)
app.include_router(admin.router)
app.include_router(misc.router)


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "app": settings.app_name,
        "environment": settings.environment,
        "provider": provider_status(),
        "deployment": {
            "serverless": settings.serverless,
            "demo_mode": settings.demo_mode,
            "background_loops": not settings.serverless,
            "websockets": not settings.serverless,
            "balances_persist": not settings.persistence_is_temporary,
            # "environment" (SECRET_KEY set), "database" (generated once and
            # reused), or "process" (changes on every restart - players get
            # logged out).
            "signing_key_source": signing_key_source(),
        },
    }


# ---------------------------------------------------------------------------
# SPA hosting: only active once `npm run build` has produced frontend/dist.
# ---------------------------------------------------------------------------
if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        # An unknown /api/... path must NOT fall through to the single-page app:
        # returning index.html for a mistyped endpoint gives the client a JSON
        # parse error instead of a 404 that says what went wrong.
        if full_path.startswith("api/"):
            return JSONResponse(
                status_code=404,
                content={"detail": f"No such endpoint: /{full_path}"},
            )
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        # Never let the browser cache index.html: it points at hashed assets
        # that change on every deploy.
        return FileResponse(
            FRONTEND_DIST / "index.html", headers={"Cache-Control": "no-store"}
        )
else:

    @app.get("/", include_in_schema=False)
    def root():
        return {
            "message": f"{settings.app_name} API is running.",
            "docs": "/docs",
            "hint": "Build the frontend (cd frontend && npm run build) or use the Vite dev server.",
        }
