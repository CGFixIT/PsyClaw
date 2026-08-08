"""Tests for utils/authn_store.py -- the SQLite/Postgres backend for auth data.

Mirrors utils/personality_db.py's own connect() contract, so these tests
mirror tests/test_personality.py's permission-hardening checks (0o600
creation, hardening an existing looser-permission file) rather than
inventing a new convention.
"""

from __future__ import annotations

import os
import sqlite3
from unittest.mock import patch

import pytest

from utils.authn_store import connect, ddl_device_tokens, ddl_indexes, ddl_sessions, ddl_users


class TestSqliteConnect:
    def test_creates_the_db_file(self, tmp_path):
        db_path = tmp_path / "auth.db"
        conn, placeholder, backend = connect(db_path, {})
        try:
            assert backend == "sqlite"
            assert placeholder == "?"
            assert db_path.exists()
        finally:
            conn.close()

    def test_creates_parent_directories(self, tmp_path):
        db_path = tmp_path / "nested" / "deeper" / "auth.db"
        conn, _, _ = connect(db_path, {})
        conn.close()
        assert db_path.exists()

    @pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits do not apply on Windows")
    def test_new_db_file_is_owner_only(self, tmp_path):
        db_path = tmp_path / "auth.db"
        previous_umask = os.umask(0o022)
        try:
            conn, _, _ = connect(db_path, {})
            conn.close()
        finally:
            os.umask(previous_umask)
        assert db_path.stat().st_mode & 0o777 == 0o600

    @pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits do not apply on Windows")
    def test_existing_looser_permission_db_is_hardened(self, tmp_path):
        db_path = tmp_path / "auth.db"
        db_path.touch()
        db_path.chmod(0o644)
        conn, _, _ = connect(db_path, {})
        conn.close()
        assert db_path.stat().st_mode & 0o777 == 0o600

    @pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits do not apply on Windows")
    def test_chmod_failure_does_not_crash_connect(self, tmp_path):
        """A permission-repair failure (root-installed service running as a
        lower-privileged user, or a deliberately read-only ops deployment)
        must degrade to a warning, not turn every connect() into a boot
        crash -- mirrors tests/test_personality.py's identical check for
        utils/personality_db.py's connect()."""
        db_path = tmp_path / "auth.db"
        db_path.touch()
        db_path.chmod(0o644)
        with patch("os.chmod", side_effect=PermissionError("nope")):
            conn, _, _ = connect(db_path, {})
        conn.close()
        # The chmod attempt failed, so the looser mode survives -- the point
        # of the test is that connect() returned a usable connection anyway.
        assert db_path.stat().st_mode & 0o777 == 0o644

    def test_returned_connection_is_usable(self, tmp_path):
        conn, _, _ = connect(tmp_path / "auth.db", {})
        try:
            conn.execute(ddl_users())
            conn.execute(
                "INSERT INTO users (username, password_hash, created_ts, disabled, "
                "last_login_ts, failed_count, locked_until_ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("alice", "scrypt$...", 0.0, 0, None, 0, None),
            )
            conn.commit()
            row = conn.execute("SELECT username FROM users").fetchone()
            assert row["username"] == "alice"
        finally:
            conn.close()

    def test_row_factory_supports_key_access(self, tmp_path):
        """AuthManager reads rows via row['col'], not a positional index --
        the sqlite3.Row factory is what makes that work."""
        conn, _, _ = connect(tmp_path / "auth.db", {})
        assert conn.row_factory is sqlite3.Row
        conn.close()


class TestPostgresOptIn:
    def test_database_url_config_key_selects_postgres(self, tmp_path):
        with pytest.raises(ImportError) as excinfo:
            connect(tmp_path / "unused.db", {"database_url": "postgresql://user:secret@host/db"})
        assert "psycopg" in str(excinfo.value)

    def test_env_var_selects_postgres(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CYCLAW_AUTH_DB_URL", "postgresql://user:secret@host/db")
        with pytest.raises(ImportError):
            connect(tmp_path / "unused.db", {})

    def test_config_key_takes_precedence_over_env_var(self, tmp_path, monkeypatch):
        """Both point at postgres either way here (psycopg absent), but this
        pins the precedence order documented in connect()'s own dsn lookup."""
        monkeypatch.setenv("CYCLAW_AUTH_DB_URL", "postgresql://from-env/db")
        with pytest.raises(ImportError):
            connect(tmp_path / "unused.db", {"database_url": "postgresql://from-config/db"})

    def test_missing_driver_error_never_echoes_the_dsn(self, tmp_path):
        """The DSN may carry credentials; the ImportError message must not
        repeat it (same rule utils/personality_db.py's connect() documents)."""
        secret_dsn = "postgresql://alice:hunter2@internal-host/proddb"
        with pytest.raises(ImportError) as excinfo:
            connect(tmp_path / "unused.db", {"database_url": secret_dsn})
        assert "hunter2" not in str(excinfo.value)
        assert "internal-host" not in str(excinfo.value)

    def test_this_env_var_does_not_leak_into_personality_db(self, tmp_path, monkeypatch):
        """CYCLAW_AUTH_DB_URL is deliberately its own env var, not
        CYCLAW_DB_URL -- setting it must not also opt personality's store
        into Postgres (see this module's docstring for why)."""
        monkeypatch.setenv("CYCLAW_AUTH_DB_URL", "postgresql://user:secret@host/db")
        monkeypatch.delenv("CYCLAW_DB_URL", raising=False)
        from utils.personality_db import connect as personality_connect

        conn, _, backend = personality_connect(tmp_path / "personality.db", {})
        try:
            assert backend == "sqlite"
        finally:
            conn.close()


class TestDDL:
    def test_all_three_tables_are_created(self, tmp_path):
        conn, _, _ = connect(tmp_path / "auth.db", {})
        try:
            conn.execute(ddl_users())
            conn.execute(ddl_sessions())
            conn.execute(ddl_device_tokens())
            for stmt in ddl_indexes():
                conn.execute(stmt)
            conn.commit()
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert {"users", "sessions", "device_tokens"} <= tables
        finally:
            conn.close()

    def test_ddl_is_idempotent(self, tmp_path):
        """CREATE TABLE IF NOT EXISTS -- running it twice (as every
        AuthManager.__init__ does on an existing DB) must not raise."""
        conn, _, _ = connect(tmp_path / "auth.db", {})
        try:
            for _ in range(2):
                conn.execute(ddl_users())
                conn.execute(ddl_sessions())
                conn.execute(ddl_device_tokens())
                for stmt in ddl_indexes():
                    conn.execute(stmt)
            conn.commit()
        finally:
            conn.close()

    def test_indexes_reference_real_columns(self, tmp_path):
        conn, _, _ = connect(tmp_path / "auth.db", {})
        try:
            conn.execute(ddl_sessions())
            conn.execute(ddl_device_tokens())
            for stmt in ddl_indexes():
                conn.execute(stmt)  # would raise sqlite3.OperationalError on a bad column
            conn.commit()
        finally:
            conn.close()
