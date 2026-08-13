"""Offline unit tests for utils.personality_db's Postgres conninfo hardening.

_harden_pg_conninfo does pure dict/string manipulation via psycopg.conninfo --
no network, no live server needed to test it. Every OTHER test that exercises
it (test_personality_postgres.py, test_ratelimit_postgres.py,
test_pgvector_store.py) only calls it as a step toward opening a real
connection, gated behind a live CYCLAW_DB_URL. This file tests the
string-building logic itself, so a regression here is caught without a
database. Still needs the psycopg package importable (it's an optional extra,
not in the base install) -- skipped automatically if it isn't.
"""

from __future__ import annotations

import pytest

psycopg_conninfo = pytest.importorskip("psycopg.conninfo")
conninfo_to_dict = psycopg_conninfo.conninfo_to_dict

from utils.personality_db import _harden_pg_conninfo  # noqa: E402


def test_adds_defaults_to_a_bare_dsn(monkeypatch):
    # ci.yml's postgres-backend job sets CYCLAW_DB_SSLMODE=disable at the job
    # level (for the trusted local service container) -- clear it here so this
    # test asserts the function's actual default, not whatever the ambient
    # environment happens to export. Without this the test would fail in
    # exactly the CI job it's meant to run in.
    monkeypatch.delenv("CYCLAW_DB_SSLMODE", raising=False)
    parts = conninfo_to_dict(_harden_pg_conninfo("postgresql://u:p@host/db"))
    assert parts["sslmode"] == "require"
    assert parts["application_name"] == "cyclaw"
    assert parts["connect_timeout"] == "10"
    assert parts["options"] == "-c statement_timeout=5000"


def test_preserves_an_explicit_sslmode():
    parts = conninfo_to_dict(_harden_pg_conninfo("postgresql://u:p@host/db?sslmode=verify-full"))
    assert parts["sslmode"] == "verify-full"


def test_respects_cyclaw_db_sslmode_env_override(monkeypatch):
    monkeypatch.setenv("CYCLAW_DB_SSLMODE", "disable")
    parts = conninfo_to_dict(_harden_pg_conninfo("postgresql://u:p@host/db"))
    assert parts["sslmode"] == "disable"


def test_appends_statement_timeout_to_existing_options_without_clobbering():
    parts = conninfo_to_dict(
        _harden_pg_conninfo("postgresql://u:p@host/db?options=-c%20lock_timeout%3D2000")
    )
    assert parts["options"] == "-c lock_timeout=2000 -c statement_timeout=5000"
