"""Offline unit tests for utils.personality_db.

Covers Postgres conninfo hardening (needs psycopg), DDL backend branches,
connect()'s ImportError path when psycopg is missing, the successful Postgres
connect path with psycopg.connect mocked (no live server), and the SQLite
chmod-OSError non-fatal path.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from utils import personality_db


def test_ddl_soul_versions_postgres_uses_identity():
    ddl = personality_db.ddl_soul_versions("postgres")
    assert "soul_versions" in ddl
    assert "BIGINT GENERATED ALWAYS AS IDENTITY" in ddl


def test_ddl_interactions_postgres_uses_identity():
    ddl = personality_db.ddl_interactions("postgres")
    assert "interactions" in ddl
    assert "BIGINT GENERATED ALWAYS AS IDENTITY" in ddl


def test_connect_postgres_raises_when_psycopg_import_fails(monkeypatch, tmp_path):
    monkeypatch.delenv("CYCLAW_DB_URL", raising=False)
    real_import = __import__

    def _block_psycopg(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "psycopg" or (isinstance(name, str) and name.startswith("psycopg.")):
            raise ImportError("simulated missing psycopg")
        return real_import(name, globals, locals, fromlist, level)

    with patch("builtins.__import__", side_effect=_block_psycopg):
        with pytest.raises(ImportError, match="psycopg is required for PostgreSQL support"):
            personality_db.connect(
                tmp_path / "unused.db",
                {"database_url": "postgresql://u:p@host/db"},
            )


def test_connect_postgres_success_returns_percent_placeholder(monkeypatch, tmp_path):
    pytest.importorskip("psycopg")
    monkeypatch.delenv("CYCLAW_DB_URL", raising=False)
    fake_conn = MagicMock(name="pg_conn")
    with patch("psycopg.connect", return_value=fake_conn) as connect:
        conn, placeholder, backend = personality_db.connect(
            tmp_path / "unused.db",
            {"database_url": "postgresql://u:p@host/db"},
        )
    assert conn is fake_conn
    assert placeholder == "%s"
    assert backend == "postgres"
    connect.assert_called_once()
    # DSN must be hardened (sslmode / timeouts) before connect — never the raw URL alone.
    hardened = connect.call_args.args[0]
    assert "sslmode" in hardened
    assert connect.call_args.kwargs.get("autocommit") is False


def test_connect_sqlite_chmod_oserror_is_non_fatal(tmp_path, caplog, monkeypatch):
    # postgres-backend CI sets CYCLAW_DB_URL; this test must still hit sqlite.
    monkeypatch.delenv("CYCLAW_DB_URL", raising=False)
    db_path = tmp_path / "personality.db"
    db_path.touch()
    with (
        patch("utils.personality_db.stat.S_IMODE", return_value=0o644),
        patch("utils.personality_db.os.chmod", side_effect=OSError("not owner")),
        caplog.at_level(logging.WARNING, logger="cyclaw.personality_db"),
    ):
        conn, placeholder, backend = personality_db.connect(db_path, {})
    try:
        assert backend == "sqlite"
        assert placeholder == "?"
        assert "Could not harden personality DB permissions to 0600" in caplog.text
    finally:
        conn.close()


@pytest.fixture(scope="module")
def _harden():
    """Conninfo helpers need the optional psycopg extra; skip the harden group if absent."""
    psycopg_conninfo = pytest.importorskip("psycopg.conninfo")
    from utils.personality_db import _harden_pg_conninfo

    return psycopg_conninfo.conninfo_to_dict, _harden_pg_conninfo


def test_adds_defaults_to_a_bare_dsn(monkeypatch, _harden):
    # ci.yml's postgres-backend job sets CYCLAW_DB_SSLMODE=disable at the job
    # level (for the trusted local service container) -- clear it here so this
    # test asserts the function's actual default, not whatever the ambient
    # environment happens to export. Without this the test would fail in
    # exactly the CI job it's meant to run in.
    conninfo_to_dict, harden = _harden
    monkeypatch.delenv("CYCLAW_DB_SSLMODE", raising=False)
    parts = conninfo_to_dict(harden("postgresql://u:p@host/db"))
    assert parts["sslmode"] == "require"
    assert parts["application_name"] == "cyclaw"
    assert parts["connect_timeout"] == "10"
    assert parts["options"] == "-c statement_timeout=5000"


def test_preserves_an_explicit_sslmode(_harden):
    conninfo_to_dict, harden = _harden
    parts = conninfo_to_dict(harden("postgresql://u:p@host/db?sslmode=verify-full"))
    assert parts["sslmode"] == "verify-full"


def test_respects_cyclaw_db_sslmode_env_override(monkeypatch, _harden):
    conninfo_to_dict, harden = _harden
    monkeypatch.setenv("CYCLAW_DB_SSLMODE", "disable")
    parts = conninfo_to_dict(harden("postgresql://u:p@host/db"))
    assert parts["sslmode"] == "disable"


def test_appends_statement_timeout_to_existing_options_without_clobbering(_harden):
    conninfo_to_dict, harden = _harden
    parts = conninfo_to_dict(
        harden("postgresql://u:p@host/db?options=-c%20lock_timeout%3D2000")
    )
    assert parts["options"] == "-c lock_timeout=2000 -c statement_timeout=5000"


def test_sqlite_and_postgres_ddl_differ_on_identity() -> None:
    sqlite_soul = personality_db.ddl_soul_versions("sqlite")
    pg_soul = personality_db.ddl_soul_versions("postgres")
    assert "AUTOINCREMENT" in sqlite_soul
    assert "GENERATED ALWAYS AS IDENTITY" in pg_soul
    sqlite_ix = personality_db.ddl_interactions("sqlite")
    pg_ix = personality_db.ddl_interactions("postgres")
    assert "AUTOINCREMENT" in sqlite_ix
    assert "GENERATED ALWAYS AS IDENTITY" in pg_ix
    indexes = personality_db.ddl_indexes("sqlite")
    assert any("idx_interactions_ts" in stmt for stmt in indexes)
