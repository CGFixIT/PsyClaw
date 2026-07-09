"""Shared pytest fixtures for CyClaw test suite.

Mocks: LLM services, embedding model, retriever, test config.
No live services required — all external deps are mocked.
"""

import copy
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from retrieval.hybrid_search import SearchResult


# DevSkim: ignore DS162092,DS137138 - test fixtures; loopback addresses are intentional
TEST_CONFIG = {
    "app": {"name": "cyclaw-test", "env": "test", "mode": "offline", "debug": True},
    "models": {
        "local_llm": {"provider": "lmstudio", "base_url": "http://127.0.0.1:1234/v1",
                      "model": "test-model", "max_tokens": 256, "temperature": 0.1, "timeout_sec": 10},
        "embeddings": {"provider": "sentence-transformers", "model": "all-MiniLM-L6-v2",
                       "dim": 384, "cache_dir": None},
        "grok": {"enabled": False, "base_url": "https://api.x.ai/v1", "model": "grok-4.3",
                 "timeout_sec": 10, "max_tokens": 256, "temperature": 0.2},
        "claude": {"enabled": False, "base_url": "https://api.anthropic.com/v1",
                   "model": "claude-sonnet-5", "anthropic_version": "2023-06-01",
                   "timeout_sec": 10, "max_tokens": 256}
    },
    "corpus": {"path": "data/corpus", "extensions": [".md", ".txt"]},
    "indexing": {"chroma_path": "", "bm25_path": "", "collection_name": "test_kb",
                 "chunk_size": 512, "chunk_overlap": 50, "batch_size": 10},
    "retrieval": {"top_k_semantic": 3, "top_k_keyword": 3, "rrf_k": 60,
                   "max_context_tokens": 1000, "min_score": 0.75,
                   "hybrid": {"enabled": True}},
    "policy": {
        "fallback": {"enabled": True, "require_user_confirm": True,
                     "send_local_context_to_grok": False,
                     "send_local_context_to_claude": False},
        "prompt_filter": {"enabled": True,
                          "banned_patterns": ["ignore previous instructions", "system prompt:"],
                          "max_input_chars": 4000},
        "privacy": {"redact_emails": True, "redact_ips": True,
                    "redact_secrets_like": ["AKIA[0-9A-Z]{16}"]}
    },
    "api": {"host": "127.0.0.1", "port": 8787},  # DevSkim: ignore DS162092
    "logging": {"level": "DEBUG", "log_file": "", "audit_file": os.path.join(tempfile.mkdtemp(), "audit.jsonl"),
                "audit_fields": {"include_query_hash": True, "include_top_score": True,
                                 "include_retrieval_mode": True, "include_online_escalated": True,
                                 "include_model_used": True}},
    "security": {"require_env": ["GROK_API_KEY"],
                 "allowed_origins": ["http://127.0.0.1", "http://localhost"]},  # DevSkim: ignore DS162092,DS137138
    "personality": {"enabled": False, "soul_path": "", "db_path": "", "interaction_ttl_days": 90}
}


@pytest.fixture
def test_config(tmp_path):
    # Deep-copy so each test gets a fully independent config tree. The old
    # ``TEST_CONFIG.copy()`` was a SHALLOW copy that only hand-cloned the
    # ``indexing`` and ``logging`` sub-dicts; every other nested dict (models,
    # policy, retrieval, security, ...) was a shared reference to the module-level
    # TEST_CONFIG. A test mutating e.g. ``cfg["models"]["grok"]["enabled"]`` or
    # appending to ``cfg["policy"]["prompt_filter"]["banned_patterns"]`` would
    # poison the global and leak into every later test (order-dependent flakes,
    # amplified under pytest-xdist). deepcopy removes that whole class of bug.
    cfg = copy.deepcopy(TEST_CONFIG)
    cfg["indexing"]["chroma_path"] = str(tmp_path / "chroma_db")
    cfg["indexing"]["bm25_path"] = str(tmp_path / "bm25.json")
    cfg["logging"]["log_file"] = str(tmp_path / "cyclaw.log")
    cfg["logging"]["audit_file"] = str(tmp_path / "audit.jsonl")
    config_file = tmp_path / "config.yaml"
    with open(config_file, "w") as f:
        yaml.dump(cfg, f)
    return cfg, str(config_file)


@pytest.fixture
def mock_search_results():
    return [
        SearchResult(text="RAG is retrieval augmented generation", score=0.92,
                     source="rag_basics.md", chunk_id=0, stem_tags=["rag", "retriev"],
                     retrieval_mode="hybrid", rrf_score=0.92),
        SearchResult(text="ChromaDB stores vector embeddings", score=0.85,
                     source="chromadb_guide.md", chunk_id=1, stem_tags=["chroma", "embed"],
                     retrieval_mode="hybrid", rrf_score=0.85),
    ]


@pytest.fixture
def mock_retriever(mock_search_results):
    retriever = MagicMock()
    retriever.hybrid_search.return_value = mock_search_results
    retriever.semantic_search.return_value = mock_search_results
    retriever.keyword_search.return_value = mock_search_results
    return retriever


@pytest.fixture
def mock_llm():
    llm = MagicMock()
    llm.generate.return_value = "This is a test answer from the local LLM."
    return llm


# =============================================================================
# Class-style mocks + result constants used by test_graph.py / test_gate.py.
#
# These mirror the dependency-injection contract of build_graph(retriever, llm,
# grok, cfg, personality): each mock exposes the same call surface the graph
# nodes touch (retriever.hybrid_search / llm.generate / grok.generate) and the
# LLM/Grok mocks record their last prompt so tests can assert on prompt content.
# =============================================================================

MOCK_HIGH_SCORE_RESULTS = [
    SearchResult(text="Veeam uses chattr +i to make backups immutable.", score=0.92,
                 source="veeam-immutability.md", chunk_id=0, stem_tags=["veeam", "immut"],
                 retrieval_mode="hybrid", rrf_score=0.92, semantic_score=0.92, semantic_rank=0),
    SearchResult(text="Immutable backups cannot be modified or deleted.", score=0.81,
                 source="veeam-immutability.md", chunk_id=1, stem_tags=["immut", "backup"],
                 retrieval_mode="hybrid", rrf_score=0.81, semantic_score=0.81, semantic_rank=1),
]

MOCK_LOW_SCORE_RESULTS = [
    SearchResult(text="A weakly related passage about unrelated topics.", score=0.30,
                 source="misc.md", chunk_id=0, stem_tags=["misc"],
                 retrieval_mode="hybrid", rrf_score=0.30, semantic_score=0.30, semantic_rank=0),
]

MOCK_EMPTY_RESULTS: list[SearchResult] = []


class MockRetriever:
    """Stand-in for HybridRetriever that returns a fixed result list."""
    def __init__(self, results):
        self.results = results

    def hybrid_search(self, query):
        return self.results

    def semantic_search(self, query, k=None):
        return self.results

    def keyword_search(self, query, k=None):
        return self.results


class MockLocalLLM:
    """Stand-in for LocalLLMClient; records the last prompt it was given."""
    def __init__(self, response="This is a test answer from the local LLM."):
        self.response = response
        self.last_prompt = None

    def generate(self, prompt):
        self.last_prompt = prompt
        return self.response


class MockGrokClient:
    """Stand-in for GrokClient; records the last prompt it was given.

    ``available`` mirrors the real ``GrokClient.is_available()`` (True when a
    ``GROK_API_KEY`` is present). It defaults to True so existing routing tests
    still reach grok_fallback; set ``available=False`` to simulate Grok enabled
    in config but with no API key.
    """
    def __init__(self, response="This is a test answer from Grok.", available=True):
        self.response = response
        self.last_prompt = None
        self._available = available

    def is_available(self):
        return self._available

    def generate(self, prompt):
        self.last_prompt = prompt
        return self.response


class MockClaudeClient(MockGrokClient):
    """Stand-in for ClaudeClient; same generate/is_available contract."""


@pytest.fixture
def bm25_index(tmp_path):
    chunks = ["RAG retrieval augmented generation", "ChromaDB vector database",
              "BM25 keyword search algorithm"]
    metadata = [{"source": f"doc{i}.md", "chunk_id": i, "stem_tags": "[]"}  for i in range(len(chunks))]
    tokenized = [c.lower().split() for c in chunks]
    bm25_path = tmp_path / "bm25.json"
    with open(bm25_path, "w", encoding="utf-8") as f:
        json.dump({"tokenized_corpus": tokenized, "chunks": chunks, "metadata": metadata}, f)
    return str(bm25_path), chunks, metadata
