"""Pluggable vector-store backends for CyClaw semantic retrieval.

Default backend is **ChromaDB** (``PersistentClient``, embedded, zero-config,
offline-first) — unchanged behavior. Set ``indexing.vector_backend: "pgvector"``
(plus a Postgres DSN via ``indexing.database_url`` / ``CYCLAW_VECTOR_DB_URL`` /
``CYCLAW_DB_URL``) to store and query the 384-dim embeddings in PostgreSQL via the
``pgvector`` extension instead.

This module isolates the *only* part of retrieval that differs between backends:
the semantic add (indexer) and the semantic query (hybrid_search). The RRF fusion
and the BM25 keyword leg in ``hybrid_search.py`` are backend-agnostic and untouched
— a pgvector deployment fuses and ranks identically to a ChromaDB one.

Trade-off: ChromaDB is a local file needing no server (keeps CyClaw offline-first);
pgvector requires a running Postgres. Only choose pgvector as a deliberate
"consolidate on Postgres" move. ``psycopg`` / ``pgvector`` are lazy-imported, so a
default (ChromaDB) install never loads them.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from utils.errors import IndexNotFoundError
from utils.telemetry_kill import apply_telemetry_kill

# Applied at import, not at client construction, because this module is the ONLY
# production path that reaches ChromaDB -- both `import chromadb` statements below
# are lazy (inside _ChromaWriter.reset and _ChromaReader.__init__), so a module-level
# apply is guaranteed to run first. That covers every entry point in one place:
# gate.py (which also applies it directly), mcp_hybrid_server.py, and
# `python -m retrieval.indexer`, which reaches ChromaDB only through here.
#
# ChromaDB reads its OTel settings from the process environment via
# pydantic-settings, so an ambient CHROMA_OTEL_GRANULARITY would otherwise be
# honored on the two paths that never import gate. Settings(anonymized_telemetry=
# False) at the call sites below does not cover this -- that flag governs the
# separate PostHog product-telemetry path.
apply_telemetry_kill()

logger = logging.getLogger(__name__)


def parse_stem_tags(raw: object) -> list[str]:
    """Safely parse stem_tags from index metadata.

    Metadata may store stem_tags as a JSON string (indexer default), a list
    (pgvector JSONB columns), or a corrupted/truncated value after a partial
    write. A bare ``json.loads`` would crash the entire retrieval path on
    malformed data; returning ``[]`` lets the query complete with degraded
    stem metadata rather than an HTTP 500.
    """
    if isinstance(raw, list):
        return list(raw)
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        logger.warning("Malformed stem_tags metadata, falling back to []: %r", raw)
        return []


# all-MiniLM-L6-v2 embedding width (config: models.embeddings.dim). Fixed table name
# keeps every SQL string a literal — no identifier interpolation, no injection seam.
_DEFAULT_EMBED_DIM = 384
_PG_TABLE = "kb_chunks"


def vector_backend(cfg: dict) -> str:
    """Return the configured vector backend ("chroma" default, or "pgvector")."""
    return ((cfg.get("indexing") or {}).get("vector_backend") or "chroma").lower()


def _pg_dsn(cfg: dict) -> str:
    """Resolve the pgvector DSN: dedicated env → indexing.database_url → CYCLAW_DB_URL."""
    return (
        os.environ.get("CYCLAW_VECTOR_DB_URL")
        or (cfg.get("indexing") or {}).get("database_url")
        or os.environ.get("CYCLAW_DB_URL")
        or ""
    )


def _embed_dim(cfg: dict) -> int:
    return int(((cfg.get("models") or {}).get("embeddings") or {}).get("dim") or _DEFAULT_EMBED_DIM)


# ============================================================ ChromaDB (default)
class _ChromaWriter:
    """Wraps the ChromaDB build path (PersistentClient + collection.add)."""

    def __init__(self, cfg: dict):
        self._chroma_path = cfg["indexing"]["chroma_path"]
        self._collection_name = cfg["indexing"]["collection_name"]
        self._collection = None

    def reset(self) -> None:
        import chromadb
        from chromadb.config import Settings

        Path(self._chroma_path).mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(
            path=self._chroma_path, settings=Settings(anonymized_telemetry=False)
        )
        try:
            client.delete_collection(self._collection_name)
        except Exception:  # noqa: S110  # nosec B110 — delete-if-exists; may not exist yet
            pass
        # Cosine space: embeddings are L2-normalized, so `1 - distance` is genuine
        # cosine similarity (matches hybrid_search's score). See indexer comment.
        self._collection = client.create_collection(
            self._collection_name, metadata={"hnsw:space": "cosine"}
        )

    def add(self, ids: list[str], documents: list[str], embeddings: Any, metadatas: list[dict]) -> None:
        self._collection.add(documents=documents, embeddings=embeddings, metadatas=metadatas, ids=ids)

    def finalize(self) -> None:
        pass

    def close(self) -> None:
        pass


class _ChromaReader:
    """Wraps the ChromaDB query path; returns normalized semantic hits."""

    def __init__(self, cfg: dict):
        import chromadb
        from chromadb.config import Settings

        chroma_path = cfg["indexing"]["chroma_path"]
        collection_name = cfg["indexing"]["collection_name"]
        if not Path(chroma_path).exists():
            raise IndexNotFoundError(
                f"ChromaDB index not found at {chroma_path}. Run: python -m retrieval.indexer"
            )
        client = chromadb.PersistentClient(
            path=chroma_path, settings=Settings(anonymized_telemetry=False)
        )
        try:
            self._collection = client.get_collection(collection_name)
        except Exception as e:
            raise IndexNotFoundError(
                f"Collection '{collection_name}' not found in ChromaDB: {e}"
            ) from e

    def query(self, embedding: Any, k: int) -> list[dict]:
        results = self._collection.query(query_embeddings=[embedding], n_results=k)
        out: list[dict] = []
        if results["documents"] and results["documents"][0]:
            for i, doc in enumerate(results["documents"][0]):
                # cosine space: distance = 1 - cos, so score = 1 - distance = cos.
                score = 1 - results["distances"][0][i]
                meta = results["metadatas"][0][i]
                out.append({
                    "text": doc, "score": score, "source": meta["source"],
                    "chunk_id": meta["chunk_id"],
                    "source_sha256": meta.get("source_sha256", ""),
                    "stem_tags": parse_stem_tags(meta.get("stem_tags", "[]")),
                })
        return out

    def close(self) -> None:
        pass


# ================================================================ pgvector (opt-in)
class _PgVectorBase:
    def __init__(self, cfg: dict):
        self._dsn = _pg_dsn(cfg)
        if not self._dsn:
            raise IndexNotFoundError(
                "vector_backend=pgvector but no DSN — set indexing.database_url, "
                "CYCLAW_VECTOR_DB_URL, or CYCLAW_DB_URL."
            )
        self._dim = _embed_dim(cfg)
        self._conn = None

    def _connection(self):
        if self._conn is None:
            import psycopg  # noqa: PLC0415 -- lazy: ChromaDB installs need no driver
            from pgvector.psycopg import register_vector  # noqa: PLC0415

            from utils.personality_db import _harden_pg_conninfo

            self._conn = psycopg.connect(_harden_pg_conninfo(self._dsn), autocommit=True)
            self._conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            register_vector(self._conn)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None


class _PgVectorWriter(_PgVectorBase):
    """Stores embeddings in a pgvector ``kb_chunks`` table with an HNSW index."""

    def reset(self) -> None:
        conn = self._connection()
        # Table name and dimension are code constants (never user input). Build the
        # HNSW index AFTER the bulk load (finalize) — far faster than maintaining it
        # row-by-row during insert.
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_PG_TABLE} ("
            "  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,"
            "  source TEXT NOT NULL,"
            "  chunk_id INT NOT NULL,"
            "  source_sha256 TEXT NOT NULL DEFAULT '',"
            "  content TEXT NOT NULL,"
            "  stem_tags TEXT NOT NULL DEFAULT '[]',"
            f"  embedding vector({self._dim}) NOT NULL"
            ")"
        )
        conn.execute(f"CREATE INDEX IF NOT EXISTS {_PG_TABLE}_src ON {_PG_TABLE} (source, chunk_id)")
        conn.execute(f"ALTER TABLE {_PG_TABLE} ADD COLUMN IF NOT EXISTS source_sha256 TEXT NOT NULL DEFAULT ''")
        conn.execute(f"DROP INDEX IF EXISTS {_PG_TABLE}_hnsw")
        conn.execute(f"TRUNCATE {_PG_TABLE}")

    def add(self, ids: list[str], documents: list[str], embeddings: Any, metadatas: list[dict]) -> None:
        conn = self._connection()
        rows = []
        for doc, emb, meta in zip(documents, embeddings, metadatas, strict=True):
            stem = meta.get("stem_tags", "[]")
            if not isinstance(stem, str):
                stem = json.dumps(stem)
            rows.append((
                meta["source"],
                int(meta["chunk_id"]),
                meta.get("source_sha256", ""),
                doc,
                stem,
                _as_list(emb),
            ))
        with conn.cursor() as cur:
            cur.executemany(
                f"INSERT INTO {_PG_TABLE} (source, chunk_id, source_sha256, content, stem_tags, embedding) "  # noqa: S608
                "VALUES (%s, %s, %s, %s, %s, %s)",
                rows,
            )

    def finalize(self) -> None:
        # Cosine ops mirror Chroma's cosine space; built once over the full set.
        self._connection().execute(
            f"CREATE INDEX IF NOT EXISTS {_PG_TABLE}_hnsw "
            f"ON {_PG_TABLE} USING hnsw (embedding vector_cosine_ops)"
        )


class _PgVectorReader(_PgVectorBase):
    """Queries the pgvector ``kb_chunks`` table; returns normalized semantic hits."""

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        conn = self._connection()
        exists = conn.execute("SELECT to_regclass(%s)", (_PG_TABLE,)).fetchone()[0]
        if exists is None:
            raise IndexNotFoundError(
                f"pgvector table '{_PG_TABLE}' not found. Run: python -m retrieval.indexer"
            )
        self._has_source_sha256 = conn.execute(
            "SELECT EXISTS ("
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "AND table_name = %s AND column_name = %s"
            ")",
            (_PG_TABLE, "source_sha256"),
        ).fetchone()[0]

    def query(self, embedding: Any, k: int) -> list[dict]:
        conn = self._connection()
        source_sha256_sql = "source_sha256" if self._has_source_sha256 else "'' AS source_sha256"
        # `<=>` is cosine distance; `1 - distance` reproduces Chroma's cosine score.
        # Same ordering expression in SELECT and ORDER BY so the HNSW index is used.
        rows = conn.execute(
            f"SELECT content, source, chunk_id, {source_sha256_sql}, stem_tags, "  # noqa: S608
            f"1 - (embedding <=> %(q)s::vector) AS score FROM {_PG_TABLE} "
            "ORDER BY embedding <=> %(q)s::vector LIMIT %(k)s",
            {"q": _as_list(embedding), "k": k},
        ).fetchall()
        out: list[dict] = []
        for content, source, chunk_id, source_sha256, stem_tags, score in rows:
            out.append({
                "text": content, "score": float(score), "source": source,
                "chunk_id": chunk_id, "source_sha256": source_sha256,
                "stem_tags": parse_stem_tags(stem_tags),
            })
        return out


def _as_list(emb: Any) -> list[float]:
    """Coerce an embedding (list or numpy array) to a plain list for pgvector binding."""
    tolist = getattr(emb, "tolist", None)
    return tolist() if callable(tolist) else list(emb)


# ===================================================================== factories
def get_vector_writer(cfg: dict):
    """Return a write-side store (``reset`` / ``add`` / ``finalize`` / ``close``)."""
    if vector_backend(cfg) == "pgvector":
        return _PgVectorWriter(cfg)
    return _ChromaWriter(cfg)


def get_vector_reader(cfg: dict):
    """Return a read-side store exposing ``query(embedding, k) -> list[dict]``."""
    if vector_backend(cfg) == "pgvector":
        return _PgVectorReader(cfg)
    return _ChromaReader(cfg)
