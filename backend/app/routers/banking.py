"""Banking method manager: the API behind the admin panel's pathway editor.

An operator adds a bank, wallet or acquirer here and it is live for the markets
it names, without a code change, a release or a restart. This router is the
write side of ``banking_methods``; ``services/routing.py`` is the read side that
the money paths use.

Design rules, each of which is a decision rather than a style:

* **Nothing is ever deleted.** ``DELETE`` deactivates. A settled deposit points
  at its pathway forever, and a reconciliation report that develops holes is
  not a reconciliation report. Deactivated rows stay readable and reversible.
* **Every write is audit-logged with before/after.** "Who switched off the
  Bhutanese rail on Friday, and when exactly" is a question that gets asked.
* **Credentials are references, not values.** ``credential_env`` holds the name
  of an environment variable. This API never accepts, returns or logs a secret;
  it reports whether the variable is *present* in the process, which is the
  only thing an operator actually needs to debug a rail.
* **The live money path is re-read, not cached.** A change here takes effect on
  the next transaction, which is what "dynamic" has to mean to be worth
  anything.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from ..models import AuditLog, BankingMethod, PaymentMethod
from ..schemas import BankingMethodIn, BankingMethodUpdateIn
from ..security import AdminUser, Db
from ..services import routing

log = logging.getLogger("app.routers.banking")

router = APIRouter(prefix="/api/admin", tags=["admin"])

#: The method families the cashier groups by. Kept in sync with the model enum
#: so a typo cannot be saved and then never match a player's choice.
METHOD_KINDS = {m.value for m in PaymentMethod}


def _get_or_404(db: Db, method_id: str) -> BankingMethod:
    row = db.get(BankingMethod, method_id)
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"No banking method with id {method_id!r}."
        )
    return row


def _describe(row: BankingMethod) -> dict:
    body = routing.payload(row)
    body["credential_present"] = routing.credential_present(row)
    return body


def _assert_name_is_free(db: Db, name: str, country: str, currency: str, exclude: str | None = None):
    """The unique key is (name, country, currency).

    Caught here so the operator gets a sentence instead of a 500 from the
    database constraint - and so the message can say which existing row they
    are colliding with.
    """
    query = select(BankingMethod).where(
        BankingMethod.name == name,
        BankingMethod.country_code == country,
        BankingMethod.currency == currency,
    )
    clash = db.execute(query).scalars().first()
    if clash is not None and clash.id != exclude:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{name!r} already exists for {country}/{currency} (id {clash.id}). "
            f"Edit that one, or give this a different name.",
        )


@router.get("/banking-methods")
def list_banking_methods(
    admin: AdminUser,
    db: Db,
    country: str | None = Query(default=None),
    currency: str | None = Query(default=None),
    include_inactive: bool = Query(default=True),
):
    """Every configured pathway, in routing order.

    Sorted the way routing resolves them, so the first row in the panel is the
    row that wins for the filter the operator has applied. The list is the
    explanation for "why did this player get that bank".
    """
    query = select(BankingMethod)
    if country:
        query = query.where(
            BankingMethod.country_code.in_([routing.normalise_country(country), routing.ANY])
        )
    if currency:
        query = query.where(
            BankingMethod.currency.in_([routing.normalise_currency(currency), routing.ANY])
        )
    if not include_inactive:
        query = query.where(BankingMethod.active.is_(True))

    rows = db.execute(query).scalars().all()
    rows.sort(
        key=lambda m: (
            not m.active,                       # live pathways first
            -((0 if m.country_code == routing.ANY else 1)
              + (0 if m.currency == routing.ANY else 1)),
            m.priority,
            m.id,
        )
    )

    return {
        "total": len(rows),
        "methods": [_describe(m) for m in rows],
        "providers": list(routing.PROVIDER_NAMES),
        "method_kinds": sorted(METHOD_KINDS),
    }


@router.post("/banking-methods", status_code=status.HTTP_201_CREATED)
def create_banking_method(payload: BankingMethodIn, admin: AdminUser, db: Db):
    """Add a pathway. It is live for its markets the moment this returns."""
    if payload.method not in METHOD_KINDS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown method {payload.method!r}. Valid: {', '.join(sorted(METHOD_KINDS))}.",
        )

    clean = routing.validate(
        name=payload.name,
        country_code=payload.country_code,
        currency=payload.currency,
        provider=payload.provider,
        min_amount_minor=payload.min_amount_minor,
        max_amount_minor=payload.max_amount_minor,
        fee_bps=payload.fee_bps,
        priority=payload.priority,
        account_id=payload.account_id,
        credential_env=payload.credential_env,
    )
    endpoint = routing.validate_endpoint(payload.api_endpoint)
    _assert_name_is_free(db, clean["name"], clean["country_code"], clean["currency"])

    row = BankingMethod(
        **clean,
        api_endpoint=endpoint,
        method=PaymentMethod(payload.method),
        notes=(payload.notes or None),
        instructions=payload.instructions or {},
        deposits_enabled=payload.deposits_enabled,
        withdrawals_enabled=payload.withdrawals_enabled,
        active=payload.active,
        created_by=admin.id,
    )
    db.add(row)
    db.flush()

    db.add(
        AuditLog(
            actor_id=admin.id,
            actor_email=admin.email,
            action="banking_method.create",
            target=row.id,
            before=None,
            after=_describe(row),
        )
    )
    db.flush()
    log.info("admin %s added banking method %s (%s)", admin.email, row.name, row.id)
    return _describe(row)


@router.patch("/banking-methods/{method_id}")
def update_banking_method(
    method_id: str, payload: BankingMethodUpdateIn, admin: AdminUser, db: Db
):
    """Edit a pathway. Omitted fields are left alone, not blanked."""
    row = _get_or_404(db, method_id)
    before = _describe(row)

    supplied = payload.model_dump(exclude_unset=True)

    if "method" in supplied and supplied["method"] not in METHOD_KINDS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Unknown method {supplied['method']!r}."
        )
    if "api_endpoint" in supplied:
        row.api_endpoint = routing.validate_endpoint(supplied["api_endpoint"])

    # Everything with a rule goes through the same validator as creation, so an
    # edit cannot reach a state that creation would have refused.
    clean = routing.validate(
        name=supplied.get("name", row.name),
        country_code=supplied.get("country_code", row.country_code),
        currency=supplied.get("currency", row.currency),
        provider=supplied.get("provider", row.provider),
        min_amount_minor=supplied.get("min_amount_minor", row.min_amount_minor),
        max_amount_minor=supplied.get("max_amount_minor", row.max_amount_minor),
        fee_bps=supplied.get("fee_bps", row.fee_bps),
        priority=supplied.get("priority", row.priority),
        account_id=supplied.get("account_id", row.account_id),
        credential_env=supplied.get("credential_env", row.credential_env),
    )
    _assert_name_is_free(
        db, clean["name"], clean["country_code"], clean["currency"], exclude=row.id
    )
    for field, value in clean.items():
        setattr(row, field, value)

    if "method" in supplied:
        row.method = PaymentMethod(supplied["method"])
    for field in (
        "deposits_enabled",
        "withdrawals_enabled",
        "active",
        "instructions",
        "notes",
    ):
        if field in supplied:
            value = supplied[field]
            setattr(row, field, value if field != "notes" else (value or None))

    routing.touch(db, row)
    db.add(
        AuditLog(
            actor_id=admin.id,
            actor_email=admin.email,
            action="banking_method.update",
            target=row.id,
            before=before,
            after=_describe(row),
        )
    )
    db.flush()
    return _describe(row)


@router.post("/banking-methods/{method_id}/toggle")
def toggle_banking_method(method_id: str, admin: AdminUser, db: Db):
    """Flip a pathway on or off. The switch an operator reaches for in a hurry."""
    row = _get_or_404(db, method_id)
    before = row.active
    row.active = not row.active
    routing.touch(db, row)

    db.add(
        AuditLog(
            actor_id=admin.id,
            actor_email=admin.email,
            action="banking_method.toggle",
            target=row.id,
            before={"active": before},
            after={"active": row.active},
        )
    )
    db.flush()
    log.warning(
        "admin %s turned banking method %s (%s) %s",
        admin.email, row.name, row.id, "ON" if row.active else "OFF",
    )
    return _describe(row)


@router.delete("/banking-methods/{method_id}")
def retire_banking_method(method_id: str, admin: AdminUser, db: Db):
    """Retire a pathway without deleting it.

    There is no destructive delete and that is deliberate: deposits and
    withdrawals reference this row for reconciliation, and history is not
    allowed to develop gaps because a contract ended.
    """
    row = _get_or_404(db, method_id)
    before = _describe(row)
    row.active = False
    row.deposits_enabled = False
    row.withdrawals_enabled = False
    routing.touch(db, row)

    db.add(
        AuditLog(
            actor_id=admin.id,
            actor_email=admin.email,
            action="banking_method.retire",
            target=row.id,
            before=before,
            after=_describe(row),
        )
    )
    db.flush()
    return {"ok": True, "id": row.id, "active": False, "retired": True}


@router.get("/banking-methods/{method_id}/explain")
def explain_routing(
    method_id: str,
    admin: AdminUser,
    db: Db,
    country: str = Query(..., description="ISO-3166 alpha-2, or *"),
    currency: str = Query(..., description="ISO-4217, or *"),
    amount_minor: int = Query(default=0, ge=0),
    direction: str = Query(default=routing.DEPOSIT),
):
    """Why a given transaction resolves the way it does.

    This is the question every payment incident starts with, and the answer is
    otherwise buried in a sort key. It lists the full ordered candidate set with
    the reason each one did or did not win.
    """
    if direction not in routing.DIRECTIONS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"direction must be one of {routing.DIRECTIONS}"
        )
    country_code = routing.normalise_country(country)
    currency_code = routing.normalise_currency(currency)

    all_rows = db.execute(select(BankingMethod)).scalars().all()
    candidates = routing.candidates(
        db,
        country=country_code,
        currency=currency_code,
        direction=direction,
        amount_minor=amount_minor or None,
    )
    winner = candidates[0].id if candidates else None

    def why(row: BankingMethod) -> str:
        if not row.active:
            return "inactive"
        enabled = (
            row.deposits_enabled if direction == routing.DEPOSIT else row.withdrawals_enabled
        )
        if not enabled:
            return f"{direction}s not enabled on this pathway"
        if row.country_code not in (country_code, routing.ANY):
            return f"country is {row.country_code}, not {country_code} or *"
        if row.currency not in (currency_code, routing.ANY):
            return f"currency is {row.currency}, not {currency_code} or *"
        if amount_minor:
            if row.min_amount_minor and amount_minor < row.min_amount_minor:
                return f"amount below this pathway's minimum ({row.min_amount_minor})"
            if row.max_amount_minor and amount_minor > row.max_amount_minor:
                return f"amount above this pathway's maximum ({row.max_amount_minor})"
        if row.id == winner:
            return "wins"
        return "a more specific or higher-priority pathway wins"

    ordered = sorted(
        all_rows,
        key=lambda m: (
            not m.active,
            -((0 if m.country_code == routing.ANY else 1)
              + (0 if m.currency == routing.ANY else 1)),
            m.priority,
            m.id,
        ),
    )
    return {
        "country": country_code,
        "currency": currency_code,
        "direction": direction,
        "amount_minor": amount_minor,
        "resolved_id": winner,
        "resolved": routing.payload(candidates[0]) if candidates else None,
        "considered": [
            {
                "id": m.id,
                "name": m.name,
                "country_code": m.country_code,
                "currency": m.currency,
                "priority": m.priority,
                "active": m.active,
                "outcome": why(m),
            }
            for m in ordered
        ],
    }
