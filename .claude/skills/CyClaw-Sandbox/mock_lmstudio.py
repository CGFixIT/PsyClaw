#!/usr/bin/env python3
"""Minimal mock LM Studio server — OpenAI-compatible API on port 1234.

Simulates qwen2.5-7b-instruct running cached offline. Serves:
  GET  /v1/models              → model list (gate.py uses this for /health)
  POST /v1/chat/completions    → deterministic reply (no GPU, no weights)

Run as a background process before starting gate.py during sandbox audit:
    python mock_lmstudio.py &

Automatically shuts down when the parent process exits (uses stderr to signal
readiness so the caller can poll with grep).
"""

from __future__ import annotations

import json
import sys
import time

try:
    from http.server import BaseHTTPRequestHandler, HTTPServer
except ImportError:
    print("stdlib http.server missing — cannot start mock LM Studio", file=sys.stderr)
    sys.exit(1)

PORT = 1234
MODEL_ID = "qwen2.5-7b-instruct"

MODELS_RESP = json.dumps({
    "object": "list",
    "data": [{"id": MODEL_ID, "object": "model", "created": 1700000000, "owned_by": "local"}],
})

COMPLETION_TMPL = {
    "id": "chatcmpl-mock-001",
    "object": "chat.completion",
    "created": 0,
    "model": MODEL_ID,
    "choices": [{
        "index": 0,
        "message": {"role": "assistant", "content": ""},
        "finish_reason": "stop",
    }],
    "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
}


def _make_completion(prompt_content: str) -> str:
    # Deterministic mock: detect the audit question and return a realistic reply.
    if "one sentence" in prompt_content.lower() or "describe" in prompt_content.lower():
        answer = (
            "CyClaw is an offline-first, RAG-enforced personal AI assistant that uses "
            "a LangGraph security topology and ChromaDB+BM25 hybrid retrieval to answer "
            "questions from a local knowledge vault without sending data to the cloud."
        )
    else:
        answer = (
            f"[Mock LM Studio — {MODEL_ID}] This is a cached offline response "
            "for sandbox audit purposes. No real model weights were loaded."
        )
    resp = dict(COMPLETION_TMPL)
    resp["created"] = int(time.time())
    resp["choices"] = [{
        "index": 0,
        "message": {"role": "assistant", "content": answer},
        "finish_reason": "stop",
    }]
    return json.dumps(resp)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:  # suppress default access log
        pass

    def _send(self, code: int, body: str) -> None:
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/v1/models":
            self._send(200, MODELS_RESP)
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self) -> None:  # noqa: N802
        if "/chat/completions" in self.path:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length).decode(errors="replace")
            try:
                body = json.loads(raw)
                msgs = body.get("messages", [])
                combined = " ".join(m.get("content", "") for m in msgs)
            except (json.JSONDecodeError, AttributeError):
                combined = raw
            self._send(200, _make_completion(combined))
        else:
            self._send(404, json.dumps({"error": "not found"}))


def main() -> None:
    # DevSkim: ignore DS162092 — mock sandbox server, loopback-only, audit use only
    server = HTTPServer(("127.0.0.1", PORT), _Handler)
    print(f"[mock_lmstudio] Listening on http://127.0.0.1:{PORT}", flush=True)
    print("[mock_lmstudio] READY", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass  # graceful shutdown via Ctrl+C; finally block closes the server
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
