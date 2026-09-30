"""End-to-end smoke test against a RUNNING server.

This is not a unit test - it drives the real HTTP API the way the browser does,
with the money moving for real inside the ledger:

    register -> deposit -> sandbox settlement -> play every game
             -> withdraw -> admin review -> payout -> books still balance

Run it against a live instance (defaults to localhost:8000):

    PYTHONPATH=. ../.venv/bin/python scripts/smoke_e2e.py
    PYTHONPATH=. ../.venv/bin/python scripts/smoke_e2e.py https://casino.example.com

It creates throwaway users with a timestamped suffix, so it is safe to run
repeatedly against the same database.
"""
from __future__ import annotations

from pathlib import Path

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")


def operator_credentials() -> tuple[str, str]:
    """The operator this run should sign in as.

    Follows the same resolution order as the app: a real environment variable
    wins, then `backend/.env` (the Makefile runs these from `backend/`), then
    the documented default. Reading `.env` matters because the server the dev
    is pointing at resolved its operator the same way - hardcoding the default
    here would sign in as an account that server never created, and report the
    app broken when the smoke run is what is wrong.
    """
    email = os.environ.get("ADMIN_EMAIL")
    password = os.environ.get("ADMIN_PASSWORD")
    if not (email and password):
        env_file = Path(__file__).resolve().parent.parent / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip("'\"")
                if not value or key.startswith("#"):
                    continue
                if key == "ADMIN_EMAIL" and not email:
                    email = value
                elif key == "ADMIN_PASSWORD" and not password:
                    password = value
    return (email or "admin@casino.example.com", password or "Admin!2345")


ADMIN_EMAIL, ADMIN_PASSWORD = operator_credentials()
STAMP = str(int(time.time()))[-6:]
PASSWORD = "SmokeTest!2345"

PASSED: list[str] = []
FAILED: list[str] = []


def call(method: str, path: str, body=None, token: str | None = None, expect=None):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw, status = resp.read(), resp.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), exc.code
    try:
        payload = json.loads(raw or b"null")
    except json.JSONDecodeError:
        payload = {"_raw": raw[:200].decode("utf-8", "replace")}
    if expect is not None and status != expect:
        raise AssertionError(f"{method} {path} -> {status} (wanted {expect}): {payload}")
    return status, payload


def check(label: str, condition: bool, detail: str = ""):
    (PASSED if condition else FAILED).append(label)
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    return condition


def money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


print(f"end-to-end smoke against {BASE}\n")

# ---------------------------------------------------------------- 1. account
print("1. account")
_, reg = call("POST", "/api/auth/register", {
    "email": f"smoke{STAMP}@example.com",
    "username": f"smoke{STAMP}",
    "password": PASSWORD,
    "date_of_birth": "1992-04-04",
    "country": "BT",
    "accepts_terms": True,
}, expect=201)
player = reg["access_token"]
check("register a player", bool(player))

_, summary = call("GET", "/api/wallet/summary", token=player, expect=200)
check("new wallet starts empty", summary["balances"]["cash"] == 0)

# ---------------------------------------------------------------- 2. deposit
print("\n2. deposit (card, sandbox provider)")
_, dep = call("POST", "/api/wallet/deposits",
              {"amount": "500.00", "method": "card"}, token=player, expect=201)
deposit_id = dep["deposit_id"]
check("deposit created", bool(deposit_id), f"status={dep['status']}")

_, before = call("GET", "/api/wallet/summary", token=player, expect=200)
check("unsettled deposit does not credit the player", before["balances"]["cash"] == 0,
      f"cash={money(before['balances']['cash'])}")

call("POST", f"/api/wallet/deposits/{deposit_id}/simulate", {"outcome": "succeed"},
     token=player, expect=200)
_, after = call("GET", "/api/wallet/summary", token=player, expect=200)
check("settled deposit credits cash", after["balances"]["cash"] == 50_000,
      f"cash={money(after['balances']['cash'])}")

status, again = call("POST", f"/api/wallet/deposits/{deposit_id}/simulate",
                     {"outcome": "succeed"}, token=player)
check("double-settling a deposit is rejected", status in (400, 409), f"got {status}")

# ------------------------------------------------------------------ 3. games
print("\n3. games (each one places a real wager)")


def play(game: str, stake: str, params: dict, label: str, token: str = player):
    _, res = call("POST", "/api/games/play",
                  {"game": game, "stake": stake, "params": params,
                   "idempotency_key": f"{game}-{STAMP}-{time.time_ns()}"},
                  token=token, expect=200)
    check(f"{label}", res["settled"] is True,
          f"stake={res['stake']} payout={res['payout']} multiplier={res['multiplier']}")
    return res


play("dice", "5.00", {"target": 50, "direction": "over"}, "dice rolls and settles")
play("limbo", "2.00", {"target": 2.0}, "limbo settles")
play("coinflip", "1.00", {"side": "heads"}, "coin flip settles")
play("plinko", "1.00", {"rows": 12, "risk": "medium"}, "plinko drops and settles")
play("wheel", "2.00", {"risk": "medium"}, "wheel spins and settles")
play("keno", "2.00", {"picks": [1, 7, 13, 22, 30]}, "keno draws and settles")
play("roulette", "5.00", {"bets": [{"kind": "red", "stake": 200},
                                          {"kind": "dozen", "number": 1, "stake": 200},
                                          {"kind": "straight", "number": 17, "stake": 100}]},
     "roulette (three legs, one atomic wager) spins and settles")
play("slots", "2.00", {}, "slots spin and settle")

# 20-line slot rejects a stake that cannot be split into whole lines
status, _ = call("POST", "/api/games/play",
                 {"game": "slots", "stake": "2.13", "params": {},
                  "idempotency_key": f"bad-{STAMP}"}, token=player)
check("slots rejects a stake that is not a whole number of lines", status in (400, 422),
      f"got {status}")

# mines: server holds the board
_, mines = call("POST", "/api/games/mines/start", {"stake": "2.00", "mines": 3},
                token=player, expect=200)
mine_id = mines["id"]
mined = set(mines["result"]["board"])
safe = next(i for i in range(25) if i not in mined)
call("POST", f"/api/games/mines/{mine_id}/open", {"index": safe}, token=player, expect=200)
_, cashout = call("POST", f"/api/games/mines/{mine_id}/cashout", token=player, expect=200)
check("mines: reveal a safe tile then cash out", cashout["settled"] is True,
      f"payout={cashout['payout']} multiplier={cashout['multiplier']}")

# blackjack: server holds the shoe
_, bj = call("POST", "/api/games/blackjack/deal", {"stake": "10.00"}, token=player, expect=200)
bj_id = bj["id"]
table = bj["table"]
while not table["finished"]:
    _, bj = call("POST", f"/api/games/blackjack/{bj_id}/action", {"action": "stand"},
                 token=player, expect=200)
    table = bj["table"]
check("blackjack: deal, stand, settle", table["finished"] is True,
      f"payout={bj['payout']}")

# crash: shared live round
_, state = call("GET", "/api/crash/state", expect=200)
deadline = time.time() + 45
while state["phase"] != "betting" and time.time() < deadline:
    time.sleep(1)
    _, state = call("GET", "/api/crash/state", expect=200)
if state["phase"] == "betting":
    _, crash = call("POST", "/api/crash/bet",
                    {"stake": "3.00", "auto_cashout": 1.2}, token=player, expect=201)
    check("crash: joined a live round", bool(crash.get("round_number")),
          f"round={crash.get('round_number')}")

    # the stake stays in the locked account until the shared round settles, so
    # wait it out before looking at balances again
    settle_deadline = time.time() + 120
    settled = False
    while time.time() < settle_deadline:
        time.sleep(2)
        _, mine = call("GET", "/api/crash/state/me", token=player, expect=200)
        bet = mine.get("your_bet")
        if bet and bet.get("settled"):
            settled = True
            break
        if bet is None and mine["phase"] == "betting":
            settled = True  # the next round opened, so ours is done
            break
    check("crash: the round settled and released the stake", settled,
          f"payout={mine.get('crash_point')}x")
else:
    check("crash: joined a live round", False, f"phase stayed {state['phase']}")
    print("     (the crash loop runs on a timer; re-run if the window was missed)")

# ------------------------------------------------------------- 4. responsible
print("\n4. player protection")
status, limits = call("POST", "/api/auth/responsible-gambling",
                      {"loss_limit_daily": "200.00", "deposit_limit_daily": "1000.00"},
                      token=player)
check("a player can set a daily loss limit",
      status == 200 and limits["loss_limit_daily"] == 20_000,
      f"loss_limit={limits.get('loss_limit_daily')} (cents)")
status, _ = call("POST", "/api/games/play",
                 {"game": "dice", "stake": "5.00", "params": {"target": 50, "direction": "over"},
                  "idempotency_key": f"after-limit-{STAMP}"}, token=player)
check("betting still works below the limit", status == 200, f"got {status}")

status, kyc = call("POST", "/api/auth/kyc",
                   {"full_name": "Smoke Tester", "doc_type": "passport",
                    "file_ref": f"passport-{STAMP}.jpg"}, token=player, expect=201)
check("KYC document accepted for review", kyc.get("status") in ("pending", "verified"),
      f"kyc={kyc.get('status')}")

# ----------------------------------------------------------- 5. provably fair
print("\n5. provably fair")
_, seeds = call("GET", "/api/auth/seeds", token=player, expect=200)
check("the player can read their seed commitment",
      bool(seeds["server_seed_hash"]) and "server_seed" not in seeds,
      "the seed itself stays hidden until it is rotated")
check("the commitment is bound to the player's client seed",
      bool(seeds["client_seed"]), f"nonce={seeds['nonce']}")

# ------------------------------------------------------------- 6. withdrawal
print("\n6. withdrawal + admin approval")
_, wallet = call("GET", "/api/wallet/summary", token=player, expect=200)
check("player has a balance to withdraw", wallet["balances"]["cash"] > 20_000,
      f"cash={money(wallet['balances']['cash'])}")

_, wd = call("POST", "/api/wallet/withdrawals",
             {"amount": "100.00", "method": "crypto_usdt",
              "destination": "TJmvQ1xSmokeTestPayoutAddress"},
             token=player, expect=201)
wd_id = wd["id"]
check("withdrawal request created", wd["status"] in ("requested", "under_review"),
      f"status={wd['status']}")

_, held = call("GET", "/api/wallet/summary", token=player, expect=200)
check("cash moved into the locked account",
      held["balances"]["locked"] >= 10_000 and held["balances"]["cash"] < wallet["balances"]["cash"],
      f"cash={money(held['balances']['cash'])} locked={money(held['balances']['locked'])}")

_, admin_login = call("POST", "/api/auth/login",
                      {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, expect=200)
admin = admin_login["access_token"]
check("admin can sign in", bool(admin))

_, queue = call("GET", "/api/admin/withdrawals?status=under_review", token=admin, expect=200)
check("withdrawal is visible in the admin queue",
      any(w["id"] == wd_id for w in queue["withdrawals"]))

_, reviewed = call("POST", f"/api/admin/withdrawals/{wd_id}/review",
                   {"approve": True, "note": "smoke test"}, token=admin, expect=200)
check("admin approval pays the withdrawal out", reviewed["status"] in ("approved", "paid"),
      f"status={reviewed['status']} ref={reviewed.get('provider_ref')}")

_, done = call("GET", "/api/wallet/summary", token=player, expect=200)
check("locked funds are gone after payout", done["balances"]["locked"] == 0,
      f"locked={money(done['balances']['locked'])}")

# --------------------------------------------------------------- 7. the books
print("\n7. books")
_, books = call("GET", "/api/wallet/integrity", token=player, expect=200)
check("ledger sums to zero after the whole flow", books["books_balanced"] is True)

_, dash = call("GET", "/api/admin/dashboard", token=admin, expect=200)
check("dashboard reports the flow", dash["money_24h"]["deposits"] >= 50_000,
      f"deposits={money(dash['money_24h']['deposits'])} "
      f"withdrawals={money(dash['money_24h']['withdrawals'])} "
      f"ggr={money(dash['money_24h']['ggr'])}")

# ------------------------------------------------------------- 8. admin pages
print("\n8. every admin surface answers")
for label, path in [
    ("revenue", "/api/admin/revenue?days=7"),
    ("health", "/api/admin/health"),
    ("players", "/api/admin/users"),
    ("deposits", "/api/admin/deposits"),
    ("aml queue", "/api/admin/aml-queue"),
    ("bonus codes", "/api/admin/bonuses/codes"),
    ("big wins", "/api/admin/big-wins"),
    ("audit log", "/api/admin/audit"),
    ("sessions", "/api/admin/sessions"),
    ("ledger explorer", "/api/admin/ledger"),
    ("webhooks", "/api/admin/webhooks"),
]:
    status, payload = call("GET", path, token=admin)
    check(f"admin {label}", status == 200 and payload is not None, f"http {status}")

# ------------------------------------------------------------ 9. public pages
print("\n9. public website")
for label, path in [
    ("game catalogue", "/api/games/catalog"),
    ("jackpot", "/api/jackpot"),
    ("promotions", "/api/promotions"),
    ("leaderboard", "/api/games/leaderboard"),
    ("legal templates", "/api/legal/terms"),
]:
    status, payload = call("GET", path)
    check(f"public {label}", status == 200 and payload is not None, f"http {status}")

status, _ = call("GET", "/api/admin/users", token=player)
check("a player token cannot reach the back office", status == 403, f"got {status}")

# --------------------------------------------------------------------- result
print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
if FAILED:
    for name in FAILED:
        print(f"  FAILED: {name}")
    sys.exit(1)
print("end-to-end money flow verified: deposit -> play -> withdraw -> payout")
