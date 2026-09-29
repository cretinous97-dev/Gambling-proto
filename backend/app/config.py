"""Runtime configuration.

Every value can be overridden with an environment variable (see .env.example).
Secrets MUST be replaced before running anywhere that is not your laptop.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- core ---------------------------------------------------------------
    app_name: str = "Naktsang Casino"
    environment: str = "development"          # development | staging | production
    debug: bool = True
    base_dir: Path = BASE_DIR

    # --- security -----------------------------------------------------------
    # CHANGE THIS. A weak/leaked signing key = anybody can mint admin tokens.
    secret_key: str = "dev-only-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_min: int = 60 * 24         # 24h
    refresh_token_ttl_days: int = 30
    password_min_length: int = 8

    # --- database -----------------------------------------------------------
    database_url: str = f"sqlite:///{BASE_DIR / 'data' / 'casino.db'}"

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
    rate_limit_bets_per_min: int = 240
    crash_round_gap_s: float = 6.0
    admin_email: str = "admin@casino.example.com"   # CHANGE THIS (must be a valid address)
    admin_password: str = "Admin!2345"
    cors_origins: str = "*"

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
    (s.base_dir / "data").mkdir(parents=True, exist_ok=True)
    if s.is_production and s.secret_key.startswith("dev-only"):
        raise RuntimeError(
            "Refusing to boot in production with the default SECRET_KEY. "
            "Set SECRET_KEY to a 64-char random value."
        )
    return s


settings = get_settings()
