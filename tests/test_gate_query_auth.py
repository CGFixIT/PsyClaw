"""Stage 3: /query requires a session cookie or device token when auth is on.

Uses a throwaway FastAPI app + real AuthManager (same pattern as
tests/test_gate_auth.py). The live gate.app keeps auth.enabled false, so
those tests stay on the unauthenticated /query path.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from gate import _reject_cross_site_query
from gate_auth import attach_identity_to_query, register_auth_routes
from utils.authn_manager import AuthManager

_GOOD_PASSWORD = "correct horse battery staple"
_ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
_PORT = 8787
_SAME_ORIGIN = f"http://localhost:{_PORT}"


def _cfg():
    return {
        "api": {"tls": {"enabled": False}, "port": _PORT},
        "security": {"allowed_hosts": _ALLOWED_HOSTS},
    }


async def _allow_all(_request: Request) -> None:
    return None


def _make_query_app(manager, cfg=None, limiter=None):
    app = FastAPI()
    events = []

    async def audit(event):
        events.append(event)

    @app.post("/query")
    async def query_endpoint(request: Request) -> dict:
        return {"username": getattr(request.state, "auth_username", None), "ok": True}

    identity = register_auth_routes(
        app, cfg or _cfg(), audit=audit, enforce_rate_limit=limiter or _allow_all, auth_manager=manager,
    )
    if manager is not None:
        attach_identity_to_query(app, identity)
    # Mirrors gate.py's real Stage 3 wiring exactly (see the comment there):
    # closes the CSRF/same-origin gap /query otherwise had relative to every
    # /auth/* route. Outside the manager branch, exactly as in gate.py -- a
    # cross-site check is not a credential, so it applies whether or not auth
    # is on, and auth-off is the configuration that used to carry no check.
    attach_identity_to_query(app, _reject_cross_site_query)
    app.state.audit_events = events
    return app


def _client(manager=None, cfg=None, limiter=None):
    cfg = cfg or _cfg()
    app = _make_query_app(manager, cfg=cfg, limiter=limiter)
    return TestClient(app, base_url=_SAME_ORIGIN)  # DevSkim: ignore DS162092,DS137138 - test loopback host


@pytest.fixture
def manager(tmp_path):
    m = AuthManager({"auth": {"enabled": True, "db_path": str(tmp_path / "auth.db")}})
    yield m
    m.close()


@pytest.fixture
def user(manager):
    manager.create_user("alice", _GOOD_PASSWORD)
    return "alice", _GOOD_PASSWORD


class TestQueryAuthDisabled:
    def test_query_works_without_credential(self):
        r = _client(None).post("/query", json={})
        assert r.status_code == 200
        assert r.json()["username"] is None


class TestQueryCrossSiteRejectedWithAuthOff:
    """The shipped default. Every case below used to be untested, because the
    cross-site check was attached only on the auth-enabled path -- so the one
    configuration CyClaw actually ships was the one nothing covered.

    A cross-site check is not a credential: with auth off /query still answers
    without one (TestQueryAuthDisabled above), and that stays true. What
    changes is that a request a browser LABELS as coming from another site no
    longer gets in on the shipped config.
    """

    def test_cross_site_origin_is_rejected(self):
        r = _client(None).post("/query", json={}, headers={"origin": "https://evil.example"})
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "CROSS_SITE_BLOCKED"

    def test_same_site_cross_port_is_rejected(self):
        r = _client(None).post("/query", json={}, headers={"sec-fetch-site": "same-site"})
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "CROSS_SITE_BLOCKED"

    def test_same_host_different_port_origin_is_rejected(self):
        """The gap the loopback-only Origin test left open until now.

        A browser that sends Origin but not Sec-Fetch-Site used to sail through
        on any loopback origin, port ignored -- so a page on another port of
        this same host was judged same-site by the one check whose stated
        purpose is stopping exactly that ride.
        """
        r = _client(None).post("/query", json={}, headers={"origin": "http://localhost:9999"})  # DevSkim: ignore DS162092,DS137138 - test loopback host
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "CROSS_SITE_BLOCKED"

    def test_same_origin_console_still_works(self):
        """The console posting to its own origin is the ordinary case."""
        r = _client(None).post("/query", json={}, headers={"origin": _SAME_ORIGIN, "sec-fetch-site": "same-origin"})
        assert r.status_code == 200

    def test_header_less_caller_still_works(self):
        """curl, PowerShell, schedulers and Telegram's client send neither
        header and are not CSRF vectors -- the absent-header allowance is the
        half of _looks_cross_site this change deliberately did not touch."""
        r = _client(None).post("/query", json={})
        assert r.status_code == 200


class TestQueryAuthEnabled:
    def test_missing_credential_is_401(self, manager, user):
        client = _client(manager)
        r = client.post("/query", json={})
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "AUTH_REQUIRED"
        # Finding 5 (PR #940 review): the rejection must reach the audit log
        # even though it raised inside the dependency, before the endpoint.
        rejected = [
            e for e in client.app.state.audit_events
            if e.get("event") == "auth_credential_rejected"
        ]
        assert len(rejected) == 1
        assert rejected[0]["path"] == "/query"

    def test_rejected_credential_is_rate_limited(self, manager, user):
        """Finding 2 (PR #940 review): /query's limiter lives in the endpoint
        body, which dependencies run before -- so the 401 path must enforce
        the budget itself. A spent budget answers 429, not 401."""
        from fastapi import HTTPException

        async def deny_all(_request: Request) -> None:
            raise HTTPException(status_code=429, detail={"code": "RATE_LIMIT"})

        r = _client(manager, limiter=deny_all).post("/query", json={})
        assert r.status_code == 429

    def test_session_cookie_works(self, manager, user):
        username, password = user
        client = _client(manager)
        login = client.post("/auth/login", json={"username": username, "password": password})
        assert login.status_code == 200
        r = client.post("/query", json={})
        assert r.status_code == 200
        assert r.json()["username"] == username

    def test_device_token_works(self, manager, user):
        username, _password = user
        token = manager.create_device_token(username, "telegram")
        r = _client(manager).post(
            "/query", json={}, headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["username"] == username

    def test_wrong_token_is_401(self, manager, user):
        r = _client(manager).post(
            "/query", json={}, headers={"Authorization": "Bearer not-a-real-token"},
        )
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "AUTH_REQUIRED"

    def test_api_key_shaped_bearer_is_not_a_device_token(self, manager, user):
        r = _client(manager).post(
            "/query", json={}, headers={"Authorization": "Bearer dummy-ops-key"},
        )
        assert r.status_code == 401


class TestQueryCrossSiteRejected:
    """Regression: /query used to carry no CSRF/same-origin check, so a
    same-site (different-port) page could ride the operator's session cookie
    -- SameSite=Strict blocks cross-SITE requests but not same-site
    cross-PORT ones. A real browser sends Sec-Fetch-Site on exactly that kind
    of request; a non-browser bearer/device-token caller sends neither header
    and must stay unaffected.
    """

    def test_same_site_cross_port_cookie_request_is_rejected(self, manager, user):
        username, password = user
        client = _client(manager)
        login = client.post("/auth/login", json={"username": username, "password": password})
        assert login.status_code == 200
        r = client.post("/query", json={}, headers={"sec-fetch-site": "same-site"})
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "CROSS_SITE_BLOCKED"

    def test_cross_site_cookie_request_is_rejected(self, manager, user):
        username, password = user
        client = _client(manager)
        login = client.post("/auth/login", json={"username": username, "password": password})
        assert login.status_code == 200
        r = client.post("/query", json={}, headers={"origin": "https://evil.example"})
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "CROSS_SITE_BLOCKED"

    def test_a_cross_site_request_with_no_credential_is_403_not_401(self, manager):
        """The only test that can catch the attach order being reversed.

        Each attach_identity_to_query call inserts at index 0 of the dependant
        tree, so the check attached LAST runs FIRST. Wiring the cross-site
        attach above the require_identity attach would flip identity ahead of
        it and turn this into a 401 -- and every other case in this class
        carries a valid credential, so none of them would notice. Rejecting on
        headers alone, before any credential work, is the same ordering every
        /auth/* route uses (_enforce_same_origin ahead of the identity dep).
        """
        r = _client(manager).post("/query", json={}, headers={"sec-fetch-site": "cross-site"})
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "CROSS_SITE_BLOCKED"

    def test_same_origin_cookie_request_still_works(self, manager, user):
        """Not a regression risk this change should introduce, but the whole
        point of the fix is that a legitimate same-origin caller is unaffected."""
        username, password = user
        client = _client(manager)
        login = client.post("/auth/login", json={"username": username, "password": password})
        assert login.status_code == 200
        r = client.post("/query", json={}, headers={"sec-fetch-site": "same-origin"})
        assert r.status_code == 200

    def test_a_forged_cross_site_header_is_rejected_even_with_a_valid_device_token(self, manager, user):
        """The check runs on headers alone, before identity is even resolved,
        so it rejects a cross-site-labeled request regardless of credential
        kind. Harmless in practice: a real device-token client (curl,
        PowerShell, Telegram's client) never sends Sec-Fetch-Site at all --
        see the no-headers case below, which is the realistic one."""
        username, _password = user
        token = manager.create_device_token(username, "telegram")
        r = _client(manager).post(
            "/query",
            json={},
            headers={"Authorization": f"Bearer {token}", "sec-fetch-site": "cross-site"},
        )
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "CROSS_SITE_BLOCKED"

    def test_device_token_caller_with_no_browser_headers_still_works(self, manager, user):
        """The realistic device-token case: no Sec-Fetch-Site/Origin at all
        (curl, PowerShell, Telegram's client) -- _looks_cross_site's absent-
        header allowance passes it straight through."""
        username, _password = user
        token = manager.create_device_token(username, "telegram")
        r = _client(manager).post(
            "/query", json={}, headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["username"] == username


class TestAttachFlipsProbe:
    def test_attach_makes_enforcement_probe_true(self, manager):
        import gate

        app = _make_query_app(manager)
        from unittest.mock import patch

        with patch.object(gate, "app", app):
            assert gate._request_path_enforcement_active() is True

    def test_unattached_query_keeps_probe_false(self):
        import gate
        from unittest.mock import patch

        app = FastAPI()

        @app.post("/query")
        def bare() -> dict:
            return {}

        with patch.object(gate, "app", app):
            assert gate._request_path_enforcement_active() is False
