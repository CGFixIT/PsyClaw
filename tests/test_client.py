"""Unit tests for llm/client.py — LocalLLMClient + GrokClient.

These exercise the error-mapping contract that the graph nodes rely on
(``local_llm_node`` / ``grok_fallback_node`` only ``except LLMServiceError`` /
``GrokServiceError``). Every wire-level failure mode — HTTP status, timeout,
unexpected exception — must be translated into the project's typed errors with
the right ``code`` and ``details``; a leaked ``httpx`` exception would escape the
node handlers and surface as a raw 500.

No live LM Studio / xAI endpoint is contacted: the per-instance ``httpx.Client``
is monkeypatched. Real ``httpx.Request``/``Response`` objects are used so the
``e.response.status_code`` path in the production code runs unmocked.
"""

import httpx
import pytest
import yaml

from llm.client import (
    ClaudeClient,
    GrokClient,
    LocalLLMClient,
    reset_local_backend_cache,
    resolve_local_backend,
)
from utils.errors import ClaudeServiceError, GrokServiceError, LLMServiceError

_URL = "http://127.0.0.1:1234/v1/chat/completions"  # DevSkim: ignore DS162092,DS137138 - loopback test URL


@pytest.fixture(autouse=True)
def _clear_local_backend_cache():
    reset_local_backend_cache()
    yield
    reset_local_backend_cache()


def _write_config(tmp_path, retry: dict = None, local_llm_extra: dict | None = None) -> str:
    """Minimal config.yaml with the models.* blocks both clients read.

    When ``retry`` is given it is injected into both model blocks so the retry
    path can be exercised; when omitted (the default) no ``retry`` key is
    present, so the clients default to ``max_retries == 0`` — the original
    single-attempt behavior every pre-existing test relies on.
    """
    local_llm = {
        "base_url": "http://127.0.0.1:1234/v1",  # DevSkim: ignore DS162092,DS137138
        "model": "test-model",
        "max_tokens": 256,
        "temperature": 0.1,
        "timeout_sec": 5,
    }
    if local_llm_extra:
        local_llm.update(local_llm_extra)
    grok = {
        "base_url": "https://api.x.ai/v1",
        "model": "grok-4.3",
        "max_tokens": 256,
        "temperature": 0.2,
        "timeout_sec": 5,
    }
    claude = {
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-sonnet-5",
        "anthropic_version": "2023-06-01",
        "max_tokens": 256,
        "timeout_sec": 5,
    }
    if retry is not None:
        local_llm["retry"] = dict(retry)
        grok["retry"] = dict(retry)
        claude["retry"] = dict(retry)
    cfg = {"models": {"local_llm": local_llm, "grok": grok, "claude": claude}}
    p = tmp_path / "config.yaml"
    with open(p, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f)
    return str(p)


class _ScriptedPost:
    """Replacement for ``httpx.Client.post`` that returns/raises a scripted sequence.

    Each call consumes the next item: an ``httpx.Response`` is returned, an
    ``Exception`` is raised. The last item is reused once the script is
    exhausted, so a persistent-failure script can drive any number of retries.
    """

    def __init__(self, script: list):
        self._script = list(script)
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        item = self._script.pop(0) if len(self._script) > 1 else self._script[0]
        if isinstance(item, Exception):
            raise item
        return item


class _FakePost:
    """Replacement for ``httpx.Client.post`` with a scripted outcome."""

    def __init__(self, *, response: httpx.Response = None, raises: Exception = None):
        self._response = response
        self._raises = raises
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self._raises is not None:
            raise self._raises
        return self._response


def _ok_response(content: str = "hello from llm") -> httpx.Response:
    req = httpx.Request("POST", _URL)
    return httpx.Response(
        200, json={"choices": [{"message": {"content": content}}]}, request=req
    )


def _claude_ok_response(content: str = "hello from claude") -> httpx.Response:
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return httpx.Response(
        200, json={"content": [{"type": "text", "text": content}]}, request=req
    )


def _status_response(status: int) -> httpx.Response:
    req = httpx.Request("POST", _URL)
    return httpx.Response(status, json={"error": "boom"}, request=req)


# =============================================================================
# LocalLLMClient
# =============================================================================

class TestLocalLLMClient:
    def test_init_reads_config(self, tmp_path):
        client = LocalLLMClient(_write_config(tmp_path))
        assert client.model == "test-model"
        assert client.max_tokens == 256
        assert client.timeout == 5
        client.close()

    def test_generate_success(self, tmp_path):
        client = LocalLLMClient(_write_config(tmp_path))
        fake = _FakePost(response=_ok_response("answer text"))
        client._client.post = fake
        assert client.generate("a prompt") == "answer text"
        # The request targets the /chat/completions endpoint with the model.
        url, kwargs = fake.calls[0]
        assert url.endswith("/chat/completions")
        assert kwargs["json"]["model"] == "test-model"
        assert kwargs["json"]["messages"][0]["content"] == "a prompt"
        client.close()

    def test_generate_null_content_maps_to_llm_service_error(self, tmp_path):
        # A 200 with content=null (e.g. a refusal / tool-call envelope) must not
        # be returned as a blank answer; it maps to the typed error the graph
        # node handles, not a leaked exception or an empty string.
        client = LocalLLMClient(_write_config(tmp_path))
        req = httpx.Request("POST", _URL)
        resp = httpx.Response(
            200, json={"choices": [{"message": {"content": None}}]}, request=req
        )
        client._client.post = _FakePost(response=resp)
        with pytest.raises(LLMServiceError):
            client.generate("a prompt")
        client.close()

    def test_generate_blank_content_maps_to_llm_service_error(self, tmp_path):
        # A whitespace-only 200 body is equally unusable.
        client = LocalLLMClient(_write_config(tmp_path))
        client._client.post = _FakePost(response=_ok_response("   \n  "))
        with pytest.raises(LLMServiceError):
            client.generate("a prompt")
        client.close()

    def test_generate_http_error_maps_to_llm_service_error(self, tmp_path):
        client = LocalLLMClient(_write_config(tmp_path))
        client._client.post = _FakePost(response=_status_response(503))
        with pytest.raises(LLMServiceError) as exc:
            client.generate("a prompt")
        assert exc.value.code == "LLM_SERVICE_ERROR"
        assert exc.value.details.get("status") == 503
        client.close()

    def test_generate_timeout_maps_to_llm_service_error(self, tmp_path):
        client = LocalLLMClient(_write_config(tmp_path))
        client._client.post = _FakePost(raises=httpx.TimeoutException("timed out"))
        with pytest.raises(LLMServiceError) as exc:
            client.generate("a prompt")
        assert exc.value.details.get("timeout_sec") == 5
        client.close()

    def test_generate_unexpected_error_maps_to_llm_service_error(self, tmp_path):
        client = LocalLLMClient(_write_config(tmp_path))
        client._client.post = _FakePost(raises=ValueError("connection reset secret=sk-leak"))
        with pytest.raises(LLMServiceError) as exc:
            client.generate("a prompt")
        # User-facing message is type-only — raw exception text must not leak.
        assert "ValueError" in exc.value.message
        assert "connection reset" not in exc.value.message
        assert "sk-leak" not in exc.value.message
        assert exc.value.details.get("exc_type") == "ValueError"
        client.close()


# =============================================================================
# GrokClient
# =============================================================================

class TestGrokClient:
    def test_is_available_reflects_api_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GROK_API_KEY", "xai-secret")
        client = GrokClient(_write_config(tmp_path))
        assert client.is_available() is True
        client.close()

        monkeypatch.delenv("GROK_API_KEY", raising=False)
        client2 = GrokClient(_write_config(tmp_path))
        assert client2.is_available() is False
        client2.close()

    def test_is_available_false_for_whitespace_only_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GROK_API_KEY", "  \n\t  ")
        client = GrokClient(_write_config(tmp_path))
        assert client.is_available() is False
        client.close()

    def test_generate_without_key_raises(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GROK_API_KEY", raising=False)
        client = GrokClient(_write_config(tmp_path))
        with pytest.raises(GrokServiceError) as exc:
            client.generate("a prompt")
        assert exc.value.details.get("required_env") == "GROK_API_KEY"
        client.close()

    def test_generate_success_sends_bearer(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GROK_API_KEY", "xai-secret")
        client = GrokClient(_write_config(tmp_path))
        fake = _FakePost(response=_ok_response("grok answer"))
        client._client.post = fake
        assert client.generate("a prompt") == "grok answer"
        _url, kwargs = fake.calls[0]
        assert kwargs["headers"]["Authorization"] == "Bearer xai-secret"
        client.close()

    def test_generate_sends_model_and_prompt_in_body(self, tmp_path, monkeypatch):
        # Verifies the model field in the request body matches config so a stale
        # model name (e.g. a retired "grok-4" or "grok-beta") is caught by CI.
        monkeypatch.setenv("GROK_API_KEY", "xai-secret")
        client = GrokClient(_write_config(tmp_path))
        fake = _FakePost(response=_ok_response("grok answer"))
        client._client.post = fake
        client.generate("a prompt")
        _url, kwargs = fake.calls[0]
        assert kwargs["json"]["model"] == "grok-4.3"
        assert kwargs["json"]["messages"][0]["content"] == "a prompt"
        client.close()

    def test_generate_success_strips_env_key_before_bearer(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GROK_API_KEY", "  xai-secret \n")
        client = GrokClient(_write_config(tmp_path))
        fake = _FakePost(response=_ok_response("grok answer"))
        client._client.post = fake
        assert client.generate("a prompt") == "grok answer"
        _url, kwargs = fake.calls[0]
        assert kwargs["headers"]["Authorization"] == "Bearer xai-secret"
        client.close()

    def test_generate_null_content_maps_to_grok_service_error(self, tmp_path, monkeypatch):
        # content=null on a 200 must map to the typed Grok error, not a blank
        # answer — the shared _extract_content guard covers Grok too.
        monkeypatch.setenv("GROK_API_KEY", "xai-secret")
        client = GrokClient(_write_config(tmp_path))
        req = httpx.Request("POST", _URL)
        resp = httpx.Response(
            200, json={"choices": [{"message": {"content": None}}]}, request=req
        )
        client._client.post = _FakePost(response=resp)
        with pytest.raises(GrokServiceError):
            client.generate("a prompt")
        client.close()

    def test_generate_http_error_maps_to_grok_service_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GROK_API_KEY", "xai-secret")
        client = GrokClient(_write_config(tmp_path))
        client._client.post = _FakePost(response=_status_response(429))
        with pytest.raises(GrokServiceError) as exc:
            client.generate("a prompt")
        assert exc.value.details.get("status") == 429
        client.close()

    def test_generate_timeout_maps_to_grok_service_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GROK_API_KEY", "xai-secret")
        client = GrokClient(_write_config(tmp_path))
        client._client.post = _FakePost(raises=httpx.TimeoutException("timed out"))
        with pytest.raises(GrokServiceError) as exc:
            client.generate("a prompt")
        assert exc.value.details.get("timeout_sec") == 5
        client.close()

    def test_generate_unexpected_error_maps_to_grok_service_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GROK_API_KEY", "xai-secret")
        client = GrokClient(_write_config(tmp_path))
        client._client.post = _FakePost(raises=ValueError("dns failure secret=sk-leak"))
        with pytest.raises(GrokServiceError) as exc:
            client.generate("a prompt")
        assert "ValueError" in exc.value.message
        assert "dns failure" not in exc.value.message
        assert "sk-leak" not in exc.value.message
        assert exc.value.details.get("exc_type") == "ValueError"
        client.close()


# =============================================================================
# ClaudeClient
# =============================================================================

class TestClaudeClient:
    def test_is_available_reflects_api_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
        client = ClaudeClient(_write_config(tmp_path))
        assert client.is_available() is True
        client.close()

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        client2 = ClaudeClient(_write_config(tmp_path))
        assert client2.is_available() is False
        client2.close()

    def test_is_available_false_for_whitespace_only_key(self, tmp_path, monkeypatch):
        # Mirrors TestGrokClient.test_is_available_false_for_whitespace_only_key.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "  \n\t  ")
        client = ClaudeClient(_write_config(tmp_path))
        assert client.is_available() is False
        client.close()

    def test_generate_without_key_raises(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        client = ClaudeClient(_write_config(tmp_path))
        with pytest.raises(ClaudeServiceError) as exc:
            client.generate("a prompt")
        assert exc.value.details.get("required_env") == "ANTHROPIC_API_KEY"
        client.close()

    def test_generate_success_sends_messages_api_request(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
        client = ClaudeClient(_write_config(tmp_path))
        fake = _FakePost(response=_claude_ok_response("claude answer"))
        client._client.post = fake
        assert client.generate("a prompt") == "claude answer"
        url, kwargs = fake.calls[0]
        assert url == "https://api.anthropic.com/v1/messages"
        assert kwargs["headers"]["x-api-key"] == "anthropic-secret"
        assert kwargs["headers"]["anthropic-version"] == "2023-06-01"
        assert kwargs["json"]["model"] == "claude-sonnet-5"
        assert kwargs["json"]["messages"][0]["content"] == "a prompt"
        assert "temperature" not in kwargs["json"]
        client.close()

    def test_generate_success_strips_env_key_before_header(self, tmp_path, monkeypatch):
        # Mirrors TestGrokClient.test_generate_success_strips_env_key_before_bearer:
        # a padded env value must not leak whitespace into the x-api-key header.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "  anthropic-secret \n")
        client = ClaudeClient(_write_config(tmp_path))
        fake = _FakePost(response=_claude_ok_response("claude answer"))
        client._client.post = fake
        assert client.generate("a prompt") == "claude answer"
        _url, kwargs = fake.calls[0]
        assert kwargs["headers"]["x-api-key"] == "anthropic-secret"
        client.close()

    def test_generate_empty_content_maps_to_claude_service_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
        client = ClaudeClient(_write_config(tmp_path))
        client._client.post = _FakePost(response=_claude_ok_response("   "))
        with pytest.raises(ClaudeServiceError):
            client.generate("a prompt")
        client.close()

    def test_generate_malformed_response_maps_to_claude_service_error(self, tmp_path, monkeypatch):
        # _extract_claude_content's shape (data["content"] list of {"type","text"}
        # blocks) is distinct from the OpenAI-style choices[0].message.content
        # shape Grok/local use — a response missing "content" entirely must still
        # map to the typed error, not a leaked KeyError.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
        client = ClaudeClient(_write_config(tmp_path))
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        resp = httpx.Response(200, json={"unexpected": "shape"}, request=req)
        client._client.post = _FakePost(response=resp)
        with pytest.raises(ClaudeServiceError):
            client.generate("a prompt")
        client.close()

    def test_generate_http_error_maps_to_claude_service_error(self, tmp_path, monkeypatch):
        # Mirrors TestGrokClient.test_generate_http_error_maps_to_grok_service_error.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
        client = ClaudeClient(_write_config(tmp_path))
        client._client.post = _FakePost(response=_status_response(529))  # Anthropic "overloaded"
        with pytest.raises(ClaudeServiceError) as exc:
            client.generate("a prompt")
        assert exc.value.details.get("status") == 529
        client.close()

    def test_generate_timeout_maps_to_claude_service_error(self, tmp_path, monkeypatch):
        # Mirrors TestGrokClient.test_generate_timeout_maps_to_grok_service_error.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
        client = ClaudeClient(_write_config(tmp_path))
        client._client.post = _FakePost(raises=httpx.TimeoutException("timed out"))
        with pytest.raises(ClaudeServiceError) as exc:
            client.generate("a prompt")
        assert exc.value.details.get("timeout_sec") == 5
        client.close()

    def test_generate_unexpected_error_maps_to_claude_service_error(self, tmp_path, monkeypatch):
        # Mirrors TestGrokClient.test_generate_unexpected_error_maps_to_grok_service_error.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
        client = ClaudeClient(_write_config(tmp_path))
        client._client.post = _FakePost(raises=ValueError("dns failure secret=sk-leak"))
        with pytest.raises(ClaudeServiceError) as exc:
            client.generate("a prompt")
        assert "ValueError" in exc.value.message
        assert "dns failure" not in exc.value.message
        assert "sk-leak" not in exc.value.message
        assert exc.value.details.get("exc_type") == "ValueError"
        client.close()


# =============================================================================
# Retry / exponential backoff (shared _post_with_retry helper)
# =============================================================================

@pytest.fixture
def no_sleep(monkeypatch):
    """Patch llm.client.time.sleep so backoff is instant; record the delays."""
    delays: list[float] = []
    monkeypatch.setattr("llm.client.time.sleep", lambda s: delays.append(s))
    return delays


class TestRetryBehavior:
    def test_default_config_does_not_retry(self, tmp_path, no_sleep):
        # No retry block in config -> max_retries == 0 -> single attempt only.
        client = LocalLLMClient(_write_config(tmp_path))
        fake = _ScriptedPost([_status_response(503)])
        client._client.post = fake
        with pytest.raises(LLMServiceError):
            client.generate("p")
        assert len(fake.calls) == 1
        assert no_sleep == []
        client.close()

    def test_retries_then_succeeds_on_transient_5xx(self, tmp_path, no_sleep):
        client = LocalLLMClient(_write_config(tmp_path, retry={"max_retries": 2, "backoff_base_sec": 0.5}))
        # Two 503s then a 200 -> recovers on the third attempt.
        fake = _ScriptedPost([_status_response(503), _status_response(503), _ok_response("recovered")])
        client._client.post = fake
        assert client.generate("p") == "recovered"
        assert len(fake.calls) == 3
        assert no_sleep == [0.5, 1.0]  # backoff_base * 2**attempt for attempts 0,1
        client.close()

    def test_retries_exhausted_raises(self, tmp_path, no_sleep):
        client = LocalLLMClient(_write_config(tmp_path, retry={"max_retries": 2, "backoff_base_sec": 0.5}))
        fake = _ScriptedPost([_status_response(500)])  # persistent failure
        client._client.post = fake
        with pytest.raises(LLMServiceError) as exc:
            client.generate("p")
        assert exc.value.details.get("status") == 500
        assert len(fake.calls) == 3  # 1 initial + 2 retries
        assert no_sleep == [0.5, 1.0]
        client.close()

    def test_backoff_is_capped_at_backoff_max_sec(self, tmp_path, no_sleep):
        # A large base with several retries would otherwise sleep 10s, 20s, 40s, 80s.
        # backoff_max_sec clamps every individual sleep so a mis-tuned config can
        # never block the worker for an unbounded time.
        client = LocalLLMClient(
            _write_config(
                tmp_path,
                retry={"max_retries": 4, "backoff_base_sec": 10.0, "backoff_max_sec": 5.0},
            )
        )
        fake = _ScriptedPost([_status_response(503)])  # persistent transient failure
        client._client.post = fake
        with pytest.raises(LLMServiceError):
            client.generate("p")
        assert len(fake.calls) == 5  # 1 initial + 4 retries
        # Uncapped would be [10.0, 20.0, 40.0, 80.0]; the cap pins each to 5.0.
        assert no_sleep == [5.0, 5.0, 5.0, 5.0]
        client.close()

    def test_backoff_max_defaults_when_absent(self, tmp_path, no_sleep):
        # No backoff_max_sec key -> falls back to the module default (30s), so a
        # config that predates this option still bounds its sleeps.
        client = LocalLLMClient(
            _write_config(tmp_path, retry={"max_retries": 3, "backoff_base_sec": 50.0})
        )
        fake = _ScriptedPost([_status_response(500)])
        client._client.post = fake
        with pytest.raises(LLMServiceError):
            client.generate("p")
        # Uncapped: [50, 100, 200]; default cap pins each to 30.0.
        assert no_sleep == [30.0, 30.0, 30.0]
        client.close()

    def test_client_error_4xx_fails_fast(self, tmp_path, no_sleep):
        # A 400 is not transient: no retry even with retries configured.
        client = LocalLLMClient(_write_config(tmp_path, retry={"max_retries": 3, "backoff_base_sec": 0.5}))
        fake = _ScriptedPost([_status_response(400)])
        client._client.post = fake
        with pytest.raises(LLMServiceError) as exc:
            client.generate("p")
        assert exc.value.details.get("status") == 400
        assert len(fake.calls) == 1
        assert no_sleep == []
        client.close()

    def test_grok_timeout_is_retried(self, tmp_path, monkeypatch, no_sleep):
        # Grok keeps retry-on-timeout: a transient network blip behind its short
        # cloud timeout is worth one more attempt.
        monkeypatch.setenv("GROK_API_KEY", "xai-secret")
        client = GrokClient(_write_config(tmp_path, retry={"max_retries": 1, "backoff_base_sec": 2.0}))
        fake = _ScriptedPost([httpx.TimeoutException("slow"), _ok_response("ok after timeout")])
        client._client.post = fake
        assert client.generate("p") == "ok after timeout"
        assert len(fake.calls) == 2
        assert no_sleep == [2.0]
        client.close()

    def test_local_timeout_fails_fast_without_retry(self, tmp_path, no_sleep):
        # A local read timeout means LM Studio is stalled ("0% processing");
        # retrying just burns another full timeout_sec on an orphaned thread while
        # the gateway's graph deadline has already returned 504. So local fails
        # fast on timeout even with retries configured — unlike transport/5xx.
        client = LocalLLMClient(_write_config(tmp_path, retry={"max_retries": 2, "backoff_base_sec": 2.0}))
        fake = _ScriptedPost([httpx.TimeoutException("slow"), _ok_response("would-be recovery")])
        client._client.post = fake
        with pytest.raises(LLMServiceError) as exc:
            client.generate("p")
        assert exc.value.details.get("timeout_sec") is not None
        assert len(fake.calls) == 1  # single attempt, no retry on timeout
        assert no_sleep == []
        client.close()

    def test_transport_error_is_retried(self, tmp_path, no_sleep):
        client = LocalLLMClient(_write_config(tmp_path, retry={"max_retries": 1, "backoff_base_sec": 1.0}))
        req = httpx.Request("POST", _URL)
        fake = _ScriptedPost([httpx.ConnectError("refused", request=req), _ok_response("ok")])
        client._client.post = fake
        assert client.generate("p") == "ok"
        assert len(fake.calls) == 2
        client.close()

    def test_grok_429_is_retried(self, tmp_path, monkeypatch, no_sleep):
        # 429 (rate limit) is transient for Grok; 4xx like 401 would not be.
        monkeypatch.setenv("GROK_API_KEY", "xai-secret")
        client = GrokClient(_write_config(tmp_path, retry={"max_retries": 2, "backoff_base_sec": 1.0}))
        fake = _ScriptedPost([_status_response(429), _ok_response("grok ok")])
        client._client.post = fake
        assert client.generate("p") == "grok ok"
        assert len(fake.calls) == 2
        client.close()

    def test_grok_401_fails_fast(self, tmp_path, monkeypatch, no_sleep):
        monkeypatch.setenv("GROK_API_KEY", "xai-secret")
        client = GrokClient(_write_config(tmp_path, retry={"max_retries": 3, "backoff_base_sec": 1.0}))
        fake = _ScriptedPost([_status_response(401)])
        client._client.post = fake
        with pytest.raises(GrokServiceError) as exc:
            client.generate("p")
        assert exc.value.details.get("status") == 401
        assert len(fake.calls) == 1  # no wasted retries / credits on auth failure
        client.close()

    def test_claude_timeout_is_retried(self, tmp_path, monkeypatch, no_sleep):
        # Mirrors test_grok_timeout_is_retried: ClaudeClient.generate() goes
        # through the same shared _post_with_retry with the default
        # retry_on_timeout=True (it's a cloud API, not the local stall case).
        monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
        client = ClaudeClient(_write_config(tmp_path, retry={"max_retries": 1, "backoff_base_sec": 2.0}))
        fake = _ScriptedPost([httpx.TimeoutException("slow"), _claude_ok_response("ok after timeout")])
        client._client.post = fake
        assert client.generate("p") == "ok after timeout"
        assert len(fake.calls) == 2
        assert no_sleep == [2.0]
        client.close()

    def test_claude_429_is_retried(self, tmp_path, monkeypatch, no_sleep):
        # Mirrors test_grok_429_is_retried.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
        client = ClaudeClient(_write_config(tmp_path, retry={"max_retries": 2, "backoff_base_sec": 1.0}))
        fake = _ScriptedPost([_status_response(429), _claude_ok_response("claude ok")])
        client._client.post = fake
        assert client.generate("p") == "claude ok"
        assert len(fake.calls) == 2
        client.close()

    def test_claude_401_fails_fast(self, tmp_path, monkeypatch, no_sleep):
        # Mirrors test_grok_401_fails_fast.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
        client = ClaudeClient(_write_config(tmp_path, retry={"max_retries": 3, "backoff_base_sec": 1.0}))
        fake = _ScriptedPost([_status_response(401)])
        client._client.post = fake
        with pytest.raises(ClaudeServiceError) as exc:
            client.generate("p")
        assert exc.value.details.get("status") == 401
        assert len(fake.calls) == 1  # no wasted retries / credits on auth failure
        client.close()

    def test_transient_retry_logs_warning(self, tmp_path, no_sleep, caplog):
        # A retried transient failure must leave a WARNING breadcrumb tagged with
        # the service + attempt count — previously the retry was entirely silent.
        client = LocalLLMClient(_write_config(tmp_path, retry={"max_retries": 1, "backoff_base_sec": 0.5}))
        fake = _ScriptedPost([_status_response(503), _ok_response("ok")])
        client._client.post = fake
        with caplog.at_level("WARNING", logger="llm.client"):
            assert client.generate("p") == "ok"
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1
        assert "ollama" in warnings[0].message
        assert "503" in warnings[0].message
        client.close()

    def test_exhausted_retries_log_error(self, tmp_path, no_sleep, caplog):
        # When retries are exhausted, the give-up must log an ERROR (not just
        # raise) so an operator can see the call failed and how many attempts ran.
        client = LocalLLMClient(_write_config(tmp_path, retry={"max_retries": 1, "backoff_base_sec": 0.5}))
        fake = _ScriptedPost([_status_response(500)])  # persistent failure
        client._client.post = fake
        with caplog.at_level("ERROR", logger="llm.client"):
            with pytest.raises(LLMServiceError):
                client.generate("p")
        errors = [r for r in caplog.records if r.levelname == "ERROR"]
        assert len(errors) == 1
        assert "ollama" in errors[0].message
        assert "500" in errors[0].message
        client.close()

    def test_non_retryable_error_logs_type_not_content(self, tmp_path, no_sleep, caplog):
        # A malformed-body failure (non-retryable) must log the exception *type*
        # only — never the response content, which could carry sensitive text.
        client = LocalLLMClient(_write_config(tmp_path))
        fake = _ScriptedPost([_ok_response(None)])  # null content -> ValueError in _extract_content
        client._client.post = fake
        with caplog.at_level("ERROR", logger="llm.client"):
            with pytest.raises(LLMServiceError):
                client.generate("p")
        errors = [r for r in caplog.records if r.levelname == "ERROR"]
        assert len(errors) == 1
        assert "ollama" in errors[0].message
        assert "non-retryable" in errors[0].message
        client.close()


class TestRetryConfigBounds:
    """Negative retry config values are clamped before they can corrupt a call.

    max_retries < 0 would make range(max_retries + 1) empty, letting every
    call fall through to the unreachable AssertionError sentinel. A negative
    backoff_base would pass a negative number to time.sleep(), raising
    ValueError on the first retry attempt.
    """

    def test_negative_max_retries_clamped_to_zero(self, tmp_path):
        client = LocalLLMClient(_write_config(tmp_path, retry={"max_retries": -5}))
        assert client.retry_max == 0
        client.close()

    def test_negative_backoff_base_clamped_to_zero(self, tmp_path):
        client = LocalLLMClient(_write_config(tmp_path, retry={"max_retries": 1, "backoff_base_sec": -2.0}))
        assert client.retry_backoff == 0.0
        client.close()

    def test_negative_backoff_max_clamped_to_zero(self, tmp_path):
        client = LocalLLMClient(_write_config(tmp_path, retry={"max_retries": 1, "backoff_max_sec": -1.0}))
        assert client.retry_backoff_max == 0.0
        client.close()

    def test_grok_negative_max_retries_clamped(self, tmp_path):
        client = GrokClient(_write_config(tmp_path, retry={"max_retries": -99}))
        assert client.retry_max == 0
        client.close()

    def test_claude_negative_max_retries_clamped(self, tmp_path):
        # Mirrors test_grok_negative_max_retries_clamped.
        client = ClaudeClient(_write_config(tmp_path, retry={"max_retries": -99}))
        assert client.retry_max == 0
        client.close()


# =============================================================================
# Local backend failover (Ollama → LM Studio)
# =============================================================================

class TestResolveLocalBackend:
    def test_fallback_disabled_no_probe(self, monkeypatch):
        probes: list[str] = []

        def capture(url, **kw):
            probes.append(url)
            return True

        monkeypatch.setattr("llm.client._probe_openai_models", capture)
        llm_cfg = {
            "provider": "ollama",
            "base_url": "http://127.0.0.1:11434/v1",  # DevSkim: ignore DS162092
            "model": "qwen2.5:7b",
            "fallback": {"enabled": False},
        }
        resolved = resolve_local_backend(llm_cfg)
        assert resolved.source == "primary"
        assert resolved.provider == "ollama"
        assert probes == []

    def test_fallback_uses_secondary_when_primary_down(self, monkeypatch):
        def probe(base_url, **kw):
            return "1234" in base_url

        monkeypatch.setattr("llm.client._probe_openai_models", probe)
        monkeypatch.setattr("utils.logger.audit_log", lambda *a, **k: None)
        llm_cfg = {
            "provider": "ollama",
            "base_url": "http://127.0.0.1:11434/v1",  # DevSkim: ignore DS162092
            "model": "qwen2.5:7b",
            "fallback": {
                "enabled": True,
                "provider": "lmstudio",
                "base_url": "http://127.0.0.1:1234/v1",  # DevSkim: ignore DS162092
                "model": "local-gguf-model",
                "probe_timeout_sec": 0.5,
            },
        }
        resolved = resolve_local_backend(llm_cfg)
        assert resolved.source == "fallback"
        assert resolved.provider == "lmstudio"
        assert resolved.model == "local-gguf-model"
        assert "1234" in resolved.base_url

    def test_fallback_keeps_primary_when_probe_ok(self, monkeypatch):
        monkeypatch.setattr("llm.client._probe_openai_models", lambda *a, **k: True)
        llm_cfg = {
            "provider": "ollama",
            "base_url": "http://127.0.0.1:11434/v1",  # DevSkim: ignore DS162092
            "model": "qwen2.5:7b",
            "fallback": {
                "enabled": True,
                "provider": "lmstudio",
                "base_url": "http://127.0.0.1:1234/v1",  # DevSkim: ignore DS162092
                "model": "other-model",
            },
        }
        resolved = resolve_local_backend(llm_cfg)
        assert resolved.source == "primary"
        assert resolved.model == "qwen2.5:7b"

    def test_fallback_enabled_requires_model(self):
        llm_cfg = {
            "base_url": "http://127.0.0.1:11434/v1",  # DevSkim: ignore DS162092
            "model": "qwen2.5:7b",
            "fallback": {
                "enabled": True,
                "base_url": "http://127.0.0.1:1234/v1",  # DevSkim: ignore DS162092
                "model": "",
            },
        }
        with pytest.raises(LLMServiceError, match="fallback requires"):
            resolve_local_backend(llm_cfg)

    def test_fallback_rejects_non_loopback_secondary(self):
        llm_cfg = {
            "base_url": "http://127.0.0.1:11434/v1",  # DevSkim: ignore DS162092
            "model": "qwen2.5:7b",
            "fallback": {
                "enabled": True,
                "base_url": "http://10.0.0.5:1234/v1",  # DevSkim: ignore DS162092
                "model": "x",
            },
        }
        with pytest.raises(LLMServiceError, match="loopback"):
            resolve_local_backend(llm_cfg)

    def test_client_generate_uses_fallback_url_and_model(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "llm.client._probe_openai_models",
            lambda base_url, **kw: "1234" in base_url,
        )
        monkeypatch.setattr("utils.logger.audit_log", lambda *a, **k: None)
        path = _write_config(
            tmp_path,
            local_llm_extra={
                "provider": "ollama",
                "base_url": "http://127.0.0.1:11434/v1",  # DevSkim: ignore DS162092
                "model": "qwen2.5:7b",
                "fallback": {
                    "enabled": True,
                    "provider": "lmstudio",
                    "base_url": "http://127.0.0.1:1234/v1",  # DevSkim: ignore DS162092
                    "model": "lmstudio-model",
                },
            },
        )
        client = LocalLLMClient(path)
        assert client.backend_source == "fallback"
        assert client.model == "lmstudio-model"
        assert client.provider == "lmstudio"
        fake = _FakePost(response=_ok_response("from lmstudio"))
        client._client.post = fake
        assert client.generate("hi") == "from lmstudio"
        url, kwargs = fake.calls[0]
        assert "1234" in url
        assert kwargs["json"]["model"] == "lmstudio-model"
        client.close()

    def test_both_probes_fail_still_uses_primary(self, monkeypatch):
        monkeypatch.setattr("llm.client._probe_openai_models", lambda *a, **k: False)
        llm_cfg = {
            "provider": "ollama",
            "base_url": "http://127.0.0.1:11434/v1",  # DevSkim: ignore DS162092
            "model": "qwen2.5:7b",
            "fallback": {
                "enabled": True,
                "provider": "lmstudio",
                "base_url": "http://127.0.0.1:1234/v1",  # DevSkim: ignore DS162092
                "model": "x",
            },
        }
        resolved = resolve_local_backend(llm_cfg)
        assert resolved.source == "primary"
        assert resolved.provider == "ollama"
