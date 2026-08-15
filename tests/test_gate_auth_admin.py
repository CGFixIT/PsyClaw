"""Stage 6 HTTP admin surface: roles, CSRF, 503-when-disabled."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from gate_auth import register_auth_routes
from utils.authn_manager import AuthManager

_GOOD = "correct horse battery staple"
_PORT = 8787
_SAME_ORIGIN = f"http://localhost:{_PORT}"


def _cfg():
    return {
        "api": {"tls": {"enabled": False}, "port": _PORT},
        "security": {"allowed_hosts": ["127.0.0.1", "localhost"]},
        "logging": {"audit_file": "logs/audit.jsonl"},
    }


async def _allow(_request: Request) -> None:
    return None


def _client(manager):
    app = FastAPI()

    async def audit(_event):
        return None

    register_auth_routes(
        app, _cfg(), audit=audit, enforce_rate_limit=_allow, auth_manager=manager,
    )
    return TestClient(app, base_url=_SAME_ORIGIN)  # DevSkim: ignore DS162092,DS137138


@pytest.fixture
def manager(tmp_path):
    m = AuthManager({"auth": {"enabled": True, "db_path": str(tmp_path / "auth.db")}})
    yield m
    m.close()


def _login(client, username, password):
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200
    return r.json()["csrf_token"]


class TestAdminDisabled:
    def test_users_503(self):
        r = _client(None).get("/auth/users")
        assert r.status_code == 503


class TestAdminMatrix:
    def test_whoami_includes_role(self, manager):
        manager.create_user("alice", _GOOD, role="operator")
        client = _client(manager)
        _login(client, "alice", _GOOD)
        r = client.get("/auth/whoami")
        assert r.json()["role"] == "operator"

    def test_operator_cannot_delete_or_set_role(self, manager):
        manager.create_user("root", _GOOD, role="admin")
        manager.create_user("alice", _GOOD, role="operator")
        manager.create_user("bob", _GOOD, role="operator")
        client = _client(manager)
        csrf = _login(client, "alice", _GOOD)
        r = client.delete("/auth/users/bob", headers={"x-cyclaw-csrf": csrf})
        assert r.status_code == 403
        r = client.post(
            "/auth/users/bob/role",
            json={"role": "admin"},
            headers={"x-cyclaw-csrf": csrf},
        )
        assert r.status_code == 403

    def test_operator_cannot_touch_admin(self, manager):
        manager.create_user("root", _GOOD, role="admin")
        manager.create_user("alice", _GOOD, role="operator")
        client = _client(manager)
        csrf = _login(client, "alice", _GOOD)
        r = client.post(
            "/auth/users/root/password",
            json={"password": "another good password!!"},
            headers={"x-cyclaw-csrf": csrf},
        )
        assert r.status_code == 403

    def test_audit_cannot_write_or_list_users(self, manager):
        manager.create_user("eve", _GOOD, role="audit")
        client = _client(manager)
        csrf = _login(client, "eve", _GOOD)
        assert client.get("/auth/users").status_code == 403
        r = client.post(
            "/auth/users",
            json={"username": "newone", "password": _GOOD, "role": "operator"},
            headers={"x-cyclaw-csrf": csrf},
        )
        assert r.status_code == 403

    def test_admin_can_create_and_list(self, manager):
        manager.create_user("root", _GOOD, role="admin")
        client = _client(manager)
        csrf = _login(client, "root", _GOOD)
        r = client.post(
            "/auth/users",
            json={"username": "bob", "password": _GOOD, "role": "operator"},
            headers={"x-cyclaw-csrf": csrf},
        )
        assert r.status_code == 200
        listed = client.get("/auth/users")
        assert listed.status_code == 200
        names = {u["username"] for u in listed.json()}
        assert {"root", "bob"} <= names

    def test_csrf_missing_on_create_is_403(self, manager):
        manager.create_user("root", _GOOD, role="admin")
        client = _client(manager)
        _login(client, "root", _GOOD)
        r = client.post(
            "/auth/users",
            json={"username": "bob", "password": _GOOD, "role": "operator"},
        )
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "CSRF_TOKEN_INVALID"

    def test_audit_can_open_summary(self, manager):
        manager.create_user("eve", _GOOD, role="audit")
        client = _client(manager)
        _login(client, "eve", _GOOD)
        r = client.get("/auth/audit/summary")
        assert r.status_code == 200

    def test_operator_cannot_open_audit_summary(self, manager):
        manager.create_user("alice", _GOOD, role="operator")
        client = _client(manager)
        _login(client, "alice", _GOOD)
        assert client.get("/auth/audit/summary").status_code == 403
