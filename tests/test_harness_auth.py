"""Tests for the harness control plane's API-key auth and cross-site guard.

The harness previously had no authentication at all -- loopback bind plus a Host
check, and nothing else. That is defensible for a chat console; it is not
defensible once a route can approve an agent's write. These tests pin the gate:
which routes it covers, that it fails closed, the dependency ordering that keeps
key-guessing throttled, and the cross-site rejection that a Host check cannot
provide on its own.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

# Module-object import only (not `from harness.server import create_app` as well):
# monkeypatching _rate_limit_settings needs the module, and importing the same
# module both ways trips CodeQL's py/import-and-import-from.
import harness.server as harness_server
from harness.config import HarnessConfig
from harness.ollama import HarnessChatClient

_KEY = "harness-auth-test-key"
_AUTH = {"Authorization": f"Bearer {_KEY}"}

# Every route the auth gate is supposed to cover, with a body that would succeed
# if the request got past the dependency chain.
GUARDED = [
    ("post", "/api/sessions", {"title": "t"}),
    ("post", "/api/sessions/does-not-exist/rename", {"title": "t"}),
    ("post", "/api/soul", {"enabled": True}),
    ("post", "/api/model", {"model": "qwen2.5:7b"}),
    ("post", "/api/chat", {"message": "hi"}),
    ("get", "/api/github/status", None),
    # The agentic coding routes. The decision route is guarded like the rest --
    # the limiter runs first in the chain precisely so it bounds key guessing,
    # and the route that can commit is the last one to exempt from that.
    ("post", "/api/agent/run", {
        "instruction": "do a thing", "branch": "claude/x", "commit_message": "m", "reason": "r",
    }),
    ("get", "/api/agent/runs/" + "0" * 32, None),
    ("post", "/api/agent/runs/" + "0" * 32 + "/decision", {"decision": "reject"}),
    # The two escalation routes past approve. Both are gated disarmed inside
    # agentic/ as well, but that is not why they are guarded here: an
    # unauthenticated caller must not be able to probe run state or spend a
    # subprocess, regardless of whether the underlying capability is armed.
    ("post", "/api/agent/runs/" + "0" * 32 + "/push", {}),
    ("post", "/api/agent/runs/" + "0" * 32 + "/publish", {"reason": "r", "confirm": True}),
]

# Left open on purpose: the console must be able to boot and tell the operator it
# needs a key. None of these changes state or spends a subprocess.
# /api/agent/checks joins them: it lists the names of an allow-list this repo
# hardcodes, spawns nothing, and the console needs it to populate help before a
# key has been entered.
OPEN = [
    "/", "/api/status", "/api/registry", "/api/sessions", "/api/harness/runs", "/api/agent/checks",
]


def _chat() -> HarnessChatClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "model": "qwen2.5:7b",
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })

    return HarnessChatClient(
        base_url="http://127.0.0.1:11434/v1", model="qwen2.5:7b", transport=httpx.MockTransport(handler)
    )


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("CYCLAW_HOME", str(tmp_path / ".CyClaw"))
    return HarnessConfig.load()


@pytest.fixture(autouse=True)
def _no_real_subprocess(monkeypatch):
    """Keep the auth tests from actually running `python -m agentic.cli`.

    Four guarded routes reach the ops shim, and test_guarded_route_accepts_the
    _right_key deliberately lets a request through the whole chain. Unstubbed
    those spawn real subprocesses -- harmless only because the shipped config
    has agentic.enabled false, which makes the suite's cost and safety depend
    on a config value none of these tests are about. Auth runs as a dependency
    before the route body, so stubbing here removes the subprocess without
    weakening a single assertion.
    """
    monkeypatch.setattr(
        harness_server, "run_agentic_op",
        lambda action, **_kwargs: SimpleNamespace(to_dict=lambda: {"ok": True, "action": action}),
    )


@pytest.fixture()
def client(cfg, monkeypatch):
    monkeypatch.setenv("CYCLAW_API_KEY", _KEY)
    return TestClient(harness_server.create_app(cfg, _chat()), base_url="http://127.0.0.1")


def _call(client, method, path, body, **kwargs):
    if method == "get":
        return client.get(path, **kwargs)
    return client.post(path, json=body, **kwargs)


# --- coverage --------------------------------------------------------------


@pytest.mark.parametrize(("method", "path", "body"), GUARDED)
def test_guarded_route_rejects_a_missing_key(client, method, path, body):
    resp = _call(client, method, path, body)
    assert resp.status_code == 401
    detail = resp.json()["detail"]
    # The _err() envelope shape, not gate.py's bare string: static/harness.html's
    # fetch helper reads detail.message, and a string detail renders as an opaque
    # "HTTP 401" with no hint that a key is needed.
    assert detail["code"] == "UNAUTHORIZED"
    assert detail["message"]


@pytest.mark.parametrize(("method", "path", "body"), GUARDED)
def test_guarded_route_rejects_a_wrong_key(client, method, path, body):
    resp = _call(client, method, path, body, headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


@pytest.mark.parametrize(("method", "path", "body"), GUARDED)
def test_guarded_route_accepts_the_right_key(client, method, path, body):
    resp = _call(client, method, path, body, headers=_AUTH)
    # 404 for the deliberately-absent session; anything but 401/403 proves the
    # dependency chain was satisfied.
    assert resp.status_code not in (401, 403)


@pytest.mark.parametrize("path", OPEN)
def test_open_route_needs_no_key(client, path):
    assert client.get(path).status_code == 200


# --- fail-closed -----------------------------------------------------------


def test_unset_env_var_refuses_rather_than_opens(cfg, monkeypatch):
    """The regression this exists for: an unset key must not mean "no auth required"."""
    monkeypatch.delenv("CYCLAW_API_KEY", raising=False)
    c = TestClient(harness_server.create_app(cfg, _chat()), base_url="http://127.0.0.1")
    resp = c.post("/api/soul", json={"enabled": True}, headers=_AUTH)
    assert resp.status_code == 401
    assert resp.json()["detail"]["details"]["reason"] == "key_not_configured"


def test_empty_env_var_is_treated_as_unset(cfg, monkeypatch):
    monkeypatch.setenv("CYCLAW_API_KEY", "")
    c = TestClient(harness_server.create_app(cfg, _chat()), base_url="http://127.0.0.1")
    assert c.post("/api/soul", json={"enabled": True}).status_code == 401


def test_non_ascii_credential_is_a_401_not_a_500(monkeypatch):
    """hmac.compare_digest raises TypeError on a non-ASCII str operand.

    Starlette decodes the Authorization header latin-1, so a pasted curly quote
    or accented character would escape the handler as an unhandled 500 -- breaking
    the fail-closed promise -- unless the comparison is done on encoded bytes.

    Exercised against the verifier directly rather than over HTTP: httpx encodes
    header values as ASCII and refuses to send the request at all, so the client
    cannot reach the code path this guards.
    """
    from fastapi import HTTPException
    from fastapi.security import HTTPAuthorizationCredentials

    from utils.auth import require_api_key

    monkeypatch.setenv("CYCLAW_API_KEY", _KEY)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="k“ey”")
    with pytest.raises(HTTPException) as excinfo:
        require_api_key(creds)
    assert excinfo.value.status_code == 401


# --- ordering --------------------------------------------------------------


def test_rate_limit_runs_before_auth(cfg, monkeypatch):
    """A wrong key against a spent budget must return 429, not 401.

    If auth ran first, the limiter would never bound key-guessing on these routes.
    Mirrors the ordering gate.py pins in tests/test_gate.py.
    """
    monkeypatch.setenv("CYCLAW_API_KEY", _KEY)
    monkeypatch.setattr(harness_server, "_rate_limit_settings", lambda: {"max_requests": 1, "window_seconds": 60})
    c = TestClient(harness_server.create_app(cfg, _chat()), base_url="http://127.0.0.1")

    assert c.post("/api/chat", json={"message": "one"}, headers=_AUTH).status_code == 200
    assert c.post("/api/chat", json={"message": "two"}, headers={"Authorization": "Bearer wrong"}).status_code == 429


def test_rate_limit_body_is_renderable_by_the_console(cfg, monkeypatch):
    """The 429 detail used key "error"; the console's fetch helper reads "message"."""
    monkeypatch.setenv("CYCLAW_API_KEY", _KEY)
    monkeypatch.setattr(harness_server, "_rate_limit_settings", lambda: {"max_requests": 1, "window_seconds": 60})
    c = TestClient(harness_server.create_app(cfg, _chat()), base_url="http://127.0.0.1")

    c.post("/api/chat", json={"message": "one"}, headers=_AUTH)
    detail = c.post("/api/chat", json={"message": "two"}, headers=_AUTH).json()["detail"]
    assert detail["code"] == "RATE_LIMIT"
    assert "Rate limit exceeded" in detail["message"]


# --- cross-site ------------------------------------------------------------


def test_cross_origin_post_is_rejected_even_with_a_valid_key(client):
    """The gap TrustedHostMiddleware cannot close.

    A page on any origin can send a CORS "simple request" POST to a loopback
    server: no preflight, and a legitimate 127.0.0.1 Host header. The browser
    does stamp Origin, which is what this rejects.
    """
    resp = client.post(
        "/api/soul", json={"enabled": True}, headers={**_AUTH, "Origin": "http://evil.example"}
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "CROSS_ORIGIN_BLOCKED"


def test_cross_site_fetch_metadata_is_rejected(client):
    resp = client.post(
        "/api/soul", json={"enabled": True}, headers={**_AUTH, "Sec-Fetch-Site": "cross-site"}
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "CROSS_SITE_BLOCKED"


@pytest.mark.parametrize("origin", ["http://127.0.0.1:8790", "http://localhost:8790", "http://[::1]:8790"])
def test_loopback_origin_is_allowed(client, origin):
    resp = client.post("/api/soul", json={"enabled": True}, headers={**_AUTH, "Origin": origin})
    assert resp.status_code == 200


@pytest.mark.parametrize("site", ["same-origin", "none"])
def test_same_origin_fetch_metadata_is_allowed(client, site):
    resp = client.post("/api/soul", json={"enabled": True}, headers={**_AUTH, "Sec-Fetch-Site": site})
    assert resp.status_code == 200


def test_absent_headers_are_allowed(client):
    """curl, PowerShell, and the sandbox verifier send neither header.

    A non-browser client is not a CSRF vector, and every browser that can mount
    the attack sends at least Origin on a cross-origin POST. Rejecting on absence
    would break every scripted operator tool for no security gain.
    """
    assert client.post("/api/soul", json={"enabled": True}, headers=_AUTH).status_code == 200


# --- parity with gate.py ---------------------------------------------------


def test_matches_gate_auth_semantics():
    """utils/auth.py is a deliberate second implementation of gate.py's check.

    harness/ cannot import gate (tests/test_harness_isolation.py), and refactoring
    gate.py onto a shared module would break four source-text checks that grep
    gate.py itself. This asserts the two cannot drift on the properties that
    matter: same env var, constant-time comparison, fail-closed on unset.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    # Read both as TEXT rather than importing utils.auth: the file is already
    # imported via `from utils.auth import require_api_key` elsewhere in this
    # module, and importing it a second way trips CodeQL's py/import-and-import-from.
    src = (root / "utils" / "auth.py").read_text(encoding="utf-8")
    gate_src = (root / "gate.py").read_text(encoding="utf-8")

    for module_src in (src, gate_src):
        assert "CYCLAW_API_KEY" in module_src
        assert "hmac.compare_digest" in module_src
        assert "401" in module_src
    # Both compare encoded bytes, not str -- see the non-ASCII test above.
    assert 'encode("utf-8")' in src
    assert 'encode("utf-8")' in gate_src
