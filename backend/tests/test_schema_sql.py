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


#: A column name that means "this holds money". Matched case-insensitively.
MONEY_COLUMN = re.compile(r"(amount|balance|locked|fee|total|win|stake|payout)", re.I)

#: Types that cannot hold money exactly at scale. NUMERIC and DECIMAL are exact,
#: but they are still the wrong choice for *storage* in this schema - see below.
INEXACT_TYPE = re.compile(r"\b(REAL|DOUBLE|FLOAT)\b", re.I)
DECIMAL_TYPE = re.compile(r"\b(NUMERIC|DECIMAL)\b", re.I)


def _table_bodies(ddl: str) -> list[str]:
    """Each `CREATE TABLE ... ;` block as one string."""
    return re.findall(r"CREATE TABLE .*?\);", ddl, re.S)


def test_money_is_never_floating_point(ddl):
    """IEEE-754 cannot represent 0.10 exactly, and a ledger that cannot add up
    is not a ledger.

    Two rules, and the difference between them is the point:

      * REAL / DOUBLE / FLOAT is refused *everywhere*, including in the
        reporting views. No decimal money figure is ever an approximation.
      * NUMERIC / DECIMAL is refused in `CREATE TABLE` bodies, because storage
        is integer minor units: the PSP APIs this integrates with speak integer
        minor units, and the ledger invariant ("every transaction sums to
        exactly zero") is an exact integer comparison.

    The views in this file are the documented exception and use NUMERIC
    deliberately - they are read-only projections computed by exact NUMERIC
    division, which is why they are checked against the first rule only.
    """
    everywhere = [
        line.strip()
        for line in ddl.splitlines()
        if MONEY_COLUMN.search(line) and INEXACT_TYPE.search(line)
    ]
    assert not everywhere, f"money in an inexact type: {everywhere}"

    in_tables = [
        line.strip()
        for body in _table_bodies(ddl)
        for line in body.splitlines()
        if MONEY_COLUMN.search(line) and DECIMAL_TYPE.search(line)
    ]
    assert not in_tables, (
        f"money stored as decimal in a table: {in_tables}. Storage is BIGINT "
        f"minor units; decimals belong in the reporting views."
    )


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


def test_the_reporting_views_are_published(ddl):
    """`wallets` and `transactions` are the decimal-facing names for the ledger.

    They are what an analyst, a BI tool or an auditor reads. They must be
    views - a materialised decimal copy of a balance would be a second source
    of truth, and this schema is built on there being exactly one.
    """
    assert "CREATE VIEW wallets AS" in ddl, "the wallets view is missing"
    assert "CREATE VIEW transactions AS" in ddl, "the transactions view is missing"
    assert "CREATE TABLE wallets" not in ddl, (
        "wallets must be a view over the ledger, not a second copy of the "
        "balance that can drift from it"
    )


def test_the_wallets_view_is_exact_numeric(ddl):
    """The view's arithmetic must be NUMERIC, not float, and must not round."""
    body = ddl.split("CREATE VIEW wallets AS", 1)[1].split(";", 1)[0]
    for column in ("balance", "locked", "available"):
        assert f"AS {column}" in body, f"wallets.{column} is missing"
    assert "::numeric(20, 2) / 100" in body, (
        "the wallets view must derive decimals by exact NUMERIC division"
    )
    assert not INEXACT_TYPE.search(body), "the wallets view uses a float type"


def test_the_banking_methods_table_is_published(ddl):
    """The admin panel's pathway registry has to exist in the real schema."""
    assert re.search(r"CREATE TABLE banking_methods \(", ddl)
    assert "uq_banking_method" in ddl, "nothing stops duplicate pathways"


def test_banking_method_credentials_are_never_a_column(ddl):
    """Only the *name* of an environment variable may be stored.

    A `credential`, `api_key` or `secret` column on this table would put a live
    key in every database export, support screenshot and staging copy. The
    column is `credential_env`, and it holds an identifier like MBOB_API_KEY.
    """
    body = ddl.split("CREATE TABLE banking_methods", 1)[1].split(");", 1)[0]
    assert "credential_env" in body, "credential_env is how a rail names its key"
    for forbidden in ("api_key", "secret", "password", "credential "):
        assert forbidden not in body.lower(), (
            f"banking_methods must not store a secret (found {forbidden!r}) - "
            f"keys belong in the environment"
        )
