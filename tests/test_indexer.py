"""Unit tests for retrieval.indexer chunking and config validation.

Covers chunk_document edge cases and the build_index fail-fast guards that
reject chunking misconfiguration before a corrupt index can be written.
"""

import hashlib
import json
from unittest.mock import MagicMock, patch

import pytest
import yaml

from retrieval import indexer
from retrieval.indexer import chunk_document, build_index, load_corpus


class TestChunkDocument:
    def test_empty_text_returns_no_chunks(self):
        assert chunk_document("") == []
        assert chunk_document("   ") == []

    def test_single_window_when_text_fits(self):
        text = "alpha beta gamma"
        chunks = chunk_document(text, chunk_size=512, overlap=50)
        assert chunks == ["alpha beta gamma"]

    def test_normal_chunk_count_and_overlap(self):
        # 10 words, size=4, overlap=2 -> stride 2 -> starts at 0,2,4,6,8 = 5 chunks.
        words = [f"w{i}" for i in range(10)]
        chunks = chunk_document(" ".join(words), chunk_size=4, overlap=2)
        assert len(chunks) == 5
        assert chunks[0] == "w0 w1 w2 w3"
        # Overlap: each chunk shares its first two words with the previous tail.
        assert chunks[1] == "w2 w3 w4 w5"
        assert chunks[-1] == "w8 w9"

    def test_no_overlap_partitions_exactly(self):
        words = [f"w{i}" for i in range(6)]
        chunks = chunk_document(" ".join(words), chunk_size=3, overlap=0)
        assert chunks == ["w0 w1 w2", "w3 w4 w5"]

    def test_rejects_overlap_ge_chunk_size_when_called_directly(self):
        # Direct callers (not just build_index) must fail loudly rather than
        # silently degrade to a one-word stride and explode the corpus.
        with pytest.raises(ValueError, match="overlap .* must be < chunk_size"):
            chunk_document("a b c d", chunk_size=4, overlap=4)
        with pytest.raises(ValueError, match="overlap .* must be < chunk_size"):
            chunk_document("a b c d", chunk_size=4, overlap=10)

    def test_rejects_chunk_size_below_one(self):
        with pytest.raises(ValueError, match="chunk_size must be >= 1"):
            chunk_document("a b c", chunk_size=0, overlap=0)

    def test_rejects_negative_overlap_when_called_directly(self):
        # step = chunk_size - overlap > chunk_size, so windows skip words silently.
        with pytest.raises(ValueError, match="overlap must be >= 0"):
            chunk_document("a b c d e", chunk_size=3, overlap=-2)


class TestBuildIndexValidation:
    def _write_config(self, tmp_path, chunk_size, chunk_overlap, batch_size=10):
        cfg = {
            "corpus": {"path": str(tmp_path / "corpus"), "extensions": [".md"]},
            "indexing": {
                "chroma_path": str(tmp_path / "chroma"),
                "bm25_path": str(tmp_path / "bm25.json"),
                "collection_name": "test_kb",
                "chunk_size": chunk_size,
                "chunk_overlap": chunk_overlap,
                "batch_size": batch_size,
            },
        }
        config_file = tmp_path / "config.yaml"
        with open(config_file, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f)
        return str(config_file)

    def test_rejects_zero_chunk_size(self, tmp_path):
        config_path = self._write_config(tmp_path, chunk_size=0, chunk_overlap=0)
        with pytest.raises(ValueError, match="chunk_size must be >= 1"):
            build_index(config_path)

    def test_rejects_overlap_equal_to_chunk_size(self, tmp_path):
        config_path = self._write_config(tmp_path, chunk_size=512, chunk_overlap=512)
        with pytest.raises(ValueError, match="chunk_overlap .* must be < chunk_size"):
            build_index(config_path)

    def test_rejects_overlap_greater_than_chunk_size(self, tmp_path):
        config_path = self._write_config(tmp_path, chunk_size=100, chunk_overlap=200)
        with pytest.raises(ValueError, match="chunk_overlap .* must be < chunk_size"):
            build_index(config_path)

    def test_rejects_negative_chunk_overlap(self, tmp_path):
        # A sign-typo'd overlap silently drops words from both indices — fail fast.
        config_path = self._write_config(tmp_path, chunk_size=512, chunk_overlap=-50)
        with pytest.raises(ValueError, match="chunk_overlap must be >= 0"):
            build_index(config_path)

    def test_rejects_non_positive_batch_size(self, tmp_path):
        # A negative batch_size yields an empty semantic index (BM25-only fallback,
        # every query fails the confidence gate); 0 crashes range() mid-build.
        config_path = self._write_config(tmp_path, chunk_size=512, chunk_overlap=50, batch_size=-1)
        with pytest.raises(ValueError, match="batch_size must be >= 1"):
            build_index(config_path)


class TestBuildIndexConfigPropagation:
    """build_index must build the semantic index with the embedding model from
    the SAME config it was called with — not the default config.yaml.

    Query-time embeddings already honour config_path
    (HybridRetriever.semantic_search -> get_embedding(query, self.config_path)).
    If build_index embeds the corpus with a different model/dimension, the index
    and the query vectors disagree and semantic retrieval silently breaks. This
    test pins the contract that the config_path reaches get_embeddings_batch.
    """

    def test_config_path_reaches_embeddings(self, tmp_path):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "a.md").write_text("hello world cyclaw retrieval fusion", encoding="utf-8")
        cfg = {
            "corpus": {"path": str(corpus), "extensions": [".md"]},
            "indexing": {
                "chroma_path": str(tmp_path / "chroma"),
                "bm25_path": str(tmp_path / "bm25.json"),
                "collection_name": "test_kb",
                "chunk_size": 512,
                "chunk_overlap": 50,
                "batch_size": 10,
            },
        }
        config_path = tmp_path / "config.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f)

        fake_embeddings = MagicMock(return_value=[[0.1, 0.2, 0.3]])
        # Stub the vector writer so build_index runs without a real ChromaDB/pgvector
        # store; the assertion below verifies config_path forwarding to embeddings.
        with (
            patch("retrieval.indexer.get_embeddings_batch", fake_embeddings),
            patch("retrieval.indexer.get_vector_writer") as mock_get_writer,
        ):
            mock_get_writer.return_value = MagicMock()
            build_index(str(config_path))

        assert fake_embeddings.called, "get_embeddings_batch was never called"
        args, kwargs = fake_embeddings.call_args
        passed_config = args[1] if len(args) > 1 else kwargs.get("config_path")
        assert passed_config == str(config_path), (
            f"build_index did not forward its config_path to get_embeddings_batch; got {passed_config!r}"
        )

    def test_build_index_stores_source_sha256_metadata(self, tmp_path):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        content = "hello world cyclaw retrieval fusion"
        (corpus / "a.md").write_text(content, encoding="utf-8")
        cfg = {
            "corpus": {"path": str(corpus), "extensions": [".md"]},
            "indexing": {
                "chroma_path": str(tmp_path / "chroma"),
                "bm25_path": str(tmp_path / "bm25.json"),
                "collection_name": "test_kb",
                "chunk_size": 512,
                "chunk_overlap": 50,
                "batch_size": 10,
            },
        }
        config_path = tmp_path / "config.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f)

        fake_writer = MagicMock()
        with (
            patch("retrieval.indexer.get_embeddings_batch", return_value=[[0.1, 0.2, 0.3]]),
            patch("retrieval.indexer.get_vector_writer", return_value=fake_writer),
        ):
            build_index(str(config_path))

        expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
        added_metadata = fake_writer.add.call_args.args[3]
        assert added_metadata[0]["source_sha256"] == expected

        bm25_data = json.loads((tmp_path / "bm25.json").read_text(encoding="utf-8"))
        assert bm25_data["metadata"][0]["source_sha256"] == expected

    def test_default_config_anchors_relative_paths_to_repo_root(self, tmp_path, monkeypatch):
        repo_root = tmp_path / "repo"
        launch_dir = tmp_path / "elsewhere"
        corpus = repo_root / "data" / "corpus"
        corpus.mkdir(parents=True)
        launch_dir.mkdir()
        (corpus / "a.md").write_text("hello world cyclaw retrieval fusion", encoding="utf-8")
        cfg = {
            "corpus": {"path": "data/corpus", "extensions": [".md"]},
            "indexing": {
                "chroma_path": "data/chroma",
                "bm25_path": "data/index/bm25.json",
                "collection_name": "test_kb",
                "chunk_size": 512,
                "chunk_overlap": 50,
                "batch_size": 10,
            },
        }
        (repo_root / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
        monkeypatch.setattr(indexer, "_REPO_ROOT", repo_root)
        monkeypatch.chdir(launch_dir)

        fake_writer = MagicMock()
        with (
            patch("retrieval.indexer.get_embeddings_batch", return_value=[[0.1, 0.2, 0.3]]) as embeddings,
            patch("retrieval.indexer.get_vector_writer", return_value=fake_writer) as get_writer,
        ):
            build_index()

        resolved_config = str((repo_root / "config.yaml").resolve())
        assert embeddings.call_args.args[1] == resolved_config
        writer_cfg = get_writer.call_args.args[0]
        assert writer_cfg["indexing"]["chroma_path"] == str((repo_root / "data" / "chroma").resolve())
        assert (repo_root / "data" / "index" / "bm25.json").exists()
        assert not (launch_dir / "data" / "index" / "bm25.json").exists()


class TestLoadCorpusCaseInsensitive:
    """load_corpus must match file extensions case-insensitively.

    On Linux/CI rglob(f"*{ext}") was case-sensitive; files with .MD or .TXT
    would be silently skipped if config specified [".md", ".txt"].
    This test validates case-insensitive extension matching on all platforms.
    """

    def test_config_uppercase_matches_lowercase_files(self, tmp_path):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        # Create files with lowercase extensions
        (corpus / "doc.md").write_text("content", encoding="utf-8")
        (corpus / "notes.txt").write_text("content", encoding="utf-8")
        # Config specifies UPPERCASE extensions (tests reverse case matching)
        docs = load_corpus(str(corpus), extensions=[".MD", ".TXT"])
        # Both files should be loaded despite config using uppercase extensions
        assert len(docs) == 2
        sources = {source for source, _ in docs}
        assert any("doc.md" in s for s in sources)
        assert any("notes.txt" in s for s in sources)

    def test_config_lowercase_matches_uppercase_files(self, tmp_path):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        # Create files with uppercase extensions (separate base names to avoid
        # Windows case-insensitivity collisions on NTFS)
        (corpus / "first.MD").write_text("content", encoding="utf-8")
        (corpus / "second.TXT").write_text("content", encoding="utf-8")
        # Config specifies lowercase extensions
        docs = load_corpus(str(corpus), extensions=[".md", ".txt"])
        # Both files should be loaded (extension match is case-insensitive)
        assert len(docs) == 2
        sources = {source for source, _ in docs}
        assert any("first.MD" in s for s in sources)
        assert any("second.TXT" in s for s in sources)

    def test_unmatched_extensions_still_skipped(self, tmp_path):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "doc.md").write_text("match", encoding="utf-8")
        (corpus / "other.json").write_text("skip", encoding="utf-8")
        docs = load_corpus(str(corpus), extensions=[".md"])
        # Only .md should be loaded; .json should be skipped
        assert len(docs) == 1
        assert "doc.md" in docs[0][0]


class TestBuildIndexObservability:
    """Silent-drop and silent-degradation paths must leave a build-log trace."""

    def _cfg(self, tmp_path, corpus):
        cfg = {
            "corpus": {"path": str(corpus), "extensions": [".md"]},
            "indexing": {
                "chroma_path": str(tmp_path / "chroma"),
                "bm25_path": str(tmp_path / "bm25.json"),
                "collection_name": "test_kb",
                "chunk_size": 512,
                "chunk_overlap": 50,
                "batch_size": 10,
            },
        }
        config_path = tmp_path / "config.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f)
        return str(config_path)

    def test_zero_chunk_document_is_skipped_with_warning(self, tmp_path, caplog):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "real.md").write_text("hello world cyclaw retrieval fusion", encoding="utf-8")
        (corpus / "empty.md").write_text("   \n\n  ", encoding="utf-8")
        config_path = self._cfg(tmp_path, corpus)

        fake_embeddings = MagicMock(return_value=[[0.1, 0.2, 0.3]])
        with (
            patch("retrieval.indexer.get_embeddings_batch", fake_embeddings),
            patch("retrieval.indexer.get_vector_writer") as mock_get_writer,
            caplog.at_level("WARNING", logger="retrieval.indexer"),
        ):
            mock_get_writer.return_value = MagicMock()
            build_index(config_path)

        assert any(
            "empty.md" in rec.getMessage() and "0 chunks" in rec.getMessage()
            for rec in caplog.records
        ), f"no zero-chunk warning for empty.md in: {[r.getMessage() for r in caplog.records]}"
        # Only the real document's single chunk reaches the embedding batch.
        embedded_texts = fake_embeddings.call_args.args[0]
        assert len(embedded_texts) == 1

    def test_non_ascii_document_warns_about_bm25_blindness(self, tmp_path, caplog):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        # Entirely non-Latin content: chunks exist (words split fine) but the
        # ASCII-only tokenizer yields zero BM25 tokens for every chunk.
        (corpus / "cyrillic.md").write_text("Привет мир это тест поиска", encoding="utf-8")
        config_path = self._cfg(tmp_path, corpus)

        fake_embeddings = MagicMock(return_value=[[0.1, 0.2, 0.3]])
        with (
            patch("retrieval.indexer.get_embeddings_batch", fake_embeddings),
            patch("retrieval.indexer.get_vector_writer") as mock_get_writer,
            caplog.at_level("WARNING", logger="retrieval.indexer"),
        ):
            mock_get_writer.return_value = MagicMock()
            build_index(config_path)

        assert any("no BM25 tokens" in rec.getMessage() for rec in caplog.records), (
            f"no BM25-blindness warning in: {[r.getMessage() for r in caplog.records]}"
        )
        # The document is still embedded (semantic leg keeps covering it).
        embedded_texts = fake_embeddings.call_args.args[0]
        assert len(embedded_texts) == 1
