"""Sessions must survive a restart when SECRET_KEY is not configured.

This is the question that decides whether SECRET_KEY is mandatory. The app
generates one when it is absent, but on a serverless platform every cold start
is a NEW process on a possibly different machine: if each one generated its own
key, every player would be logged out at random.

The only way to be sure is to use two separate processes against one database -
which is exactly what this does.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent

#: Runs in a child process: boot the app, then either register a user (and
#: print a token) or verify a token. Printed as one JSON line.
CHILD_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, os.environ["BACKEND_DIR"])
from app.main import bootstrap_app, app
bootstrap_app()
from fastapi.testclient import TestClient

mode = sys.argv[1]
with TestClient(app) as client:
    if mode == "issue":
        r = client.post("/api/auth/register", json={
            "email": "restart@example.com", "username": "restartuser",
            "password": "Passw0rd!23", "date_of_birth": "1990-01-01",
            "country": "BT", "accepts_terms": True,
        })
        assert r.status_code == 201, r.text
        print(json.dumps({"token": r.json()["access_token"]}))
    else:
        token = sys.argv[2]
        r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        print(json.dumps({"status": r.status_code, "body": r.text[:120]}))
"""


def run_child(mode: str, db_path: Path, token: str | None = None) -> dict:
    env = {
        **os.environ,
        "BACKEND_DIR": str(BACKEND),
        "PYTHONPATH": str(BACKEND),
        "DATABASE_URL": f"sqlite:///{db_path}",
        "VERCEL": "1",                    # serverless semantics
        "CORS_ORIGINS": "*",
        "ADMIN_EMAIL": "admin@example.com",
        "ADMIN_PASSWORD": "Passw0rd!23",
        # Ignore any developer's `backend/.env`. These children run from
        # `backend/`, where pydantic-settings would read one - and a local
        # SECRET_KEY is exactly the value the test is withholding. Without
        # this, "unset" means "unset unless you happen to have configured it",
        # and the test silently stops asserting anything on the machines most
        # likely to run it.
        "ENV_FILE": os.devnull,
    }
    env.pop("SECRET_KEY", None)           # the whole point
    args = [sys.executable, "-c", CHILD_SCRIPT, mode]
    if token:
        args.append(token)

    result = subprocess.run(
        args, capture_output=True, text=True, env=env, cwd=str(BACKEND), timeout=120
    )
    assert result.returncode == 0, f"child failed:\n{result.stdout}\n{result.stderr}"
    line = [ln for ln in result.stdout.splitlines() if ln.startswith("{")][-1]
    return json.loads(line)


@pytest.fixture()
def fresh_db(tmp_path: Path):
    db = tmp_path / "casino.db"
    yield db
    for suffix in ("", "-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)


def test_session_survives_a_restart_without_secret_key(fresh_db: Path):
    """Issue a token in one process, use it in another, share only the database."""
    issued = run_child("issue", fresh_db)
    assert issued["token"], issued

    # A brand-new process: new memory, new module import, same database.
    verified = run_child("verify", fresh_db, token=issued["token"])
    assert verified["status"] == 200, (
        "the token was rejected by a second process - the signing key is not "
        f"shared, so every cold start would log players out: {verified}"
    )


def test_the_key_is_stored_once_not_regenerated(fresh_db: Path):
    """Independent boots must agree on one persisted key, not mint new ones."""
    import sqlite3

    issued = run_child("issue", fresh_db)

    def stored_value() -> str:
        with sqlite3.connect(fresh_db) as conn:
            rows = conn.execute(
                "SELECT value FROM app_settings WHERE key = 'session_signing_key'"
            ).fetchall()
        assert len(rows) == 1, f"expected exactly one stored key, found {len(rows)}"
        assert len(rows[0][0]) >= 32, "the stored key is too short to be safe"
        return rows[0][0]

    first = stored_value()

    # A second process must reuse it: the token from the first process has to
    # still verify, which is only possible if the key did not change.
    verified = run_child("verify", fresh_db, token=issued["token"])
    assert verified["status"] == 200, verified

    assert stored_value() == first, "the signing key was regenerated on a later boot"


def test_an_explicit_secret_key_wins_over_the_stored_one(fresh_db: Path):
    """If the operator sets SECRET_KEY it must be used, not the database copy."""
    run_child("issue", fresh_db)  # creates a stored key

    env = {
        **os.environ,
        "BACKEND_DIR": str(BACKEND),
        "PYTHONPATH": str(BACKEND),
        "DATABASE_URL": f"sqlite:///{fresh_db}",
        "VERCEL": "1",
        "SECRET_KEY": "an-explicitly-configured-key-" + "x" * 40,
    }
    script = (
        "import os, sys; sys.path.insert(0, os.environ['BACKEND_DIR']);"
        "from app.main import bootstrap_app; bootstrap_app();"
        "from app.security import signing_key;"
        "print(signing_key())"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, env=env, cwd=str(BACKEND), timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "an-explicitly-configured-key" in result.stdout, (
        "an environment SECRET_KEY must take precedence over the stored fallback"
    )


#: Boot the configuration exactly as the app does, and report the key it would
#: have used. Deliberately imports nothing else: the refusal must happen before
#: a database is opened or a server is bound.
PROD_BOOT_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, os.environ["BACKEND_DIR"])
try:
    from app.config import get_settings
    settings = get_settings()
except RuntimeError as exc:
    print(json.dumps({"refused": True, "message": str(exc)}))
else:
    print(json.dumps({"refused": False, "secret_key": settings.secret_key}))
"""


def boot_in_production(secret_key: str | None) -> dict:
    env = {
        **os.environ,
        "BACKEND_DIR": str(BACKEND),
        "PYTHONPATH": str(BACKEND),
        "ENVIRONMENT": "production",       # take the guard out of demo mode
        "DATABASE_URL": "sqlite:////tmp/production-boot-check.db",
        # Same reason as run_child: `**os.environ` above would otherwise let a
        # local `backend/.env` supply the very SECRET_KEY this test withholds.
        "ENV_FILE": os.devnull,
    }
    env.pop("SECRET_KEY", None)
    if secret_key is not None:
        env["SECRET_KEY"] = secret_key

    result = subprocess.run(
        [sys.executable, "-c", PROD_BOOT_SCRIPT],
        capture_output=True, text=True, env=env, cwd=str(BACKEND), timeout=120,
    )
    assert result.returncode == 0, f"child failed:\n{result.stdout}\n{result.stderr}"
    line = [ln for ln in result.stdout.splitlines() if ln.startswith("{")][-1]
    return json.loads(line)


def test_production_refuses_to_boot_without_an_explicit_secret_key():
    """The generated key is a development convenience, not a production one.

    The app persists a generated key in the database so that sessions survive a
    cold start, which is the right call for a test deployment and the wrong one
    for one holding real money: the key then lives beside the sessions it signs,
    so anything that can read the database - a leaked backup, a reporting
    replica, an over-broad read-only role - can mint a session for any account,
    the admin included.

    Refusing to start is the entire control. Without it, a copy-pasted deploy
    with a default key produces forgeable admin sessions and says nothing.
    """
    outcome = boot_in_production(secret_key=None)

    assert outcome["refused"], (
        "the app booted in production without SECRET_KEY - sessions signed with "
        "the built-in default are forgeable by anyone who has read this source"
    )
    assert "SECRET_KEY" in outcome["message"]


def test_production_refuses_an_empty_secret_key():
    """The dangerous one, because nothing about it looks wrong.

    `SECRET_KEY=` in a `.env` file - which is what a commented-out example
    line becomes when someone uncomments it - sets the variable to the empty
    string. It is present. It is not the built-in default. Everything boots,
    and every session token is signed with nothing at all, so anyone who has
    read this source can mint a token for any account.

    The guard used to test only for the literal default, so this sailed
    through. It is now a refusal, like an unset variable.
    """
    outcome = boot_in_production(secret_key="")

    assert outcome["refused"], (
        "production booted with an EMPTY SECRET_KEY - sessions signed with an "
        "empty key are forgeable by anyone"
    )
    assert "SECRET_KEY" in outcome["message"]


def test_production_refuses_a_key_short_enough_to_brute_force():
    """HS256 signs with the key directly; a short one falls offline."""
    outcome = boot_in_production(secret_key="hunter2")

    assert outcome["refused"], "a 7-character signing key was accepted in production"
    assert "short" in outcome["message"].lower() or "characters" in outcome["message"]


def test_production_boots_with_an_explicit_secret_key():
    """The guard must refuse the default, not production itself."""
    key = "a-real-key-from-the-environment-" + "x" * 32
    outcome = boot_in_production(secret_key=key)

    assert not outcome["refused"], outcome.get("message")
    assert outcome["secret_key"] == key
