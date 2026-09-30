"""Data model.

Money convention: every `*_minor` / `amount` column is an INTEGER in USD cents.
Balances are a derived cache of the ledger; `app.ledger` is the source of truth
and every mutation goes through it inside a transaction.
"""
from __future__ import annotations

import enum
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Enum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, UTCDateTime


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# enums
# ---------------------------------------------------------------------------
class UserRole(str, enum.Enum):
    player = "player"
    support = "support"
    admin = "admin"


class AccountKind(str, enum.Enum):
    """Double-entry account types. Every ledger transaction balances to zero
    across this closed set - that is what makes the books auditable."""

    user_available = "user_available"     # spendable cash
    user_bonus = "user_bonus"             # playable bonus, wager-gated
    user_locked = "user_locked"           # reserved for an open bet/withdrawal
    house_revenue = "house_revenue"       # gross gaming revenue (negative = paid out)
    bonus_pool = "bonus_pool"             # operator-funded promotional budget
    payment_clearing = "payment_clearing"  # money in flight with the PSP
    rakeback_pool = "rakeback_pool"
    chargeback_loss = "chargeback_loss"
    fee_income = "fee_income"


class TxType(str, enum.Enum):
    deposit = "deposit"
    deposit_bonus = "deposit_bonus"
    withdrawal_hold = "withdrawal_hold"
    withdrawal_settled = "withdrawal_settled"
    withdrawal_refund = "withdrawal_refund"
    withdrawal_fee = "withdrawal_fee"
    bet_stake = "bet_stake"
    bet_payout = "bet_payout"
    bet_refund = "bet_refund"
    bonus_claim = "bonus_claim"
    bonus_expiry = "bonus_expiry"
    rakeback = "rakeback"
    admin_adjustment = "admin_adjustment"
    chargeback = "chargeback"


class TxStatus(str, enum.Enum):
    pending = "pending"
    posted = "posted"
    reversed = "reversed"


class DepositStatus(str, enum.Enum):
    pending = "pending"
    requires_action = "requires_action"   # 3DS / wallet confirm screen
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    chargeback = "chargeback"


class WithdrawalStatus(str, enum.Enum):
    requested = "requested"
    under_review = "under_review"
    approved = "approved"
    rejected = "rejected"
    paid = "paid"
    cancelled = "cancelled"


class PaymentMethod(str, enum.Enum):
    card = "card"
    bank_transfer = "bank_transfer"
    crypto_btc = "crypto_btc"
    crypto_eth = "crypto_eth"
    crypto_usdt = "crypto_usdt"
    ewallet = "ewallet"


class KycStatus(str, enum.Enum):
    none = "none"
    pending = "pending"
    verified = "verified"
    rejected = "rejected"


class RoundStatus(str, enum.Enum):
    betting = "betting"
    running = "running"
    crashed = "crashed"
    settled = "settled"


# ---------------------------------------------------------------------------
# users / auth
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.player)

    #: What the player *reads* money in. Set from their country at signup and
    #: theirs to change; it never decides which rail a payment takes.
    display_currency: Mapped[str] = mapped_column(String(3), default="USD")
    #: What the player *reads* the interface in - a BCP-47 tag (`pt-BR`) and its
    #: base language (`pt`). Both stored because a browser needs the tag for
    #: date and number formatting and the catalogue is keyed by language.
    locale: Mapped[str | None] = mapped_column(String(16))
    language: Mapped[str | None] = mapped_column(String(8))
    country: Mapped[str] = mapped_column(String(2), default="BT")
    date_of_birth: Mapped[datetime | None] = mapped_column(UTCDateTime)
    phone: Mapped[str | None] = mapped_column(String(32))

    kyc_status: Mapped[KycStatus] = mapped_column(Enum(KycStatus), default=KycStatus.none)
    kyc_full_name: Mapped[str | None] = mapped_column(String(255))
    kyc_document_ref: Mapped[str | None] = mapped_column(String(255))

    # responsible gambling
    self_excluded_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    cool_off_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    loss_limit_daily: Mapped[int | None] = mapped_column(BigInteger)   # USD cents
    deposit_limit_daily: Mapped[int | None] = mapped_column(BigInteger)

    # provably fair: client seed is player-controlled, server seed stays secret
    # until the player rotates it, then it is revealed and its hash is auditable.
    client_seed: Mapped[str] = mapped_column(String(128), default=lambda: secrets.token_hex(8))
    server_seed: Mapped[str] = mapped_column(String(128), default=lambda: secrets.token_hex(32))
    server_seed_hash: Mapped[str] = mapped_column(String(64), default="")
    nonce: Mapped[int] = mapped_column(BigInteger, default=0)
    prev_server_seeds: Mapped[list] = mapped_column(JSON, default=list)

    # loyalty
    vip_tier: Mapped[int] = mapped_column(Integer, default=0)
    wagered_lifetime: Mapped[int] = mapped_column(BigInteger, default=0)
    rakeback_claimed: Mapped[int] = mapped_column(BigInteger, default=0)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    balances: Mapped[list["Balance"]] = relationship(
        back_populates="user",
        lazy="selectin",
        primaryjoin="User.id == foreign(Balance.user_id)",
    )

    @property
    def is_admin(self) -> bool:
        return self.role in (UserRole.admin,)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class SessionAudit(Base):
    __tablename__ = "session_audit"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str | None] = mapped_column(String(32), index=True)
    action: Mapped[str] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ---------------------------------------------------------------------------
# ledger
# ---------------------------------------------------------------------------
class Balance(Base):
    """Cached materialised balance per (owner, account kind).

    `user_id` carries the player id, or the sentinel ``app.ledger.SYSTEM_OWNER``
    for operator accounts (house revenue, clearing, bonus pool...). It is
    intentionally NOT a foreign key: system accounts must be able to own a
    balance row, and the ledger - not the cache - is the source of truth for
    who is owed what.
    """

    __tablename__ = "balances"
    __table_args__ = (UniqueConstraint("user_id", "kind", name="uq_balance_user_kind"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[AccountKind] = mapped_column(Enum(AccountKind), index=True)
    amount: Mapped[int] = mapped_column(BigInteger, default=0)      # USD cents
    locked: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow
    )

    user: Mapped[User | None] = relationship(
        back_populates="balances",
        primaryjoin="foreign(Balance.user_id) == User.id",
        viewonly=True,
    )


class LedgerTransaction(Base):
    """One business event (deposit, bet, payout...). Groups its entries."""

    __tablename__ = "ledger_transactions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    type: Mapped[TxType] = mapped_column(Enum(TxType), index=True)
    status: Mapped[TxStatus] = mapped_column(Enum(TxStatus), default=TxStatus.posted)
    user_id: Mapped[str | None] = mapped_column(String(32), index=True)
    reference: Mapped[str | None] = mapped_column(String(128), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    memo: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, index=True
    )

    entries: Mapped[list["LedgerEntry"]] = relationship(
        back_populates="transaction", lazy="selectin", cascade="all, delete-orphan"
    )


class LedgerEntry(Base):
    """Signed movement on one account. Sum over a transaction == 0."""

    __tablename__ = "ledger_entries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    transaction_id: Mapped[str] = mapped_column(
        ForeignKey("ledger_transactions.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str | None] = mapped_column(String(32), index=True)
    kind: Mapped[AccountKind] = mapped_column(Enum(AccountKind), index=True)
    amount: Mapped[int] = mapped_column(BigInteger)          # signed USD cents
    balance_after: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    transaction: Mapped[LedgerTransaction] = relationship(back_populates="entries")


Index("ix_ledger_entries_user_kind", LedgerEntry.user_id, LedgerEntry.kind)


# ---------------------------------------------------------------------------
# payments
# ---------------------------------------------------------------------------
class BankingMethod(Base):
    """A payment pathway, defined as data rather than as code.

    Which bank, wallet or card scheme a player in a given country can use is a
    commercial decision that changes per market, per contract and per month. It
    does not belong in a router, a provider adapter or a deploy. This table is
    that decision, and the admin panel is how it is edited.

    Two columns carry `'*'` as a wildcard: ``country_code`` and ``currency``.
    A row matching every country at priority 100 is the fallback that catches
    a player in a market nobody has configured yet; a specific row at priority
    10 wins for that market. Resolution is ``ORDER BY priority, id`` so the
    outcome is stable and explainable rather than "whichever row came back
    first" - an operator debugging "why did this player get that bank" needs a
    deterministic answer.

    What is deliberately NOT stored here: credentials. ``credential_env`` holds
    the *name* of the environment variable that carries the secret, never the
    secret itself. A payments table is read by every admin session, exported by
    every reporting query and copied into every staging database; a key stored
    here would leak through all three without anyone doing anything wrong. The
    name is enough for the adapter to find the value at call time.

    ``api_endpoint`` is the institution's URL, kept per row because a local
    rail (a Bhutanese wallet, a regional bank) and a global acquirer do not
    share one. It is validated to be https in production - a deposit endpoint
    reached over plain http discloses the amount, the account and the session.
    """

    __tablename__ = "banking_methods"
    __table_args__ = (
        UniqueConstraint("name", "country_code", "currency", name="uq_banking_method"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)

    #: What the player sees, and what staff call it on the phone ("Bank of
    #: Bhutan mBoB"), not the internal product code.
    name: Mapped[str] = mapped_column(String(120))
    #: ISO-3166 alpha-2, or '*' for every country.
    country_code: Mapped[str] = mapped_column(String(2), default="*", index=True)
    #: ISO-4217, or '*' for every currency.
    currency: Mapped[str] = mapped_column(String(3), default="*", index=True)

    #: Merchant / account / biller id at the institution (the "who is being
    #: paid" half of the request). Not a secret: it appears on the player's own
    #: statement and is the same number in test and live.
    account_id: Mapped[str] = mapped_column(String(120), default="")
    #: Base URL for this pathway's API. Empty means "use the adapter default".
    api_endpoint: Mapped[str] = mapped_column(String(255), default="")
    #: NAME of the env var holding this pathway's credential - never the value.
    credential_env: Mapped[str] = mapped_column(String(64), default="")

    #: Which adapter speaks to it: adyen, stripe, cryptopay, bank_transfer,
    #: ewallet, sandbox. Must be a registered provider - validated on write so
    #: a typo cannot be saved and then silently fail on a real deposit.
    provider: Mapped[str] = mapped_column(String(32), default="bank_transfer")
    #: The player-facing product family, for grouping in the cashier.
    method: Mapped[PaymentMethod] = mapped_column(
        Enum(PaymentMethod), default=PaymentMethod.bank_transfer
    )

    deposits_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    withdrawals_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    #: The master switch. Deactivating never deletes: a settled deposit points
    #: at this row forever, and history must not develop holes.
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    #: Lower wins. Ties break on id so routing is never arbitrary.
    priority: Mapped[int] = mapped_column(Integer, default=100)

    #: Per-transaction bounds in minor units. 0 means "no bound from this row";
    #: the global config limits still apply on top.
    min_amount_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    max_amount_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    #: Fee in basis points (25 = 0.25%). Integer, like every other rate here.
    fee_bps: Mapped[int] = mapped_column(Integer, default=0)

    #: Player-facing next steps when this rail is chosen out-of-band: the bank
    #: account to transfer to, the wallet handle to send to, the reference
    #: format. Shown verbatim, so it must never contain a credential.
    instructions: Mapped[dict] = mapped_column(JSON, default=dict)
    #: Free text for staff: contract, contact, why it was switched off.
    notes: Mapped[str | None] = mapped_column(Text)

    created_by: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow
    )


class Deposit(Base):
    __tablename__ = "deposits"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    amount: Mapped[int] = mapped_column(BigInteger)                 # requested, cents
    credited: Mapped[int] = mapped_column(BigInteger, default=0)    # net credited
    bonus_credited: Mapped[int] = mapped_column(BigInteger, default=0)
    fee: Mapped[int] = mapped_column(BigInteger, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    method: Mapped[PaymentMethod] = mapped_column(Enum(PaymentMethod))
    provider: Mapped[str] = mapped_column(String(32), default="sandbox")
    #: The banking_methods row this was routed through, resolved at creation.
    #: Stored rather than re-derived: routing rules change, and reconciliation
    #: needs to know which account the money actually went to, not which row
    #: would win today.
    banking_method_id: Mapped[str | None] = mapped_column(String(32), index=True)
    provider_ref: Mapped[str | None] = mapped_column(String(128), index=True)
    status: Mapped[DepositStatus] = mapped_column(
        Enum(DepositStatus), default=DepositStatus.pending, index=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    failure_reason: Mapped[str | None] = mapped_column(String(255))
    # sandbox/3DS artefact - never store real PANs. Last4 only, ever.
    card_last4: Mapped[str | None] = mapped_column(String(4))
    crypto_address: Mapped[str | None] = mapped_column(String(128))
    crypto_txid: Mapped[str | None] = mapped_column(String(128))
    confirmations: Mapped[int] = mapped_column(Integer, default=0)
    # Provider-supplied next-step payload (checkout URL, wallet address, bank
    # details). Persisted so a page reload can resume an unfinished checkout.
    instructions: Mapped[dict] = mapped_column(JSON, default=dict)
    bonus_code: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Withdrawal(Base):
    __tablename__ = "withdrawals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    amount: Mapped[int] = mapped_column(BigInteger)      # requested, cents
    fee: Mapped[int] = mapped_column(BigInteger, default=0)
    net_amount: Mapped[int] = mapped_column(BigInteger, default=0)
    method: Mapped[PaymentMethod] = mapped_column(Enum(PaymentMethod))
    destination: Mapped[str] = mapped_column(String(255))   # masked wallet/IBAN
    provider: Mapped[str] = mapped_column(String(32), default="sandbox")
    #: The banking_methods row chosen for the payout. The reviewer sees it, and
    #: it is what reconciles against the institution's statement.
    banking_method_id: Mapped[str | None] = mapped_column(String(32), index=True)
    provider_ref: Mapped[str | None] = mapped_column(String(128), index=True)
    # The provider's *payout instrument* id (Adyen transferInstrumentId, Stripe
    # connected-account bank account token...). `destination` above is the
    # masked string shown to staff; this is the token actually used to move
    # money, and it must be a verified instrument - never free text from a form.
    payout_ref: Mapped[str | None] = mapped_column(String(191))
    status: Mapped[WithdrawalStatus] = mapped_column(
        Enum(WithdrawalStatus), default=WithdrawalStatus.requested, index=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(32))
    review_note: Mapped[str | None] = mapped_column(Text)
    rejection_reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    paid_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class PaymentWebhook(Base):
    """Raw provider callbacks, stored before processing so replays are visible."""

    __tablename__ = "payment_webhooks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    event_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    signature_valid: Mapped[bool] = mapped_column(Boolean, default=False)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ---------------------------------------------------------------------------
# games
# ---------------------------------------------------------------------------
class GameRound(Base):
    """Crash / shared-round games. Instant games record a GamePlay instead."""

    __tablename__ = "game_rounds"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    game: Mapped[str] = mapped_column(String(32), index=True, default="crash")
    round_number: Mapped[int] = mapped_column(BigInteger, index=True)
    server_seed: Mapped[str] = mapped_column(String(128))
    server_seed_hash: Mapped[str] = mapped_column(String(64))
    client_seed: Mapped[str] = mapped_column(String(128), default="public")
    nonce: Mapped[int] = mapped_column(BigInteger, default=0)
    crash_point: Mapped[float] = mapped_column(Numeric(12, 4))
    status: Mapped[RoundStatus] = mapped_column(Enum(RoundStatus), default=RoundStatus.betting, index=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    crashed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    settled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Bet(Base):
    """A single wager. One row per stake, including its settlement."""

    __tablename__ = "bets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    game: Mapped[str] = mapped_column(String(32), index=True)
    round_id: Mapped[str | None] = mapped_column(String(32), index=True)
    stake: Mapped[int] = mapped_column(BigInteger)              # cents
    stake_source: Mapped[AccountKind] = mapped_column(
        Enum(AccountKind), default=AccountKind.user_available
    )
    payout: Mapped[int] = mapped_column(BigInteger, default=0)
    multiplier: Mapped[float] = mapped_column(Numeric(14, 6), default=0)
    profit: Mapped[int] = mapped_column(BigInteger, default=0)
    # provably-fair inputs actually used for this wager
    server_seed_hash: Mapped[str | None] = mapped_column(String(64))
    client_seed: Mapped[str | None] = mapped_column(String(128))
    nonce: Mapped[int | None] = mapped_column(BigInteger)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    settled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    wager_contribution: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    settled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


# ---------------------------------------------------------------------------
# bonus / marketing / compliance
# ---------------------------------------------------------------------------
class BonusGrant(Base):
    __tablename__ = "bonus_grants"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(32), index=True)
    amount: Mapped[int] = mapped_column(BigInteger)
    wager_required: Mapped[int] = mapped_column(BigInteger, default=0)
    wagered: Mapped[int] = mapped_column(BigInteger, default=0)
    max_cashout: Mapped[int | None] = mapped_column(BigInteger)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    converted: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(32), default="signup")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class BonusCode(Base):
    __tablename__ = "bonus_codes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    bonus_type: Mapped[str] = mapped_column(String(16), default="fixed")  # fixed|percent
    value: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    max_amount: Mapped[int] = mapped_column(BigInteger, default=0)
    wager_multiplier: Mapped[float] = mapped_column(Numeric(8, 2), default=30)
    uses_left: Mapped[int] = mapped_column(Integer, default=100)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    min_deposit: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class KycDocument(Base):
    __tablename__ = "kyc_documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    doc_type: Mapped[str] = mapped_column(String(32))       # passport|id|proof_of_address|selfie
    file_ref: Mapped[str] = mapped_column(String(255))      # object-store key, NOT the image
    status: Mapped[KycStatus] = mapped_column(Enum(KycStatus), default=KycStatus.pending)
    reviewer_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class LimitUsage(Base):
    """Rolling daily aggregates for deposit/loss limit enforcement."""

    __tablename__ = "limit_usage"
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_limit_user_day"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    day: Mapped[str] = mapped_column(String(10), index=True)   # YYYY-MM-DD UTC
    deposit_total: Mapped[int] = mapped_column(BigInteger, default=0)
    loss_total: Mapped[int] = mapped_column(BigInteger, default=0)
    wager_total: Mapped[int] = mapped_column(BigInteger, default=0)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(128))
    body: Mapped[str] = mapped_column(Text)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    username: Mapped[str] = mapped_column(String(64))
    body: Mapped[str] = mapped_column(Text)
    vip_tier: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class AuditLog(Base):
    """Every admin/support action lands here. Non-negotiable for a licensed op."""

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    actor_id: Mapped[str | None] = mapped_column(String(32), index=True)
    actor_email: Mapped[str | None] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str | None] = mapped_column(String(64))
    before: Mapped[dict] = mapped_column(JSON, default=dict)
    after: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AppSetting(Base):
    """Small key/value store for values that must survive a restart.

    Used for one thing today: a signing key generated at boot when SECRET_KEY
    is not configured. Storing it here keeps sessions valid across cold starts
    instead of logging every player out each time a serverless instance is
    recycled.
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(512))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class JackpotPool(Base):
    __tablename__ = "jackpot_pools"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    amount: Mapped[int] = mapped_column(BigInteger, default=0)
    contribution_pct: Mapped[float] = mapped_column(Numeric(6, 4), default=0.01)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow
    )


def in_days(days: int) -> datetime:
    return utcnow() + timedelta(days=days)
