"""Tests for gate_auth.py -- /auth/login, /auth/logout, /auth/whoami.

register_auth_routes is a registration function, not tied to gate.py's own
module-level app: gate.py calls it ONCE at import time with whatever
cfg/auth_manager existed then, so patching `gate.cfg` afterward would never
reach gate_auth's already-closed-over values (unlike route handlers that read
`cfg` live on every request). Tests instead build a throwaway FastAPI app and
a real AuthManager (SQLite in tmp_path) and call register_auth_routes
directly -- the same reason harness/server.py's create_app() factory is
tested by calling it fresh per test rather than importing a module-level app.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from gate_auth import register_auth_routes
from utils.authn_manager import AuthManager

_GOOD_PASSWORD = "correct horse battery staple"
_ALLOWED_HOSTS = ["127.0.0.1", "localhost"]


def _cfg(tls_enabled=False):
    return {
        "api": {"tls": {"enabled": tls_enabled}},
        "security": {"allowed_hosts": _ALLOWED_HOSTS},
    }


async def _allow_all(_request: Request) -> None:
    return None


def _make_app(manager, cfg=None, enforce_rate_limit=_allow_all):
    app = FastAPI()
    events = []

    async def audit(event):
        events.append(event)

    register_auth_routes(
        app, cfg or _cfg(), audit=audit, enforce_rate_limit=enforce_rate_limit, auth_manager=manager,
    )
    app.state.audit_events = events
    return app


@pytest.fixture
def manager(tmp_path):
    m = AuthManager({"auth": {"enabled": True, "db_path": str(tmp_path / "auth.db")}})
    yield m
    m.close()


@pytest.fixture
def user(manager):
    manager.create_user("alice", _GOOD_PASSWORD)
    return "alice", _GOOD_PASSWORD


def _client(manager=None, cfg=None, enforce_rate_limit=_allow_all):
    app = _make_app(manager, cfg=cfg, enforce_rate_limit=enforce_rate_limit)
    return TestClient(app, base_url="http://localhost")  # DevSkim: ignore DS162092,DS137138 - test loopback host


class TestAuthDisabled:
    """auth_manager is None whenever config.yaml's auth.enabled is false --
    the shipped default. Every route must say so with 503, not 404 (a 404
    would disclose that the route conditionally exists) and not crash."""

    def test_login_503(self):
        r = _client(None).post("/auth/login", json={"username": "a", "password": "b"})
        assert r.status_code == 503
        assert r.json()["detail"]["code"] == "AUTH_DISABLED"

    def test_logout_503(self):
        r = _client(None).post("/auth/logout")
        assert r.status_code == 503

    def test_whoami_503(self):
        r = _client(None).get("/auth/whoami")
        assert r.status_code == 503


class TestLogin:
    def test_success_returns_username_and_csrf(self, manager, user):
        username, password = user
        r = _client(manager).post("/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200
        body = r.json()
        assert body["username"] == username
        assert body["csrf_token"]
        assert body["expires_ts"] > 0

    def test_success_sets_the_session_cookie(self, manager, user):
        username, password = user
        r = _client(manager).post("/auth/login", json={"username": username, "password": password})
        assert "cyclaw_session" in r.cookies

    def test_cookie_is_httponly_and_samesite_strict(self, manager, user):
        username, password = user
        r = _client(manager).post("/auth/login", json={"username": username, "password": password})
        set_cookie = r.headers.get("set-cookie", "").lower()
        assert "httponly" in set_cookie
        assert "samesite=strict" in set_cookie

    def test_cookie_is_secure_when_tls_is_configured(self, manager, user):
        username, password = user
        r = _client(manager, cfg=_cfg(tls_enabled=True)).post(
            "/auth/login", json={"username": username, "password": password}
        )
        assert "secure" in r.headers.get("set-cookie", "").lower()

    def test_cookie_is_not_secure_when_tls_is_not_configured(self, manager, user):
        """The design doc's §5/§7 rule: Secure is not sent over plain HTTP,
        so a cookie must not claim Secure when TLS genuinely isn't on."""
        username, password = user
        r = _client(manager, cfg=_cfg(tls_enabled=False)).post(
            "/auth/login", json={"username": username, "password": password}
        )
        assert "secure" not in r.headers.get("set-cookie", "").lower()

    def test_wrong_password_is_401_generic(self, manager, user):
        username, _ = user
        r = _client(manager).post("/auth/login", json={"username": username, "password": "wrong wrong wrong"})
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "AUTH_LOGIN_FAILED"
        assert "cyclaw_session" not in r.cookies

    def test_unknown_user_is_401_same_shape_as_wrong_password(self, manager):
        r = _client(manager).post("/auth/login", json={"username": "ghost", "password": "whatever12345"})
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "AUTH_LOGIN_FAILED"

    def test_locked_account_is_423_with_retry_after(self, manager, user):
        username, password = user
        client = _client(manager)
        for _ in range(5):
            client.post("/auth/login", json={"username": username, "password": "wrong"})
        r = client.post("/auth/login", json={"username": username, "password": password})
        assert r.status_code == 423
        assert r.json()["detail"]["details"]["retry_after_sec"] > 0

    def test_extra_field_is_422(self, manager, user):
        username, password = user
        r = _client(manager).post(
            "/auth/login", json={"username": username, "password": password, "extra": "nope"}
        )
        assert r.status_code == 422

    def test_login_is_audited(self, manager, user):
        username, password = user
        app = _make_app(manager)
        TestClient(app, base_url="http://localhost").post(
            "/auth/login", json={"username": username, "password": password}
        )
        events = [e["event"] for e in app.state.audit_events]
        assert "auth_login_ok" in events

    def test_failed_login_is_audited_without_leaking_the_password(self, manager, user):
        username, _ = user
        app = _make_app(manager)
        TestClient(app, base_url="http://localhost").post(
            "/auth/login", json={"username": username, "password": "wrong"}
        )
        for event in app.state.audit_events:
            assert "wrong" not in str(event)


class TestSameOrigin:
    def test_cross_site_header_is_rejected(self, manager, user):
        username, password = user
        r = _client(manager).post(
            "/auth/login",
            json={"username": username, "password": password},
            headers={"sec-fetch-site": "cross-site"},
        )
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "CROSS_SITE_BLOCKED"

    def test_same_site_header_is_allowed(self, manager, user):
        username, password = user
        r = _client(manager).post(
            "/auth/login",
            json={"username": username, "password": password},
            headers={"sec-fetch-site": "same-origin"},
        )
        assert r.status_code == 200

    def test_cross_origin_header_is_rejected(self, manager, user):
        username, password = user
        r = _client(manager).post(
            "/auth/login",
            json={"username": username, "password": password},
            headers={"origin": "http://evil.example"},
        )
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "CROSS_ORIGIN_BLOCKED"

    def test_allowed_origin_is_accepted(self, manager, user):
        username, password = user
        r = _client(manager).post(
            "/auth/login",
            json={"username": username, "password": password},
            headers={"origin": "http://localhost"},
        )
        assert r.status_code == 200

    def test_absent_origin_and_sec_fetch_site_are_allowed(self, manager, user):
        """curl/PowerShell/Telegram send neither -- must not be blocked."""
        username, password = user
        r = _client(manager).post("/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200


class TestLogoutAndCsrf:
    def _login(self, client, username, password):
        r = client.post("/auth/login", json={"username": username, "password": password})
        return r.json()["csrf_token"]

    def test_logout_without_session_cookie_is_401(self, manager):
        r = _client(manager).post("/auth/logout", headers={"x-cyclaw-csrf": "irrelevant"})
        assert r.status_code == 401

    def test_logout_without_csrf_header_is_403(self, manager, user):
        username, password = user
        client = _client(manager)
        self._login(client, username, password)
        r = client.post("/auth/logout")
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "CSRF_TOKEN_INVALID"

    def test_logout_with_wrong_csrf_is_403(self, manager, user):
        username, password = user
        client = _client(manager)
        self._login(client, username, password)
        r = client.post("/auth/logout", headers={"x-cyclaw-csrf": "not-the-real-token"})
        assert r.status_code == 403

    def test_logout_with_correct_csrf_succeeds(self, manager, user):
        username, password = user
        client = _client(manager)
        csrf = self._login(client, username, password)
        r = client.post("/auth/logout", headers={"x-cyclaw-csrf": csrf})
        assert r.status_code == 200
        assert r.json() == {"ok": True}

    def test_logout_actually_revokes_the_session(self, manager, user):
        username, password = user
        client = _client(manager)
        csrf = self._login(client, username, password)
        client.post("/auth/logout", headers={"x-cyclaw-csrf": csrf})
        r = client.get("/auth/whoami")
        assert r.status_code == 401

    def test_csrf_token_from_one_session_does_not_work_for_another(self, manager):
        manager.create_user("alice", _GOOD_PASSWORD)
        manager.create_user("bob", "another good password entirely")
        app = _make_app(manager)
        alice = TestClient(app, base_url="http://localhost")
        bob = TestClient(app, base_url="http://localhost")
        alice.post("/auth/login", json={"username": "alice", "password": _GOOD_PASSWORD})
        bob_csrf = bob.post(
            "/auth/login", json={"username": "bob", "password": "another good password entirely"}
        ).json()["csrf_token"]
        # alice's cookie jar, bob's CSRF token -- must not be accepted.
        r = alice.post("/auth/logout", headers={"x-cyclaw-csrf": bob_csrf})
        assert r.status_code == 403


class TestWhoami:
    def test_via_session_cookie(self, manager, user):
        username, password = user
        client = _client(manager)
        client.post("/auth/login", json={"username": username, "password": password})
        r = client.get("/auth/whoami")
        assert r.status_code == 200
        assert r.json()["username"] == username

    def test_via_bearer_token(self, manager, user):
        username, _ = user
        token = manager.create_device_token(username, "laptop")
        r = _client(manager).get("/auth/whoami", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["username"] == username

    def test_no_credentials_is_401(self, manager):
        r = _client(manager).get("/auth/whoami")
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "AUTH_REQUIRED"

    def test_revoked_token_is_401(self, manager, user):
        username, _ = user
        token = manager.create_device_token(username, "laptop")
        manager.revoke_device_token(username, "laptop")
        r = _client(manager).get("/auth/whoami", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401

    def test_garbage_bearer_token_is_401_not_500(self, manager):
        r = _client(manager).get("/auth/whoami", headers={"Authorization": "Bearer not-a-real-token"})
        assert r.status_code == 401

    def test_malformed_authorization_header_is_401_not_500(self, manager):
        r = _client(manager).get("/auth/whoami", headers={"Authorization": "NotBearer something"})
        assert r.status_code == 401

    def test_whoami_does_not_require_csrf(self, manager, user):
        """GET is not state-changing -- CSRF must never gate a read path."""
        username, password = user
        client = _client(manager)
        client.post("/auth/login", json={"username": username, "password": password})
        r = client.get("/auth/whoami")  # deliberately no X-CyClaw-CSRF header
        assert r.status_code == 200


class TestRateLimitOrdering:
    """Mirrors gate.py's own TestFailedAuthDoesNotBypassRateLimit convention:
    a spent rate-limit budget must win even over a state that would otherwise
    resolve to a DIFFERENT error, or the limiter stops bounding abuse."""

    def test_rate_limit_runs_before_login_is_attempted(self, manager, user):
        username, password = user

        async def deny(_request: Request) -> None:
            raise HTTPException(status_code=429, detail={"code": "RATE_LIMIT"})

        r = _client(manager, enforce_rate_limit=deny).post(
            "/auth/login", json={"username": username, "password": password}
        )
        assert r.status_code == 429

    def test_rate_limit_runs_before_session_or_csrf_lookup(self, manager):
        async def deny(_request: Request) -> None:
            raise HTTPException(status_code=429, detail={"code": "RATE_LIMIT"})

        client = _client(manager, enforce_rate_limit=deny)
        client.cookies.set("cyclaw_session", "totally-not-a-real-session-id")
        r = client.post("/auth/logout", headers={"x-cyclaw-csrf": "whatever"})
        assert r.status_code == 429

    def test_rate_limit_runs_before_whoami_credential_check(self, manager):
        async def deny(_request: Request) -> None:
            raise HTTPException(status_code=429, detail={"code": "RATE_LIMIT"})

        r = _client(manager, enforce_rate_limit=deny).get("/auth/whoami")
        assert r.status_code == 429


def test_csrf_comparison_uses_a_timing_safe_compare():
    """A timing leak cannot be caught by behaviour, so pin it at the source --
    same reasoning tests/test_authn.py pins hmac.compare_digest usage in
    verify_password. A plain `==` would leak the CSRF token's prefix through
    response timing."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "gate_auth.py").read_text(encoding="utf-8")
    assert "hmac.compare_digest(supplied.encode" in src
