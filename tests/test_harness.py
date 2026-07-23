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
from harness import server as harness_server
from harness.server import create_app
from harness.sessions import SessionStore, SessionStoreError, TokenTally

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
    # base_url sets the Host header to an allowed loopback host; the default
    # "testserver" is now rejected by TrustedHostMiddleware (see the rebinding test).
    return TestClient(create_app(cfg, chat), base_url="http://127.0.0.1")


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
        usage=TokenTally(prompt_tokens=10, completion_tokens=5),
    )
    assert updated.tally.total == 15
    loaded = store.get(s.session_id)
    assert loaded.messages[0].role == "user"
    assert loaded.tally.exchanges == 1


def test_session_store_rejects_traversal(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    with pytest.raises(SessionStoreError):
        store.get("../../etc/passwd")


def test_session_store_skips_corrupt_files_in_listing(tmp_path):
    """JSON that parses but isn't session-shaped must not break the listing.

    Regression: get() used to catch only OSError/JSONDecodeError, so a
    valid-JSON-but-corrupt file (non-dict payload, missing session_id,
    unknown message keys) escaped as KeyError/TypeError/AttributeError and
    500-ed /api/sessions instead of being skipped.
    """
    store = SessionStore(tmp_path / "sessions")
    good = store.create(model="m", title="keep me")
    (tmp_path / "sessions" / "aaaaaaaaaaaa.json").write_text("[1, 2, 3]", encoding="utf-8")
    (tmp_path / "sessions" / "bbbbbbbbbbbb.json").write_text('{"title": "no id"}', encoding="utf-8")
    (tmp_path / "sessions" / "cccccccccccc.json").write_text(
        json.dumps({"session_id": "cccccccccccc", "messages": [{"bogus": "key"}]}), encoding="utf-8"
    )
    listed = store.list()
    assert [s["session_id"] for s in listed] == [good.session_id]
    with pytest.raises(SessionStoreError):
        store.get("aaaaaaaaaaaa")


# -- chat client -------------------------------------------------------------------

def test_chat_client_extracts_usage():
    chat = HarnessChatClient(
        base_url="http://127.0.0.1:11434/v1", model="qwen2.5:7b", transport=_mock_transport("hello", 21, 9)
    )
    result = chat.chat(system_prompt="s", messages=[{"role": "user", "content": "hi"}])
    assert result.body_text == "hello"
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


def test_console_follows_local_backend_fallback(cfg, monkeypatch):
    """Regression: create_app read models.local_llm directly, bypassing
    llm.client.resolve_local_backend — so with fallback.enabled true and the
    primary (Ollama) down, /query and /health switched to the fallback (LM
    Studio) but the console still targeted the dead primary."""
    import llm.client as llm_client

    llm_cfg = {
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5:7b",
        "provider": "ollama",
        "fallback": {
            "enabled": True,
            "provider": "lmstudio",
            "base_url": "http://127.0.0.1:1234/v1",
            "model": "my-lmstudio-model",
        },
    }
    monkeypatch.setattr(harness_server, "_llm_settings", lambda: llm_cfg)
    # primary probe fails, fallback probe succeeds
    monkeypatch.setattr(
        llm_client, "_probe_openai_models", lambda base_url, **kw: ":1234" in base_url
    )
    llm_client.reset_local_backend_cache()
    try:
        data = TestClient(create_app(cfg), base_url="http://127.0.0.1").get("/api/status").json()
    finally:
        llm_client.reset_local_backend_cache()
    assert data["provider"] == "lmstudio"
    assert data["base_url"] == "http://127.0.0.1:1234/v1"
    assert data["model"] == "my-lmstudio-model"


def test_registry_endpoint(client):
    data = client.get("/api/registry").json()
    assert any(s["name"] == "ponytail" for s in data["skills"])


def test_rejects_non_loopback_host_header(cfg):
    """DNS-rebinding defense: a request whose Host header is not a loopback host
    is rejected by TrustedHostMiddleware before reaching a state-changing route,
    mirroring gate.py's protection for the same single-operator threat model."""
    rebind = TestClient(create_app(cfg, _loopback_chat()), base_url="http://attacker.example")
    assert rebind.get("/api/status").status_code == 400
    assert rebind.post("/api/soul", json={"enabled": False}).status_code == 400


def _loopback_chat() -> HarnessChatClient:
    return HarnessChatClient(
        base_url="http://127.0.0.1:11434/v1", model="qwen2.5:7b", transport=_mock_transport()
    )


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


def test_harness_runs_stray_file_does_not_displace_runs(client, tmp_path, monkeypatch):
    """Regression: the newest-N slice used to happen BEFORE the is_dir filter,
    so a stray file (index, .lock) inside the window silently dropped a real run."""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    for name in ("run-a", "run-b", "run-c"):
        (runs_dir / name).mkdir()
    (runs_dir / "zzz-stray.lock").write_text("", encoding="utf-8")  # sorts first
    monkeypatch.setattr(harness_server, "_RUNS_DIR", runs_dir)
    monkeypatch.setattr(harness_server, "_MAX_RUNS", 3)
    data = client.get("/api/harness/runs").json()
    assert data["count"] == 3
    assert {r["run_id"] for r in data["runs"]} == {"run-a", "run-b", "run-c"}


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
