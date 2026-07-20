#!/usr/bin/env python3
"""
Script Name : mock_ollama.py
Summary     : Minimal mock Ollama server for CyClaw sandbox audits.
Requires    : Python >= 3.12, stdlib only
Usage       : python mock_ollama.py --host 127.0.0.1 --port 11434 --model qwen2.5:7b-instruct
Author      : CGFixIT Personal Agent
Version     : 1.0
Last-Updated: 2026-07-20

Description
-----------
This script provides a lightweight, deterministic mock Ollama API server.

It implements the core Ollama endpoints commonly used by local LLM clients:

    GET  /api/tags
    GET  /api/version
    POST /api/chat
    POST /api/generate

For compatibility with existing CyClaw/LM Studio/OpenAI-style clients, it also
implements:

    GET  /v1/models
    POST /v1/chat/completions

This mock does not load model weights, does not require GPU access, and does
not call the real Ollama daemon. It is intended for offline CI/sandbox audit use.

Examples
--------
Start the mock server:

    python mock_ollama.py

Start with a custom model name:

    python mock_ollama.py --model llama3.2:3b

Health check:

    curl http://127.0.0.1:11434/api/tags

Ollama chat test:

    curl http://127.0.0.1:11434/api/chat -d '{
      "model": "qwen2.5:7b-instruct",
      "messages": [{"role": "user", "content": "Describe CyClaw in one sentence."}],
      "stream": false
    }'

OpenAI-compatible fallback test:

    curl http://127.0.0.1:11434/v1/chat/completions -d '{
      "model": "qwen2.5:7b-instruct",
      "messages": [{"role": "user", "content": "Describe CyClaw in one sentence."}]
    }'
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 11434
DEFAULT_MODEL = "qwen2.5:7b-instruct"
LOG_PATH = Path("mock_ollama.log")
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(message)s"


logger = logging.getLogger("mock_ollama")
logger.setLevel(logging.INFO)

_file_handler = RotatingFileHandler(
    LOG_PATH,
    maxBytes=5_242_880,
    backupCount=3,
    encoding="utf-8",
)
_file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
logger.addHandler(_file_handler)


@dataclass(frozen=True)
class ServerConfig:
    """Runtime configuration for the mock Ollama server."""

    host: str
    port: int
    model: str
    verbose: bool = False


def utc_now_iso() -> str:
    """Return current UTC time in Ollama-like ISO-8601 format."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def make_deterministic_answer(prompt_content: str, model: str) -> str:
    """
    Return a deterministic mock response based on the prompt content.

    Parameters
    ----------
    prompt_content:
        Combined prompt text extracted from chat or generate requests.
    model:
        Mock model identifier.

    Returns
    -------
    str
        Deterministic response text.
    """
    normalized = prompt_content.lower()

    if "one sentence" in normalized or "describe" in normalized:
        return (
            "CyClaw is an offline-first, RAG-enforced personal AI assistant "
            "that uses local retrieval and controlled model access to answer "
            "questions from a private knowledge vault without sending data "
            "to the cloud."
        )

    if "health" in normalized or "ready" in normalized:
        return "Mock Ollama is ready and serving deterministic offline responses."

    return (
        f"[Mock Ollama — {model}] This is a cached offline response for sandbox "
        "audit purposes. No real model weights were loaded."
    )


def split_for_streaming(text: str) -> list[str]:
    """
    Split response text into simple word chunks for fake streaming.

    This intentionally avoids clever tokenization. It is a deterministic mock,
    not a real tokenizer.
    """
    words = text.split(" ")
    chunks: list[str] = []

    for index, word in enumerate(words):
        suffix = " " if index < len(words) - 1 else ""
        chunks.append(f"{word}{suffix}")

    return chunks or [""]


class MockOllamaHandler(BaseHTTPRequestHandler):
    """
    HTTP handler implementing a subset of Ollama and OpenAI-compatible APIs.

    The handler uses a class-level config assigned before server startup.
    """

    config = ServerConfig(
        host=DEFAULT_HOST,
        port=DEFAULT_PORT,
        model=DEFAULT_MODEL,
    )

    server_version = "MockOllama/1.0"
    sys_version = ""

    def log_message(self, fmt: str, *args: object) -> None:
        """Route HTTP access logs through standard logging."""
        if self.config.verbose:
            logger.info("%s - %s", self.client_address[0], fmt % args)

    def _send_json(self, code: int, body: dict[str, Any]) -> None:
        """Send a JSON response."""
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")

        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(data)

    def _send_ndjson_stream(self, code: int, objects: list[dict[str, Any]]) -> None:
        """Send an Ollama-style newline-delimited JSON streaming response."""
        self.send_response(code)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        for item in objects:
            line = json.dumps(item, ensure_ascii=False).encode("utf-8") + b"\n"
            self.wfile.write(line)
            self.wfile.flush()
            time.sleep(0.01)

    def _send_sse_stream(self, code: int, objects: list[dict[str, Any]]) -> None:
        """Send an OpenAI-style server-sent event stream."""
        self.send_response(code)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        for item in objects:
            payload = json.dumps(item, ensure_ascii=False)
            self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
            self.wfile.flush()
            time.sleep(0.01)

        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _read_json_body(self) -> tuple[dict[str, Any] | None, str | None]:
        """
        Read and parse a JSON request body.

        Returns
        -------
        tuple[dict[str, Any] | None, str | None]
            Parsed JSON body and error message. If parsing succeeds, error is None.
        """
        length = int(self.headers.get("Content-Length", 0))

        if length <= 0:
            return {}, None

        raw = self.rfile.read(length).decode("utf-8", errors="replace")

        try:
            body = json.loads(raw)
        except json.JSONDecodeError as exc:
            return None, f"invalid JSON body: {exc}"

        if not isinstance(body, dict):
            return None, "JSON request body must be an object"

        return body, None

    def do_OPTIONS(self) -> None:  # noqa: N802
        """Support browser/client CORS preflight."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        """Handle GET endpoints."""
        path = urlparse(self.path).path.rstrip("/") or "/"

        if path == "/api/tags":
            self._send_json(200, self._ollama_tags_response())
            return

        if path == "/api/version":
            self._send_json(200, {"version": "0.0.0-mock"})
            return

        if path == "/v1/models":
            self._send_json(200, self._openai_models_response())
            return

        if path in {"/", "/health", "/ready"}:
            self._send_json(
                200,
                {
                    "status": "ok",
                    "server": "mock_ollama",
                    "model": self.config.model,
                    "ollama_api": True,
                    "openai_compat": True,
                },
            )
            return

        self._send_json(404, {"error": f"not found: {path}"})

    def do_POST(self) -> None:  # noqa: N802
        """Handle POST endpoints."""
        path = urlparse(self.path).path.rstrip("/") or "/"

        body, error = self._read_json_body()
        if error is not None or body is None:
            self._send_json(400, {"error": error or "invalid request"})
            return

        if path == "/api/chat":
            self._handle_ollama_chat(body)
            return

        if path == "/api/generate":
            self._handle_ollama_generate(body)
            return

        if path == "/v1/chat/completions":
            self._handle_openai_chat_completions(body)
            return

        self._send_json(404, {"error": f"not found: {path}"})

    def _ollama_tags_response(self) -> dict[str, Any]:
        """Return an Ollama /api/tags response."""
        return {
            "models": [
                {
                    "name": self.config.model,
                    "model": self.config.model,
                    "modified_at": "2026-07-20T00:00:00Z",
                    "size": 4_200_000_000,
                    "digest": "sha256:mock-cyclaw-ollama-model",
                    "details": {
                        "parent_model": "",
                        "format": "gguf",
                        "family": "qwen2",
                        "families": ["qwen2"],
                        "parameter_size": "7B",
                        "quantization_level": "Q4_K_M",
                    },
                }
            ]
        }

    def _openai_models_response(self) -> dict[str, Any]:
        """Return an OpenAI-compatible /v1/models response."""
        return {
            "object": "list",
            "data": [
                {
                    "id": self.config.model,
                    "object": "model",
                    "created": 1_700_000_000,
                    "owned_by": "local",
                }
            ],
        }

    def _handle_ollama_chat(self, body: dict[str, Any]) -> None:
        """Handle POST /api/chat."""
        model = str(body.get("model") or self.config.model)
        messages = body.get("messages", [])
        stream = bool(body.get("stream", True))

        prompt_content = self._extract_messages_content(messages)
        answer = make_deterministic_answer(prompt_content, model)

        if stream:
            stream_objects = self._build_ollama_chat_stream(model, answer)
            self._send_ndjson_stream(200, stream_objects)
            return

        self._send_json(200, self._build_ollama_chat_response(model, answer))

    def _handle_ollama_generate(self, body: dict[str, Any]) -> None:
        """Handle POST /api/generate."""
        model = str(body.get("model") or self.config.model)
        prompt = str(body.get("prompt") or "")
        stream = bool(body.get("stream", True))

        answer = make_deterministic_answer(prompt, model)

        if stream:
            stream_objects = self._build_ollama_generate_stream(model, answer)
            self._send_ndjson_stream(200, stream_objects)
            return

        self._send_json(200, self._build_ollama_generate_response(model, answer))

    def _handle_openai_chat_completions(self, body: dict[str, Any]) -> None:
        """
        Handle POST /v1/chat/completions.

        This is included as a compatibility bridge for code that still expects
        LM Studio/OpenAI-compatible endpoints.
        """
        model = str(body.get("model") or self.config.model)
        messages = body.get("messages", [])
        stream = bool(body.get("stream", False))

        prompt_content = self._extract_messages_content(messages)
        answer = make_deterministic_answer(prompt_content, model)

        if stream:
            stream_objects = self._build_openai_chat_stream(model, answer)
            self._send_sse_stream(200, stream_objects)
            return

        self._send_json(200, self._build_openai_chat_response(model, answer))

    @staticmethod
    def _extract_messages_content(messages: Any) -> str:
        """Extract text content from a list of chat messages."""
        if not isinstance(messages, list):
            return str(messages)

        content_parts: list[str] = []

        for message in messages:
            if not isinstance(message, dict):
                content_parts.append(str(message))
                continue

            content = message.get("content", "")

            if isinstance(content, str):
                content_parts.append(content)
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        text = item.get("text") or item.get("content") or ""
                        content_parts.append(str(text))
                    else:
                        content_parts.append(str(item))
            else:
                content_parts.append(str(content))

        return " ".join(part for part in content_parts if part)

    @staticmethod
    def _usage_stats() -> dict[str, int]:
        """Return deterministic fake timing/token statistics."""
        return {
            "total_duration": 100_000_000,
            "load_duration": 1_000_000,
            "prompt_eval_count": 10,
            "prompt_eval_duration": 20_000_000,
            "eval_count": 20,
            "eval_duration": 79_000_000,
        }

    def _build_ollama_chat_response(self, model: str, answer: str) -> dict[str, Any]:
        """Build a non-streaming Ollama /api/chat response."""
        response = {
            "model": model,
            "created_at": utc_now_iso(),
            "message": {
                "role": "assistant",
                "content": answer,
            },
            "done_reason": "stop",
            "done": True,
        }
        response.update(self._usage_stats())
        return response

    def _build_ollama_generate_response(self, model: str, answer: str) -> dict[str, Any]:
        """Build a non-streaming Ollama /api/generate response."""
        response = {
            "model": model,
            "created_at": utc_now_iso(),
            "response": answer,
            "done_reason": "stop",
            "done": True,
            "context": [1, 2, 3],
        }
        response.update(self._usage_stats())
        return response

    def _build_ollama_chat_stream(self, model: str, answer: str) -> list[dict[str, Any]]:
        """Build a fake streaming Ollama /api/chat response."""
        chunks = [
            {
                "model": model,
                "created_at": utc_now_iso(),
                "message": {
                    "role": "assistant",
                    "content": chunk,
                },
                "done": False,
            }
            for chunk in split_for_streaming(answer)
        ]

        final = {
            "model": model,
            "created_at": utc_now_iso(),
            "message": {
                "role": "assistant",
                "content": "",
            },
            "done_reason": "stop",
            "done": True,
        }
        final.update(self._usage_stats())
        chunks.append(final)

        return chunks

    def _build_ollama_generate_stream(self, model: str, answer: str) -> list[dict[str, Any]]:
        """Build a fake streaming Ollama /api/generate response."""
        chunks = [
            {
                "model": model,
                "created_at": utc_now_iso(),
                "response": chunk,
                "done": False,
            }
            for chunk in split_for_streaming(answer)
        ]

        final = {
            "model": model,
            "created_at": utc_now_iso(),
            "response": "",
            "done_reason": "stop",
            "done": True,
            "context": [1, 2, 3],
        }
        final.update(self._usage_stats())
        chunks.append(final)

        return chunks

    @staticmethod
    def _build_openai_chat_response(model: str, answer: str) -> dict[str, Any]:
        """Build a non-streaming OpenAI-compatible chat completion response."""
        return {
            "id": "chatcmpl-mock-ollama-001",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": answer,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
            },
        }

    @staticmethod
    def _build_openai_chat_stream(model: str, answer: str) -> list[dict[str, Any]]:
        """Build fake streaming OpenAI-compatible chat completion chunks."""
        created = int(time.time())
        chunks: list[dict[str, Any]] = []

        for chunk in split_for_streaming(answer):
            chunks.append(
                {
                    "id": "chatcmpl-mock-ollama-001",
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "content": chunk,
                            },
                            "finish_reason": None,
                        }
                    ],
                }
            )

        chunks.append(
            {
                "id": "chatcmpl-mock-ollama-001",
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop",
                    }
                ],
            }
        )

        return chunks


def configure_logging(verbose: bool) -> None:
    """Configure console logging if verbose mode is enabled."""
    if not verbose:
        return

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(console_handler)
    logger.setLevel(logging.DEBUG)


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Run a minimal mock Ollama server for CyClaw sandbox audits.",
    )

    parser.add_argument(
        "--host",
        default=os.getenv("MOCK_OLLAMA_HOST", DEFAULT_HOST),
        help=f"Bind address. Default: {DEFAULT_HOST}",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("MOCK_OLLAMA_PORT", str(DEFAULT_PORT))),
        help=f"Bind port. Default: {DEFAULT_PORT}",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("MOCK_OLLAMA_MODEL", DEFAULT_MODEL),
        help=f"Mock model name. Default: {DEFAULT_MODEL}",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose request logging.",
    )

    return parser.parse_args()


def run_server(config: ServerConfig) -> None:
    """Start the mock Ollama HTTP server."""
    configure_logging(config.verbose)

    MockOllamaHandler.config = config

    # DevSkim: ignore DS162092
    # This is a local-only mock server intended for sandbox/CI audit use.
    server = ThreadingHTTPServer((config.host, config.port), MockOllamaHandler)

    base_url = f"http://{config.host}:{config.port}"

    print(f"[mock_ollama] Listening on {base_url}", flush=True)
    print(f"[mock_ollama] Model: {config.model}", flush=True)
    print("[mock_ollama] READY", file=sys.stderr, flush=True)

    logger.info("Mock Ollama listening on %s", base_url)
    logger.info("Mock model: %s", config.model)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[mock_ollama] Stopping...", flush=True)
    finally:
        server.server_close()
        logger.info("Mock Ollama stopped")


def main() -> None:
    """CLI entrypoint."""
    args = parse_args()

    config = ServerConfig(
        host=args.host,
        port=args.port,
        model=args.model,
        verbose=args.verbose,
    )

    run_server(config)


if __name__ == "__main__":
    main()
