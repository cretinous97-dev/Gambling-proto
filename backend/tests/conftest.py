"""Test harness. Each test module gets a fresh SQLite database in a tmp dir."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

TMP = Path(tempfile.mkdtemp(prefix="casino-tests-"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TMP / 'test.db'}")
os.environ.setdefault("SECRET_KEY", "test-secret-not-for-production-use-only")
os.environ.setdefault("PAYMENT_PROVIDER", "sandbox")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SIGNUP_BONUS_USD", "0")
os.environ.setdefault("FIRST_DEPOSIT_BONUS_PCT", "0")
os.environ.setdefault("ADMIN_EMAIL", "admin@example.com")
os.environ.setdefault("ADMIN_PASSWORD", "Admin!2345")
os.environ.setdefault("RATE_LIMIT_BETS_PER_MIN", "100000")

from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine, session_scope  # noqa: E402
from app.main import app, seed_admin  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    seed_admin()
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    with session_scope() as session:
        yield session


@pytest.fixture
def admin_headers(client) -> dict:
    """Sign in as the seeded operator. Admin actions are the ones that move
    money out, so tests should exercise the real login, not a forged token."""
    from app.config import settings

    resp = client.post(
        "/api/auth/login",
        json={"email": settings.admin_email, "password": settings.admin_password},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def register(
    client: TestClient,
    email: str,
    username: str,
    password="Passw0rd!23",
    country: str = "BT",
) -> dict:
    resp = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "username": username,
            "password": password,
            "date_of_birth": "1995-04-12",
            "country": country,
            "accepts_terms": True,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def deposit(client: TestClient, token: str, amount: str, method="card") -> dict:
    resp = client.post(
        "/api/wallet/deposits",
        json={"amount": amount, "method": method},
        headers=auth_headers(token),
    )
    assert resp.status_code == 201, resp.text
    deposit_id = resp.json()["deposit_id"]
    done = client.post(
        f"/api/wallet/deposits/{deposit_id}/simulate",
        json={"outcome": "succeed"},
        headers=auth_headers(token),
    )
    assert done.status_code == 200, done.text
    return done.json()


def cash(client: TestClient, token: str) -> int:
    resp = client.get("/api/wallet/summary", headers=auth_headers(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["balances"]["cash"]
