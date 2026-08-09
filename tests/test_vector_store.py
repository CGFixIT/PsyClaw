"""Unit tests for retrieval.vector_store's ChromaDB reader (_ChromaReader).

Uses a real ChromaDB PersistentClient in a tmp_path (no mocking of chromadb
itself) so these tests exercise the actual exception types the library raises,
not an assumed shape.
"""

from __future__ import annotations

import pytest

from utils.errors import IndexNotFoundError


def _cfg(chroma_path, collection_name="cyclaw_kb"):
    return {"indexing": {"chroma_path": str(chroma_path), "collection_name": collection_name}}


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
