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
from .security import hash_password
from .services.crash_loop import bootstrap, ops_loop, run_forever

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed_admin()
    status = provider_status()
    log.info(
        "payments: provider=%s mode=%s %s",
        status.get("provider"),
        status.get("mode"),
        status.get("warning") or "",
    )
    bootstrap()
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
    }


# ---------------------------------------------------------------------------
# SPA hosting: only active once `npm run build` has produced frontend/dist.
# ---------------------------------------------------------------------------
if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
else:

    @app.get("/", include_in_schema=False)
    def root():
        return {
            "message": f"{settings.app_name} API is running.",
            "docs": "/docs",
            "hint": "Build the frontend (cd frontend && npm run build) or use the Vite dev server.",
        }
