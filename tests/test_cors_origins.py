"""#1298 N9: shipped CORS origins must name an explicit port.

A scheme+host with no port is implicit :80/:443 — a different browser origin
from the console on :8787. Portless rows made a page on loopback :80 a
CORS-simple GET to unauthenticated /health.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parents[1]
_PORT_SUFFIX = re.compile(r":\d+$")


def test_shipped_allowed_origins_all_include_an_explicit_port() -> None:
    cfg = yaml.safe_load((_ROOT / "config.yaml").read_text(encoding="utf-8"))
    origins = cfg["security"]["allowed_origins"]
    assert origins, "allowed_origins must not be empty"
    for origin in origins:
        assert isinstance(origin, str) and _PORT_SUFFIX.search(origin), (
            f"portless CORS origin {origin!r} (#1298 N9)"
        )


def test_portless_loopback_origin_cannot_read_health() -> None:
    import gate

    client = TestClient(gate.app, base_url="http://127.0.0.1:8787")
    resp = client.get(
        "/health",
        headers={"Origin": "http://127.0.0.1", "Host": "127.0.0.1:8787"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") is None


def test_console_origin_can_read_health() -> None:
    import gate

    client = TestClient(gate.app, base_url="http://127.0.0.1:8787")
    resp = client.get(
        "/health",
        headers={"Origin": "http://127.0.0.1:8787", "Host": "127.0.0.1:8787"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://127.0.0.1:8787"
