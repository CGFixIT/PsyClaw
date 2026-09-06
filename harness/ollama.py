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
import os
import socket
import struct
import threading
from contextlib import suppress
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from harness.chat_cancel import arm_cancel_reads
from utils.errors import AgenticError

logger = logging.getLogger("cyclaw.harness.ollama")

_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
_HTTP_OK = 200
_ERROR_BODY_PREVIEW = 300
# Keep missing-key/direct-construction behavior aligned with the shipped 27B
# local-model budget. Explicit config values still override this fallback.
DEFAULT_CHAT_TIMEOUT_SEC = 720.0
# linger-on, timeout 0: abort() sends RST so a Darwin recv() in another
# thread actually returns. shutdown()+close() alone left the hung POST
# blocked on macos-latest CI (join(2) still alive).
_LINGER_RST = struct.pack("ii", 1, 0)


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


def _inflight_socket(conn: object) -> object:
    inner = getattr(conn, "_connection", None)
    stream = getattr(inner, "_network_stream", None)
    sock = getattr(stream, "_sock", None)
    if sock is not None:
        return sock
    # Bandit B610 matches any call named extra() as Django QuerySet.extra.
    get_extra_info = getattr(stream, "get_extra_info", None)
    return get_extra_info("socket") if callable(get_extra_info) else None


def _yank_socket_fd(sock: object) -> None:
    """RST + close the fd so Darwin's blocked recv() returns.

    detach() first so later sock.close() / GC cannot close a recycled fd.
    """
    with suppress(OSError, AttributeError, TypeError):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, _LINGER_RST)
    with suppress(OSError, AttributeError):
        sock.shutdown(socket.SHUT_RDWR)
    try:
        fd = sock.fileno()
    except (OSError, AttributeError, TypeError):
        fd = -1
    detach = getattr(sock, "detach", None)
    if callable(detach):
        with suppress(OSError, AttributeError):
            detach()
    if fd >= 0:
        with suppress(OSError):
            os.close(fd)
    with suppress(OSError, AttributeError):
        sock.close()


def _shutdown_inflight_sockets(client: httpx.Client) -> None:
    """Force-close TCP sockets of any in-flight request on ``client``.

    ``httpx.Client.close()`` does not abort an active POST (httpx 0.28.1 /
    httpcore). Without this, ``/api/chat/cancel`` reports cancelled while
    ``GenerationGate`` stays held until the read timeout
    (``models.local_llm.timeout_sec``, shipped 720s). Darwin also ignores a
    cross-thread ``shutdown()``+``close()`` on a blocked ``recv()``; an
    ``SO_LINGER`` RST plus closing the fd is the fast path. Sliced reads in
    ``harness.chat_cancel`` still abort if the socket cannot be found.
    """
    transport = getattr(client, "_transport", None)
    pool = getattr(transport, "_pool", None)
    connections = getattr(pool, "connections", None) or getattr(pool, "_connections", None)
    if not connections:
        return
    for conn in list(connections):
        sock = _inflight_socket(conn)
        if sock is not None:
            _yank_socket_fd(sock)
        closer = getattr(conn, "close", None)
        if callable(closer):
            with suppress(OSError, AttributeError, RuntimeError):
                closer()


def _token_count(raw_count: object) -> int:
    """Best-effort int for one usage counter; malformed values degrade to 0."""
    # A helper rather than inline in _parse_chat_response: WPS229 caps try
    # bodies at one statement and WPS210/WPS231 cap that function's locals
    # and complexity, so the degrade-to-0 guard has to live here.
    try:
        return int(raw_count or 0)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0


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
    # usage is cosmetic (console token tally) — a proxy sending a non-dict
    # usage block or non-numeric counts must degrade the tally to 0, not turn
    # a good answer into the unparseable 500 the guards above exist to prevent
    usage = parsed.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    return ChatResult(
        body_text=body_text,
        model=str(parsed.get("model", fallback_model)),
        prompt_tokens=_token_count(usage.get("prompt_tokens")),
        completion_tokens=_token_count(usage.get("completion_tokens")),
    )


class HarnessChatClient:
    """OpenAI-compatible chat client with token-usage extraction."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_sec: float = DEFAULT_CHAT_TIMEOUT_SEC,
        api_key: str = "",
        reasoning_effort: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if (urlparse(base_url).hostname or "") not in _LOOPBACK_HOSTS:
            raise HarnessLLMError(
                "harness chat only targets loopback model servers",
                details={"base_url": base_url},
            )
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key.strip()
        # Already resolved and provider-gated by llm.client.resolve_local_backend
        # (harness/server.py passes ResolvedLocalBackend.reasoning_effort), so
        # this is never validated or re-read from config here. None = omit.
        self.reasoning_effort = reasoning_effort
        self._timeout_sec = timeout_sec
        self._transport = transport
        self._cancel = threading.Event()
        self._client = self._build_client()

    def close(self) -> None:
        self._cancel.set()
        self._client.close()

    def abort_in_flight(self) -> None:
        """Abort an in-flight Ollama POST and open a fresh client.

        ``Client.close()`` is terminal and does not interrupt a blocked
        POST, so the live sockets are shut down first. Recreate rather
        than reuse the closed client. Darwin also needs the sliced-read
        wrapper: SHUT_RDWR alone does not wake a timed recv there.
        """
        old = self._client
        old_cancel = self._cancel
        # Publish the replacement before waking the blocked POST. Shutdown
        # unblocks chat(), whose finally can drop GenerationGate before
        # old.close() returns. A retry that started on old would then die
        # in close().
        self._cancel = threading.Event()
        self._client = self._build_client()
        old_cancel.set()
        _shutdown_inflight_sockets(old)
        old.close()

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
        payload: dict = {
            "model": use_model,
            "messages": [{"role": "system", "content": system_prompt}, *messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        # Same object serves plain chat and /loop, so both carry the control.
        if self.reasoning_effort is not None:
            payload["reasoning_effort"] = self.reasoning_effort
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

    def _build_client(self) -> httpx.Client:
        client = httpx.Client(
            timeout=self._timeout_sec,
            transport=self._transport,
            trust_env=False,
        )
        arm_cancel_reads(client, self._cancel)
        return client
