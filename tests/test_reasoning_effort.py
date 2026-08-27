"""Outbound-payload tests for the Ollama thinking/reasoning kill switch.

Proves the CONTROL REACHES THE WIRE on every local-model path, rather than
merely that config.yaml holds a value. A client could hide a reasoning trace
while the model still performs one, so every assertion below inspects the
serialized request body, never a helper's return value.

Two vocabularies, deliberately not interchangeable:
  * OpenAI-compatible /v1/chat/completions -> ``reasoning_effort: "none"``
  * native /api/chat, /api/generate        -> ``think: false``

No live Ollama. Each target is exercised through its own established seam:
``LocalLLMClient`` has no injectable transport, so it gets a real loopback
HTTPServer (wire bytes) or a patched ``_client.post``; the harness and agentic
clients take ``transport=`` and get ``httpx.MockTransport``.
"""

from __future__ import annotations

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from agentic.harness_optimizer.model_adapter import LocalProposerClient
from guardrails.integration import _apply_guardrails_config
from harness.config import HarnessConfig
from harness.ollama import HarnessChatClient
from harness.server import create_app
from llm.client import LocalLLMClient, reset_local_backend_cache, resolve_local_backend
from utils.config_validation import (
    resolve_reasoning_effort,
    validate_local_llm_reasoning_effort,
)
from utils.errors import ConfigError

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SHIPPED_CONFIG = _REPO_ROOT / "config.yaml"
_THROUGHPUT_SCRIPT = _REPO_ROOT / "scripts" / "measure_local_llm_throughput.py"


@pytest.fixture(autouse=True)
def _clear_backend_cache():
    # resolve_local_backend memoizes per config shape; every test here varies
    # that shape, so a leaked entry would silently answer the wrong question.
    reset_local_backend_cache()
    yield
    reset_local_backend_cache()


# =============================================================================
# 1. Configuration: present / absent / normalized / invalid
# =============================================================================

class TestReasoningEffortConfig:
    @pytest.mark.parametrize("value", ["none", "low", "medium", "high", "max"])
    def test_every_documented_value_is_accepted(self, value):
        assert resolve_reasoning_effort({"reasoning_effort": value}) == value

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [(" NONE ", "none"), ("None", "none"), ("\tHigh\n", "high"), ("MAX", "max")],
    )
    def test_case_and_whitespace_are_normalized(self, raw, expected):
        assert resolve_reasoning_effort({"reasoning_effort": raw}) == expected

    def test_absent_key_resolves_to_none_so_the_field_is_omitted(self):
        # Backward compatibility: omitting the field is what preserves the
        # pre-existing behaviour (Ollama auto-enables thinking on a capable model).
        assert resolve_reasoning_effort({}) is None

    @pytest.mark.parametrize("blank", ["", "   ", "\n"])
    def test_empty_or_whitespace_is_treated_as_unset(self, blank):
        assert resolve_reasoning_effort({"reasoning_effort": blank}) is None

    @pytest.mark.parametrize("bad", ["disabled", "off", "false", "minimal", "NONE!", "0"])
    def test_invalid_value_raises_rather_than_defaulting(self, bad):
        # Explicitly NOT silently coerced to "none" -- Ollama itself rejects an
        # unknown value, so a silent substitution would hide a misconfiguration.
        with pytest.raises(ConfigError):
            resolve_reasoning_effort({"reasoning_effort": bad})

    @pytest.mark.parametrize("bad", [True, 1, 0, 1.5, ["none"], {"a": 1}])
    def test_non_string_value_raises(self, bad):
        with pytest.raises(ConfigError):
            resolve_reasoning_effort({"reasoning_effort": bad})

    def test_error_message_names_the_key_and_the_valid_set(self):
        with pytest.raises(ConfigError) as exc:
            resolve_reasoning_effort({"reasoning_effort": "disabled"})
        assert "models.local_llm.reasoning_effort" in str(exc.value)
        assert "none" in str(exc.value)
        assert exc.value.details["received"] == "disabled"
        assert "none" in exc.value.details["valid"]

    def test_boot_validator_rejects_a_bad_shipped_value(self):
        cfg = {"models": {"local_llm": {"reasoning_effort": "disabled"}}}
        with pytest.raises(ConfigError):
            validate_local_llm_reasoning_effort(cfg)

    @pytest.mark.parametrize(
        "cfg",
        [{}, {"models": None}, {"models": {}}, {"models": {"local_llm": None}}],
    )
    def test_boot_validator_is_a_noop_when_blocks_are_absent(self, cfg):
        validate_local_llm_reasoning_effort(cfg)  # must not raise

    def test_shipped_config_disables_thinking_explicitly(self):
        with open(_SHIPPED_CONFIG, encoding="utf-8") as f:
            shipped = yaml.safe_load(f)
        local_llm = shipped["models"]["local_llm"]
        assert local_llm["reasoning_effort"] == "none"
        # The whole point of the setting: it is the local (Qwen) model's block.
        assert local_llm["model"] == "qwen3.8:27b-mlx"
        validate_local_llm_reasoning_effort(shipped)  # must not raise


# =============================================================================
# 2. Core RAG path -- real wire bytes over a loopback socket
# =============================================================================

class _OllamaLikeHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {}
        self.server.received.append(body)  # type: ignore[attr-defined]
        data = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class _MockOllamaServer(HTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), _OllamaLikeHandler)
        self.received: list[dict] = []


@pytest.fixture
def mock_ollama():
    servers: list[_MockOllamaServer] = []

    def factory() -> tuple[str, _MockOllamaServer]:
        server = _MockOllamaServer()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}/v1", server

    yield factory
    for s in servers:
        s.shutdown()
        s.server_close()


def _local_config(tmp_path, base_url: str, **local_extra) -> str:
    local_llm = {
        "provider": "ollama",
        "base_url": base_url,
        "model": "qwen3.8:27b-mlx",
        "max_tokens": 256,
        "temperature": 0.1,
        "timeout_sec": 5,
    }
    local_llm.update(local_extra)
    path = tmp_path / "config.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump({"models": {"local_llm": local_llm}}, f)
    return str(path)


class TestCoreRagOutboundPayload:
    def test_ollama_request_carries_reasoning_effort_none_on_the_wire(self, tmp_path, mock_ollama):
        base_url, server = mock_ollama()
        client = LocalLLMClient(_local_config(tmp_path, base_url, reasoning_effort="none"))
        client.generate("what is CyClaw?")
        client.close()

        body = server.received[0]
        assert body["reasoning_effort"] == "none"
        # The OpenAI-compatible endpoint does not understand `think`.
        assert "think" not in body

    def test_existing_request_fields_are_unchanged(self, tmp_path, mock_ollama):
        base_url, server = mock_ollama()
        client = LocalLLMClient(_local_config(tmp_path, base_url, reasoning_effort="none"))
        client.generate("what is CyClaw?")
        client.close()

        body = server.received[0]
        assert body["model"] == "qwen3.8:27b-mlx"
        assert body["messages"] == [{"role": "user", "content": "what is CyClaw?"}]
        assert body["max_tokens"] == 256
        assert body["temperature"] == pytest.approx(0.1)

    def test_absent_key_omits_the_field_entirely(self, tmp_path, mock_ollama):
        base_url, server = mock_ollama()
        client = LocalLLMClient(_local_config(tmp_path, base_url))
        client.generate("hello")
        client.close()

        body = server.received[0]
        assert "reasoning_effort" not in body
        assert "think" not in body

    def test_normalized_value_reaches_the_wire_normalized(self, tmp_path, mock_ollama):
        base_url, server = mock_ollama()
        client = LocalLLMClient(_local_config(tmp_path, base_url, reasoning_effort=" NONE "))
        client.generate("hello")
        client.close()

        assert server.received[0]["reasoning_effort"] == "none"

    def test_invalid_value_fails_before_any_request_is_made(self, tmp_path, mock_ollama):
        base_url, server = mock_ollama()
        with pytest.raises(ConfigError):
            LocalLLMClient(_local_config(tmp_path, base_url, reasoning_effort="disabled"))
        assert server.received == []


# =============================================================================
# 3. Provider isolation and failover -- the resolved backend decides
# =============================================================================

def _failover_cfg(*, primary_provider: str, fallback_provider: str, effort: str = "none") -> dict:
    return {
        "provider": primary_provider,
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "primary-model",
        "max_tokens": 256,
        "temperature": 0.1,
        "timeout_sec": 5,
        "reasoning_effort": effort,
        "fallback": {
            "enabled": True,
            "provider": fallback_provider,
            "base_url": "http://127.0.0.1:1234/v1",
            "model": "fallback-model",
            "probe_timeout_sec": 0.1,
        },
    }


class TestProviderIsolation:
    def test_lmstudio_primary_receives_neither_field(self, tmp_path, mock_ollama):
        base_url, server = mock_ollama()
        client = LocalLLMClient(
            _local_config(tmp_path, base_url, provider="lmstudio", reasoning_effort="none")
        )
        assert client.reasoning_effort is None
        client.generate("hello")
        client.close()

        body = server.received[0]
        assert "reasoning_effort" not in body
        assert "think" not in body

    def test_ollama_primary_selected_carries_the_value(self):
        cfg = _failover_cfg(primary_provider="ollama", fallback_provider="lmstudio")
        with patch("llm.client._probe_openai_models", return_value=True):
            resolved = resolve_local_backend(cfg)
        assert resolved.source == "primary"
        assert resolved.provider == "ollama"
        assert resolved.reasoning_effort == "none"

    def test_failover_to_lmstudio_drops_the_ollama_only_field(self):
        # Primary probe fails, secondary succeeds.
        cfg = _failover_cfg(primary_provider="ollama", fallback_provider="lmstudio")
        with patch("llm.client._probe_openai_models", side_effect=[False, True]):
            resolved = resolve_local_backend(cfg)
        assert resolved.source == "fallback"
        assert resolved.provider == "lmstudio"
        assert resolved.reasoning_effort is None

    def test_failover_to_an_ollama_secondary_keeps_the_field(self):
        cfg = _failover_cfg(primary_provider="lmstudio", fallback_provider="ollama")
        with patch("llm.client._probe_openai_models", side_effect=[False, True]):
            resolved = resolve_local_backend(cfg)
        assert resolved.source == "fallback"
        assert resolved.provider == "ollama"
        assert resolved.reasoning_effort == "none"

    def test_lmstudio_primary_selected_has_no_field_even_though_configured(self):
        cfg = _failover_cfg(primary_provider="lmstudio", fallback_provider="ollama")
        with patch("llm.client._probe_openai_models", return_value=True):
            resolved = resolve_local_backend(cfg)
        assert resolved.source == "primary"
        assert resolved.reasoning_effort is None

    def test_failover_request_to_lmstudio_carries_no_ollama_field_on_the_wire(self, tmp_path):
        # End-to-end version of the leak check: build the client with failover
        # active and assert the body that actually reaches the fallback.
        cfg = _failover_cfg(primary_provider="ollama", fallback_provider="lmstudio")
        sent: list[dict] = []

        def fake_post(url, **kwargs):
            sent.append(kwargs["json"])
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "ok"}}]},
                request=httpx.Request("POST", url),
            )

        with patch("llm.client._probe_openai_models", side_effect=[False, True]):
            client = LocalLLMClient(cfg={"models": {"local_llm": cfg}})
        assert client.backend_source == "fallback"
        client._client.post = fake_post
        client.generate("hello")
        client.close()

        assert "reasoning_effort" not in sent[0]
        assert "think" not in sent[0]

    def test_cache_key_separates_two_reasoning_effort_values(self):
        base = _failover_cfg(primary_provider="ollama", fallback_provider="lmstudio", effort="none")
        with patch("llm.client._probe_openai_models", return_value=True):
            first = resolve_local_backend(base)
            second = resolve_local_backend({**base, "reasoning_effort": "high"})
        assert first.reasoning_effort == "none"
        assert second.reasoning_effort == "high"


class TestCloudPayloadsUnchanged:
    """Grok and Claude must not gain either Ollama-only field."""

    def _cloud_cfg(self, tmp_path) -> str:
        cfg = {
            "models": {
                "local_llm": {
                    "provider": "ollama",
                    "base_url": "http://127.0.0.1:11434/v1",
                    "model": "qwen3.8:27b-mlx",
                    "max_tokens": 256,
                    "temperature": 0.1,
                    "timeout_sec": 5,
                    "reasoning_effort": "none",
                },
                "grok": {
                    "enabled": True,
                    "base_url": "https://api.x.ai/v1",
                    "model": "grok-4.5",
                    "timeout_sec": 10,
                    "max_tokens": 256,
                    "temperature": 0.2,
                },
                "claude": {
                    "enabled": True,
                    "base_url": "https://api.anthropic.com/v1",
                    "model": "claude-sonnet-5",
                    "anthropic_version": "2023-06-01",
                    "timeout_sec": 10,
                    "max_tokens": 256,
                },
            }
        }
        path = tmp_path / "config.yaml"
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f)
        return str(path)

    @pytest.mark.parametrize("client_name", ["grok", "claude"])
    def test_cloud_client_payload_has_neither_field(self, tmp_path, monkeypatch, client_name):
        from llm.client import ClaudeClient, GrokClient

        monkeypatch.setenv("GROK_API_KEY", "dummy")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
        path = self._cloud_cfg(tmp_path)
        client = GrokClient(path) if client_name == "grok" else ClaudeClient(path)

        sent: list[dict] = []

        def fake_post(url, **kwargs):
            sent.append(kwargs["json"])
            payload = (
                {"content": [{"type": "text", "text": "ok"}]}
                if client_name == "claude"
                else {"choices": [{"message": {"content": "ok"}}]}
            )
            return httpx.Response(200, json=payload, request=httpx.Request("POST", url))

        client._client.post = fake_post
        client.generate("hello")
        client.close()

        assert "reasoning_effort" not in sent[0]
        assert "think" not in sent[0]


# =============================================================================
# 4. Harness -- normal chat AND /loop, each exercised on its own
# =============================================================================

_HARNESS_KEY = "harness-test-key"


def _harness_auth(app) -> dict:
    return {"Authorization": f"Bearer {_HARNESS_KEY}", "X-CyClaw-CSRF": app.state.csrf_token}


@pytest.fixture()
def harness_cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("CYCLAW_API_KEY", _HARNESS_KEY)
    monkeypatch.setenv("CYCLAW_HOME", str(tmp_path / ".CyClaw"))
    return HarnessConfig.load()


def _capturing_transport(captured: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={
            "model": "qwen3.8:27b-mlx",
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        })

    return httpx.MockTransport(handler)


class TestHarnessOutboundPayload:
    def test_direct_chat_call_carries_the_field_and_keeps_existing_shape(self):
        captured: list[dict] = []
        chat = HarnessChatClient(
            base_url="http://127.0.0.1:11434/v1",
            model="qwen3.8:27b-mlx",
            reasoning_effort="none",
            transport=_capturing_transport(captured),
        )
        result = chat.chat(system_prompt="sys", messages=[{"role": "user", "content": "hi"}])
        chat.close()

        body = captured[0]
        assert body["reasoning_effort"] == "none"
        assert "think" not in body
        # Nothing else moved.
        assert body["model"] == "qwen3.8:27b-mlx"
        assert body["messages"][0] == {"role": "system", "content": "sys"}
        assert body["messages"][1] == {"role": "user", "content": "hi"}
        assert body["max_tokens"] == 2048
        assert body["temperature"] == pytest.approx(0.3)
        assert body["stream"] is False
        # Token accounting / ChatResult unchanged.
        assert result.prompt_tokens == 11
        assert result.completion_tokens == 7

    def test_client_without_the_setting_omits_the_field(self):
        captured: list[dict] = []
        chat = HarnessChatClient(
            base_url="http://127.0.0.1:11434/v1",
            model="qwen3.8:27b-mlx",
            transport=_capturing_transport(captured),
        )
        chat.chat(system_prompt="sys", messages=[{"role": "user", "content": "hi"}])
        chat.close()

        assert "reasoning_effort" not in captured[0]
        assert "think" not in captured[0]

    def test_normal_chat_route_carries_the_field(self, harness_cfg):
        captured: list[dict] = []
        chat = HarnessChatClient(
            base_url="http://127.0.0.1:11434/v1",
            model="qwen3.8:27b-mlx",
            reasoning_effort="none",
            transport=_capturing_transport(captured),
        )
        app = create_app(harness_cfg, chat)
        client = TestClient(app, base_url="http://127.0.0.1", headers=_harness_auth(app))
        assert client.post("/api/chat", json={"message": "status"}).status_code == 200

        assert captured[0]["reasoning_effort"] == "none"

    def test_loop_iteration_carries_the_field_independently_of_plain_chat(self, harness_cfg):
        # /loop is not a route -- it is `loop: true` on POST /api/chat, sharing
        # one HarnessChatClient. Driving a REAL loop turn (not asserting that it
        # "eventually calls the same client") is what proves the loop
        # orchestration does not bypass config propagation.
        captured: list[dict] = []
        chat = HarnessChatClient(
            base_url="http://127.0.0.1:11434/v1",
            model="qwen3.8:27b-mlx",
            reasoning_effort="none",
            transport=_capturing_transport(captured),
        )
        app = create_app(harness_cfg, chat)
        client = TestClient(app, base_url="http://127.0.0.1", headers=_harness_auth(app))

        sid = client.post("/api/sessions", json={"title": "loop"}).json()["session_id"]
        client.post(f"/api/sessions/{sid}/goal", json={"goal": "finish the loop feature"})
        assert client.post(
            "/api/chat", json={"message": "plain", "session_id": sid}
        ).status_code == 200
        assert client.post(
            "/api/chat", json={"message": "loop-now", "session_id": sid, "loop": True}
        ).status_code == 200

        assert len(captured) == 2
        plain, loop = captured
        assert plain["reasoning_effort"] == "none"
        assert loop["reasoning_effort"] == "none"
        assert "think" not in loop
        # The loop's own budget still differs from plain chat -- proof this is a
        # genuine loop turn and that max_tokens was not touched by this change.
        assert loop["max_tokens"] != plain["max_tokens"]

    def test_default_chat_client_propagates_from_the_resolved_backend(self, monkeypatch):
        # Proves config propagation at the real construction site rather than a
        # hand-passed constructor argument.
        from harness import server as harness_server

        monkeypatch.setattr(
            harness_server,
            "_llm_settings",
            lambda: {
                "provider": "ollama",
                "base_url": "http://127.0.0.1:11434/v1",
                "model": "qwen3.8:27b-mlx",
                "timeout_sec": 30,
                "reasoning_effort": "none",
            },
        )
        backend = harness_server._resolve_backend()
        assert backend.reasoning_effort == "none"
        chat = harness_server._default_chat_client(backend)
        try:
            assert chat.reasoning_effort == "none"
        finally:
            chat.close()

    def test_default_chat_client_drops_it_for_a_non_ollama_backend(self, monkeypatch):
        from harness import server as harness_server

        monkeypatch.setattr(
            harness_server,
            "_llm_settings",
            lambda: {
                "provider": "lmstudio",
                "base_url": "http://127.0.0.1:1234/v1",
                "model": "local-model",
                "timeout_sec": 30,
                "reasoning_effort": "none",
            },
        )
        backend = harness_server._resolve_backend()
        chat = harness_server._default_chat_client(backend)
        try:
            assert backend.reasoning_effort is None
            assert chat.reasoning_effort is None
        finally:
            chat.close()


# =============================================================================
# 5. Agentic local proposer
# =============================================================================

def _audit_cfg(tmp_path) -> dict:
    return {
        "logging": {"audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {}},
        "policy": {"privacy": {}},
    }


def _proposer_transport(captured: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"choices": [{"message": {"content": "# Proposal\n\nok"}}]})

    return httpx.MockTransport(handler)


class TestAgenticProposerOutboundPayload:
    def test_payload_carries_the_field_and_parsing_still_succeeds(self, tmp_path):
        captured: list[dict] = []
        cfg = _audit_cfg(tmp_path)
        client = LocalProposerClient(
            base_url="http://localhost:1234/v1",  # DevSkim: ignore DS162092 - loopback test URL
            model="local-test-model",
            reasoning_effort="none",
            transport=_proposer_transport(captured),
        )
        try:
            response = client.invoke(system_prompt="sys", user_prompt="usr", cfg=cfg)
        finally:
            client.close()

        body = captured[0]
        assert body["reasoning_effort"] == "none"
        assert "think" not in body
        # System and user messages intact; budgets untouched.
        assert body["messages"] == [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "usr"},
        ]
        assert body["max_tokens"] == 2048
        assert body["temperature"] == pytest.approx(0.0)
        assert response.content.startswith("# Proposal")

    def test_audit_events_are_unchanged(self, tmp_path):
        cfg = _audit_cfg(tmp_path)
        client = LocalProposerClient(
            base_url="http://localhost:1234/v1",  # DevSkim: ignore DS162092 - loopback test URL
            model="local-test-model",
            reasoning_effort="none",
            transport=_proposer_transport([]),
        )
        # Distinctive sentinels so a substring match cannot collide with an
        # ordinary field name (plain "sys" hides inside "system_prompt_hash").
        try:
            client.invoke(
                system_prompt="SYSTEM_SENTINEL_9f3a",
                user_prompt="USER_SENTINEL_4b7c",
                cfg=cfg,
            )
        finally:
            client.close()

        events = [
            json.loads(line)
            for line in Path(cfg["logging"]["audit_file"]).read_text(encoding="utf-8").splitlines()
        ]
        names = [e["event"] for e in events]
        assert "agentic_harness_proposer_model_invoked" in names
        assert "agentic_harness_proposer_model_succeeded" in names
        assert "agentic_harness_proposer_model_failed" not in names
        # Prompts are still hashed, never logged in the clear.
        invoked = next(e for e in events if e["event"] == "agentic_harness_proposer_model_invoked")
        assert invoked["system_prompt_hash"]
        assert invoked["user_prompt_hash"]
        serialized = json.dumps(events)
        assert "SYSTEM_SENTINEL_9f3a" not in serialized
        assert "USER_SENTINEL_4b7c" not in serialized

    def test_omitted_when_not_configured(self, tmp_path):
        captured: list[dict] = []
        client = LocalProposerClient(
            base_url="http://localhost:1234/v1",  # DevSkim: ignore DS162092 - loopback test URL
            model="local-test-model",
            transport=_proposer_transport(captured),
        )
        try:
            client.invoke(system_prompt="sys", user_prompt="usr", cfg=_audit_cfg(tmp_path))
        finally:
            client.close()

        assert "reasoning_effort" not in captured[0]

    def test_cli_helper_reads_config_and_gates_on_provider(self):
        # The real construction sites call this helper, so testing it proves
        # config propagation rather than a manually supplied constructor arg.
        from agentic.cli import _local_reasoning_effort
        from types import SimpleNamespace

        app_cfg = {"models": {"local_llm": {"reasoning_effort": "none"}}}
        ollama_cfg = SimpleNamespace(deepagent_github=SimpleNamespace(provider="ollama"))
        other_cfg = SimpleNamespace(deepagent_github=SimpleNamespace(provider="openai_compatible"))

        assert _local_reasoning_effort(ollama_cfg, app_cfg) == "none"
        # A non-Ollama provider never receives the Ollama-only field.
        assert _local_reasoning_effort(other_cfg, app_cfg) is None
        # Absent key stays absent.
        assert _local_reasoning_effort(ollama_cfg, {"models": {"local_llm": {}}}) is None
        assert _local_reasoning_effort(ollama_cfg, {}) is None

    def test_cli_helper_raises_on_an_invalid_configured_value(self):
        from agentic.cli import _local_reasoning_effort
        from types import SimpleNamespace

        cfg = SimpleNamespace(deepagent_github=SimpleNamespace(provider="ollama"))
        with pytest.raises(ConfigError):
            _local_reasoning_effort(cfg, {"models": {"local_llm": {"reasoning_effort": "off"}}})


# =============================================================================
# 6. Native Ollama endpoint -- think:false, never reasoning_effort
# =============================================================================

@pytest.fixture(scope="module")
def throughput_script():
    spec = importlib.util.spec_from_file_location(
        "measure_local_llm_throughput", _THROUGHPUT_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestNativeThroughputScript:
    def _run(self, script, monkeypatch, *, warmup: bool) -> list[dict]:
        sent: list[dict] = []

        def fake_post(host: str, body: dict, timeout_sec: int) -> dict:
            sent.append(body)
            return {"eval_count": 128, "eval_duration": 4_000_000_000,
                    "prompt_eval_count": 64, "prompt_eval_duration": 1_000_000_000}

        monkeypatch.setattr(script, "_post_generate", fake_post)
        script.run_suite(
            host="http://127.0.0.1:11434",
            model="qwen3.8:27b-mlx",
            num_predict=128,
            timeout_sec=10,
            warmup=warmup,
        )
        return sent

    def test_warmup_and_every_measured_call_disable_thinking(self, throughput_script, monkeypatch):
        sent = self._run(throughput_script, monkeypatch, warmup=True)
        # 1 warm-up + 2 measured cases (short_decode, rag_prefill).
        assert len(sent) == 3
        for body in sent:
            assert body["think"] is False
            # Native endpoints do not accept the OpenAI-compatible spelling.
            assert "reasoning_effort" not in body

    def test_measured_calls_disable_thinking_without_warmup(self, throughput_script, monkeypatch):
        sent = self._run(throughput_script, monkeypatch, warmup=False)
        assert len(sent) == 2
        for body in sent:
            assert body["think"] is False
            assert "reasoning_effort" not in body

    def test_non_thinking_is_the_shipped_default_with_no_flag_to_re_enable(self):
        # The script exposes no --think switch; non-thinking is unconditional,
        # so the benchmark can never silently measure a different workload.
        text = _THROUGHPUT_SCRIPT.read_text(encoding="utf-8")
        assert '"think": False' in text
        assert "--think" not in text


# =============================================================================
# 7. Guardrails (NeMo) -- routed through model_kwargs, loopback + Ollama only
# =============================================================================

class _StubModel:
    def __init__(self, type_: str | None = "main"):
        self.type = type_
        self.engine = ""
        self.model = ""
        self.parameters: dict | None = None


class _StubRailsConfig:
    def __init__(self, models):
        self.models = models


def _guardrails_cfg(**overrides):
    from guardrails.config import GuardrailsConfig

    base = {
        "enabled": True,
        "engine": "openai",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen3.8:27b-mlx",
        "reasoning_effort": "none",
    }
    base.update(overrides)
    return GuardrailsConfig(**base)


class TestGuardrailsModelParameters:
    def test_reasoning_effort_lands_in_model_kwargs(self):
        model = _StubModel()
        _apply_guardrails_config(_StubRailsConfig([model]), _guardrails_cfg())
        assert model.parameters["model_kwargs"]["reasoning_effort"] == "none"
        # The existing base_url wiring is untouched.
        assert model.parameters["base_url"] == "http://127.0.0.1:11434/v1"

    def test_existing_model_kwargs_are_preserved(self):
        model = _StubModel()
        model.parameters = {"model_kwargs": {"seed": 7}}
        _apply_guardrails_config(_StubRailsConfig([model]), _guardrails_cfg())
        assert model.parameters["model_kwargs"] == {"seed": 7, "reasoning_effort": "none"}

    def test_absent_setting_adds_no_model_kwargs(self):
        model = _StubModel()
        _apply_guardrails_config(_StubRailsConfig([model]), _guardrails_cfg(reasoning_effort=None))
        assert "model_kwargs" not in (model.parameters or {})

    def test_non_main_models_are_left_alone(self):
        model = _StubModel(type_="self_check")
        _apply_guardrails_config(_StubRailsConfig([model]), _guardrails_cfg())
        assert model.parameters is None

    def test_loader_gates_off_a_non_loopback_endpoint(self, tmp_path, monkeypatch):
        # A guardrails block repointed at a real OpenAI server must not receive
        # an Ollama-only field, so the value resolves to None at load time.
        cfg = self._write(tmp_path, monkeypatch, base_url="https://api.openai.com/v1")
        assert cfg.reasoning_effort is None

    def test_loader_gates_off_a_non_openai_engine(self, tmp_path, monkeypatch):
        cfg = self._write(tmp_path, monkeypatch, engine="nim")
        assert cfg.reasoning_effort is None

    def test_loader_reads_the_single_source_key(self, tmp_path, monkeypatch):
        cfg = self._write(tmp_path, monkeypatch)
        assert cfg.reasoning_effort == "none"

    def test_loader_ignores_a_stray_guardrails_block_override(self, tmp_path, monkeypatch):
        # models.local_llm is the single source; a duplicate under guardrails:
        # must not be able to diverge from it.
        cfg = self._write(tmp_path, monkeypatch, block_extra={"reasoning_effort": "high"})
        assert cfg.reasoning_effort == "none"

    def _write(self, tmp_path, monkeypatch, *, engine="openai",
               base_url="http://127.0.0.1:11434/v1", block_extra=None):
        from guardrails.config import load_guardrails_config
        from utils.logger import reset_config_cache

        block = {
            "enabled": True,
            "engine": engine,
            "base_url": base_url,
            "model": "qwen3.8:27b-mlx",
        }
        block.update(block_extra or {})
        doc = {
            "guardrails": block,
            "models": {"local_llm": {"reasoning_effort": "none"}},
        }
        path = tmp_path / "config.yaml"
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(doc, f)
        reset_config_cache()
        try:
            return load_guardrails_config(str(path))
        finally:
            reset_config_cache()


# =============================================================================
# 8. Negative / regression assertions -- the wrong fixes must stay absent
# =============================================================================

def _code_lines(path: Path) -> str:
    """Source with whole-line ``#`` comments removed.

    These regression checks assert a field name is ABSENT from a file, and the
    comments explaining why it is absent necessarily mention it -- so a raw
    substring scan would fail on its own documentation.
    """
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


_SOURCES = [
    _REPO_ROOT / "llm" / "client.py",
    _REPO_ROOT / "harness" / "ollama.py",
    _REPO_ROOT / "harness" / "server.py",
    _REPO_ROOT / "agentic" / "harness_optimizer" / "model_adapter.py",
    _REPO_ROOT / "guardrails" / "integration.py",
    _REPO_ROOT / "scripts" / "measure_local_llm_throughput.py",
]


class TestNoPromptLevelWorkaround:
    @pytest.mark.parametrize("path", _SOURCES, ids=lambda p: p.name)
    def test_no_no_think_token_or_think_tag_stripping(self, path):
        text = path.read_text(encoding="utf-8")
        # A prompt-level "/no_think" marker is not disabling reasoning, it is
        # asking politely; and stripping <think> tags hides a trace the model
        # still spent tokens producing.
        assert "/no_think" not in text
        assert "<think>" not in text
        assert "</think>" not in text

    def test_no_system_prompt_instructs_the_model_not_to_reason(self):
        prompts = (_REPO_ROOT / "harness" / "prompts.py").read_text(encoding="utf-8").lower()
        for phrase in ("do not think", "don't think", "no thinking", "without thinking"):
            assert phrase not in prompts

    def test_shipped_budgets_and_sampling_were_not_touched(self):
        with open(_SHIPPED_CONFIG, encoding="utf-8") as f:
            shipped = yaml.safe_load(f)
        local_llm = shipped["models"]["local_llm"]
        # Reducing max_tokens / context / temperature is NOT disabling thinking;
        # these pins must stay exactly where CLAUDE.md documents them.
        assert local_llm["max_tokens"] == 4096
        assert local_llm["temperature"] == 0.2
        assert local_llm["timeout_sec"] == 720
        assert shipped["retrieval"]["max_context_tokens"] == 4000

    def test_openai_compatible_clients_never_send_think(self):
        for path in (
            _REPO_ROOT / "llm" / "client.py",
            _REPO_ROOT / "harness" / "ollama.py",
            _REPO_ROOT / "agentic" / "harness_optimizer" / "model_adapter.py",
        ):
            assert '"think"' not in _code_lines(path)

    def test_native_script_never_sends_reasoning_effort(self):
        # Comments legitimately NAME the field to explain why it is absent here,
        # so only executable lines are checked.
        assert "reasoning_effort" not in _code_lines(_THROUGHPUT_SCRIPT)
