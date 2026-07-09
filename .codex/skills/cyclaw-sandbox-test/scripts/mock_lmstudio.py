#!/usr/bin/env python3
"""Minimal mock LM Studio server: OpenAI-compatible API on port 1234."""

from __future__ import annotations

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 1234
MODEL_ID = "qwen2.5-7b-instruct"
GROK_MODEL_ID = "grok-4.3"
CLAUDE_MODEL_ID = "claude-sonnet-5"

MODELS_RESP = json.dumps(
    {
        "object": "list",
        "data": [
            {"id": MODEL_ID, "object": "model", "created": 1700000000, "owned_by": "local"},
            {"id": GROK_MODEL_ID, "object": "model", "created": 1700000000, "owned_by": "mock-xai"},
            {"id": CLAUDE_MODEL_ID, "object": "model", "created": 1700000000, "owned_by": "mock-anthropic"},
        ],
    }
)

COMPLETION_TMPL = {
    "id": "chatcmpl-mock-001",
    "object": "chat.completion",
    "created": 0,
    "model": MODEL_ID,
    "choices": [{"index": 0, "message": {"role": "assistant", "content": ""}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
}


def _answer(prompt_content: str, model_id: str) -> str:
    if "one sentence" in prompt_content.lower() or "describe" in prompt_content.lower():
        return (
            "CyClaw is an offline-first, RAG-enforced personal AI assistant that uses "
            "a LangGraph security topology and ChromaDB+BM25 hybrid retrieval to answer "
            "questions from a local knowledge vault without sending data to the cloud."
        )
    if model_id == GROK_MODEL_ID:
        return "[Mock Grok API] Dummy-key external fallback response for sandbox audit purposes."
    if model_id == CLAUDE_MODEL_ID:
        return "[Mock Claude API] Dummy-key external fallback response for sandbox audit purposes."
    return (
        f"[Mock LM Studio - {MODEL_ID}] This is a cached offline response "
        "for sandbox audit purposes. No real model weights were loaded."
    )


def _make_completion(prompt_content: str, model_id: str = MODEL_ID) -> str:
    answer = _answer(prompt_content, model_id)
    resp = dict(COMPLETION_TMPL)
    resp["created"] = int(time.time())
    resp["model"] = model_id
    resp["choices"] = [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}]
    return json.dumps(resp)


def _make_claude_completion(prompt_content: str, model_id: str = CLAUDE_MODEL_ID) -> str:
    return json.dumps(
        {
            "id": "msg-mock-001",
            "type": "message",
            "role": "assistant",
            "model": model_id,
            "content": [{"type": "text", "text": _answer(prompt_content, model_id)}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 20},
        }
    )


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
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
        if "/chat/completions" not in self.path and "/messages" not in self.path:
            self._send(404, json.dumps({"error": "not found"}))
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode(errors="replace")
        try:
            body = json.loads(raw)
            msgs = body.get("messages", [])
            combined = " ".join(m.get("content", "") for m in msgs)
            model_id = body.get("model") or MODEL_ID
        except (json.JSONDecodeError, AttributeError):
            combined = raw
            model_id = MODEL_ID
        if "/messages" in self.path:
            self._send(200, _make_claude_completion(combined, str(model_id)))
        else:
            self._send(200, _make_completion(combined, str(model_id)))


def main() -> None:
    server = HTTPServer(("127.0.0.1", PORT), _Handler)
    print(f"[mock_lmstudio] Listening on http://127.0.0.1:{PORT}", flush=True)
    print("[mock_lmstudio] READY", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
