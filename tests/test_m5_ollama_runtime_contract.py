"""Static contract for the documented M5 Pro 48 GB Ollama/Qwen runtime.

Joins the four sources named by issue #1176: ``config.yaml``,
``macos/ollama-mlx.env``, ``llm/client.py``, and
``docs/m5-48gb-coding-expectations.md``.

No live Ollama, no Darwin, no tok/s claim. A retune that moves one source
without the others, or that pushes the RAG floor past the shipped 16k
window, must fail CI here rather than in a skill script that CI does not run.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import NamedTuple

import yaml

_REPO = Path(__file__).resolve().parent.parent
_HEADROOM_TOKENS = 1500
_MIN_GRAPH_MARGIN_SEC = 30
_ENV_CONTEXT_RE = re.compile(r"(?m)^OLLAMA_CONTEXT_LENGTH=(\d+)$")

# Documented M5 Pro 48 GB shipped tuple. Changing a pin is a product retune
# and must land with the matching doc + env + config in the same PR.
_SHIPPED_MODEL = "qwen3.8:27b-mlx"
_SHIPPED_REASONING_EFFORT = "none"
_SHIPPED_MAX_TOKENS = 4096
_SHIPPED_LLM_TIMEOUT_SEC = 720
_SHIPPED_GRAPH_TIMEOUT_SEC = 780
_SHIPPED_MAX_CONTEXT_TOKENS = 8000
_SHIPPED_OLLAMA_CONTEXT_LENGTH = 16384


class M5RuntimeContract(NamedTuple):
    local_llm: dict[str, object]
    max_tokens: int
    timeout_sec: int
    graph_timeout_sec: int
    max_context_tokens: int
    ollama_context_length: int
    m5_doc: str
    client_src: str


def _load_contract() -> M5RuntimeContract:
    cfg = yaml.safe_load((_REPO / "config.yaml").read_text(encoding="utf-8"))
    local_llm = cfg["models"]["local_llm"]
    env_text = (_REPO / "macos" / "ollama-mlx.env").read_text(encoding="utf-8")
    match = _ENV_CONTEXT_RE.search(env_text)
    assert match is not None, "macos/ollama-mlx.env must set OLLAMA_CONTEXT_LENGTH=<int>"
    return M5RuntimeContract(
        local_llm=local_llm,
        max_tokens=int(local_llm["max_tokens"]),
        timeout_sec=int(local_llm["timeout_sec"]),
        graph_timeout_sec=int(cfg["api"]["graph_timeout_sec"]),
        max_context_tokens=int(cfg["retrieval"]["max_context_tokens"]),
        ollama_context_length=int(match.group(1)),
        m5_doc=(_REPO / "docs" / "m5-48gb-coding-expectations.md").read_text(encoding="utf-8"),
        client_src=(_REPO / "llm" / "client.py").read_text(encoding="utf-8"),
    )


def test_shipped_config_matches_documented_m5_tuple() -> None:
    c = _load_contract()
    assert c.local_llm["model"] == _SHIPPED_MODEL
    assert c.local_llm["reasoning_effort"] == _SHIPPED_REASONING_EFFORT
    assert c.max_tokens == _SHIPPED_MAX_TOKENS
    assert c.timeout_sec == _SHIPPED_LLM_TIMEOUT_SEC
    assert c.graph_timeout_sec == _SHIPPED_GRAPH_TIMEOUT_SEC
    assert c.max_context_tokens == _SHIPPED_MAX_CONTEXT_TOKENS
    assert c.ollama_context_length == _SHIPPED_OLLAMA_CONTEXT_LENGTH


def test_m5_doctrine_cites_shipped_values_next_to_their_keys() -> None:
    """Keyword-adjacent so an unrelated 8000 (soul_max_chars) cannot hide a stale RAG sentence."""
    c = _load_contract()
    doc = c.m5_doc
    assert _SHIPPED_MODEL in doc
    assert re.search(r"`reasoning_effort:\s*none`", doc)
    assert re.search(rf"`max_tokens`\s*{c.max_tokens}\b", doc)
    assert re.search(rf"`max_context_tokens`\s*{c.max_context_tokens}\b", doc)
    assert re.search(rf"`num_ctx`\s*{c.ollama_context_length}\b", doc)
    assert re.search(rf"{c.timeout_sec}s\b.*local LLM timeout", doc)
    assert re.search(rf"{c.graph_timeout_sec}s\b.*graph timeout", doc)


def test_rag_floor_fits_inside_shipped_ollama_window() -> None:
    c = _load_contract()
    floor = c.max_context_tokens + c.max_tokens + _HEADROOM_TOKENS
    assert floor <= c.ollama_context_length, (
        f"RAG floor {floor} (max_context_tokens {c.max_context_tokens} + "
        f"max_tokens {c.max_tokens} + {_HEADROOM_TOKENS} headroom) exceeds "
        f"OLLAMA_CONTEXT_LENGTH {c.ollama_context_length}"
    )


def test_graph_timeout_outlives_local_llm_timeout() -> None:
    c = _load_contract()
    assert c.graph_timeout_sec - c.timeout_sec >= _MIN_GRAPH_MARGIN_SEC


def test_local_generate_does_not_retry_read_timeouts() -> None:
    """Static pin for llm/client.py. Behavioral coverage lives in test_client.py."""
    c = _load_contract()
    tree = ast.parse(c.client_src)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "LocalLLMClient":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "generate":
                    for call in ast.walk(item):
                        if not isinstance(call, ast.Call):
                            continue
                        func = call.func
                        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                        if name != "_post_with_retry":
                            continue
                        for kw in call.keywords:
                            if kw.arg == "retry_on_timeout":
                                assert isinstance(kw.value, ast.Constant)
                                assert kw.value.value is False
                                return
                    raise AssertionError(
                        "LocalLLMClient.generate calls _post_with_retry without retry_on_timeout=False"
                    )
    raise AssertionError("LocalLLMClient.generate not found")
