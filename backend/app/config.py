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
    model_config = SettingsConfigDict(
        # Relative paths resolve against the working directory, which for this
        # project means `backend/.env`. Point ENV_FILE somewhere else - at
        # /dev/null, for instance - to run a process that must not be
        # influenced by a developer's local file: the serverless smoke and the
        # secret-key tests both do, because they assert on what happens when a
        # value is absent, and a local `.env` is very good at supplying it.
        env_file=os.getenv("ENV_FILE", ".env"),
        extra="ignore",
    )

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
    # Adyen: the processor most licensed iGaming operators are approved for.
    adyen_api_key: str = ""
    adyen_merchant_account: str = ""
    adyen_hmac_key: str = ""            # webhook HMAC key from the Customer Area
    adyen_environment: str = "test"     # test | live
    adyen_balance_account_id: str = ""  # required only for payouts
    cryptopay_api_key: str = ""
    cryptopay_webhook_secret: str = ""
    # HMAC key for callbacks from locally negotiated bank/wallet rails
    # (the paths configured in the admin panel). Without it, unsigned
    # callbacks to /api/payments/webhooks/bank_transfer are REFUSED - a
    # missing secret must never mean "accept anything".
    bank_transfer_webhook_secret: str = ""

    # The merchant / account id this deployment collects into, used as the
    # default `account_id` on seeded banking pathways. NOT a secret: it is the
    # number printed on the player's own statement and the same in test and
    # live for a given contract. Per-pathway values live in `banking_methods`,
    # because a global acquirer and a local bank do not share one.
    merchant_account_id: str = ""

    # Region defaults for the cashier, used when a player's country has no
    # pathway configured yet and for the "you are paying in" hint.
    default_country: str = "BT"
    default_currency: str = "BTN"

    # --- jurisdiction policy ------------------------------------------------
    # How the platform decides which countries may register, play and move
    # money. This is a POLICY switch, not a safety mechanism:
    #
    #   allow_all  -> every country is accepted (the default here)
    #   blocklist  -> every country except JURISDICTION_BLOCKLIST
    #   allowlist  -> only the countries in JURISDICTION_ALLOWLIST
    #
    # RESTRICTED_REGIONS is a middle tier for markets where you want accounts
    # and gameplay but not payment rails: registration and play are allowed,
    # deposits and withdrawals are refused with a specific message.
    #
    # Whatever you set here, you are the operator: the licence you hold, and
    # the market you accept players from, are your decisions and your
    # liability. Player-protection rules (age, KYC, AML flags, limits,
    # self-exclusion) are enforced separately in services/compliance.py and
    # are NOT affected by this switch.
    jurisdiction_mode: str = "allow_all"
    jurisdiction_blocklist: str = ""            # ISO-3166 alpha-2, comma separated
    jurisdiction_allowlist: str = ""            # used when mode = allowlist
    restricted_regions: str = ""                # accounts yes, payment rails no
    # Trust the CDN's country header (see services/geo.py) as the caller's
    # region. Off by default: with allow_all it changes nothing, and an
    # operator who turns on geo-blocking should confirm their edge sets it.
    geo_enforcement: bool = False

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

    # --- localization -------------------------------------------------------
    # Localization is a DISPLAY concern here. The ledger keeps one settlement
    # currency (USD, integer cents) and every balance, bet and limit is stored
    # and enforced in it. These settings decide how amounts are presented and
    # in what language; see app/i18n.py for the reasoning.
    default_locale: str = "en"
    supported_locales: str = "en,es,pt,de,fr,it,zh,hi,ar"   # comma separated
    settlement_currency: str = "USD"
    # Display currencies offered to players, comma separated. An empty value
    # falls back to every currency the FX table below knows about.
    display_currencies: str = ""
    # FX rates relative to the settlement currency, as JSON:
    #   FX_RATES={"EUR": 0.92, "GBP": 0.79, "INR": 83.4}
    # These are PRESENTATION rates. They are not used to value a bet, settle a
    # ledger entry, price a payout or enforce a limit - see app/i18n.py. Wire
    # them to your treasury feed (ADMIN: GET/PUT /api/admin/fx-rates) before
    # showing real customers a foreign-currency figure.
    fx_rates: str = ""
    fx_rates_updated_at: str = ""

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
        """Retained for callers that only need the set; see services/jurisdiction."""
        return {c.strip().upper() for c in self.jurisdiction_blocklist.split(",") if c.strip()}

    @property
    def allowlist(self) -> set[str]:
        return {c.strip().upper() for c in self.jurisdiction_allowlist.split(",") if c.strip()}

    @property
    def restricted(self) -> set[str]:
        return {c.strip().upper() for c in self.restricted_regions.split(",") if c.strip()}

    @property
    def locales(self) -> list[str]:
        listed = [loc.strip().lower() for loc in self.supported_locales.split(",") if loc.strip()]
        if self.default_locale.lower() not in listed:
            listed.insert(0, self.default_locale.lower())
        return listed

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


#: Shortest SECRET_KEY allowed in production. HS256 signs with the key
#: directly, so a short key is brute-forceable offline against any token an
#: attacker has already seen - and tokens are handed to every player.
MIN_SECRET_KEY_LENGTH = 32


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
    if s.is_production:
        # The session signing key is the one secret whose absence is invisible:
        # everything still works, and every token is forgeable. So this refuses
        # rather than warns, and it refuses on every way of not having a key -
        # the built-in default, an unset variable, and (the one that used to
        # slip through) a variable that is present but empty, which is exactly
        # what a `.env` line reading `SECRET_KEY=` produces.
        #
        # Length is the check because it is the only property of a key that can
        # be judged without judging the operator: HS256 with a short key is
        # brute-forceable offline against any token the attacker has seen.
        key = s.secret_key or ""
        if not key:
            raise RuntimeError(
                "Refusing to boot in production without SECRET_KEY. Sessions "
                "would be signed with an empty key, which anyone can forge. "
                'Generate one: python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        if key.startswith("dev-only"):
            raise RuntimeError(
                "Refusing to boot in production with the default SECRET_KEY. "
                "Set SECRET_KEY to a 64-char random value."
            )
        if len(key) < MIN_SECRET_KEY_LENGTH:
            raise RuntimeError(
                f"Refusing to boot in production: SECRET_KEY is {len(key)} "
                f"characters, and a key shorter than {MIN_SECRET_KEY_LENGTH} "
                f"is brute-forceable offline. Generate one: "
                f'python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
    return s


settings = get_settings()
