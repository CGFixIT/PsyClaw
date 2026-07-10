"""CyClaw MCP server — retrieval-only, no sampling capability.

Protocol-level guarantee: this server CANNOT invoke an LLM.
Only exposes hybrid_search tool via JSON-RPC over stdio.
User gate handled entirely in FastAPI HTTP layer.
"""

import json
import sys

from retrieval.hybrid_search import HybridRetriever
from utils.errors import RAGError
from utils.logger import audit_log, redact_sensitive

# Bounds for the client-supplied top_k. The retriever fuses at most
# top_k_semantic + top_k_keyword distinct chunks, so an unbounded value buys
# nothing; a non-int or non-positive value would otherwise crash the slice.
_DEFAULT_TOP_K = 5
_MAX_TOP_K = 50
# A stdio JSON-RPC caller could otherwise hand this process an arbitrarily
# large string to embed/tokenize on every search — this ceiling is just a
# sanity bound on request size, not a security filter (this path doesn't run
# check_input; see the DESIGN DECISION comment in _handle_search below).
_MAX_QUERY_CHARS = 65536


def _coerce_top_k(raw: object) -> int:
    """Coerce a JSON-RPC ``top_k`` argument to a positive, bounded int.

    JSON-RPC clients can send ``top_k`` as a string, float, null, or a negative
    number. ``results[:top_k]`` would raise TypeError on a string and silently
    return nothing on a negative value, so normalise here instead.
    """
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _DEFAULT_TOP_K
    if value < 1:
        return _DEFAULT_TOP_K
    return min(value, _MAX_TOP_K)

CAPABILITIES = {
    "tools": {},
    "sampling": None  # CRITICAL: No LLM path at protocol level
}

TOOLS = [
    {
        "name": "hybrid_search",
        "description": "Search local .md corpus using semantic + keyword retrieval with RRF fusion",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "top_k": {"type": "integer", "default": 5, "description": "Max results"},
                "mode": {
                    "type": "string",
                    "enum": ["hybrid", "semantic", "keyword"],
                    "default": "hybrid",
                    "description": "Retrieval mode"
                }
            },
            "required": ["query"]
        }
    }
]

def _error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _score_scale(mode: str, results) -> str:
    """Name the scale of the ``score`` field for this response.

    The three modes emit scores on three incompatible scales — raw BM25
    (unbounded, e.g. 2.7), cosine similarity (bounded 0..1), and RRF fusion
    (~1/rrf_k, e.g. 0.033) — but the payload exposed them all under one bare
    ``score`` key, silently misleading any client comparing across modes.
    (retrieval/hybrid_search.py documents at length why raw BM25 is not
    comparable to the RRF/min_score scale.)

    ``hybrid`` needs one refinement: on a semantic-only degraded retrieval,
    hybrid_search returns the cosine scores unchanged (deliberately — see its
    fallback comments), while both the fused path and the BM25-only fallback
    are on the RRF 1/(rrf_k + rank) scale.
    """
    if mode == "semantic":
        return "cosine_similarity"
    if mode == "keyword":
        return "bm25_raw"
    if results and all(r.retrieval_mode == "semantic" for r in results):
        return "cosine_similarity"
    return "rrf"

def _handle_search(msg_id, args: dict, retriever: HybridRetriever) -> dict:
    # DESIGN DECISION (PR #99 #12): this path intentionally does NOT run
    # utils.sanitizer.check_input. The MCP server is retrieval-only —
    # CAPABILITIES["sampling"] is None, so there is no LLM to escalate a prompt
    # injection to — and it serves a trusted local stdio caller. Prompt-injection
    # filtering here would have no escalation target to protect. If strict policy
    # parity with the HTTP path is ever desired, add check_input(query) below and
    # mirror the HTTP audit-on-block; it is omitted by design today.
    query = args.get("query", "")
    if not isinstance(query, str) or not query:
        return _error(msg_id, -32602, "hybrid_search requires a non-empty string query")
    if len(query) > _MAX_QUERY_CHARS:
        return _error(msg_id, -32602, f"hybrid_search query exceeds {_MAX_QUERY_CHARS} characters")
    top_k = _coerce_top_k(args.get("top_k", _DEFAULT_TOP_K))
    # Normalise mode BEFORE dispatch so the audit event and the response
    # metadata record what was actually executed. Previously the raw client
    # value passed through to both the audit and the metadata, while the
    # dispatch fell back to hybrid for anything not in {semantic, keyword} —
    # so a typo like mode="hybird" would run hybrid retrieval but be logged
    # and reported as mode="hybird".
    requested_mode = args.get("mode", "hybrid")
    mode = requested_mode if requested_mode in ("semantic", "keyword") else "hybrid"
    try:
        if mode == "semantic":
            results = retriever.semantic_search(query, k=top_k)
        elif mode == "keyword":
            results = retriever.keyword_search(query, k=top_k)
        else:
            results = retriever.hybrid_search(query)[:top_k]
        # Audit privacy parity with the HTTP path: pass the FULL query to
        # audit_log, which SHA-256-hashes the "query" field (and redacts PII)
        # before persisting. The stored event therefore holds only query_hash —
        # never raw text — and the hash matches what the HTTP/graph audit path
        # writes for the same query. (Previously this passed query[:100], which
        # both implied cleartext and produced a hash that diverged from HTTP for
        # queries longer than 100 chars.)
        audit_log({"event": "mcp_rag_query", "query": query, "mode": mode,
                   "hit_count": len(results), "top_score": results[0].score if results else 0.0})
        payload = {
            "chunks": [{"text": r.text, "score": r.score, "source": r.source,
                        "chunk_id": r.chunk_id, "source_sha256": r.source_sha256,
                        "stem_tags": r.stem_tags[:5], "mode": r.retrieval_mode}
                       for r in results],
            # The response payload may echo the query — the caller already has it.
            # Only the persisted audit event is privacy-constrained.
            # score_scale names the scale the `score` field is on for THIS
            # response (bm25_raw / cosine_similarity / rrf) — the three modes
            # are not cross-comparable (see _score_scale).
            "metadata": {"query": query, "retrieval_mode": mode,
                         "total_results": len(results),
                         "score_scale": _score_scale(mode, results)}
        }
        return {"jsonrpc": "2.0", "id": msg_id, "result": payload}
    except RAGError as e:
        # Audit parity with the HTTP path (gate.py graph_error). The error body
        # is already sanitised before it leaves the process; the audit field
        # passes through utils.logger redactors as well.
        audit_log({"event": "mcp_rag_error", "query": query, "mode": mode,
                   "error": f"{e.code}: {e.message}"})
        return _error(msg_id, -32000, f"{e.code}: {e.message}")
    except Exception as e:
        # Privacy parity with the HTTP path (gate._sanitize_error): a raw
        # exception string can carry a filesystem path or a token surfaced from a
        # degraded dependency. Redact with the config-driven secret patterns
        # before it leaves the process in the JSON-RPC error body.
        safe_err = redact_sensitive(str(e))
        audit_log({"event": "mcp_rag_error", "query": query, "mode": mode, "error": safe_err})
        return _error(msg_id, -32000, f"Search error: {safe_err}")

def handle_message(msg: dict, retriever: HybridRetriever) -> dict:
    if not isinstance(msg, dict):
        return _error(None, -32600, "Invalid Request")
    method = msg.get("method")
    msg_id = msg.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {
            "protocolVersion": "2025-11-25",
            "capabilities": CAPABILITIES,
            "serverInfo": {"name": "cyclaw-hybrid-rag", "version": "1.0.0"}
        }}
    elif method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
    elif method == "tools/call":
        params = msg.get("params", {})
        if not isinstance(params, dict):
            return _error(msg_id, -32602, "tools/call params must be an object")
        tool_name = params.get("name")
        args = params.get("arguments", {})
        if not isinstance(args, dict):
            return _error(msg_id, -32602, "tools/call arguments must be an object")
        if tool_name == "hybrid_search":
            return _handle_search(msg_id, args, retriever)
        return _error(msg_id, -32601, f"Unknown tool: {tool_name}")
    elif method == "notifications/initialized":
        # JSON-RPC notifications (no "id" field) are fire-and-forget by spec —
        # the caller isn't waiting for a reply, so sending one back would just
        # be a stray line on stdout the client never expects. Returning None
        # here is what main()'s "if response is not None" check relies on to
        # skip writing anything for this method.
        return None
    return _error(msg_id, -32601, f"Unknown method: {method}")

def main():
    try:
        retriever = HybridRetriever()
    except Exception as e:
        sys.stderr.write(f"[MCP] Failed to init retriever: {e}\n")
        sys.exit(1)
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                response = handle_message(msg, retriever)
                if response is not None:
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()
            except json.JSONDecodeError as e:
                err = _error(None, -32700, f"Parse error: {str(e)}")
                sys.stdout.write(json.dumps(err) + "\n")
                sys.stdout.flush()
    finally:
        retriever.close()

if __name__ == "__main__":
    main()
