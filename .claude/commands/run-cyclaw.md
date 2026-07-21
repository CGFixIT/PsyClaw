---
description: Run, start, build, smoke-test, or interact with the CyClaw FastAPI RAG server.
---

Start the CyClaw server (if needed) and run the smoke-test suite against it. $ARGUMENTS

CyClaw is a Python FastAPI server (`gate.py`) that exposes a RAG pipeline over
a local ChromaDB + BM25 index. It binds to `127.0.0.1:8787`. The driver is a
bash smoke script at `.claude/skills/run-cyclaw/smoke.sh`.

LM Studio is an **external dependency** (the local LLM). CI and the smoke
script run without it: the `/query` path degrades gracefully
(`offline-best-effort` returns an `[LLM Error: ...]` answer), and all
structural flows are testable.

---

## Prerequisites

```bash
# Python 3.12 required
pip install torch==2.13.0+cpu --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt -c constraints.txt --ignore-installed PyYAML
```

PyYAML conflict is harmless — skip the reinstall with `--ignore-installed PyYAML`.

---

## Build (retrieval index)

Must be run once before the server starts; idempotent:

```bash
mkdir -p data/personality index logs
GROK_API_KEY=dummy python3 -m retrieval.indexer
```

Expected: `[Indexer] Done. ChromaDB: index/chroma_db, BM25: index/bm25.json`

---

## Run (agent path — smoke driver)

The smoke script launches the server, runs all checks, and exits 0/1:

```bash
bash .claude/skills/run-cyclaw/smoke.sh
```

### Checks performed

**Core API (gateway + graph)**
1. `GET /health` — `index_ready=True`, `graph_ready=True`
2. `POST /query` — direct local path (`needs_confirm=False`)
3. `POST /query user_confirmed_online=false` — `model_used=local` or `offline-best-effort`
4. Prompt injection blocked → HTTP 400
5. `GET /soul` with valid Bearer token → soul version returned
5b. `GET /soul` without auth → HTTP 401 (fail-closed)
6. `GET /static/terminal.html` → HTTP 200
7. Terminal HTML endpoint discovery — parse `terminal.html` for API routes and probe each reachable one

**agentic/fsconnect — filesystem connector**
8. Lazy import gate — `agentic.fsconnect` not in `sys.modules` on the core path
9. Path-safety escape rejection — `../` traversal raises `FsPathError`
10. Emulated FS reads — `fs_list` / `fs_stat` / `fs_read` / `fs_grep` on a temp sandbox dir
11. Emulated FS writes (dry-run) — `writes_enabled=False` → dry-run plan, no file created
12. Emulated FS writes (live, temp root) — `writes_enabled=True` with temp config + writable root → file created, content verified
13. OS platform detection — `osutil._file_manager_argv` returns correct argv for current OS

**agentic/sqlconnect — SQL connector**
14. Lazy import gate — `agentic.sqlconnect` not in `sys.modules` on the core path
15. SELECT guard accepts valid `SELECT` query
16. DML rejected — `INSERT` / `UPDATE` / `DELETE` each raise `SqlConnectError`
17. SQL comment injection blocked — `--` / `/* */` raise `SqlConnectError`
18. Multi-statement blocked — stacked statements raise `SqlConnectError`

**NeMo guardrails**
19. Soft import — `guardrails.integration` imports cleanly without `nemoguardrails` installed
20. Isolation — `guardrails` not in `gate.py` / `graph.py` import graph
21. Offline path — `get_cyclaw_guardrails()` raises `GuardrailsDependencyError` when `nemoguardrails` is absent (callers degrade via `safe_generate`)
22. Soul mutation detection — `detect_soul_mutation_intent` flags mutation queries
23. Injection scan — `scan_injection` detects injection patterns in content
24. Grounding check — `grounding_score` returns a float in `[0.0, 1.0]`

**PostgreSQL backends (opt-in — skipped cleanly when `CYCLAW_DB_URL` unset)**
25. Soul DB Postgres — `tests/test_personality_postgres.py` (live connect/execute lifecycle)
26. Rate-limiter Postgres — `tests/test_ratelimit_postgres.py` (allow/deny, restart-survival)
27. pgvector store — `tests/test_pgvector_store.py` (index + cosine ranking parity)

**Full test suite**
28. `pytest tests/ -q --tb=short --continue-on-collection-errors` — all unit and integration tests; postgres/pgvector/fsconnect/sqlconnect/guardrails tests included (postgres tests skip cleanly without a DSN)

**Report**
29. Comprehensive pass/fail summary written to `.claude/sandbox-test.txt`

---

## Run (human path)

```bash
GROK_API_KEY=dummy uvicorn gate:app --host 127.0.0.1 --port 8787
```

Then open `http://127.0.0.1:8787` (Soul Console terminal UI). Useless headless.

---

## Interact via curl

```bash
BASE="http://127.0.0.1:8787"
CYCLAW_API_KEY="smoke-test-key-ci"

curl -s $BASE/health | python3 -m json.tool
curl -s -X POST $BASE/query \
  -H "Content-Type: application/json" \
  -d '{"query":"What is RRF fusion?"}' | python3 -m json.tool
curl -s -X POST $BASE/query \
  -H "Content-Type: application/json" \
  -d '{"query":"What is CyClaw?","user_confirmed_online":false}' | python3 -m json.tool
curl -s $BASE/soul -H "Authorization: Bearer $CYCLAW_API_KEY" | python3 -m json.tool
```

---

## Tests

```bash
GROK_API_KEY=dummy pytest tests/ -q --tb=short --continue-on-collection-errors
# Postgres live tests (requires CYCLAW_DB_URL + psycopg[binary] + pgvector):
CYCLAW_DB_URL=postgresql://... CYCLAW_DB_SSLMODE=disable \
  pytest tests/test_personality_postgres.py tests/test_ratelimit_postgres.py \
         tests/test_pgvector_store.py -q
```

---

## Gotchas

- **`status: degraded`** in `/health` is normal without LM Studio. `index_ready`
  and `graph_ready` are the meaningful smoke fields.
- **`needs_confirm: true`** on `/query` is correct when the top retrieval score
  is below `min_score`. Re-submit with `user_confirmed_online: false` to drive
  the offline path.
- **PyYAML install conflict** — always use `--ignore-installed PyYAML`.
- **TELEMETRY KILL messages** on startup are intentional.
- **`GROK_API_KEY`** must be set (any non-empty value works offline).
- **soul.md preservation** — the smoke script backs up and restores your real
  `data/personality/soul.md`; it is never left modified.
- **Postgres checks skip cleanly** without `CYCLAW_DB_URL` — set it to a live
  DSN to exercise the live connect/execute paths.
- **`writes_enabled=True` test** uses a fully isolated `/tmp` directory —
  no project files are ever touched by the write-emulation check.
- **NeMo guardrails** soft-import: tests pass whether or not `nemoguardrails`
  is installed; the integration degrades to a transparent no-op when absent.

## Notes

- LM Studio is an external dependency; without it `/query` degrades gracefully
  to `offline-best-effort` rather than failing.
- Also invoked by `/sandbox-runtime-verification` as its primary test driver.
- A fresh container has no Python deps installed — install first (see `CLAUDE.md` §8) before running this.
