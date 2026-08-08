"""Database backend for per-user authentication (docs/AUTHENTICATION_DESIGN.md).

Stores ``users``, ``sessions`` and ``device_tokens``. Mirrors
``utils/personality_db.py``'s ``connect()`` pattern deliberately: same SQLite
0600-hardening, same hardened Postgres conninfo (TLS, timeouts, application
name), same "no new storage technology" constraint.

One deliberate deviation from that mirror: Postgres opt-in uses its OWN env
var (``CYCLAW_AUTH_DB_URL``), not the shared ``CYCLAW_DB_URL`` personality
uses. ``gate.py``'s rate limiter already established this precedent
(``RATE_LIMIT_DB_URL`` does not fall back to ``CYCLAW_DB_URL`` either) for
the same reason it applies more sharply here: auth data (password hashes,
session ids, device-token hashes) is higher-sensitivity than personality
data, and a shared env var would silently comingle the two into whatever
database an operator pointed ``CYCLAW_DB_URL`` at for a different subsystem.
Each subsystem's Postgres backend is opted into independently.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

# _harden_pg_conninfo is intentionally reused rather than re-implemented: the
# hardening rules (TLS default, connect_timeout, statement_timeout,
# application_name, never-log-the-DSN) apply identically to every Postgres
# connection this codebase opens, and duplicating them would let the two
# copies drift.
from utils.personality_db import _harden_pg_conninfo

_AUTH_DB_ENV = "CYCLAW_AUTH_DB_URL"


def connect(db_path: Path, auth_cfg: dict) -> tuple[Any, str, str]:
    """Open a DB connection and return ``(conn, placeholder_char, backend_name)``.

    Postgres: set ``auth.database_url`` in config.yaml or the
    ``CYCLAW_AUTH_DB_URL`` env var to a ``postgresql://`` DSN.
    SQLite: default, uses ``db_path``.
    """
    dsn = auth_cfg.get("database_url") or os.environ.get(_AUTH_DB_ENV) or ""
    if dsn.startswith("postgresql") or dsn.startswith("postgres"):
        try:
            import psycopg  # type: ignore[import]
            from psycopg.rows import dict_row  # type: ignore[import]
        except ImportError as exc:
            # NB: never include the DSN in this message -- it may carry credentials.
            raise ImportError(
                "psycopg is required for PostgreSQL support. "
                "Install it with: pip install 'cyclaw[postgres]'  (or pip install 'psycopg[binary]')"
            ) from exc
        conn = psycopg.connect(_harden_pg_conninfo(dsn), row_factory=dict_row, autocommit=False)
        return conn, "%s", "postgres"
    # Default: SQLite (offline-first, zero-config)
    import sqlite3
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Pre-create the file owner-only via os.open()'s mode, same reasoning as
    # personality_db.connect: sqlite3.connect() alone would create it at the
    # process umask (commonly 0644), exposing password hashes and session ids
    # to every local account on a shared machine.
    db_existed = db_path.exists()
    fd = os.open(db_path, os.O_RDWR | os.O_CREAT, 0o600)
    os.close(fd)
    if db_existed:
        try:
            if stat.S_IMODE(db_path.stat().st_mode) != 0o600:
                os.chmod(db_path, 0o600)
        except OSError:
            # Never let a permission failure here crash startup -- a
            # root-installed service running as a lower-privileged user, or an
            # ops team that deliberately shipped it read-only, would otherwise
            # turn every connect() into a boot crash instead of a read. The
            # logger import is local: this module must stay importable before
            # logging is configured (auth_manager.py calls connect() during
            # its own __init__, which can run before setup_logging()).
            import logging
            logging.getLogger("cyclaw.authn_store").warning(
                "Could not harden auth DB permissions to 0600: %s", db_path
            )
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn, "?", "sqlite"


def ddl_users() -> str:
    return """
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            created_ts REAL NOT NULL,
            disabled INTEGER NOT NULL DEFAULT 0,
            last_login_ts REAL,
            failed_count INTEGER NOT NULL DEFAULT 0,
            locked_until_ts REAL
        )
    """


def ddl_sessions() -> str:
    return """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            csrf_token TEXT NOT NULL,
            created_ts REAL NOT NULL,
            last_seen_ts REAL NOT NULL,
            expires_ts REAL NOT NULL,
            revoked INTEGER NOT NULL DEFAULT 0
        )
    """


def ddl_device_tokens() -> str:
    return """
        CREATE TABLE IF NOT EXISTS device_tokens (
            token_hash TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            label TEXT NOT NULL,
            created_ts REAL NOT NULL,
            last_used_ts REAL,
            revoked INTEGER NOT NULL DEFAULT 0
        )
    """


def ddl_indexes() -> list[str]:
    """Index DDL applied after the tables exist. Valid on both backends."""
    return [
        "CREATE INDEX IF NOT EXISTS idx_sessions_username ON sessions(username)",
        "CREATE INDEX IF NOT EXISTS idx_device_tokens_username ON device_tokens(username)",
    ]
