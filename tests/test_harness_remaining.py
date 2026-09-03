"""Remaining harness coverage: server helpers, config, prompts, registry, ollama."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from harness import server as harness_server
from harness.config import (
    HarnessConfig,
    HarnessConfigError,
    _discard_staged,
    _load_json,
    default_home,
)
from harness.ollama import ChatResult, HarnessChatClient, HarnessLLMError, _parse_chat_response, _token_count
from harness.prompts import _read_skill_body, _strip_frontmatter, compose_system_prompt
from harness.registry_view import list_mcp_tools, list_repo_skills
from harness.schemas import (
    AgentRunRequest,
    ApiKeysRequest,
    _MAX_API_KEY_LEN,
    _MAX_ENV_NAME_LEN,
    _MAX_READ_FILE_LEN,
    _MAX_READ_FILES,
    _canonicalize_read_paths,
    _one_safe_read_path,
)
from harness.server import GenerationGate, clip_history, create_app
from harness.sessions import SessionStore, TokenTally
from harness.skills_view import list_wired_skills, render_skills_diagram
from harness.tools_view import render_tools_diagram
from harness.web_search import WebTool, WebToolError
from tests.test_harness import _auth_headers, _mock_transport


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("CYCLAW_HOME", str(tmp_path / ".CyClaw"))
    monkeypatch.setenv("CYCLAW_API_KEY", "harness-test-key")
    return HarnessConfig.load()


def _is_loopback_peer_request(host: str | None, port: int = 9):
    request = MagicMock()
    if host is None:
        request.client = None
    else:
        request.client = SimpleNamespace(host=host, port=port)
    return harness_server._is_loopback_peer(request)


def test_is_loopback_peer_missing_and_aliases():
    assert _is_loopback_peer_request(None) is False
    assert _is_loopback_peer_request("localhost") is True
    assert _is_loopback_peer_request("127.0.0.1") is True
    assert _is_loopback_peer_request("not a host") is False
    assert _is_loopback_peer_request("8.8.8.8") is False


def test_config_helpers_degrade_when_parsed_is_not_a_dict(monkeypatch):
    monkeypatch.setattr(harness_server, "_get_config", lambda *_a, **_k: "nope")
    assert harness_server._llm_settings() == {}
    assert harness_server._deepagent_github_settings() == {}
    assert harness_server._rate_limit_settings() == {}
    assert harness_server._loop_rate_limit_settings() == {}


def test_loop_rate_limit_settings_requires_api_mapping(monkeypatch):
    monkeypatch.setattr(harness_server, "_get_config", lambda *_a, **_k: {"api": "nope"})
    assert harness_server._loop_rate_limit_settings() == {}
    monkeypatch.setattr(
        harness_server, "_get_config", lambda *_a, **_k: {"api": {"harness_loop_rate_limit": 3}}
    )
    assert harness_server._loop_rate_limit_settings() == {}


def test_loop_max_tokens_rejects_non_positive_and_non_int():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(harness_server, "_loop_rate_limit_settings", lambda: {"max_tokens": "nope"})
        assert harness_server._loop_max_tokens() == harness_server._DEFAULT_LOOP_MAX_TOKENS
        mp.setattr(harness_server, "_loop_rate_limit_settings", lambda: {"max_tokens": 0})
        assert harness_server._loop_max_tokens() == harness_server._DEFAULT_LOOP_MAX_TOKENS
        mp.setattr(harness_server, "_loop_rate_limit_settings", lambda: {"max_tokens": -4})
        assert harness_server._loop_max_tokens() == harness_server._DEFAULT_LOOP_MAX_TOKENS


def test_clip_history_empty_when_budget_is_non_positive():
    msgs = [{"role": "user", "content": "hello"}]
    assert clip_history(msgs, 0) == []
    assert clip_history(msgs, -1) == []


def test_generation_gate_release_swallows_runtime_error():
    gate = GenerationGate()
    lock = MagicMock()
    lock.locked.return_value = True
    lock.release.side_effect = RuntimeError("released twice")
    gate._lock = lock
    gate.release()
    lock.release.assert_called_once()


def test_canonical_backend_key_returns_none_on_invalid_url():
    assert harness_server._canonical_backend_key("http://[::1:80") is None


def test_agent_run_shares_chat_backend_stays_cautious(monkeypatch):
    monkeypatch.setattr(harness_server, "_deepagent_github_settings", lambda: {})
    assert harness_server._agent_run_shares_chat_backend() is True

    monkeypatch.setattr(
        harness_server, "_deepagent_github_settings", lambda: {"base_url": "http://[::1:80"}
    )
    monkeypatch.setattr(
        harness_server,
        "_resolve_backend",
        lambda: SimpleNamespace(base_url="http://127.0.0.1:11434/v1"),
    )
    assert harness_server._agent_run_shares_chat_backend() is True


def test_get_soul_state(cfg):
    app = create_app(cfg)
    client = TestClient(app, base_url="http://127.0.0.1", headers=_auth_headers(app))
    assert client.get("/api/soul").json() == {"enabled": True}


def test_web_fetch_failed_is_502(cfg):
    class Boom(WebTool):
        def fetch(self, url: str) -> dict:
            raise WebToolError("upstream HTTP 502", code="WEB_FETCH_FAILED")

        def status(self) -> dict:
            raise WebToolError("DNS failed", code="WEB_DNS")

        def set_enabled(self, enabled: bool) -> dict:
            raise WebToolError("allowlist is empty", code="WEB_ALLOWLIST_EMPTY")

        def allow(self, raw: str) -> dict:
            raise WebToolError("bad", code="WEB_BAD_URL")

        def deny(self, raw: str) -> dict:
            raise WebToolError("bad", code="WEB_BAD_URL")

    web = Boom(cfg, transport=httpx.MockTransport(lambda r: httpx.Response(200)), resolver=lambda _h: None)
    app = create_app(cfg, HarnessChatClient(
        base_url="http://127.0.0.1:11434/v1", model="x", transport=_mock_transport()
    ), web)
    client = TestClient(app, base_url="http://127.0.0.1", headers=_auth_headers(app))
    fetch = client.post("/api/web/fetch", json={"url": "https://docs.python.org/"})
    assert fetch.status_code == 502
    assert fetch.json()["detail"]["code"] == "WEB_FETCH_FAILED"
    status = client.get("/api/web")
    assert status.status_code == 502
    toggle = client.post("/api/web", json={"enabled": True})
    assert toggle.status_code == 409
    allow = client.post("/api/web/allow", json={"url": "https://docs.python.org/"})
    assert allow.status_code == 400
    deny = client.post("/api/web/deny", json={"url": "https://docs.python.org/"})
    assert deny.status_code == 400


def test_keys_write_oserror_is_500(cfg, monkeypatch):
    app = create_app(cfg)
    client = TestClient(app, base_url="http://127.0.0.1", headers=_auth_headers(app))

    def boom(_updates):
        raise OSError("disk full")

    monkeypatch.setattr(harness_server.env_keys, "write_keys", boom)
    resp = client.post("/api/keys", json={"keys": {"GROK_API_KEY": "abc-not-a-real-key"}})
    assert resp.status_code == 500
    assert resp.json()["detail"]["code"] == "ENV_KEY_WRITE_FAILED"


def test_chat_maps_llm_error_to_502(cfg):
    chat = HarnessChatClient(
        base_url="http://127.0.0.1:11434/v1", model="x", transport=_mock_transport()
    )

    def boom(**_kwargs):
        raise HarnessLLMError("model server unreachable — is Ollama running?")

    chat.chat = boom  # type: ignore[method-assign]
    app = create_app(cfg, chat)
    client = TestClient(app, base_url="http://127.0.0.1", headers=_auth_headers(app))
    resp = client.post("/api/chat", json={"message": "hello"})
    assert resp.status_code == 502
    assert "Ollama" in resp.json()["detail"]["message"] or resp.json()["detail"]["code"]


def test_loop_in_flight_conflict(cfg):
    started = __import__("threading").Event()
    release = __import__("threading").Event()

    def handler(request: httpx.Request) -> httpx.Response:
        started.set()
        release.wait(timeout=5)
        return httpx.Response(200, json={
            "model": "x",
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })

    chat = HarnessChatClient(
        base_url="http://127.0.0.1:11434/v1",
        model="x",
        transport=httpx.MockTransport(handler),
    )
    app = create_app(cfg, chat)
    client = TestClient(app, base_url="http://127.0.0.1", headers=_auth_headers(app))
    sid = client.post("/api/sessions", json={"title": "loop"}).json()["session_id"]
    client.post(f"/api/sessions/{sid}/goal", json={"goal": "finish coverage"})

    errors: list[int] = []

    def first() -> None:
        resp = client.post(
            "/api/chat", json={"message": "go", "session_id": sid, "loop": True}
        )
        errors.append(resp.status_code)

    t = __import__("threading").Thread(target=first)
    t.start()
    assert started.wait(timeout=5)
    second = client.post(
        "/api/chat", json={"message": "go2", "session_id": sid, "loop": True}
    )
    release.set()
    t.join(timeout=5)
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "LOOP_IN_FLIGHT"


def test_getattr_app_and_unknown(cfg, monkeypatch):
    harness_server._default_app.cache_clear()
    fake = object()
    monkeypatch.setattr(HarnessConfig, "load", classmethod(lambda cls, *a, **k: cfg))
    monkeypatch.setattr(harness_server, "create_app", lambda *_a, **_k: fake)
    assert harness_server.__getattr__("app") is fake
    with pytest.raises(AttributeError):
        harness_server.__getattr__("not_an_attr")
    harness_server._default_app.cache_clear()


def test_home_falls_back_to_path_home(monkeypatch):
    monkeypatch.delenv("CYCLAW_HOME", raising=False)
    monkeypatch.delenv("USERPROFILE", raising=False)
    assert default_home() == Path.home() / ".CyClaw"


def test_discard_staged_swallows_unlink_errors(tmp_path, monkeypatch):
    def boom(_path):
        raise OSError("busy")

    monkeypatch.setattr("harness.config.os.unlink", boom)
    _discard_staged(str(tmp_path / "missing.tmp"))


def test_main_refuses_non_loopback_and_bad_port(cfg, monkeypatch):
    monkeypatch.setenv("CYCLAW_HARNESS_HOST", "8.8.8.8")
    with pytest.raises(SystemExit):
        harness_server.main()
    monkeypatch.setenv("CYCLAW_HARNESS_HOST", "127.0.0.1")
    monkeypatch.setenv("CYCLAW_HARNESS_PORT", "not-a-port")
    with pytest.raises(SystemExit):
        harness_server.main()
    monkeypatch.setenv("CYCLAW_HARNESS_PORT", "80")
    with pytest.raises(SystemExit):
        harness_server.main()
    monkeypatch.setenv("CYCLAW_HARNESS_PORT", "8790")
    monkeypatch.setattr(harness_server, "_is_port_in_use", lambda *_a, **_k: True)
    harness_server.main()  # busy port: print and return, do not bind


def test_load_json_rejects_non_object_and_bad_json(tmp_path):
    bad = tmp_path / "config.json"
    bad.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(HarnessConfigError):
        _load_json(bad)
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(HarnessConfigError):
        _load_json(bad)


def test_apply_stored_ignores_invalid_types_and_privileged_port(tmp_path):
    cfg = HarnessConfig.load(tmp_path / "home")
    original = cfg.port
    cfg._apply_stored({
        "port": 80,
        "soul_enabled": "yes",
        "selected_model": 1,
        "web_enabled": 1,
        "memory_enabled": 1,
    })
    assert cfg.port == original
    assert cfg.soul_enabled is True


def test_ensure_layout_maps_oserror(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("not a dir", encoding="utf-8")
    with pytest.raises(HarnessConfigError) as exc:
        HarnessConfig.load(blocker)
    assert exc.value.code == "HARNESS_CONFIG_ERROR"


def test_seed_skills_skips_when_repo_skills_missing(tmp_path):
    cfg = HarnessConfig.load(tmp_path / "home")
    cfg.repo_root = tmp_path / "empty-repo"
    cfg._seed_skills()  # no .claude/skills directory


def test_seed_one_skill_survives_write_failure(tmp_path, caplog):
    cfg = HarnessConfig.load(tmp_path / "home")
    src = tmp_path / "ponytail" / "SKILL.md"
    src.parent.mkdir()
    src.write_text("body", encoding="utf-8")
    dest_parent = cfg.skills_dir / "ponytail"
    dest_parent.mkdir(parents=True, exist_ok=True)
    (dest_parent / "SKILL.md").write_text("keep", encoding="utf-8")
    cfg._seed_one_skill(src)  # dest exists: no overwrite
    assert (dest_parent / "SKILL.md").read_text(encoding="utf-8") == "keep"

    dest_parent.joinpath("SKILL.md").unlink()
    dest_parent.rmdir()
    dest_parent.write_text("file-not-dir", encoding="utf-8")
    with caplog.at_level("WARNING", logger="cyclaw.harness.config"):
        cfg._seed_one_skill(src)
    assert "could not seed skill" in caplog.text


def test_strip_frontmatter_without_closing_fence():
    raw = "---\nname: x\nstill frontmatter"
    assert _strip_frontmatter(raw) == raw.strip()
    assert _strip_frontmatter("no fence") == "no fence"


def test_compose_prompt_skips_missing_skills_and_soul(tmp_path):
    prompt = compose_system_prompt(
        soul_enabled=True,
        skills_dir=tmp_path / "missing",
        soul_path=tmp_path / "no-soul.md",
    )
    assert "Discipline contract" not in prompt
    assert "Operator persona" not in prompt
    assert _read_skill_body("ponytail", tmp_path / "missing") is None


def test_list_repo_skills_outside_repo_and_unreadable(tmp_path, monkeypatch):
    skill_dir = tmp_path / "outside"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: outside\n---\nbody\n", encoding="utf-8")
    rows = list_repo_skills(tmp_path)
    assert rows[0]["name"] == "outside"
    assert rows[0]["path"] == str(skill_dir / "SKILL.md")

    def boom(self, *a, **k):
        raise OSError("unreadable")

    monkeypatch.setattr(Path, "read_text", boom)
    assert list_repo_skills(tmp_path) == []


def test_schema_read_path_helpers_reject_bad_inputs():
    with pytest.raises(ValueError, match="read_files"):
        _one_safe_read_path(123)
    with pytest.raises(ValueError, match="read_files"):
        _one_safe_read_path("x" * (_MAX_READ_FILE_LEN + 1))
    with pytest.raises(ValueError, match="read_files"):
        _one_safe_read_path("../escape.py")
    assert _one_safe_read_path("src/ok.py") == "src/ok.py"
    with pytest.raises(ValueError, match="at most"):
        _canonicalize_read_paths([f"f{i}.py" for i in range(_MAX_READ_FILES + 1)])
    assert _canonicalize_read_paths(["a.py", "./a.py", "b.py"]) == ["a.py", "b.py"]


def test_agent_run_rejects_blank_plan():
    with pytest.raises(ValidationError, match="plan must not be blank"):
        AgentRunRequest(
            instruction="do the thing",
            branch="agent/coverage",
            commit_message="msg",
            reason="because tests",
            plan="   ",
        )
    ok = AgentRunRequest(
        instruction="do the thing",
        branch="agent/coverage",
        commit_message="msg",
        reason="because tests",
        plan="reviewed plan text",
        read_files=["src/a.py", "src/a.py"],
    )
    assert ok.plan == "reviewed plan text"
    assert ok.read_files == ["src/a.py"]
    with pytest.raises(ValidationError, match="at most one of pr / issue"):
        AgentRunRequest(
            instruction="do the thing",
            branch="agent/coverage",
            commit_message="msg",
            reason="because tests",
            pr=1,
            issue=2,
        )


def test_api_keys_request_bounds_name_and_value():
    with pytest.raises(ValidationError, match="key name too long"):
        ApiKeysRequest(keys={"K" * (_MAX_ENV_NAME_LEN + 1): "secret-value-here"})
    with pytest.raises(ValidationError, match="value too long"):
        ApiKeysRequest(keys={"GROK_API_KEY": "s" * (_MAX_API_KEY_LEN + 1)})


def test_render_empty_tools_and_skills_diagrams():
    tools = render_tools_diagram([], wired=0, total=0)
    assert "HARNESS TOOLS" in tools
    assert "(none)" in tools
    skills = render_skills_diagram([], wired=0, total=0)
    assert "HARNESS SKILLS" in skills
    assert "(none)" in skills


def test_list_wired_skills_includes_governed_rows(monkeypatch):
    monkeypatch.setattr(
        "harness.skills_view.list_governed_skills",
        lambda: [{"name": "gov-demo", "path": "reg.json", "description": "governed"}],
    )
    payload = list_wired_skills()
    governed = [row for row in payload["skills"] if row["role"] == "governed"]
    assert governed
    assert governed[0]["name"] == "gov-demo"
    assert governed[0]["source"] == "agentic-registry"
    assert governed[0]["wired"] is False


def test_session_record_exchange_trims_to_max_messages(tmp_path, monkeypatch):
    import harness.sessions as sessions_mod

    monkeypatch.setattr(sessions_mod, "_MAX_MESSAGES", 4)
    store = SessionStore(tmp_path / "sessions")
    session = store.create(model="m", title="trim")
    usage = TokenTally(prompt_tokens=1, completion_tokens=1)
    for idx in range(3):
        store.record_exchange(
            session.session_id,
            user_text=f"u{idx}",
            assistant_text=f"a{idx}",
            model="m",
            usage=usage,
        )
    reloaded = store.get(session.session_id)
    assert len(reloaded.messages) == 4
    assert reloaded.messages[0].text == "u1"
    assert reloaded.messages[-1].text == "a2"


def test_list_mcp_tools_malformed_and_non_literal(tmp_path):
    broken = tmp_path / "mcp.py"
    broken.write_text("TOOLS = unknown_name\n", encoding="utf-8")
    assert list_mcp_tools(broken) == []
    broken.write_text("def foo():\n    pass\n", encoding="utf-8")
    assert list_mcp_tools(broken) == []
    broken.write_text("??? syntax", encoding="utf-8")
    assert list_mcp_tools(broken) == []
    assert list_mcp_tools(tmp_path / "absent.py") == []


def test_token_count_and_parse_chat_response_degrade():
    assert _token_count("nope") == 0
    assert _token_count(None) == 0
    resp = httpx.Response(200, json=["not", "a", "dict"])
    with pytest.raises(HarnessLLMError):
        _parse_chat_response(resp, "x")
    resp = httpx.Response(200, content=b"not-json")
    with pytest.raises(HarnessLLMError):
        _parse_chat_response(resp, "x")
    resp = httpx.Response(200, json={"choices": [1]})
    with pytest.raises(HarnessLLMError):
        _parse_chat_response(resp, "x")
    resp = httpx.Response(200, json={"choices": [{"message": 1}]})
    with pytest.raises(HarnessLLMError):
        _parse_chat_response(resp, "x")
    resp = httpx.Response(200, json={"choices": [{"message": {"content": 12}}]})
    with pytest.raises(HarnessLLMError):
        _parse_chat_response(resp, "x")
    ok = _parse_chat_response(
        httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": 1}),
        "fallback",
    )
    assert ok.body_text == "ok"
    assert ok.prompt_tokens == 0


def test_chat_client_error_paths():
    chat = HarnessChatClient(base_url="http://127.0.0.1:11434/v1", model="")
    try:
        with pytest.raises(HarnessLLMError):
            chat.chat(system_prompt="s", messages=[])
    finally:
        chat.close()

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    chat = HarnessChatClient(
        base_url="http://127.0.0.1:11434/v1",
        model="x",
        transport=httpx.MockTransport(timeout_handler),
    )
    try:
        with pytest.raises(HarnessLLMError) as exc:
            chat.chat(system_prompt="s", messages=[{"role": "user", "content": "hi"}])
        assert "unreachable" in str(exc.value)
    finally:
        chat.close()

    def not_ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="busy")

    chat = HarnessChatClient(
        base_url="http://127.0.0.1:11434/v1",
        model="x",
        transport=httpx.MockTransport(not_ok),
    )
    try:
        with pytest.raises(HarnessLLMError) as exc:
            chat.chat(system_prompt="s", messages=[{"role": "user", "content": "hi"}])
        assert "HTTP 503" in str(exc.value)
    finally:
        chat.close()


def test_chat_client_sends_reasoning_effort_and_aborts():
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={
            "model": "x",
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": "bad", "completion_tokens": 2},
        })

    chat = HarnessChatClient(
        base_url="http://127.0.0.1:11434/v1",
        model="x",
        reasoning_effort="high",
        transport=httpx.MockTransport(handler),
    )
    try:
        result = chat.chat(system_prompt="s", messages=[{"role": "user", "content": "hi"}])
        assert isinstance(result, ChatResult)
        assert result.prompt_tokens == 0
        assert captured[0]["reasoning_effort"] == "high"
        old = chat._client
        chat.abort_in_flight()
        assert old.is_closed is True
        assert chat._client is not old
    finally:
        chat.close()
