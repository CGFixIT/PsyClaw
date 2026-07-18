"""Unit tests for CyClaw's JSON-RPC MCP server (mcp_hybrid_server.py).

These tests guard the MCP protocol-layer invariants that CyClaw v1.3.0
depends on:

  * sampling = None at the capability level (the MCP server CANNOT route to
    an LLM — this is a structural guarantee, not policy)
  * tools/list exposes only the retrieval-only hybrid_search tool
  * tools/call dispatches correctly across hybrid / semantic / keyword modes
  * Unknown methods return JSON-RPC error code -32601
  * RAGError from the retriever maps to JSON-RPC error code -32000
  * notifications/initialized returns None (no response for notifications)

All tests are hermetic: the HybridRetriever is replaced with
unittest.mock.MagicMock — no live ChromaDB, BM25 pickle, or network.
Run with:

    pytest tests/test_mcp_server.py -v
"""

import json
from unittest.mock import MagicMock

import pytest
import yaml

import mcp_hybrid_server
from mcp_hybrid_server import CAPABILITIES, TOOLS, handle_message
from retrieval.hybrid_search import SearchResult
from utils.errors import RAGError
from utils.logger import audit_log as _real_audit_log, hash_query, reset_config_cache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_search_result(
    text: str = "veeam immutability ensures backups cannot be modified",
    score: float = 0.92,
    source: str = "veeam-immutability.md",
    chunk_id: int = 0,
    stem_tags=None,
    retrieval_mode: str = "hybrid",
) -> SearchResult:
    """Build a fully-populated SearchResult for MCP dispatch tests."""
    return SearchResult(
        text=text,
        score=score,
        source=source,
        chunk_id=chunk_id,
        stem_tags=stem_tags if stem_tags is not None else ["veeam", "immut", "backup"],
        retrieval_mode=retrieval_mode,
        semantic_score=score,
        semantic_rank=chunk_id,
        keyword_score=score * 0.9,
        keyword_rank=chunk_id,
        rrf_score=score,
        rrf_semantic_contrib=score * 0.6,
        rrf_keyword_contrib=score * 0.4,
    )


@pytest.fixture
def retriever():
    """A MagicMock retriever — no live index, all three search methods stubbed."""
    r = MagicMock()
    results = [
        _make_search_result(text="first chunk", score=0.92, chunk_id=0),
        _make_search_result(text="second chunk", score=0.81, chunk_id=1),
    ]
    r.hybrid_search.return_value = results
    r.semantic_search.return_value = results
    r.keyword_search.return_value = results
    return r


# ---------------------------------------------------------------------------
# 1. initialize
# ---------------------------------------------------------------------------

def test_initialize_returns_capabilities(retriever):
    """initialize must return protocolVersion + capabilities with sampling=None."""
    msg = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    result = handle_message(msg, retriever)

    assert result is not None
    assert "result" in result
    # CRITICAL invariant — MCP server cannot route to an LLM
    assert result["result"]["capabilities"]["sampling"] is None
    assert result["result"]["protocolVersion"] == "2025-11-25"
    assert result["result"]["serverInfo"]["name"] == "cyclaw-hybrid-rag"


# ---------------------------------------------------------------------------
# 2. Module-level sampling-is-None invariant
# ---------------------------------------------------------------------------

def test_sampling_is_none_invariant():
    """Protocol-level guarantee: CAPABILITIES['sampling'] is None at import.

    This is the strongest possible regression guard for the no-LLM-from-MCP
    architecture commitment. If anyone changes this to a dict, the test fails
    immediately at collection time across the whole suite.
    """
    assert "sampling" in CAPABILITIES
    assert CAPABILITIES["sampling"] is None


# ---------------------------------------------------------------------------
# 3. tools/list
# ---------------------------------------------------------------------------

def test_tools_list_returns_hybrid_search(retriever):
    """tools/list must surface only hybrid_search with `query` as required."""
    msg = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    result = handle_message(msg, retriever)

    assert result is not None
    tools = result["result"]["tools"]
    tool_names = [t["name"] for t in tools]
    assert "hybrid_search" in tool_names

    hybrid_tool = next(t for t in tools if t["name"] == "hybrid_search")
    assert "query" in hybrid_tool["inputSchema"]["required"]
    # TOOLS module constant should match what the handler returns
    assert tools == TOOLS


# ---------------------------------------------------------------------------
# 4. tools/call — hybrid mode (default)
# ---------------------------------------------------------------------------

def test_tools_call_hybrid_search(retriever):
    """tools/call with mode=hybrid returns 2 chunks with the documented keys."""
    msg = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "hybrid_search", "arguments": {"query": "test"}},
    }
    result = handle_message(msg, retriever)

    assert result is not None
    chunks = result["result"]["chunks"]
    assert len(chunks) == 2
    expected_keys = {"text", "score", "source", "chunk_id", "source_sha256", "stem_tags", "mode"}
    for chunk in chunks:
        assert expected_keys.issubset(set(chunk.keys()))


# ---------------------------------------------------------------------------
# 5. tools/call — semantic mode
# ---------------------------------------------------------------------------

def test_tools_call_semantic_mode(retriever):
    """mode=semantic must dispatch to retriever.semantic_search (not hybrid/keyword)."""
    msg = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "hybrid_search",
            "arguments": {"query": "test", "mode": "semantic"},
        },
    }
    result = handle_message(msg, retriever)

    assert result is not None
    assert "result" in result
    retriever.semantic_search.assert_called_once()
    assert retriever.hybrid_search.call_count == 0
    assert retriever.keyword_search.call_count == 0


# ---------------------------------------------------------------------------
# 6. tools/call — keyword mode
# ---------------------------------------------------------------------------

def test_tools_call_keyword_mode(retriever):
    """mode=keyword must dispatch to retriever.keyword_search (not hybrid/semantic)."""
    msg = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {
            "name": "hybrid_search",
            "arguments": {"query": "test", "mode": "keyword"},
        },
    }
    result = handle_message(msg, retriever)

    assert result is not None
    assert "result" in result
    retriever.keyword_search.assert_called_once()
    assert retriever.hybrid_search.call_count == 0
    assert retriever.semantic_search.call_count == 0


# ---------------------------------------------------------------------------
# 6b. score_scale metadata — the three modes emit incomparable score scales
# ---------------------------------------------------------------------------

def _call(retriever, arguments: dict) -> dict:
    msg = {
        "jsonrpc": "2.0",
        "id": 99,
        "method": "tools/call",
        "params": {"name": "hybrid_search", "arguments": arguments},
    }
    return handle_message(msg, retriever)


def test_score_scale_keyword_is_bm25_raw(retriever):
    result = _call(retriever, {"query": "test", "mode": "keyword"})
    assert result["result"]["metadata"]["score_scale"] == "bm25_raw"


def test_score_scale_semantic_is_cosine(retriever):
    result = _call(retriever, {"query": "test", "mode": "semantic"})
    assert result["result"]["metadata"]["score_scale"] == "cosine_similarity"


def test_score_scale_hybrid_fused_is_rrf(retriever):
    # Fixture results carry retrieval_mode="hybrid" (fused path) → RRF scale.
    result = _call(retriever, {"query": "test"})
    assert result["result"]["metadata"]["score_scale"] == "rrf"


def test_score_scale_hybrid_semantic_only_fallback_is_cosine(retriever):
    # hybrid_search's semantic-only degraded fallback returns cosine scores
    # UNCHANGED (documented in retrieval/hybrid_search.py) — the scale must
    # say so rather than claiming rrf.
    retriever.hybrid_search.return_value = [
        _make_search_result(text="only leg", score=0.88, chunk_id=0, retrieval_mode="semantic"),
    ]
    result = _call(retriever, {"query": "test"})
    assert result["result"]["metadata"]["score_scale"] == "cosine_similarity"


def test_score_scale_hybrid_bm25_fallback_is_rrf(retriever):
    # The BM25-only fallback is rebased onto the RRF scale by
    # _normalize_single_path, so keyword-mode chunks under mode=hybrid → rrf.
    retriever.hybrid_search.return_value = [
        _make_search_result(text="kw leg", score=0.016, chunk_id=0, retrieval_mode="keyword"),
    ]
    result = _call(retriever, {"query": "test"})
    assert result["result"]["metadata"]["score_scale"] == "rrf"


# ---------------------------------------------------------------------------
# 7. Unknown method → JSON-RPC -32601
# ---------------------------------------------------------------------------

def test_unknown_method_returns_error_32601(retriever):
    """Unknown methods must return JSON-RPC error code -32601 (Method not found)."""
    msg = {"jsonrpc": "2.0", "method": "nonexistent/method", "id": 6}
    result = handle_message(msg, retriever)

    assert result is not None
    assert "error" in result
    assert result["error"]["code"] == -32601


def test_non_object_message_returns_invalid_request(retriever):
    result = handle_message(["not", "a", "dict"], retriever)
    assert result["error"]["code"] == -32600


def test_tools_call_rejects_non_object_params(retriever):
    msg = {"jsonrpc": "2.0", "id": 61, "method": "tools/call", "params": "oops"}
    result = handle_message(msg, retriever)
    assert result["error"]["code"] == -32602


def test_tools_call_rejects_non_object_arguments(retriever):
    msg = {
        "jsonrpc": "2.0",
        "id": 62,
        "method": "tools/call",
        "params": {"name": "hybrid_search", "arguments": "bad"},
    }
    result = handle_message(msg, retriever)
    assert result["error"]["code"] == -32602


def test_tools_call_rejects_missing_query(retriever):
    msg = {
        "jsonrpc": "2.0",
        "id": 63,
        "method": "tools/call",
        "params": {"name": "hybrid_search", "arguments": {}},
    }
    result = handle_message(msg, retriever)
    assert result["error"]["code"] == -32602


def test_tools_call_rejects_oversized_query(retriever):
    msg = {
        "jsonrpc": "2.0",
        "id": 64,
        "method": "tools/call",
        "params": {
            "name": "hybrid_search",
            "arguments": {"query": "x" * (mcp_hybrid_server._MAX_QUERY_CHARS + 1)},
        },
    }
    result = handle_message(msg, retriever)
    assert result["error"]["code"] == -32602
    retriever.hybrid_search.assert_not_called()


# ---------------------------------------------------------------------------
# 8. notifications/initialized → None
# ---------------------------------------------------------------------------

def test_notifications_initialized_returns_none(retriever):
    """JSON-RPC notifications must NOT produce a response per the spec."""
    msg = {"jsonrpc": "2.0", "method": "notifications/initialized", "id": None}
    result = handle_message(msg, retriever)

    assert result is None


# ---------------------------------------------------------------------------
# 9. RAGError from retriever → JSON-RPC -32000
# ---------------------------------------------------------------------------

def test_rag_error_returns_error_32000(retriever):
    """If the retriever raises RAGError, the MCP server must return code -32000."""
    retriever.hybrid_search.side_effect = RAGError("no index", code="IDX")
    msg = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {
            "name": "hybrid_search",
            "arguments": {"query": "test", "mode": "hybrid"},
        },
    }
    result = handle_message(msg, retriever)

    assert result is not None
    assert "error" in result
    assert result["error"]["code"] == -32000


# ---------------------------------------------------------------------------
# 10. Audit privacy parity (T1.3): persisted MCP audit event hashes the query
# ---------------------------------------------------------------------------

def test_mcp_audit_event_hashes_full_query(retriever, tmp_path, monkeypatch):
    """The persisted MCP audit event must store a hashed query, not raw text,
    with parity to the HTTP path (full-query SHA-256, no [:100] truncation).
    """
    audit_file = tmp_path / "audit.jsonl"
    cfg = {
        "logging": {
            "audit_file": str(audit_file),
            "audit_fields": {"include_query_hash": True},
        },
        "policy": {"privacy": {"redact_emails": True, "redact_ips": True,
                               "redact_secrets_like": []}},
    }
    config_path = tmp_path / "config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(cfg, f)

    reset_config_cache()
    # Route the server's audit_log through the temp config so the test is
    # hermetic, while still exercising the REAL audit_log hashing/redaction.
    monkeypatch.setattr(
        mcp_hybrid_server, "audit_log",
        lambda event: _real_audit_log(event, config_path=str(config_path)),
    )

    long_query = "veeam immutability backup repository " * 5  # > 100 chars
    msg = {
        "jsonrpc": "2.0", "id": 99, "method": "tools/call",
        "params": {"name": "hybrid_search", "arguments": {"query": long_query}},
    }
    result = handle_message(msg, retriever)

    # The response payload returned to the caller may still echo the query.
    assert result["result"]["metadata"]["query"] == long_query

    event = json.loads(audit_file.read_text().strip())
    assert "query" not in event, "raw query must not be persisted"
    assert "query_hash" in event
    # Parity: identical to what the HTTP/graph audit path writes for this query.
    assert event["query_hash"] == hash_query(long_query)
    reset_config_cache()


# ---------------------------------------------------------------------------
# 11. top_k coercion: a JSON-RPC client can send top_k as a string/float/null
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        (5, 5),
        ("3", 3),       # JSON string
        (4.0, 4),       # float
        ("abc", 5),     # non-numeric -> default
        (None, 5),      # null -> default
        (0, 5),         # non-positive -> default
        (-2, 5),        # negative -> default
        (999, 50),      # above ceiling -> clamped to _MAX_TOP_K
    ],
)
def test_coerce_top_k(raw, expected):
    assert mcp_hybrid_server._coerce_top_k(raw) == expected


def test_string_top_k_does_not_crash_slice(retriever):
    """A string top_k must be coerced before slicing, not raise TypeError."""
    msg = {
        "jsonrpc": "2.0", "id": 11, "method": "tools/call",
        "params": {"name": "hybrid_search",
                   "arguments": {"query": "test", "top_k": "1", "mode": "hybrid"}},
    }
    result = handle_message(msg, retriever)
    assert "error" not in result
    assert len(result["result"]["chunks"]) == 1  # coerced "1" -> slice to 1


# ---------------------------------------------------------------------------
# 12. Generic (non-RAGError) failures are redacted before leaving the process
# ---------------------------------------------------------------------------

def test_generic_error_is_redacted(retriever):
    """A raw exception string carrying a secret must be redacted in the error body."""
    secret = "sk-" + "a" * 40  # matches default policy.privacy.redact_secrets_like
    reset_config_cache()
    retriever.semantic_search.side_effect = RuntimeError(f"upstream failed token={secret}")
    msg = {
        "jsonrpc": "2.0", "id": 12, "method": "tools/call",
        "params": {"name": "hybrid_search",
                   "arguments": {"query": "test", "mode": "semantic"}},
    }
    result = handle_message(msg, retriever)

    assert result["error"]["code"] == -32000
    message = result["error"]["message"]
    assert secret not in message, "raw secret must not leak into the JSON-RPC error"
    assert "[REDACTED_SECRET]" in message
    reset_config_cache()


# ---------------------------------------------------------------------------
# 13. Mode normalization: audit + metadata record the mode actually executed
# ---------------------------------------------------------------------------

def test_unknown_mode_normalised_in_audit_and_metadata(retriever, tmp_path, monkeypatch):
    """A mode value not in {semantic, keyword} falls through to the hybrid
    branch in dispatch, but pre-fix the audit and the response metadata
    recorded the raw client value (e.g. 'hybird'), causing the audit log to
    disagree with what actually ran."""
    audit_file = tmp_path / "audit.jsonl"
    cfg = {
        "logging": {"audit_file": str(audit_file), "audit_fields": {"include_query_hash": True}},
        "policy": {"privacy": {"redact_emails": False, "redact_ips": False, "redact_secrets_like": []}},
    }
    config_path = tmp_path / "config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(cfg, f)

    reset_config_cache()
    monkeypatch.setattr(
        mcp_hybrid_server, "audit_log",
        lambda event: _real_audit_log(event, config_path=str(config_path)),
    )

    msg = {
        "jsonrpc": "2.0", "id": 13, "method": "tools/call",
        "params": {"name": "hybrid_search",
                   "arguments": {"query": "test", "mode": "hybird"}},  # typo
    }
    result = handle_message(msg, retriever)
    assert result["result"]["metadata"]["retrieval_mode"] == "hybrid"

    event = json.loads(audit_file.read_text().strip())
    assert event["retrieval_mode"] == "hybrid", "audit must record the mode that ran, not the typo"
    reset_config_cache()


# ---------------------------------------------------------------------------
# 14. Audit parity on failure: MCP failures must write an audit event too
# ---------------------------------------------------------------------------

def test_rag_error_writes_audit_event(retriever, tmp_path, monkeypatch):
    """A RAGError on the MCP path must produce an mcp_rag_error audit event so
    the audit log captures failures alongside successes — parity with the
    HTTP graph_error path. Pre-fix the error branch returned silently and the
    failure left no audit trace."""
    audit_file = tmp_path / "audit.jsonl"
    cfg = {
        "logging": {"audit_file": str(audit_file), "audit_fields": {"include_query_hash": True}},
        "policy": {"privacy": {"redact_emails": False, "redact_ips": False, "redact_secrets_like": []}},
    }
    config_path = tmp_path / "config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(cfg, f)

    reset_config_cache()
    monkeypatch.setattr(
        mcp_hybrid_server, "audit_log",
        lambda event: _real_audit_log(event, config_path=str(config_path)),
    )

    retriever.hybrid_search.side_effect = RAGError("no index", code="IDX")
    msg = {
        "jsonrpc": "2.0", "id": 14, "method": "tools/call",
        "params": {"name": "hybrid_search",
                   "arguments": {"query": "anything", "mode": "hybrid"}},
    }
    result = handle_message(msg, retriever)
    assert result["error"]["code"] == -32000

    event = json.loads(audit_file.read_text().strip())
    assert event["event"] == "mcp_rag_error"
    assert event["retrieval_mode"] == "hybrid"
    assert "IDX" in event["error"]
    # Query field is hashed by audit_log, never persisted as raw text.
    assert event.get("query_hash") == hash_query("anything")
    assert "query" not in event
    reset_config_cache()


def test_generic_error_writes_audit_event(retriever, tmp_path, monkeypatch):
    """A non-RAGError on the MCP path must also produce an audit event, with the
    same redaction the JSON-RPC error body uses."""
    audit_file = tmp_path / "audit.jsonl"
    cfg = {
        "logging": {"audit_file": str(audit_file), "audit_fields": {"include_query_hash": True}},
        "policy": {"privacy": {"redact_emails": False, "redact_ips": False,
                               "redact_secrets_like": [r"sk-[A-Za-z0-9]{20,}"]}},
    }
    config_path = tmp_path / "config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(cfg, f)

    reset_config_cache()
    # Pin _get_config to the test config for both the audit write AND the
    # in-flight redact_sensitive call (the latter is what poisons the cache
    # in the generic-error branch — redact_sensitive uses the default config
    # path and the global cache is non-keyed on the arg, so a subsequent
    # audit_log(config_path=...) silently reuses whatever was cached first).
    # Dotted-string form so the file does not need a second utils.logger
    # import alongside the existing `from utils.logger import ...` (CodeQL).
    monkeypatch.setattr("utils.logger._get_config", lambda *_a, **_kw: cfg)
    monkeypatch.setattr(
        mcp_hybrid_server, "audit_log",
        lambda event: _real_audit_log(event, config_path=str(config_path)),
    )

    secret = "sk-" + "a" * 40
    retriever.semantic_search.side_effect = RuntimeError(f"upstream token={secret}")
    msg = {
        "jsonrpc": "2.0", "id": 15, "method": "tools/call",
        "params": {"name": "hybrid_search",
                   "arguments": {"query": "anything", "mode": "semantic"}},
    }
    handle_message(msg, retriever)

    event = json.loads(audit_file.read_text().strip())
    assert event["event"] == "mcp_rag_error"
    assert event["retrieval_mode"] == "semantic"
    assert secret not in event["error"], "audit must not persist the raw secret"
    assert "[REDACTED_SECRET]" in event["error"]
    reset_config_cache()
