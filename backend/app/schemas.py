"""Pydantic request/response models.

Money crosses this boundary as INTEGER cents (`*_minor`) except where a player
types a decimal amount, in which case the field is a decimal STRING and is
converted with `money.to_minor`. Never a float - floats lose cents.
"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from .models import PaymentMethod
from .money import MoneyError, to_minor


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------
class RegisterIn(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)
    date_of_birth: date
    country: str = Field(min_length=2, max_length=2)
    phone: str | None = Field(default=None, max_length=32)
    accepts_terms: bool
    bonus_code: str | None = Field(default=None, max_length=32)

    @field_validator("username")
    @classmethod
    def _username_charset(cls, v: str) -> str:
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError("username may contain letters, digits, _ and - only")
        return v

    @field_validator("accepts_terms")
    @classmethod
    def _must_accept(cls, v: bool) -> bool:
        if not v:
            raise ValueError("you must accept the terms and confirm you are of legal age")
        return v


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class ProfileUpdateIn(BaseModel):
    display_currency: str | None = Field(default=None, max_length=3)
    phone: str | None = Field(default=None, max_length=32)


class ResponsibleGamblingIn(BaseModel):
    """Limits can be tightened instantly. Raising them is deliberately
    restricted to a future effective date by the router (cooling-off rule)."""

    loss_limit_daily: str | None = None       # decimal string, e.g. "100.00"
    deposit_limit_daily: str | None = None
    self_exclude_days: int | None = Field(default=None, ge=0, le=3650)
    cool_off_hours: int | None = Field(default=None, ge=0, le=24 * 90)


class SeedRotateIn(BaseModel):
    client_seed: str | None = Field(default=None, min_length=4, max_length=64)


class KycSubmitIn(BaseModel):
    full_name: str = Field(min_length=3, max_length=255)
    doc_type: str = Field(pattern="^(passport|national_id|drivers_license|proof_of_address|selfie)$")
    file_ref: str = Field(min_length=3, max_length=255)


# ---------------------------------------------------------------------------
# wallet
# ---------------------------------------------------------------------------
class DepositIn(BaseModel):
    amount: str
    method: PaymentMethod
    idempotency_key: str | None = Field(default=None, max_length=128)
    bonus_code: str | None = Field(default=None, max_length=32)

    @field_validator("method")
    @classmethod
    def _supported(cls, v: PaymentMethod) -> PaymentMethod:
        return v


class WithdrawalIn(BaseModel):
    amount: str
    method: PaymentMethod
    destination: str = Field(min_length=4, max_length=255)
    idempotency_key: str | None = Field(default=None, max_length=128)


class SimulateDepositIn(BaseModel):
    outcome: str = Field(default="succeed", pattern="^(succeed|fail|chargeback)$")


# ---------------------------------------------------------------------------
# games
# ---------------------------------------------------------------------------
class InstantBetIn(BaseModel):
    game: str
    stake: str
    params: dict = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=128)


class MinesStartIn(BaseModel):
    stake: str
    mines: int = Field(default=3, ge=1, le=24)
    idempotency_key: str | None = Field(default=None, max_length=128)


class MinesOpenIn(BaseModel):
    index: int = Field(ge=0, le=24)


class BlackjackDealIn(BaseModel):
    stake: str
    idempotency_key: str | None = Field(default=None, max_length=128)


class BlackjackActionIn(BaseModel):
    action: str = Field(pattern="^(hit|stand|double|split)$")


class CrashBetIn(BaseModel):
    stake: str
    auto_cashout: float | None = Field(default=None, ge=1.01, le=1_000_000)
    idempotency_key: str | None = Field(default=None, max_length=128)


class CashoutIn(BaseModel):
    target: float | None = Field(default=None, ge=1.01, le=1_000_000)


class VerifySeedIn(BaseModel):
    server_seed: str
    client_seed: str
    nonce: int
    game: str = "crash"
    params: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# admin
# ---------------------------------------------------------------------------
class ReviewWithdrawalIn(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=500)
    reason: str | None = Field(default=None, max_length=255)


class AdminAdjustIn(BaseModel):
    amount: str                                 # signed decimal string, e.g. "-10.00"
    reason: str = Field(min_length=3, max_length=255)


class UserAdminUpdateIn(BaseModel):
    is_active: bool | None = None
    is_banned: bool | None = None
    role: str | None = Field(default=None, pattern="^(player|support|admin)$")
    kyc_status: str | None = Field(default=None, pattern="^(none|pending|verified|rejected)$")
    email_verified: bool | None = None
    vip_tier: int | None = Field(default=None, ge=0, le=4)
    note: str | None = Field(default=None, max_length=500)


class BonusCreateIn(BaseModel):
    user_id: str
    amount: str
    code: str = Field(min_length=2, max_length=32)
    wager_multiplier: float = Field(default=30, ge=0, le=200)
    reason: str = Field(min_length=3, max_length=255)


class BonusCodeIn(BaseModel):
    code: str = Field(min_length=2, max_length=32)
    bonus_type: str = Field(default="percent", pattern="^(fixed|percent)$")
    value: float = Field(ge=0)
    max_amount: str = "0"
    wager_multiplier: float = Field(default=30, ge=0, le=200)
    uses_left: int = Field(default=100, ge=0)
    min_deposit: str = "0"


class ChatIn(BaseModel):
    body: str = Field(min_length=1, max_length=400)


def money_field(value: str | float | int) -> int:
    try:
        return to_minor(value, "USD")
    except MoneyError as exc:
        raise ValueError(str(exc)) from exc


class TimestampedOut(ORMModel):
    id: str
    created_at: datetime
