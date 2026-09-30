"""The money rails: provider callbacks, signature verification, idempotency.

The bug this file exists to prevent
-----------------------------------
``services/payments.py`` had a complete, correct ``ingest_webhook`` and no HTTP
route calling it. Every deposit taken through a live processor would have been
redirected to the provider, paid by the player, and then never credited, because
nothing was listening. The deposit flow *looks* healthy without a webhook: the
player returns to a success page and the balance does not move. That is a
support ticket per deposit, and it is invisible to any test that only exercises
the application's own endpoints.

So these tests post real bytes to the real route, signed the way the provider
signs them.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest

from app.config import settings
from app.payments import get_provider
from app.payments.adyen import AdyenProvider
from app.payments.base import PaymentProvider
from app.services import payments as pay_svc
from tests.conftest import auth_headers, cash, deposit, register


@pytest.fixture
def provider(monkeypatch):
    """Pin the active provider for one test and restore it afterwards."""
    original = settings.payment_provider
    yield settings
    settings.payment_provider = original
    get_provider.cache_clear()


def _sandbox_signature(body: bytes) -> str:
    return hmac.new(settings.secret_key.encode(), body, hashlib.sha256).hexdigest()


def _sandbox_event(deposit_id: str, kind: str = "payment.succeeded",
                   event_id: str | None = None) -> bytes:
    payload = {"type": kind, "deposit_id": deposit_id}
    if event_id:
        payload["id"] = event_id
    return json.dumps(payload).encode()


# ---------------------------------------------------------------------------
# the route exists, and is defended
# ---------------------------------------------------------------------------
def test_the_webhook_route_is_reachable(client):
    """A PSP must be able to POST to a documented URL.

    Both paths are published: the canonical one and the one DEPLOY-CHECKLIST
    tells operators to register with their provider. If either 404s, money is
    silently lost, so both are asserted here.
    """
    for path in ("/api/payments/webhooks/sandbox", "/api/wallet/webhooks/sandbox"):
        resp = client.post(path, content=b"{}", headers={"x-signature": "nope"})
        assert resp.status_code != 404, f"{path} does not exist"


def test_a_webhook_with_no_signature_is_rejected(client):
    resp = client.post(
        "/api/payments/webhooks/sandbox",
        content=_sandbox_event("dep_whatever"),
    )
    assert resp.status_code == 400
    assert "signature" in resp.json()["detail"].lower()


def test_a_webhook_with_a_wrong_signature_is_rejected(client):
    resp = client.post(
        "/api/payments/webhooks/sandbox",
        content=_sandbox_event("dep_whatever"),
        headers={"x-signature": "0" * 64},
    )
    assert resp.status_code == 400


def test_an_unsigned_webhook_is_recorded_for_ops(client):
    """A rejected callback must leave a trace: silence is how a signature
    misconfiguration goes unnoticed for a week."""
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import PaymentWebhook

    client.post(
        "/api/payments/webhooks/sandbox",
        content=_sandbox_event("dep_x"),
        headers={"x-signature": "bad"},
    )
    with session_scope() as db:
        rows = db.execute(select(PaymentWebhook)).scalars().all()
        assert rows, "the rejected webhook was not stored"
        assert rows[-1].signature_valid is False
        assert rows[-1].error == "invalid signature"


def test_callbacks_for_an_inactive_provider_are_refused(client, provider):
    """A valid signature for a rail this deployment is not settling is still
    not something to act on."""
    body = _sandbox_event("dep_whatever")
    provider.payment_provider = "stripe"      # active provider is now stripe
    get_provider.cache_clear()

    resp = client.post(
        "/api/payments/webhooks/sandbox",
        content=body,
        headers={"x-signature": _sandbox_signature(body)},
    )
    assert resp.status_code == 409
    assert "sandbox" in resp.json()["detail"]


def test_an_unknown_provider_is_a_404(client):
    resp = client.post("/api/payments/webhooks/paypal-ish", content=b"{}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# a signed callback settles the deposit - end to end
# ---------------------------------------------------------------------------
def test_a_signed_webhook_credits_the_deposit(client, provider):
    tokens = register(client, "hook@example.com", "hookplayer")
    headers = auth_headers(tokens["access_token"])

    created = client.post(
        "/api/wallet/deposits",
        json={"amount": "75.00", "method": "card"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    deposit_id = created.json()["deposit_id"]
    assert cash(client, tokens["access_token"]) == 0, "credited before payment?"

    body = _sandbox_event(deposit_id)
    resp = client.post(
        "/api/payments/webhooks/sandbox",
        content=body,
        headers={"x-signature": _sandbox_signature(body)},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "processed"
    # The player's money is only real after this point.
    assert cash(client, tokens["access_token"]) == 7500


def test_a_replayed_webhook_does_not_credit_twice(client):
    """PSPs retry. A retry must be a no-op, not a second deposit.

    This is the single most expensive bug in payment integration: a non-
    idempotent handler turns one payment into many, and the loss is not
    discovered until reconciliation.
    """
    tokens = register(client, "replay@example.com", "replayplayer")
    headers = auth_headers(tokens["access_token"])
    created = client.post(
        "/api/wallet/deposits",
        json={"amount": "40.00", "method": "card"},
        headers=headers,
    ).json()
    body = _sandbox_event(created["deposit_id"], event_id="evt_replay_1")
    signature = _sandbox_signature(body)

    first = client.post(
        "/api/payments/webhooks/sandbox", content=body, headers={"x-signature": signature}
    )
    second = client.post(
        "/api/payments/webhooks/sandbox", content=body, headers={"x-signature": signature}
    )
    third = client.post(
        "/api/payments/webhooks/sandbox", content=body, headers={"x-signature": signature}
    )

    assert first.json()["status"] == "processed"
    assert second.json()["status"] == "duplicate"
    assert third.json()["status"] == "duplicate"
    assert cash(client, tokens["access_token"]) == 4000, "credited more than once"


def test_a_failed_payment_webhook_marks_the_deposit_failed(client):
    tokens = register(client, "failed@example.com", "failedplayer")
    headers = auth_headers(tokens["access_token"])
    created = client.post(
        "/api/wallet/deposits",
        json={"amount": "20.00", "method": "card"},
        headers=headers,
    ).json()

    body = _sandbox_event(created["deposit_id"], "payment.failed")
    resp = client.post(
        "/api/payments/webhooks/sandbox",
        content=body,
        headers={"x-signature": _sandbox_signature(body)},
    )
    assert resp.status_code == 200

    listed = client.get("/api/wallet/deposits", headers=headers).json()
    row = next(d for d in listed["deposits"] if d["id"] == created["deposit_id"])
    assert row["status"] == "failed"
    assert cash(client, tokens["access_token"]) == 0


def test_a_chargeback_is_recorded_against_the_deposit(client):
    tokens = register(client, "cb@example.com", "cbplayer")
    headers = auth_headers(tokens["access_token"])
    deposit(client, tokens["access_token"], "30.00")
    listed = client.get("/api/wallet/deposits", headers=headers).json()
    deposit_id = listed["deposits"][0]["id"]

    body = _sandbox_event(deposit_id, "payment.chargeback")
    resp = client.post(
        "/api/payments/webhooks/sandbox",
        content=body,
        headers={"x-signature": _sandbox_signature(body)},
    )
    assert resp.status_code == 200

    listed = client.get("/api/wallet/deposits", headers=headers).json()
    row = next(d for d in listed["deposits"] if d["id"] == deposit_id)
    assert row["status"] == "chargeback"


def test_a_provider_that_sends_no_event_id_is_still_deduplicated(client):
    """PSPs retry, and not every payload carries an id.

    Without a fingerprint of the raw bytes, "the same callback again" is
    indistinguishable from "a second payment with identical contents", and the
    retry path becomes a duplicate credit.
    """
    tokens = register(client, "noid@example.com", "noidplayer")
    headers = auth_headers(tokens["access_token"])
    created = client.post(
        "/api/wallet/deposits",
        json={"amount": "60.00", "method": "card"},
        headers=headers,
    ).json()
    body = _sandbox_event(created["deposit_id"])      # no "id" field at all
    signature = _sandbox_signature(body)

    first = client.post(
        "/api/payments/webhooks/sandbox", content=body, headers={"x-signature": signature}
    )
    second = client.post(
        "/api/payments/webhooks/sandbox", content=body, headers={"x-signature": signature}
    )
    assert first.json()["status"] == "processed"
    assert second.json()["status"] == "duplicate"
    assert cash(client, tokens["access_token"]) == 6000


def test_a_webhook_for_an_unknown_deposit_is_rejected_not_ignored(client):
    """Silently ignoring an unknown reference hides a mismatch between what we
    think we sent the provider and what it thinks it is collecting."""
    body = _sandbox_event("dep_does_not_exist")
    resp = client.post(
        "/api/payments/webhooks/sandbox",
        content=body,
        headers={"x-signature": _sandbox_signature(body)},
    )
    assert resp.status_code == 422
    assert "unknown deposit" in resp.json()["detail"]


def test_a_processor_cannot_credit_an_amount_we_did_not_ask_for(client, db, provider):
    """The provider states how much it collected; we check it against the row.

    A compromised or misconfigured integration that reports a larger amount
    must not create money. This is asserted on the Stripe handler because that
    is where the comparison lives, but the rule is the same in every adapter
    that receives an amount from outside.
    """
    from app.payments.base import PaymentError
    from app.payments.stripe_provider import StripeProvider

    tokens = register(client, "mismatch@example.com", "mismatchplayer")
    headers = auth_headers(tokens["access_token"])
    deposit_id = client.post(
        "/api/wallet/deposits",
        json={"amount": "25.00", "method": "card"},
        headers=headers,
    ).json()["deposit_id"]

    event = {
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_test", "amount_total": 100_000,
                            "metadata": {"deposit_id": deposit_id}}},
    }
    with pytest.raises(PaymentError) as exc:
        StripeProvider().handle_webhook(db, event)
    assert "amount mismatch" in str(exc.value)
    assert cash(client, tokens["access_token"]) == 0, "money was created from a mismatch"


def test_webhook_locations_helper_tells_the_operator_where_to_register(client):
    body = client.get("/api/payments/providers").json()
    assert body["active_provider"] == settings.payment_provider
    assert body["url"].endswith(f"/{settings.payment_provider}")
    assert any(p["configured"] for p in body["providers"])


# ---------------------------------------------------------------------------
# the provider interface itself
# ---------------------------------------------------------------------------
def test_every_provider_implements_the_contract():
    """A provider that cannot verify a webhook must not be trusted with money.

    The base class refuses everything by default; what this asserts is that the
    shipped providers override it rather than inheriting the refusal by
    accident.
    """
    from app.payments.cryptopay import CryptoPayProvider
    from app.payments.sandbox import SandboxProvider
    from app.payments.stripe_provider import StripeProvider

    for cls in (SandboxProvider, StripeProvider, CryptoPayProvider, AdyenProvider):
        assert issubclass(cls, PaymentProvider)
        assert cls.name and cls.name != "base"
        assert cls.verify_webhook is not PaymentProvider.verify_webhook, (
            f"{cls.name} inherits the base refusal-to-verify default"
        )


def test_a_provider_with_no_secret_refuses_every_webhook(monkeypatch):
    """Configured wrongly, the safe failure is refusing the callback."""
    from app.payments.stripe_provider import StripeProvider

    monkeypatch.setattr(settings, "stripe_webhook_secret", "")
    provider = StripeProvider()
    ok, event = provider.verify_webhook(b'{"id":"evt_1"}', "t=1,v1=deadbeef")
    assert ok is False and event == {}


# ---------------------------------------------------------------------------
# Adyen's signature scheme
# ---------------------------------------------------------------------------
def _adyen_item(**overrides) -> dict:
    item = {
        "pspReference": "8535876789001234",
        "originalReference": "",
        "merchantAccountCode": "TestMerchant",
        "merchantReference": "dep_123",
        "amount": {"value": 5000, "currency": "USD"},
        "eventCode": "AUTHORISATION",
        "success": "true",
    }
    item.update(overrides)
    return item


def _sign(item: dict, key: str) -> str:
    """Sign the way Adyen documents it, from the docs, independently of the
    implementation - otherwise the test just repeats the bug."""
    amount = item.get("amount") or {}
    signing_string = "".join(
        str(x) for x in (
            item.get("pspReference", ""), item.get("originalReference", ""),
            item.get("merchantAccountCode", ""), item.get("merchantReference", ""),
            amount.get("value", ""), amount.get("currency", ""),
            item.get("eventCode", ""), item.get("success", ""),
        )
    )
    digest = hmac.new(key.encode(), signing_string.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def test_adyen_accepts_a_correctly_signed_notification(monkeypatch):
    monkeypatch.setattr(settings, "adyen_hmac_key", "44782DEF547AAA06C910C43932B1EB0C71FC68D9D0C057550C48EC2ACF6BA056")
    provider = AdyenProvider()
    item = _adyen_item()
    item["additionalData"] = {"hmacSignature": _sign(item, settings.adyen_hmac_key)}
    body = json.dumps({"notificationItems": [{"NotificationRequestItem": item}]}).encode()

    ok, event = provider.verify_webhook(body, None)
    assert ok is True, "a correctly signed Adyen notification was refused"
    assert event["items"][0]["merchantReference"] == "dep_123"


def test_adyen_rejects_a_tampered_amount(monkeypatch):
    """The attack to defend against: sign a $1 payment, then edit the amount."""
    monkeypatch.setattr(settings, "adyen_hmac_key", "44782DEF547AAA06C910C43932B1EB0C71FC68D9D0C057550C48EC2ACF6BA056")
    provider = AdyenProvider()
    item = _adyen_item()
    item["additionalData"] = {"hmacSignature": _sign(item, settings.adyen_hmac_key)}
    item["amount"]["value"] = 500000          # tampered after signing
    body = json.dumps({"notificationItems": [{"NotificationRequestItem": item}]}).encode()

    ok, _ = provider.verify_webhook(body, None)
    assert ok is False


def test_adyen_rejects_a_batch_with_one_unsigned_item(monkeypatch):
    """One verified item must not smuggle in an unverified sibling."""
    monkeypatch.setattr(settings, "adyen_hmac_key", "44782DEF547AAA06C910C43932B1EB0C71FC68D9D0C057550C48EC2ACF6BA056")
    provider = AdyenProvider()
    good = _adyen_item()
    good["additionalData"] = {"hmacSignature": _sign(good, settings.adyen_hmac_key)}
    bad = _adyen_item(merchantReference="dep_999", pspReference="OTHER",
                      amount={"value": 999999, "currency": "USD"})
    body = json.dumps(
        {"notificationItems": [
            {"NotificationRequestItem": good},
            {"NotificationRequestItem": bad},
        ]}
    ).encode()

    ok, _ = provider.verify_webhook(body, None)
    assert ok is False


def test_adyen_without_an_hmac_key_refuses_everything(monkeypatch):
    monkeypatch.setattr(settings, "adyen_hmac_key", "")
    provider = AdyenProvider()
    item = _adyen_item()
    item["additionalData"] = {"hmacSignature": "anything"}
    body = json.dumps({"notificationItems": [{"NotificationRequestItem": item}]}).encode()
    ok, _ = provider.verify_webhook(body, None)
    assert ok is False


def test_adyen_authorisation_settles_our_deposit(client, provider, monkeypatch):
    """The whole loop: Adyen notifies, our deposit is credited.

    The provider is switched to Adyen with a known HMAC key, and the
    notification is signed exactly as Adyen signs it.
    """
    key = "44782DEF547AAA06C910C43932B1EB0C71FC68D9D0C057550C48EC2ACF6BA056"
    monkeypatch.setattr(settings, "adyen_hmac_key", key)
    monkeypatch.setattr(settings, "adyen_api_key", "test_key")
    monkeypatch.setattr(settings, "adyen_merchant_account", "TestMerchant")

    tokens = register(client, "adyen@example.com", "adyenplayer")
    headers = auth_headers(tokens["access_token"])
    # The deposit is created while the sandbox is active: creating it through
    # Adyen would need a network call to their API, and the point of this test
    # is the *callback* that settles it.
    created = client.post(
        "/api/wallet/deposits",
        json={"amount": "120.00", "method": "card"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    deposit_id = created.json()["deposit_id"]

    provider.payment_provider = "adyen"
    get_provider.cache_clear()

    item = _adyen_item(merchantReference=deposit_id, amount={"value": 12000, "currency": "USD"})
    item["additionalData"] = {
        "hmacSignature": _sign(item, key),
        "metadata.deposit_id": deposit_id,
    }
    body = json.dumps({"notificationItems": [{"NotificationRequestItem": item}]}).encode()

    resp = client.post("/api/payments/webhooks/adyen", content=body)
    assert resp.status_code == 200, resp.text
    assert cash(client, tokens["access_token"]) == 12000, "Adyen deposit was not credited"


def test_adyen_payout_without_an_instrument_fails_loudly(monkeypatch):
    """A payout with no verified destination must fail with something an
    operator can act on - not silently pay to nowhere."""
    from sqlalchemy.orm import Session

    from app.models import PaymentMethod, Withdrawal

    monkeypatch.setattr(settings, "adyen_balance_account_id", "BA_test")
    provider = AdyenProvider()
    withdrawal = Withdrawal(
        id="wd_test", user_id="u1", amount=5000, net_amount=5000,
        method=PaymentMethod.card, destination="card:****1111",
    )
    with pytest.raises(Exception) as exc:
        provider.create_payout(Session(), withdrawal)
    assert "instrument" in str(exc.value).lower()


def test_adyen_health_is_honest_without_credentials(monkeypatch):
    monkeypatch.setattr(settings, "adyen_api_key", "")
    monkeypatch.setattr(settings, "adyen_merchant_account", "")
    health = AdyenProvider().health()
    assert health["ok"] is False
    assert "not configured" in health["detail"]


# ---------------------------------------------------------------------------
# the payout side of the rails
# ---------------------------------------------------------------------------
def test_an_approved_withdrawal_records_a_provider_reference(client, admin_headers):
    """Every payout must carry the provider's reference, or reconciliation is
    impossible when a player says the money never arrived."""
    tokens = register(client, "payout@example.com", "payoutplayer")
    headers = auth_headers(tokens["access_token"])
    deposit(client, tokens["access_token"], "200.00")

    created = client.post(
        "/api/wallet/withdrawals",
        json={"amount": "100.00", "method": "crypto_usdt",
              "destination": "TJmvQ1xPayoutRefTestAddress"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    wid = created.json()["id"]

    reviewed = client.post(
        f"/api/admin/withdrawals/{wid}/review",
        json={"approve": True, "note": "verified"},
        headers=admin_headers,
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["status"] in ("approved", "paid")
    assert reviewed.json().get("provider_ref"), "payout has no provider reference"

    # The books must still balance after the payout.
    integrity = client.get("/api/wallet/integrity", headers=headers).json()
    assert integrity["books_balanced"] is True, integrity
