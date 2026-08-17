"""Search hit dataclass shared by hybrid retrieval and optional memory fusion.

Kept out of hybrid_search.py so memory.retrieval_adapter can type and
construct hits without importing the retriever (and its Chroma/BM25 stack).
"""

from __future__ import annotations

from dataclasses import dataclass


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
