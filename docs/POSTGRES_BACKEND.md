# CyClaw PostgreSQL Backends (opt-in)

CyClaw is **offline-first by default**: the soul DB is SQLite, the vector store is
embedded ChromaDB, and the rate limiter is in-memory. None of that requires a
server. This guide covers the **opt-in** PostgreSQL backends for deployments that
have consciously moved to a server-backed posture (durability, multi-process, or
consolidating state onto one database).

Three independent surfaces can use Postgres; each is off unless you configure a DSN:

| Surface | Default | Enable with | Tables / objects |
|---|---|---|---|
| Soul / personality DB | SQLite (`data/personality/cyclaw_soul.db`) | `CYCLAW_DB_URL` or `personality.database_url` | `soul_versions`, `interactions` |
| Rate-limiter persistence | in-memory | `api.rate_limit.database_url` or `CYCLAW_RATELIMIT_DB_URL` (deliberately does **not** fall back to `CYCLAW_DB_URL` — see `gate.py`) | `rate_hits` |
| Vector store (pgvector) | ChromaDB | `indexing.vector_backend: pgvector` + `indexing.database_url` / `CYCLAW_VECTOR_DB_URL` / `CYCLAW_DB_URL` | `kb_chunks` (+ HNSW index) |

> The external `agentic/sqlconnect/` connector is a *separate* feature (read-only
> access to an operator's own Postgres/MSSQL) and is unrelated to these internal
> backends.

## Install

Postgres support is an optional extra — the default install stays SQLite-only:

```bash
pip install 'cyclaw[postgres]'   # soul DB + rate limiter over Postgres
pip install 'cyclaw[pgvector]'   # also the pgvector vector backend
```

`psycopg` / `pgvector` are lazy-imported, so a default install never loads them.

## Enable

One Postgres can serve all three surfaces, but `CYCLAW_DB_URL` alone only reaches
two of them: the soul DB and the pgvector store both fall back to it. The
rate limiter deliberately does **not** — it needs its own explicit
`CYCLAW_RATELIMIT_DB_URL` (or `api.rate_limit.database_url`), by design, so
pointing `CYCLAW_DB_URL` at the soul database doesn't silently opt
rate-limiting into the same Postgres too.

```bash
export CYCLAW_DB_URL="postgresql://cyclaw:***@db.internal:5432/cyclaw?sslmode=verify-full"
# vector store also needs the config flag:
#   indexing.vector_backend: "pgvector"
# rate limiter needs its own explicit DSN if you want it persisted too:
#   export CYCLAW_RATELIMIT_DB_URL="postgresql://cyclaw:***@db.internal:5432/cyclaw"
```

Then build the index (pgvector) / start the server as usual:

```bash
python -m retrieval.indexer    # writes embeddings into kb_chunks + HNSW index
cyclaw-server
```

## Security posture (enforced in code)

The Postgres connect path (`utils/personality_db.py::_harden_pg_conninfo`, reused by
the rate limiter and pgvector) applies these by default — you do not need to set them:

- **TLS required.** If the DSN omits `sslmode`, `sslmode=require` is injected.
  Override with `CYCLAW_DB_SSLMODE` (use `verify-full` + a CA in production; `disable`
  only for a trusted same-host CI/dev container).
- **Bounded sessions.** `connect_timeout=10` and a server-side
  `statement_timeout=5000ms`, so a hung query cannot pin the shared connection.
- **Observability.** `application_name=cyclaw` (visible in `pg_stat_activity`).
- **No credential leakage.** The DSN is never logged or echoed in errors. The audit
  log already hashes queries and never persists raw text.
- **Parameterized SQL only.** Every value is bound; table/column names are code
  constants (no identifier interpolation).

### Least-privilege role (recommended)

Do not point CyClaw at a superuser. Create a dedicated role with only the rights it
needs:

```sql
CREATE ROLE cyclaw LOGIN PASSWORD '***';
CREATE DATABASE cyclaw OWNER cyclaw;
-- pgvector only: the `vector` extension must exist. Either pre-create it as a
-- superuser (preferred for least-privilege)…
\c cyclaw
CREATE EXTENSION IF NOT EXISTS vector;
-- …then CyClaw needs only table CRUD + index creation in its schema:
GRANT USAGE, CREATE ON SCHEMA public TO cyclaw;
```

CyClaw creates its own tables/indexes on first use (`CREATE TABLE IF NOT EXISTS`,
`CREATE INDEX IF NOT EXISTS`). If you pre-create the `vector` extension as a
superuser, the `cyclaw` role does not need extension-creation privileges.

## pgvector trade-off

pgvector is a deliberate choice, not an upgrade. ChromaDB's `PersistentClient` is a
zero-config local file that needs no server and keeps CyClaw offline-first; pgvector
requires a running Postgres. The corpus is small (a markdown KB at 384-dim), well
within pgvector's strong range, so performance is not the deciding factor — the
trade is **local-first vs. consolidate-on-Postgres**. Keep ChromaDB unless you are
intentionally running server-backed.

Schema: `kb_chunks(id, source, chunk_id, content, stem_tags, embedding vector(384))`
with an HNSW `vector_cosine_ops` index built after bulk load. Cosine distance
(`<=>`) with `1 - distance` reproduces ChromaDB's cosine score exactly, so RRF
fusion and the BM25 keyword leg are unchanged. Tune `hnsw.ef_search` per query if
recall needs it.

## Performance notes

- **Rate limiter:** with Postgres persistence, each persisted `allow()` is a network
  round-trip (heavier than a local SQLite write). It stays opt-in for durability; the
  in-memory default is untouched. For true multi-instance rate limiting, **Redis**
  (atomic counters + TTL) is the recommended target — not built here.
- **Soul DB:** tiny and already crash-safe; Postgres buys client/server auth, TLS,
  and central backups, not speed. The `idx_interactions_ts` index keeps the TTL prune
  a range scan as history grows.

## Verification

The `postgres-backend` CI job (ubuntu, `pgvector/pgvector:pg16` service) runs the
live tests for the soul DB, rate limiter, pgvector store, and authn:

```bash
# locally, against your own Postgres+pgvector:
export CYCLAW_DB_URL="postgresql://cyclaw:***@localhost:5432/cyclaw"
export CYCLAW_DB_SSLMODE=disable   # local trusted server only
pytest tests/test_personality_postgres.py \
       tests/test_ratelimit_postgres.py \
       tests/test_pgvector_store.py \
       tests/test_authn_postgres.py -q
```

Without a DSN these tests skip, so the default offline suite stays green.
