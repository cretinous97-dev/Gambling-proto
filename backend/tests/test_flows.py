"""End-to-end money flows through the HTTP API.

These are the tests that matter commercially: deposit, play, withdraw, approve,
pay. If one of these breaks, players either cannot get their money or can get
it twice.
"""
from __future__ import annotations

from tests.conftest import auth_headers, cash, deposit, register


def test_register_login_and_wallet(client):
    tokens = register(client, "player1@example.com", "playerone")
    headers = auth_headers(tokens["access_token"])

    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["username"] == "playerone"
    assert body["balances"]["cash"] == 0

    login = client.post(
        "/api/auth/login",
        json={"email": "player1@example.com", "password": "Passw0rd!23"},
    )
    assert login.status_code == 200, login.text
    assert "access_token" in login.json()

    bad = client.post(
        "/api/auth/login",
        json={"email": "player1@example.com", "password": "wrong-password"},
    )
    assert bad.status_code == 401


def test_duplicate_registration_refused(client):
    register(client, "dupe@example.com", "dupeuser")
    again = client.post(
        "/api/auth/register",
        json={
            "email": "dupe@example.com",
            "username": "dupeuser2",
            "password": "Passw0rd!23",
            "date_of_birth": "1990-01-01",
            "country": "BT",
            "accepts_terms": True,
        },
    )
    assert again.status_code == 409


def test_underage_registration_refused(client):
    resp = client.post(
        "/api/auth/register",
        json={
            "email": "kid@example.com",
            "username": "kiduser",
            "password": "Passw0rd!23",
            "date_of_birth": "2015-01-01",
            "country": "BT",
            "accepts_terms": True,
        },
    )
    assert resp.status_code == 403


def test_blocked_jurisdiction_refused(client):
    resp = client.post(
        "/api/auth/register",
        json={
            "email": "us@example.com",
            "username": "usplayer",
            "password": "Passw0rd!23",
            "date_of_birth": "1990-01-01",
            "country": "US",
            "accepts_terms": True,
        },
    )
    assert resp.status_code == 403


def test_deposit_credited_once_and_visible(client):
    tokens = register(client, "dep1@example.com", "depone")
    token = tokens["access_token"]

    first = deposit(client, token, "100.00")
    assert first["status"] == "succeeded"
    assert first["credited"] == 10_000
    assert cash(client, token) == 10_000

    # Replaying the sandbox confirmation must not credit twice.
    again = client.post(
        f"/api/wallet/deposits/{first['id']}/simulate",
        json={"outcome": "succeed"},
        headers=auth_headers(token),
    )
    assert again.status_code == 409
    assert cash(client, token) == 10_000

    statement = client.get("/api/wallet/statement", headers=auth_headers(token)).json()
    assert statement["entries"][0]["amount"] == 10_000
    assert statement["entries"][0]["balance_after"] == 10_000


def test_deposit_idempotency_key_prevents_duplicate_charge(client):
    tokens = register(client, "dep2@example.com", "deptwo")
    token = tokens["access_token"]
    payload = {"amount": "50.00", "method": "card", "idempotency_key": "client-key-1"}

    a = client.post("/api/wallet/deposits", json=payload, headers=auth_headers(token))
    b = client.post("/api/wallet/deposits", json=payload, headers=auth_headers(token))
    assert a.status_code == 201 and b.status_code == 201
    assert a.json()["deposit_id"] == b.json()["deposit_id"]


def test_deposit_limits_enforced(client):
    tokens = register(client, "dep3@example.com", "depthree")
    token = tokens["access_token"]
    too_small = client.post(
        "/api/wallet/deposits",
        json={"amount": "1.00", "method": "card"},
        headers=auth_headers(token),
    )
    assert too_small.status_code == 400
    too_big = client.post(
        "/api/wallet/deposits",
        json={"amount": "999999.00", "method": "card"},
        headers=auth_headers(token),
    )
    assert too_big.status_code == 400


def test_daily_deposit_limit_enforced_by_operator_rule(client):
    tokens = register(client, "dep4@example.com", "depfour")
    token = tokens["access_token"]

    resp = client.post(
        "/api/auth/responsible-gambling",
        json={"deposit_limit_daily": "150.00"},
        headers=auth_headers(token),
    )
    assert resp.status_code == 200, resp.text

    deposit(client, token, "100.00")
    over = client.post(
        "/api/wallet/deposits",
        json={"amount": "100.00", "method": "card"},
        headers=auth_headers(token),
    )
    assert over.status_code == 403
    assert "limit" in over.json()["detail"].lower()


def test_dice_bet_settles_and_debits_correctly(client):
    tokens = register(client, "dice1@example.com", "diceone")
    token = tokens["access_token"]
    deposit(client, token, "100.00")

    before = cash(client, token)
    resp = client.post(
        "/api/games/play",
        json={"game": "dice", "stake": "1.00", "params": {"target": 50.0, "direction": "over"}},
        headers=auth_headers(token),
    )
    assert resp.status_code == 200, resp.text
    bet = resp.json()
    assert bet["settled"] is True
    assert bet["payout"] == int(1.00 * 1.98 * 100) if bet["result"]["win"] else bet["payout"] == 0
    after = cash(client, token)
    assert after == before - 100 + bet["payout"]


def test_bet_rejected_when_balance_is_zero(client):
    tokens = register(client, "poor@example.com", "poorplayer")
    token = tokens["access_token"]
    resp = client.post(
        "/api/games/play",
        json={"game": "dice", "stake": "5.00", "params": {"target": 50.0}},
        headers=auth_headers(token),
    )
    assert resp.status_code == 402


def test_bet_limits_enforced(client):
    tokens = register(client, "lim1@example.com", "limone")
    token = tokens["access_token"]
    deposit(client, token, "100.00")
    too_small = client.post(
        "/api/games/play",
        json={"game": "dice", "stake": "0.01", "params": {"target": 50.0}},
        headers=auth_headers(token),
    )
    assert too_small.status_code == 400
    too_big = client.post(
        "/api/games/play",
        json={"game": "dice", "stake": "5000.00", "params": {"target": 50.0}},
        headers=auth_headers(token),
    )
    assert too_big.status_code == 400


def test_roulette_leg_stakes_must_match(client):
    tokens = register(client, "rl1@example.com", "rlone")
    token = tokens["access_token"]
    deposit(client, token, "100.00")
    resp = client.post(
        "/api/games/play",
        json={
            "game": "roulette",
            "stake": "5.00",
            "params": {"bets": [{"kind": "red", "stake": 100}]},
        },
        headers=auth_headers(token),
    )
    assert resp.status_code == 400


def test_blackjack_full_hand_flow(client):
    tokens = register(client, "bj1@example.com", "bjone")
    token = tokens["access_token"]
    deposit(client, token, "200.00")

    deal = client.post(
        "/api/games/blackjack/deal",
        json={"stake": "10.00"},
        headers=auth_headers(token),
    )
    assert deal.status_code == 200, deal.text
    body = deal.json()
    bet_id = body["id"]
    table = body["table"]
    assert len(table["hands"][0]["cards"]) == 2

    # Play the hand out (or it is already finished by a natural blackjack).
    guard = 0
    while not table["finished"] and guard < 12:
        guard += 1
        action = "hit" if table["hands"][table["active"]]["total"] < 17 else "stand"
        resp = client.post(
            f"/api/games/blackjack/{bet_id}/action",
            json={"action": action},
            headers=auth_headers(token),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        table = body["table"]

    assert table["finished"] is True
    assert body["settled"] is True
    summary = client.get("/api/wallet/summary", headers=auth_headers(token)).json()
    assert summary["balances"]["locked"] == 0
    # Stake was 10.00 and the hand is closed: cash must equal deposit +/- outcome
    assert 10_000 <= summary["balances"]["cash"] <= 30_000


def test_mines_flow_with_cashout(client):
    tokens = register(client, "mines1@example.com", "minesone")
    token = tokens["access_token"]
    deposit(client, token, "100.00")

    start = client.post(
        "/api/games/mines/start",
        json={"stake": "1.00", "mines": 3},
        headers=auth_headers(token),
    )
    assert start.status_code == 200, start.text
    bet_id = start.json()["id"]
    board = start.json()["result"]["board"]

    safe = [i for i in range(25) if i not in board][:2]
    opened = None
    for idx in safe:
        opened = client.post(
            f"/api/games/mines/{bet_id}/open", json={"index": idx}, headers=auth_headers(token)
        )
        assert opened.status_code == 200, opened.text
        if opened.json()["settled"]:
            break

    if not opened.json()["settled"]:
        out = client.post(f"/api/games/mines/{bet_id}/cashout", headers=auth_headers(token))
        assert out.status_code == 200, out.text
        assert out.json()["payout"] > 100      # at least 2 safe tiles at 3 mines
    assert cash(client, token) > 0


def test_self_exclusion_blocks_betting(client):
    tokens = register(client, "se1@example.com", "seone")
    token = tokens["access_token"]
    deposit(client, token, "50.00")

    client.post(
        "/api/auth/responsible-gambling",
        json={"self_exclude_days": 7},
        headers=auth_headers(token),
    )
    blocked = client.post(
        "/api/games/play",
        json={"game": "dice", "stake": "1.00", "params": {"target": 50.0}},
        headers=auth_headers(token),
    )
    assert blocked.status_code == 403
    assert "self-excluded" in blocked.json()["detail"].lower()

    blocked_deposit = client.post(
        "/api/wallet/deposits",
        json={"amount": "10.00", "method": "card"},
        headers=auth_headers(token),
    )
    assert blocked_deposit.status_code == 403


def test_kyc_required_before_large_withdrawal(client):
    tokens = register(client, "kyc1@example.com", "kycone")
    token = tokens["access_token"]
    deposit(client, token, "2000.00")
    resp = client.post(
        "/api/wallet/withdrawals",
        json={"amount": "1500.00", "method": "crypto_usdt", "destination": "TJmvQ1examplewalletaddress"},
        headers=auth_headers(token),
    )
    assert resp.status_code == 403
    assert "verification" in resp.json()["detail"].lower()


def test_withdrawal_review_and_payout_admin_flow(client):
    """The full commercial loop: request -> admin approve -> paid -> books balance."""
    tokens = register(client, "wd1@example.com", "wdone")
    token = tokens["access_token"]
    deposit(client, token, "500.00")

    req = client.post(
        "/api/wallet/withdrawals",
        json={"amount": "200.00", "method": "crypto_usdt", "destination": "TJmvQ1examplewalletaddress"},
        headers=auth_headers(token),
    )
    assert req.status_code == 201, req.text
    wd = req.json()
    assert wd["status"] == "under_review"
    # Funds are reserved immediately: cash drops, locked rises.
    summary = client.get("/api/wallet/summary", headers=auth_headers(token)).json()
    assert summary["balances"]["cash"] == 30_000
    assert summary["balances"]["locked"] == 20_000

    admin_login = client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "Admin!2345"}
    )
    assert admin_login.status_code == 200, admin_login.text
    admin_token = admin_login.json()["access_token"]

    queue = client.get(
        "/api/admin/withdrawals", params={"status": "under_review"},
        headers=auth_headers(admin_token),
    )
    assert queue.status_code == 200
    assert any(w["id"] == wd["id"] for w in queue.json()["withdrawals"])

    approve = client.post(
        f"/api/admin/withdrawals/{wd['id']}/review",
        json={"approve": True, "note": "KYC checked"},
        headers=auth_headers(admin_token),
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()["status"] == "paid"          # sandbox pays instantly

    summary = client.get("/api/wallet/summary", headers=auth_headers(token)).json()
    assert summary["balances"]["cash"] == 30_000
    assert summary["balances"]["locked"] == 0

    integrity = client.get("/api/wallet/integrity", headers=auth_headers(token)).json()
    assert integrity["books_balanced"] is True


def test_withdrawal_rejection_returns_funds(client):
    tokens = register(client, "wd2@example.com", "wdtwo")
    token = tokens["access_token"]
    deposit(client, token, "300.00")

    wd = client.post(
        "/api/wallet/withdrawals",
        json={"amount": "100.00", "method": "crypto_btc", "destination": "bc1qexamplewalletaddress"},
        headers=auth_headers(token),
    ).json()

    admin_token = client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "Admin!2345"}
    ).json()["access_token"]

    reject = client.post(
        f"/api/admin/withdrawals/{wd['id']}/review",
        json={"approve": False, "reason": "Third-party deposit suspected"},
        headers=auth_headers(admin_token),
    )
    assert reject.status_code == 200, reject.text
    assert reject.json()["status"] == "rejected"
    assert cash(client, token) == 30_000


def test_player_can_cancel_pending_withdrawal(client):
    tokens = register(client, "wd3@example.com", "wdthree")
    token = tokens["access_token"]
    deposit(client, token, "300.00")
    wd = client.post(
        "/api/wallet/withdrawals",
        json={"amount": "100.00", "method": "crypto_eth", "destination": "0xexamplewalletaddress1234"},
        headers=auth_headers(token),
    ).json()
    cancel = client.post(
        f"/api/wallet/withdrawals/{wd['id']}/cancel", headers=auth_headers(token)
    )
    assert cancel.status_code == 200, cancel.text
    assert cancel.json()["status"] == "cancelled"
    assert cash(client, token) == 30_000


def test_insufficient_funds_for_withdrawal(client):
    tokens = register(client, "wd4@example.com", "wdfour")
    token = tokens["access_token"]
    deposit(client, token, "50.00")
    # 60.00 requested against a 50.00 cash balance
    resp = client.post(
        "/api/wallet/withdrawals",
        json={"amount": "60.00", "method": "crypto_usdt", "destination": "TQexamplewalletaddress9"},
        headers=auth_headers(token),
    )
    assert resp.status_code == 402


def test_admin_can_adjust_balance_and_it_is_audited(client):
    tokens = register(client, "adj1@example.com", "adjone")
    token = tokens["access_token"]
    user_id = client.get("/api/auth/me", headers=auth_headers(token)).json()["id"]

    admin_token = client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "Admin!2345"}
    ).json()["access_token"]

    credit = client.post(
        f"/api/admin/users/{user_id}/adjust",
        json={"amount": "25.00", "reason": "Goodwill credit for downtime"},
        headers=auth_headers(admin_token),
    )
    assert credit.status_code == 200, credit.text
    assert cash(client, token) == 2_500

    audit = client.get(
        "/api/admin/audit", params={"action": "wallet.adjust"}, headers=auth_headers(admin_token)
    ).json()
    assert any(e["target"] == user_id for e in audit["entries"])


def test_over_adjustment_beyond_balance_is_refused(client):
    tokens = register(client, "adj2@example.com", "adjtwo")
    token = tokens["access_token"]
    user_id = client.get("/api/auth/me", headers=auth_headers(token)).json()["id"]
    admin_token = client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "Admin!2345"}
    ).json()["access_token"]

    resp = client.post(
        f"/api/admin/users/{user_id}/adjust",
        json={"amount": "-500.00", "reason": "attempted clawback"},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 400


def test_chargeback_claws_back_the_deposit(client):
    tokens = register(client, "cb1@example.com", "cbone")
    token = tokens["access_token"]
    dep = deposit(client, token, "100.00")
    assert cash(client, token) == 10_000

    resp = client.post(
        f"/api/wallet/deposits/{dep['id']}/simulate",
        json={"outcome": "chargeback"},
        headers=auth_headers(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "chargeback"
    assert cash(client, token) == 0
    integrity = client.get("/api/wallet/integrity", headers=auth_headers(token)).json()
    assert integrity["books_balanced"] is True


def test_non_admin_cannot_reach_admin_endpoints(client):
    tokens = register(client, "pleb@example.com", "plebuser")
    token = tokens["access_token"]
    for path in ("/api/admin/dashboard", "/api/admin/users", "/api/admin/withdrawals"):
        resp = client.get(path, headers=auth_headers(token))
        assert resp.status_code == 403, path


def test_unauthenticated_is_401(client):
    assert client.get("/api/wallet/summary").status_code == 401
    assert client.get("/api/admin/dashboard").status_code == 401


def test_seed_rotation_reveals_old_seed(client):
    tokens = register(client, "seed1@example.com", "seedone")
    token = tokens["access_token"]
    before = client.get("/api/auth/seeds", headers=auth_headers(token)).json()

    rotated = client.post(
        "/api/auth/seeds/rotate",
        json={"client_seed": "my-lucky-seed"},
        headers=auth_headers(token),
    )
    assert rotated.status_code == 200, rotated.text
    body = rotated.json()
    assert body["revealed"]["server_seed_hash"] == before["server_seed_hash"]
    assert body["next"]["server_seed_hash"] != before["server_seed_hash"]
    assert body["next"]["client_seed"] == "my-lucky-seed"

    after = client.get("/api/auth/seeds", headers=auth_headers(token)).json()
    assert after["previous"][-1]["server_seed"] == body["revealed"]["server_seed"]


def test_crash_round_state_and_history(client):
    state = client.get("/api/crash/state")
    assert state.status_code == 200, state.text
    body = state.json()
    assert body["phase"] in ("betting", "running", "crashed", "settled")
    history = client.get("/api/crash/history")
    assert history.status_code == 200


def test_websocket_multiplier_feed(client):
    with client.websocket_connect("/api/crash/ws") as ws:
        message = ws.receive_json()
        assert message["type"] == "crash.state"
        assert "phase" in message
