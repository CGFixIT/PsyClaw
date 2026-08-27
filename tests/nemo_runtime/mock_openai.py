"""Loopback OpenAI-compatible mock for NeMo engine construction tests.

Stdlib only. Binds 127.0.0.1. Does not load weights and does not call NVIDIA.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def _json(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/").endswith("/models") or self.path.endswith("/v1/models"):
            self._json(
                200,
                {
                    "object": "list",
                    "data": [{"id": "qwen3.8:27b-mlx", "object": "model", "owned_by": "local"}],
                },
            )
            return
        self._json(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b""
        try:
            req = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            req = {}
        messages = req.get("messages") or []
        last = ""
        if messages:
            content = messages[-1].get("content", "")
            last = content if isinstance(content, str) else str(content)
        # Conservative self-check contract: "yes" blocks on NVIDIA's yes/no prompts.
        # Return "no" (allow) unless the user text obviously asks to jailbreak.
        answer = "no"
        low = last.lower()
        if "ignore previous" in low or "jailbreak" in low or "system prompt" in low:
            answer = "yes"
        if "grounded" in low or "evidence" in low:
            answer = "yes"
        self._json(
            200,
            {
                "id": "chatcmpl-mock",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": answer},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )


class LoopbackOpenAIMock:
    """Threading HTTP server on 127.0.0.1 with an ephemeral port."""

    def __init__(self) -> None:
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._httpd.server_address
        return f"http://{host}:{port}/v1"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
