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
_PORT = 8787
_SAME_ORIGIN = f"http://localhost:{_PORT}"


def _cfg(tls_enabled=False, port=_PORT):
    return {
        "api": {"tls": {"enabled": tls_enabled}, "port": port},
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


def _base_url(cfg=None):
    """The same-origin check compares Origin against THIS request's own live
    url (request.url.port/.scheme), not a config-derived expectation -- so
    the test client's base_url must actually reflect cfg's port/scheme for a
    same-origin test to mean anything. TestClient/httpx never opens a real
    socket (ASGI in-process dispatch), so an "https://" base_url is enough to
    make request.url.scheme read "https" inside the app without needing a
    real TLS handshake or certificate.
    """
    cfg = cfg or _cfg()
    api_cfg = cfg.get("api", {}) if isinstance(cfg, dict) else {}
    port = api_cfg.get("port", _PORT) if isinstance(api_cfg, dict) else _PORT
    tls_cfg = api_cfg.get("tls", {}) if isinstance(api_cfg, dict) else {}
    scheme = "https" if isinstance(tls_cfg, dict) and tls_cfg.get("enabled") is True else "http"
    return f"{scheme}://localhost:{port}"


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
    cfg = cfg or _cfg()
    app = _make_app(manager, cfg=cfg, enforce_rate_limit=enforce_rate_limit)
    return TestClient(app, base_url=_base_url(cfg))  # DevSkim: ignore DS162092,DS137138 - test loopback host


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

    def test_setup_status_503(self):
        r = _client(None).get("/auth/setup-status")
        assert r.status_code == 503


class TestBootstrapPassword:
    def test_setup_status_after_bootstrap(self, manager):
        manager.bootstrap_if_empty()
        r = _client(manager).get("/auth/setup-status")
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is True
        assert body["needs_password"] is True
        assert body["username"] == "admin"

    def test_setup_status_cross_site_is_rejected(self, manager):
        manager.bootstrap_if_empty()
        r = _client(manager).get(
            "/auth/setup-status", headers={"sec-fetch-site": "cross-site"}
        )
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "CROSS_SITE_BLOCKED"

    def test_setup_status_portless_origin_is_rejected(self, manager):
        """Implicit :80 is a different origin from the console on :8787 (#1298 N10)."""
        manager.bootstrap_if_empty()
        r = _client(manager).get(
            "/auth/setup-status", headers={"origin": "http://localhost"}
        )
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "CROSS_ORIGIN_BLOCKED"

    def test_setup_status_same_origin_is_allowed(self, manager):
        manager.bootstrap_if_empty()
        r = _client(manager).get(
            "/auth/setup-status", headers={"origin": _SAME_ORIGIN}
        )
        assert r.status_code == 200

    def test_loopback_can_set_password(self, manager):
        manager.bootstrap_if_empty()
        app = _make_app(manager)
        client = TestClient(app, base_url=_base_url(), client=("127.0.0.1", 50000))
        r = client.post("/auth/bootstrap-password", json={"password": _GOOD_PASSWORD})
        assert r.status_code == 200
        assert r.json()["username"] == "admin"
        assert "cyclaw_session" in r.cookies
        assert manager.needs_password_setup() is False
        again = client.post("/auth/bootstrap-password", json={"password": _GOOD_PASSWORD})
        assert again.status_code == 409

    def test_loopback_proxied_is_forbidden(self, manager):
        manager.bootstrap_if_empty()
        app = _make_app(manager)
        client = TestClient(app, base_url=_base_url(), client=("127.0.0.1", 50000))
        r = client.post(
            "/auth/bootstrap-password",
            json={"password": _GOOD_PASSWORD},
            headers={"X-Forwarded-For": "8.8.8.8"},
        )
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "AUTH_LOOPBACK_ONLY"
        assert "forwarding headers" in r.json()["detail"]["message"]
        assert manager.needs_password_setup() is True

    def test_non_loopback_is_forbidden(self, manager):
        manager.bootstrap_if_empty()
        app = _make_app(manager)
        client = TestClient(app, base_url=_base_url(), client=("10.0.0.8", 50000))
        r = client.post("/auth/bootstrap-password", json={"password": _GOOD_PASSWORD})
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "AUTH_LOOPBACK_ONLY"
        assert manager.needs_password_setup() is True


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
        # Threshold-5 lockout is only _LOCKOUT_BASE_SEC (2.0s). login() snapshots
        # manager._now() before scrypt; on Windows CI five hashes can exceed that
        # window, so the sixth request sees an expired lock and returns 200.
        # Freeze the clock so this asserts lockout policy, not hasher wall time.
        frozen = manager._now()
        real_now = manager._now
        manager._now = lambda: frozen
        try:
            client = _client(manager)
            for _ in range(5):
                client.post("/auth/login", json={"username": username, "password": "wrong"})
            r = client.post("/auth/login", json={"username": username, "password": password})
            assert r.status_code == 423
            assert r.json()["detail"]["details"]["retry_after_sec"] > 0
        finally:
            manager._now = real_now

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
            headers={"origin": _SAME_ORIGIN},
        )
        assert r.status_code == 200

    def test_same_host_different_port_is_rejected(self, manager, user):
        """A browser's Origin is scheme+host+port; allowed_hosts and
        TrustedHostMiddleware both deliberately ignore port (gate.py's own
        documented reason), so without this the same-origin check would too
        -- letting any OTHER service on this host/IP but a different port
        pass as 'same-origin' on the LAN deployment this module targets."""
        username, password = user
        r = _client(manager).post(
            "/auth/login",
            json={"username": username, "password": password},
            headers={"origin": "http://localhost:9999"},
        )
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "CROSS_ORIGIN_BLOCKED"

    def test_a_different_allow_listed_host_is_not_same_origin(self, manager, user):
        """Same-origin means the Origin names THIS request's own host, not
        merely some host on the allow-list.

        The shipped config.yaml allow-lists two distinct LAN machines
        (10.0.0.111 and 10.0.0.112) alongside the loopback names, so an
        allow-list membership test would call a page served by one of them
        'same-origin' with a CyClaw running on the other. That is the
        'another device on the LAN' adversary docs/AUTHENTICATION_DESIGN.md §3
        names, and /auth/login is the one auth route with no CSRF token to
        fall back on, since a session does not exist yet -- so it lands as a
        login-CSRF. This test uses the 127.0.0.1/localhost pair the fixtures
        already allow-list, which has the identical shape: the request's Host
        is localhost, the Origin claims 127.0.0.1, both are allow-listed, the
        port and scheme match, and it still must be rejected.
        """
        username, password = user
        r = _client(manager).post(
            "/auth/login",
            json={"username": username, "password": password},
            headers={"origin": f"http://127.0.0.1:{_PORT}"},  # DevSkim: ignore DS162092,DS137138 - test loopback host
        )
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "CROSS_ORIGIN_BLOCKED"

    @pytest.mark.parametrize(
        "malformed_origin",
        [
            "http://localhost:notaport",  # non-numeric port -- urlparse() succeeds, .port raises
            "http://localhost:99999",  # out of the 0-65535 range -- same, lazy .port raise
            "http://[evil",  # unbalanced IPv6 bracket -- urlparse() itself raises
        ],
    )
    def test_malformed_origin_port_is_rejected_not_a_500(self, manager, user, malformed_origin):
        """Two distinct ValueError sources, both attacker-controlled on an
        unauthenticated route: urlparse().port is a lazy property that raises
        for a non-numeric or out-of-range port string, while a structurally
        malformed Origin (an unbalanced IPv6 bracket) makes urlparse() itself
        raise before .port is ever reached. An uncaught ValueError from
        either source would turn a malformed cross-origin request into an
        unhandled 500 instead of the 403 this check exists to return."""
        username, password = user
        r = _client(manager).post(
            "/auth/login",
            json={"username": username, "password": password},
            headers={"origin": malformed_origin},
        )
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "CROSS_ORIGIN_BLOCKED"

    def test_a_wildcard_allow_list_host_is_accepted(self, manager, user):
        """Match allowed_hosts the way TrustedHostMiddleware does.

        Starlette honours a leading `*.` domain wildcard, so an operator who
        allow-lists `*.example.com` is legitimately served at
        `node.example.com`. A plain `in` test is stricter than the Host filter
        that already admitted the request, and would 403 that operator's own
        login. Kept in step with gate.py's copy of the same matcher.
        """
        username, password = user
        cfg = {
            "api": {"tls": {"enabled": False}, "port": _PORT},
            "security": {"allowed_hosts": ["*.example.com"]},
        }
        client = TestClient(_make_app(manager, cfg=cfg), base_url=f"http://node.example.com:{_PORT}")
        r = client.post(
            "/auth/login",
            json={"username": username, "password": password},
            headers={"origin": f"http://node.example.com:{_PORT}"},
        )
        assert r.status_code == 200

    def test_a_bare_star_allow_list_is_still_refused(self, manager, user):
        """The one deliberate divergence from the middleware's rule.

        A bare `"*"` makes TrustedHostMiddleware skip Host validation
        entirely. With the Host unvalidated there is nothing for the Origin to
        be compared against, so the same-origin check must refuse rather than
        accept a pair that is unvalidated on both sides.
        """
        username, password = user
        cfg = {
            "api": {"tls": {"enabled": False}, "port": _PORT},
            "security": {"allowed_hosts": ["*"]},
        }
        client = TestClient(_make_app(manager, cfg=cfg), base_url=f"http://anything.test:{_PORT}")
        r = client.post(
            "/auth/login",
            json={"username": username, "password": password},
            headers={"origin": f"http://anything.test:{_PORT}"},
        )
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "CROSS_ORIGIN_BLOCKED"

    def test_a_malformed_port_is_rejected_on_a_scheme_default_port_too(self, manager, user):
        """The case the parametrized test above cannot reach, on the deployment
        this module exists for.

        The malformed-port branch used to fall back to origin_port = None, on
        the stated reasoning that None "can never equal request.url.port". That
        holds only while the server is reached on 8787. Serve on 443 -- the TLS
        deployment auth is for -- and request.url.port is itself None, so the
        fallback made "https://localhost:notaport" compare EQUAL to the target
        and pass as same-origin. Refusing outright is what closes it.
        """
        username, password = user
        cfg = _cfg(tls_enabled=True, port=443)
        client = TestClient(_make_app(manager, cfg=cfg), base_url="https://localhost")  # DevSkim: ignore DS162092,DS137138 - test loopback host
        r = client.post(
            "/auth/login",
            json={"username": username, "password": password},
            headers={"origin": "https://localhost:notaport"},  # DevSkim: ignore DS162092,DS137138 - test loopback host
        )
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "CROSS_ORIGIN_BLOCKED"

    def test_origin_without_an_explicit_port_is_rejected(self, manager, user):
        """CyClaw's port (8787) is never a scheme default, so a genuine
        same-origin request's Origin header always states it explicitly --
        one lacking a port is not this app, regardless of hostname."""
        username, password = user
        r = _client(manager).post(
            "/auth/login",
            json={"username": username, "password": password},
            headers={"origin": "http://localhost"},
        )
        assert r.status_code == 403

    def test_same_host_and_port_but_https_is_rejected_when_tls_is_off(self, manager, user):
        username, password = user
        r = _client(manager, cfg=_cfg(tls_enabled=False)).post(
            "/auth/login",
            json={"username": username, "password": password},
            headers={"origin": f"https://localhost:{_PORT}"},
        )
        assert r.status_code == 403

    def test_https_origin_is_accepted_when_tls_is_on(self, manager, user):
        username, password = user
        r = _client(manager, cfg=_cfg(tls_enabled=True)).post(
            "/auth/login",
            json={"username": username, "password": password},
            headers={"origin": f"https://localhost:{_PORT}"},
        )
        assert r.status_code == 200

    def test_absent_origin_and_sec_fetch_site_are_allowed(self, manager, user):
        """curl/PowerShell/Telegram send neither -- must not be blocked."""
        username, password = user
        r = _client(manager).post("/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200

    def test_origin_matching_the_live_request_is_accepted_even_if_config_disagrees(self, manager, user):
        """The check compares Origin against THIS request's own live
        url (request.url.port/.scheme), not api.port/api.tls.enabled --
        config states how the operator INTENDED to run the server, not
        necessarily the connection a given request actually arrived on. cfg
        here claims port 9999; the app is actually served (via the test
        client's base_url) on 8787. An Origin naming the port the request
        genuinely arrived on must still be accepted."""
        username, password = user
        app = _make_app(manager, cfg=_cfg(port=9999))
        client = TestClient(app, base_url="http://localhost:8787")
        r = client.post(
            "/auth/login",
            json={"username": username, "password": password},
            headers={"origin": "http://localhost:8787"},
        )
        assert r.status_code == 200

    def test_origin_matching_only_the_stale_config_port_is_rejected(self, manager, user):
        """The mirror of the test above: an Origin that matches the
        CONFIGURED port rather than the port this request actually arrived
        on must be rejected -- proving the check is not silently still
        keying off config."""
        username, password = user
        app = _make_app(manager, cfg=_cfg(port=9999))
        client = TestClient(app, base_url="http://localhost:8787")
        r = client.post(
            "/auth/login",
            json={"username": username, "password": password},
            headers={"origin": "http://localhost:9999"},
        )
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "CROSS_ORIGIN_BLOCKED"


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
        assert r.json()["csrf_token"]
        assert "no-store" in r.headers.get("cache-control", "").lower()

    def test_whoami_rotates_csrf_so_a_reload_can_logout(self, manager, user):
        """Login plaintext is issued once; the DB stores only the hash. After
        a reload the console has the cookie but not the JS token. whoami must
        mint a new one so logout/Users writes work without clearing cookies."""
        username, password = user
        client = _client(manager)
        login_csrf = client.post(
            "/auth/login", json={"username": username, "password": password}
        ).json()["csrf_token"]
        whoami = client.get("/auth/whoami")
        assert whoami.status_code == 200
        new_csrf = whoami.json()["csrf_token"]
        assert new_csrf
        assert new_csrf != login_csrf
        stale = client.post("/auth/logout", headers={"x-cyclaw-csrf": login_csrf})
        assert stale.status_code == 403
        ok = client.post("/auth/logout", headers={"x-cyclaw-csrf": new_csrf})
        assert ok.status_code == 200

    def test_via_bearer_token(self, manager, user):
        username, _ = user
        token = manager.create_device_token(username, "laptop")
        r = _client(manager).get("/auth/whoami", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["username"] == username
        assert r.json().get("csrf_token") is None
        assert "no-store" in r.headers.get("cache-control", "").lower()

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
        """CSRF must never GATE whoami. A cookie session may rotate the token
        in the response so a reloaded tab can mutate; that is a side effect,
        not an input requirement."""
        username, password = user
        client = _client(manager)
        client.post("/auth/login", json={"username": username, "password": password})
        r = client.get("/auth/whoami")  # deliberately no X-CyClaw-CSRF header
        assert r.status_code == 200

    def test_whoami_rejects_a_cross_site_request(self, manager, user):
        """/auth/whoami was the only one of the three /auth/* routes that
        omitted the same-origin check, so a page explicitly marked
        cross-site could still confirm a victim's login state (username) via
        their ambient session cookie. Now consistent with login/logout."""
        username, password = user
        client = _client(manager)
        client.post("/auth/login", json={"username": username, "password": password})
        r = client.get("/auth/whoami", headers={"sec-fetch-site": "cross-site"})
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "CROSS_SITE_BLOCKED"

    def test_whoami_still_works_with_a_same_origin_header(self, manager, user):
        username, password = user
        client = _client(manager)
        client.post("/auth/login", json={"username": username, "password": password})
        r = client.get("/auth/whoami", headers={"origin": _SAME_ORIGIN})
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
    response timing, and comparing the RAW header value (rather than its
    hash) would compare against the wrong thing entirely -- the stored value
    is authn.hash_token(csrf_token), not the plaintext (issue #998; see
    authn_manager.SessionInfo's docstring). Both CSRF-enforcing call sites
    (_enforce_csrf and _require_write_actor) must hash the header first."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "gate_auth.py").read_text(encoding="utf-8")
    assert src.count("hmac.compare_digest(") == 2
    assert "authn.hash_token(supplied).encode" in src
    assert "compare_digest(supplied.encode" not in src


def test_login_and_logout_run_the_blocking_manager_call_on_a_worker_thread():
    """auth_login/auth_logout are `async def` route handlers; a blocking
    scrypt-hash + SQLite call inside one runs directly on gate.py's single
    event loop (uvicorn.run carries no workers=), stalling every other
    in-flight request -- including /query -- for the duration. Whether the
    event loop actually stalls cannot be pinned reliably by a behavioural
    test (TestClient's own concurrency model doesn't guarantee two calls
    share one loop), so this pins the fix at the source instead -- same
    reasoning as the CSRF timing-safe-compare pin above."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "gate_auth.py").read_text(encoding="utf-8")
    assert "await asyncio.to_thread(manager.login" in src
    assert "await asyncio.to_thread(manager.logout" in src
