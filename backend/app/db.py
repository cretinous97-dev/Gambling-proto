"""SQLAlchemy engine / session wiring.

SQLite by default so the whole thing runs with zero setup. Point DATABASE_URL
at Postgres for anything real (see .env.example) - the models are portable.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import DateTime, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from .config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    pool_pre_ping=True,
    future=True,
)

if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _rec):  # pragma: no cover - driver glue
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetime that survives SQLite.

    SQLite has no native timestamp type and drops tzinfo on the way back, which
    silently turns every `stored_dt < utcnow()` comparison into a TypeError
    (or, worse on some drivers, a *wrong* answer). This decorator normalises on
    write and re-attaches UTC on read, so all application code can assume
    aware datetimes forever.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        # Tolerate plain `date` values (date_of_birth) written into a
        # timestamp column without forcing every caller to widen them.
        if not isinstance(value, datetime):
            value = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(timezone.utc)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    """FastAPI dependency. Commits on clean exit, rolls back on exception."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """For background jobs / scripts."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


#: Additive column patches, applied on boot.
#:
#: `create_all` creates missing TABLES and never touches an existing one, so a
#: model that gains a column would work on a fresh database and fail on every
#: deployment that already has data. That is the worst kind of bug: it only
#: appears in production, on the instance that matters.
#:
#: Each entry is (table, column, SQL type). They are additive and nullable by
#: design - a patch must never rewrite or drop data, because it runs
#: automatically and unattended.
#:
#: This is deliberately the smallest thing that can work. It is not a migration
#: framework: it cannot rename, backfill or re-type. The moment a schema change
#: needs any of those, adopt Alembic - backend/sql/schema.sql is the current
#: shape, and README "Database changes" describes the switch.
COLUMN_PATCHES: tuple[tuple[str, str, str], ...] = (
    ("withdrawals", "payout_ref", "VARCHAR(191)"),
    ("deposits", "banking_method_id", "VARCHAR(32)"),
    ("withdrawals", "banking_method_id", "VARCHAR(32)"),
    ("users", "locale", "VARCHAR(16)"),
    ("users", "language", "VARCHAR(8)"),
)


def _ensure_columns() -> list[str]:
    """Add any missing patched column. Safe to run on every boot."""
    from sqlalchemy import inspect, text

    applied: list[str] = []
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    for table, column, sql_type in COLUMN_PATCHES:
        if table not in existing_tables:
            continue
        columns = {c["name"] for c in inspector.get_columns(table)}
        if column in columns:
            continue
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))
        applied.append(f"{table}.{column}")
    return applied


def init_db() -> None:
    from . import models  # noqa: F401  (register mappers)

    Base.metadata.create_all(bind=engine)
    applied = _ensure_columns()
    if applied:
        import logging

        logging.getLogger("app.db").info("schema patches applied: %s", ", ".join(applied))
