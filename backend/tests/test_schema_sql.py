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
