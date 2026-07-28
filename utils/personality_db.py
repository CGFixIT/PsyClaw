"""Database backend shim for PersonalityManager.

Returns ``(conn, placeholder, backend)`` where ``placeholder`` is '?' for SQLite
or '%s' for Postgres and ``backend`` is the string 'sqlite' or 'postgres' (used
to pick the right DDL via :func:`ddl_soul_versions` / :func:`ddl_interactions`).
Switch to Postgres by setting CYCLAW_DB_URL or personality.database_url in
config.yaml to a postgresql:// DSN. Default is SQLite (zero-config, local-first).

Security posture (Postgres path):
  * TLS is required by default — if the DSN omits ``sslmode`` we inject
    ``sslmode=require``. Override with ``CYCLAW_DB_SSLMODE`` (e.g. ``verify-full``
    for production with a CA, or ``disable`` for a trusted same-host CI service).
  * Sessions are bounded — ``connect_timeout`` plus a server-side
    ``statement_timeout`` so a hung query cannot pin the single shared connection.
  * ``application_name=cyclaw`` is set for auditability in ``pg_stat_activity``.
  * The DSN (which may carry credentials) is NEVER logged or echoed in errors.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# Connection hardening defaults (Postgres). statement_timeout mirrors the
# sqlconnect convention (agentic/sqlconnect/config.py: statement_timeout_ms=5000).
_PG_APP_NAME = "cyclaw"
_PG_CONNECT_TIMEOUT_S = 10
_PG_STATEMENT_TIMEOUT_MS = 5000
_PG_DEFAULT_SSLMODE = "require"


def _harden_pg_conninfo(dsn: str) -> str:
    """Return a hardened libpq conninfo string built from ``dsn``.

    Adds TLS (``sslmode``), an ``application_name``, a ``connect_timeout`` and a
    server-side ``statement_timeout`` *without* overriding anything the operator
    set explicitly. We parse + rebuild via ``psycopg.conninfo`` rather than string
    concatenation so an attacker-influenced DSN cannot smuggle extra keywords.
    """
    from psycopg.conninfo import conninfo_to_dict, make_conninfo  # type: ignore[import]

    parts = conninfo_to_dict(dsn)
    # TLS: default to require; operator may override via the DSN itself or
    # CYCLAW_DB_SSLMODE (CI's trusted local service sets this to "disable").
    if "sslmode" not in parts:
        parts["sslmode"] = os.environ.get("CYCLAW_DB_SSLMODE") or _PG_DEFAULT_SSLMODE
    parts.setdefault("application_name", _PG_APP_NAME)
    parts.setdefault("connect_timeout", str(_PG_CONNECT_TIMEOUT_S))
    # Bound every statement on this connection; preserve any operator-supplied
    # options by appending rather than replacing.
    timeout_opt = f"-c statement_timeout={_PG_STATEMENT_TIMEOUT_MS}"
    existing_opts = parts.get("options")
    parts["options"] = f"{existing_opts} {timeout_opt}".strip() if existing_opts else timeout_opt
    return make_conninfo(**parts)


def connect(db_path: Path, pers_cfg: dict) -> tuple[Any, str, str]:
    """Open a DB connection and return ``(conn, placeholder_char, backend_name)``.

    Postgres: set CYCLAW_DB_URL=postgresql://user:pass@host/dbname
    SQLite:   default, uses db_path resolved from config.

    ``conn`` is a ``sqlite3.Connection`` or a ``psycopg.Connection`` (typed
    ``Any`` so importing psycopg stays optional for SQLite-only installs).
    """
    dsn = os.environ.get("CYCLAW_DB_URL") or pers_cfg.get("database_url") or ""
    if dsn.startswith("postgresql") or dsn.startswith("postgres"):
        try:
            import psycopg  # type: ignore[import]
            from psycopg.rows import dict_row  # type: ignore[import]
        except ImportError as exc:
            # NB: never include the DSN in this message — it may carry credentials.
            raise ImportError(
                "psycopg is required for PostgreSQL support. "
                "Install it with: pip install 'cyclaw[postgres]'  (or pip install 'psycopg[binary]')"
            ) from exc
        conn = psycopg.connect(_harden_pg_conninfo(dsn), row_factory=dict_row, autocommit=False)
        return conn, "%s", "postgres"
    # Default: SQLite (offline-first, zero-config)
    import sqlite3
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # sqlite3.connect() creates the database with the process umask, which
    # commonly leaves the full soul/version history at 0644. Pre-create it
    # owner-only and also repair permissions on databases from older installs.
    fd = os.open(db_path, os.O_RDWR | os.O_CREAT, 0o600)
    os.close(fd)
    os.chmod(db_path, 0o600)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn, "?", "sqlite"


def ddl_soul_versions(backend: str) -> str:
    if backend == "postgres":
        return """
            CREATE TABLE IF NOT EXISTS soul_versions (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                sha256 TEXT NOT NULL,
                content TEXT NOT NULL,
                reason TEXT,
                timestamp TEXT NOT NULL
            )
        """
    return """
        CREATE TABLE IF NOT EXISTS soul_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sha256 TEXT NOT NULL,
            content TEXT NOT NULL,
            reason TEXT,
            timestamp TEXT NOT NULL
        )
    """


def ddl_interactions(backend: str) -> str:
    if backend == "postgres":
        return """
            CREATE TABLE IF NOT EXISTS interactions (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                query_hash TEXT NOT NULL,
                outcome TEXT,
                timestamp TEXT NOT NULL
            )
        """
    return """
        CREATE TABLE IF NOT EXISTS interactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query_hash TEXT NOT NULL,
            outcome TEXT,
            timestamp TEXT NOT NULL
        )
    """


def ddl_indexes(backend: str) -> list[str]:
    """Index DDL applied after the tables exist (same syntax on both backends).

    The TTL prune (``DELETE FROM interactions WHERE timestamp < ?``) and the
    maintenance sweep both range-scan ``interactions.timestamp``; an index keeps
    that O(log n) instead of a full-table scan as history grows. ``CREATE INDEX
    IF NOT EXISTS`` is valid on both SQLite and PostgreSQL, so one list serves
    both. ``backend`` is accepted for symmetry / future backend-specific indexes.
    """
    return [
        "CREATE INDEX IF NOT EXISTS idx_interactions_ts ON interactions(timestamp)",
    ]
