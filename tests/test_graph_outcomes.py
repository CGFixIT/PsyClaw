"""Data-driven graph / gate / BM25 outcome battery (issue #960).

Each line in ``tests/fixtures/graph_outcomes.jsonl`` is one case. Graph rows
reuse the mocked ``test_graph`` stack (no embeddings, no live LLM). Gate rows
call ``check_input`` only — injection is rejected before ``retrieve`` (I1 still
holds *inside* the graph). Retrieval rows run real ``keyword_search`` on an
in-memory BM25 corpus so exact-term cases can honestly claim the keyword leg
fired.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest
from rank_bm25 import BM25Okapi

from graph import build_graph
from retrieval.hybrid_search import HybridRetriever
from retrieval.stemmer import tokenize_and_stem
from tests.conftest import (
    MOCK_EMPTY_RESULTS,
    MOCK_HIGH_SCORE_RESULTS,
    MOCK_LOW_SCORE_RESULTS,
    MockClaudeClient,
    MockGrokClient,
    MockLocalLLM,
    MockRetriever,
    TEST_CONFIG,
)
from tests.test_graph import _make_cfg
from utils.errors import PromptInjectionError, RAGError
from utils.logger import reset_config_cache
from utils.sanitizer import check_input

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "graph_outcomes.jsonl"
_SHIPPED_CONFIG = _REPO_ROOT / "config.yaml"

_MOCK_RESULTS = {
    "high": MOCK_HIGH_SCORE_RESULTS,
    "low": MOCK_LOW_SCORE_RESULTS,
    "empty": MOCK_EMPTY_RESULTS,
}

_ROUTE_FROM_MODEL = {
    "local": "local_llm",
    "grok": "grok_fallback",
    "claude": "claude_fallback",
    "offline-best-effort": "offline_best_effort",
}

REQUIRED_TAGS = frozenset({
    "high-score",
    "low-score",
    "offline",
    "hybrid-consent",
    "injection",
    "out-of-corpus",
    "exact-term",
})

# Digit-only tokens (e.g. "401") are invisible to BM25 — the stemmer is
# letter-led. Exact-term error-code rows use IndexNotFoundError instead.
_BM25_CHUNKS: list[tuple[str, str]] = [
    (
        "security-cve.md",
        "Chroma PersistentClient CVE-2026-45829 risk accepted for local-only use.",
    ),
    (
        "error-codes.md",
        "HybridRetriever raises IndexNotFoundError when the BM25 index is missing or corrupt.",
    ),
    (
        "veeam-immutability.md",
        "Veeam uses chattr +i to make backups immutable.",
    ),
]


def _load_cases() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in _FIXTURE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        case = json.loads(line)
        if not isinstance(case, dict) or "id" not in case:
            raise ValueError(f"graph_outcomes.jsonl: expected object with id, got {case!r}")
        rows.append(case)
    return rows


CASES = _load_cases()


class _RaisingRetriever:
    """Stand-in whose hybrid_search raises the same RAGError retrieve_node catches."""

    def hybrid_search(self, query: str) -> list[Any]:
        raise RAGError("retriever exploded")


def _bm25_retriever() -> HybridRetriever:
    """Keyword-only HybridRetriever. Bypasses __init__ so no embeddings load."""
    chunks = [text for _, text in _BM25_CHUNKS]
    metadata = [
        {"source": src, "chunk_id": i, "stem_tags": "[]"}
        for i, (src, _) in enumerate(_BM25_CHUNKS)
    ]
    tokenized = [tokenize_and_stem(text) for text in chunks]
    retriever = object.__new__(HybridRetriever)
    retriever.bm25 = BM25Okapi(tokenized)
    retriever.bm25_chunks = chunks
    retriever.bm25_metadata = metadata
    retriever.top_k_keyword = 5
    retriever.rrf_k = 60
    retriever._bm25_scores = lru_cache(maxsize=256)(retriever.bm25.get_scores)
    return retriever


@pytest.fixture(autouse=True)
def _audit_to_tmp(tmp_path, monkeypatch):
    cfg = {
        **TEST_CONFIG,
        "logging": {
            **TEST_CONFIG["logging"],
            "audit_file": str(tmp_path / "audit.jsonl"),
            "log_file": str(tmp_path / "gateway.log"),
        },
    }
    reset_config_cache()
    monkeypatch.setattr("utils.logger._get_config", lambda config_path="config.yaml": cfg)
    yield
    reset_config_cache()


def _source_names(result: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for key in ("retrieved_docs", "answer_sources"):
        for doc in result.get(key) or []:
            if isinstance(doc, dict):
                source = doc.get("source")
                if isinstance(source, str) and source:
                    found.add(source)
    return found


def _hit_sources(hits: list[Any]) -> set[str]:
    found: set[str] = set()
    for hit in hits:
        source = getattr(hit, "source", None)
        if isinstance(source, str) and source:
            found.add(source)
    return found


def _assert_sources(sources: set[str], case: dict[str, Any]) -> None:
    for src in case.get("must_retrieve") or []:
        assert src in sources, f"missing source {src!r} in {sorted(sources)}"
    for src in case.get("must_not_retrieve") or []:
        assert src not in sources, f"unexpected source {src!r} in {sorted(sources)}"


def _assert_expected_route(case: dict[str, Any], result: dict[str, Any]) -> None:
    expected = case.get("expected_route")
    if expected == "user_gate":
        assert (result.get("answer_model") or "") == ""
        assert result.get("needs_user_confirm") is True
        return
    got = _ROUTE_FROM_MODEL.get(result.get("answer_model") or "", "")
    assert got == expected, f"route {got!r} != expected {expected!r}"


def _run_gate_row(case: dict[str, Any]) -> None:
    with pytest.raises(PromptInjectionError):
        check_input(str(case["query"]), str(_SHIPPED_CONFIG))
    assert case.get("expected_error_code") == 400
    assert case.get("expected_route") == "gate_block"


def _run_retrieval_row(case: dict[str, Any]) -> None:
    hits = _bm25_retriever().keyword_search(str(case["query"]))
    assert case.get("expected_route") == "keyword_hit"
    matching = [h for h in hits if h.source in set(case.get("must_retrieve") or [])]
    assert matching, f"no keyword hit for {case.get('must_retrieve')!r} in {_hit_sources(hits)}"
    for hit in matching:
        assert hit.retrieval_mode == "keyword"
        assert (hit.keyword_score or 0) > 0
    _assert_sources(_hit_sources(hits), case)


def _graph_retriever(mock_key: str) -> Any:
    if mock_key == "error":
        return _RaisingRetriever()
    if mock_key not in _MOCK_RESULTS:
        raise AssertionError(f"unknown mock_results {mock_key!r}")
    return MockRetriever(_MOCK_RESULTS[mock_key])


def _run_graph_row(case: dict[str, Any], tmp_path: Path) -> None:
    grok_on = bool(case.get("grok_available"))
    claude_on = bool(case.get("claude_available"))
    cfg = _make_cfg(
        tmp_path,
        mode=str(case["mode"]),
        grok_enabled=grok_on,
        claude_enabled=claude_on,
    )
    grok = None
    if grok_on:
        grok = MockGrokClient(
            response="Grok outcome-battery answer.",
            available=bool(case.get("grok_client_available", True)),
        )
    claude = None
    if claude_on:
        claude = MockClaudeClient(
            response="Claude outcome-battery answer.",
            available=bool(case.get("claude_client_available", True)),
        )
    graph = build_graph(
        retriever=_graph_retriever(str(case["mock_results"])),
        llm=MockLocalLLM(),
        grok=grok,
        claude=claude,
        cfg=cfg,
    )
    payload: dict[str, Any] = {"query": case["query"]}
    if "user_confirmed_online" in case:
        payload["user_confirmed_online"] = case["user_confirmed_online"]
    if case.get("online_provider"):
        payload["online_provider"] = case["online_provider"]
    result = graph.invoke(payload)

    got_model = result.get("answer_model") or ""
    assert got_model == (case.get("expected_answer_model") or "")
    _assert_expected_route(case, result)
    # route_by_score sets needs_user_confirm on a low top_score and later
    # nodes do not clear it. Only assert the pause (True). An answered
    # low-score path is identified by answer_model, not by this flag.
    if case.get("expected_needs_confirm") is True:
        assert result.get("needs_user_confirm") is True

    _assert_sources(_source_names(result), case)

    forbidden = set(case.get("must_not_call") or [])
    if "grok" in forbidden:
        assert grok is not None and grok.last_prompt is None
    if "claude" in forbidden:
        assert claude is not None and claude.last_prompt is None


@pytest.mark.parametrize("case", CASES, ids=lambda c: str(c["id"]))
def test_outcome_row(case: dict[str, Any], tmp_path: Path) -> None:
    layer = case["layer"]
    if layer == "gate":
        _run_gate_row(case)
        return
    if layer == "retrieval":
        _run_retrieval_row(case)
        return
    if layer != "graph":
        raise AssertionError(f"unknown layer {layer!r}")
    _run_graph_row(case, tmp_path)


def test_battery_covers_required_categories() -> None:
    seen: set[str] = set()
    for case in CASES:
        tags = case.get("tags") or []
        if not isinstance(tags, list):
            raise AssertionError(f"{case['id']}: tags must be a list")
        seen.update(str(tag) for tag in tags)
    missing = REQUIRED_TAGS - seen
    assert not missing, f"battery missing required tags: {sorted(missing)}"
    assert 20 <= len(CASES) <= 30, f"expected 20-30 cases, got {len(CASES)}"
