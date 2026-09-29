"""The banking method manager: dynamic routing, admin CRUD, rail callbacks.

What this file is protecting
----------------------------
Every deposit used to go to whichever provider `PAYMENT_PROVIDER` named. Adding
a Bhutanese wallet, a regional bank or a second acquirer meant a code change and
a release, and an operator could not run two rails at once at all.

The tests below are written against the properties that make the replacement
safe, in the order an incident would find them:

  1. **Routing is deterministic and explainable.** Specific beats wildcard,
     priority breaks ties, and the same inputs always pick the same rail.
  2. **A player cannot choose their own rail.** Passing a pathway id is a
     preference, not an authority: it is honoured only if the operator has
     enabled that pathway for that account's country and that amount.
  3. **A rail is never credited twice or by the wrong amount.** A replayed
     callback is a no-op; a callback that disagrees with the deposit is
     refused.
  4. **Credentials never enter the database.** The column holds a variable
     name, and the API refuses anything that looks like a key.
  5. **Simulation cannot be reached through a live rail.** The sandbox's
     simulate endpoint is gated on the provider that carries *that* deposit,
     which after routing is not necessarily the deployment default.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest
from sqlalchemy import select

from app.config import settings
from app.db import session_scope
from app.models import BankingMethod, Deposit, DepositStatus, Withdrawal
from app.services import routing
from tests.conftest import auth_headers, cash, register

ADMIN = "/api/admin/banking-methods"


def make_pathway(**overrides) -> dict:
    """A valid create payload, with anything overridden per test."""
    body = {
        "name": "Test Bank",
        "country_code": "BT",
        "currency": "BTN",
        "provider": "bank_transfer",
        "method": "bank_transfer",
        "account_id": "201835782",
        "credential_env": "TESTBANK_API_KEY",
        "deposits_enabled": True,
        "withdrawals_enabled": True,
        "active": True,
        "priority": 50,
        "min_amount_minor": 0,
        "max_amount_minor": 0,
        "fee_bps": 0,
    }
    body.update(overrides)
    return body


@pytest.fixture
def clear_pathways():
    """Start from an empty registry so seeded rows cannot decide a test."""
    with session_scope() as db:
        db.query(BankingMethod).delete()
        db.commit()
    yield
    with session_scope() as db:
        db.query(BankingMethod).delete()
        db.commit()


# ---------------------------------------------------------------------------
# routing: which pathway wins
# ---------------------------------------------------------------------------
def test_a_specific_market_beats_the_global_fallback(client, admin_headers, clear_pathways):
    client.post(ADMIN, json=make_pathway(name="Fallback", country_code="*", currency="*", priority=1), headers=admin_headers)
    client.post(ADMIN, json=make_pathway(name="Bhutan Local", country_code="BT", currency="BTN", priority=900), headers=admin_headers)

    with session_scope() as db:
        picked = routing.resolve(db, country="BT", currency="BTN", direction=routing.DEPOSIT)

    # Priority says the fallback should win (1 before 900). Specificity says
    # otherwise, and specificity is the point: adding a local rail must take
    # effect for local players even if it is configured last and numbered badly.
    assert picked.name == "Bhutan Local"


def test_priority_breaks_a_tie_between_equally_specific_pathways(client, admin_headers, clear_pathways):
    client.post(ADMIN, json=make_pathway(name="Second", priority=80), headers=admin_headers)
    client.post(ADMIN, json=make_pathway(name="First", priority=20), headers=admin_headers)

    with session_scope() as db:
        picked = routing.resolve(db, country="BT", currency="BTN", direction=routing.DEPOSIT)

    assert picked.name == "First"


def test_an_inactive_pathway_is_never_selected(client, admin_headers, clear_pathways):
    created = client.post(ADMIN, json=make_pathway(name="Switched Off"), headers=admin_headers).json()
    client.post(f"{ADMIN}/{created['id']}/toggle", headers=admin_headers)

    with session_scope() as db:
        assert routing.resolve(db, country="BT", currency="BTN", direction=routing.DEPOSIT) is None


def test_a_deposit_only_pathway_is_not_used_for_payouts(client, admin_headers, clear_pathways):
    client.post(ADMIN, json=make_pathway(name="Deposits Only", withdrawals_enabled=False), headers=admin_headers)
    client.post(ADMIN, json=make_pathway(name="Payouts Only", deposits_enabled=False, withdrawals_enabled=True, priority=60), headers=admin_headers)

    with session_scope() as db:
        inbound = routing.resolve(db, country="BT", currency="BTN", direction=routing.DEPOSIT)
        outbound = routing.resolve(db, country="BT", currency="BTN", direction=routing.WITHDRAWAL)

    assert inbound.name == "Deposits Only"
    assert outbound.name == "Payouts Only"


def test_amount_bounds_exclude_a_pathway(client, admin_headers, clear_pathways):
    client.post(
        ADMIN,
        json=make_pathway(name="Small Only", min_amount_minor=1_000, max_amount_minor=50_000),
        headers=admin_headers,
    )

    with session_scope() as db:
        assert routing.resolve(db, country="BT", currency="BTN", direction=routing.DEPOSIT, amount_minor=5_000) is not None
        assert routing.resolve(db, country="BT", currency="BTN", direction=routing.DEPOSIT, amount_minor=500_000) is None


def test_an_unconfigured_market_gets_no_pathway_rather_than_the_wrong_one(client, admin_headers, clear_pathways):
    """A configured deployment is authoritative; it must not guess.

    Falling back to "some provider" for a market nobody configured is how money
    ends up in an account the operator never chose - worse than a refusal,
    because a refusal is visible to the player immediately.
    """
    client.post(ADMIN, json=make_pathway(name="Bhutan Only"), headers=admin_headers)

    with session_scope() as db:
        assert routing.resolve(db, country="DE", currency="EUR", direction=routing.DEPOSIT) is None


def test_routing_is_unknown_country_safe(client, admin_headers, clear_pathways):
    """A wildcard pathway must serve a player with no usable country.

    This is the "allow global access" guarantee: a market nobody has configured
    still has somewhere to pay.
    """
    client.post(ADMIN, json=make_pathway(name="World", country_code="*", currency="*"), headers=admin_headers)

    with session_scope() as db:
        for country in ("", "ZZ", "BT", "US"):
            picked = routing.resolve(db, country=country, currency="USD", direction=routing.DEPOSIT)
            assert picked is not None, f"{country!r} was refused despite a wildcard pathway"


# ---------------------------------------------------------------------------
# validation: a row that saves is a row that works
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("field", "value", "why"),
    [
        ("country_code", "BHT", "not an ISO-3166 alpha-2 code"),
        ("currency", "BT", "not an ISO-4217 code"),
        ("provider", "paypal", "not a registered adapter"),
        ("method", "cheque", "not a cashier method family"),
        ("priority", 99_999, "beyond the allowed range"),
        ("fee_bps", 20_000, "more than 100%"),
    ],
)
def test_an_invalid_pathway_is_refused_at_the_form(client, admin_headers, clear_pathways, field, value, why):
    resp = client.post(ADMIN, json=make_pathway(**{field: value}), headers=admin_headers)
    assert resp.status_code == 400, f"{field}={value!r} should be rejected ({why}): {resp.text}"


def test_a_minimum_above_the_maximum_is_refused(client, admin_headers, clear_pathways):
    """It would save a pathway no transaction could ever qualify for."""
    resp = client.post(
        ADMIN,
        json=make_pathway(min_amount_minor=500_000, max_amount_minor=1_000),
        headers=admin_headers,
    )
    assert resp.status_code == 400
    assert "no transaction" in resp.json()["detail"]


def test_the_credential_field_refuses_an_actual_key(client, admin_headers, clear_pathways):
    """The column holds a variable NAME. Pasting a key must not be possible.

    This is the difference between a key that lives in the environment and a key
    that ships in every database dump, support screenshot and staging copy.
    """
    resp = client.post(
        ADMIN,
        json=make_pathway(credential_env="sk_live_51H8xQ2eZvKYlo2C"),
        headers=admin_headers,
    )
    assert resp.status_code == 400
    assert "environment variable" in resp.json()["detail"]


def test_a_plaintext_endpoint_is_refused_in_production(client, admin_headers, clear_pathways, monkeypatch):
    monkeypatch.setattr(type(settings), "is_production", property(lambda self: True))
    resp = client.post(
        ADMIN,
        json=make_pathway(api_endpoint="http://bank.example.com/pay"),
        headers=admin_headers,
    )
    assert resp.status_code == 400
    assert "https" in resp.json()["detail"]


def test_a_loopback_endpoint_is_allowed_even_in_production(client, admin_headers, clear_pathways, monkeypatch):
    """A sidecar or local aggregator is reached over http and never leaves the host."""
    monkeypatch.setattr(type(settings), "is_production", property(lambda self: True))
    resp = client.post(
        ADMIN,
        json=make_pathway(api_endpoint="http://127.0.0.1:9100/pay"),
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text


def test_a_duplicate_pathway_is_refused_with_a_usable_message(client, admin_headers, clear_pathways):
    assert client.post(ADMIN, json=make_pathway(), headers=admin_headers).status_code == 201
    again = client.post(ADMIN, json=make_pathway(), headers=admin_headers)
    assert again.status_code == 409
    assert "already exists" in again.json()["detail"]


def test_the_credential_value_is_never_returned(client, admin_headers, clear_pathways, monkeypatch):
    monkeypatch.setenv("TESTBANK_API_KEY", "super-secret-value")
    created = client.post(ADMIN, json=make_pathway(), headers=admin_headers).json()

    assert created["credential_env"] == "TESTBANK_API_KEY"
    assert "super-secret-value" not in json.dumps(created)
    assert created["credential_present"] is True


# ---------------------------------------------------------------------------
# admin CRUD
# ---------------------------------------------------------------------------
def test_an_admin_can_add_edit_and_retire_a_pathway(client, admin_headers, clear_pathways):
    created = client.post(ADMIN, json=make_pathway(), headers=admin_headers).json()
    method_id = created["id"]
    assert created["active"] is True

    edited = client.patch(
        f"{ADMIN}/{method_id}",
        json={"name": "Renamed Bank", "priority": 5},
        headers=admin_headers,
    ).json()
    assert edited["name"] == "Renamed Bank"
    assert edited["priority"] == 5
    # An omitted field keeps its value rather than being blanked.
    assert edited["currency"] == "BTN"
    assert edited["account_id"] == "201835782"

    retired = client.delete(f"{ADMIN}/{method_id}", headers=admin_headers).json()
    assert retired["retired"] is True

    listed = client.get(ADMIN, headers=admin_headers).json()["methods"]
    row = next(m for m in listed if m["id"] == method_id)
    assert row["active"] is False


def test_a_retired_pathway_still_exists(client, admin_headers, clear_pathways):
    """Retiring is not deleting: history points at these rows forever."""
    created = client.post(ADMIN, json=make_pathway(), headers=admin_headers).json()
    client.delete(f"{ADMIN}/{created['id']}", headers=admin_headers)

    with session_scope() as db:
        assert db.get(BankingMethod, created["id"]) is not None


def test_every_pathway_change_is_audit_logged(client, admin_headers, clear_pathways):
    created = client.post(ADMIN, json=make_pathway(), headers=admin_headers).json()
    client.patch(f"{ADMIN}/{created['id']}", json={"priority": 7}, headers=admin_headers)
    client.post(f"{ADMIN}/{created['id']}/toggle", headers=admin_headers)

    entries = client.get("/api/admin/audit?limit=50", headers=admin_headers).json()
    actions = {e["action"] for e in entries["entries"]}
    assert {"banking_method.create", "banking_method.update", "banking_method.toggle"} <= actions


def test_a_player_cannot_reach_the_banking_admin_api(client, clear_pathways):
    tokens = register(client, "banking_player@example.com", "bankingplayer")
    headers = auth_headers(tokens["access_token"])

    assert client.get(ADMIN, headers=headers).status_code == 403
    assert client.post(ADMIN, json=make_pathway(), headers=headers).status_code == 403
    assert client.post(f"{ADMIN}/anything/toggle", headers=headers).status_code == 403


def test_the_admin_list_explains_which_pathway_wins(client, admin_headers, clear_pathways):
    client.post(ADMIN, json=make_pathway(name="Fallback", country_code="*", currency="*", priority=1), headers=admin_headers)
    client.post(ADMIN, json=make_pathway(name="Local", country_code="BT", currency="BTN"), headers=admin_headers)

    explained = client.get(
        f"{ADMIN}/anything/explain?country=BT&currency=BTN&amount_minor=10000",
        headers=admin_headers,
    ).json()

    assert explained["resolved"]["name"] == "Local"
    outcomes = {row["name"]: row["outcome"] for row in explained["considered"]}
    assert outcomes["Local"] == "wins"
    assert outcomes["Fallback"] == "a more specific or higher-priority pathway wins"


# ---------------------------------------------------------------------------
# the money path uses the routing
# ---------------------------------------------------------------------------
def test_a_deposit_records_the_pathway_it_was_routed_through(client, admin_headers, clear_pathways):
    created = client.post(ADMIN, json=make_pathway(name="Bhutan Local"), headers=admin_headers).json()
    tokens = register(client, f"routed_{uuid.uuid4().hex[:8]}@example.com", f"routed{uuid.uuid4().hex[:6]}")
    headers = auth_headers(tokens["access_token"])

    resp = client.post(
        "/api/wallet/deposits",
        json={"amount": "100.00", "method": "bank_transfer"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text

    with session_scope() as db:
        row = db.execute(
            Deposit.__table__.select().where(Deposit.id == resp.json()["deposit_id"])
        ).first()
    assert row.banking_method_id == created["id"]


def test_a_player_cannot_force_a_switched_off_pathway(client, admin_headers, clear_pathways):
    """Passing an id is a preference. The operator's switch is the authority."""
    created = client.post(ADMIN, json=make_pathway(name="Off Limits"), headers=admin_headers).json()
    client.post(f"{ADMIN}/{created['id']}/toggle", headers=admin_headers)

    tokens = register(client, f"force_{uuid.uuid4().hex[:8]}@example.com", f"force{uuid.uuid4().hex[:6]}")
    resp = client.post(
        "/api/wallet/deposits",
        json={"amount": "50.00", "method": "bank_transfer", "banking_method_id": created["id"]},
        headers=auth_headers(tokens["access_token"]),
    )
    assert resp.status_code == 409
    assert "not available" in resp.json()["detail"]


def test_a_player_cannot_force_a_pathway_from_another_market(client, admin_headers, clear_pathways):
    """The account's country decides, not the request body.

    A player outside the market a rail serves must not be able to route their
    money into it by editing the payload.
    """
    created = client.post(
        ADMIN, json=make_pathway(name="Bhutan Only", country_code="BT", currency="BTN"),
        headers=admin_headers,
    ).json()
    tokens = register(client, "german_player@example.com", "germanplayer", country="DE")

    resp = client.post(
        "/api/wallet/deposits",
        json={"amount": "50.00", "method": "card", "banking_method_id": created["id"]},
        headers=auth_headers(tokens["access_token"]),
    )
    assert resp.status_code == 409


def test_the_cashier_lists_only_what_this_player_can_use(client, admin_headers, clear_pathways):
    client.post(ADMIN, json=make_pathway(name="Bhutan Local"), headers=admin_headers)
    client.post(
        ADMIN,
        json=make_pathway(name="Euro Rail", country_code="DE", currency="EUR", provider="stripe"),
        headers=admin_headers,
    )

    bhutan = register(client, f"cashier_{uuid.uuid4().hex[:8]}@example.com", f"cash{uuid.uuid4().hex[:6]}", country="BT")
    listed = client.get(
        "/api/payments/methods", headers=auth_headers(bhutan["access_token"])
    ).json()

    names = {m["name"] for m in listed["methods"]}
    assert names == {"Bhutan Local"}
    # Player-facing payload must not leak internal topology.
    assert "api_endpoint" not in listed["methods"][0]
    assert "credential_env" not in listed["methods"][0]


def test_a_withdrawal_uses_the_payout_pathway(client, admin_headers, clear_pathways):
    created = client.post(
        ADMIN,
        json=make_pathway(name="Payout Rail", deposits_enabled=False, withdrawals_enabled=True),
        headers=admin_headers,
    ).json()
    email = f"payout_{uuid.uuid4().hex[:8]}@example.com"
    tokens = register(client, email, f"pay{uuid.uuid4().hex[:6]}")
    headers = auth_headers(tokens["access_token"])

    # A payouts-only pathway must refuse deposits, so the float comes from the
    # back office exactly as it would for a manual credit.
    refused = client.post(
        "/api/wallet/deposits",
        json={"amount": "200.00", "method": "bank_transfer", "banking_method_id": created["id"]},
        headers=headers,
    )
    assert refused.status_code == 409

    with session_scope() as db:
        from app.ledger import admin_adjust
        from app.models import User

        user = db.query(User).filter(User.email == email).one()
        admin_adjust(db, user.id, 20_000, reference="test-float", memo="test float")
        db.commit()

    resp = client.post(
        "/api/wallet/withdrawals",
        json={
            "amount": "100.00",
            "method": "bank_transfer",
            "destination": "BT00BANK0000000001",
            "banking_method_id": created["id"],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text

    with session_scope() as db:
        row = db.get(Withdrawal, resp.json()["id"])
    assert row.banking_method_id == created["id"]


def test_registration_still_works_with_no_pathways_configured(client, clear_pathways):
    """An unconfigured deployment must behave exactly as it did before.

    Seeding is a convenience, not a dependency: a fresh database with an empty
    registry still lets a player sign up and deposit through the default
    provider.
    """
    tokens = register(client, "no_pathways@example.com", "nopathways")
    headers = auth_headers(tokens["access_token"])

    resp = client.post("/api/wallet/deposits", json={"amount": "10.00", "method": "card"}, headers=headers)
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# the rail's callbacks
# ---------------------------------------------------------------------------
def sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def rail(client, admin_headers, clear_pathways, monkeypatch):
    """A live bank_transfer pathway with a signing secret, plus a funded player."""
    monkeypatch.setattr(settings, "bank_transfer_webhook_secret", "rail-test-secret")
    created = client.post(ADMIN, json=make_pathway(name="Rail Bank"), headers=admin_headers).json()
    unique = uuid.uuid4().hex[:10]
    tokens = register(client, f"rail_{unique}@example.com", f"rail{unique}")
    headers = auth_headers(tokens["access_token"])
    return {"pathway": created, "headers": headers}


def post_callback(client, body: dict, secret: str | None):
    raw = json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["X-Signature"] = sign(raw, secret)
    return client.post("/api/payments/webhooks/bank_transfer", content=raw, headers=headers)


def test_a_signed_callback_credits_the_deposit_immediately(client, rail):
    """The requirement: wallet credited on a signed webhook from the gateway."""
    created = client.post(
        "/api/wallet/deposits",
        json={"amount": "150.00", "method": "bank_transfer", "banking_method_id": rail["pathway"]["id"]},
        headers=rail["headers"],
    )
    assert created.status_code == 201, created.text
    deposit_id = created.json()["deposit_id"]
    assert cash(client, rail["headers"]["Authorization"].split()[1]) == 0

    resp = post_callback(
        client,
        {
            "event_id": "mob-evt-1",
            "type": "deposit.succeeded",
            "deposit_id": deposit_id,
            "amount_minor": 15_000,
            "provider_ref": "MBOB-991",
        },
        "rail-test-secret",
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "processed"
    assert cash(client, rail["headers"]["Authorization"].split()[1]) == 15_000

    with session_scope() as db:
        assert db.get(Deposit, deposit_id).status is DepositStatus.succeeded


def test_an_unsigned_callback_is_refused(client, rail):
    created = client.post(
        "/api/wallet/deposits",
        json={"amount": "150.00", "method": "bank_transfer", "banking_method_id": rail["pathway"]["id"]},
        headers=rail["headers"],
    ).json()

    resp = post_callback(
        client,
        {"event_id": "unsigned-1", "type": "deposit.succeeded", "deposit_id": created["deposit_id"], "amount_minor": 15_000},
        None,
    )
    assert resp.status_code == 400
    assert cash(client, rail["headers"]["Authorization"].split()[1]) == 0


def test_a_forged_signature_is_refused(client, rail):
    created = client.post(
        "/api/wallet/deposits",
        json={"amount": "150.00", "method": "bank_transfer", "banking_method_id": rail["pathway"]["id"]},
        headers=rail["headers"],
    ).json()

    resp = post_callback(
        client,
        {"event_id": "forged-1", "type": "deposit.succeeded", "deposit_id": created["deposit_id"], "amount_minor": 15_000},
        "not-the-secret",
    )
    assert resp.status_code == 400
    assert cash(client, rail["headers"]["Authorization"].split()[1]) == 0


def test_a_replayed_callback_credits_once(client, rail):
    created = client.post(
        "/api/wallet/deposits",
        json={"amount": "150.00", "method": "bank_transfer", "banking_method_id": rail["pathway"]["id"]},
        headers=rail["headers"],
    ).json()
    event = {
        "event_id": "replay-1",
        "type": "deposit.succeeded",
        "deposit_id": created["deposit_id"],
        "amount_minor": 15_000,
    }

    first = post_callback(client, event, "rail-test-secret")
    second = post_callback(client, event, "rail-test-secret")

    assert first.json()["status"] == "processed"
    assert second.json()["status"] == "duplicate"
    assert cash(client, rail["headers"]["Authorization"].split()[1]) == 15_000


def test_a_callback_for_the_wrong_amount_is_refused(client, rail):
    """A signed callback is authentic - which is not the same as correct.

    Crediting whatever the rail reports would turn a mistyped reference into
    money the player never paid, and a balance the operator cannot reconcile.
    """
    created = client.post(
        "/api/wallet/deposits",
        json={"amount": "150.00", "method": "bank_transfer", "banking_method_id": rail["pathway"]["id"]},
        headers=rail["headers"],
    ).json()

    resp = post_callback(
        client,
        {
            "event_id": "mismatch-1",
            "type": "deposit.succeeded",
            "deposit_id": created["deposit_id"],
            "amount_minor": 15_000_00,      # 100x what was requested
        },
        "rail-test-secret",
    )
    assert resp.status_code == 422
    assert "mismatch" in resp.json()["detail"]
    assert cash(client, rail["headers"]["Authorization"].split()[1]) == 0


def test_a_callback_naming_no_known_deposit_is_refused(client, rail):
    resp = post_callback(
        client,
        {"event_id": "orphan-1", "type": "deposit.succeeded", "reference": "DEP-NOPE", "amount_minor": 100},
        "rail-test-secret",
    )
    assert resp.status_code == 422
    assert "no deposit matches" in resp.json()["detail"]


def test_callbacks_are_refused_when_no_signing_secret_is_set(client, admin_headers, clear_pathways, monkeypatch):
    """A missing secret must never mean "accept anything"."""
    monkeypatch.setattr(settings, "bank_transfer_webhook_secret", "")
    client.post(ADMIN, json=make_pathway(name="Rail Bank"), headers=admin_headers)

    raw = json.dumps({"event_id": "x", "type": "deposit.succeeded"}).encode()
    resp = client.post(
        "/api/payments/webhooks/bank_transfer",
        content=raw,
        headers={"Content-Type": "application/json", "X-Signature": "anything"},
    )
    assert resp.status_code == 400


def test_a_provider_with_a_live_pathway_may_post_its_callbacks(client, admin_headers, clear_pathways, monkeypatch):
    """Multi-rail: the deployment default is not the only provider allowed.

    Before this, a deployment settling through two rails had every callback
    from the second one answered 409 - and the deposits sat unsettled while the
    provider retried into a wall.
    """
    monkeypatch.setattr(settings, "payment_provider", "sandbox")
    monkeypatch.setattr(settings, "bank_transfer_webhook_secret", "rail-test-secret")
    client.post(ADMIN, json=make_pathway(name="Rail Bank"), headers=admin_headers)

    raw = json.dumps({"event_id": "multi-1", "type": "deposit.succeeded", "reference": "nope"}).encode()
    resp = client.post(
        "/api/payments/webhooks/bank_transfer",
        content=raw,
        headers={"Content-Type": "application/json", "X-Signature": sign(raw, "rail-test-secret")},
    )
    # Reaches the handler (422: the reference is unknown), rather than being
    # turned away at the door (409).
    assert resp.status_code == 422


def test_a_provider_with_no_pathway_is_still_refused(client, admin_headers, clear_pathways, monkeypatch):
    monkeypatch.setattr(settings, "payment_provider", "sandbox")
    resp = client.post(
        "/api/payments/webhooks/adyen",
        content=b'{"notificationItems":[]}',
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# simulation cannot be reached through a live rail
# ---------------------------------------------------------------------------
def test_a_rail_deposit_cannot_be_simulated(client, admin_headers, clear_pathways, monkeypatch):
    """The bug this test exists for.

    The simulate endpoint used to check the *deployment's* provider. Once a
    deployment settles through a real rail while its default is still sandbox,
    that check passes - handing every player a button that credits a real-rail
    deposit for free. It must be gated on the provider carrying that deposit.
    """
    monkeypatch.setattr(settings, "payment_provider", "sandbox")
    client.post(ADMIN, json=make_pathway(name="Rail Bank"), headers=admin_headers)
    tokens = register(client, "sim_rail@example.com", "simrail")
    headers = auth_headers(tokens["access_token"])

    created = client.post(
        "/api/wallet/deposits",
        json={"amount": "100.00", "method": "bank_transfer"},
        headers=headers,
    )
    assert created.status_code == 201, created.text

    resp = client.post(
        f"/api/wallet/deposits/{created.json()['deposit_id']}/simulate",
        json={"outcome": "succeed"},
        headers=headers,
    )
    assert resp.status_code == 403, (
        "a deposit routed to a live rail was credited by the sandbox simulator"
    )
    assert cash(client, tokens["access_token"]) == 0


def test_a_sandbox_deposit_can_still_be_simulated(client, admin_headers, clear_pathways):
    """The guard must not break the simulator it is protecting."""
    tokens = register(client, "sim_ok@example.com", "simok")
    headers = auth_headers(tokens["access_token"])

    created = client.post(
        "/api/wallet/deposits", json={"amount": "25.00", "method": "card"}, headers=headers
    )
    assert created.status_code == 201, created.text
    resp = client.post(
        f"/api/wallet/deposits/{created.json()['deposit_id']}/simulate",
        json={"outcome": "succeed"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert cash(client, tokens["access_token"]) == 2_500


# ---------------------------------------------------------------------------
# closing the loop on an out-of-band payout
# ---------------------------------------------------------------------------
def test_a_manually_paid_withdrawal_can_be_confirmed(client, admin_headers, clear_pathways):
    """A rail with no payout API still has to be able to finish a withdrawal.

    Without this, approving a payout on a manual rail left the money in
    `user_locked` permanently: only a terminal state releases the block, and
    nothing could reach one.
    """
    from app.ledger import admin_adjust
    from app.models import User

    pathway = client.post(
        ADMIN,
        json=make_pathway(name="Manual Payout", deposits_enabled=False, withdrawals_enabled=True),
        headers=admin_headers,
    ).json()

    email = f"manual_{uuid.uuid4().hex[:8]}@example.com"
    tokens = register(client, email, f"manual{uuid.uuid4().hex[:6]}")
    headers = auth_headers(tokens["access_token"])

    with session_scope() as db:
        user = db.query(User).filter(User.email == email).one()
        admin_adjust(db, user.id, 30_000, reference="float", memo="test float")
        db.commit()

    wd = client.post(
        "/api/wallet/withdrawals",
        json={
            "amount": "100.00", "method": "bank_transfer",
            "destination": "mBoB 17123456", "banking_method_id": pathway["id"],
        },
        headers=headers,
    ).json()

    approved = client.post(
        f"/api/admin/withdrawals/{wd['id']}/review",
        json={"approve": True, "note": "KYC checked"},
        headers=admin_headers,
    )
    assert approved.status_code == 200, approved.text
    # Approved, but not paid: the funds stay held until the transfer is made.
    assert approved.json()["status"] == "approved"
    assert approved.json()["paid_at"] is None

    confirmed = client.post(
        f"/api/admin/withdrawals/{wd['id']}/mark-paid",
        json={"reference": "MBOB-PAY-77120", "note": "sent from the bank console"},
        headers=admin_headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "paid"
    assert confirmed.json()["provider_ref"] == "MBOB-PAY-77120"

    # The hold is released - the money is genuinely gone, not still reserved.
    assert cash(client, tokens["access_token"]) == 20_000


def test_a_payout_cannot_be_confirmed_without_a_reference(client, admin_headers, clear_pathways):
    """"Marked paid" with no evidence is how a payout queue becomes unauditable."""
    from app.ledger import admin_adjust
    from app.models import User

    pathway = client.post(
        ADMIN,
        json=make_pathway(name="Manual Payout 2", deposits_enabled=False, withdrawals_enabled=True),
        headers=admin_headers,
    ).json()
    email = f"noref_{uuid.uuid4().hex[:8]}@example.com"
    tokens = register(client, email, f"noref{uuid.uuid4().hex[:6]}")
    headers = auth_headers(tokens["access_token"])

    with session_scope() as db:
        user = db.query(User).filter(User.email == email).one()
        admin_adjust(db, user.id, 30_000, reference="float", memo="test float")
        db.commit()

    wd = client.post(
        "/api/wallet/withdrawals",
        json={
            "amount": "100.00", "method": "bank_transfer",
            "destination": "mBoB 17123456", "banking_method_id": pathway["id"],
        },
        headers=headers,
    ).json()
    client.post(
        f"/api/admin/withdrawals/{wd['id']}/review",
        json={"approve": True},
        headers=admin_headers,
    )

    resp = client.post(
        f"/api/admin/withdrawals/{wd['id']}/mark-paid",
        json={},
        headers=admin_headers,
    )
    assert resp.status_code == 422


def test_confirming_a_payout_twice_does_not_double_settle(client, admin_headers, clear_pathways):
    from app.ledger import admin_adjust
    from app.models import User

    pathway = client.post(
        ADMIN,
        json=make_pathway(name="Manual Payout 3", deposits_enabled=False, withdrawals_enabled=True),
        headers=admin_headers,
    ).json()
    email = f"twice_{uuid.uuid4().hex[:8]}@example.com"
    tokens = register(client, email, f"twice{uuid.uuid4().hex[:6]}")
    headers = auth_headers(tokens["access_token"])

    with session_scope() as db:
        user = db.query(User).filter(User.email == email).one()
        admin_adjust(db, user.id, 30_000, reference="float", memo="test float")
        db.commit()

    wd = client.post(
        "/api/wallet/withdrawals",
        json={
            "amount": "100.00", "method": "bank_transfer",
            "destination": "mBoB 17123456", "banking_method_id": pathway["id"],
        },
        headers=headers,
    ).json()
    client.post(f"/api/admin/withdrawals/{wd['id']}/review", json={"approve": True}, headers=admin_headers)

    client.post(
        f"/api/admin/withdrawals/{wd['id']}/mark-paid",
        json={"reference": "MBOB-PAY-A"}, headers=admin_headers,
    )
    client.post(
        f"/api/admin/withdrawals/{wd['id']}/mark-paid",
        json={"reference": "MBOB-PAY-B"}, headers=admin_headers,
    )

    assert cash(client, tokens["access_token"]) == 20_000


# ---------------------------------------------------------------------------
# seeding
# ---------------------------------------------------------------------------
def test_a_local_rail_without_its_credential_is_seeded_switched_off(client, monkeypatch):
    """An enabled pathway with no key breaks the market it serves.

    Routing prefers the more specific match, so a Bhutanese rail that is on
    while `MBOB_API_KEY` is missing does not quietly fall back to the global
    one - it takes every Bhutanese deposit and fails them. Seeding it enabled
    would turn a working deployment into one with a broken country in it.
    """
    from app.services import routing

    monkeypatch.delenv("MBOB_API_KEY", raising=False)
    with session_scope() as db:
        db.query(BankingMethod).delete()
        db.commit()
    with session_scope() as db:
        routing.seed_starter_pathways(db)
        db.commit()

    with session_scope() as db:
        mbob = db.execute(
            select(BankingMethod).where(BankingMethod.name.like("mBoB%"))
        ).scalar_one()
        assert mbob.active is False, "a rail with no credential was enabled"
        assert "MBOB_API_KEY" in (mbob.notes or "")

        # The global fallback carries the market instead, so nobody is stuck.
        picked = routing.resolve(db, country="BT", currency="BTN", direction=routing.DEPOSIT)
        assert picked is not None
        assert picked.country_code == "*"


def test_a_local_rail_with_its_credential_is_seeded_ready(client, monkeypatch):
    from app.services import routing

    monkeypatch.setenv("MBOB_API_KEY", "a-real-key-from-the-environment")
    with session_scope() as db:
        db.query(BankingMethod).delete()
        db.commit()
    with session_scope() as db:
        routing.seed_starter_pathways(db)
        db.commit()

    with session_scope() as db:
        mbob = db.execute(
            select(BankingMethod).where(BankingMethod.name.like("mBoB%"))
        ).scalar_one()
        assert mbob.active is True
        assert routing.credential_present(mbob) is True
        picked = routing.resolve(db, country="BT", currency="BTN", direction=routing.DEPOSIT)
        assert picked.name.startswith("mBoB")


def test_seeding_never_overwrites_an_operators_decision(client, monkeypatch):
    """Switching a rail off must survive every future deploy."""
    from app.services import routing

    monkeypatch.setenv("MBOB_API_KEY", "a-real-key")
    with session_scope() as db:
        db.query(BankingMethod).delete()
        db.commit()
    with session_scope() as db:
        routing.seed_starter_pathways(db)
        db.commit()

    with session_scope() as db:
        mbob = db.execute(
            select(BankingMethod).where(BankingMethod.name.like("mBoB%"))
        ).scalar_one()
        mbob.active = False
        mbob.priority = 1
        db.commit()

    with session_scope() as db:
        added = routing.seed_starter_pathways(db)
        db.commit()
    assert added == [], "re-seeding re-created pathways that already existed"

    with session_scope() as db:
        again = db.execute(
            select(BankingMethod).where(BankingMethod.name.like("mBoB%"))
        ).scalar_one()
        assert again.active is False, "the seed turned a retired rail back on"
        assert again.priority == 1, "the seed overwrote the operator's priority"
