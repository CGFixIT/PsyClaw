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
from retrieval.hybrid_search import HybridRetriever, SearchResult, _mps_risk_present
from retrieval.vector_store import parse_stem_tags
from utils.errors import EmbeddingServiceError, IndexNotFoundError


class TestRRFFusion:
    """Test Reciprocal Rank Fusion math independently."""

    def test_real_fusion_path_combines_both_legs(self):
        """Exercise the REAL HybridRetriever.hybrid_search fusion path.

        The pure-math tests below recompute RRF on bare Python literals and
        stay green even if the fusion formula in hybrid_search changes; this
        test fails if the real formula drifts from 1/(rrf_k + rank) summed
        across legs.
        """
        def hit(mode, chunk_id, leg_score):
            return SearchResult(
                text=f"t{chunk_id}", score=leg_score, source="s.md",
                chunk_id=chunk_id, stem_tags=[], retrieval_mode=mode,
            )

        # Chunk 0 ranks first in BOTH legs; chunk 1 is semantic-only (rank 1).
        sem = [hit("semantic", 0, 0.91), hit("semantic", 1, 0.80)]
        kw = [hit("keyword", 0, 5.2)]
        fake = SimpleNamespace(
            rrf_k=60, top_k_semantic=5, top_k_keyword=5,
            semantic_search=lambda q: sem,
            keyword_search=lambda q: kw,
        )
        out = HybridRetriever.hybrid_search(fake, "q")

        by_id = {r.chunk_id: r for r in out}
        # Dual-leg hit: two RRF terms, merged as "hybrid", ranked first.
        assert by_id[0].rrf_score == pytest.approx(1 / 60 + 1 / 60)
        assert by_id[0].score == pytest.approx(by_id[0].rrf_score)
        assert by_id[0].retrieval_mode == "hybrid"
        assert by_id[0].rrf_semantic_contrib == pytest.approx(1 / 60)
        assert by_id[0].rrf_keyword_contrib == pytest.approx(1 / 60)
        assert by_id[0].semantic_rank == 0
        assert by_id[0].keyword_rank == 0
        # Single-leg hit: one RRF term at its own rank, outranked by the fused hit.
        assert by_id[1].rrf_score == pytest.approx(1 / 61)
        assert by_id[1].rrf_keyword_contrib is None
        assert out[0].chunk_id == 0

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


class TestHybridDegradePaths:
    """Fail-soft dual-leg paths in hybrid_search — load-bearing for /query
    availability when embeddings or BM25 index shape is broken."""

    @staticmethod
    def _hit(mode: str, chunk_id: int, score: float = 1.0) -> SearchResult:
        return SearchResult(
            text=f"t{chunk_id}", score=score, source="s.md", chunk_id=chunk_id,
            stem_tags=[], retrieval_mode=mode,
        )

    def test_semantic_failure_falls_back_to_normalized_keyword(self) -> None:
        # EmbeddingServiceError on semantic must not crash hybrid_search; BM25
        # hits are rebased via _normalize_single_path (raw 9.9 would clear min_score).
        import types

        kw = [self._hit("keyword", 0, score=9.9), self._hit("keyword", 1, score=0.5)]

        def boom_sem(_q: str) -> list[SearchResult]:
            raise EmbeddingServiceError("embedder offline")

        fake = SimpleNamespace(
            rrf_k=60, top_k_semantic=5, top_k_keyword=5,
            semantic_search=boom_sem,
            keyword_search=lambda q: kw,
        )
        # Bind the real instance method so hybrid_search's self._normalize_* works.
        fake._normalize_single_path = types.MethodType(
            HybridRetriever._normalize_single_path, fake
        )
        with patch("retrieval.hybrid_search.audit_log") as audit:
            out = HybridRetriever.hybrid_search(fake, "q")
        assert len(out) == 2
        assert out[0].score == pytest.approx(1 / 60)
        assert out[1].score == pytest.approx(1 / 61)
        assert any(
            c.args and c.args[0].get("event") == "retrieval_degraded"
            and c.args[0].get("path") == "semantic"
            for c in audit.call_args_list
        )

    def test_keyword_failure_falls_back_to_semantic_unchanged(self) -> None:
        # Soft-degrade keyword path; semantic scores stay raw (not rebased).
        sem = [self._hit("semantic", 0, score=0.91)]

        def boom_kw(_q: str) -> list[SearchResult]:
            raise ValueError("corrupt bm25 meta")

        fake = SimpleNamespace(
            rrf_k=60, top_k_semantic=5, top_k_keyword=5,
            semantic_search=lambda q: sem,
            keyword_search=boom_kw,
        )
        with patch("retrieval.hybrid_search.audit_log") as audit:
            out = HybridRetriever.hybrid_search(fake, "q")
        assert len(out) == 1
        assert out[0].score == pytest.approx(0.91)
        assert any(
            c.args and c.args[0].get("event") == "retrieval_degraded"
            and c.args[0].get("path") == "keyword"
            for c in audit.call_args_list
        )

    def test_both_legs_fail_returns_empty(self) -> None:
        def boom_sem(_q: str) -> list[SearchResult]:
            raise EmbeddingServiceError("down")

        def boom_kw(_q: str) -> list[SearchResult]:
            raise TypeError("bad shape")

        fake = SimpleNamespace(
            rrf_k=60, top_k_semantic=5, top_k_keyword=5,
            semantic_search=boom_sem,
            keyword_search=boom_kw,
        )
        with patch("retrieval.hybrid_search.audit_log"):
            out = HybridRetriever.hybrid_search(fake, "q")
        assert out == []


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

    def test_mismatched_bm25_lengths_raise_index_not_found(self, tmp_path):
        """Corrupt BM25 arrays must fail boot as IndexNotFoundError, not hot-path 500."""
        from utils.errors import IndexNotFoundError

        repo = tmp_path / "repo"
        index_dir = repo / "index"
        index_dir.mkdir(parents=True)
        (index_dir / "bm25.json").write_text(
            json.dumps({
                "tokenized_corpus": [["alpha"], ["beta"]],
                "chunks": ["alpha"],  # length mismatch
                "metadata": [{"source": "a.md", "chunk_id": 0}],
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
        with patch(
            "retrieval.hybrid_search.get_vector_reader",
            return_value=SimpleNamespace(close=lambda: None),
        ):
            with pytest.raises(IndexNotFoundError, match="corrupt or empty"):
                HybridRetriever(str(repo / "config.yaml"))


class TestEmbeddingFingerprintCheck:
    """HybridRetriever.__init__'s embedding-fingerprint guard.

    Stays silent when the reader has no .fingerprint() at all -- e.g. pgvector,
    or any of the SimpleNamespace mocks the rest of this file and
    TestConfigPathAnchoring above already rely on. A MISMATCHED fingerprint is
    always fatal (raises IndexNotFoundError), on any platform -- it is proof
    the index doesn't match the current config. An ABSENT fingerprint is only
    fatal when _mps_risk_present() says this machine could plausibly have
    built that index on MPS; otherwise it warns and continues (PR #734's
    Codex-review finding: the original warn-always design let an Apple Silicon
    checkout silently compare CPU query vectors against MPS-built corpus
    vectors -- exactly the bug this PR exists to fix).
    """

    @staticmethod
    def _build_repo(tmp_path, models_cfg=None):
        repo = tmp_path / "repo"
        index_dir = repo / "index"
        index_dir.mkdir(parents=True)
        (index_dir / "bm25.json").write_text(
            json.dumps({
                "tokenized_corpus": [["alpha"]],
                "chunks": ["alpha"],
                "metadata": [{"source": "a.md", "chunk_id": 0, "stem_tags": "[]"}],
            }),
            encoding="utf-8",
        )
        cfg = {
            "indexing": {
                "bm25_path": "index/bm25.json",
                "chroma_path": "index/chroma_db",
                "collection_name": "test_kb",
            },
            "retrieval": {"top_k_semantic": 1, "top_k_keyword": 1, "rrf_k": 60},
        }
        if models_cfg is not None:
            cfg["models"] = models_cfg
        (repo / "config.yaml").write_text(json.dumps(cfg), encoding="utf-8")
        return repo / "config.yaml"

    def test_no_fingerprint_method_is_a_silent_noop(self, tmp_path):
        # No `models:` section either -- the pre-existing config fixtures in
        # this file omit it, so build_index/HybridRetriever must not KeyError.
        config_path = self._build_repo(tmp_path)
        reader = SimpleNamespace(close=lambda: None)
        with (
            patch("retrieval.hybrid_search.get_vector_reader", return_value=reader),
            patch("retrieval.hybrid_search.audit_log") as audit,
        ):
            HybridRetriever(str(config_path))
        audit.assert_not_called()

    def test_absent_fingerprint_warns_and_audits_when_no_mps_risk(self, tmp_path):
        config_path = self._build_repo(
            tmp_path, models_cfg={"embeddings": {"model": "all-MiniLM-L6-v2", "dim": 384}}
        )
        reader = SimpleNamespace(close=lambda: None, fingerprint=lambda: None)
        with (
            patch("retrieval.hybrid_search.get_vector_reader", return_value=reader),
            patch("retrieval.hybrid_search._mps_risk_present", return_value=False),
            patch("retrieval.hybrid_search.audit_log") as audit,
        ):
            HybridRetriever(str(config_path))  # must not raise
        event = audit.call_args.args[0]
        assert event["event"] == "embedding_fingerprint_absent"
        assert event["expected"] == {"model": "all-MiniLM-L6-v2", "dim": "384", "device": "cpu"}
        assert event["fatal"] is False

    def test_absent_fingerprint_raises_when_mps_available(self, tmp_path):
        # The Codex-review finding this closes: on an Apple Silicon checkout
        # with a pre-fix index (no fingerprint at all), the corpus vectors may
        # have been computed on MPS while queries are now computed on CPU --
        # continuing to serve it silently reproduces the exact cross-device
        # ranking bug this PR fixes. MPS-available is the only signal that
        # distinguishes "provably CPU-built" (Linux/Windows) from "possibly
        # MPS-built" (Apple Silicon) for a fingerprint-less index.
        config_path = self._build_repo(
            tmp_path, models_cfg={"embeddings": {"model": "all-MiniLM-L6-v2", "dim": 384}}
        )
        reader = SimpleNamespace(close=lambda: None, fingerprint=lambda: None)
        with (
            patch("retrieval.hybrid_search.get_vector_reader", return_value=reader),
            patch("retrieval.hybrid_search._mps_risk_present", return_value=True),
            patch("retrieval.hybrid_search.audit_log") as audit,
            pytest.raises(IndexNotFoundError, match="no embedding fingerprint"),
        ):
            HybridRetriever(str(config_path))
        event = audit.call_args.args[0]
        assert event["event"] == "embedding_fingerprint_absent"
        assert event["fatal"] is True

    def test_matching_fingerprint_is_silent(self, tmp_path):
        config_path = self._build_repo(
            tmp_path, models_cfg={"embeddings": {"model": "all-MiniLM-L6-v2", "dim": 384}}
        )
        matching = {"model": "all-MiniLM-L6-v2", "dim": "384", "device": "cpu"}
        reader = SimpleNamespace(close=lambda: None, fingerprint=lambda: matching)
        with (
            patch("retrieval.hybrid_search.get_vector_reader", return_value=reader),
            patch("retrieval.hybrid_search.audit_log") as audit,
        ):
            HybridRetriever(str(config_path))
        audit.assert_not_called()

    def test_mismatched_fingerprint_always_raises(self, tmp_path):
        config_path = self._build_repo(
            tmp_path, models_cfg={"embeddings": {"model": "all-MiniLM-L6-v2", "dim": 384}}
        )
        # A pre-EMBED_DEVICE-pin index -- built while sentence-transformers
        # auto-selected mps, on the exact query that produced PR #734's finding.
        stale = {"model": "all-MiniLM-L6-v2", "dim": "384", "device": "mps"}
        reader = SimpleNamespace(close=lambda: None, fingerprint=lambda: stale)
        with (
            patch("retrieval.hybrid_search.get_vector_reader", return_value=reader),
            # A mismatch is unconditionally fatal -- proves this path does not
            # depend on _mps_risk_present() the way the absent-fingerprint path
            # does; forcing it False here would make a regression to
            # "mismatch also needs MPS risk" pass by accident.
            patch("retrieval.hybrid_search._mps_risk_present", return_value=False),
            patch("retrieval.hybrid_search.audit_log") as audit,
            pytest.raises(IndexNotFoundError, match="fingerprint mismatch"),
        ):
            HybridRetriever(str(config_path))
        event = audit.call_args.args[0]
        assert event["event"] == "embedding_fingerprint_mismatch"
        assert event["index"] == stale
        assert event["expected"]["device"] == "cpu"

    def test_fingerprint_check_failure_never_blocks_construction(self, tmp_path):
        config_path = self._build_repo(tmp_path)

        def _boom():
            raise RuntimeError("reader is misbehaving")

        reader = SimpleNamespace(close=lambda: None, fingerprint=_boom)
        with patch("retrieval.hybrid_search.get_vector_reader", return_value=reader):
            # Must not raise -- the fingerprint check is observability only.
            HybridRetriever(str(config_path))


class TestMpsRiskPresent:
    """_mps_risk_present() itself -- the signal that scopes the absent-
    fingerprint guard to Apple Silicon only (see TestEmbeddingFingerprintCheck)."""

    def test_true_when_mps_available(self):
        torch = pytest.importorskip("torch")

        with patch.object(torch.backends.mps, "is_available", return_value=True):
            assert _mps_risk_present() is True

    def test_false_when_mps_unavailable(self):
        torch = pytest.importorskip("torch")

        with patch.object(torch.backends.mps, "is_available", return_value=False):
            assert _mps_risk_present() is False

    def test_false_on_probe_failure(self):
        torch = pytest.importorskip("torch")

        def _boom():
            raise RuntimeError("backend probe exploded")

        with patch.object(torch.backends.mps, "is_available", side_effect=_boom):
            assert _mps_risk_present() is False

    def test_false_when_torch_not_installed(self):
        # The docstring on _mps_risk_present names "torch not installed" as a
        # supported condition that must resolve to False, but the three tests
        # above all require torch to be importable -- so the one branch the
        # function documents as its safety property had no coverage. A None
        # entry in sys.modules makes `import torch` raise ImportError, which
        # reproduces the absent-torch case on a machine that HAS torch.
        with patch.dict("sys.modules", {"torch": None}):
            assert _mps_risk_present() is False


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
