"""Bootstrapping the operator account.

Why this has its own file
-------------------------
`seed_admin` runs inside the application's startup hook. Anything it raises
stops the process from coming up, so a bug here is not "the admin is missing" -
it is "the deployment is down", and it is down for a reason that looks like
nothing (a changed email address).

That is exactly what happened: the account was looked up by email while the
username was the hardcoded string "admin". An installation that already had a
user called "admin" - which every installation does, because that is the
username this function gives the first one - hit the unique index on `username`
and the application refused to boot. The fix and its regressions live here.

The tests run against the shared test database, which conftest has already
seeded with `admin@example.com` / username `admin`. That is not incidental: it
is the exact state an existing deployment is in when an operator changes
`ADMIN_EMAIL`, and it is what makes these tests meaningful.
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.db import session_scope
from app.main import seed_admin
from app.models import User, UserRole
from app.security import verify_password


@pytest.fixture
def new_operator(monkeypatch):
    """Point the seed at a second operator address, as a deploy would."""
    monkeypatch.setattr(settings, "admin_email", "second.operator@example.com")
    monkeypatch.setattr(settings, "admin_password", "SecondOperator!23")
    yield settings.admin_email


def test_the_configured_operator_can_be_provisioned_alongside_an_existing_one(new_operator):
    """Changing ADMIN_EMAIL must not make the application unable to boot.

    The old code raised `IntegrityError: UNIQUE constraint failed:
    users.username` out of the startup hook here, which took the whole
    deployment down.
    """
    seed_admin()

    with session_scope() as db:
        created = db.query(User).filter(User.email == new_operator).one()
        assert created.role is UserRole.admin
        # The obvious username was taken, so it was qualified rather than
        # colliding.
        assert created.username != "admin"
        assert created.username.startswith("admin")

        # The account that was already there is untouched, and still owns the
        # plain "admin" username.
        original = db.query(User).filter(User.username == "admin").one()
        assert original.role is UserRole.admin
        assert original.email == "admin@example.com"


def test_the_seeded_password_is_hashed_and_never_stored(new_operator, monkeypatch):
    """bcrypt, cost 12, and no trace of the plaintext anywhere on the row."""
    plaintext = "SecondOperator!23"
    monkeypatch.setattr(settings, "admin_password", plaintext)
    seed_admin()

    with session_scope() as db:
        created = db.query(User).filter(User.email == new_operator).one()

    assert created.password_hash.startswith("$2b$12$"), "not a bcrypt cost-12 hash"
    assert plaintext not in created.password_hash
    assert verify_password(plaintext, created.password_hash)
    assert not verify_password("wrong-password", created.password_hash)


def test_seeding_twice_does_not_create_a_second_account(new_operator):
    """Every cold start on a serverless platform calls this."""
    seed_admin()
    seed_admin()
    seed_admin()

    with session_scope() as db:
        matches = db.query(User).filter(User.email == new_operator).all()
    assert len(matches) == 1


def test_an_existing_account_is_not_overwritten(new_operator):
    """Re-running the seed must not reset a password an operator has changed.

    A deploy that silently reverted the admin password to whatever is in
    `ADMIN_PASSWORD` would hand the account back to anyone who can read the
    environment - and would do it every time the service restarted.
    """
    seed_admin()

    with session_scope() as db:
        created = db.query(User).filter(User.email == new_operator).one()
        changed = "$2b$12$" + "x" * 53
        created.password_hash = changed
        db.commit()

    seed_admin()

    with session_scope() as db:
        again = db.query(User).filter(User.email == new_operator).one()
    assert again.password_hash == changed, "the seed overwrote a rotated password"


def test_a_failure_to_seed_does_not_stop_the_boot(monkeypatch):
    """The last line of defence: a seed must never be fatal.

    Whatever is wrong with the environment - a malformed address, a database
    that rejects the insert - the process has to come up, because the operator
    needs a running server to diagnose it.
    """
    monkeypatch.setattr(settings, "admin_email", "not-an-address")
    monkeypatch.setattr(
        "app.main.hash_password",
        lambda *_: (_ for _ in ()).throw(RuntimeError("hashing unavailable")),
    )

    seed_admin()      # must not raise
