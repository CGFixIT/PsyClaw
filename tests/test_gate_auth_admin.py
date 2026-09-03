"""Stage 6 HTTP admin surface: roles, CSRF, 503-when-disabled."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from gate_auth import register_auth_routes
from utils.authn_manager import AuthManager
from utils.errors import AuthLastAdmin

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

    def test_duplicate_create_is_409_with_a_typed_code(self, manager):
        # Characterization: gate_auth already mapped AuthUserExists to 409, but
        # nothing asserted it -- grep AUTH_USER_EXISTS across tests/ was empty
        # before this, so the harness copy could (and did) diverge unnoticed.
        manager.create_user("root", _GOOD, role="admin")
        client = _client(manager)
        csrf = _login(client, "root", _GOOD)
        body = {"username": "bob", "password": _GOOD, "role": "operator"}
        assert client.post("/auth/users", json=body, headers={"x-cyclaw-csrf": csrf}).status_code == 200
        again = client.post("/auth/users", json=body, headers={"x-cyclaw-csrf": csrf})
        assert again.status_code == 409
        assert again.json()["detail"]["code"] == "AUTH_USER_EXISTS"

    def test_raced_duplicate_create_is_409_not_an_unhandled_500(self, manager):
        # Blinding the pre-check to "bob" only -- and not to "root" -- is what a
        # racing writer produces, and leaves the DB constraint as the only
        # defence. Scoped to one username on purpose: the same statement
        # resolves the session's own user, so blinding it wholesale 401s the
        # request before it ever reaches create_user. The raw backend error
        # matches no branch of _raise_auth_error's ladder and gate.py registers
        # no RAGError handler, so before the fix this escaped as an unhandled
        # 500.
        manager.create_user("root", _GOOD, role="admin")
        client = _client(manager)
        csrf = _login(client, "root", _GOOD)
        body = {"username": "bob", "password": _GOOD, "role": "operator"}
        assert client.post("/auth/users", json=body, headers={"x-cyclaw-csrf": csrf}).status_code == 200
        manager._sql_get_user += " AND username <> 'bob'"
        again = client.post("/auth/users", json=body, headers={"x-cyclaw-csrf": csrf})
        assert again.status_code == 409
        assert again.json()["detail"]["code"] == "AUTH_USER_EXISTS"

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

    def test_last_admin_cannot_be_deleted_disabled_or_demoted(self, manager):
        manager.create_user("root", _GOOD, role="admin")
        client = _client(manager)
        csrf = _login(client, "root", _GOOD)
        deleted = client.delete("/auth/users/root", headers={"x-cyclaw-csrf": csrf})
        assert deleted.status_code == 403
        assert deleted.json()["detail"]["code"] == "AUTH_LAST_ADMIN"
        disabled = client.post("/auth/users/root/disable", headers={"x-cyclaw-csrf": csrf})
        assert disabled.status_code == 403
        assert disabled.json()["detail"]["code"] == "AUTH_LAST_ADMIN"
        demoted = client.post(
            "/auth/users/root/role",
            json={"role": "operator"},
            headers={"x-cyclaw-csrf": csrf},
        )
        assert demoted.status_code == 403
        assert demoted.json()["detail"]["code"] == "AUTH_LAST_ADMIN"

    def test_unknown_user_mutations_are_404(self, manager):
        manager.create_user("root", _GOOD, role="admin")
        client = _client(manager)
        csrf = _login(client, "root", _GOOD)
        headers = {"x-cyclaw-csrf": csrf}
        pw = client.post(
            "/auth/users/ghost/password",
            json={"password": "another good password!!"},
            headers=headers,
        )
        assert pw.status_code == 404
        assert pw.json()["detail"]["code"] == "AUTH_USER_NOT_FOUND"
        role = client.post(
            "/auth/users/ghost/role", json={"role": "operator"}, headers=headers,
        )
        assert role.status_code == 404
        assert role.json()["detail"]["code"] == "AUTH_USER_NOT_FOUND"
        disabled = client.post("/auth/users/ghost/disable", headers=headers)
        assert disabled.status_code == 404
        assert disabled.json()["detail"]["code"] == "AUTH_USER_NOT_FOUND"

    def test_short_password_on_set_and_self_is_422(self, manager):
        manager.create_user("root", _GOOD, role="admin")
        client = _client(manager)
        csrf = _login(client, "root", _GOOD)
        headers = {"x-cyclaw-csrf": csrf}
        other = client.post(
            "/auth/users/root/password", json={"password": "short"}, headers=headers,
        )
        assert other.status_code == 422
        assert other.json()["detail"]["code"] == "AUTH_POLICY"
        own = client.post("/auth/password", json={"password": "short"}, headers=headers)
        assert own.status_code == 422
        assert own.json()["detail"]["code"] == "AUTH_POLICY"

    def test_self_password_change_succeeds(self, manager):
        manager.create_user("root", _GOOD, role="admin")
        client = _client(manager)
        csrf = _login(client, "root", _GOOD)
        r = client.post(
            "/auth/password",
            json={"password": "another good password!!"},
            headers={"x-cyclaw-csrf": csrf},
        )
        assert r.status_code == 200
        assert r.json() == {"ok": True}

    def test_invalid_role_on_create_is_422(self, manager):
        manager.create_user("root", _GOOD, role="admin")
        client = _client(manager)
        csrf = _login(client, "root", _GOOD)
        r = client.post(
            "/auth/users",
            json={"username": "bob", "password": _GOOD, "role": "superuser"},
            headers={"x-cyclaw-csrf": csrf},
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "AUTH_POLICY"

    def test_operator_cannot_create_admin_but_can_create_operator(self, manager):
        manager.create_user("root", _GOOD, role="admin")
        manager.create_user("alice", _GOOD, role="operator")
        client = _client(manager)
        csrf = _login(client, "alice", _GOOD)
        denied = client.post(
            "/auth/users",
            json={"username": "mallory", "password": _GOOD, "role": "admin"},
            headers={"x-cyclaw-csrf": csrf},
        )
        assert denied.status_code == 403
        assert denied.json()["detail"]["code"] == "AUTH_PERMISSION_DENIED"
        ok = client.post(
            "/auth/users",
            json={"username": "bob", "password": _GOOD, "role": "operator"},
            headers={"x-cyclaw-csrf": csrf},
        )
        assert ok.status_code == 200
        assert ok.json()["username"] == "bob"

    def test_operator_can_reset_another_operator_password(self, manager):
        manager.create_user("root", _GOOD, role="admin")
        manager.create_user("alice", _GOOD, role="operator")
        manager.create_user("bob", _GOOD, role="operator")
        client = _client(manager)
        csrf = _login(client, "alice", _GOOD)
        r = client.post(
            "/auth/users/bob/password",
            json={"password": "another good password!!"},
            headers={"x-cyclaw-csrf": csrf},
        )
        assert r.status_code == 200
        assert r.json() == {"ok": True}

    def test_admin_can_disable_and_enable(self, manager):
        manager.create_user("root", _GOOD, role="admin")
        manager.create_user("bob", _GOOD, role="operator")
        client = _client(manager)
        csrf = _login(client, "root", _GOOD)
        headers = {"x-cyclaw-csrf": csrf}
        assert client.post("/auth/users/bob/disable", headers=headers).status_code == 200
        assert manager.get_user("bob").disabled is True
        assert client.post("/auth/users/bob/enable", headers=headers).status_code == 200
        assert manager.get_user("bob").disabled is False

    def test_enable_user_manager_error_is_mapped(self, manager):
        manager.create_user("root", _GOOD, role="admin")
        manager.create_user("bob", _GOOD, role="operator")
        client = _client(manager)
        csrf = _login(client, "root", _GOOD)
        with patch.object(manager, "enable_user", side_effect=AuthLastAdmin("cannot enable")):
            r = client.post("/auth/users/bob/enable", headers={"x-cyclaw-csrf": csrf})
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "AUTH_LAST_ADMIN"

    def test_admin_can_set_role(self, manager):
        manager.create_user("root", _GOOD, role="admin")
        manager.create_user("bob", _GOOD, role="operator")
        client = _client(manager)
        csrf = _login(client, "root", _GOOD)
        r = client.post(
            "/auth/users/bob/role",
            json={"role": "audit"},
            headers={"x-cyclaw-csrf": csrf},
        )
        assert r.status_code == 200
        assert manager.get_user("bob").role == "audit"

    def test_invalid_role_on_set_role_is_422(self, manager):
        manager.create_user("root", _GOOD, role="admin")
        manager.create_user("bob", _GOOD, role="operator")
        client = _client(manager)
        csrf = _login(client, "root", _GOOD)
        r = client.post(
            "/auth/users/bob/role",
            json={"role": "superuser"},
            headers={"x-cyclaw-csrf": csrf},
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "AUTH_POLICY"

    def test_audit_cannot_disable(self, manager):
        manager.create_user("root", _GOOD, role="admin")
        manager.create_user("eve", _GOOD, role="audit")
        manager.create_user("bob", _GOOD, role="operator")
        client = _client(manager)
        csrf = _login(client, "eve", _GOOD)
        r = client.post("/auth/users/bob/disable", headers={"x-cyclaw-csrf": csrf})
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "AUTH_PERMISSION_DENIED"

    def test_admin_bearer_can_create_without_csrf(self, manager):
        manager.create_user("root", _GOOD, role="admin")
        token = manager.create_device_token("root", "laptop")
        client = _client(manager)
        r = client.post(
            "/auth/users",
            json={"username": "bob", "password": _GOOD, "role": "operator"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["username"] == "bob"

    def test_operator_bearer_cannot_write(self, manager):
        manager.create_user("alice", _GOOD, role="operator")
        token = manager.create_device_token("alice", "laptop")
        client = _client(manager)
        r = client.post(
            "/auth/users",
            json={"username": "bob", "password": _GOOD, "role": "operator"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "AUTH_PERMISSION_DENIED"

    def test_invalid_bearer_write_is_401(self, manager):
        manager.create_user("root", _GOOD, role="admin")
        client = _client(manager)
        r = client.post(
            "/auth/users",
            json={"username": "bob", "password": _GOOD, "role": "operator"},
            headers={"Authorization": "Bearer not-a-real-token"},
        )
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "AUTH_REQUIRED"

    def test_list_users_when_actor_row_vanished_is_401(self, manager):
        """TOCTOU: session still validates, then get_user misses the row."""
        manager.create_user("root", _GOOD, role="admin")
        client = _client(manager)
        _login(client, "root", _GOOD)
        manager.get_user = lambda _username: None  # type: ignore[method-assign]
        r = client.get("/auth/users")
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "AUTH_REQUIRED"

    def test_write_without_cookie_or_bearer_is_401(self, manager):
        manager.create_user("root", _GOOD, role="admin")
        client = _client(manager)
        r = client.post(
            "/auth/users",
            json={"username": "bob", "password": _GOOD, "role": "operator"},
        )
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "AUTH_REQUIRED"

    def test_enable_unknown_user_is_404(self, manager):
        manager.create_user("root", _GOOD, role="admin")
        client = _client(manager)
        csrf = _login(client, "root", _GOOD)
        r = client.post("/auth/users/ghost/enable", headers={"x-cyclaw-csrf": csrf})
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "AUTH_USER_NOT_FOUND"

    def test_admin_can_delete_a_non_last_user(self, manager):
        manager.create_user("root", _GOOD, role="admin")
        manager.create_user("bob", _GOOD, role="operator")
        client = _client(manager)
        csrf = _login(client, "root", _GOOD)
        r = client.delete("/auth/users/bob", headers={"x-cyclaw-csrf": csrf})
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        assert manager.get_user("bob") is None

    def test_create_then_missing_row_is_500(self, manager):
        manager.create_user("root", _GOOD, role="admin")
        client = _client(manager)
        csrf = _login(client, "root", _GOOD)
        real_get = manager.get_user

        def _hide_bob(username: str):
            if username == "bob":
                return None
            return real_get(username)

        manager.get_user = _hide_bob  # type: ignore[method-assign]
        r = client.post(
            "/auth/users",
            json={"username": "bob", "password": _GOOD, "role": "operator"},
            headers={"x-cyclaw-csrf": csrf},
        )
        assert r.status_code == 500
        assert r.json()["detail"]["code"] == "AUTH_ERROR"

    def test_unmapped_manager_error_is_not_caught_as_auth_error(self, manager):
        manager.create_user("root", _GOOD, role="admin")
        client = _client(manager)
        csrf = _login(client, "root", _GOOD)

        def _boom(username, password, role="operator"):
            raise RuntimeError("disk full")

        manager.create_user = _boom  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="disk full"):
            client.post(
                "/auth/users",
                json={"username": "bob", "password": _GOOD, "role": "operator"},
                headers={"x-cyclaw-csrf": csrf},
            )
