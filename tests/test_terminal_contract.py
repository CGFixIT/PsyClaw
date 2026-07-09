"""Contract tests between the web console (static/terminal.html) and gate.py.

The console is a static file served by the gateway itself, so nothing ties its
fetch() targets to gate.py's registered routes: a renamed or removed endpoint
only fails at runtime in a browser, invisible to pytest and CI. These tests
extract every gateway path the console calls — direct `${API}/...` fetch
targets plus callOps('/ops/...') invocations — and assert each one exists on
gate.app with the HTTP method the console uses.

Runs entirely offline against the imported FastAPI app (no server, no LLM).
"""

import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

import gate

_TERMINAL_HTML = Path(gate.__file__).resolve().parent / "static" / "terminal.html"

# Paths the console calls with POST (everything else it calls with GET).
_POST_PATHS = {
    "/query", "/soul/reload", "/soul/propose", "/soul/apply", "/soul/restore",
    "/ops/sync", "/ops/agentic", "/ops/fsconnect", "/ops/sqlconnect",
}


def _console_paths() -> set[str]:
    html = _TERMINAL_HTML.read_text(encoding="utf-8")
    # Direct fetch targets: `${API}/health`, `${API}/soul/reload`, ...
    paths = set(re.findall(r"\$\{API\}(/[A-Za-z0-9_/-]+)", html))
    # Indirect ops targets: callOps('/ops/sync', {...}) -> fetch(`${API}${path}`)
    paths |= set(re.findall(r"callOps\('(/[A-Za-z0-9_/-]+)'", html))
    return paths


def _gate_routes() -> dict[str, set[str]]:
    return {r.path: set(r.methods or ()) for r in gate.app.routes if isinstance(r, APIRoute)}


def test_console_path_extraction_is_not_empty():
    """Regex-rot guard: if terminal.html's fetch idiom changes and extraction
    breaks, this fails loudly instead of the per-path tests passing vacuously."""
    paths = _console_paths()
    assert len(paths) >= 10, f"extracted only {sorted(paths)} from terminal.html"
    assert "/health" in paths
    assert "/query" in paths
    assert any(p.startswith("/ops/") for p in paths)


def test_online_confirm_buttons_send_explicit_provider():
    html = _TERMINAL_HTML.read_text(encoding="utf-8")
    assert "handleConfirm(true, id, 'grok')" in html
    assert "handleConfirm(true, id, 'claude')" in html
    assert "body.online_provider = onlineProvider" in html
    assert "Escalating to ${providerLabel}" in html


@pytest.mark.parametrize("path", sorted(_console_paths()))
def test_console_endpoint_exists_on_gateway(path):
    routes = _gate_routes()
    assert path in routes, (
        f"static/terminal.html calls {path!r} but gate.py registers no such "
        f"route — the console would get a 404 at runtime"
    )


@pytest.mark.parametrize("path", sorted(_console_paths()))
def test_console_endpoint_accepts_console_method(path):
    routes = _gate_routes()
    if path not in routes:
        pytest.skip("missing route reported by test_console_endpoint_exists_on_gateway")
    method = "POST" if path in _POST_PATHS else "GET"
    assert method in routes[path], (
        f"console calls {method} {path} but the route only allows "
        f"{sorted(routes[path])} — the console would get a 405 at runtime"
    )
