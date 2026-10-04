"""Prove the Alembic history against a live database instead of asserting it.

`alembic upgrade head` succeeding only proves the scripts parse. This script takes the
database all the way down and back up, then compares what it finds with what the ORM
declares:

1. drop anything a previous run left behind (`downgrade base`),
2. `upgrade head` and compare the resulting schema against `Base.metadata`,
3. confirm `citext` is really the column type and not merely `USER-DEFINED`,
4. `downgrade base` again — a migration that cannot be reversed is a broken migration.

The downgrade at the end is why this is a script and not a test: it destroys the
application tables of whatever `POSTGRES_SYNC_URI` points at. Never point it at a
database you care about.

Usage:
    python scripts/verify_migrations.py
"""

from __future__ import annotations

import sys

import psycopg
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app import models  # noqa: F401  registers every table on Base.metadata
from app.core.config import get_settings
from app.db.base import Base

settings = get_settings()
url = settings.postgres_sync_uri
# psycopg wants a plain libpq URL; SQLAlchemy's dialect prefix has to come off.
psycopg_url = url.replace("postgresql+psycopg://", "postgresql://")

#: Alembic keeps its own bookkeeping table. It is not part of the application schema, so
#: counting it as a stray table makes every clean run look like a failure — which is
#: exactly what happened the first time this script ran.
BOOKKEEPING = {"alembic_version"}


def app_tables(state: dict[str, list[str]]) -> list[str]:
    return [table for table in state["tables"] if table not in BOOKKEEPING]


def db_state() -> dict[str, list[str]]:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            inspector = inspect(conn)
            tables = sorted(inspector.get_table_names())
            return {
                "tables": tables,
                "indexes": sorted(
                    index["name"] for table in tables for index in inspector.get_indexes(table)
                ),
            }
    finally:
        engine.dispose()


def extension_present(name: str) -> bool:
    with psycopg.connect(psycopg_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_extension WHERE extname = %s", (name,))
        return cur.fetchone() is not None


def column_udt(table: str, column: str) -> str:
    """The real type of a column, read from the catalog.

    `information_schema.columns.data_type` reports `USER-DEFINED` for every extension
    type, so it cannot tell `citext` from any other — checking it that way reported a
    false failure on a column that was correct all along.
    """
    with psycopg.connect(psycopg_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT t.typname FROM pg_attribute a "
            "JOIN pg_class c ON c.oid = a.attrelid "
            "JOIN pg_type t ON t.oid = a.atttypid "
            "WHERE c.relname = %s AND a.attname = %s",
            (table, column),
        )
        row = cur.fetchone()
    return row[0] if row else "<missing>"


def main() -> int:
    checks: list[tuple[str, str]] = []  # (label, ok)
    failures: list[str] = []

    def record(label: str, ok: bool, detail: str = "") -> None:
        checks.append((label, "ok" if ok else "FAILED"))
        print(f"   [{'ok' if ok else 'FAIL'}] {label}{f' — {detail}' if detail else ''}")
        if not ok:
            failures.append(f"{label}{f': {detail}' if detail else ''}")

    config = Config("alembic.ini")
    print(f"DSN: {settings.dsn_for(url)}\n")

    print("1. clean slate")
    leftover = app_tables(db_state())
    if leftover:
        print(f"   {len(leftover)} table(s) left from a previous run, rolling back")
        command.downgrade(config, "base")
    print(f"   application tables before migrating: {len(app_tables(db_state()))}")

    print("\n2. alembic upgrade head")
    command.upgrade(config, "head")
    migrated = db_state()
    created = app_tables(migrated)
    expected = sorted(Base.metadata.tables)
    print(f"   tables created: {len(created)} (models declare {len(expected)})")
    print(f"   indexes created: {len(migrated['indexes'])}")

    record("no model table is missing", not [t for t in expected if t not in created])
    record("no stray table was created", not [t for t in created if t not in expected])
    record("citext extension installed", extension_present("citext"))

    email_udt = column_udt("users", "email")
    record("users.email is citext", email_udt == "citext", f"udt_name={email_udt}")

    print("\n3. alembic downgrade base")
    command.downgrade(config, "base")
    final = db_state()
    remaining = app_tables(final)
    print(f"   application tables left: {len(remaining)}")
    print(f"   alembic_version still present: {bool(set(final['tables']) & BOOKKEEPING)}")
    record("downgrade removed every table", not remaining, ", ".join(remaining))

    print("\nresult:", "everything matches" if not failures else f"{len(failures)} failure(s)")
    for label, status in checks:
        print(f"   {status:<6} {label}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
