"""#1298 N9: shipped CORS origins must name an explicit port.

A scheme+host with no port is implicit :80/:443 — a different browser origin
from the console on :8787. Portless rows made a page on loopback :80 a
CORS-simple GET to unauthenticated /health (corpus_path) and /index/status.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parents[1]
_PORT_SUFFIX = re.compile(r":\d+$")
_PATHS = ("/health", "/index/status")
_PORTLESS = (
    "http://127.0.0.1",
    "http://localhost",
)
_CONSOLE = "http://127.0.0.1:8787"


def test_shipped_allowed_origins_all_include_an_explicit_port() -> None:
    cfg = yaml.safe_load((_ROOT / "config.yaml").read_text(encoding="utf-8"))
    origins = cfg["security"]["allowed_origins"]
    assert origins, "allowed_origins must not be empty"
    for origin in origins:
        assert isinstance(origin, str) and _PORT_SUFFIX.search(origin), (
            f"portless CORS origin {origin!r} (#1298 N9)"
        )
        assert origin not in _PORTLESS


@pytest.mark.parametrize("path", _PATHS)
@pytest.mark.parametrize("origin", _PORTLESS)
def test_portless_loopback_origin_gets_no_acao(path: str, origin: str) -> None:
    import gate

    client = TestClient(gate.app, base_url=_CONSOLE)
    resp = client.get(path, headers={"Origin": origin})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") is None


@pytest.mark.parametrize("path", _PATHS)
def test_console_origin_gets_acao(path: str) -> None:
    import gate

    client = TestClient(gate.app, base_url=_CONSOLE)
    resp = client.get(path, headers={"Origin": _CONSOLE})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == _CONSOLE


@pytest.mark.parametrize("path", _PATHS)
def test_headerless_caller_still_200(path: str) -> None:
    import gate

    client = TestClient(gate.app, base_url=_CONSOLE)
    resp = client.get(path)
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") is None
