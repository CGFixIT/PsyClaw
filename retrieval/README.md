# `retrieval/` — hybrid search and indexing

The RAG layer: local CPU embeddings + ChromaDB semantic search fused with
BM25 keyword search via Reciprocal Rank Fusion. Retrieval is the
**unconditional first step** of every query (invariant I1) — no LLM call
precedes it.

## Modules

| Module | Role |
|---|---|
| `results.py` | `SearchResult` dataclass. Imported by the retriever and by optional memory fusion so the memory package does not pull in Chroma/BM25. Re-exported from `hybrid_search.py`. |
| `hybrid_search.py` | `HybridRetriever`: ChromaDB semantic leg + BM25Okapi keyword leg → RRF fusion (`retrieval.rrf_k`, shipped 60). Degrades gracefully if one leg fails. |
| `indexer.py` | Corpus ingestion from `data/corpus/` (walked recursively; file types from `corpus.extensions`, shipped `[".md", ".txt"]`, matched case-insensitively): chunking (`indexing.chunk_size`/`chunk_overlap`), chunk sanitization via the prompt filter, writes both indices. Run `python -m retrieval.indexer` (or `cyclaw-index`) explicitly — the server never builds the index on startup or on a query — the only in-process build is the operator-triggered `POST /index/build` route (loopback peer + same-origin, background thread, progress via `GET /index/status`); a missing index is fail-soft (503 `INDEX_NOT_FOUND`). |
| `embeddings.py` | Local sentence-transformers embeddings, device hardcoded to CPU (`EMBED_DEVICE` — cross-platform ranking determinism; see the constant's own comment). Triple `lru_cache`; `embedding_fingerprint()` detects index staleness. HF offline flags are set conditionally, never blanket (see `utils/telemetry_kill.py`'s exclusion note). |
| `vector_store.py` | Pluggable semantic backend: embedded ChromaDB `PersistentClient` (default, offline-first) or pgvector (`indexing.vector_backend: "pgvector"` + Postgres DSN). The sole ChromaDB chokepoint — it applies the telemetry kill itself. Every `PersistentClient(...)` call here must pass `Settings(anonymized_telemetry=False)`; `gate.py` calls `utils.telemetry_kill.verify_telemetry_contract()` at boot, which AST-parses this file and fails closed if any site is missing it. RRF and BM25 are backend-agnostic. |
| `stemmer.py` | Porter-based stemmer with custom AI/DevOps/CyClaw vocabulary; avoids NLTK punkt (CVE surface). Pins `nltk==3.10.3` (closes a `PorterStemmer` DoS cluster) and caps every token at 256 chars before stemming as defense in depth (CVE-2026-81722). |
| `clear_cache.py` | Dry-run-by-default embedding-cache cleaner (`--apply` to delete). The cache is a regenerable artifact; index/audit/soul are untouched. |

## Numbers that trip people

- `retrieval.min_score` (shipped **0.028**) is on the **RRF scale**, not
  cosine — fused scores rarely exceed ~0.1. "Fixing" it toward 0.5 routes
  every query to the user gate.
- `indexing.chunk_overlap` must stay `< chunk_size`.
- The BM25 store is **JSON** (`index/bm25.json`), never pickle — pickle is an
  RCE vector and `test_security` guards the format.
- All values live in `config.yaml`, with one deliberate exception: the
  query-embedding LRU cache size is fixed at import time by `functools.lru_cache`
  (default `2048` in `embeddings.py`), so it is overridable only via the
  `CYCLAW_EMBED_CACHE_SIZE` env var, not `config.yaml`.

## Related

- Corpus location and rules: [`data/README.md`](../data/README.md)
- Retrieval-only MCP surface: `mcp_hybrid_server.py` (no LLM path,
  `sampling: None`). The harness `/tools` command AST-catalogs `hybrid_search`
  but does not invoke it (`invoked=false`). Live search stays on
  `POST /query` or a Claude Desktop MCP client.
- Index health tooling: `.claude/skills/index-doctor/`
