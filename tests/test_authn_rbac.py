"""Stage 6: roles, last-admin protection, and role-column migration."""

from __future__ import annotations

import sqlite3

import pytest

from utils import authn, authn_store
from utils.authn_manager import AuthManager, BOOTSTRAP_USERNAME
from utils.errors import AuthLastAdmin

_GOOD = "correct horse battery staple"


@pytest.fixture
def manager(tmp_path):
    m = AuthManager({"auth": {"enabled": True, "db_path": str(tmp_path / "auth.db")}})
    yield m
    m.close()


def test_validate_role_rejects_unknown():
    with pytest.raises(authn.PasswordPolicyError):
        authn.validate_role("superuser")
    assert authn.validate_role("Admin") == "admin"


def test_bootstrap_admin_has_admin_role(manager):
    assert manager.bootstrap_if_empty() is True
    user = manager.get_user(BOOTSTRAP_USERNAME)
    assert user is not None
    assert user.role == "admin"


def test_create_user_default_role_is_operator(manager):
    manager.create_user("alice", _GOOD)
    assert manager.get_user("alice").role == "operator"


def test_create_user_role_validated(manager):
    with pytest.raises(authn.PasswordPolicyError):
        manager.create_user("alice", _GOOD, role="root")


def test_last_admin_cannot_be_deleted_disabled_or_demoted(manager):
    manager.bootstrap_if_empty()
    with pytest.raises(AuthLastAdmin):
        manager.delete_user(BOOTSTRAP_USERNAME)
    with pytest.raises(AuthLastAdmin):
        manager.disable_user(BOOTSTRAP_USERNAME)
    with pytest.raises(AuthLastAdmin):
        manager.set_role(BOOTSTRAP_USERNAME, "operator")


def test_second_admin_allows_demoting_the_first(manager):
    manager.bootstrap_if_empty()
    manager.create_user("other", _GOOD, role="admin")
    manager.set_role(BOOTSTRAP_USERNAME, "operator")
    assert manager.get_user(BOOTSTRAP_USERNAME).role == "operator"


def test_delete_user_removes_row(manager):
    manager.create_user("alice", _GOOD)
    manager.delete_user("alice")
    assert manager.get_user("alice") is None


def test_migration_adds_role_and_promotes_bootstrap(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(str(db))
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
        "VALUES ('admin', 'placeholder', 1, 0, 0)"
    )
    conn.execute(
        "INSERT INTO users (username, password_hash, created_ts, disabled, failed_count) "
        "VALUES ('alice', 'placeholder', 1, 0, 0)"
    )
    conn.commit()
    conn.close()

    manager = AuthManager({"auth": {"enabled": True, "db_path": str(db)}})
    try:
        assert manager.get_user("admin").role == "admin"
        assert manager.get_user("alice").role == "operator"
        names = authn_store.users_column_names(manager.conn, manager.backend)
        assert "role" in {n.lower() for n in names}
    finally:
        manager.close()
