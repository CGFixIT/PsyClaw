"""Unit tests for retrieval.vector_store helpers, Chroma writer/reader, and
pgvector factory / DSN error paths (no live Postgres required).

Uses a real ChromaDB PersistentClient in a tmp_path (no mocking of chromadb
itself) so these tests exercise the actual exception types the library raises,
not an assumed shape.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from utils.errors import IndexNotFoundError


def _cfg(chroma_path, collection_name="cyclaw_kb"):
    return {"indexing": {"chroma_path": str(chroma_path), "collection_name": collection_name}}


def _pg_cfg(dsn: str = "postgresql://u:p@127.0.0.1:1/db", dim: int = 384) -> dict:
    return {
        "models": {"embeddings": {"dim": dim}},
        "indexing": {"vector_backend": "pgvector", "database_url": dsn},
    }


class TestBackendHelpers:
    def test_vector_backend_defaults_to_chroma(self):
        from retrieval.vector_store import vector_backend

        assert vector_backend({}) == "chroma"
        assert vector_backend({"indexing": {}}) == "chroma"
        assert vector_backend({"indexing": {"vector_backend": None}}) == "chroma"

    def test_vector_backend_pgvector_is_lowercased(self):
        from retrieval.vector_store import vector_backend

        assert vector_backend({"indexing": {"vector_backend": "PgVector"}}) == "pgvector"

    def test_pg_dsn_prefers_dedicated_env_then_cfg_then_shared_env(self, monkeypatch):
        from retrieval.vector_store import _pg_dsn

        monkeypatch.delenv("CYCLAW_VECTOR_DB_URL", raising=False)
        monkeypatch.delenv("CYCLAW_DB_URL", raising=False)
        assert _pg_dsn({}) == ""
        assert _pg_dsn({"indexing": {"database_url": "postgresql://cfg/db"}}) == "postgresql://cfg/db"

        monkeypatch.setenv("CYCLAW_DB_URL", "postgresql://shared/db")
        assert _pg_dsn({}) == "postgresql://shared/db"
        assert _pg_dsn({"indexing": {"database_url": "postgresql://cfg/db"}}) == "postgresql://cfg/db"

        monkeypatch.setenv("CYCLAW_VECTOR_DB_URL", "postgresql://dedicated/db")
        assert _pg_dsn({"indexing": {"database_url": "postgresql://cfg/db"}}) == "postgresql://dedicated/db"

    def test_embed_dim_reads_models_section_or_default(self):
        from retrieval.vector_store import _DEFAULT_EMBED_DIM, _embed_dim

        assert _embed_dim({}) == _DEFAULT_EMBED_DIM
        assert _embed_dim({"models": {}}) == _DEFAULT_EMBED_DIM
        assert _embed_dim({"models": {"embeddings": {"dim": 768}}}) == 768

    def test_as_list_accepts_plain_list_and_tolist(self):
        from retrieval.vector_store import _as_list

        assert _as_list([1.0, 2.5]) == [1.0, 2.5]

        class _Arr:
            def tolist(self):
                return [3.0, 4.0]

        assert _as_list(_Arr()) == [3.0, 4.0]


class TestChromaWriterReader:
    def test_missing_chroma_path_raises_index_not_found(self, tmp_path):
        from retrieval.vector_store import get_vector_reader

        with pytest.raises(IndexNotFoundError, match="ChromaDB index not found"):
            get_vector_reader(_cfg(tmp_path / "does-not-exist"))

    def test_writer_reset_add_finalize_and_reader_roundtrip(self, tmp_path):
        from retrieval.vector_store import get_vector_reader, get_vector_writer

        cfg = _cfg(tmp_path / "chroma")
        writer = get_vector_writer(cfg)
        try:
            writer.reset(fingerprint={"model": "all-MiniLM-L6-v2", "dim": "3", "device": "cpu"})
            writer.add(
                ["chunk_0"],
                ["hello world"],
                [[1.0, 0.0, 0.0]],
                [{
                    "source": "a.md",
                    "chunk_id": 0,
                    "source_sha256": "ab" * 32,
                    "stem_tags": '["tag"]',
                }],
            )
            writer.finalize()
        finally:
            writer.close()

        reader = get_vector_reader(cfg)
        try:
            assert reader.fingerprint() == {
                "model": "all-MiniLM-L6-v2",
                "dim": "3",
                "device": "cpu",
            }
            hits = reader.query([1.0, 0.0, 0.0], k=1)
        finally:
            reader.close()

        assert len(hits) == 1
        assert hits[0]["text"] == "hello world"
        assert hits[0]["source"] == "a.md"
        assert hits[0]["chunk_id"] == 0
        assert hits[0]["stem_tags"] == ["tag"]

    def test_fingerprint_none_when_collection_has_no_stamp(self, tmp_path):
        from retrieval.vector_store import get_vector_reader, get_vector_writer

        cfg = _cfg(tmp_path / "chroma")
        writer = get_vector_writer(cfg)
        try:
            # reset() with no fingerprint → only hnsw:space in collection metadata.
            writer.reset()
            writer.add(
                ["chunk_0"],
                ["unstamped"],
                [[0.0, 1.0, 0.0]],
                [{"source": "b.md", "chunk_id": 1, "stem_tags": "[]"}],
            )
            writer.finalize()
        finally:
            writer.close()

        reader = get_vector_reader(cfg)
        try:
            assert reader.fingerprint() is None
        finally:
            reader.close()


class TestChromaReaderMissingCollection:
    def test_missing_collection_raises_index_not_found(self, tmp_path):
        import chromadb
        from chromadb.config import Settings

        from retrieval.vector_store import get_vector_reader

        chroma_path = tmp_path / "chroma"
        # A real PersistentClient must have touched the path (chromadb creates
        # its sqlite file lazily) so _ChromaReader's Path(...).exists() check
        # passes and execution reaches the get_collection() call this test
        # targets, rather than failing earlier on a missing directory.
        chromadb.PersistentClient(path=str(chroma_path), settings=Settings(anonymized_telemetry=False))

        with pytest.raises(IndexNotFoundError, match="not found in ChromaDB"):
            get_vector_reader(_cfg(chroma_path))

    def test_unrelated_construction_error_is_not_reported_as_index_not_found(self, tmp_path, monkeypatch):
        """Narrowing the except clause to chromadb.errors.NotFoundError must not
        mask a genuinely different failure (e.g. a corrupted client) as though
        it were an ordinary missing-collection case."""
        import chromadb
        from chromadb.config import Settings

        from retrieval.vector_store import get_vector_reader

        chroma_path = tmp_path / "chroma"
        chromadb.PersistentClient(path=str(chroma_path), settings=Settings(anonymized_telemetry=False))

        def _boom(self, name):
            raise RuntimeError("simulated corrupt client state")

        # chromadb.PersistentClient is a factory function, not a class -- patch
        # the actual client class it returns.
        import chromadb.api.client

        monkeypatch.setattr(chromadb.api.client.Client, "get_collection", _boom)

        with pytest.raises(RuntimeError, match="simulated corrupt client state"):
            get_vector_reader(_cfg(chroma_path))


class TestPgvectorFactoryOffline:
    """pgvector choose-backend / DSN / connect-raise paths without a live DB."""

    @pytest.fixture
    def _pg_driver(self):
        pytest.importorskip("psycopg")
        pytest.importorskip("pgvector")

    def test_writer_and_reader_require_dsn(self, monkeypatch):
        from retrieval.vector_store import get_vector_reader, get_vector_writer

        monkeypatch.delenv("CYCLAW_VECTOR_DB_URL", raising=False)
        monkeypatch.delenv("CYCLAW_DB_URL", raising=False)
        cfg = {"indexing": {"vector_backend": "pgvector"}}
        with pytest.raises(IndexNotFoundError, match="no DSN"):
            get_vector_writer(cfg)
        with pytest.raises(IndexNotFoundError, match="no DSN"):
            get_vector_reader(cfg)

    def test_writer_close_is_noop_before_connect(self, monkeypatch):
        from retrieval.vector_store import get_vector_writer

        monkeypatch.delenv("CYCLAW_VECTOR_DB_URL", raising=False)
        monkeypatch.delenv("CYCLAW_DB_URL", raising=False)
        writer = get_vector_writer(_pg_cfg())
        writer.close()  # _conn is still None

    def test_connection_propagates_connect_error(self, monkeypatch, _pg_driver):
        import psycopg

        from retrieval.vector_store import get_vector_writer

        monkeypatch.delenv("CYCLAW_VECTOR_DB_URL", raising=False)
        monkeypatch.delenv("CYCLAW_DB_URL", raising=False)
        writer = get_vector_writer(_pg_cfg())
        try:
            with patch("psycopg.connect", side_effect=psycopg.OperationalError("refused")):
                with pytest.raises(psycopg.OperationalError, match="refused"):
                    writer.reset()
        finally:
            writer.close()

    def test_writer_reset_add_finalize_against_fake_connection(self, monkeypatch, _pg_driver):
        """Exercise real writer SQL builders without a live Postgres.

        Only ``psycopg.connect`` / ``register_vector`` are stubbed (driver
        boundary); reset/add/finalize/close run the shipped methods.
        """
        from retrieval.vector_store import get_vector_writer

        monkeypatch.delenv("CYCLAW_VECTOR_DB_URL", raising=False)
        monkeypatch.delenv("CYCLAW_DB_URL", raising=False)

        mock_conn = MagicMock(name="pg_conn")
        mock_cur = MagicMock(name="pg_cur")
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_conn.cursor.return_value.__exit__.return_value = False

        writer = get_vector_writer(_pg_cfg(dim=3))
        try:
            with (
                patch("psycopg.connect", return_value=mock_conn),
                patch("pgvector.psycopg.register_vector") as reg,
            ):
                writer.reset()
                writer.add(
                    ["id0", "id1"],
                    ["doc0", "doc1"],
                    [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
                    [
                        {"source": "a.md", "chunk_id": 0, "source_sha256": "aa", "stem_tags": '["a"]'},
                        {"source": "b.md", "chunk_id": 1, "stem_tags": ["b"]},  # non-str → json.dumps
                    ],
                )
                writer.finalize()
                reg.assert_called_once_with(mock_conn)
        finally:
            writer.close()

        assert mock_conn.execute.call_count >= 5  # reset DDL + finalize HNSW
        mock_cur.executemany.assert_called_once()
        rows = mock_cur.executemany.call_args.args[1]
        assert rows[0][0] == "a.md" and rows[0][4] == '["a"]'
        assert rows[1][4] == '["b"]'  # list stem_tags coerced to JSON string
        mock_conn.close.assert_called_once()

    def test_reader_missing_table_raises_and_closes(self, monkeypatch, _pg_driver):
        from retrieval.vector_store import get_vector_reader

        monkeypatch.delenv("CYCLAW_VECTOR_DB_URL", raising=False)
        monkeypatch.delenv("CYCLAW_DB_URL", raising=False)

        mock_conn = MagicMock(name="pg_conn")
        mock_conn.execute.return_value.fetchone.return_value = [None]

        with (
            patch("psycopg.connect", return_value=mock_conn),
            patch("pgvector.psycopg.register_vector"),
            pytest.raises(IndexNotFoundError, match="kb_chunks"),
        ):
            get_vector_reader(_pg_cfg())

        mock_conn.close.assert_called_once()

    def test_reader_query_maps_rows(self, monkeypatch, _pg_driver):
        from retrieval.vector_store import get_vector_reader

        monkeypatch.delenv("CYCLAW_VECTOR_DB_URL", raising=False)
        monkeypatch.delenv("CYCLAW_DB_URL", raising=False)

        mock_conn = MagicMock(name="pg_conn")

        def _execute(sql, params=None):
            result = MagicMock()
            if "to_regclass" in sql:
                result.fetchone.return_value = ["kb_chunks"]
            elif "information_schema.columns" in sql:
                result.fetchone.return_value = [True]
            else:
                result.fetchall.return_value = [
                    ("hit text", "src.md", 2, "deadbeef", '["x"]', 0.91),
                ]
            return result

        mock_conn.execute.side_effect = _execute

        with (
            patch("psycopg.connect", return_value=mock_conn),
            patch("pgvector.psycopg.register_vector"),
        ):
            reader = get_vector_reader(_pg_cfg(dim=3))
            try:
                hits = reader.query([1.0, 0.0, 0.0], k=1)
            finally:
                reader.close()

        assert hits == [{
            "text": "hit text",
            "score": 0.91,
            "source": "src.md",
            "chunk_id": 2,
            "source_sha256": "deadbeef",
            "stem_tags": ["x"],
        }]

    def test_reader_query_without_source_sha256_column(self, monkeypatch, _pg_driver):
        from retrieval.vector_store import get_vector_reader

        monkeypatch.delenv("CYCLAW_VECTOR_DB_URL", raising=False)
        monkeypatch.delenv("CYCLAW_DB_URL", raising=False)

        mock_conn = MagicMock(name="pg_conn")
        seen_sql: list[str] = []

        def _execute(sql, params=None):
            seen_sql.append(sql)
            result = MagicMock()
            if "to_regclass" in sql:
                result.fetchone.return_value = ["kb_chunks"]
            elif "information_schema.columns" in sql:
                result.fetchone.return_value = [False]
            else:
                result.fetchall.return_value = [
                    ("legacy", "old.md", 0, "", "[]", 0.5),
                ]
            return result

        mock_conn.execute.side_effect = _execute

        with (
            patch("psycopg.connect", return_value=mock_conn),
            patch("pgvector.psycopg.register_vector"),
        ):
            reader = get_vector_reader(_pg_cfg(dim=3))
            try:
                hits = reader.query([0.0, 1.0, 0.0], k=1)
            finally:
                reader.close()

        assert any("'' AS source_sha256" in sql for sql in seen_sql)
        assert hits[0]["source_sha256"] == ""
        assert hits[0]["text"] == "legacy"
