"""Preflight for the integration suite.

Every test in this directory talks to a real PostgreSQL. Without one they do not fail
fast: each of them spends up to `connect_timeout` inside `psycopg` before raising
`ConnectionTimeout`, so a full local run turned into 147 errors and ~4 minutes of
waiting — a slow way to learn "there is no database".

So the check happens once per session, on a plain TCP connect (no driver import, no
credentials needed), and the whole directory is skipped with the command that fixes it.

Environment variables honoured, in order:
  TEST_DATABASE_URL      — full DSN, including the driver part
  POSTGRES_SYNC_URI      — what the application itself uses
  POSTGRES_HOST/PORT     — host and port only, default 127.0.0.1:5432
"""

from __future__ import annotations

import os
import socket
from urllib.parse import urlparse

import pytest

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5432
CONNECT_TIMEOUT_SECONDS = 2.0


def _target() -> tuple[str, int, str]:
    dsn = os.getenv("TEST_DATABASE_URL") or os.getenv("POSTGRES_SYNC_URI") or ""
    if dsn:
        parsed = urlparse(dsn.replace("postgresql+psycopg://", "postgresql://"))
        if parsed.hostname:
            return parsed.hostname, parsed.port or DEFAULT_PORT, dsn
    host = os.getenv("POSTGRES_HOST", DEFAULT_HOST)
    port = int(os.getenv("POSTGRES_PORT", DEFAULT_PORT))
    return host, port, f"{host}:{port}"


def _probe() -> tuple[bool, str]:
    host, port, shown = _target()
    try:
        with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT_SECONDS):
            return True, shown
    except OSError as exc:
        return False, f"{shown} — {exc.strerror or exc}"


@pytest.fixture(scope="session", autouse=True)
def require_postgres() -> None:
    """Skip the whole directory when PostgreSQL is not reachable."""
    reachable, detail = _probe()
    if not reachable:
        pytest.skip(
            "PostgreSQL недоступен (" + detail + "). "
            "Поднять: docker compose up -d postgres  ·  "
            "или задать TEST_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/db"
        )
