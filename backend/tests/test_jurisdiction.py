"""Jurisdiction policy: every mode, every tier, and the HTTP surface.

Policy tests are worth writing precisely because the policy is configurable:
the failure mode of a configurable gate is not "wrong answer", it is "the gate
silently stopped being consulted". These tests call the engine directly and
then drive the real registration and deposit endpoints, so a router that stops
asking the policy shows up as a red test rather than as an open door.
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.services import jurisdiction
from tests.conftest import auth_headers, deposit, register


@pytest.fixture
def policy():
    """Set a jurisdiction policy for one test and restore the shipped default.

    The settings object is a process-wide singleton, so a test that mutates it
    without restoring would silently reconfigure every test that runs after it.
    """
    original = (
        settings.jurisdiction_mode,
        settings.jurisdiction_blocklist,
        settings.jurisdiction_allowlist,
        settings.restricted_regions,
        settings.geo_enforcement,
    )
    yield settings
    (
        settings.jurisdiction_mode,
        settings.jurisdiction_blocklist,
        settings.jurisdiction_allowlist,
        settings.restricted_regions,
        settings.geo_enforcement,
    ) = original


# ---------------------------------------------------------------------------
# the engine
# ---------------------------------------------------------------------------
def test_default_policy_is_open_to_all_countries():
    assert jurisdiction.mode() == "allow_all"
    for country in ("US", "GB", "FR", "NL", "AU", "BT", "IR", "KP"):
        for action in jurisdiction.ACTIONS:
            decision = jurisdiction.evaluate(country, action)
            assert decision.allowed, f"{country} {action} was refused under allow_all"
            assert decision.tier == jurisdiction.OPEN


def test_blocklist_mode_refuses_only_listed_countries(policy):
    policy.jurisdiction_mode = "blocklist"
    policy.jurisdiction_blocklist = "US, GB ,fr"

    assert jurisdiction.evaluate("US", jurisdiction.REGISTER).allowed is False
    assert jurisdiction.evaluate("gb", jurisdiction.DEPOSIT).allowed is False
    assert jurisdiction.evaluate("FR", jurisdiction.PLAY).allowed is False
    # ...and nobody else is affected
    assert jurisdiction.evaluate("DE", jurisdiction.REGISTER).allowed is True
    assert jurisdiction.evaluate("BT", jurisdiction.DEPOSIT).allowed is True


def test_allowlist_mode_refuses_everything_unlisted(policy):
    policy.jurisdiction_mode = "allowlist"
    policy.jurisdiction_allowlist = "BT,IN"

    assert jurisdiction.evaluate("BT", jurisdiction.REGISTER).allowed is True
    assert jurisdiction.evaluate("IN", jurisdiction.WITHDRAW).allowed is True
    for country in ("US", "DE", "NG", "ZZ"):
        decision = jurisdiction.evaluate(country, jurisdiction.REGISTER)
        assert decision.allowed is False
        assert decision.tier == jurisdiction.NOT_ALLOWED


def test_allowlist_refuses_an_unknown_country(policy):
    """An empty/malformed country must not be treated as "allowed".

    In allowlist mode a missing country is the one value an attacker controls,
    so it has to fail closed.
    """
    policy.jurisdiction_mode = "allowlist"
    policy.jurisdiction_allowlist = "BT"

    for unknown in (None, "", "  ", "XYZ", "1", "B"):
        decision = jurisdiction.evaluate(unknown, jurisdiction.REGISTER)
        assert decision.allowed is False, f"{unknown!r} slipped through"


def test_restricted_tier_blocks_payments_but_not_play(policy):
    """The middle tier: accounts and gameplay, no money rails."""
    policy.restricted_regions = "NG"

    for action in (jurisdiction.REGISTER, jurisdiction.PLAY):
        assert jurisdiction.evaluate("NG", action).allowed is True

    for action in (jurisdiction.DEPOSIT, jurisdiction.WITHDRAW):
        decision = jurisdiction.evaluate("NG", action)
        assert decision.allowed is False
        assert decision.tier == jurisdiction.RESTRICTED
        # The message has to be usable by support, not a stack trace.
        assert "NG" in decision.reason
        assert "account" in decision.reason.lower() or "support" in decision.reason.lower()


def test_blocklist_and_restricted_combine(policy):
    """A country can be blocked entirely and another merely restricted."""
    policy.jurisdiction_mode = "blocklist"
    policy.jurisdiction_blocklist = "US"
    policy.restricted_regions = "NG"

    assert jurisdiction.evaluate("US", jurisdiction.PLAY).allowed is False
    assert jurisdiction.evaluate("NG", jurisdiction.PLAY).allowed is True
    assert jurisdiction.evaluate("NG", jurisdiction.DEPOSIT).allowed is False


def test_unknown_mode_falls_back_to_open(policy, caplog):
    """A typo in an env var must not lock every player out of the platform."""
    policy.jurisdiction_mode = "blocklistt"
    assert jurisdiction.mode() == "allow_all"
    assert jurisdiction.evaluate("US", jurisdiction.REGISTER).allowed is True


def test_snapshot_reports_the_effective_policy(policy):
    policy.jurisdiction_mode = "allowlist"
    policy.jurisdiction_allowlist = "BT, IN"
    policy.restricted_regions = "NG"

    snap = jurisdiction.snapshot()
    assert snap["mode"] == "allowlist"
    assert snap["allowlist"] == ["BT", "IN"]
    assert snap["restricted"] == ["NG"]
    assert snap["open_to_every_country"] is False
    assert snap["warning"] is None


def test_snapshot_warns_when_everything_is_open(policy):
    policy.jurisdiction_mode = "allow_all"
    warning = jurisdiction.snapshot()["warning"]
    assert warning and "all countries accepted" in warning.lower()


# ---------------------------------------------------------------------------
# the HTTP surface: the gates must actually call the policy
# ---------------------------------------------------------------------------
def test_registration_respects_the_policy(client, policy):
    policy.jurisdiction_mode = "blocklist"
    policy.jurisdiction_blocklist = "GB"

    refused = client.post(
        "/api/auth/register",
        json={
            "email": "gb@example.com", "username": "gbplayer",
            "password": "Passw0rd!23", "date_of_birth": "1990-01-01",
            "country": "GB", "accepts_terms": True,
        },
    )
    assert refused.status_code == 403
    assert "GB" in refused.json()["detail"]

    allowed = client.post(
        "/api/auth/register",
        json={
            "email": "de@example.com", "username": "deplayer",
            "password": "Passw0rd!23", "date_of_birth": "1990-01-01",
            "country": "DE", "accepts_terms": True,
        },
    )
    assert allowed.status_code == 201, allowed.text


def test_deposit_respects_the_restricted_tier(client, policy):
    """A restricted player can hold and play an existing balance, but cannot
    top up - which is the whole point of the tier."""
    policy.restricted_regions = "NG"
    tokens = register(client, "ng@example.com", "ngplayer", country="NG")
    headers = auth_headers(tokens["access_token"])

    resp = client.post(
        "/api/wallet/deposits",
        # amounts are decimal strings on the wire: the API never accepts a
        # float for money (0.1 + 0.2 is not 0.3 in binary floating point).
        json={"amount": "50.00", "method": "card"},
        headers=headers,
    )
    assert resp.status_code == 403, resp.text
    assert "NG" in resp.json()["detail"]

    # gameplay is untouched
    empty = client.get("/api/wallet/summary", headers=headers)
    assert empty.status_code == 200


def test_withdrawal_respects_the_policy(client, policy):
    """A policy change must gate money leaving the platform too."""
    # Deliberately not "wd@example.com": test_ledger.py builds that user
    # directly and the whole suite shares one session-scoped database, so a
    # shared email is a UNIQUE violation waiting for the right test order.
    tokens = register(client, "wd-policy@example.com", "wdpolicyplayer")
    headers = auth_headers(tokens["access_token"])
    deposit(client, tokens["access_token"], "100.00")
    assert client.post(
        "/api/wallet/withdrawals",
        json={"amount": "50.00", "method": "crypto_usdt", "destination": "TJmvQ1xPolicyTestPayoutAddr"},
        headers=headers,
    ).status_code == 201

    policy.restricted_regions = "BT"      # the test players register from BT
    resp = client.post(
        "/api/wallet/withdrawals",
        json={"amount": "10.00", "method": "crypto_usdt", "destination": "TJmvQ1xPolicyTestPayoutAddr"},
        headers=headers,
    )
    assert resp.status_code == 403, resp.text
    assert "Withdrawals" in resp.json()["detail"]


def test_public_config_publishes_the_policy(client):
    """A refused player must be able to see the rule that refused them."""
    body = client.get("/api/config").json()
    assert body["jurisdiction"]["mode"] == settings.jurisdiction_mode
    assert "blocklist" in body["jurisdiction"]
    assert body["jurisdiction"]["warning"]


def test_edge_country_header_is_read_when_enforcement_is_on(client, policy):
    """With GEO_ENFORCEMENT the edge's country decides, not the form field."""
    policy.jurisdiction_mode = "blocklist"
    policy.jurisdiction_blocklist = "US"
    policy.geo_enforcement = True

    # Claims Bhutan, but the edge says the request came from the US.
    resp = client.post(
        "/api/auth/register",
        json={
            "email": "vpn@example.com", "username": "vpnplayer",
            "password": "Passw0rd!23", "date_of_birth": "1990-01-01",
            "country": "BT", "accepts_terms": True,
        },
        headers={"x-vercel-ip-country": "US"},
    )
    assert resp.status_code == 403, resp.text


def test_edge_header_is_ignored_when_enforcement_is_off(client, policy):
    """Off by default: a header must not silently outrank the account."""
    policy.jurisdiction_mode = "blocklist"
    policy.jurisdiction_blocklist = "US"
    policy.geo_enforcement = False

    resp = client.post(
        "/api/auth/register",
        json={
            "email": "noedge@example.com", "username": "noedgeplayer",
            "password": "Passw0rd!23", "date_of_birth": "1990-01-01",
            "country": "BT", "accepts_terms": True,
        },
        headers={"x-vercel-ip-country": "US"},
    )
    assert resp.status_code == 201, resp.text


def test_region_endpoint_reports_the_edge_and_a_suggestion(client):
    body = client.get("/api/region", headers={"x-vercel-ip-country": "DE"}).json()
    assert body["country"] == "DE"
    assert body["source"] == "vercel"
    assert body["suggested_currency"] == "EUR"
    assert body["settlement_currency"] == "USD"


def test_region_endpoint_is_honest_when_the_edge_says_nothing(client):
    body = client.get("/api/region").json()
    assert body["country"] is None
    assert body["source"] is None
    # Never guess a country; fall back to the configured defaults.
    assert body["suggested_currency"]
