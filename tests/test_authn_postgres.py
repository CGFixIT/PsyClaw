"""Live Postgres-backend tests for utils.authn_store and AuthManager.

These exercise the psycopg connect/execute paths that the default suite never
opens: ``information_schema`` column discovery, ``ALTER TABLE`` role migration,
the partial unique live-token index, connection hardening, and a full
AuthManager lifecycle through ``%s``-templated SQL.

SKIPPED unless CYCLAW_DB_URL (or CYCLAW_AUTH_DB_URL) points at a reachable
Postgres, so the default offline suite stays green with zero extra deps. The
``postgres-backend`` CI job runs them for real.

The DSN is passed via ``auth.database_url`` (config takes precedence over
CYCLAW_AUTH_DB_URL). That is the path with no live coverage today; it also
means this file does not need a new workflow env key.

``psycopg`` is imported only inside fixtures/tests so collection stays clean
when the driver is absent. Cleanup uses a *separate autocommit* connection:
``authn_store.connect`` opens with ``autocommit=False`` and AuthManager does
not always roll back.

The interleaved last-admin TOCTOU case is issue #997, not this file.
"""

import os
from pathlib import Path

import pytest

from utils.authn_manager import AuthManager
from utils.authn_store import (
    connect,
    ddl_device_tokens,
    ddl_indexes,
    ddl_sessions,
    ddl_users,
    ensure_users_role_column,
    users_column_names,
)

_GOOD = "correct horse battery staple"

DSN = os.environ.get("CYCLAW_DB_URL") or os.environ.get("CYCLAW_AUTH_DB_URL")
pytestmark = pytest.mark.skipif(
    not (DSN and DSN.startswith("postgres")),
    reason="no Postgres DSN; skipping live Postgres authn tests",
)


def _auth_cfg(tmp_path: Path) -> dict:
    return {
        "auth": {
            "enabled": True,
            "db_path": str(tmp_path / "unused.db"),
            "database_url": DSN,
        }
    }


@pytest.fixture
def clean_auth_db():
    """Drop auth tables before and after each test. Separate autocommit conn."""
    import psycopg

    from utils.personality_db import _harden_pg_conninfo

    def _drop() -> None:
        with psycopg.connect(_harden_pg_conninfo(DSN), autocommit=True) as conn:
            conn.execute("DROP TABLE IF EXISTS device_tokens CASCADE")
            conn.execute("DROP TABLE IF EXISTS sessions CASCADE")
            conn.execute("DROP TABLE IF EXISTS users CASCADE")

    _drop()
    yield
    _drop()


def _raw_connect(tmp_path: Path):
    return connect(tmp_path / "unused.db", {"database_url": DSN})


def test_pg_connect_contract(clean_auth_db, tmp_path):
    """Backend name, %s placeholder, and inherited connection hardening."""
    conn, placeholder, backend = _raw_connect(tmp_path)
    try:
        assert backend == "postgres"
        assert placeholder == "%s"
        app_name = conn.execute(
            "SELECT current_setting('application_name') AS application_name"
        ).fetchone()["application_name"]
        assert app_name == "cyclaw"
        timeout = conn.execute("SHOW statement_timeout").fetchone()["statement_timeout"]
        assert timeout in ("5000ms", "5s")
    finally:
        conn.close()


def test_pg_ddl_is_idempotent(clean_auth_db, tmp_path):
    conn, _, backend = _raw_connect(tmp_path)
    try:
        assert backend == "postgres"
        for _ in range(2):
            conn.execute(ddl_users())
            conn.execute(ddl_sessions())
            conn.execute(ddl_device_tokens())
            for stmt in ddl_indexes():
                conn.execute(stmt)
        conn.commit()
        tables = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
        ).fetchall()
        names = {row["tablename"] for row in tables}
        assert {"users", "sessions", "device_tokens"} <= names
    finally:
        conn.close()


def test_pg_users_column_names_and_role_alter(clean_auth_db, tmp_path):
    """information_schema branch + ALTER TABLE ADD COLUMN role (pre-Stage-6)."""
    conn, _, backend = _raw_connect(tmp_path)
    try:
        conn.execute(
            """
            CREATE TABLE users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                created_ts REAL NOT NULL,
                disabled INTEGER NOT NULL DEFAULT 0,
                last_login_ts REAL,
                failed_count INTEGER NOT NULL DEFAULT 0,
                locked_until_ts REAL
            )
            """
        )
        conn.execute(
            "INSERT INTO users (username, password_hash, created_ts, disabled, failed_count) "
            "VALUES (%s, %s, %s, %s, %s)",
            ("admin", "placeholder", 1.0, 0, 0),
        )
        conn.commit()
        names = {n.lower() for n in users_column_names(conn, backend)}
        assert "username" in names
        assert "role" not in names

        ensure_users_role_column(conn, backend)
        conn.commit()
        names = {n.lower() for n in users_column_names(conn, backend)}
        assert "role" in names
        row = conn.execute(
            "SELECT role FROM users WHERE username = %s", ("admin",)
        ).fetchone()
        assert row["role"] == "operator"

        ensure_users_role_column(conn, backend)
        conn.commit()
    finally:
        conn.close()


def test_pg_partial_unique_live_token_label(clean_auth_db, tmp_path):
    """Two live (username, label) rows are unrepresentable; revoked labels reuse."""
    from psycopg.errors import UniqueViolation

    conn, _, backend = _raw_connect(tmp_path)
    try:
        assert backend == "postgres"
        conn.execute(ddl_users())
        conn.execute(ddl_sessions())
        conn.execute(ddl_device_tokens())
        for stmt in ddl_indexes():
            conn.execute(stmt)
        conn.commit()
        idx = conn.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'device_tokens' "
            "AND indexname = 'idx_device_tokens_live_label'"
        ).fetchone()
        assert idx is not None

        conn.execute(
            "INSERT INTO device_tokens (token_hash, username, label, created_ts, last_used_ts, revoked) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            ("hash-live-1", "alice", "laptop", 1.0, None, 0),
        )
        conn.commit()
        with pytest.raises(UniqueViolation):
            conn.execute(
                "INSERT INTO device_tokens (token_hash, username, label, created_ts, last_used_ts, revoked) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                ("hash-live-2", "alice", "laptop", 2.0, None, 0),
            )
        conn.rollback()

        conn.execute(
            "UPDATE device_tokens SET revoked = 1 WHERE token_hash = %s",
            ("hash-live-1",),
        )
        conn.execute(
            "INSERT INTO device_tokens (token_hash, username, label, created_ts, last_used_ts, revoked) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            ("hash-live-2", "alice", "laptop", 3.0, None, 0),
        )
        conn.commit()
        live = conn.execute(
            "SELECT COUNT(*) AS n FROM device_tokens WHERE username = %s AND label = %s AND revoked = 0",
            ("alice", "laptop"),
        ).fetchone()
        assert int(live["n"]) == 1
    finally:
        conn.close()


def test_pg_auth_manager_lifecycle(clean_auth_db, tmp_path):
    """bootstrap → create → login → validate → logout → device-token CRUD."""
    manager = AuthManager(_auth_cfg(tmp_path))
    try:
        assert manager.backend == "postgres"
        assert manager._ph == "%s"
        assert manager.bootstrap_if_empty() is True
        assert manager.bootstrap_if_empty() is False

        canonical = manager.create_user("alice", _GOOD)
        assert canonical == "alice"
        result = manager.login("alice", _GOOD)
        assert result.username == "alice"
        info = manager.validate_session(result.session_id)
        assert info is not None
        assert info.username == "alice"
        assert manager.logout(result.session_id) is True
        assert manager.validate_session(result.session_id) is None

        token = manager.create_device_token("alice", "laptop")
        assert manager.verify_device_token(token) == "alice"
        assert manager.revoke_device_token("alice", "laptop") is True
        assert manager.verify_device_token(token) is None
    finally:
        manager.close()
