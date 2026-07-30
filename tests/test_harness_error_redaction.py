"""The harness chat client must not hand upstream bytes to the console.

harness/server.py:_err serializes ``HarnessLLMError.details`` straight into the
HTTPException the browser renders, and the harness -- unlike gate_ops.py, which
gets a ``sanitize_error`` callable injected by gate.py -- has no redaction layer
of its own. So the redaction has to happen where the error is raised.

No live services: httpx MockTransport stands in for the model server, and
harness.ollama imports only httpx + utils.errors, so this file needs no FastAPI.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from harness.ollama import HarnessChatClient, HarnessLLMError

_SECRET = "Bearer sk-live-operator-token-do-not-leak"
_UPSTREAM_URL = "https://internal-gateway.corp.example/v1/chat/completions"


def _client(handler) -> HarnessChatClient:
    return HarnessChatClient(
        base_url="http://127.0.0.1:11434/v1",
        model="qwen2.5:7b",
        transport=httpx.MockTransport(handler),
    )


def _chat(client: HarnessChatClient):
    return client.chat(system_prompt="sys", messages=[{"role": "user", "content": "hi"}])


def test_upstream_error_body_is_not_returned_to_the_console():
    """A 401 body from a local OpenAI-compatible proxy can echo the presented
    Authorization header or an internal upstream URL. Neither may reach details."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"message": f"rejected {_SECRET} at {_UPSTREAM_URL}"}},
        )

    with pytest.raises(HarnessLLMError) as excinfo:
        _chat(_client(handler))

    exc = excinfo.value
    assert "401" in exc.message, "the status code is the actionable part -- keep it"
    serialized = repr(exc.details) + exc.message
    assert _SECRET not in serialized
    assert _UPSTREAM_URL not in serialized
    assert "rejected" not in serialized


def test_upstream_error_body_still_reaches_the_operator_log(caplog):
    """Redaction moves the body to the server-side log, it does not discard it."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream stack trace here")

    with caplog.at_level(logging.DEBUG, logger="cyclaw.harness.ollama"):
        with pytest.raises(HarnessLLMError):
            _chat(_client(handler))

    assert "upstream stack trace here" in caplog.text


def test_transport_failure_reports_exception_type_not_its_message():
    """str() on an httpx error embeds the full request URL and OS message; the
    exception TYPE is what an operator acts on (ConnectError vs ReadTimeout)."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused to " + _UPSTREAM_URL, request=request)

    with pytest.raises(HarnessLLMError) as excinfo:
        _chat(_client(handler))

    details = excinfo.value.details
    assert details["error"] == "ConnectError"
    assert _UPSTREAM_URL not in repr(details)
    # base_url is operator-supplied config and loopback-only -- keep it, it is
    # the single most useful field when the model server is not running.
    assert details["base_url"] == "http://127.0.0.1:11434/v1"


def test_successful_chat_is_unaffected():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "model": "qwen2.5:7b",
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        })

    result = _chat(_client(handler))
    assert result.body_text == "ok"
    assert result.prompt_tokens == 11
    assert result.completion_tokens == 7
