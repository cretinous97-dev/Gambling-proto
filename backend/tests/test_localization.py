"""Localization: languages, display currencies, and the display/settlement line.

The invariant this file protects
--------------------------------
Display conversion is a rendering step. The moment a converted amount reaches
the ledger, a limit check or a payout, the books are being kept in a currency
that changes when a rate feed updates - and there is no audit trail that can
explain a balance afterwards.

So the tests below do two things: check the conversion maths (including the
zero-decimal currencies, where getting the precision wrong is a 100x error),
and check that nothing in the money path moves when a player switches the
currency they *see*.
"""
from __future__ import annotations

import pytest

from app import i18n
from app.config import settings
from tests.conftest import auth_headers, cash, deposit, register


@pytest.fixture
def rates():
    """Swap the rate table for one test and restore it afterwards."""
    original = (settings.fx_rates, settings.fx_rates_updated_at)
    yield settings
    settings.fx_rates, settings.fx_rates_updated_at = original


# ---------------------------------------------------------------------------
# the conversion maths
# ---------------------------------------------------------------------------
def test_conversion_keeps_the_settlement_amount_attached():
    """Every converted amount carries the real amount with it."""
    converted = i18n.to_display(2000, "EUR").as_dict()
    assert converted["settlement"] == {"minor": 2000, "currency": "USD"}
    assert converted["display"]["currency"] == "EUR"
    assert converted["display_only"] is True


def test_two_decimal_currencies_round_to_cents():
    d = i18n.to_display(1000, "EUR")          # $10.00 at 0.92
    assert d.display_minor == 920              # €9.20


def test_zero_decimal_currencies_do_not_gain_a_fraction():
    """JPY has no minor unit. Showing ¥1573.00 implies a precision the
    currency does not have, and *storing* it is a 100x error."""
    d = i18n.to_display(1000, "JPY")          # $10.00 at 157
    assert d.display_minor == 1570
    assert i18n.currency_meta("JPY")["minor"] == 0


def test_crypto_display_keeps_its_precision():
    """Eight decimals, not two: rounding BTC to cents would show every small
    balance as 0.00 and, if that figure were ever stored, destroy it."""
    meta = i18n.currency_meta("BTC")
    assert meta["minor"] == 8

    d = i18n.to_display(10_000, "BTC")        # $100.00 at 0.0000150 BTC/USD
    assert d.display_minor == 150_000         # 0.00150000 BTC, in satoshis
    # ...and the two-decimal form of the same amount would have been zero:
    assert round(100.0 * 0.0000150, 2) == 0.0


def test_the_settlement_currency_is_always_one(rates):
    rates.fx_rates = '{"USD": 5.0, "EUR": 0.9}'
    assert i18n.rates()["USD"] == 1.0, "the settlement currency was re-rated"
    assert i18n.rates()["EUR"] == 0.9


def test_an_unknown_currency_falls_back_to_settlement_rather_than_guessing():
    d = i18n.to_display(5000, "XYZ")
    assert d.display_currency == "USD"
    assert d.display_minor == 5000


def test_a_broken_rate_table_is_reported_not_swallowed(rates):
    """A bad FX_RATES must degrade to defaults and say so - silently using a
    wrong rate is how a player sees a balance they cannot explain."""
    rates.fx_rates = '{"EUR": "not-a-number", "GBP": -1, "JPY": 150}'
    problems = i18n.rates_problems()
    assert any("EUR" in p for p in problems)
    assert any("GBP" in p for p in problems)
    assert i18n.rates()["JPY"] == 150
    assert i18n.rates()["EUR"] > 0, "a bad entry must not remove the fallback"


def test_invalid_json_degrades_to_defaults(rates):
    rates.fx_rates = "{not json"
    assert i18n.rates_problems()
    assert i18n.rates()["EUR"] > 0


# ---------------------------------------------------------------------------
# picking a language and a currency
# ---------------------------------------------------------------------------
def test_accept_language_wins_over_the_country():
    assert i18n.locale_for("DE", "pt-BR,pt;q=0.9,en;q=0.8") == "pt"


def test_the_country_decides_when_no_header_is_sent():
    assert i18n.locale_for("BR") == "pt"
    assert i18n.locale_for("CN") == "zh"


def test_an_unsupported_language_falls_back_to_the_default():
    assert i18n.locale_for("BT", "xx-YY") == settings.default_locale


def test_currency_follows_the_country():
    assert i18n.currency_for("IN") == "INR"
    assert i18n.currency_for("DE") == "EUR"
    assert i18n.currency_for("US") == "USD"


def test_an_unmapped_country_gets_the_settlement_currency():
    assert i18n.currency_for("ZZ") == settings.settlement_currency


def test_every_offered_locale_has_a_translation_bundle():
    """A locale listed in the API but missing a bundle renders an empty page.

    The API is the source of truth for what the frontend offers, so the two
    lists have to agree; this checks the file exists for each one.
    """
    from pathlib import Path

    bundles = Path(__file__).resolve().parent.parent.parent / "frontend" / "src" / "i18n" / "locales"
    missing = [code for code in settings.locales if not (bundles / f"{code}.json").is_file()]
    assert not missing, f"no translation bundle for: {missing}"


def test_right_to_left_metadata_is_declared():
    assert i18n.LOCALES["ar"]["dir"] == "rtl"
    assert i18n.LOCALES["en"]["dir"] == "ltr"


# ---------------------------------------------------------------------------
# the API surface
# ---------------------------------------------------------------------------
def test_config_publishes_localization_without_leaking_anything(client):
    body = client.get("/api/config").json()
    fx = body["i18n"]["fx"]
    assert fx["base"] == settings.settlement_currency
    assert fx["display_only"] is True
    assert "Presentation rates only" in fx["note"]
    assert body["i18n"]["settlement_currency"] == settings.settlement_currency
    assert any(loc["code"] == "en" for loc in body["i18n"]["locales"])
    assert any(cur["code"] == "USD" for cur in body["i18n"]["currencies"])


def test_i18n_endpoint_stands_alone(client):
    body = client.get("/api/i18n").json()
    assert body["locales"] and body["currencies"] and body["fx"]["rates"]


def test_a_player_can_choose_a_display_currency(client):
    tokens = register(client, "cur@example.com", "curplayer")
    headers = auth_headers(tokens["access_token"])

    ok = client.patch(
        "/api/auth/me", json={"display_currency": "eur"}, headers=headers
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["display_currency"] == "EUR"


def test_an_unsupported_display_currency_is_refused(client):
    tokens = register(client, "cur2@example.com", "curplayer2")
    headers = auth_headers(tokens["access_token"])
    resp = client.patch(
        "/api/auth/me", json={"display_currency": "ZZZ"}, headers=headers
    )
    assert resp.status_code == 400
    assert "Unsupported display currency" in resp.json()["detail"]


def test_changing_the_display_currency_never_touches_the_money(client):
    """The whole point of the display/settlement split.

    A player switches from USD to EUR and back; their balance, the ledger sum
    and the integrity check are identical throughout, because none of them know
    or care what the player is looking at.
    """
    tokens = register(client, "split@example.com", "splitplayer")
    headers = auth_headers(tokens["access_token"])
    deposit(client, tokens["access_token"], "123.45")

    before = cash(client, tokens["access_token"])
    integrity_before = client.get("/api/wallet/integrity", headers=headers).json()

    for currency in ("EUR", "JPY", "INR", "BTC", "USD"):
        resp = client.patch(
            "/api/auth/me", json={"display_currency": currency}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        assert cash(client, tokens["access_token"]) == before
        summary = client.get("/api/wallet/summary", headers=headers).json()
        assert summary["balances"]["currency"] == settings.settlement_currency, (
            "the wallet started reporting a non-settlement currency"
        )

    integrity_after = client.get("/api/wallet/integrity", headers=headers).json()
    assert integrity_after["books_balanced"] is True
    assert integrity_after["your_accounts"] == integrity_before["your_accounts"]


def test_a_restart_with_different_rates_does_not_change_a_balance(client, rates):
    tokens = register(client, "rates@example.com", "ratesplayer")
    headers = auth_headers(tokens["access_token"])
    deposit(client, tokens["access_token"], "80.00")
    before = cash(client, tokens["access_token"])

    rates.fx_rates = '{"EUR": 42.0}'          # someone fat-fingers the feed
    assert cash(client, tokens["access_token"]) == before


# ---------------------------------------------------------------------------
# the FX admin surface
# ---------------------------------------------------------------------------
def test_an_operator_can_update_display_rates(client, admin_headers, rates):
    resp = client.put(
        "/api/admin/fx-rates",
        json={"rates": {"EUR": 0.95, "GBP": 0.8}},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["fx"]["rates"]["EUR"] == 0.95
    assert resp.json()["fx"]["source"] == "configured"
    assert resp.json()["fx"]["updated_at"]


def test_fx_updates_are_audited(client, admin_headers):
    client.put(
        "/api/admin/fx-rates", json={"rates": {"EUR": 0.94}}, headers=admin_headers
    )
    log = client.get("/api/admin/audit?limit=20", headers=admin_headers)
    assert log.status_code == 200, log.text
    entries = log.json()["entries"] if isinstance(log.json(), dict) else log.json()
    assert any(e.get("action") == "fx.rates_update" for e in entries), (
        "a rate change that moves what every player sees was not recorded"
    )


def test_the_audit_trail_is_visible_in_both_views(client, admin_headers):
    """An action recorded by `audit()` must appear in the compliance view.

    The two audit tables are written by one helper precisely because they had
    drifted: auth events and money actions were recorded in `session_audit` and
    invisible in `audit_log`, which is the table an auditor reads.
    """
    client.put(
        "/api/admin/fx-rates", json={"rates": {"EUR": 0.93}}, headers=admin_headers
    )

    compliance = client.get("/api/admin/audit?action=fx.rates", headers=admin_headers).json()
    assert any(e["action"] == "fx.rates_update" for e in compliance["entries"])
    entry = next(e for e in compliance["entries"] if e["action"] == "fx.rates_update")
    assert entry["actor"], "the audit row does not say who made the change"

    sessions = client.get("/api/admin/sessions", headers=admin_headers).json()
    sec_rows = sessions["sessions"] if isinstance(sessions, dict) else sessions
    assert any(r["action"] == "fx.rates_update" for r in sec_rows), (
        "the security view lost an event the compliance view has"
    )


def test_a_player_cannot_change_the_rates(client):
    tokens = register(client, "notadmin@example.com", "notadminplayer")
    resp = client.put(
        "/api/admin/fx-rates",
        json={"rates": {"EUR": 999}},
        headers=auth_headers(tokens["access_token"]),
    )
    assert resp.status_code == 403


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"rates": {"USD": 2.0}}, "settlement currency"),
        ({"rates": {"XYZ": 1.5}}, "Unknown currency"),
        ({"rates": {"EUR": 0}}, "greater than zero"),
        ({"rates": {"EUR": -3}}, "greater than zero"),
    ],
)
def test_bad_rate_updates_are_refused(client, admin_headers, payload, expected):
    resp = client.put("/api/admin/fx-rates", json=payload, headers=admin_headers)
    assert resp.status_code == 400, resp.text
    assert expected.lower() in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# transaction history (the dashboard's data source)
# ---------------------------------------------------------------------------
def test_transaction_history_shows_settled_movement(client):
    tokens = register(client, "hist@example.com", "histplayer")
    headers = auth_headers(tokens["access_token"])
    deposit(client, tokens["access_token"], "50.00")

    body = client.get("/api/wallet/transactions", headers=headers).json()
    assert body["entries"], "a settled deposit did not appear in the history"
    assert body["amounts_are_minor_units"] is True
    assert body["settlement_currency"] == settings.settlement_currency
    top = body["entries"][0]
    assert {"kind", "amount", "balance_after", "created_at", "status"} <= set(top)


def test_transaction_history_shows_in_flight_items(client):
    """A deposit the processor has not confirmed is not in the ledger, and the
    player still has to be able to see it."""
    tokens = register(client, "inflight@example.com", "inflightplayer")
    headers = auth_headers(tokens["access_token"])
    created = client.post(
        "/api/wallet/deposits",
        json={"amount": "30.00", "method": "card"},
        headers=headers,
    ).json()

    body = client.get("/api/wallet/transactions", headers=headers).json()
    pending = body["pending"]
    assert any(p["id"] == created["deposit_id"] for p in pending)
    settled_ids = {e["reference"] for e in body["entries"]}
    assert created["deposit_id"] not in settled_ids, "settled and pending disagreed"


def test_transaction_history_is_paginated_and_ordered(client):
    tokens = register(client, "page@example.com", "pageplayer")
    headers = auth_headers(tokens["access_token"])
    for _ in range(3):
        deposit(client, tokens["access_token"], "10.00")

    page1 = client.get("/api/wallet/transactions?limit=2", headers=headers).json()
    page2 = client.get("/api/wallet/transactions?limit=2&offset=2", headers=headers).json()

    assert len(page1["entries"]) == 2
    assert page1["total"] >= 3
    assert {e["id"] for e in page1["entries"]} & {e["id"] for e in page2["entries"]} == set()

    times = [e["created_at"] for e in page1["entries"]]
    assert times == sorted(times, reverse=True), "history is not newest-first"


def test_transaction_history_can_be_filtered_by_kind(client):
    tokens = register(client, "filter@example.com", "filterplayer")
    headers = auth_headers(tokens["access_token"])
    deposit(client, tokens["access_token"], "20.00")

    body = client.get("/api/wallet/transactions?kind=deposit", headers=headers).json()
    assert body["entries"]
    assert all(e["kind"] == "deposit" for e in body["entries"])
