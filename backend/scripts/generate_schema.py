"""Write backend/sql/schema.sql - the PostgreSQL schema, from the models.

Why generate rather than hand-write it: a hand-maintained schema file drifts
from the models within a release or two, and the drift is only discovered by
whoever runs it against a real database. Generating it means the file is always
what the application actually expects, and the test suite fails if somebody
edits a model without regenerating.

    python backend/scripts/generate_schema.py          # write
    python backend/scripts/generate_schema.py --check   # fail if stale

The output is PostgreSQL-specific on purpose: `create_all` handles SQLite
locally, and this file is what you run on the database that holds the money.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import create_mock_engine  # noqa: E402

from app import models  # noqa: E402,F401  (registers every table on Base.metadata)
from app.db import Base  # noqa: E402

OUT = ROOT / "backend" / "sql" / "schema.sql"

HEADER = """\
-- {app} - PostgreSQL schema
--
-- GENERATED FILE - do not edit by hand.
--   regenerate:  make schema-sql
--   verify:      make test   (a test fails if this file is out of date)
--
-- The application creates these tables itself on first boot (`create_all`),
-- which is convenient for development and is NOT how you should manage the
-- schema of a database holding player money. On a real deployment:
--
--   1. create the database and a role that owns it
--   2. run this file once, or let Alembic manage the same shape going forward
--   3. never let the application role own the schema in production
--
-- MONEY MODEL (read this before changing any amount column)
--
-- Money is stored as BIGINT *minor units* - integer cents, integer ngultrum,
-- integer yen. Never floating point, and deliberately not NUMERIC either:
--
--   * every card scheme and wallet API in existence (Adyen, Stripe, mBoB)
--     takes and returns integer minor units, so integer storage means an amount
--     is never re-rounded on the way to the processor;
--   * the ledger invariant is that `ledger_entries` sums to exactly zero per
--     transaction. With integers that is an exact equality; with a decimal
--     type it is a comparison you have to get the scale right on, in every
--     query, forever;
--   * addition and subtraction of integers cannot lose a unit. There is no
--     rounding mode to argue about, because there is no rounding.
--
-- For reporting, BI and anything that would rather read a decimal, the
-- NUMERIC views at the end of this file (`wallets`, `transactions`) divide by
-- 100 with NUMERIC arithmetic, which is exact. They are read-only: nothing
-- writes through them. So the storage stays exact and integer, and every
-- decimal figure an analyst or an auditor sees is exact too.
--
-- Naming: the tables an operator asked for are all here. Users is `users`;
-- Wallets is `balances` (the live balance, a cache of the ledger) backed by
-- `ledger_entries` (the record); Transactions is `ledger_transactions` plus
-- `ledger_entries`; and the custom banks added in the admin panel are
-- `banking_methods`. The `wallets` and `transactions` views expose the first
-- three under those names.
"""


def ddl_statements() -> list[str]:
    """Compile every table/index/type to PostgreSQL DDL.

    Uses a mock engine so nothing needs to be connected: the SQL is compiled by
    the same dialect SQLAlchemy would use against a real PostgreSQL server.
    """
    from sqlalchemy.dialects import postgresql

    statements: list[str] = []

    def collect(sql, *multiparams, **params):
        text = str(sql.compile(dialect=postgresql.dialect())).strip()
        if text and not text.startswith("--"):
            statements.append(text.rstrip(";") + ";")

    mock = create_mock_engine("postgresql+psycopg://", collect)
    Base.metadata.create_all(mock, checkfirst=False)
    return statements


def ordered_statements() -> list[str]:
    """Deterministic order: types, then tables, then indexes, each sorted.

    SQLAlchemy walks the metadata in a set, and Python's string hashing is
    randomised per process, so the same models produce the same statements in a
    different order on every run. Sorting makes the file stable, which is the
    only thing that lets a test tell "somebody changed a model" apart from
    "somebody ran the generator twice".
    """
    def rank(statement: str) -> tuple[int, str]:
        if statement.startswith("CREATE TYPE"):
            return (0, statement)
        if statement.startswith("CREATE TABLE"):
            return (1, statement)
        if statement.startswith("CREATE INDEX") or statement.startswith("CREATE UNIQUE INDEX"):
            return (3, statement)
        if statement.startswith("CREATE VIEW"):
            return (4, statement)
        return (2, statement)

    return sorted(ddl_statements(), key=rank)


def reporting_views(settlement_currency: str) -> list[str]:
    """Read-only decimal views over the integer ledger.

    Every money column here is NUMERIC, computed by NUMERIC division, so the
    figures are exact rather than approximated. They are views and not tables
    on purpose: a materialised decimal copy of a balance is a second source of
    truth, and the whole point of this schema is that there is only one.

    `wallets` gives one row per (owner, account kind) - the same grain as
    `balances`, which is what "real-time balance tracking" means here: the row
    is updated in the same transaction as the ledger entries that justify it,
    and `wallets` shows it as a decimal.

    `transactions` is the flat posting list: one row per ledger entry, joined
    to the transaction that grouped it, so a statement can be produced with a
    single indexed query and no application-side arithmetic. Nothing in it can
    be updated or deleted through the view, which is what "non-destructive
    ledger" has to mean to survive an audit.
    """
    return [
        f"""CREATE VIEW wallets AS
SELECT
    b.id                                                    AS wallet_id,
    b.user_id,
    b.kind                                                  AS account_kind,
    (b.amount::numeric(20, 2) / 100)                        AS balance,
    (b.locked::numeric(20, 2) / 100)                        AS locked,
    ((b.amount - b.locked)::numeric(20, 2) / 100)           AS available,
    '{settlement_currency}'                                 AS settlement_currency,
    b.updated_at
FROM balances b;""",
        """CREATE VIEW transactions AS
SELECT
    t.id                                        AS transaction_id,
    t.type                                      AS transaction_type,
    t.status,
    t.user_id,
    t.reference,
    t.memo,
    e.kind                                      AS account_kind,
    (e.amount::numeric(20, 2) / 100)            AS amount,
    (e.balance_after::numeric(20, 2) / 100)     AS balance_after,
    t.created_at
FROM ledger_transactions t
JOIN ledger_entries e ON e.transaction_id = t.id;""",
    ]


def render() -> str:
    from app.config import settings

    statements = ordered_statements() + reporting_views(settings.settlement_currency)
    body = "\n\n".join(statements)
    return HEADER.format(app=settings.app_name) + "\n" + body + "\n"


def main(argv: list[str]) -> int:
    rendered = render()
    if "--check" in argv:
        current = OUT.read_text() if OUT.is_file() else ""
        if current != rendered:
            print(
                "backend/sql/schema.sql is out of date.\n"
                "Run `make schema-sql` (or backend/scripts/generate_schema.py) and commit it.",
                file=sys.stderr,
            )
            return 1
        print("schema.sql is up to date")
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(rendered)
    tables = Base.metadata.tables
    print(f"wrote {OUT.relative_to(ROOT)} - {len(tables)} tables, {rendered.count('CREATE INDEX')} indexes")
    for name in sorted(tables):
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
