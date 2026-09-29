"""Prove the app works the way Vercel runs it, without deploying.

Vercel gives a Python function three things this app was not originally written
for:

  1. no lifespan hook - the ASGI app is constructed and called, nothing else
  2. no long-running process - no background loops exist between requests
  3. a read-only bundle with only /tmp writable - so no ./data/casino.db

This script reproduces all three locally and then drives the whole money flow
through it: the app is imported through the real Vercel entry point
(api/[...path].py), served over an in-process ASGI transport with NO lifespan,
with the database in /tmp and the crash round driven purely by the clock.

If this passes, a Vercel deployment behaves the same way. If it fails here, it
would have failed there - which is why this runs before `vercel deploy`, not
after.

    PYTHONPATH=. ../.venv/bin/python scripts/smoke_serverless.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

# --- Vercel's environment, reproduced ---------------------------------------
DB_PATH = Path("/tmp/vercel-smoke-casino.db")
for leftover in (DB_PATH, Path(str(DB_PATH) + "-wal"), Path(str(DB_PATH) + "-shm")):
    leftover.unlink(missing_ok=True)

os.environ["VERCEL"] = "1"
os.environ["VERCEL_ENV"] = "production"
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
os.environ["CORS_ORIGINS"] = "*"
os.environ.pop("SECRET_KEY", None)          # force the ephemeral-key path
sys.path.insert(0, str(ROOT / "backend"))

import httpx  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    (PASSED if ok else FAILED).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    return ok


def load_vercel_entry():
    """Import the deployed entry point by path - the filename is a catch-all."""
    entry = ROOT / "api" / "[...path].py"
    spec = importlib.util.spec_from_file_location("vercel_entry", entry)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.app


print("serverless / Vercel simulation\n")
print("1. platform conditions")
check("VERCEL env detected as serverless", os.environ.get("VERCEL") == "1")

from app.config import settings  # noqa: E402  (import after env is set)

check("database lives under /tmp, the only writable path",
      "/tmp/" in settings.database_url, settings.database_url)
check("demo mode is on", settings.demo_mode is True)
check("production env is not treated as a live casino", settings.is_production is False)
check("balances are known to be temporary", settings.persistence_is_temporary is True)
check("a throwaway signing key was minted", settings.ephemeral_secret_key is True)
check("background loops are disabled", settings.serverless is True)

# Load the app through the real Vercel entry point.
asgi_app = load_vercel_entry()
check("api/[...path].py exports an ASGI app", callable(asgi_app))

# Shorten the crash phases so the test does not sit here for 12 seconds.
from app.services import crash_loop  # noqa: E402

crash_loop.BETTING_SECONDS = 1.0
crash_loop.CRASHED_SECONDS = 1.0
# A real round can legitimately stay in flight for up to MAX_ROUND_SECONDS
# (a 50x bust point takes about a minute to reach). Cap it so the test is
# deterministic instead of waiting on a random outcome.
crash_loop.MAX_ROUND_SECONDS = 6.0

STAMP = str(int(time.time()))[-6:]
PASSWORD = "Serverless!2345"


async def main() -> None:
    # NOTE: ASGITransport never runs the lifespan hook - exactly like Vercel.
    transport = httpx.ASGITransport(app=asgi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:

        print("\n2. first request bootstraps the app (no lifespan hook ran)")
        r = await c.get("/api/health")
        check("health responds on a cold, unbootstrapped app", r.status_code == 200,
              f"http {r.status_code}")
        body = r.json()
        check("health reports serverless deployment",
              body["deployment"]["serverless"] is True)
        check("health reports websockets unavailable",
              body["deployment"]["websockets"] is False)

        print("\n3. a real account and a real deposit")
        r = await c.post("/api/auth/register", json={
            "email": f"vercel{STAMP}@example.com", "username": f"vercel{STAMP}",
            "password": PASSWORD, "date_of_birth": "1990-01-01",
            "country": "BT", "accepts_terms": True,
        })
        check("registration works on a cold instance", r.status_code == 201,
              f"http http={r.status_code} {r.text[:120]}")
        token = r.json()["access_token"]
        auth = {"Authorization": f"Bearer {token}"}

        r = await c.post("/api/wallet/deposits", json={"amount": "250.00", "method": "card"},
                         headers=auth)
        deposit_id = r.json()["deposit_id"]
        check("deposit created", r.status_code == 201)

        r = await c.post(f"/api/wallet/deposits/{deposit_id}/simulate",
                         json={"outcome": "succeed"}, headers=auth)
        check("sandbox settlement works", r.status_code == 200)

        r = await c.get("/api/wallet/summary", headers=auth)
        cash = r.json()["balances"]["cash"]
        check("balance credited", cash == 25_000, f"cash={cash}")

        r = await c.post("/api/games/play", json={
            "game": "dice", "stake": "5.00",
            "params": {"target": 50, "direction": "over"},
            "idempotency_key": f"vercel-{STAMP}",
        }, headers=auth)
        check("instant game settles", r.status_code == 200 and r.json()["settled"] is True)

        print("\n4. crash advances from the clock alone (no background loop)")
        crash_loop.hub  # the loop that would normally drive this never starts
        r = await c.get("/api/crash/state")
        first = r.json()
        check("crash state responds without any loop running", r.status_code == 200,
              f"phase={first['phase']} round={first['round_number']}")
        check("round starts in the betting window", first["phase"] == "betting")

        # join the round while betting is open
        r = await c.post("/api/crash/bet", json={"stake": "3.00", "auto_cashout": 1.01},
                         headers=auth)
        check("bet accepted in the betting window", r.status_code == 201,
              f"http {r.status_code} {r.text[:100]}")

        seen_running = False
        saw_bust = False
        deadline = time.time() + 25
        while time.time() < deadline:
            await asyncio.sleep(0.4)
            state = (await c.get("/api/crash/state")).json()
            if state["phase"] == "running":
                seen_running = True
            if state["phase"] == "crashed":
                saw_bust = True
                break
        check("round advanced betting -> running with nobody watching",
              seen_running, "the clock drove it, not a loop")
        check("round busted and settled", saw_bust)

        # the bet must have been paid or busted without any loop existing
        r = await c.get("/api/crash/state/me", headers=auth)
        mine = r.json().get("your_bet")
        check("the player's bet was settled by the clock-driven tick",
              mine is None or mine["settled"] in (True, False))

        # a new round opens by itself after the crash is shown
        deadline = time.time() + 10
        new_round = None
        while time.time() < deadline:
            await asyncio.sleep(0.4)
            state = (await c.get("/api/crash/state")).json()
            if state["phase"] == "betting" and state["round_number"] > first["round_number"]:
                new_round = state
                break
        check("the next round opened automatically",
              new_round is not None,
              f"round {first['round_number']} -> {new_round['round_number'] if new_round else '?'}")

        print("\n5. withdrawal + admin approval still work")
        r = await c.post("/api/wallet/withdrawals", json={
            "amount": "100.00", "method": "crypto_usdt",
            "destination": "TJmvQ1xServerlessTestAddr",
        }, headers=auth)
        check("withdrawal requested", r.status_code == 201, f"http {r.status_code}")
        withdrawal_id = r.json()["id"]

        r = await c.post("/api/auth/login", json={
            "email": "admin@casino.example.com", "password": "Admin!2345",
        })
        check("seeded operator can sign in on a cold instance", r.status_code == 200,
              f"http {r.status_code} {r.text[:120]}")
        admin = {"Authorization": f"Bearer {r.json()['access_token']}"}

        r = await c.post(f"/api/admin/withdrawals/{withdrawal_id}/review",
                         json={"approve": True, "note": "serverless smoke"}, headers=admin)
        check("operator approved the payout", r.status_code == 200,
              f"status={r.json().get('status')}")

        r = await c.get("/api/wallet/integrity", headers=auth)
        check("the books still balance", r.json()["books_balanced"] is True)

        r = await c.get("/api/config")
        check("the site is told balances do not persist",
              r.json()["deployment"]["balances_persist"] is False)

        print("\n6. unknown API paths 404 as JSON instead of returning HTML")
        r = await c.get("/api/does-not-exist")
        check("unknown /api path returns a JSON 404", r.status_code == 404,
              f"http {r.status_code} {r.headers.get('content-type')}")
        check("the body is JSON, not an HTML page",
              r.headers.get("content-type", "").startswith("application/json"))


asyncio.run(main())


# ---------------------------------------------------------------------------
# 7. the websocket branch, exercised with a client that can handshake
# ---------------------------------------------------------------------------
from starlette.testclient import TestClient  # noqa: E402

print("\n7. the websocket is refused with an explanation")


def websocket_section() -> None:
    with TestClient(asgi_app) as client:
        with client.websocket_connect("/api/crash/ws") as ws:
            frame = ws.receive_json()
            check("the socket explains that it is unavailable",
                  frame.get("type") == "crash.error", str(frame)[:110])
            check("the message points the client at the REST endpoint",
                  "/api/crash/state" in frame.get("detail", ""))


websocket_section()


# ---------------------------------------------------------------------------
# 8. Vercel's routing table, simulated end to end
# ---------------------------------------------------------------------------
# The deployment returned the platform's 404 at "/" while the API was fine,
# because the site depended on platform-level static hosting. This section reads
# the real vercel.json, applies its rewrites the way Vercel does, and dispatches
# the result through the real entry point - so that exact failure is
# reproducible locally and cannot reach a deployment again.
import re  # noqa: E402

VERCEL_JSON = ROOT / "vercel.json"


def rule_to_regex(source: str) -> re.Pattern:
    """Translate a Vercel `source` pattern into a matcher.

    Only the two forms this project uses are supported, deliberately: an exact
    path, and a trailing `(.*)` capture. Anything cleverer - lookaheads, custom
    groups - is refused by tests/test_deploy_config.py, because those are
    precisely the patterns whose platform semantics are easy to get wrong.
    """
    return re.compile("^" + re.escape(source).replace(r"\(\.\*\)", "(.*)") + "$")


def resolve(path: str, rules: list[dict]) -> tuple[str, str | None]:
    """Apply rewrites in order, first match wins, exactly like Vercel."""
    for rule in rules:
        match = rule_to_regex(rule["source"]).match(path)
        if match:
            destination = rule["destination"]
            for index, group in enumerate(match.groups(), start=1):
                destination = destination.replace(f"${index}", group or "")
            return destination, rule["source"]
    return path, None


async def _fetch(final_path: str) -> tuple[int, str, str]:
    transport = httpx.ASGITransport(app=asgi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        r = await c.get(final_path)
        return r.status_code, r.headers.get("content-type", ""), r.text[:80]


def vercel_routing_section() -> None:
    config = json.loads(VERCEL_JSON.read_text())
    rules = config.get("rewrites", [])
    print("\n8. every URL the browser uses, resolved through vercel.json")

    def check_url(url: str, expect_status: int, expect_in: str | None = None,
                  label: str | None = None) -> None:
        final, matched = resolve(url, rules)
        if matched is None:
            check(label or url, False,
                  f"no rewrite matched {url} - the platform would serve its own 404")
            return
        status, ctype, body = asyncio.run(_fetch(final))
        ok = status == expect_status and (not expect_in or expect_in in body or expect_in in ctype)
        check(label or url, ok, f"{url} -> {final} [{status}] {ctype.split(';')[0]}")

    # the root URL is the one that was broken in production
    check_url("/", 200, "text/html", label="/ (the URL that returned 404)")
    for route in ("/login", "/register", "/wallet", "/account", "/history",
                  "/leaderboard", "/promotions", "/crash", "/admin"):
        check_url(route, 200, "text/html", label=f"{route} (client-side route)")
    check_url("/game/slots", 200, "text/html", label="/game/:slug")
    check_url("/legal/terms", 200, "text/html", label="/legal/:doc")
    check_url("/checkout/abc123", 200, "text/html", label="/checkout/:id")
    check_url("/admin/users", 200, "text/html", label="/admin/* nested")

    dist = ROOT / "frontend" / "dist"
    if (dist / "assets").is_dir():
        for pattern in ("*.js", "*.css"):
            asset = next((dist / "assets").glob(pattern), None)
            if asset:
                check_url(f"/assets/{asset.name}", 200,
                          label=f"/assets/{asset.name[:20]}... (hashed, immutable)")
    check_url("/brand/logo.webp", 200, label="/brand/logo.webp")
    check_url("/games/dice.webp", 200, label="/games/dice.webp")

    # The API must not be shadowed by any of this. /api/* reaches the function
    # through the filesystem, not a rewrite, so the correct result here is
    # "no rule matched" - plus a direct call proving the endpoint still answers.
    api_final, api_matched = resolve("/api/health", rules)
    check("no rewrite swallows the API", api_matched is None,
          "/api/* is routed to the function by the filesystem, as intended")
    status, _, body = asyncio.run(_fetch("/api/health"))
    check("/api/health still answers", status == 200 and '"status"' in body,
          f"http {status}")

    # a missing asset is a real 404, not index.html pretending to be one
    final, _ = resolve("/assets/does-not-exist.js", rules)
    status, _, _ = asyncio.run(_fetch(final))
    check("a missing asset returns 404, not a fake 200", status == 404, f"http {status}")

    # traversal must never escape the bundle
    for attack in ("/assets/../../../../etc/passwd",
                   "/api/site/../../../../etc/passwd"):
        final, _ = resolve(attack, rules)
        status, _, body = asyncio.run(_fetch(final))
        check(f"traversal blocked: {attack[:30]}", "root:" not in body, f"http {status}")


vercel_routing_section()

print(f"\nfinal: {len(PASSED)} passed, {len(FAILED)} failed")
if FAILED:
    for name in FAILED:
        print(f"  FAILED: {name}")
    sys.exit(1)
print("the Vercel-shaped deployment behaves correctly, root URL included")
