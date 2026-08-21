#!/usr/bin/env python3
"""Measure Ollama prefill/decode tok/s on the operator's machine.

Operator tool. Not imported by gate.py / graph.py / mcp (I6). stdlib only so it
runs before the CyClaw venv exists.

Ollama's native ``POST /api/generate`` (not the OpenAI-compat wrapper) is the
source of truth: ``eval_count`` / ``eval_duration`` are nanoseconds the runner
actually spent, not a client-side wall-clock guess.

    python3 scripts/measure_local_llm_throughput.py
    python3 scripts/measure_local_llm_throughput.py --model qwen3.8:27b-nvfp4
    python3 scripts/measure_local_llm_throughput.py --json

Exit: 0 measured, 2 Ollama unreachable, 3 generate failed, 1 bad args.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_HOST = "http://127.0.0.1:11434"  # DevSkim: ignore DS162092,DS137138
DEFAULT_MODEL = "qwen3.8:27b-mlx"
DEFAULT_NUM_PREDICT = 256
# ~1.6k chars ≈ 400 tokens of filler. Enough to exercise prefill without
# sitting in a 16k-context 27B prompt for minutes on first run.
_RAG_UNIT = (
    "CyClaw retrieves fused RAG chunks, injects a capped soul preamble, "
    "and asks the local model for a grounded answer. "
)


def tok_per_sec(count: Any, duration_ns: Any) -> float | None:
    """Ollama reports durations in nanoseconds. Zero/missing -> None."""
    try:
        tokens = int(count)
        nanos = int(duration_ns)
    except (TypeError, ValueError):
        return None
    if tokens <= 0 or nanos <= 0:
        return None
    return tokens / (nanos / 1_000_000_000)


def rates_from_generate(payload: dict) -> dict[str, Any]:
    """Collapse one /api/generate body into prefill/decode rates."""
    prefill = tok_per_sec(
        payload.get("prompt_eval_count"), payload.get("prompt_eval_duration")
    )
    decode = tok_per_sec(payload.get("eval_count"), payload.get("eval_duration"))
    load_ns = payload.get("load_duration")
    try:
        load_ms = int(load_ns) / 1_000_000 if load_ns is not None else None
    except (TypeError, ValueError):
        load_ms = None
    return {
        "model": payload.get("model"),
        "prompt_eval_count": payload.get("prompt_eval_count"),
        "eval_count": payload.get("eval_count"),
        "prefill_tok_s": None if prefill is None else round(prefill, 2),
        "decode_tok_s": None if decode is None else round(decode, 2),
        "load_ms": None if load_ms is None else round(load_ms, 1),
        "total_duration_ns": payload.get("total_duration"),
    }


def _http_url(url: str) -> str:
    """Refuse file: / custom schemes so urlopen is loopback HTTP only."""
    scheme = urllib.parse.urlparse(url).scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError(f"refusing non-http URL scheme {scheme!r}")
    return url


def _post_generate(host: str, body: dict, timeout_sec: int) -> dict:
    url = _http_url(host.rstrip("/") + "/api/generate")
    raw = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310
        url,
        data=raw,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _tags_reachable(host: str, timeout_sec: int = 3) -> bool:
    url = _http_url(host.rstrip("/") + "/api/tags")
    req = urllib.request.Request(url, method="GET")  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:  # noqa: S310
            return 200 <= getattr(resp, "status", 200) < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _rag_prompt(target_chars: int = 1600) -> str:
    text = _RAG_UNIT
    while len(text) < target_chars:
        text += _RAG_UNIT
    return text[:target_chars] + "\nSummarize the preceding notes in two sentences."


def run_suite(
    *,
    host: str,
    model: str,
    num_predict: int,
    timeout_sec: int,
    warmup: bool,
) -> list[dict[str, Any]]:
    prompts = (
        ("short_decode", "Reply with exactly twenty words about local RAG agents."),
        ("rag_prefill", _rag_prompt()),
    )
    results: list[dict[str, Any]] = []
    if warmup:
        try:
            _post_generate(
                host,
                {
                    "model": model,
                    "prompt": "ping",
                    "stream": False,
                    "options": {"num_predict": 8, "temperature": 0},
                    "keep_alive": "30m",
                },
                timeout_sec,
            )
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            # Warmup is best-effort; the measured calls report the real failure.
            pass
    for name, prompt in prompts:
        body = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": num_predict, "temperature": 0},
            "keep_alive": "30m",
        }
        payload = _post_generate(host, body, timeout_sec)
        row = rates_from_generate(payload)
        row["case"] = name
        results.append(row)
    return results


def _print_table(rows: list[dict[str, Any]]) -> None:
    print(
        f"{'case':<14} {'prefill tok/s':>14} {'decode tok/s':>14} "
        f"{'prompt tok':>11} {'out tok':>8} {'load ms':>10}"
    )
    for row in rows:
        print(
            f"{row.get('case', ''):<14} "
            f"{str(row.get('prefill_tok_s')):>14} "
            f"{str(row.get('decode_tok_s')):>14} "
            f"{str(row.get('prompt_eval_count')):>11} "
            f"{str(row.get('eval_count')):>8} "
            f"{str(row.get('load_ms')):>10}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure Ollama prefill/decode tok/s (native /api/generate)."
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--num-predict", type=int, default=DEFAULT_NUM_PREDICT)
    parser.add_argument("--timeout-sec", type=int, default=180)
    parser.add_argument("--no-warmup", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.num_predict <= 0 or args.timeout_sec <= 0:
        print("num-predict and timeout-sec must be positive", file=sys.stderr)
        return 1
    try:
        _http_url(args.host)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not _tags_reachable(args.host):
        print(
            f"Ollama not reachable at {args.host}/api/tags. "
            "Start it with macos/ollama-mlx.env sourced (see OLLAMA_SETUP.md).",
            file=sys.stderr,
        )
        return 2
    try:
        rows = run_suite(
            host=args.host,
            model=args.model,
            num_predict=args.num_predict,
            timeout_sec=args.timeout_sec,
            warmup=not args.no_warmup,
        )
    except urllib.error.HTTPError as exc:
        print(f"generate failed HTTP {exc.code}: {exc.reason}", file=sys.stderr)
        return 3
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        print(f"generate failed: {exc}", file=sys.stderr)
        return 3

    if args.json:
        print(json.dumps({"model": args.model, "host": args.host, "runs": rows}, indent=2))
    else:
        print(f"model={args.model}  host={args.host}")
        _print_table(rows)
        print(
            "These are this-machine numbers. Paste decode tok/s back into "
            "config.yaml comments only after you have run this; do not invent."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
