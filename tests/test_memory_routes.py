"""FastAPI TestClient tests for gate_memory routes."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from gate_memory import register_memory_routes
from memory.store import apply_proposal, get_fact, insert_fact
from schemas.api import MemoryProposeRequest


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
            "facts": {"enabled": True, "max_content_chars": 8192, "max_active": 10000},
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


def test_negative_limit_does_not_bypass_the_pagination_cap(memory_app):
    """Issue #1000: /memory/facts and /memory/episodes capped `limit` only on
    the upper bound (`min(limit, 500)`), so a negative value passed through
    unchanged and SQLite's `LIMIT` treats a negative number as unbounded --
    the entire table came back in one response. `offset` already had a floor
    (`max(offset, 0)`); `limit` now gets the same treatment."""
    app, cfg, key, _audit = memory_app
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {key}"}

    for i in range(3):
        insert_fact(cfg, f"fact number {i}", reason="seed")

    unclamped = client.get("/memory/facts", headers=headers)
    assert unclamped.status_code == 200
    assert len(unclamped.json()["facts"]) == 3

    negative = client.get("/memory/facts?limit=-1", headers=headers)
    assert negative.status_code == 200
    assert negative.json()["facts"] == []


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


def test_content_only_update_preserves_metadata(memory_app):
    """Codex P1: content-only update must not clobber category/tags/confidence."""
    app, cfg, key, _audit = memory_app
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {key}"}

    fact = insert_fact(
        cfg,
        "Original content about shell",
        category="prefs",
        tags=["shell", "zsh"],
        confidence=0.7,
        reason="seed",
    )

    prop = client.post(
        "/memory/propose",
        headers=headers,
        json={
            "action": "update_fact",
            "fact_id": fact.id,
            "content": "Updated content about shell",
            "reason": "content-only edit",
        },
    )
    assert prop.status_code == 200, prop.text
    payload = prop.json()["payload"]
    assert "content" in payload
    assert "category" not in payload
    assert "tags" not in payload
    assert "confidence" not in payload

    applied = client.post(
        "/memory/apply",
        headers=headers,
        json={"proposal_id": prop.json()["id"], "reason": "apply content-only"},
    )
    assert applied.status_code == 200, applied.text
    updated = get_fact(cfg, fact.id)
    assert updated is not None
    assert updated.content == "Updated content about shell"
    assert updated.category == "prefs"
    assert updated.tags == ["shell", "zsh"]
    assert updated.confidence == pytest.approx(0.7)


def test_proposal_payload_builder_omits_defaults_on_update():
    req = MemoryProposeRequest(
        action="update_fact",
        fact_id=3,
        content="only content",
        reason="r",
    )
    from gate_memory import _proposal_payload

    payload = _proposal_payload(req)
    assert payload == {"fact_id": 3, "content": "only content"}


def test_status_error_does_not_leak_raw_exception_text(memory_app):
    """A failing memory store must not echo filesystem paths or schema details
    into the /memory/status JSON payload."""
    app, _cfg, key, audit = memory_app
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {key}"}

    with patch(
        "memory.mirror.count_active_facts",
        side_effect=sqlite3.OperationalError("no such table: facts at /secret/path.db"),
    ):
        st = client.get("/memory/status", headers=headers)
    assert st.status_code == 200
    body = st.json()
    assert "error" in body
    assert "/secret/path.db" not in body["error"]
    assert "OperationalError" in body["error"]
