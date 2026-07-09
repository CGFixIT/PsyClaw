"""Unit tests for hybrid search with mocked embeddings.

Tests RRF fusion logic, graceful degradation, and score calculation
without requiring live sentence-transformers or ChromaDB indices.
"""

import json
from functools import lru_cache
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from rank_bm25 import BM25Okapi

from retrieval.stemmer import tokenize_and_stem
from retrieval.hybrid_search import HybridRetriever, SearchResult
from retrieval.vector_store import parse_stem_tags


class TestRRFFusion:
    """Test Reciprocal Rank Fusion math independently."""

    def test_rrf_score_calculation(self):
        """Verify RRF formula: 1 / (k + rank)"""
        k = 60
        # Rank 0 (best): 1/60 = 0.01667
        # Rank 1: 1/61 = 0.01639
        assert abs(1 / (k + 0) - 0.01667) < 0.001
        assert abs(1 / (k + 1) - 0.01639) < 0.001

    def test_combined_rrf_scores_higher(self):
        """Document appearing in both semantic and keyword gets higher score."""
        k = 60
        semantic_only = 1 / (k + 0)  # 0.01667
        combined = 1 / (k + 0) + 1 / (k + 2)  # 0.01667 + 0.01613 = 0.0328
        assert combined > semantic_only

    def test_rrf_ordering(self):
        """Higher combined rank should yield higher score."""
        k = 60
        score_rank_0_0 = 1 / (k + 0) + 1 / (k + 0)  # Best in both
        score_rank_0_3 = 1 / (k + 0) + 1 / (k + 3)  # Best in one, 4th in other
        score_rank_2_2 = 1 / (k + 2) + 1 / (k + 2)  # 3rd in both
        assert score_rank_0_0 > score_rank_0_3
        assert score_rank_0_3 > score_rank_2_2


class TestSinglePathNormalization:
    """Single-path fallback scores must be re-based into the RRF range so the
    min_score gate (calibrated for fused 1/(k+rank) output) stays meaningful."""

    @staticmethod
    def _kw_hit(score, chunk_id):
        return SearchResult(
            text="t", score=score, source="s.md", chunk_id=chunk_id,
            stem_tags=[], retrieval_mode="keyword",
            keyword_score=score, keyword_rank=chunk_id,
        )

    def test_raw_bm25_scores_rebased_to_rrf(self):
        fake = SimpleNamespace(rrf_k=60)
        # Raw BM25 scores (unbounded) — 2.7 would trivially clear min_score=0.028
        hits = [self._kw_hit(2.7, 0), self._kw_hit(0.01, 1)]
        out = HybridRetriever._normalize_single_path(fake, hits)

        assert out[0].score == pytest.approx(1 / 60)
        assert out[1].score == pytest.approx(1 / 61)
        # rrf_score mirrors score; keyword contrib is populated for a keyword path
        assert out[0].rrf_score == pytest.approx(1 / 60)
        assert out[0].rrf_keyword_contrib == pytest.approx(1 / 60)
        # Ordering preserved (rank 0 outranks rank 1)
        assert out[0].score > out[1].score

    def test_single_path_top_below_fusion_agreement(self):
        """A single-path top hit (one RRF term) must score below two agreeing
        paths (two terms) — degraded retrieval is lower confidence."""
        fake = SimpleNamespace(rrf_k=60)
        out = HybridRetriever._normalize_single_path(fake, [self._kw_hit(9.9, 0)])
        single = out[0].score
        both_paths_agree = 1 / 60 + 1 / 60
        assert single < both_paths_agree


class TestFusionReturnsFullUnion:
    """The fuser must return the entire RRF-fused union and let each caller
    slice to its own budget. A previous inner cap of
    ``max(top_k_semantic, top_k_keyword)`` silently dropped chunks an MCP caller
    requested via ``top_k > 5``."""

    @staticmethod
    def _hit(mode, chunk_id):
        return SearchResult(
            text=f"t{chunk_id}", score=1.0, source="s.md", chunk_id=chunk_id,
            stem_tags=[], retrieval_mode=mode,
        )

    def test_disjoint_paths_are_not_truncated(self):
        # 5 semantic + 5 keyword chunks with NO overlap -> a 10-chunk union.
        sem = [self._hit("semantic", i) for i in range(5)]
        kw = [self._hit("keyword", i) for i in range(5, 10)]
        fake = SimpleNamespace(
            rrf_k=60, top_k_semantic=5, top_k_keyword=5,
            semantic_search=lambda q: sem,
            keyword_search=lambda q: kw,
        )
        out = HybridRetriever.hybrid_search(fake, "q")
        # Pre-fix this returned only 5 (max(5, 5)); the other 5 were dropped.
        assert len(out) == 10
        assert {r.chunk_id for r in out} == set(range(10))


class TestConfigPathAnchoring:
    def test_relative_index_paths_resolve_from_config_dir(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        outside = tmp_path / "outside"
        index_dir = repo / "index"
        index_dir.mkdir(parents=True)
        outside.mkdir()
        (index_dir / "bm25.json").write_text(
            json.dumps({
                "tokenized_corpus": [["alpha"]],
                "chunks": ["alpha"],
                "metadata": [{"source": "a.md", "chunk_id": 0, "stem_tags": "[]"}],
            }),
            encoding="utf-8",
        )
        (repo / "config.yaml").write_text(
            json.dumps({
                "indexing": {
                    "bm25_path": "index/bm25.json",
                    "chroma_path": "index/chroma_db",
                    "collection_name": "test_kb",
                },
                "retrieval": {"top_k_semantic": 1, "top_k_keyword": 1, "rrf_k": 60},
            }),
            encoding="utf-8",
        )

        monkeypatch.chdir(outside)
        with patch("retrieval.hybrid_search.get_vector_reader", return_value=SimpleNamespace(close=lambda: None)) as reader:
            retriever = HybridRetriever(str(repo / "config.yaml"))

        reader_cfg = reader.call_args.args[0]
        assert retriever.config_path == str((repo / "config.yaml").resolve())
        assert reader_cfg["indexing"]["bm25_path"] == str((index_dir / "bm25.json").resolve())
        assert reader_cfg["indexing"]["chroma_path"] == str((index_dir / "chroma_db").resolve())


class TestTokenizationForBM25:
    """Ensure tokenization produces useful BM25 input."""

    def test_veeam_terms(self):
        tokens = tokenize_and_stem("Veeam immutability backup configuration")
        assert "veeam" in tokens
        assert "immut" in tokens or "immutab" in tokens
        assert "backup" in tokens

    def test_technical_query(self):
        tokens = tokenize_and_stem("How do I configure SOBR with S3?")
        assert "configur" in tokens or "configure" in tokens
        assert "sobr" in tokens

    def test_empty_query(self):
        tokens = tokenize_and_stem("")
        assert tokens == []

    def test_single_word(self):
        tokens = tokenize_and_stem("kubernetes")
        assert len(tokens) == 1
        # `kubernetes` is intentionally folded to the domain token `k8s`
        # (retrieval/stemmer.py _CUSTOM_STEMS), not Porter-stemmed.
        assert tokens[0] == "k8s"


class TestParseStemTags:
    """parse_stem_tags must never crash on corrupted metadata."""

    def test_valid_json_string(self):
        assert parse_stem_tags('["veeam", "backup"]') == ["veeam", "backup"]

    def test_already_list(self):
        assert parse_stem_tags(["already", "parsed"]) == ["already", "parsed"]

    def test_empty_json_array(self):
        assert parse_stem_tags("[]") == []

    def test_malformed_json_returns_empty(self):
        assert parse_stem_tags("{truncated") == []

    def test_none_returns_empty(self):
        assert parse_stem_tags(None) == []

    def test_non_list_json_returns_empty(self):
        assert parse_stem_tags('"just a string"') == []

    def test_empty_string_returns_empty(self):
        assert parse_stem_tags("") == []


class TestBM25ScoreCache:
    """keyword_search()'s BM25 score cache must speed up repeated identical
    queries without letting downstream mutation of one call's SearchResult
    objects (RRF fusion sets hit.score / hit.rrf_score in place) leak into a
    later identical call's results.
    """

    @staticmethod
    def _make_retriever():
        # Bypass HybridRetriever.__init__ (which also builds a vector-store
        # reader) -- these tests only exercise the BM25/keyword leg, so only
        # the attributes keyword_search()/_normalize_single_path() touch are
        # set, mirroring exactly what __init__ assigns for those.
        chunks = ["RAG retrieval augmented generation", "ChromaDB vector database",
                  "BM25 keyword search algorithm"]
        metadata = [{"source": f"doc{i}.md", "chunk_id": i, "stem_tags": "[]"}
                    for i in range(len(chunks))]
        # Tokenize the corpus the same way retrieval/indexer.py does (via
        # tokenize_and_stem), not a naive .split() -- otherwise query tokens
        # (stemmed) never match corpus tokens (unstemmed) and every query
        # scores 0 against every chunk.
        tokenized = [tokenize_and_stem(c) for c in chunks]
        r = object.__new__(HybridRetriever)
        r.bm25 = BM25Okapi(tokenized)
        r.bm25_chunks = chunks
        r.bm25_metadata = metadata
        r.top_k_keyword = 5
        r.rrf_k = 60
        r._bm25_scores = lru_cache(maxsize=256)(r.bm25.get_scores)
        return r

    def test_repeated_identical_query_hits_cache(self):
        r = self._make_retriever()
        calls = []
        real_get_scores = r.bm25.get_scores

        def counting_get_scores(query):
            calls.append(query)
            return real_get_scores(query)

        r.bm25.get_scores = counting_get_scores
        r._bm25_scores = lru_cache(maxsize=256)(r.bm25.get_scores)

        r.keyword_search("retrieval augmented generation")
        r.keyword_search("retrieval augmented generation")
        # Second identical call served from the cache -> get_scores ran once.
        assert len(calls) == 1

    def test_cached_scores_survive_downstream_mutation(self):
        # This is the exact hazard the cache design must avoid: RRF fusion
        # mutates SearchResult.score / .rrf_score in place
        # (_normalize_single_path). If keyword_search() cached and returned
        # the SAME SearchResult objects across calls, mutating the first
        # call's results would corrupt the second call's results too.
        r = self._make_retriever()
        first = r.keyword_search("retrieval augmented generation")
        assert first  # sanity: the fixture corpus does match this query
        original_scores = [h.score for h in first]

        # Simulate what hybrid_search()/_normalize_single_path() do downstream.
        for h in first:
            h.score = 999.0
            h.rrf_score = 999.0

        second = r.keyword_search("retrieval augmented generation")
        assert [h.score for h in second] == original_scores
        assert all(h.rrf_score is None for h in second)
        # And the two calls must not even share object identity.
        assert all(a is not b for a, b in zip(first, second, strict=True))

    def test_hybrid_search_bm25_only_fallback_not_corrupted_by_repeat_calls(self):
        # End-to-end version of the above via the real fallback path: with no
        # semantic hits, hybrid_search() routes through _normalize_single_path,
        # which mutates hit.score/.rrf_score in place. Calling hybrid_search()
        # twice for the same query must give the same rrf_score both times.
        r = self._make_retriever()
        r.semantic_search = lambda query, k=None: []

        first = r.hybrid_search("retrieval augmented generation")
        second = r.hybrid_search("retrieval augmented generation")
        assert [h.rrf_score for h in first] == [h.rrf_score for h in second]
        assert [h.score for h in first] == [h.score for h in second]
