"""Live pgvector-backend tests for retrieval.vector_store.

Exercises the pgvector write (`_PgVectorWriter`) and query (`_PgVectorReader`)
paths against a real Postgres+pgvector service. SKIPPED unless CYCLAW_DB_URL points
at a reachable Postgres with the ``vector`` extension available — so the default
ChromaDB suite stays green with zero extra deps, and the `postgres-backend` CI job
runs these for real.

Uses hand-built unit embeddings (no sentence-transformers model needed) so the test
is fast and deterministic: it verifies the cosine ORDER BY and the `1 - distance`
score mapping reproduce ChromaDB's ranking, plus metadata round-trip.
"""

import math
import os

import pytest

from retrieval.vector_store import get_vector_reader, get_vector_writer

DSN = os.environ.get("CYCLAW_DB_URL")
pytestmark = pytest.mark.skipif(
    not (DSN and DSN.startswith("postgres")),
    reason="CYCLAW_DB_URL not set to a Postgres DSN; skipping live pgvector tests",
)

DIM = 384


def _unit(vec):
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / n for x in vec]


def _vec(*nonzero):
    """Build a DIM-length vector from (index, value) pairs, then L2-normalize."""
    v = [0.0] * DIM
    for i, val in nonzero:
        v[i] = val
    return _unit(v)


def _cfg():
    return {
        "models": {"embeddings": {"dim": DIM}},
        "indexing": {"vector_backend": "pgvector", "database_url": DSN},
    }


@pytest.fixture
def fresh_store():
    """Drop kb_chunks before/after so each test starts clean."""
    import psycopg

    from utils.personality_db import _harden_pg_conninfo

    with psycopg.connect(_harden_pg_conninfo(DSN), autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS kb_chunks")
    yield
    with psycopg.connect(_harden_pg_conninfo(DSN), autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS kb_chunks")


def test_pgvector_index_and_rank(fresh_store):
    cfg = _cfg()
    # Three chunks at increasing cosine distance from the query direction e0.
    near = _vec((0, 1.0))                 # cos = 1.0 with query
    mid = _vec((0, 0.6), (1, 0.8))        # cos ~ 0.6
    far = _vec((1, 1.0))                  # cos = 0.0
    docs = ["near doc", "mid doc", "far doc"]
    embeddings = [near, mid, far]
    metadatas = [
        {"source": "a.md", "chunk_id": 0, "source_sha256": "a" * 64, "stem_tags": '["near"]'},
        {"source": "b.md", "chunk_id": 1, "source_sha256": "b" * 64, "stem_tags": '["mid"]'},
        {"source": "c.md", "chunk_id": 2, "source_sha256": "c" * 64, "stem_tags": '["far"]'},
    ]

    writer = get_vector_writer(cfg)
    try:
        writer.reset()
        writer.add(["chunk_0", "chunk_1", "chunk_2"], docs, embeddings, metadatas)
        writer.finalize()
    finally:
        writer.close()

    reader = get_vector_reader(cfg)
    try:
        hits = reader.query(_vec((0, 1.0)), k=3)
    finally:
        reader.close()

    assert [h["text"] for h in hits] == ["near doc", "mid doc", "far doc"], "cosine ranking order"
    # score = 1 - cosine_distance = cosine similarity; descending.
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True)
    assert math.isclose(scores[0], 1.0, abs_tol=1e-4), "identical direction → score ~1.0"
    # metadata round-trips (stem_tags parsed back to a list).
    assert hits[0]["source"] == "a.md" and hits[0]["chunk_id"] == 0
    assert hits[0]["source_sha256"] == "a" * 64
    assert hits[0]["stem_tags"] == ["near"]


def test_pgvector_reader_missing_table_raises(fresh_store):
    from utils.errors import IndexNotFoundError

    # No index built (fresh_store dropped the table) → reader must refuse clearly.
    with pytest.raises(IndexNotFoundError):
        get_vector_reader(_cfg())


def test_pgvector_reader_handles_legacy_rows_without_source_hash(fresh_store):
    import psycopg
    from pgvector.psycopg import register_vector

    from utils.personality_db import _harden_pg_conninfo

    with psycopg.connect(_harden_pg_conninfo(DSN), autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(conn)
        conn.execute(
            "CREATE TABLE kb_chunks ("
            "id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,"
            "source TEXT NOT NULL,"
            "chunk_id INT NOT NULL,"
            "content TEXT NOT NULL,"
            "stem_tags TEXT NOT NULL DEFAULT '[]',"
            f"embedding vector({DIM}) NOT NULL"
            ")"
        )
        conn.execute(
            "INSERT INTO kb_chunks (source, chunk_id, content, stem_tags, embedding) "
            "VALUES (%s, %s, %s, %s, %s)",
            ("legacy.md", 0, "legacy doc", "[]", _vec((0, 1.0))),
        )

    reader = get_vector_reader(_cfg())
    try:
        hits = reader.query(_vec((0, 1.0)), k=1)
    finally:
        reader.close()

    assert hits[0]["source"] == "legacy.md"
    assert hits[0]["source_sha256"] == ""


def test_pgvector_reset_accepts_and_ignores_fingerprint(fresh_store):
    # pgvector has no per-collection metadata slot to stamp a fingerprint into
    # (unlike _ChromaWriter) -- reset() must still accept the same optional
    # `fingerprint` argument indexer.py now always passes, and simply ignore it.
    cfg = _cfg()
    md = [{"source": "x.md", "chunk_id": 0, "stem_tags": "[]"}]
    writer = get_vector_writer(cfg)
    try:
        writer.reset({"model": "all-MiniLM-L6-v2", "dim": "384", "device": "cpu"})
        writer.add(["chunk_0"], ["first"], [_vec((0, 1.0))], md)
        writer.finalize()
    finally:
        writer.close()

    reader = get_vector_reader(cfg)
    try:
        hits = reader.query(_vec((0, 1.0)), k=10)
    finally:
        reader.close()
    assert len(hits) == 1 and hits[0]["text"] == "first"
    assert not hasattr(reader, "fingerprint")


def test_pgvector_rebuild_truncates(fresh_store):
    cfg = _cfg()
    md = [{"source": "x.md", "chunk_id": 0, "stem_tags": "[]"}]
    writer = get_vector_writer(cfg)
    try:
        writer.reset()
        writer.add(["chunk_0"], ["first"], [_vec((0, 1.0))], md)
        writer.finalize()
        # A second build resets (TRUNCATE) — no stale rows accumulate.
        writer.reset()
        writer.add(["chunk_0"], ["second"], [_vec((0, 1.0))], md)
        writer.finalize()
    finally:
        writer.close()

    reader = get_vector_reader(cfg)
    try:
        hits = reader.query(_vec((0, 1.0)), k=10)
    finally:
        reader.close()
    assert len(hits) == 1 and hits[0]["text"] == "second"
