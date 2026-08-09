"""FastAPI TestClient tests for gate_memory routes."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gate_memory import register_memory_routes


@pytest.fixture
def api_key(monkeypatch):
    key = "test-memory-key-32chars-minimum!!"
    monkeypatch.setenv("CYCLAW_API_KEY", key)
    return key


@pytest.fixture
def memory_app(tmp_path: Path, api_key: str):
    cfg = {
        "memory": {
            "enabled": True,
            "db_path": str(tmp_path / "mem.db"),
            "facts": {"enabled": True, "max_content_chars": 8192},
            "episodes": {"enabled": True, "store_raw_query": False, "max_answer_summary_chars": 200},
            "retrieval_fusion": {"enabled": False},
            "propose_apply": {"enabled": True},
            "export_html": {"enabled": True},
            "consolidation": {"enabled": False},
        },
        "policy": {
            "prompt_filter": {"banned_patterns": ["ignore previous instructions"]},
            "privacy": {"redact_emails": True, "redact_ips": True, "redact_secrets_like": []},
        },
    }
    app = FastAPI()
    audit = AsyncMock()

    async def _rl():
        return None

    def require_api_key(request=None):  # simplified — TestClient passes header check below
        from fastapi import Header, HTTPException

        # real dependency style
        return None

    # Use a real fail-closed dependency matching gate style
    from fastapi import Depends, Header, HTTPException

    async def _require(authorization: str | None = Header(default=None)):
        expected = os.environ.get("CYCLAW_API_KEY") or ""
        if not expected:
            raise HTTPException(status_code=401, detail="API key not configured")
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing bearer token")
        token = authorization.removeprefix("Bearer ").strip()
        if token != expected:
            raise HTTPException(status_code=401, detail="Invalid API key")

    register_memory_routes(
        app,
        cfg=cfg,
        audit=audit,
        enforce_rate_limit=_rl,
        require_api_key=_require,
    )
    return app, cfg, api_key, audit


def test_status_without_key_401(memory_app):
    app, _cfg, _key, _audit = memory_app
    client = TestClient(app)
    assert client.get("/memory/status").status_code == 401


def test_status_and_propose_apply(memory_app):
    app, _cfg, key, audit = memory_app
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {key}"}

    st = client.get("/memory/status", headers=headers)
    assert st.status_code == 200
    assert st.json()["enabled"] is True

    prop = client.post(
        "/memory/propose",
        headers=headers,
        json={
            "action": "add_fact",
            "content": "User prefers dark mode",
            "category": "prefs",
            "tags": ["ui"],
            "reason": "operator observed preference",
        },
    )
    assert prop.status_code == 200, prop.text
    pid = prop.json()["id"]

    applied = client.post(
        "/memory/apply",
        headers=headers,
        json={"proposal_id": pid, "reason": "confirmed by operator"},
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["status"] == "applied"

    facts = client.get("/memory/facts", headers=headers)
    assert facts.status_code == 200
    assert any("dark mode" in f["content"] for f in facts.json()["facts"])

    html = client.get("/query/export/html", headers=headers)
    assert html.status_code == 200
    assert "text/html" in html.headers["content-type"]


def test_disabled_master_404_on_facts(tmp_path, api_key):
    cfg = {
        "memory": {
            "enabled": False,
            "db_path": str(tmp_path / "x.db"),
            "facts": {"enabled": False},
            "episodes": {"enabled": False},
            "retrieval_fusion": {"enabled": False},
            "propose_apply": {"enabled": False},
            "export_html": {"enabled": False},
            "consolidation": {"enabled": False},
        },
        "policy": {"prompt_filter": {"banned_patterns": []}, "privacy": {}},
    }
    app = FastAPI()

    async def _rl():
        return None

    from fastapi import Header, HTTPException
    import os

    async def _require(authorization: str | None = Header(default=None)):
        expected = os.environ.get("CYCLAW_API_KEY") or ""
        if not authorization or authorization.removeprefix("Bearer ").strip() != expected:
            raise HTTPException(status_code=401, detail="no")

    register_memory_routes(app, cfg=cfg, audit=AsyncMock(), enforce_rate_limit=_rl, require_api_key=_require)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {api_key}"}
    # status still 200 with flags
    assert client.get("/memory/status", headers=headers).status_code == 200
    assert client.get("/memory/status", headers=headers).json()["enabled"] is False
    assert client.get("/memory/facts", headers=headers).status_code == 404


def test_blank_reason_rejected_by_schema(memory_app):
    app, _cfg, key, _audit = memory_app
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {key}"}
    r = client.post(
        "/memory/propose",
        headers=headers,
        json={"action": "add_fact", "content": "x", "reason": ""},
    )
    assert r.status_code == 422
