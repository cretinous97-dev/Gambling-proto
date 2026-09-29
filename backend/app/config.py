"""Runtime configuration.

Every value can be overridden with an environment variable (see .env.example).
Secrets MUST be replaced before running anywhere that is not your laptop.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- core ---------------------------------------------------------------
    app_name: str = "Naktsang Casino"
    environment: str = "development"          # development | staging | production
    debug: bool = True
    base_dir: Path = BASE_DIR

    # --- deployment mode ----------------------------------------------------
    # Serverless platforms (Vercel) set VERCEL=1. There is no persistent disk
    # and no process that keeps running between requests, which changes two
    # things: where SQLite is allowed to live, and how shared games advance.
    serverless: bool = bool(os.getenv("VERCEL") or os.getenv("SERVERLESS"))
    # Demo mode is for public test deployments: the sandbox provider plus a
    # database that resets. It is surfaced in /api/config so the UI can say so.
    demo_mode: bool = False

    # --- security -----------------------------------------------------------
    # CHANGE THIS. A weak/leaked signing key = anybody can mint admin tokens.
    secret_key: str = "dev-only-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_min: int = 60 * 24         # 24h
    refresh_token_ttl_days: int = 30
    password_min_length: int = 8

    # --- database -----------------------------------------------------------
    # Empty means "pick something sensible": ./data/casino.db locally, /tmp on
    # serverless (the only writable path). Set DATABASE_URL to a Postgres URL
    # for a deployment that must keep player balances across restarts.
    database_url: str = ""
    sqlite_fallback_dir: str = "/tmp"

    # --- payments -----------------------------------------------------------
    # 'sandbox'  -> self-contained simulator, no real money, safe to demo.
    # 'stripe'   -> Stripe adapter; requires your own merchant account + keys.
    # 'cryptopay'-> on-chain adapter; requires your own node/API keys + wallet.
    payment_provider: str = "sandbox"
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    cryptopay_api_key: str = ""
    cryptopay_webhook_secret: str = ""

    # --- compliance / risk --------------------------------------------------
    house_edge_default: float = 0.01             # 1% theoretical edge
    min_deposit_usd: float = 5.0
    max_deposit_usd: float = 10_000.0
    min_withdrawal_usd: float = 10.0
    max_withdrawal_usd: float = 5_000.0
    withdrawal_fee_pct: float = 0.0
    # Withdrawals above this always require a human admin approval.
    auto_approve_withdrawal_under_usd: float = 0.0
    default_wager_requirement_x: float = 1.0     # rollover on deposits w/ bonus
    kyc_required_above_usd: float = 1_000.0      # lifetime withdrawals
    self_exclusion_gate: bool = True
    jurisdiction_blocklist: str = "US,GB,FR,NL,AU"  # ISO-2, comma separated

    # --- bonus / VIP --------------------------------------------------------
    signup_bonus_usd: float = 0.0
    first_deposit_bonus_pct: float = 100.0
    first_deposit_bonus_max_usd: float = 100.0
    first_deposit_bonus_wager_x: float = 30.0
    rakeback_pct: float = 0.5

    # --- ops ----------------------------------------------------------------
    min_bet_usd: float = 0.10
    max_bet_usd: float = 2_000.0
    # Hard ceiling on what a single bet can return, in USD. This is the stopper
    # against the worst case in gambling: a player finds an edge (or a bug, or
    # a stuck seed) and bets the table maximum on it. Without a cap, one
    # unlucky combination of maximum stake and top prize is unbounded
    # liability. 0 disables the cap - do not disable it in production.
    max_win_usd: float = 100_000.0
    rate_limit_bets_per_min: int = 240
    crash_round_gap_s: float = 6.0
    admin_email: str = "admin@casino.example.com"   # CHANGE THIS (must be a valid address)
    admin_password: str = "Admin!2345"
    cors_origins: str = "*"

    @model_validator(mode="after")
    def _fill_defaults(self):
        if self.serverless:
            # Vercel runs its own production tier; do not make a test deploy
            # look like a live casino just because VERCEL_ENV says production.
            if os.getenv("VERCEL_ENV") and "ENVIRONMENT" not in os.environ:
                self.environment = "staging"
            self.demo_mode = True
            if not self.secret_key or self.secret_key.startswith("dev-only"):
                # Keep the deployment usable, but never silently: sessions will
                # not survive a cold start, and the operator must set a real key.
                import secrets as _secrets

                self.secret_key = _secrets.token_urlsafe(48)
                self.ephemeral_secret_key = True

        if not self.database_url:
            if self.serverless:
                self.database_url = f"sqlite:///{Path(self.sqlite_fallback_dir) / 'casino.db'}"
            else:
                self.database_url = f"sqlite:///{self.base_dir / 'data' / 'casino.db'}"
        return self

    #: Set by the validator when a throwaway signing key had to be minted.
    ephemeral_secret_key: bool = False

    @property
    def persistence_is_temporary(self) -> bool:
        """True when balances will not survive a restart/cold start."""
        return self.serverless and self.database_url.startswith("sqlite")

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def blocklist(self) -> set[str]:
        return {c.strip().upper() for c in self.jurisdiction_blocklist.split(",") if c.strip()}

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    for directory in (s.base_dir / "data", Path(s.sqlite_fallback_dir)):
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Read-only bundle directory (serverless). The engine only needs a
            # writable path for SQLite, which the validator has already chosen.
            pass
    if s.is_production and s.secret_key.startswith("dev-only"):
        raise RuntimeError(
            "Refusing to boot in production with the default SECRET_KEY. "
            "Set SECRET_KEY to a 64-char random value."
        )
    return s


settings = get_settings()
