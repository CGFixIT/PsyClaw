"""Minimal chat client for the harness console.

Talks to the OpenAI-compatible endpoint configured in CyClaw's
``local_llm.base_url`` (Ollama by default — free, offline, no login). Chosen
over the native Ollama API because the configured base_url already points at
the ``/v1`` compatibility surface, and its ``usage`` block gives the
prompt/completion token counts the console tallies.

The httpx transport is injectable so tests use ``MockTransport`` and never
require a live Ollama (the same pattern as ``agentic.harness_optimizer``'s
proposer client). Loopback URLs only: a non-loopback base_url is refused
rather than quietly sending prompts to a remote host.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from utils.errors import AgenticError

logger = logging.getLogger("cyclaw.harness.ollama")

_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
_HTTP_OK = 200
_ERROR_BODY_PREVIEW = 300


class HarnessLLMError(AgenticError):
    """Chat call failed (unreachable, refused, or malformed response)."""

    def __init__(self, message: str, code: str = "HARNESS_LLM_ERROR", details: dict | None = None):
        super().__init__(message, code=code, details=details)


@dataclass(frozen=True)
class ChatResult:
    body_text: str
    model: str
    prompt_tokens: int
    completion_tokens: int


def _is_loopback(url: str) -> bool:
    return (urlparse(url).hostname or "") in _LOOPBACK_HOSTS


def _parse_chat_response(resp: httpx.Response, fallback_model: str) -> ChatResult:
    """Extract body text + token usage, or raise a typed error."""
    try:
        parsed = resp.json()
    except ValueError as exc:
        raise HarnessLLMError("malformed response from model server") from exc
    if not isinstance(parsed, dict):
        raise HarnessLLMError("malformed response from model server")
    first = (parsed.get("choices") or [{}])[0]
    if not isinstance(first, dict):
        first = {}
    body = first.get("message", {})
    if not isinstance(body, dict):
        body = {}
    body_text = body.get("content")
    if not isinstance(body_text, str):
        raise HarnessLLMError("malformed response from model server")
    usage = parsed.get("usage") or {}
    return ChatResult(
        body_text=body_text,
        model=str(parsed.get("model", fallback_model)),
        prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
        completion_tokens=int(usage.get("completion_tokens", 0) or 0),
    )


class HarnessChatClient:
    """OpenAI-compatible chat client with token-usage extraction."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_sec: float = 300.0,
        api_key: str = "",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not _is_loopback(base_url):
            raise HarnessLLMError(
                "harness chat only targets loopback model servers",
                details={"base_url": base_url},
            )
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key.strip()
        self._client = httpx.Client(timeout=timeout_sec, transport=transport)

    def close(self) -> None:
        self._client.close()

    def chat(
        self,
        *,
        system_prompt: str,
        messages: list[dict],
        model: str | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.3,
    ) -> ChatResult:
        """One chat completion. ``messages`` are prior turns (user/assistant)."""
        use_model = (model or self.model).strip()
        if not use_model:
            raise HarnessLLMError("no model selected; set one with /model use <name>")
        payload = {
            "model": use_model,
            "messages": [{"role": "system", "content": system_prompt}, *messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        auth = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            resp = self._client.post(f"{self.base_url}/chat/completions", json=payload, headers=auth)
        except httpx.HTTPError as exc:
            # str(exc) on an httpx error embeds the full request URL and, for
            # some transport errors, the underlying OS message. The exception
            # TYPE is what an operator actually acts on (ConnectError vs
            # ReadTimeout), so send that and keep the rest out of the browser.
            # Same call guardrails/integration.py makes for provider errors.
            logger.debug("harness chat transport failure", exc_info=True)
            raise HarnessLLMError(
                "model server unreachable — is Ollama running?",
                details={"base_url": self.base_url, "error": type(exc).__name__},
            ) from exc
        if resp.status_code != _HTTP_OK:
            # The response body is upstream-controlled bytes. base_url is
            # loopback-only, but "loopback" routinely means an OpenAI-compatible
            # proxy the operator points at a remote provider (backend.api_key
            # exists for exactly that), and a 401/403 body from such a proxy can
            # echo the presented Authorization header or an internal upstream
            # URL. harness/server.py:_err puts details straight into the
            # HTTPException the console renders, and there is no sanitize_error
            # on this side the way gate.py injects one into gate_ops.py.
            # Keep the body for the operator's own terminal, not the browser.
            logger.debug(
                "harness chat upstream HTTP %s: %s",
                resp.status_code,
                resp.text[:_ERROR_BODY_PREVIEW],
            )
            raise HarnessLLMError(f"model server returned HTTP {resp.status_code}")
        return _parse_chat_response(resp, use_model)
