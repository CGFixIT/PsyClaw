"""Security-response-header parity between the harness control plane and gate.py.

gate.py installs `_SecurityHeadersMiddleware` and stamps nosniff, X-Frame-Options,
Referrer-Policy, Permissions-Policy, X-Permitted-Cross-Domain-Policies and a CSP
on EVERY response (tests/test_gate.py::TestSecurityResponseHeaders pins it).
The harness app shipped only TrustedHostMiddleware, so its ~20 ``/api/*`` JSON
routes carried none of them -- on the more privileged of the two surfaces, since
those routes run checks, push branches, and open PRs, while the read-mostly
gateway on 8787 was fully hardened.

The console page keeps its own, laxer CSP on purpose: ``static/harness.html``
embeds an inline ``<script>`` (``terminal.html`` does not -- it loads
``/static/terminal.js``), so gate.py's ``script-src 'self'`` would render the
harness console blank. The middleware uses ``setdefault``, so the handler's
explicit header wins and only the JSON API inherits the strict
``default-src 'none'``.
"""

from __future__ import annotations

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


@pytest.mark.parametrize("path", ["/", "/api/status", "/api/registry", "/api/sessions"])
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


def test_console_keeps_its_own_csp_because_harness_html_has_an_inline_script(client):
    """The GET / handler's explicit CSP must survive the middleware's setdefault.

    If this ever inverts, the console renders blank: static/harness.html carries
    an inline <script> block, which default-src 'none' forbids.
    """
    resp = client.get("/")
    csp = resp.headers.get("content-security-policy", "")
    assert "frame-ancestors 'none'" in csp
    assert "default-src 'none'" not in csp, (
        "the console page must not inherit the API's default-src 'none' -- "
        "static/harness.html embeds an inline <script> and would render blank"
    )
    # The page's own Cache-Control (it carries a per-process CSRF token) must
    # likewise survive.
    assert "no-store" in resp.headers.get("cache-control", "")


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
