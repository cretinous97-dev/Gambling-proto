"""Public content: chat, promotions, leaderboards, site config, legal pages.

The legal text served here is a TEMPLATE. It states plainly what the operator
must do before going live - do not ship it as-is, and do not represent it as
legal advice.
"""
from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import select

from ..config import settings
from ..models import BonusCode, ChatMessage, JackpotPool, User
from ..payments import provider_status
from ..schemas import ChatIn
from ..security import CurrentUser, Db, MaybeUser

router = APIRouter(prefix="/api", tags=["public"])


@router.get("/config")
def site_config():
    return {
        "name": settings.app_name,
        "environment": settings.environment,
        "provider": provider_status(),
        "limits": {
            "min_bet": int(settings.min_bet_usd * 100),
            "max_bet": int(settings.max_bet_usd * 100),
            "min_deposit": int(settings.min_deposit_usd * 100),
            "max_deposit": int(settings.max_deposit_usd * 100),
            "min_withdrawal": int(settings.min_withdrawal_usd * 100),
            "max_withdrawal": int(settings.max_withdrawal_usd * 100),
            "withdrawal_fee_pct": settings.withdrawal_fee_pct,
            "kyc_required_above": int(settings.kyc_required_above_usd * 100),
        },
        "bonuses": {
            "signup_bonus": int(settings.signup_bonus_usd * 100),
            "first_deposit_pct": settings.first_deposit_bonus_pct,
            "first_deposit_cap": int(settings.first_deposit_bonus_max_usd * 100),
            "first_deposit_wager_x": settings.first_deposit_bonus_wager_x,
        },
        "jurisdiction_blocklist": sorted(settings.blocklist),
        # Surface the deployment honestly: a public test deployment moves no
        # real money and may not persist balances between restarts. The UI
        # shows a banner rather than letting testers think they lost funds.
        "deployment": {
            "serverless": settings.serverless,
            "demo_mode": settings.demo_mode,
            "balances_persist": not settings.persistence_is_temporary,
            "real_money": provider_status().get("mode") == "live",
        },
    }


@router.get("/jackpot")
def jackpot(db: Db):
    pool = db.execute(select(JackpotPool)).scalars().first()
    if pool is None:
        pool = JackpotPool(name="Grand", amount=100_000, contribution_pct=0.01)
        db.add(pool)
        db.flush()
    return {
        "name": pool.name,
        "amount": pool.amount,
        "contribution_pct": float(pool.contribution_pct),
    }


@router.get("/promotions")
def promotions(db: Db):
    rows = db.execute(
        select(BonusCode).where(BonusCode.active.is_(True), BonusCode.uses_left > 0)
    ).scalars().all()
    return {
        "promotions": [
            {
                "code": c.code,
                "type": c.bonus_type,
                "value": float(c.value),
                "max_amount": c.max_amount,
                "wager_multiplier": float(c.wager_multiplier),
                "min_deposit": c.min_deposit,
                "uses_left": c.uses_left,
            }
            for c in rows
        ],
        "built_in": {
            "signup_bonus": int(settings.signup_bonus_usd * 100),
            "first_deposit_pct": settings.first_deposit_bonus_pct,
            "first_deposit_cap": int(settings.first_deposit_bonus_max_usd * 100),
            "wager_x": settings.first_deposit_bonus_wager_x,
        },
    }


# ---------------------------------------------------------------------------
# live chat (moderated room; support agents can join from the back office)
# ---------------------------------------------------------------------------
@router.get("/chat")
def chat_history(db: Db, limit: int = Query(default=50, ge=1, le=200)):
    rows = db.execute(
        select(ChatMessage).order_by(ChatMessage.created_at.desc()).limit(limit)
    ).scalars().all()
    return {
        "messages": [
            {
                "id": m.id,
                "username": m.username,
                "body": m.body,
                "vip_tier": m.vip_tier,
                "created_at": m.created_at,
            }
            for m in reversed(rows)
        ]
    }


@router.post("/chat")
def post_chat(payload: ChatIn, user: CurrentUser, db: Db):
    body = payload.body.strip()
    if body.startswith("/"):
        # Tiny command surface, kept intentionally minimal.
        return {"message": {"id": "system", "username": "system",
                            "body": "Commands: /tip is disabled, /help shows the rules.",
                            "vip_tier": 0}}

    msg = ChatMessage(user_id=user.id, username=user.username, body=body, vip_tier=user.vip_tier)
    db.add(msg)
    db.flush()
    return {
        "message": {
            "id": msg.id, "username": msg.username, "body": msg.body,
            "vip_tier": msg.vip_tier, "created_at": msg.created_at,
        }
    }


# ---------------------------------------------------------------------------
# legal templates
# ---------------------------------------------------------------------------
@router.get("/legal/{doc}")
def legal(doc: str):
    docs = {
        "terms": {
            "title": "Terms of Service (template)",
            "sections": [
                {"heading": "Eligibility",
                 "body": "You must be at least 18 (or the legal gambling age in your "
                         "jurisdiction, whichever is higher) and not resident in a blocked "
                         "jurisdiction. Accounts are limited to one per person and per device."},
                {"heading": "Licence",
                 "body": "OPERATOR MUST INSERT: licence number, issuing authority, "
                         "registered company name and address. Operating without one is a "
                         "criminal offence in most markets."},
                {"heading": "Deposits and withdrawals",
                 "body": "Deposits are credited once cleared by the payment provider. "
                         "Withdrawals are reviewed and paid within 24 hours; we may request "
                         "identity documents before the first payout."},
                {"heading": "Bonuses",
                 "body": "All bonuses carry a playthrough requirement, are non-withdrawable "
                         "until met, expire after 30 days, and may be capped. Game weighting "
                         "varies - see the game's rules page."},
                {"heading": "Irregular play",
                 "body": "Collusion, exploitation of software defects, use of bots and "
                         "multi-accounting void all affected bets and may result in account "
                         "closure and referral to the relevant authority."},
                {"heading": "Disputes",
                 "body": "OPERATOR MUST INSERT: dispute resolution process, ADR provider "
                         "and governing law."},
            ],
        },
        "privacy": {
            "title": "Privacy Policy (template)",
            "sections": [
                {"heading": "Data we hold",
                 "body": "Account details, identity documents, transaction records, game "
                         "history, device and IP data."},
                {"heading": "Why we hold it",
                 "body": "To operate your account, to meet AML/CTF and licensing "
                         "obligations, and to prevent fraud. Records are retained for the "
                         "period required by your licence (typically 5-10 years)."},
                {"heading": "Your rights",
                 "body": "OPERATOR MUST INSERT: GDPR/local equivalents - access, "
                         "rectification, erasure (subject to retention duties), portability."},
                {"heading": "Processors",
                 "body": "Payment providers and KYC vendors receive only what they need. "
                         "A current processor list must be published."},
            ],
        },
        "responsible-gambling": {
            "title": "Responsible Gambling",
            "sections": [
                {"heading": "Tools available now",
                 "body": "Deposit limits, loss limits, cool-off periods and self-exclusion "
                         "are all available under Account > Responsible Gambling. Limits "
                         "tighten instantly and cannot be loosened for 24 hours."},
                {"heading": "Self-exclusion",
                 "body": "Self-exclusion blocks deposits and all play for the chosen period. "
                         "It cannot be reversed early. OPERATOR MUST INSERT: national "
                         "self-exclusion scheme registration."},
                {"heading": "Signs to watch",
                 "body": "Playing to escape, chasing losses, gambling past your means, "
                         "hiding it from others. If any apply, take a cool-off today."},
                {"heading": "Get help",
                 "body": "OPERATOR MUST INSERT: local helpline numbers and treatment "
                         "providers for each market served."},
            ],
        },
        "aml": {
            "title": "AML / KYC Policy (template)",
            "sections": [
                {"heading": "Risk-based approach",
                 "body": "Identity is verified before cumulative withdrawals exceed "
                         f"{settings.kyc_required_above_usd:.0f} USD, and on any AML flag "
                         "raised in review."},
                {"heading": "Transaction monitoring",
                 "body": "Deposit/withdrawal velocity, structuring, and mismatch between "
                         "play volume and profile are flagged for human review."},
                {"heading": "Reporting",
                 "body": "OPERATOR MUST INSERT: the local FIU, the reporting threshold "
                         "(commonly 10,000 USD or local equivalent) and the officer's name."},
                {"heading": "Sanctions and PEP screening",
                 "body": "OPERATOR MUST INSERT: screening vendor and refresh cadence."},
            ],
        },
    }
    if doc not in docs:
        from fastapi import HTTPException, status

        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown document {doc!r}")
    return docs[doc]
