---
title: "Agentic FS/SQL Refactor — Verification & Audit Report"
date: 2026-06-26
tags: [agentic, fsconnect, sqlconnect, security, refactor, audit]
source: "claude/agentic-fs-sql-refactor → main PR"
related: ["docs/agentic/FSCONNECT_SQL_ROADMAP.md", "docs/Local_Sandbox_Complete_Audit_2026-06-25.md"]
---

## Summary

This document records the baseline verification of `feature/agentic-fs-sql-os` (HEAD `20b5c49`), the architecture refactor applied on branch `claude/agentic-fs-sql-refactor`, and the adversarial verification of both changes before merge into `main`.

**Overall result: APPROVED FOR MERGE.**  
All tests green, security posture preserved, two concrete improvements (one security hardening + one efficiency win) confirmed by four independent adversarial verifiers.

---

## Environment

| Item | Value |
|---|---|
| Python | 3.12.3 (`/usr/bin/python3.12`, venv `/home/user/cyclaw-venv`) |
| torch | 2.12.1+cpu (CVE-2025-32434 install order respected) |
| uv | 0.8.17 |
| ruff | 0.15.8 (`/root/.local/bin/ruff`) |
| `GROK_API_KEY` | `dummy` (test/offline use) |
| Original branch | `origin/feature/agentic-fs-sql-os` @ `20b5c49` |
| Refactor branch | `claude/agentic-fs-sql-refactor` (2 commits above feature branch) |

---

## Baseline Verification — `feature/agentic-fs-sql-os` @ `20b5c49`

**Verdict: `BASELINE_GREEN`**

### Test Totals (full suite)

| Metric | Value |
|---|---|
| Collected / Passed | **606 / 606** |
| Failed / Errors | 0 / 0 |
| Skipped | 0 |
| Coverage | **88.44%** (threshold 80% ✅) |

### fs/sql Targeted Tests (11 files)

110 passed, 0 failed across:
`test_fsconnect_cli`, `test_fsconnect_client`, `test_fsconnect_config`, `test_fsconnect_indexer`,
`test_fsconnect_pathsafe`, `test_fsconnect_selftest`, `test_fsconnect_writer`,
`test_sqlconnect_cli`, `test_sqlconnect_client`, `test_sqlconnect_config`, `test_agentic_isolation`.

### CLI Self-Tests

| CLI | Exit | Result |
|---|---|---|
| `agentic.cli test` | 0 | 5/5 (gh-not-on-PATH skipped; write gate + registry injection checks OK) |
| `agentic.fsconnect.cli test` | 0 | 5/5 (config, path-traversal 3/3, injection scanner, write gate, read end-to-end) |
| `agentic.sqlconnect.cli test` | 0 | 5/5 (config, read-only guard DML/SELECT, identifier; pg-driver/DSN skipped) |

### FS Tool-Call Matrix (writes_enabled=true, mode=write, root=/tmp/cw-fsroot)

| Op | Exit | Outcome |
|---|---|---|
| `write` (with `--reason`) | 0 | `status:applied`, 21 bytes, sha256, injection_flags=[] |
| `append` (with `--reason`) | 0 | `status:applied`, +22 bytes |
| `mkdir` (with `--reason`) | 0 | `status:applied`, created `/tmp/cw-fsroot/subdir` |
| `move` (with `--reason --confirm`) | 0 | `status:applied`, note.txt → subdir/note.txt |
| `list` | 0 | correct entries, types, modes |
| `read` | 0 | content returned, injection_flags=[] |
| `stat` | 0 | file size 43 |
| `grep foo` | 0 | match_count 1, line 2 |
| `index` (dry-run) | 0 | `op:index_scan`, eligible 1 |
| `index --apply` | 0 | `op:index_apply`, staged 1 |

**Write gate correctly refused (exit 4):**
- Write without `--reason` → exit 4, "a non-empty human reason is required"
- Overwrite without `--confirm` → exit 4, "destructive op requires confirm=True"
- Move without `--confirm` → exit 4, "destructive op requires confirm=True"
- `writes_enabled: false` + full reason/confirm → exit 0, `dry_run_plan` (safe no-op by design)

### SQL Tool-Call Matrix (sqlconnect enabled, DSN set)

| Op | Exit | Outcome |
|---|---|---|
| `status` | 0 | config rendered (read_only=true, max_rows 1000) |
| `schema` | 3 | psycopg not installed (driver-missing env error — expected) |
| `query SELECT` | 3 | same (pre-connect driver check) |
| `query DELETE` | 2 | **read-only guard refuses DML pre-connect** ✅ |

No write path exists by construction (`allow_write: false` hard-off; only SELECT/WITH accepted).

### gate.py / `/health` Smoke

- `import gate` → OK; TELEMETRY KILL banner (expected); CYCLAW_API_KEY warning (expected).
- `sys.modules` after `import gate`: zero `agentic.*` / `sync.*` entries — isolation confirmed.
- `/health` → HTTP 200, `status: degraded` (no LM Studio — expected), `index_ready: true`, `graph_ready: true`.

### Anomalies / Notes

1. **No SQL driver in venv.** psycopg/psycopg2/pyodbc absent; live SELECT not exercised. The read-only pre-connect guard (which fires before any connection) was verified. Not a code defect.
2. **`/health` has no top-level `mode` field.** Returns `status/index_ready/graph_ready/services`. Cosmetic discrepancy with some docs.
3. No flaky tests observed.

---

## Refactor Applied — `claude/agentic-fs-sql-refactor`

**Verdict: `REFACTOR_GREEN`**

Two commits above `origin/feature/agentic-fs-sql-os`:

```
33d7ab9 perf(fsconnect): memoize injection-pattern compilation
7a34157 security(sqlconnect): reject SQL comments in read-only query guard
```

Diff scope: 3 files changed, 40 insertions(+), 7 deletions(−).  
Files touched: `agentic/sqlconnect/client.py`, `agentic/fsconnect/client.py`, `tests/test_sqlconnect_client.py`.  
Files NOT touched: `gate.py`, `graph.py`, `mcp_hybrid_server.py`, `agentic/fsconnect/pathsafe.py`, `agentic/fsconnect/writer.py`.

### Test Totals (refactored branch, independently confirmed)

| Metric | Before (original) | After (refactor) |
|---|---|---|
| Passed | 606 | **610** (+4 test cases) |
| Failed | 0 | **0** |
| Coverage | 88.44% | 88.18% (denominator artifact; absolute covered-statement count rose) |

Coverage remains well above the 80% gate.

### Change 1 — Security: SQL Comment Rejection

**File:** `agentic/sqlconnect/client.py` — `assert_read_only_sql()`

**What:** Added a pre-check that rejects SQL containing `--`, `/*`, or `*/` before the keyword/multi-statement guards run.

**Why:** SQL comments are a documented vector for hiding a forbidden keyword or a stacked DML statement from a keyword scanner — the DB strips the comment, the text-scanner does not see through it. Example: `SELECT 1/**/UNION SELECT password FROM users` passes the `SELECT`-prefix check and may pass a naïve keyword scan if the `UNION` is hidden inside the comment. Read-only previews never legitimately need SQL comments.

**Effect:** Comment-based bypass vectors are closed. Guard fires before keyword scan (correct ordering confirmed). New regression tests:
- Parametrized cases added to `test_assert_rejects_non_readonly`: `SELECT 1 -- harmless`, `SELECT 1 /* DROP TABLE t */`, `SELECT 1 /* ; */ FROM t`.
- Dedicated `test_assert_rejects_comments_with_specific_code` verifying the `SQLCONNECT_BAD_QUERY` error code and "comment" in the message.

**Acceptable trade-off:** `WHERE col = '--'` (string literal containing `--`) is rejected as a false positive. This is fail-closed and acceptable for a preview tool. MySQL `#` comments are not in the guard, but since supported drivers are Postgres/MSSQL only (where `#` is not a comment delimiter), the keyword scanner still catches any hidden forbidden keyword.

### Change 2 — Efficiency: Injection-Pattern Compilation Memoized

**File:** `agentic/fsconnect/client.py` — `build_injection_patterns()` / new `_compile_injection_patterns()`

**What:** Wrapped the regex-compilation loop in `@lru_cache(maxsize=8)` keyed on the source-pattern tuple. `build_injection_patterns()` now returns `list(_compile_injection_patterns(tuple(sources)))`.

**Why:** The CLI builds a short-lived `FsClient`/`FsWriter`/`FsIndexer` on each invocation, and each construction previously recompiled ~46 regexes (13 OWASP + 33 banned patterns). The patterns are config-derived and static within a run. The cache eliminates redundant compilation.

**Correctness verified:**
- Caller gets a fresh mutable `list` each call; cache holds an immutable `tuple` — mutation-leak free.
- Cache keyed on full source tuple: distinct config → distinct cache entry.
- `try/except re.error` skip and `re.IGNORECASE` flag preserved verbatim from the original.
- `re.Pattern` objects are immutable/stateless — safe to share across threads.
- Direct byte-for-byte parity with a reimplemented inline reference confirmed.

---

## Adversarial Verification Results

Four independent verifiers ran in parallel. All returned **PASS / blocking=false**.

| Dimension | Verdict | Blocking | Key Finding |
|---|---|---|---|
| SQL guard bypass probe | **PASS** | No | All comment vectors (`--`, `/**/`, block-splice) rejected; legit SELECT/WITH unaffected; MySQL `#` no bypass on Postgres/MSSQL; 19 tests green |
| Cache correctness | **PASS** | No | Behavior-equivalent; caller-mutation-safe; correct keying; bad-regex skip preserved; 23 fsconnect scanner tests green |
| Security posture (all 5 invariants) | **PASS** | No | pathsafe 0-line diff; isolation suite green; gate imports 0 out-of-band modules; fs write gate refuses ungated; SQL DML refused pre-connect; no core imports in connectors |
| Completeness / PR-readiness | **PASS** | No | Clean tree; 610 passed; no debug prints/TODOs; no CLAUDE.md drift (fsconnect/sqlconnect never enumerated there); branch PR-ready |

### Non-Blocking Follow-Ups (for future issues)

1. **SQL string-literal false positives** — `WHERE col = '--'` rejected. Could be narrowed with a proper SQL lexer tokenizer if ever needed.
2. **MySQL `#` comment guard** — not needed now (Postgres/MSSQL only), but should be added if MySQL driver is ever introduced.
3. **mypy** — not installed in the shared venv; `--strict` check not run. Should be added to CI.

---

## Security Posture Statement

All five CyClaw graph-topology invariants remain intact:

1. **RAG-First** — `gate.py`/`graph.py` not touched.
2. **Topology = Policy** — no routing changes.
3. **Triple-Gated External** — no Grok-path changes.
4. **Audit Convergence** — no graph-node changes.
5. **Soul Governance** — `data/personality/soul.md` not touched; no `reason`-bypass added.

Module isolation confirmed: `agentic/` / `fsconnect/` / `sqlconnect/` do not import `gate`, `graph`, or `mcp_hybrid_server`; `gate.py` imports zero out-of-band modules. `tests/test_agentic_isolation.py` — **green**.

Write gating preserved: `writes_enabled` + `mode=write` + `--reason` + `--confirm` (destructive) all required. `pathsafe.py` and `writer.py` untouched.

SQL read-only enforced: DML rejected pre-connect (exit 2); refactor only tightened the guard.

---

## Recommendation

**Merge `claude/agentic-fs-sql-refactor` into `main`.**

The refactor is minimal (40 insertions, 3 files), measurably better (one security hardening with regression tests + one efficiency improvement), and confirmed safe by baseline verification, full suite regression, and four independent adversarial verifiers.
