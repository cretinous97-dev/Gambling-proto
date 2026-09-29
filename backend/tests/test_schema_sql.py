"""The published PostgreSQL schema must match the models.

`backend/sql/schema.sql` is a deliverable: it is what an operator runs against
the database that holds the money. A generated file that is not regenerated is
worse than no file, because it is confidently wrong - so the first test here
compares it byte for byte with what the models produce right now.

The remaining tests assert the properties of that schema that money depends on:
integer minor units, and the unique constraints that make a retry or a replayed
callback a no-op instead of a second credit.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA = ROOT / "backend" / "sql" / "schema.sql"


@pytest.fixture(scope="module")
def ddl() -> str:
    assert SCHEMA.is_file(), (
        "backend/sql/schema.sql is missing - run `make schema-sql`"
    )
    return SCHEMA.read_text()


def test_the_published_schema_matches_the_models(ddl):
    import sys

    sys.path.insert(0, str(ROOT / "backend" / "scripts"))
    from generate_schema import render

    assert ddl == render(), (
        "schema.sql is out of date. Run `make schema-sql` and commit the result."
    )


@pytest.mark.parametrize(
    "table",
    ["users", "balances", "ledger_transactions", "ledger_entries",
     "deposits", "withdrawals", "refresh_tokens", "payment_webhooks",
     "session_audit", "audit_log", "kyc_documents"],
)
def test_the_required_tables_are_published(ddl, table):
    assert re.search(rf"CREATE TABLE {table} \(", ddl), f"{table} is missing from the schema"


def test_money_is_never_floating_point(ddl):
    """IEEE-754 cannot represent 0.10 exactly, and a ledger that cannot add up
    is not a ledger. Every money column is a BIGINT of minor units."""
    offenders = [
        line.strip()
        for line in ddl.splitlines()
        if re.search(r"(amount|balance|locked|fee|total|win|stake|payout)", line, re.I)
        and re.search(r"\b(REAL|DOUBLE|FLOAT|NUMERIC|DECIMAL)\b", line, re.I)
    ]
    assert not offenders, f"money stored as a float: {offenders}"


def test_the_ledger_cannot_double_post(ddl):
    """The unique keys are the mechanism that makes retries safe, so their
    absence is a duplicate-money bug waiting for a flaky network."""
    assert re.search(r"CREATE TABLE ledger_transactions.*?UNIQUE \(idempotency_key\)",
                     ddl, re.S), "ledger_transactions.idempotency_key is not unique"
    assert re.search(r"CREATE TABLE deposits.*?UNIQUE \(idempotency_key\)",
                     ddl, re.S), "deposits.idempotency_key is not unique"
    assert re.search(r"CREATE TABLE withdrawals.*?UNIQUE \(idempotency_key\)",
                     ddl, re.S), "withdrawals.idempotency_key is not unique"


def test_a_balance_row_is_unique_per_owner_and_account(ddl):
    assert re.search(
        r"CREATE TABLE balances.*?CONSTRAINT uq_balance_user_kind UNIQUE \(user_id, kind\)",
        ddl, re.S,
    ), "nothing stops two balance rows for the same account"


def test_the_payout_instrument_column_survived(ddl):
    """Payouts need a verified destination token, distinct from the masked
    string staff see. Losing it silently breaks payouts on upgrade."""
    assert "payout_ref" in ddl


def test_money_columns_are_indexed_where_they_are_queried(ddl):
    """Statements are queried by user and date; an unindexed ledger is a table
    scan on every page of history."""
    for index in ("ix_ledger_entries_user_kind", "ix_ledger_transactions_created_at"):
        assert f"CREATE INDEX {index}" in ddl, f"{index} is missing"


def test_the_published_schema_is_valid_postgresql(ddl):
    """Parse the file with PostgreSQL's own grammar.

    Everything else in this module checks the *content* of the schema: that it
    matches the models, that it has the right tables, that the money is
    integral and the keys unique. None of that notices a syntax error. The
    application never reads this file - `create_all` builds the tables from the
    models - so a typo here would sail through the whole suite and first
    surface when an operator ran it against the database that holds the money.

    pglast ships libpg_query, the parser PostgreSQL itself uses, so this is not
    a close approximation of Postgres syntax: it is the thing that will reject
    or accept the file on a real server.

    Skips rather than fails when pglast is absent, so a minimal environment is
    not blocked by a check it cannot run. It is pinned in
    ``backend/requirements.txt`` (development only - the deployed runtime has
    no use for a SQL parser), so `make install` enables it.
    """
    pglast = pytest.importorskip(
        "pglast", reason="pip install pglast to parse the schema as PostgreSQL"
    )

    try:
        statements = pglast.parse_sql(ddl)
    except Exception as exc:  # libpg_query raises on the first syntax error
        pytest.fail(f"backend/sql/schema.sql is not valid PostgreSQL: {exc}")

    kinds: dict[str, int] = {}
    for statement in statements:
        name = type(statement.stmt).__name__
        kinds[name] = kinds.get(name, 0) + 1

    # Guard against the parser quietly accepting a prefix of the file and
    # stopping: every table and enum written in the text must have produced a
    # node. If a statement were swallowed, the file would parse "clean" while
    # silently omitting tables.
    assert kinds.get("CreateStmt", 0) == len(re.findall(r"^CREATE TABLE ", ddl, re.M)), (
        "the parser did not see every CREATE TABLE - the schema may be truncated"
    )
    assert kinds.get("CreateEnumStmt", 0) == len(re.findall(r"^CREATE TYPE ", ddl, re.M))
    # `CREATE UNIQUE INDEX` counts too - four of the indexes in this schema
    # are unique, and they are the ones that stop a replayed callback from
    # crediting twice.
    assert kinds.get("IndexStmt", 0) == len(
        re.findall(r"^CREATE (?:UNIQUE )?INDEX ", ddl, re.M)
    )

    assert kinds["CreateStmt"] >= 20, "the schema lost tables"
