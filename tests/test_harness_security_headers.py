"""Security-response-header parity between the harness control plane and gate.py.

gate.py installs `_SecurityHeadersMiddleware` and stamps nosniff, X-Frame-Options,
Referrer-Policy, Permissions-Policy, X-Permitted-Cross-Domain-Policies and a CSP
on EVERY response (tests/test_gate.py::TestSecurityResponseHeaders pins it).
The harness app shipped only TrustedHostMiddleware, so its ~20 ``/api/*`` JSON
routes carried none of them -- on the more privileged of the two surfaces, since
those routes run checks, push branches, and open PRs, while the read-mostly
gateway on 8787 was fully hardened.

The console page keeps its own CSP on purpose, but a STRICTER one than gate.py's,
not a laxer one: ``static/harness.html`` embeds an inline ``<script>`` and an
inline ``<style>`` (``terminal.html`` does neither -- it loads
``/static/terminal.js``), so the handler mints a fresh nonce per response and
substitutes it into both the header and those two tags. A middleware cannot do
that -- it stamps one fixed string -- which is why ``setdefault`` still matters:
the handler's header must win. Everything else, the JSON API and the ``/static``
assets alike, inherits the strict ``default-src 'none'`` default.

The nonce is per RESPONSE, not per process. One reused across responses is
replayable by whatever markup an XSS bug injects, which is the whole thing a
nonce exists to stop, so ``test_console_csp_nonce_is_fresh_per_response`` pins
the rotation as hard as it pins the policy itself.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import harness.server as harness_server
from harness.config import HarnessConfig
from harness.ollama import HarnessChatClient

# The exact set gate.py stamps. Kept as literals rather than imported from
# gate.py: importing gate pulls in the full app init (ChromaDB, retriever), and
# the point of this module is that the two apps agree by contract, not by
# sharing an object.
REQUIRED_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
    "x-permitted-cross-domain-policies": "none",
}


def _nonce_from_csp(csp: str) -> str:
    """The base64url value out of a `'nonce-...'` source expression, or ''."""
    match = re.search(r"'nonce-([A-Za-z0-9_-]+)'", csp)
    return match.group(1) if match else ""


def _chat() -> HarnessChatClient:
    return SimpleNamespace(  # type: ignore[return-value]
        model="qwen3.8:27b-mlx",
        base_url="http://127.0.0.1:11434/v1",
        available=lambda: False,
        close=lambda: None,
    )


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("CYCLAW_HOME", str(tmp_path / ".CyClaw"))
    return HarnessConfig.load()


@pytest.fixture
def client(cfg, monkeypatch):
    monkeypatch.setenv("CYCLAW_API_KEY", "header-test-key")
    return TestClient(harness_server.create_app(cfg, _chat()), base_url="http://127.0.0.1")


@pytest.mark.parametrize(
    "path",
    ["/", "/api/status", "/api/registry", "/api/sessions", "/static/auth_admin.js"],
)
def test_every_response_carries_the_hardening_headers(client, path):
    resp = client.get(path)
    assert resp.status_code == 200, f"{path} did not answer 200"
    for header, expected in REQUIRED_HEADERS.items():
        assert resp.headers.get(header) == expected, f"{path} missing or wrong {header}"


def test_api_responses_get_a_strict_csp(client):
    """A JSON document loads no subresources, so default-src 'none' is free here."""
    csp = client.get("/api/status").headers.get("content-security-policy", "")
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "base-uri 'none'" in csp


def test_console_csp_is_strict_and_nonce_based(client):
    """The GET / handler's explicit CSP must survive the middleware's setdefault.

    The console is the more privileged surface, so it carries a real policy --
    not just framing control. Anything weaker here and a future XSS bug on this
    page has no CSP standing between it and the routes that push branches.
    """
    resp = client.get("/")
    csp = resp.headers.get("content-security-policy", "")
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "base-uri 'none'" in csp
    assert "form-action 'none'" in csp
    assert "connect-src 'self'" in csp
    # 'self' keeps /static/auth_admin.js loadable; the nonce covers the inline
    # block. Losing either breaks the console rather than failing quietly.
    assert "script-src 'self' 'nonce-" in csp
    assert "style-src 'nonce-" in csp
    # The page's own Cache-Control (it carries a per-process CSRF token and a
    # single-response nonce) must likewise survive.
    assert "no-store" in resp.headers.get("cache-control", "")


def test_console_csp_nonce_matches_the_markup(client):
    """A nonce only works if the header and the tags carry the same value."""
    resp = client.get("/")
    header_nonce = _nonce_from_csp(resp.headers.get("content-security-policy", ""))
    assert header_nonce, "GET / advertised no nonce in its CSP"
    assert f'<script nonce="{header_nonce}">' in resp.text
    assert f'<style nonce="{header_nonce}">' in resp.text
    assert harness_server._CSP_NONCE_PLACEHOLDER not in resp.text, (
        "the literal placeholder reached the browser -- substitution did not run"
    )


def test_console_csp_nonce_is_fresh_per_response(client):
    """Per response, not per process.

    A nonce that outlives one response is replayable by any markup an injection
    manages to place, which defeats the point of having one at all.
    """
    first = _nonce_from_csp(client.get("/").headers.get("content-security-policy", ""))
    second = _nonce_from_csp(client.get("/").headers.get("content-security-policy", ""))
    assert first and second
    assert first != second


def test_static_assets_inherit_the_strict_default_csp(client):
    """The mounted assets set no policy of their own, so they take the API's."""
    csp = client.get("/static/auth_admin.js").headers.get("content-security-policy", "")
    assert "default-src 'none'" in csp


def test_error_responses_are_hardened_too(client):
    """gate.py stamps its headers outermost so errors carry them; match that."""
    resp = client.get("/api/definitely-not-a-route")
    assert resp.status_code == 404
    for header, expected in REQUIRED_HEADERS.items():
        assert resp.headers.get(header) == expected, f"404 missing or wrong {header}"


def test_rejected_host_still_carries_the_headers(client):
    """The middleware is outermost, so it wraps the TrustedHost 400 as well."""
    resp = client.get("/api/status", headers={"Host": "evil.example.com"})
    assert resp.status_code == 400
    assert resp.headers.get("x-content-type-options") == "nosniff"


def test_unauthenticated_guarded_route_is_hardened(client):
    """A 401 is the most likely response an attacker sees; it must be hardened."""
    resp = client.post("/api/model", json={"model": "qwen3.8:27b-mlx"})
    assert resp.status_code in (401, 403)
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options") == "DENY"
