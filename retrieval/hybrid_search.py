"""
Hybrid retrieval: semantic (ChromaDB) + keyword (BM25) with RRF fusion.

Uses sentence-transformers directly for query embeddings (no Ollama).
Degrades gracefully if one retrieval path fails.
"""

import heapq
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from rank_bm25 import BM25Okapi

from utils.errors import EmbeddingServiceError, IndexNotFoundError
from utils.logger import audit_log

from .embeddings import get_embedding
from .indexer import _anchor_index_paths, _resolve_config_path
from .stemmer import tokenize_and_stem
from .vector_store import get_vector_reader, parse_stem_tags


@dataclass
class SearchResult:
    """Single search result with provenance metadata."""
    text: str
    score: float
    source: str
    chunk_id: int
    stem_tags: list[str]
    retrieval_mode: str  # "semantic" | "keyword" | "hybrid"
    source_sha256: str = ""
    semantic_score: float | None = None
    semantic_rank: int | None = None
    keyword_score: float | None = None
    keyword_rank: int | None = None
    rrf_score: float | None = None
    rrf_semantic_contrib: float | None = None
    rrf_keyword_contrib: float | None = None

class HybridRetriever:
    def __init__(self, config_path: str = "config.yaml"):
        resolved_config_path = _resolve_config_path(config_path)
        with open(resolved_config_path, encoding="utf-8") as f:
            self.cfg = _anchor_index_paths(yaml.safe_load(f), resolved_config_path)

        self.config_path = str(resolved_config_path)
        bm25_path = self.cfg["indexing"]["bm25_path"]

        # Semantic backend is pluggable (ChromaDB default, or pgvector). The reader
        # validates its own index/collection existence and raises IndexNotFoundError.
        # BM25 stays file-based regardless of the vector backend.
        if not Path(bm25_path).exists():
            raise IndexNotFoundError(
                f"BM25 index not found at {bm25_path}. Run: python -m retrieval.indexer"
            )

        self._vector_reader = get_vector_reader(self.cfg)

        # Validate path is a regular file before deserializing. The BM25 index is
        # project-generated (retrieval/indexer.py) and read from a config-controlled
        # path — not from user-supplied input.
        resolved = Path(bm25_path).resolve()
        if not resolved.is_file():
            raise IndexNotFoundError(f"BM25 index path is not a regular file: {bm25_path}")
        with open(resolved, encoding="utf-8") as f:  # DevSkim: ignore DS161085 - project-generated index
            bm25_data = json.load(f)
            tokenized = bm25_data["tokenized_corpus"]
            chunks = bm25_data["chunks"]
            metadata = bm25_data["metadata"]
            # Length parity is load-bearing: keyword_search indexes into all three
            # by the same rank. Mismatch or empty corpus raises IndexError /
            # ZeroDivisionError on the hot path and is NOT covered by the
            # hybrid_search degrade except list — surface as IndexNotFoundError
            # so gate boot soft-fails like a missing index.
            n_tok, n_chunks, n_meta = len(tokenized), len(chunks), len(metadata)
            if n_tok == 0 or n_tok != n_chunks or n_tok != n_meta:
                raise IndexNotFoundError(
                    f"BM25 index corrupt or empty at {bm25_path} "
                    f"(tokenized={n_tok}, chunks={n_chunks}, metadata={n_meta}). "
                    f"Run: python -m retrieval.indexer"
                )
            self.bm25 = BM25Okapi(tokenized)
            self.bm25_chunks = chunks
            self.bm25_metadata = metadata

        self.top_k_semantic = self.cfg["retrieval"]["top_k_semantic"]
        self.top_k_keyword = self.cfg["retrieval"]["top_k_keyword"]
        self.rrf_k = self.cfg["retrieval"]["rrf_k"]

        # Per-instance BM25 score cache: repeated identical queries (common on
        # the retrieval hot path) previously re-ran get_scores() -- an
        # O(corpus size) computation over the full BM25 term/document matrix
        # -- on every single call. Built as an lru_cache-wrapped closure over
        # this instance's own bound self.bm25.get_scores and stored as an
        # instance attribute, NOT a bare @lru_cache decorating the method
        # itself: the latter would be one cache shared by the whole class,
        # pinning every HybridRetriever instance ever passed to it alive for
        # the life of the process (a real leak across the many short-lived
        # instances tests construct). This way the cache dies with the
        # instance. Caches only the raw, never-mutated scores array returned
        # by BM25Okapi -- NOT the SearchResult objects keyword_search() builds
        # from it below, since downstream RRF fusion mutates those objects in
        # place (hit.score / hit.rrf_score in _normalize_single_path and
        # hybrid_search) and returning shared instances across calls would
        # leak one query's fusion state into the next identical query.
        self._bm25_scores = lru_cache(maxsize=256)(self.bm25.get_scores)

    def close(self) -> None:
        """Close the underlying vector store connection.

        No-op for ChromaDB (embedded, no persistent connection). For pgvector
        the psycopg connection is released so the OS reclaims the socket.
        """
        self._vector_reader.close()

    def semantic_search(self, query: str, k: int | None = None) -> list[SearchResult]:
        if k is None:
            k = self.top_k_semantic
        emb = get_embedding(query, self.config_path)
        # The vector reader returns normalized hits (text/score/source/chunk_id/
        # stem_tags) for whichever backend is configured; score is already the
        # cosine similarity (1 - distance), identical across ChromaDB and pgvector.
        raw = self._vector_reader.query(emb, k)
        hits = []
        for i, r in enumerate(raw):
            score = r["score"]
            hits.append(SearchResult(
                text=r["text"], score=score, source=r["source"],
                chunk_id=r["chunk_id"], stem_tags=r["stem_tags"],
                source_sha256=r.get("source_sha256", ""),
                retrieval_mode="semantic", semantic_score=score, semantic_rank=i,
            ))
        return hits

    def keyword_search(self, query: str, k: int | None = None) -> list[SearchResult]:
        """BM25 keyword leg. Returns UP TO ``k`` hits, but only those with a
        positive score — chunks the query tokens don't appear in (score 0) are
        dropped. So a query with few keyword matches yields fewer than ``k`` hits,
        which is intentional: zero-score docs carry no keyword signal and would
        add noise to the RRF fusion. The semantic leg fills the rank space the
        keyword leg leaves empty; RRF (hybrid_search) tolerates asymmetric leg
        sizes by design.
        """
        if k is None:
            k = self.top_k_keyword
        query_tokens = tokenize_and_stem(query)
        # lru_cache requires hashable args -- tokenize_and_stem() returns a
        # list (its public contract; callers elsewhere may hold/extend it),
        # so convert to a tuple only for the cache key. This tuple() call is
        # O(number of query tokens), trivial next to the O(corpus size)
        # get_scores() computation it lets repeated identical queries skip.
        scores = self._bm25_scores(tuple(query_tokens))
        # Top-k selection only: heapq.nlargest is O(n log k) and matches the
        # ordering of sorted(..., reverse=True)[:k], avoiding a full O(n log n)
        # sort of every chunk in the corpus on each keyword query.
        top_indices = heapq.nlargest(k, range(len(scores)), key=scores.__getitem__)
        hits = []
        for idx in top_indices:
            if scores[idx] > 0:
                meta = self.bm25_metadata[idx]
                stem_tags = parse_stem_tags(meta.get("stem_tags", "[]"))
                hits.append(SearchResult(
                    text=self.bm25_chunks[idx], score=scores[idx],
                    source=meta["source"], chunk_id=meta["chunk_id"],
                    stem_tags=stem_tags, retrieval_mode="keyword",
                    source_sha256=meta.get("source_sha256", ""),
                    keyword_score=scores[idx], keyword_rank=len(hits),
                ))
        return hits

    def _normalize_single_path(self, hits: list[SearchResult]) -> list[SearchResult]:
        """Re-score a BM25-only fallback result list into the RRF range.

        Raw BM25 scores are unbounded positive floats (a hit can score 2.7) and
        are not comparable to the fused ``rrf_score`` the downstream
        ``min_score`` gate is calibrated against. Returning them made
        ``min_score`` misfire: a high raw BM25 score trivially cleared the gate
        (false high-confidence, skipping escalation), while a low one escalated
        even on a relevant hit.

        Reusing the same ``1 / (rrf_k + rank)`` weight the fused path uses keeps
        these scores on one scale. Because a single path contributes only one
        term (vs. two when both paths agree), even the top BM25-only hit stays
        below the fusion-agreement threshold — a degraded retrieval is correctly
        treated as lower confidence rather than silently trusted.

        The semantic-only fallback is intentionally NOT routed here: its raw
        ``1 - distance`` score is already bounded and is the value the gate is
        tuned to admit, so it is returned unchanged by ``hybrid_search``.
        """
        normalized: list[SearchResult] = []
        for rank, hit in enumerate(hits):
            contrib = 1 / (self.rrf_k + rank)
            hit.score = contrib
            hit.rrf_score = contrib
            if hit.retrieval_mode == "semantic":
                hit.rrf_semantic_contrib = contrib
            elif hit.retrieval_mode == "keyword":
                hit.rrf_keyword_contrib = contrib
            normalized.append(hit)
        return normalized

    def hybrid_search(self, query: str) -> list[SearchResult]:
        semantic_hits: list[SearchResult] = []
        keyword_hits: list[SearchResult] = []
        try:
            semantic_hits = self.semantic_search(query)
        except EmbeddingServiceError as e:
            audit_log({"event": "retrieval_degraded", "path": "semantic", "error": str(e)})
        try:
            keyword_hits = self.keyword_search(query)
        except (json.JSONDecodeError, KeyError, AttributeError, IndexError, TypeError, ValueError) as e:
            # IndexError/TypeError: corrupt metadata shape after a partial write
            # or manual edit; ValueError: BM25 edge cases. Soft-degrade like the
            # semantic EmbeddingServiceError path so hybrid still answers.
            audit_log({"event": "retrieval_degraded", "path": "keyword", "error": str(e)})

        if not semantic_hits and not keyword_hits:
            return []
        if not semantic_hits:
            # BM25-only fallback: raw BM25 scores are unbounded positive floats
            # and are NOT comparable to the fused rrf_score the min_score gate is
            # calibrated against (a raw 2.7 would trivially clear 0.028 as false
            # high-confidence). Rebase them into the RRF range.
            return self._normalize_single_path(keyword_hits)
        if not keyword_hits:
            # Semantic-only fallback: raw `1 - distance` already lies in a
            # bounded, comparable range and is what the min_score gate is tuned
            # to admit (a strong cosine hit clears 0.028 — this is the path the
            # CI RAG smoke exercises on the single-chunk corpus, where BM25 IDF
            # degenerates to <=0 and the keyword path is empty). Leave it as-is:
            # re-basing to 1/(k+rank) would discard the similarity magnitude and
            # sink genuine hits below the gate.
            return semantic_hits

        scores = {}
        semantic_meta = {}
        keyword_meta = {}

        # No "text" in the per-leg meta dicts below: the merged SearchResult
        # already carries the chunk text (text=hit.text from all_hits). These
        # dicts only feed the merged result's semantic_*/keyword_* fields below.
        for rank, hit in enumerate(semantic_hits):
            key = (hit.source, hit.chunk_id)
            contrib = 1 / (self.rrf_k + rank)
            scores[key] = scores.get(key, 0) + contrib
            semantic_meta[key] = {"rank": rank, "score": hit.score, "rrf_contrib": contrib,
                                   "stem_tags": hit.stem_tags}

        for rank, hit in enumerate(keyword_hits):
            key = (hit.source, hit.chunk_id)
            contrib = 1 / (self.rrf_k + rank)
            scores[key] = scores.get(key, 0) + contrib
            keyword_meta[key] = {"rank": rank, "score": hit.score, "rrf_contrib": contrib,
                                  "stem_tags": hit.stem_tags}

        all_hits = {(h.source, h.chunk_id): h for h in semantic_hits + keyword_hits}
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        merged = []
        for (source, chunk_id), score in ranked:
            hit = all_hits[(source, chunk_id)]
            sm = semantic_meta.get((source, chunk_id))
            km = keyword_meta.get((source, chunk_id))
            merged.append(SearchResult(
                text=hit.text, score=score, source=source, chunk_id=chunk_id,
                stem_tags=hit.stem_tags, retrieval_mode="hybrid",
                source_sha256=hit.source_sha256,
                semantic_score=sm["score"] if sm else None,
                semantic_rank=sm["rank"] if sm else None,
                keyword_score=km["score"] if km else None,
                keyword_rank=km["rank"] if km else None,
                rrf_score=score,
                rrf_semantic_contrib=sm["rrf_contrib"] if sm else None,
                rrf_keyword_contrib=km["rrf_contrib"] if km else None,
            ))

        # Return the full RRF-fused union. The previous
        # ``merged[:max(top_k_semantic, top_k_keyword)]`` cap was both redundant
        # and lossy: every caller already slices to its own budget --
        # graph.py via ``_format_context_chunks(limit=...)`` / ``docs[:5]`` and
        # mcp_hybrid_server via ``hybrid_search(query)[:top_k]``. Capping here to
        # 5 silently overrode an MCP caller asking for ``top_k > 5`` (the fused
        # union can hold up to top_k_semantic + top_k_keyword distinct chunks),
        # dropping chunks the caller explicitly requested before they ever saw
        # them. Slicing is the caller's responsibility, not the fuser's.
        return merged
