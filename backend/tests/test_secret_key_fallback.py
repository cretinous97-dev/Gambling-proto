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
