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

from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from utils.errors import AgenticError


class HarnessLLMError(AgenticError):
    """Chat call failed (unreachable, refused, or malformed response)."""

    def __init__(self, message: str, code: str = "HARNESS_LLM_ERROR", details: dict | None = None):
        super().__init__(message, code=code, details=details)


@dataclass(frozen=True)
class ChatResult:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int


def _is_loopback(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host in {"127.0.0.1", "localhost", "::1"}


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
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            resp = self._client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise HarnessLLMError(
                "model server unreachable — is Ollama running?",
                details={"base_url": self.base_url, "error": str(exc)},
            ) from exc
        if resp.status_code != 200:
            raise HarnessLLMError(
                f"model server returned HTTP {resp.status_code}",
                details={"body": resp.text[:300]},
            )
        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage") or {}
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise HarnessLLMError("malformed response from model server") from exc
        return ChatResult(
            content=str(content),
            model=str(data.get("model", use_model)),
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
        )
