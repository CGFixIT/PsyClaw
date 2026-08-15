"""Real-HTTP-server tests for llm.client.LocalLLMClient against a mock Ollama endpoint.

``tests/test_client.py`` already exhaustively covers ``LocalLLMClient``'s
error-mapping and retry contract by monkeypatching ``httpx.Client.post`` —
real ``httpx.Request``/``Response`` objects, but no real socket. This file
covers the one thing that can't: a genuine HTTP server on a real loopback
port, so the actual wire protocol (JSON request/response bytes over TCP) is
exercised end-to-end rather than Python-level objects swapped in for the
transport.

``LocalLLMClient`` targets Ollama's OpenAI-compatible ``/chat/completions``
endpoint in production (``config.yaml`` ``models.local_llm``, CLAUDE.md) —
the class name predates the LM Studio -> Ollama migration, hence the
``_ollama`` suffix on this file rather than the class name.

No live Ollama daemon is contacted: the mock server below is a plain
``http.server.HTTPServer`` bound to an ephemeral ``127.0.0.1`` port,
matching the same minimal-mock pattern used by
``.claude/skills/CyClaw-Sandbox/mock_ollama.py``, scoped down to just the
one endpoint this test needs.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

import pytest
import yaml

from llm.client import LocalLLMClient, ResolvedLocalBackend
from utils.errors import LLMServiceError


class _OllamaLikeHandler(BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible ``/chat/completions`` responder, scripted per-server."""

    def log_message(self, fmt: str, *args: object) -> None:  # silence test-run noise
        pass

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {}
        self.server.received.append(body)  # type: ignore[attr-defined]
        if self.server.script:  # type: ignore[attr-defined]
            status, payload = self.server.script.pop(0)  # type: ignore[attr-defined]
        else:
            status, payload = 200, {"choices": [{"message": {"content": "default"}}]}
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class _MockOllamaServer(HTTPServer):
    """Adds a response script + a received-request-bodies log to assert on."""

    def __init__(self, script: list[tuple[int, dict]]):
        super().__init__(("127.0.0.1", 0), _OllamaLikeHandler)
        self.script = list(script)
        self.received: list[dict] = []


@pytest.fixture
def mock_ollama():
    """Yields a factory: script -> (base_url, server). Servers are torn down after the test."""
    servers: list[_MockOllamaServer] = []

    def factory(script: list[tuple[int, dict]]) -> tuple[str, _MockOllamaServer]:
        server = _MockOllamaServer(script)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        port = server.server_address[1]
        servers.append(server)
        return f"http://127.0.0.1:{port}/v1", server

    yield factory
    for s in servers:
        s.shutdown()
        s.server_close()


def _write_config(tmp_path, base_url: str) -> str:
    cfg = {
        "models": {
            "local_llm": {
                "base_url": base_url,
                "model": "qwen3.8:27b",
                "max_tokens": 256,
                "temperature": 0.1,
                "timeout_sec": 5,
            }
        }
    }
    p = tmp_path / "config.yaml"
    with open(p, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f)
    return str(p)


class TestLocalLLMClientAgainstRealHTTPServer:
    """Exercises ``LocalLLMClient.generate()`` over a real socket, not a monkeypatch."""

    def test_generate_success_over_real_socket(self, tmp_path, mock_ollama):
        base_url, _server = mock_ollama(
            [(200, {"choices": [{"message": {"content": "real socket answer"}}]})]
        )
        client = LocalLLMClient(_write_config(tmp_path, base_url))
        assert client.generate("hello") == "real socket answer"
        client.close()

    def test_request_shape_matches_openai_compatible_contract(self, tmp_path, mock_ollama):
        # Confirms the actual bytes CyClaw sends -- read back off a real TCP
        # connection, not a mocked call's kwargs -- deserialize into the
        # model/messages/max_tokens/temperature shape Ollama's OpenAI-compatible
        # endpoint expects.
        base_url, server = mock_ollama([(200, {"choices": [{"message": {"content": "ok"}}]})])
        client = LocalLLMClient(_write_config(tmp_path, base_url))
        client.generate("what is CyClaw?")
        client.close()
        assert len(server.received) == 1
        body = server.received[0]
        assert body["model"] == "qwen3.8:27b"
        assert body["messages"] == [{"role": "user", "content": "what is CyClaw?"}]
        assert body["max_tokens"] == 256
        assert body["temperature"] == pytest.approx(0.1)

    def test_http_500_over_real_socket_maps_to_llm_service_error(self, tmp_path, mock_ollama):
        base_url, _server = mock_ollama([(500, {"error": "boom"})])
        client = LocalLLMClient(_write_config(tmp_path, base_url))
        with pytest.raises(LLMServiceError) as exc:
            client.generate("hello")
        assert exc.value.details.get("status") == 500
        client.close()

    def test_null_content_over_real_socket_maps_to_llm_service_error(self, tmp_path, mock_ollama):
        # Mirrors test_client.py's monkeypatched equivalent, but the null-content
        # body is real bytes decoded by httpx off a real response, not an object
        # constructed in-process.
        base_url, _server = mock_ollama(
            [(200, {"choices": [{"message": {"content": None}}]})]
        )
        client = LocalLLMClient(_write_config(tmp_path, base_url))
        with pytest.raises(LLMServiceError):
            client.generate("hello")
        client.close()

    def test_connection_refused_maps_to_llm_service_error(self, tmp_path):
        # A real connection failure (nothing listening on the port) -- not a
        # simulated httpx.TransportError -- must still map to the typed error.
        # Bind an ephemeral server just to reserve a free port, then close it
        # immediately so the port is guaranteed unoccupied when the client connects.
        probe = HTTPServer(("127.0.0.1", 0), _OllamaLikeHandler)
        port = probe.server_address[1]
        probe.server_close()
        client = LocalLLMClient(_write_config(tmp_path, f"http://127.0.0.1:{port}/v1"))
        with pytest.raises(LLMServiceError):
            client.generate("hello")
        client.close()


class TestBackendSwapLockingRegression:
    """Regression guard for the failover-state race: _readopt_backend_if_stale's
    multi-attribute reassignment (base_url/model/api_key/...) and generate()'s
    do_post() snapshot read must serialize through the SAME self._backend_lock --
    otherwise a concurrent reader could observe a torn combination, e.g. the new
    backend's base_url paired with the old backend's api_key.

    Both tests force a real, deterministic interleaving via threading.Event
    synchronization (never a bare sleep racing a timeout): _provider_label is
    patched to pause INSIDE the locked reassignment block, after every
    attribute write, giving a controllable window during which the lock is
    known to be held.
    """

    def _resolved(self, base_url: str) -> ResolvedLocalBackend:
        return ResolvedLocalBackend(
            provider="ollama", base_url=base_url, model="qwen3.8:27b", source="primary",
        )

    def test_readopt_backend_holds_lock_for_its_entire_reassignment(self, tmp_path, mock_ollama):
        base_url, _server = mock_ollama([(200, {"choices": [{"message": {"content": "ok"}}]})])
        client = LocalLLMClient(_write_config(tmp_path, base_url))

        entered_critical_section = threading.Event()
        release_critical_section = threading.Event()

        def fake_label(provider):
            entered_critical_section.set()
            assert release_critical_section.wait(timeout=5), "test timed out waiting for release"
            return provider

        with patch("llm.client.resolve_local_backend", return_value=self._resolved(base_url)), \
             patch("llm.client._provider_label", side_effect=fake_label):
            writer = threading.Thread(target=client._readopt_backend_if_stale)
            writer.start()
            assert entered_critical_section.wait(timeout=5)

            # The reassignment block is mid-execution right now, still inside
            # `with self._backend_lock:` -- a non-blocking acquire must fail.
            assert client._backend_lock.acquire(blocking=False) is False

            release_critical_section.set()
            writer.join(timeout=5)
            assert not writer.is_alive()

        # Lock must be free again once the method has returned.
        assert client._backend_lock.acquire(blocking=False) is True
        client._backend_lock.release()
        client.close()

    def test_concurrent_generate_blocks_until_backend_swap_releases_lock(self, tmp_path, mock_ollama):
        base_url, _server = mock_ollama([(200, {"choices": [{"message": {"content": "ok"}}]})])
        client = LocalLLMClient(_write_config(tmp_path, base_url))
        # Not degraded/recheck: generate() must go straight to do_post() rather
        # than calling _readopt_backend_if_stale itself, so the ONLY reason it
        # could block is contending for self._backend_lock with the writer below.
        assert client._degraded is False
        assert client._recheck_backend is False

        entered_critical_section = threading.Event()
        release_critical_section = threading.Event()
        generate_completed = threading.Event()

        def fake_label(provider):
            entered_critical_section.set()
            assert release_critical_section.wait(timeout=5), "test timed out waiting for release"
            return provider

        def run_generate():
            client.generate("hello")
            generate_completed.set()

        with patch("llm.client.resolve_local_backend", return_value=self._resolved(base_url)), \
             patch("llm.client._provider_label", side_effect=fake_label):
            writer = threading.Thread(target=client._readopt_backend_if_stale)
            writer.start()
            assert entered_critical_section.wait(timeout=5)

            reader = threading.Thread(target=run_generate)
            reader.start()
            # do_post()'s snapshot read contends for the same held lock, so it
            # must not complete while the writer is still inside its section.
            assert generate_completed.wait(timeout=0.3) is False

            release_critical_section.set()
            writer.join(timeout=5)
            reader.join(timeout=5)

        assert generate_completed.is_set()
        client.close()
