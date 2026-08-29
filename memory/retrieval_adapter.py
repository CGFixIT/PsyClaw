"""Fuse FTS memory hits into hybrid SearchResult lists (optional, default off)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from memory.flags import facts_retrieval_enabled

if TYPE_CHECKING:
    from retrieval.results import SearchResult

logger = logging.getLogger("cyclaw.memory.retrieval")


def fuse_memory_hits(
    query: str,
    corpus_hits: list[SearchResult],
    cfg: dict[str, Any],
) -> list[SearchResult]:
    """Append memory fact hits and re-sort by score.

    Callers in hybrid_search must still wrap in try/except. This function is
    defensive but may raise on programmer error; hooks catch everything.
    """
    from retrieval.results import SearchResult as SR  # lazy — no hybrid_search / Chroma stack

    mem = cfg.get("memory") or {}
    if mem.get("enabled") is not True:
        return list(corpus_hits)
    fusion = mem.get("retrieval_fusion") or {}
    if fusion.get("enabled") is not True:
        return list(corpus_hits)
    if not facts_retrieval_enabled(mem):
        return list(corpus_hits)

    max_hits = int(fusion.get("max_hits", 3) or 3)
    rrf_k = int(fusion.get("rrf_k", 60) or 60)
    source_prefix = str(fusion.get("source_prefix") or "memory:fact:")

    from memory.store import search_facts_fts

    fts_hits = search_facts_fts(cfg, query, limit=max_hits)
    if not fts_hits:
        return list(corpus_hits)

    memory_results: list[SR] = []
    for rank, (fact_id, content, _bm25_rank) in enumerate(fts_hits):
        score = 1.0 / (rrf_k + rank)
        memory_results.append(
            SR(
                text=content,
                score=score,
                source=f"{source_prefix}{fact_id}",
                chunk_id=int(fact_id),
                stem_tags=["memory", "fact"],
                retrieval_mode="memory",
                source_sha256="",
                rrf_score=score,
            )
        )

    merged = list(corpus_hits) + memory_results
    merged.sort(key=lambda h: h.score, reverse=True)
    return merged
