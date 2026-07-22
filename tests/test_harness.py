"""Tests for the out-of-band PowerShell coding harness (``harness/``).

No live services: the chat client is exercised over an httpx MockTransport,
and the FastAPI app is tested via TestClient against a tmp-path harness home.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from harness.config import HarnessConfig, default_home
from harness.ollama import HarnessChatClient, HarnessLLMError
from harness.prompts import _strip_frontmatter, compose_system_prompt
from harness.registry_view import full_registry, list_mcp_tools, list_repo_skills
from harness.server import create_app
from harness.sessions import SessionStore, SessionStoreError


# -- fixtures -------------------------------------------------------------------

def _mock_transport(reply: str = "ok", prompt_tokens: int = 11, completion_tokens: int = 7):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "model": "qwen2.5:7b",
            "choices": [{"message": {"role": "assistant", "content": reply}}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        })

    return httpx.MockTransport(handler)


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("CYCLAW_HOME", str(tmp_path / ".CyClaw"))
    return HarnessConfig.load()


@pytest.fixture()
def client(cfg):
    chat = HarnessChatClient(
        base_url="http://127.0.0.1:11434/v1", model="qwen2.5:7b", transport=_mock_transport()
    )
    return TestClient(create_app(cfg, chat))


# -- config ---------------------------------------------------------------------

def test_home_prefers_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("CYCLAW_HOME", str(tmp_path / "custom"))
    assert default_home() == tmp_path / "custom"


def test_home_defaults_to_userprofile(monkeypatch):
    monkeypatch.delenv("CYCLAW_HOME", raising=False)
    monkeypatch.setenv("USERPROFILE", r"C:\Users\tester")
    assert default_home() == Path(r"C:\Users\tester") / ".CyClaw"


def test_layout_created_and_config_seeded(cfg):
    for sub in ("sessions", "skills", "tools", "memory"):
        assert (cfg.home / sub).is_dir()
    assert cfg.config_path.exists()
    # repo skills seeded into the home catalog
    assert (cfg.skills_dir / "ponytail" / "SKILL.md").exists()


def test_config_roundtrip_persists_toggles(cfg):
    cfg.soul_enabled = False
    cfg.selected_model = "llama3.1:8b"
    cfg.save()
    reloaded = HarnessConfig.load(cfg.home)
    assert reloaded.soul_enabled is False
    assert reloaded.selected_model == "llama3.1:8b"


# -- prompts ---------------------------------------------------------------------

def test_strip_frontmatter_drops_yaml_block():
    assert _strip_frontmatter("---\nname: x\n---\n\nBody here.\n") == "Body here."


def test_system_prompt_contains_both_discipline_skills():
    prompt = compose_system_prompt(soul_enabled=False)
    assert "ponytail" in prompt
    assert "karpathy-guidelines" in prompt
    assert "YAGNI" in prompt  # ponytail rule 1 marker


def test_system_prompt_soul_toggle():
    with_soul = compose_system_prompt(soul_enabled=True)
    without = compose_system_prompt(soul_enabled=False)
    assert "soul" not in without.lower() or len(without) < len(with_soul)


# -- registry --------------------------------------------------------------------

def test_repo_skills_include_ponytail_and_karpathy():
    names = {s["name"] for s in list_repo_skills()}
    assert "ponytail" in names
    assert "karpathy-guidelines" in names


def test_mcp_tools_parsed_without_import():
    tools = list_mcp_tools()
    assert any(t["name"] == "hybrid_search" for t in tools)


def test_full_registry_shape():
    reg = full_registry()
    assert set(reg) == {"skills", "tools", "connectors"}
    assert any(c["id"] == "github" for c in reg["connectors"])


# -- sessions ---------------------------------------------------------------------

def test_session_store_roundtrip(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    s = store.create(model="m", title="t1")
    updated = store.record_exchange(
        s.session_id, user_text="hi", assistant_text="yo", model="m",
        prompt_tokens=10, completion_tokens=5,
    )
    assert updated.tally.total == 15
    loaded = store.get(s.session_id)
    assert loaded.messages[0].role == "user"
    assert loaded.tally.exchanges == 1


def test_session_store_rejects_traversal(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    with pytest.raises(SessionStoreError):
        store.get("../../etc/passwd")


# -- chat client -------------------------------------------------------------------

def test_chat_client_extracts_usage():
    chat = HarnessChatClient(
        base_url="http://127.0.0.1:11434/v1", model="qwen2.5:7b", transport=_mock_transport("hello", 21, 9)
    )
    result = chat.chat(system_prompt="s", messages=[{"role": "user", "content": "hi"}])
    assert result.content == "hello"
    assert result.prompt_tokens == 21
    assert result.completion_tokens == 9


def test_chat_client_refuses_non_loopback():
    with pytest.raises(HarnessLLMError):
        HarnessChatClient(base_url="http://169.254.1.1/v1", model="x")


# -- HTTP API -----------------------------------------------------------------------

def test_status_endpoint(client, cfg):
    data = client.get("/api/status").json()
    assert data["soul_enabled"] is True
    assert data["home"] == str(cfg.home)
    assert "skills" in data["layout"]


def test_registry_endpoint(client):
    data = client.get("/api/registry").json()
    assert any(s["name"] == "ponytail" for s in data["skills"])


def test_soul_toggle_persists(client, cfg):
    resp = client.post("/api/soul", json={"enabled": False})
    assert resp.json()["enabled"] is False
    assert HarnessConfig.load(cfg.home).soul_enabled is False
    client.post("/api/soul", json={"enabled": True})


def test_model_select_persists(client, cfg):
    resp = client.post("/api/model", json={"model": "llama3.1:8b"})
    assert resp.json()["model"] == "llama3.1:8b"
    assert HarnessConfig.load(cfg.home).selected_model == "llama3.1:8b"


def test_chat_creates_session_and_tallies_tokens(client):
    resp = client.post("/api/chat", json={"message": "hello there"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["reply"] == "ok"
    assert data["tally"]["total"] == 18
    # second exchange accumulates
    resp2 = client.post("/api/chat", json={"message": "again", "session_id": data["session_id"]})
    assert resp2.json()["tally"]["total"] == 36
    assert resp2.json()["tally"]["exchanges"] == 2


def test_sessions_listing_and_rename(client):
    client.post("/api/chat", json={"message": "hi"})
    sessions = client.get("/api/sessions").json()["sessions"]
    assert len(sessions) == 1
    sid = sessions[0]["session_id"]
    renamed = client.post(f"/api/sessions/{sid}/rename", json={"title": "work"})
    assert renamed.json()["title"] == "work"


def test_chat_unknown_session_404(client):
    resp = client.post("/api/chat", json={"message": "hi", "session_id": "deadbeefdead"})
    assert resp.status_code == 404


def test_harness_runs_endpoint(client):
    data = client.get("/api/harness/runs").json()
    assert data["count"] == len(data["runs"])


def test_console_served(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"CyClaw" in resp.content


def test_session_files_written_under_home(client, cfg):
    client.post("/api/chat", json={"message": "hi"})
    files = list((cfg.home / "sessions").glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    # "total" is a computed property of TokenTally, not a persisted field.
    assert payload["tally"]["prompt_tokens"] + payload["tally"]["completion_tokens"] == 18
